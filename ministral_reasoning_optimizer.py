"""
Ministral 3 Reasoning Optimizer - LLM-basierte Komplexitäts-Klassifikation
===========================================================================

Strategie: Das Modell selbst entscheidet über Query-Komplexität
- Keine starren Keywords
- Flexible, kontextabhängige Einschätzung
- Optimales Token-Budget Management

Hardware-Constraints:
- max_tokens: 8192 (Hardware-Limit)
- temperature: 0.7 (Standard)

Author: AI Assistant
Date: 2025-12-20
"""

from typing import Optional, Dict, List, Any, Tuple, cast
import logging
import re
from dataclasses import dataclass
import json
import threading

logger = logging.getLogger(__name__)

# Import cuda_lock from model_loader for thread-safe LLM access.
# This module stores a raw Llama instance (self.model) and calls
# create_chat_completion() directly -- without this lock, those calls
# race with concurrent inference from other threads (RAG-PERSIST,
# parallel tools, streaming) → access-violation crash.
try:
    from scripts.model_loader import cuda_lock as _cuda_lock
except ImportError:
    _cuda_lock = threading.RLock()  # Fallback for standalone usage


@dataclass
class TokenBudget:
    """Token-Budget Konfiguration basierend auf Komplexität"""
    max_tokens: int
    thinking_ratio: float
    enable_reasoning: bool
    description: str


# Token-Budget Definitionen
TOKEN_BUDGETS = {
    "simple": TokenBudget(
        max_tokens=2048,
        thinking_ratio=0.3,
        enable_reasoning=False,
        description="Direkte Faktenfragen, kurze Antworten"
    ),
    "medium": TokenBudget(
        max_tokens=5120,
        thinking_ratio=0.5,
        enable_reasoning=True,
        description="Moderate Erklärungen, Anleitungen"
    ),
    "complex": TokenBudget(
        max_tokens=8192,
        thinking_ratio=0.6,
        enable_reasoning=True,
        description="Tiefe Analysen, Multi-Step-Reasoning"
    )
}


class ReasoningMemory:
    """Verwaltet Reasoning-Traces mit FIFO-Limit"""
    
    def __init__(self, max_traces: int = 2):
        self.traces: List[Tuple[str, str, str]] = []
        self.max_traces = max_traces
    
    def add_trace(self, query: str, reasoning: str, answer: str):
        """Fügt neuen Trace hinzu, behält nur letzte N"""
        self.traces.append((query, reasoning, answer))
        
        if len(self.traces) > self.max_traces:
            self.traces = self.traces[-self.max_traces:]
    
    def get_context_for_llm(self) -> List[Dict]:
        """Gibt Traces als Messages für LLM.
        
        WICHTIG: Das Magistral Jinja2-Template unterstützt bei assistant-Messages
        mit List-Content NUR content[0]['text'] -- es kennt KEIN {"type": "thinking"}.
        Stattdessen [THINK]...[/THINK] Tags inline im Text-String verwenden,
        die das Template als default_system_message definiert.
        """
        messages = []
        
        for query, reasoning, answer in self.traces:
            messages.append({
                "role": "user",
                "content": query
            })
            
            # Template-konformes Format: [THINK]...[/THINK] als String
            if reasoning:
                messages.append({
                    "role": "assistant",
                    "content": f"[THINK]{reasoning}[/THINK]{answer}"
                })
            else:
                messages.append({
                    "role": "assistant",
                    "content": answer
                })
        
        return messages
    
    def clear(self):
        """Löscht alle Traces"""
        self.traces = []


