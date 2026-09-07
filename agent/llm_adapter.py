"""LLM Adapter abstraction and default implementation.

Provides a capability-driven interface for KG extraction clients.
"""
from typing import Any, Dict, Optional


class LLMAdapter:
    """Abstract adapter interface."""
    def supports_grammar(self) -> bool:
        raise NotImplementedError()

    def generate_with_grammar(self, messages, grammar_str: str, max_tokens: int, temperature: float) -> str:
        raise NotImplementedError()

    def generate_response(self, messages, max_tokens: int, temperature: float, stop: Optional[list] = None) -> str:
        raise NotImplementedError()

    def repair_response(self, messages, grammar_str: str, original_response: str, max_tokens: int) -> str:
        """Attempt to repair a malformed JSON response using constrained generation.

        Default: return empty string (no repair).
        """
        raise NotImplementedError()


class DefaultLLMAdapter(LLMAdapter):
    def __init__(self, llm_client: Any):
        self.llm = llm_client

    def supports_grammar(self) -> bool:
        return bool(self.llm and hasattr(self.llm, 'generate_with_grammar'))

    def generate_with_grammar(self, messages, grammar_str: str, max_tokens: int, temperature: float) -> str:
        if not self.llm:
            return ""
        if hasattr(self.llm, 'generate_with_grammar'):
            return self.llm.generate_with_grammar(messages=messages, grammar_str=grammar_str, max_tokens=max_tokens, temperature=temperature)
        return ""

    def generate_response(self, messages, max_tokens: int, temperature: float, stop: Optional[list] = None) -> str:
        if not self.llm:
            return ""
        if hasattr(self.llm, 'generate_response'):
            return self.llm.generate_response(messages=messages, max_tokens=max_tokens, temperature=temperature, stop=stop)
        # Kein generischer Fallback: Llama.generate() nimmt Token-IDs, nicht messages.
        # Wenn generate_response fehlt, ist das wrapped Objekt falsch konfiguriert.
        return ""

    def repair_response(self, messages, grammar_str: str, original_response: str, max_tokens: int) -> str:
        """Repair by re-invoking grammar-based generation with strict instructions.

        If grammar is available use it; otherwise attempt a focused generate_response.
        """
        if not self.llm:
            return ""

        repair_messages = [
            {"role": "system", "content": "Du bist ein JSON‑Reparaturagent. Gib AUSSCHLIESSLICH valides JSON zurück, keine Erklärungen."},
            {"role": "user", "content": f"Die vorherige Antwort war strukturell fehlerhaft. Korrigiere nur das JSON und gib nichts sonst aus. ORIGINAL:\n{original_response}"}
        ]

        try:
            if hasattr(self.llm, 'generate_with_grammar'):
                return self.llm.generate_with_grammar(messages=repair_messages, grammar_str=grammar_str, max_tokens=max_tokens, temperature=0.0)
            if hasattr(self.llm, 'generate_response'):
                return self.llm.generate_response(messages=repair_messages, max_tokens=max_tokens, temperature=0.0)
        except Exception:
            return ""
        return ""
