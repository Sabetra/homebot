"""
LangChain Adapter for Local llama.cpp ModelLoader
===================================================

Wraps the project's ModelLoader (llama-cpp-python) as a LangChain-compatible
BaseChatModel so LangGraph can invoke it natively.

This is the bridge between:
  - LangGraph's StateGraph (expects langchain_core interfaces)
  - Our local ModelLoader (llama-cpp-python GGUF inference)

SOTA Pattern: Custom BaseChatModel adapter (LangChain docs 2025/2026)
  → _generate() translates ChatMessages → prompt → ModelLoader.generate_response()
  → Returns ChatResult with AIMessage

✅ Phase 9b: Real LangGraph integration (replaces LangGraph-inspired stub).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterator, List, Optional, Protocol, Sequence, runtime_checkable

from pydantic import ConfigDict

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

logger = logging.getLogger(__name__)


@runtime_checkable
class _ModelLoaderProtocol(Protocol):
    """Minimal contract required by LocalLlamaCppChat.

    Keeps adapter testable and decoupled from concrete ModelLoader classes.
    """

    def generate_response(
        self,
        prompt: str = "",
        image_path: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        messages: Optional[list] = None,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        stop: Optional[list] = None,
        **kwargs: Any,
    ) -> str:
        ...

    def generate_response_stream(
        self,
        messages: list,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
    ) -> Iterator[str]:
        ...

    def count_tokens(self, text: str) -> int:
        ...


def _messages_to_dicts(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """Convert LangChain messages to dicts for create_chat_completion.
    
    Konvertiert LangChain BaseMessage-Objekte in das Standard-Messages-Format
    [{"role": "...", "content": "..."}], damit create_chat_completion() das
    korrekte Mistral/Magistral Chat-Template automatisch anwendet.
    """
    result: list[dict[str, str]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            result.append({"role": "system", "content": str(msg.content)})
        elif isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": str(msg.content)})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": str(msg.content)})
        else:
            role = getattr(msg, "type", "user")
            content = str(getattr(msg, "content", ""))
            if role == "assistant":
                mapped_role = "assistant"
            elif role == "system":
                mapped_role = "system"
            else:
                mapped_role = "user"
            result.append({"role": mapped_role, "content": content})
    return result


class LocalLlamaCppChat(BaseChatModel):
    """LangChain BaseChatModel wrapping the local llama-cpp-python ModelLoader.

    Usage::

        from scripts.model_loader import get_model_loader
        model = LocalLlamaCppChat(model_loader=get_model_loader())

        # Now usable in any LangGraph node:
        result = model.invoke([HumanMessage("Wie geht es dir?")])
    """

    # ── Pydantic fields (LangChain uses Pydantic V1 internally) ──
    model_loader: Any = None
    model_name: str = "local-mistral-gguf"
    default_max_tokens: int = 1024
    default_temperature: float = 0.7
    default_top_p: float = 0.9
    default_top_k: int = 40
    default_repeat_penalty: float = 1.1

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── Required by BaseChatModel ──

    @property
    def _llm_type(self) -> str:
        return "local-llamacpp"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "max_tokens": self.default_max_tokens,
            "temperature": self.default_temperature,
        }

    def _validated_model_loader(self) -> _ModelLoaderProtocol:
        loader = self.model_loader
        if loader is None:
            raise RuntimeError(
                "LocalLlamaCppChat: model_loader is None -- "
                "call set_model_loader() or pass it at construction."
            )

        missing_methods: list[str] = []
        if not hasattr(loader, "generate_response"):
            missing_methods.append("generate_response")
        if not hasattr(loader, "count_tokens"):
            missing_methods.append("count_tokens")
        if missing_methods:
            raise TypeError(
                "LocalLlamaCppChat: model_loader does not satisfy required interface; "
                f"missing={missing_methods}"
            )

        return loader

    def _generation_params(
        self,
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        max_tokens = int(kwargs.get("max_tokens", self.default_max_tokens))
        temperature = float(kwargs.get("temperature", self.default_temperature))
        top_p = float(kwargs.get("top_p", self.default_top_p))
        top_k = int(kwargs.get("top_k", self.default_top_k))
        repeat_penalty = float(kwargs.get("repeat_penalty", self.default_repeat_penalty))

        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        if not (0.0 <= temperature <= 2.0):
            raise ValueError("temperature must be in [0.0, 2.0]")
        if not (0.0 < top_p <= 1.0):
            raise ValueError("top_p must be in (0.0, 1.0]")
        if top_k < 0:
            raise ValueError("top_k must be >= 0")
        if repeat_penalty <= 0.0:
            raise ValueError("repeat_penalty must be > 0.0")

        return {
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repeat_penalty": repeat_penalty,
            "stop": stop,
        }

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Core generation -- translates LangChain messages → local LLM call.

        This is the ONLY method that touches the actual model.
        """
        model_loader = self._validated_model_loader()
        messages_dicts = _messages_to_dicts(messages)
        params = self._generation_params(stop=stop, **kwargs)

        logger.debug(
            "LocalLlamaCppChat._generate: message_count=%d, max_tokens=%d, temp=%.2f",
            len(messages_dicts),
            params["max_tokens"],
            params["temperature"],
        )

        try:
            response_text = model_loader.generate_response(
                messages=messages_dicts,
                max_tokens=params["max_tokens"],
                temperature=params["temperature"],
                top_p=params["top_p"],
                top_k=params["top_k"],
                repeat_penalty=params["repeat_penalty"],
                stop=params["stop"],
            )
        except Exception as exc:
            logger.exception("LocalLlamaCppChat generation failed")
            raise RuntimeError(
                "LocalLlamaCppChat: generation failed in model_loader.generate_response"
            ) from exc

        if response_text is None:
            response_text = ""
        if not isinstance(response_text, str):
            response_text = str(response_text)

        message = AIMessage(content=response_text)
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async bridge for LangGraph/LangChain async execution paths."""
        return await asyncio.to_thread(
            self._generate,
            messages,
            stop,
            None,
            **kwargs,
        )

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Token-streaming path using ModelLoader.generate_response_stream when available."""
        model_loader = self._validated_model_loader()
        messages_dicts = _messages_to_dicts(messages)
        params = self._generation_params(stop=stop, **kwargs)

        if not hasattr(model_loader, "generate_response_stream"):
            result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            content = result.generations[0].message.content if result.generations else ""
            yield ChatGenerationChunk(message=AIMessageChunk(content=str(content)))
            return

        try:
            stream_iter = model_loader.generate_response_stream(
                messages=messages_dicts,
                max_tokens=params["max_tokens"],
                temperature=params["temperature"],
                top_p=params["top_p"],
                top_k=params["top_k"],
                repeat_penalty=params["repeat_penalty"],
            )
            for token in stream_iter:
                if token:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=str(token)))
        except Exception as exc:
            logger.exception("LocalLlamaCppChat streaming failed")
            raise RuntimeError(
                "LocalLlamaCppChat: streaming failed in model_loader.generate_response_stream"
            ) from exc

    def get_num_tokens(self, text: str) -> int:
        """Exact token count via llama.cpp tokenizer."""
        if self.model_loader is not None:
            try:
                count: int = self._validated_model_loader().count_tokens(text)
                return count
            except Exception:
                logger.debug("LocalLlamaCppChat token count fallback used", exc_info=True)
        # Fallback: ~4 chars per token for German
        return max(1, len(text) // 4)

    def set_model_loader(self, model_loader: Any) -> None:
        """Late-bind the model loader (useful for lazy initialization)."""
        self.model_loader = model_loader
        logger.info("✅ LocalLlamaCppChat: model_loader bound")