class MinistralReasoningOptimizer:
    """
    Wrapper für optimierten Ministral 3 Reasoning-Aufruf
    
    Features:
    - LLM-basierte Komplexitäts-Klassifikation (keine Keywords!)
    - Adaptives Token-Budget Management
    - Reasoning-Traces Memory (FIFO)
    - Context-Window Optimization
    - Vision-Support
    """
    
    def __init__(
        self, 
        llama_model,
        temperature: float = 0.7,
        max_tokens_limit: int = 8192,
        max_reasoning_traces: int = 2,
        debug: bool = False,
        model_name: str = "Unknown Model"  # NEU: Modell-Name für Logging
    ):
        """
        Args:
            llama_model: llama-cpp-python Llama Instanz
            temperature: Sampling-Temperatur (default: 0.7)
            max_tokens_limit: Hardware-Limit (default: 8192)
            max_reasoning_traces: Max. Anzahl Traces im Memory (default: 2)
            debug: Debug-Logging aktivieren
            model_name: Name des verwendeten Modells (für Logging)
        """
        self.model = llama_model
        self.temperature = temperature
        self.max_tokens_limit = max_tokens_limit
        self.reasoning_memory = ReasoningMemory(max_traces=max_reasoning_traces)
        self.debug = debug
        self.model_name = model_name  # NEU: Speichere Modell-Namen
        self._native_thinking: Optional[bool] = None
        
        if debug:
            logger.setLevel(logging.DEBUG)
    
    def _native_thinking_template(self) -> bool:
        """Capability-Probe: Denkt das Modell per Template-Default nativ?

        Render-basiert statt String-Matching: Der Default-Render
        (add_generation_prompt=True, enable_thinking undefined) muss mit einem
        OFFENEN Think-Prefill enden. String-Matching wäre falsch — Gemma 4
        enthält 'enable_thinking' im Template, prefillt per Default aber einen
        GESCHLOSSENEN Thought-Block (denkt nicht nativ); Qwen3.x/Nemotron
        enden mit offenem '<think>' (denken nativ). Nativen Denkern darf kein
        [THINK]-Prompt injiziert werden — die Formate kollidieren.
        """
        if self._native_thinking is None:
            self._native_thinking = False
            try:
                probe = [{"role": "user", "content": "probe"}]
                rendered = ""
                loader = self._resolve_loader()
                render_fn = getattr(loader, "_render_chat_template", None)
                if render_fn is not None:
                    rendered = render_fn(probe, tools=None)
                else:
                    import jinja2
                    metadata = getattr(self.model, "metadata", None) or {}
                    template_str = metadata.get("tokenizer.chat_template", "") or ""
                    if template_str:
                        env = jinja2.Environment()
                        env.globals["raise_exception"] = lambda msg: (
                            (_ for _ in ()).throw(ValueError(msg))
                        )
                        rendered = env.from_string(template_str).render(
                            messages=probe,
                            add_generation_prompt=True,
                            bos_token="",
                            eos_token="",
                        )
                tail = rendered[-200:]
                for opener, closer in (
                    ("<think>", "</think>"),
                    ("<|channel>thought", "<channel|>"),
                ):
                    idx = tail.rfind(opener)
                    if idx >= 0 and closer not in tail[idx + len(opener):]:
                        self._native_thinking = True
                        break
            except Exception:
                self._native_thinking = False
            if self._native_thinking:
                logger.info(
                    "[CAPABILITY] Natives Thinking-Template erkannt — "
                    "[THINK]-Prompt-Injektion deaktiviert"
                )
        return self._native_thinking

    def _resolve_loader(self):
        """Liefert den Singleton-ModelLoader, wenn er dasselbe LLM hält.

        Über den Loader laufen Utility-Calls durch _process_text_only
        (enable_thinking-Steuerung + Think-Stripping); der rohe
        create_chat_completion-Pfad kennt beides nicht.
        """
        try:
            from scripts.model_loader import ModelLoader
            loader = ModelLoader._instance
            # Loader-Singleton hält immer das AKTIVE Modell — auch nach einem
            # Modellwechsel, bei dem self.model eine veraltete Referenz wäre.
            if loader is not None and loader.llm is not None:
                return loader
        except Exception:
            pass
        return None
    
    def estimate_complexity_with_llm(
        self, 
        query: str, 
        has_image: bool = False
    ) -> str:
        """
        Lässt das LLM selbst die Query-Komplexität einschätzen
        
        Vorteile:
        - Keine starren Keywords
        - Kontextabhängige Einschätzung
        - Versteht implizite Komplexität
        
        Returns:
            "simple", "medium", oder "complex"
        """
        
        # Meta-Prompt für Selbst-Klassifikation
        classification_prompt = f"""Analyze the following user query and classify its complexity level.

Query: "{query}"
Has Image: {"Yes" if has_image else "No"}

Classification Criteria:

SIMPLE:
- Direct factual questions with short answers
- Basic definitions
- Yes/no questions
- Small talk
- Example: "Who is the president?", "What does HTTP mean?"

MEDIUM:
- Explanations with moderate depth
- How-to guides
- Comparisons between 2-3 concepts
- Simple image descriptions
- Example: "How do I install Python?", "Explain REST vs GraphQL"

COMPLEX:
- Multi-step reasoning required
- Deep analysis or explanations
- Technical/scientific topics
- Complex image analysis (diagrams, charts)
- Code debugging
- Causal "why" questions
- Comparisons of 4+ items
- Example: "Explain quantum entanglement step-by-step", "Analyze economic impacts of AI"

Note: Images automatically increase complexity by one level (simple→medium, medium→complex).

Respond with ONLY ONE WORD: simple, medium, or complex"""

        try:
            loader = self._resolve_loader()
            if loader is not None:
                # Loader-Pfad: deaktiviert Thinking bei kleinem Budget und
                # strippt Reasoning-Markup — liefert direkt das Antwortwort.
                classification = loader.generate_response(
                    prompt=classification_prompt,
                    max_tokens=16,
                    temperature=0.3,
                ).strip().lower()
            else:
                # Schneller LLM-Call für Klassifikation (niedrige tokens)
                # ── CRITICAL: cuda_lock prevents concurrent llama.cpp access ──
                with _cuda_lock:
                    response = self.model.create_chat_completion(
                        messages=[
                            {"role": "user", "content": classification_prompt}
                        ],
                        temperature=0.3,  # Niedrig für konsistente Klassifikation
                        max_tokens=10,    # Nur ein Wort nötig
                        stream=False,
                        repeat_penalty=1.1,  # Verhindert Token-Repetition
                    )
                classification = response["choices"][0]["message"]["content"].strip().lower()
            
            # Validierung (tolerant: Wort auch in längerer Antwort finden)
            if classification not in ["simple", "medium", "complex"]:
                match = re.search(r"\b(simple|medium|complex)\b", classification)
                if match:
                    classification = match.group(1)
            if classification in ["simple", "medium", "complex"]:
                if self.debug:
                    logger.debug(f"LLM classified query as: {classification}")
                return classification
            else:
                # Fallback bei ungültiger Antwort
                logger.warning(f"Invalid LLM classification: {classification}, defaulting to 'medium'")
                return "medium"
        
        except Exception as e:
            logger.error(f"LLM classification failed: {e}, defaulting to 'medium'")
            return "medium"
    
    def get_system_prompt(self, enable_reasoning: bool) -> str:
        """
        Gibt optimierten System-Prompt mit NATIVEM Magistral [THINK]-Format.
        
        WICHTIG: Wenn ein custom system_message gesetzt wird, ERSETZT das
        Magistral-Template den default_system_message (der [THINK] enthält).
        → Wir MÜSSEN die [THINK]-Anweisung selbst einbetten.
        """
        
        base_instructions = """You are a helpful, knowledgeable AI assistant with access to various tools.

Core Guidelines:
- Provide accurate, well-structured answers
- Use tools when needed for current information or computations
- Be concise but thorough
- Cite sources when using external information
- For images: Analyze systematically (objects, colors, text, context, meaning)"""
        
        if enable_reasoning:
            if self._native_thinking_template():
                # Natives Reasoning-Modell (z.B. Qwen3.x): Das Template steuert
                # Thinking selbst — [THINK]-Injektion würde kollidieren.
                return base_instructions + """

Think through the problem carefully before answering.
Format your response using Markdown, and use LaTeX for any mathematical equations.
Answer in the same language as the input."""
            # ── NATIVES Magistral [THINK]-Format (aus default_system_message) ──
            # Das Modell wurde mit [THINK]...[/THINK] trainiert.
            # <thinking>-XML ist NICHT das native Format → wird ignoriert.
            reasoning_instructions = """

First draft your thinking process (inner monologue) until you arrive at a response.
Format your response using Markdown, and use LaTeX for any mathematical equations.
Write both your thoughts and the response in the same language as the input.

Your thinking process must follow the template below:
[THINK]
Your thoughts or/and draft, like working through an exercise on scratch paper.
Be thorough and work through the problem step-by-step:

Step 1: Understand the problem -- Break down components, identify what is needed
Step 2: Analyze relevant concepts -- Recall knowledge, consider relationships
Step 3: Develop approach -- Plan logical steps, consider alternatives
Step 4: Work through solution -- Execute step-by-step, show intermediate results
Step 5: Verify and refine -- Check for errors, ensure completeness

Use 5-10 steps for complex topics. Each step must be substantive.
Use the same language as the input.
[/THINK]
Here, provide a clear, well-structured final answer based on your reasoning.

IMPORTANT:
- ALWAYS use [THINK]...[/THINK] for EVERY complex question
- MINIMUM 5 substantive reasoning steps for complex questions
- Show your work and intermediate conclusions inside [THINK]
- The final answer after [/THINK] must be self-contained"""
            return base_instructions + reasoning_instructions
        else:
            fast_instructions = """
- Provide direct, concise answers for straightforward questions
- Skip detailed reasoning for simple factual queries
- Be efficient with token usage"""
            return base_instructions + fast_instructions
    
    def count_tokens_approximate(self, messages: List[Dict]) -> int:
        """
        Approximiert Token-Count für Messages
        
        Einfache Heuristik: ~1.3 tokens pro Wort
        Genug für Token-Budget Management
        """
        total = 0
        
        for msg in messages:
            # Role overhead
            total += 4
            
            # Content
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content.split()) * 1.3
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        total += len(block["text"].split()) * 1.3
        
        return int(total)
    
    def prune_context_if_needed(
        self,
        messages: List[Dict],
        max_context_tokens: int,
        keep_system: bool = True
    ) -> List[Dict]:
        """
        Kürzt Context wenn zu lang (FIFO-Strategie)
        
        Behält:
        - System-Prompt (immer)
        - Letzte 2-3 Turns (immer)
        - Ältere Messages bis Token-Limit
        """
        current_tokens = self.count_tokens_approximate(messages)
        
        if current_tokens <= max_context_tokens:
            return messages  # Alles OK
        
        # Split: System + History + Recent
        system_msg = messages[0] if keep_system and messages else None
        
        # Letzte 6 Messages = 3 Turns (user + assistant)
        recent_count = min(6, len(messages) - 1)
        recent = messages[-recent_count:] if len(messages) > recent_count else []
        history = messages[1:-recent_count] if len(messages) > recent_count + 1 else []
        
        # Lösche älteste History bis genug Platz
        while history:
            test_messages = [system_msg] + history + recent if system_msg else history + recent
            if self.count_tokens_approximate(test_messages) <= max_context_tokens:
                break
            history.pop(0)  # FIFO
        
        # Zusammensetzen
        result = []
        if system_msg:
            result.append(system_msg)
        result.extend(history)
        result.extend(recent)
        
        if self.debug:
            logger.debug(f"Context pruned: {current_tokens} → {self.count_tokens_approximate(result)} tokens")
        
        return result
    
    def parse_ministral_response(self, response: Dict) -> Dict[str, str]:
        """
        Extrahiert Reasoning + Answer aus Magistral Response
        
        Unterstützt VIER Formate (Prioritätsreihenfolge):
        1. [THINK]...[/THINK] -- Natives Magistral-Template-Format
        2. Control-Tokens: {"type": "thinking", "text": "..."} (List-Content)
        3. XML-Tags: <thinking>...</thinking> <answer>...</answer>
        4. Plain-Text Fallback
        """
        
        choice = response["choices"][0]
        content = choice["message"]["content"]
        
        # FORMAT 0: Natives <think>-Format (Qwen3.x u.a.)
        # Das Template befüllt <think> oft im Prompt vor — der Output enthält
        # dann NUR das schließende </think>; alles davor ist Reasoning.
        if isinstance(content, str) and "</think>" in content:
            before, after = content.split("</think>", 1)
            reasoning = before.replace("<think>", "").strip()
            answer = after.strip()
            return {
                "reasoning": reasoning,
                "answer": answer if answer else reasoning,
            }
        
        # FORMAT 1: [THINK]...[/THINK] -- Natives Magistral-Format
        # Das Modell-Template definiert [THINK]...[/THINK] als Reasoning-Marker.
        # Dies ist das primäre Format das das Modell generiert.
        if isinstance(content, str):
            
            think_match = re.search(
                r'\[THINK\](.*?)\[/THINK\]',
                content,
                re.DOTALL
            )
            
            if think_match:
                reasoning = think_match.group(1).strip()
                # Answer = alles NACH [/THINK]
                after_think = content.split('[/THINK]', 1)
                answer = after_think[1].strip() if len(after_think) > 1 else ""
                
                # Falls kein Text nach [/THINK], prüfe <answer>-Tags
                if not answer:
                    answer_match = re.search(
                        r'<answer>(.*?)</answer>',
                        content,
                        re.DOTALL | re.IGNORECASE
                    )
                    if answer_match:
                        answer = answer_match.group(1).strip()
                
                return {
                    "reasoning": reasoning,
                    "answer": answer if answer else content
                }
            
            # FORMAT 1b: [THINK] OHNE schließendes [/THINK]
            # Häufig bei Follow-up-Queries: LLM generiert [THINK]...(content)
            # aber schließt den Tag nicht. Ohne diesen Fallback wird reasoning=""
            # und der gesamte Content (inkl. [THINK]-Prefix) als answer gesetzt.
            if '[THINK]' in content and '[/THINK]' not in content:
                after_open = content.split('[THINK]', 1)[1].strip()
                if after_open:
                    logger.debug(
                        f"[THINK] ohne [/THINK] erkannt — "
                        f"extrahiere {len(after_open)} Zeichen als Reasoning"
                    )
                    return {
                        "reasoning": after_open,
                        "answer": after_open  # Volltext als Answer (Calling-Code nutzt nur reasoning)
                    }
        
        # FORMAT 2: Control-Tokens (List-Content von llama-cpp-python)
        if isinstance(content, list):
            reasoning_parts = []
            answer_parts = []
            
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        reasoning_parts.append(block.get("text", ""))
                    elif block.get("type") == "text":
                        answer_parts.append(block.get("text", ""))
            
            if reasoning_parts or answer_parts:
                return {
                    "reasoning": "\n".join(reasoning_parts),
                    "answer": "\n".join(answer_parts)
                }
        
        # FORMAT 3: XML-Tags (wenn Modell <thinking> nutzt)
        if isinstance(content, str):
            thinking_match = re.search(
                r'<thinking>(.*?)</thinking>', 
                content, 
                re.DOTALL | re.IGNORECASE
            )
            
            answer_match = re.search(
                r'<answer>(.*?)</answer>', 
                content, 
                re.DOTALL | re.IGNORECASE
            )
            
            if thinking_match or answer_match:
                reasoning = thinking_match.group(1).strip() if thinking_match else ""
                answer = answer_match.group(1).strip() if answer_match else ""
                
                if reasoning and not answer:
                    after_thinking = content.split('</thinking>', 1)
                    if len(after_thinking) > 1:
                        answer = after_thinking[1].strip()
                
                return {
                    "reasoning": reasoning,
                    "answer": answer if answer else content
                }
        
        # FORMAT 3: Fallback - Alles ist Answer (z.B. bei simple queries ohne reasoning)
        return {
            "reasoning": "",
            "answer": str(content)
        }
    
    def chat(
        self,
        query: str,
        image_path: Optional[str] = None,
        conversation_history: Optional[List[Dict]] = None,
        force_complexity: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Optimierter Chat mit adaptivem Reasoning
        
        Args:
            query: User-Query
            image_path: Optional Pfad zu Bild
            conversation_history: Bisherige Conversation
            force_complexity: Optional: Erzwinge Komplexität ("simple"/"medium"/"complex")
        
        Returns:
            {
                "answer": str,
                "reasoning": str,
                "complexity": str,
                "tokens_used": int,
                "token_budget": int
            }
        """
        
        # 1. Komplexität ermitteln (LLM-basiert!)
        if force_complexity and force_complexity in TOKEN_BUDGETS:
            complexity = force_complexity
            if self.debug:
                logger.debug(f"Forced complexity: {complexity}")
        else:
            complexity = self.estimate_complexity_with_llm(query, has_image=bool(image_path))
        
        # 2. Token-Budget
        token_config = TOKEN_BUDGETS[complexity]
        max_tokens = min(token_config.max_tokens, self.max_tokens_limit)
        
        if self.debug:
            logger.debug(f"Complexity: {complexity}, Token-Budget: {max_tokens}")
        
        # 3. System-Prompt
        enable_reasoning = token_config.enable_reasoning
        system_prompt = self.get_system_prompt(enable_reasoning)
        
        # 4. Messages aufbauen
        messages = [{"role": "system", "content": system_prompt}]
        
        # Alte Conversation
        if conversation_history:
            messages.extend(conversation_history)
        
        # Reasoning-Memory (nur letzte 2 Traces)
        memory_messages = self.reasoning_memory.get_context_for_llm()
        if memory_messages:
            messages.extend(memory_messages)
        
        # Aktuelle Query
        if image_path:
            # Cast für Multi-Content Messages (korrekt für LLM-APIs, Type-Checker versteht es nicht)
            from typing import Any
            messages.append(cast(Dict[str, Any], {
                "role": "user",
                "content": [
                    {"type": "text", "text": query},
                    {"type": "image_url", "image_url": {"url": f"file://{image_path}"}}
                ]
            }))
        else:
            messages.append({
                "role": "user",
                "content": query
            })
        
        # 5. Context-Pruning (Reserve für Answer)
        max_context_tokens = max_tokens - 1500  # Reserve für Answer
        messages = self.prune_context_if_needed(messages, max_context_tokens)
        
        # 6. LLM-Call
        if self.debug:
            logger.debug(f"Calling LLM with {len(messages)} messages, max_tokens={max_tokens}")
        
        try:
            # ── CRITICAL: cuda_lock prevents concurrent llama.cpp access ──
            with _cuda_lock:
                response = self.model.create_chat_completion(
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    repeat_penalty=1.1,  # Verhindert StartStart...ThinkThink Degeneration
                )
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return {
                "answer": f"Fehler bei der Verarbeitung: {str(e)}",
                "reasoning": "",
                "complexity": complexity,
                "tokens_used": 0,
                "token_budget": max_tokens,
                "error": str(e)
            }
        
        # 7. Response parsen
        parsed = self.parse_ministral_response(response)
        
        # 8. Memory updaten (wenn Reasoning vorhanden)
        if parsed.get("reasoning"):
            self.reasoning_memory.add_trace(
                query,
                parsed["reasoning"],
                parsed["answer"]
            )
        
        # 9. Tokens zählen
        usage = response.get("usage", {})
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        
        if self.debug:
            logger.debug(f"Response: {len(parsed['answer'])} chars, {total_tokens} total tokens ({completion_tokens} completion)")
            if parsed.get("reasoning"):
                logger.debug(f"Reasoning: {len(parsed['reasoning'])} chars")
        
        return {
            "answer": parsed["answer"],
            "reasoning": parsed.get("reasoning", ""),
            "complexity": complexity,
            "tokens_used": completion_tokens,  # Nur Completion-Tokens (nicht Prompt+Completion)
            "token_budget": max_tokens
        }
    
    def clear_memory(self):
        """Löscht Reasoning-Memory"""
        self.reasoning_memory.clear()
        if self.debug:
            logger.debug("Reasoning memory cleared")


# Utility-Funktion für einfache Integration
def create_optimizer(
    llama_model,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    debug: bool = False
) -> MinistralReasoningOptimizer:
    """
    Factory-Funktion für schnelle Initialisierung
    
    Usage:
        >>> from llama_cpp import Llama
        >>> model = Llama(model_path="path/to/model.gguf", n_ctx=8192)
        >>> optimizer = create_optimizer(model, debug=True)
        >>> result = optimizer.chat("Explain quantum computing")
    """
    return MinistralReasoningOptimizer(
        llama_model=llama_model,
        temperature=temperature,
        max_tokens_limit=max_tokens,
        debug=debug
    )
