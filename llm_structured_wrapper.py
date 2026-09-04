"""
LLM Structured Output Wrapper
==============================

Provides Pydantic-validated LLM outputs with automatic retry logic.

Features:
- Automatic JSON extraction from LLM responses
- Pydantic validation
- Retry logic with exponential backoff
- Multiple fallback strategies
- Comprehensive error handling
- Logging and debugging

Author: Roadmap Implementation Phase 1
Date: 2026-02-13
"""

from typing import TypeVar, Type, Optional, List, Dict, Any, Union, Callable
from pydantic import BaseModel, ValidationError
import json
import re
import logging
from functools import wraps
import time
import traceback
from utils.token_manager import calculate_dynamic_max_tokens, estimate_prompt_tokens

logger = logging.getLogger(__name__)

# Type variable for Pydantic models
T = TypeVar('T', bound=BaseModel)


class StructuredOutputError(Exception):
    """Raised when LLM output cannot be parsed/validated after retries"""
    pass


class LLMStructuredWrapper:
    """
    Wrapper für LLM Calls mit Pydantic Output Validation
    
    Features:
    - Automatic JSON extraction from LLM responses (handles markdown, embedded JSON, etc.)
    - Pydantic validation with detailed error messages
    - Retry logic with exponential backoff
    - Fallback strategies for robustness
    - Comprehensive logging for debugging
    
    Example:
        >>> wrapper = LLMStructuredWrapper(llm_client)
        >>> output = wrapper.generate_structured(
        ...     prompt="Extract tools needed",
        ...     output_schema=ToolExtractionOutput
        ... )
        >>> print(output.tools)
    """
    
    def __init__(
        self,
        llm_client,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        backoff_factor: float = 2.0,
        temperature: float = 0.1,  # Niedrig für konsistente Strukturierung
        enable_logging: bool = True
    ):
        """
        Initialize the wrapper
        
        Args:
            llm_client: LLM client with generate_response() method
            max_retries: Maximum number of retry attempts
            retry_delay: Initial delay between retries (seconds)
            backoff_factor: Exponential backoff multiplier
            temperature: LLM temperature (lower = more deterministic)
            enable_logging: Enable detailed logging
        """
        self.llm_client = llm_client
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.backoff_factor = backoff_factor
        self.temperature = temperature
        self.enable_logging = enable_logging

        # SOTA constrained-decoding cache.
        #
        # Per-Pydantic-class GBNF grammar compiled from the model's JSON
        # Schema. With this in place llama.cpp's sampler is *guaranteed*
        # to emit only tokens that keep the partial output a valid
        # prefix of a JSON document conforming to the schema. This is
        # the only sound fix for mode-collapse loops on degenerate
        # inputs: no ``max_tokens`` value, prompt rewording, or system-
        # prompt tightening can prevent a free-decoding LLM from
        # entering a thought-channel loop and exhausting its budget on
        # tokens that downstream stripping then deletes \u2014 leaving an
        # empty response.
        #
        # Cache key: the Pydantic class itself (schemas are immutable
        # after class definition).
        #
        # IMPORTANT: We cache the *schema JSON string*, not the compiled
        # ``LlamaGrammar`` object. ``LlamaGrammar`` carries mutable
        # parser/sampler state that llama.cpp updates per token; reusing
        # the same instance across ``create_completion`` calls triggers
        # a native C++ exception on Windows (WinError 0xe06d7363) once
        # the previous call's state collides with a new completion.
        # Fresh ``LlamaGrammar.from_json_schema(...)`` per call is sub-
        # millisecond and entirely safe; the JSON string cache keeps
        # ``model_json_schema()`` from running every call. ``None``
        # marks a schema we already determined is non-compilable.
        self._schema_grammar_cache: Dict[Type[BaseModel], Optional[str]] = {}

        if not enable_logging:
            logger.setLevel(logging.WARNING)
    
    def _log(self, level: str, message: str) -> None:
        """Internal logging with enable/disable support"""
        if not self.enable_logging:
            return
        
        if level == "info":
            logger.info(message)
        elif level == "warning":
            logger.warning(message)
        elif level == "error":
            logger.error(message)
        elif level == "debug":
            logger.debug(message)
    
    def _extract_json_from_response(self, response: str) -> str:
        """
        Extrahiert JSON aus LLM Response
        
        Supports multiple formats:
        1. Pure JSON (starts with { or [)
        2. Markdown code blocks (```json ... ```)
        3. Embedded JSON in text
        4. Malformed JSON with common issues
        
        Args:
            response: Raw LLM response string
            
        Returns:
            Extracted JSON string
        """
        response = response.strip()
        
        # 1. Try: Pure JSON
        if response.startswith('{') or response.startswith('['):
            return response
        
        # 2. Try: Markdown Code Block
        # Matches: ```json\n{...}\n``` or ```\n{...}\n```
        json_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            extracted = match.group(1).strip()
            self._log("debug", f"Extracted from markdown block: {extracted[:100]}...")
            return extracted
        
        # 3. Try: Find JSON object {...}
        obj_match = re.search(r'\{[\s\S]*\}', response)
        if obj_match:
            extracted = obj_match.group(0)
            self._log("debug", f"Extracted JSON object: {extracted[:100]}...")
            return extracted
        
        # 4. Try: Find JSON array [...]
        arr_match = re.search(r'\[[\s\S]*\]', response)
        if arr_match:
            extracted = arr_match.group(0)
            self._log("debug", f"Extracted JSON array: {extracted[:100]}...")
            return extracted
        
        # 5. Fallback: Return as-is and let json.loads fail
        self._log("warning", "Could not extract JSON, returning raw response")
        return response
    
    def _fix_common_json_issues(self, json_str: str) -> str:
        """
        Fix common JSON formatting issues conservatively.

        Issues handled:
        - Trailing commas (always safe to remove).
        - Pure single-quoted Python-dict-style output (only when the
          string contains *zero* double quotes — otherwise replacing
          ``'`` → ``"`` would corrupt apostrophes inside legitimate
          double-quoted JSON strings, e.g. ``"SAP's transformation"`` →
          ``"SAP"s transformation"``, which is the bug that caused the
          structured-output retries to fail).

        Args:
            json_str: Potentially malformed JSON string

        Returns:
            Conservatively repaired JSON string
        """
        # Trailing commas — always safe.
        json_str = re.sub(r',\s*([\]}])', r'\1', json_str)

        # Single-quote → double-quote ONLY when the LLM emitted a fully
        # Python-style dict (no double quotes at all). Doing it
        # unconditionally corrupts every English contraction / possessive
        # inside a properly double-quoted string value.
        if '"' not in json_str and "'" in json_str:
            json_str = json_str.replace("'", '"')

        return json_str
    
    def _get_schema_grammar(self, output_schema: Type[BaseModel]) -> Any:
        """Build (and cache) a llama.cpp GBNF grammar from a Pydantic schema.

        Returns ``None`` if the underlying ``llama_cpp`` package or the
        ``LlamaGrammar.from_json_schema`` API is unavailable; callers
        must treat ``None`` as "free decoding" and degrade gracefully.

        Caching is keyed on the Pydantic class (schema is immutable
        after class definition), so the GBNF compile cost (a few ms) is
        paid once per process and per schema, not per call.

        Why this is the SOTA path for structured output:
          * Free decoding lets the model emit arbitrary tokens, which
            on degenerate inputs (specific document content +
            temperature=0 determinism + thought-channel-capable model
            templates) collapses into mode loops that exhaust the
            token budget on tokens downstream stripping then removes,
            producing an empty response.
          * Grammar-constrained decoding restricts the sampler to
            tokens that keep the partial output a valid prefix of a
            JSON document matching the schema. Thought-channel
            openers, markdown fences, and prose preambles are simply
            not sampleable. The class of bug is eliminated, not
            patched.
        """
        try:
            from llama_cpp import LlamaGrammar  # type: ignore
        except ImportError:
            return None

        # Phase 1 — fetch / build the schema JSON string (cached).
        if output_schema in self._schema_grammar_cache:
            schema_json = self._schema_grammar_cache[output_schema]
            if schema_json is None:
                # Earlier compile attempt failed permanently for this schema.
                return None
        else:
            try:
                schema_json = json.dumps(
                    output_schema.model_json_schema(), ensure_ascii=False
                )
            except Exception as exc:
                self._log(
                    "warning",
                    f"Schema serialisation failed for "
                    f"{output_schema.__name__}: {exc}. Falling back to "
                    f"free decoding.",
                )
                self._schema_grammar_cache[output_schema] = None
                return None
            self._schema_grammar_cache[output_schema] = schema_json

        # Phase 2 — compile a *fresh* LlamaGrammar per call. Reusing
        # one instance across completions crashes llama.cpp natively
        # (WinError 0xe06d7363) because its parser state is mutated
        # per token. Compilation is sub-millisecond.
        try:
            return LlamaGrammar.from_json_schema(schema_json)
        except Exception as exc:
            self._log(
                "warning",
                f"Schema-grammar compilation failed for "
                f"{output_schema.__name__}: {exc}. Falling back to "
                f"free decoding (may be unstable on degenerate inputs).",
            )
            self._schema_grammar_cache[output_schema] = None
            return None

    @staticmethod
    def _collect_field_descriptions(output_schema: Type[T]) -> List[str]:
        """Sammelt menschenlesbare Feldbeschreibungen aus dem JSON-Schema.

        Geht properties + ``$defs`` rekursiv durch und erzeugt kompakte
        Lines wie ``- ExtractedTransaction.amount: signed Float ...``.
        Das ist die einzige semantische Information aus dem Schema,
        die das LLM braucht (Datentypen / Encoding / Erlaeuterungen);
        die strukturelle Form wird via GBNF-Grammar erzwungen.
        """
        schema_json = output_schema.model_json_schema()
        lines: List[str] = []

        def walk(props: Dict[str, Any], owner: str) -> None:
            for name, prop in props.items():
                desc = (prop.get("description") or "").strip()
                if desc:
                    label = f"{owner}.{name}" if owner else name
                    lines.append(f"- {label}: {desc}")

        walk(schema_json.get("properties", {}), owner="")
        for def_name, def_schema in schema_json.get("$defs", {}).items():
            walk(def_schema.get("properties", {}), owner=def_name)
        return lines

    def _create_schema_prompt(
        self,
        base_prompt: str,
        output_schema: Type[T],
        *,
        grammar_active: bool,
    ) -> str:
        """Erweitert den Prompt um Schema-Hinweise.

        Zwei Modi:

        * ``grammar_active=True`` (SOTA-Pfad): GBNF-Grammar erzwingt das
          Schema strukturell. Wir geben dem Modell nur eine knappe Liste
          der Feldbeschreibungen mit -- kein vollstaendiger JSON-Dump,
          keine Format-Regeln (die Grammar erlaubt nur valides JSON).
          Das spart bei verschachtelten Pydantic-Modellen ~2-3k
          Prompt-Tokens, die sonst dem Output-Budget fehlen und auf
          n_ctx-knappen Modellen Truncation provozieren.

        * ``grammar_active=False`` (Free-Decoding-Fallback): hier MUSS
          das Modell die Form selbst lernen, also injizieren wir den
          vollstaendigen JSON-Schema-Dump als Referenz.
        """
        schema_json = output_schema.model_json_schema()
        schema_desc = schema_json.get("description", output_schema.__name__)

        if grammar_active:
            field_lines = self._collect_field_descriptions(output_schema)
            field_block = "\n".join(field_lines) if field_lines else "(keine zusaetzlichen Hinweise)"
            return (
                f"{base_prompt}\n\n"
                f"Antwort als JSON-Objekt ({schema_desc}). "
                "Die Grammatik erzwingt das Schema strukturell -- du musst nur "
                "die Inhalte korrekt fuellen.\n\n"
                f"Feld-Bedeutungen:\n{field_block}"
            )

        # Fallback ohne Grammar: voller Schema-Dump.
        example = schema_json.get("examples", [{}])[0] if schema_json.get("examples") else {}
        return (
            f"{base_prompt}\n\n"
            "WICHTIG: Antworte NUR mit einem validen JSON-Objekt gemaess diesem Schema:\n\n"
            f"Schema: {schema_desc}\n\n"
            f"```json\n{json.dumps(schema_json, indent=2, ensure_ascii=False)}\n```\n\n"
            "REGELN:\n"
            "1. Antworte AUSSCHLIESSLICH mit dem JSON-Objekt\n"
            "2. KEIN zusaetzlicher Text vor oder nach dem JSON\n"
            "3. Verwende doppelte Anfuehrungszeichen (\" nicht ')\n"
            "4. Keine trailing commas\n"
            "5. Alle required fields muessen vorhanden sein\n\n"
            f"Beispiel:\n{json.dumps(example, indent=2, ensure_ascii=False)}\n\n"
            f"Jetzt generiere das JSON fuer: {base_prompt}"
        )

    @staticmethod
    def _merge_system_into_first_user(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge system content into the first user turn for Mistral-style templates."""
        system_chunks: List[str] = []
        rest: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    system_chunks.append(content)
            else:
                rest.append(dict(msg))

        if not system_chunks:
            return rest

        merged_system = "\n\n".join(system_chunks)
        for index, msg in enumerate(rest):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    msg["content"] = f"{merged_system}\n\n{content}".strip()
                rest[index] = msg
                return rest

        return [{"role": "user", "content": merged_system}, *rest]

    @staticmethod
    def _normalize_conversation_roles(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge consecutive user/assistant turns into template-safe messages.

        Some downstream GGUF chat templates reject any non-alternating
        conversation after the optional system prompt. The wrapper receives
        message history from multiple subsystems, so the invariant has to be
        restored here as well instead of assuming every caller already keeps a
        perfect user/assistant cadence.
        """
        normalized: List[Dict[str, Any]] = []
        for raw_message in messages:
            if not isinstance(raw_message, dict):
                continue

            role = str(raw_message.get("role") or "").strip()
            if not role:
                continue

            content = raw_message.get("content", "")
            if isinstance(content, str):
                content = content.strip()
            if not content and role != "system":
                continue

            message = dict(raw_message)
            message["role"] = role
            message["content"] = content

            has_conversation_turn = any(
                existing.get("role") in {"user", "assistant"}
                for existing in normalized
            )
            if role == "assistant" and not has_conversation_turn:
                continue

            if (
                normalized
                and role in {"user", "assistant"}
                and normalized[-1].get("role") == role
            ):
                previous = dict(normalized[-1])
                previous_content = previous.get("content", "")
                if isinstance(previous_content, str) and isinstance(content, str):
                    previous["content"] = (
                        f"{previous_content}\n\n{content}".strip()
                        if previous_content else content
                    )
                normalized[-1] = previous
                continue

            normalized.append(message)

        return normalized

    def _build_call_messages(
        self,
        enhanced_prompt: str,
        messages: Optional[List[Dict[str, str]]],
        system_prompt: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Build chat messages without creating invalid consecutive user turns."""
        call_messages: List[Dict[str, Any]] = [dict(msg) for msg in messages] if messages else []

        if system_prompt:
            call_messages.insert(0, {"role": "system", "content": system_prompt})

        if call_messages and call_messages[-1].get("role") == "user":
            last_message = dict(call_messages[-1])
            last_content = last_message.get("content", "")
            if isinstance(last_content, str) and last_content.strip():
                last_message["content"] = f"{last_content}\n\n{enhanced_prompt}".strip()
            else:
                last_message["content"] = enhanced_prompt
            call_messages[-1] = last_message
        else:
            call_messages.append({"role": "user", "content": enhanced_prompt})

        normalized_messages = self._normalize_conversation_roles(call_messages)
        return self._merge_system_into_first_user(normalized_messages)
    
    def generate_structured(
        self,
        prompt: str,
        output_schema: Type[T],
        messages: Optional[List[Dict[str, str]]] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> T:
        """
        Generates LLM response and validates against Pydantic schema
        
        Args:
            prompt: User prompt (or will be appended to messages)
            output_schema: Pydantic Model class for validation
            messages: Optional message history
            max_tokens: Maximum tokens for LLM generation. If None, calculated
                        dynamically from the model's context window minus the
                        estimated prompt tokens and a 10 % safety buffer.
            system_prompt: Optional system prompt
            **kwargs: Additional LLM parameters
            
        Returns:
            Validated Pydantic model instance
            
        Raises:
            StructuredOutputError: If validation fails after all retries
            
        Example:
            >>> result = wrapper.generate_structured(
            ...     prompt="Extract emotional markers",
            ...     output_schema=EmotionalAnalysisOutput
            ... )
            >>> print(result.dominant_emotion)
        """
        # Build (or reuse cached) GBNF grammar from the Pydantic schema.
        # If the underlying client supports a ``grammar`` kwarg, it will
        # constrain decoding to schema-conformant tokens and eliminate
        # mode-collapse loops at the source.
        grammar = self._get_schema_grammar(output_schema)
        grammar_kwargs: Dict[str, Any] = {}
        if grammar is not None and "grammar" not in kwargs:
            grammar_kwargs["grammar"] = grammar

        # Build prompt AFTER grammar resolution so we can drop the
        # full JSON-Schema dump when the grammar already enforces it
        # (saves thousands of prompt tokens on nested Pydantic models).
        enhanced_prompt = self._create_schema_prompt(
            prompt, output_schema, grammar_active=grammar is not None
        )

        # Dynamic max_tokens: derive from the model's real context window so
        # we never silently truncate structured output.  Callers may still
        # pass an explicit value to override (e.g. for very short schemas).
        if max_tokens is None:
            context_window: int = (
                self.llm_client.get_max_context_tokens()
                if hasattr(self.llm_client, "get_max_context_tokens")
                else 0
            ) or 16384
            text_for_estimation = enhanced_prompt
            if system_prompt:
                text_for_estimation = system_prompt + "\n" + text_for_estimation
            if messages:
                for _m in messages:
                    text_for_estimation += "\n" + (_m.get("content") or "")
            prompt_tokens = estimate_prompt_tokens(text_for_estimation)
            max_tokens = calculate_dynamic_max_tokens(
                model_context_window=context_window,
                prompt_tokens=prompt_tokens,
                safety_buffer_ratio=0.10,
                min_output_tokens=512,
            )
            self._log(
                "debug",
                f"Dynamic max_tokens={max_tokens} "
                f"(context_window={context_window}, prompt≈{prompt_tokens} tokens)",
            )

        retries = 0
        current_delay = self.retry_delay
        last_error = None
        last_response = None
        json_str = ""  # Initialize to avoid unbound variable
        
        while retries < self.max_retries:
            try:
                # Build messages in a template-compatible form.  Mistral-style
                # chat templates reject explicit system turns and consecutive
                # user turns.
                call_messages = self._build_call_messages(
                    enhanced_prompt=enhanced_prompt,
                    messages=messages,
                    system_prompt=system_prompt,
                )
                
                # LLM Call
                self._log("info", f"🔄 LLM call attempt {retries + 1}/{self.max_retries}")
                
                response = self.llm_client.generate_response(
                    messages=call_messages,
                    max_tokens=max_tokens,
                    temperature=self.temperature,
                    **grammar_kwargs,
                    **kwargs
                )
                
                last_response = response
                self._log("debug", f"Raw response: {response[:200]}...")

                # Empty response is a structural failure (e.g. the
                # underlying LLM truncated before emitting a token).
                # Treat it as an explicit error so retries get fresh
                # parameters and the final exception message is clear,
                # rather than feeding "" to the JSON extractor and
                # surfacing a misleading "Expecting value at column 0".
                if not isinstance(response, str) or not response.strip():
                    raise StructuredOutputError(
                        "LLM returned empty response (likely max_tokens "
                        "truncation before any token was emitted; raise "
                        "max_tokens or shorten the prompt)."
                    )

                # Extract JSON
                json_str = self._extract_json_from_response(response)
                
                # Try to fix common issues
                json_str = self._fix_common_json_issues(json_str)
                
                # Parse JSON
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError as e:
                    # Last attempt: Try to salvage partial JSON
                    self._log("warning", f"JSON decode failed: {e}, attempting repair...")
                    # Remove any non-JSON prefix/suffix
                    json_str = json_str.strip()
                    if not (json_str.startswith('{') or json_str.startswith('[')):
                        # Try to find and extract JSON
                        match = re.search(r'(\{.*\}|\[.*\])', json_str, re.DOTALL)
                        if match:
                            json_str = match.group(1)
                    data = json.loads(json_str)  # Re-attempt
                
                # Validate with Pydantic
                validated = output_schema(**data)
                
                self._log("info", f"✅ Structured output validated successfully (attempt {retries + 1})")
                return validated
                
            except json.JSONDecodeError as e:
                last_error = f"JSON Parsing Error: {e}"
                self._log("warning", f"⚠️ Attempt {retries + 1}/{self.max_retries} failed: {last_error}")
                self._log("debug", f"   Response was: {last_response[:300] if last_response else 'N/A'}...")
                self._log("debug", f"   Extracted JSON: {json_str[:300] if json_str else 'N/A'}...")
                
            except ValidationError as e:
                last_error = f"Pydantic Validation Error: {e}"
                self._log("warning", f"⚠️ Attempt {retries + 1}/{self.max_retries} failed: {last_error}")
                self._log("debug", f"   JSON was: {json_str[:300] if json_str else 'N/A'}...")
                self._log("debug", f"   Validation errors: {e.errors()}")
                
            except Exception as e:
                last_error = f"Unexpected Error: {type(e).__name__}: {e}"
                self._log("error", f"❌ Attempt {retries + 1}/{self.max_retries} failed: {last_error}")
                self._log("debug", f"   Traceback: {traceback.format_exc()}")
            
            retries += 1
            
            # Retry with backoff
            if retries < self.max_retries:
                self._log("info", f"🔄 Retrying in {current_delay:.1f}s...")
                time.sleep(current_delay)
                current_delay *= self.backoff_factor
        
        # All retries failed
        error_msg = (
            f"Failed to generate valid structured output after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )
        if last_response:
            error_msg += f"\nLast response: {last_response[:500]}..."
        
        self._log("error", f"❌ {error_msg}")
        raise StructuredOutputError(error_msg)
    
    def generate_structured_safe(
        self,
        prompt: str,
        output_schema: Type[T],
        fallback: Optional[T] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        **kwargs
    ) -> Optional[T]:
        """
        Safe variant: Returns None (or fallback) instead of raising exception
        
        Args:
            prompt: User prompt
            output_schema: Pydantic Model class
            fallback: Fallback value if validation fails
            on_error: Optional callback function for error handling
            **kwargs: Additional parameters for generate_structured()
            
        Returns:
            Validated model or fallback/None
            
        Example:
            >>> result = wrapper.generate_structured_safe(
            ...     prompt="Extract tools",
            ...     output_schema=ToolExtractionOutput,
            ...     fallback=ToolExtractionOutput(tools=[], needs_tools=False)
            ... )
        """
        try:
            return self.generate_structured(prompt, output_schema, **kwargs)
        except StructuredOutputError as e:
            self._log("error", f"❌ Structured output failed (safe mode): {e}")
            if on_error:
                on_error(e)
            return fallback
        except Exception as e:
            self._log("error", f"❌ Unexpected error in safe mode: {e}")
            if on_error:
                on_error(e)
            return fallback


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_structured_wrapper(
    llm_client,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    **kwargs
) -> LLMStructuredWrapper:
    """
    Factory function for creating structured wrapper
    
    Args:
        llm_client: LLM client instance
        max_retries: Maximum retry attempts
        retry_delay: Initial delay between retries
        **kwargs: Additional wrapper parameters
        
    Returns:
        Configured LLMStructuredWrapper instance
    """
    return LLMStructuredWrapper(
        llm_client=llm_client,
        max_retries=max_retries,
        retry_delay=retry_delay,
        **kwargs
    )


# ============================================================================
# TESTING
# ============================================================================

if __name__ == "__main__":
    print("🧪 Testing LLM Structured Wrapper...")
    
    # Mock LLM client for testing
    class MockLLMClient:
        def generate_response(self, messages, max_tokens=1024, temperature=0.1, **kwargs):
            # Simulate LLM response with JSON
            return '''```json
{
    "tools": [
        {
            "tool": "web_search",
            "parameters": {"query": "test query"},
            "reasoning": "Need to search the web",
            "confidence": 0.95
        }
    ],
    "needs_tools": true,
    "reasoning": "User asked a question requiring web search"
}
```'''
    
    # Test the wrapper
    try:
        from llm_output_schemas import ToolExtractionOutput
        
        mock_client = MockLLMClient()
        wrapper = LLMStructuredWrapper(mock_client, enable_logging=True)
        
        result = wrapper.generate_structured(
            prompt="Search for latest AI news",
            output_schema=ToolExtractionOutput
        )
        
        print(f"✅ Wrapper test passed!")
        print(f"   Tools: {len(result.tools)}")
        print(f"   Needs tools: {result.needs_tools}")
        print(f"   First tool: {result.tools[0].tool if result.tools else 'N/A'}")
        
    except Exception as e:
        print(f"❌ Wrapper test failed: {e}")
        import traceback
        traceback.print_exc()
