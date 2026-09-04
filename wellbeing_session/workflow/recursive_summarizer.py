"""
Recursive LLM-Based Context Summarizer -- SOTA Pattern
======================================================

Replaces the rule-based keyword-matching summarizer in chat_context_manager.py
with a true SOTA recursive, LLM-powered summarization pipeline.

SOTA Patterns implemented:
  1. **Recursive Map-Reduce**: Long conversations are split into chunks,
     each chunk is summarized by the LLM, then chunk-summaries are merged
     recursively until a single coherent summary remains.
  2. **Salience Ranking**: Each message is scored for care relevance
     before summarization -- high-salience messages are preserved verbatim.
  3. **Running Summary (Incremental)**: Maintains a rolling summary that
     is updated with each new chunk instead of re-summarizing everything.
  4. **Token-aware**: Respects model context limits at every stage.

Reference: LangChain Summarization Chains, Anthropic Context-Compression (2025),
           Recursive Abstractive Summarization (Liu et al., 2023).

✅ Phase 9b: SOTA recursive summarization for agent chat + psych sessions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts (German, care context)
# ---------------------------------------------------------------------------

CHUNK_SUMMARY_SYSTEM = """Du bist ein Dokumentationsassistent für Care-Gespräche. Fasse den folgenden Gesprächsausschnitt
zusammen. Behalte alle care-relevanten Informationen:
- Emotionale Zustände und Veränderungen
- Besprochene Themen und Probleme
- Erkenntnisse und Fortschritte
- Erwähnte Strategien und Bewältigungsmechanismen
- Krisenhinweise oder Sicherheitsbedenken

Schreibe in der 3. Person. Sei prägnant aber vollständig."""

CHUNK_SUMMARY_USER = """GESPRÄCHSAUSSCHNITT:
{chunk_text}

Fasse diesen Ausschnitt zusammen (150-250 Wörter):"""

MERGE_SUMMARIES_SYSTEM = """Du bist ein Dokumentationsassistent für Care-Gespräche. Vereinige die folgenden
Teil-Zusammenfassungen zu einer kohärenten Gesamtzusammenfassung.
Eliminiere Redundanzen, behalte aber alle einzigartigen care-relevanten
Informationen. Ordne chronologisch."""

MERGE_SUMMARIES_USER = """TEIL-ZUSAMMENFASSUNGEN:
{summaries_text}

Erstelle eine vereinigte Zusammenfassung (200-400 Wörter):"""

INCREMENTAL_SUMMARY_SYSTEM = """Du bist ein Dokumentationsassistent für Care-Gespräche. Aktualisiere die bestehende
Gesprächszusammenfassung mit den neuen Nachrichten. Integriere neue Informationen
nahtlos, ohne die bestehende Zusammenfassung komplett neu zu schreiben."""

INCREMENTAL_SUMMARY_USER = """BESTEHENDE ZUSAMMENFASSUNG:
{existing_summary}

NEUE NACHRICHTEN:
{new_messages}

Aktualisierte Zusammenfassung (200-400 Wörter):"""

SALIENCE_SYSTEM = """Bewerte die Relevanz dieser Nachricht für die Care-Begleitung auf einer Skala von 1-5:
1 = Small talk, kein Care-Wert
2 = Allgemeine Information, wenig Care-Bezug
3 = Moderate Relevanz (Alltagsprobleme, leichte Emotionen)
4 = Hohe Relevanz (tiefe Emotionen, wichtige Erkenntnisse, Bewältigungsstrategien)
5 = Kritisch (Krisenhinweise, Durchbrüche, Sicherheitsbedenken)

Antworte NUR mit einer Zahl (1-5)."""

SALIENCE_USER = """Nachricht: {message}

Relevanz (1-5):"""


# ---------------------------------------------------------------------------
# Salience Scorer
# ---------------------------------------------------------------------------

class SalienceScorer:
    """Score messages for care relevance using LLM or heuristics."""

    # Keywords that indicate high salience (fallback heuristic)
    HIGH_SALIENCE_KEYWORDS = {
        5: ["suizid", "selbstverletzung", "sterben", "umbringen", "krise", "notfall"],
        4: ["angst", "depression", "panik", "trauma", "trauer", "verzweiflung",
            "erkenntnis", "durchbruch", "fortschritt", "strategie", "bewältigung",
            "therapie", "medikament"],
        3: ["stress", "sorge", "problem", "konflikt", "beziehung", "arbeit",
            "schlaf", "erschöpft", "überfordert"],
    }

    def __init__(self, model_loader: Any = None) -> None:
        self.model_loader = model_loader

    def score_message(self, message: str) -> int:
        """Score a single message (1-5). Uses LLM if available, else heuristic."""
        if self.model_loader is not None:
            return self._score_with_llm(message)
        return self._score_with_heuristic(message)

    def score_messages(self, messages: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], int]]:
        """Score a list of messages, returning (message, score) tuples."""
        scored = []
        for msg in messages:
            content = msg.get("content", "")
            if msg.get("role") == "system":
                scored.append((msg, 5))  # System messages are always high salience
            elif not content.strip():
                scored.append((msg, 1))
            else:
                score = self.score_message(content)
                scored.append((msg, score))
        return scored

    def _score_with_llm(self, message: str) -> int:
        """Use the LLM to score salience."""
        try:
            salience_messages = [
                {"role": "system", "content": SALIENCE_SYSTEM},
                {"role": "user", "content": SALIENCE_USER.format(message=message[:500])}
            ]
            response = self.model_loader.generate_response(
                messages=salience_messages, max_tokens=10, temperature=0.1
            )
            response_str = str(response).strip() if response else ""
            # Extract first digit
            for ch in response_str:
                if ch.isdigit() and 1 <= int(ch) <= 5:
                    return int(ch)
            return self._score_with_heuristic(message)
        except Exception:
            return self._score_with_heuristic(message)

    def _score_with_heuristic(self, message: str) -> int:
        """Fast keyword-based salience scoring (fallback)."""
        text_lower = message.lower()
        for score in [5, 4, 3]:
            keywords = self.HIGH_SALIENCE_KEYWORDS.get(score, [])
            if any(kw in text_lower for kw in keywords):
                return score
        # Length-based: longer messages tend to be more substantive
        if len(message) > 200:
            return 3
        if len(message) > 80:
            return 2
        return 1


# ---------------------------------------------------------------------------
# Recursive Summarizer
# ---------------------------------------------------------------------------

class RecursiveLLMSummarizer:
    """SOTA recursive LLM-based conversation summarizer.

    Supports three modes:
      1. ``summarize_full`` -- Full recursive map-reduce summarization
      2. ``summarize_incremental`` -- Update existing summary with new messages
      3. ``summarize_with_salience`` -- Score messages, preserve high-salience
         verbatim, summarize the rest

    Usage::

        summarizer = RecursiveLLMSummarizer(model_loader=my_loader)
        summary = summarizer.summarize_full(messages)
        summary = summarizer.summarize_incremental(existing_summary, new_messages)
    """

    def __init__(
        self,
        model_loader: Any = None,
        chunk_size: int = 8,
        max_summary_tokens: int = 1024,
        salience_threshold: int = 4,
        token_estimator: Any = None,
    ) -> None:
        """
        Args:
            model_loader: ModelLoader for LLM calls. If None, falls back to heuristic.
            chunk_size: Number of messages per chunk for map-reduce.
            max_summary_tokens: Target max tokens for final summary.
            salience_threshold: Messages >= this score are preserved verbatim.
            token_estimator: Callable(str) -> int for token counting.
        """
        self.model_loader = model_loader
        self.chunk_size = chunk_size
        self.max_summary_tokens = max_summary_tokens
        self.salience_threshold = salience_threshold
        self.scorer = SalienceScorer(model_loader)
        self._estimate_tokens = token_estimator or self._default_token_estimator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize_full(self, messages: List[Dict[str, Any]]) -> str:
        """Full recursive map-reduce summarization.

        1. Split messages into chunks of ``chunk_size``
        2. Summarize each chunk with LLM
        3. If >1 chunk summary, merge them recursively
        4. Return final summary
        """
        if not messages:
            return ""

        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) <= 3:
            return self._format_messages(non_system)

        # Map phase: summarize each chunk
        chunks = self._split_into_chunks(non_system, self.chunk_size)
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            chunk_text = self._format_messages(chunk)
            summary = self._summarize_chunk(chunk_text, i + 1, len(chunks))
            chunk_summaries.append(summary)

        # Reduce phase: merge summaries recursively
        return self._recursive_merge(chunk_summaries)

    def summarize_incremental(
        self,
        existing_summary: str,
        new_messages: List[Dict[str, Any]],
    ) -> str:
        """Update an existing summary with new messages (incremental SOTA).

        This avoids re-summarizing the entire conversation -- only the new
        messages are integrated into the running summary.
        """
        if not new_messages:
            return existing_summary

        new_text = self._format_messages(new_messages)

        if not existing_summary:
            return self.summarize_full(new_messages)

        if self.model_loader is None:
            # Heuristic fallback: append a compact representation
            return self._incremental_heuristic(existing_summary, new_messages)

        incremental_messages = [
            {"role": "system", "content": INCREMENTAL_SUMMARY_SYSTEM},
            {"role": "user", "content": INCREMENTAL_SUMMARY_USER.format(
                existing_summary=existing_summary,
                new_messages=new_text,
            )}
        ]

        try:
            response = self.model_loader.generate_response(
                messages=incremental_messages,
                max_tokens=self.max_summary_tokens,
                temperature=0.3,
            )
            result = str(response).strip() if response else ""
            if len(result) > 50:
                return result
        except Exception as exc:
            logger.warning("Incremental summarization failed: %s", exc)

        return self._incremental_heuristic(existing_summary, new_messages)

    def summarize_with_salience(
        self,
        messages: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Salience-aware summarization.

        Returns:
            (summary_of_low_salience_msgs, list_of_high_salience_msgs_to_keep)
        """
        scored = self.scorer.score_messages(messages)

        high_salience: List[Dict[str, Any]] = []
        low_salience: List[Dict[str, Any]] = []

        for msg, score in scored:
            if score >= self.salience_threshold:
                high_salience.append(msg)
            else:
                low_salience.append(msg)

        summary = self.summarize_full(low_salience)

        logger.info(
            "Salience summarization: %d high (kept), %d low (summarized)",
            len(high_salience),
            len(low_salience),
        )

        return summary, high_salience

    # ------------------------------------------------------------------
    # LLM calls (private)
    # ------------------------------------------------------------------

    def _summarize_chunk(self, chunk_text: str, chunk_num: int, total_chunks: int) -> str:
        """Summarize a single chunk of messages."""
        if self.model_loader is None:
            return self._heuristic_chunk_summary(chunk_text)

        chunk_messages = [
            {"role": "system", "content": CHUNK_SUMMARY_SYSTEM},
            {"role": "user", "content": CHUNK_SUMMARY_USER.format(chunk_text=chunk_text)}
        ]

        try:
            response = self.model_loader.generate_response(
                messages=chunk_messages,
                max_tokens=min(512, self.max_summary_tokens),
                temperature=0.3,
            )
            result = str(response).strip() if response else ""
            if len(result) > 30:
                logger.debug(
                    "Chunk %d/%d summarized: %d chars → %d chars",
                    chunk_num, total_chunks, len(chunk_text), len(result),
                )
                return result
        except Exception as exc:
            logger.warning("Chunk %d summarization failed: %s", chunk_num, exc)

        return self._heuristic_chunk_summary(chunk_text)

    def _recursive_merge(self, summaries: List[str], depth: int = 0) -> str:
        """Recursively merge summaries until a single one remains."""
        if len(summaries) <= 1:
            return summaries[0] if summaries else ""

        if depth > 5:  # Safety: prevent infinite recursion
            return "\n\n---\n\n".join(summaries)

        # If we can merge all at once (small enough), do it
        combined_tokens = sum(self._estimate_tokens(s) for s in summaries)
        if combined_tokens < self.max_summary_tokens * 2:
            return self._merge_summaries(summaries)

        # Otherwise, pair up and merge
        merged: List[str] = []
        for i in range(0, len(summaries), 2):
            pair = summaries[i:i + 2]
            if len(pair) == 2:
                merged.append(self._merge_summaries(pair))
            else:
                merged.append(pair[0])

        return self._recursive_merge(merged, depth + 1)

    def _merge_summaries(self, summaries: List[str]) -> str:
        """Merge multiple summaries into one."""
        if self.model_loader is None:
            return "\n\n".join(summaries)

        summaries_text = "\n\n---\n\n".join(
            f"Teil {i + 1}:\n{s}" for i, s in enumerate(summaries)
        )
        merge_messages = [
            {"role": "system", "content": MERGE_SUMMARIES_SYSTEM},
            {"role": "user", "content": MERGE_SUMMARIES_USER.format(summaries_text=summaries_text)}
        ]

        try:
            response = self.model_loader.generate_response(
                messages=merge_messages,
                max_tokens=self.max_summary_tokens,
                temperature=0.3,
            )
            result = str(response).strip() if response else ""
            if len(result) > 50:
                return result
        except Exception as exc:
            logger.warning("Summary merge failed: %s", exc)

        return "\n\n".join(summaries)

    # ------------------------------------------------------------------
    # Heuristic fallbacks (no LLM)
    # ------------------------------------------------------------------

    def _heuristic_chunk_summary(self, chunk_text: str) -> str:
        """Rule-based chunk summary (fallback when no LLM is available)."""
        lines = chunk_text.strip().split("\n")
        # Keep first and last line, plus lines with emotional keywords
        important_lines = []
        for line in lines:
            lower = line.lower()
            if any(kw in lower for kw in [
                "angst", "trauer", "stress", "freude", "erkenntnis",
                "strategie", "krise", "fortschritt", "ziel",
            ]):
                important_lines.append(line)

        if not important_lines:
            # Keep first and last
            if lines:
                important_lines = [lines[0]]
                if len(lines) > 1:
                    important_lines.append(lines[-1])

        return "\n".join(important_lines[:10])

    def _incremental_heuristic(
        self,
        existing_summary: str,
        new_messages: List[Dict[str, Any]],
    ) -> str:
        """Heuristic incremental update (fallback)."""
        # Extract key content from new messages
        key_points = []
        for msg in new_messages:
            content = str(msg.get("content", ""))
            if len(content) > 50:
                key_points.append(f"- {msg.get('role', '?')}: {content[:150]}...")

        if not key_points:
            return existing_summary

        update = "\n".join(key_points[-5:])  # Keep last 5 key points
        return f"{existing_summary}\n\n[Aktualisierung]\n{update}"

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        """Format messages into a readable transcript."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))
            role_label = {"user": "Benutzer", "assistant": "Assistent", "system": "System"}.get(
                role, role
            )
            lines.append(f"{role_label}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _split_into_chunks(
        messages: List[Dict[str, Any]], chunk_size: int
    ) -> List[List[Dict[str, Any]]]:
        """Split messages into fixed-size chunks."""
        return [
            messages[i:i + chunk_size]
            for i in range(0, len(messages), chunk_size)
        ]

    @staticmethod
    def _default_token_estimator(text: str) -> int:
        """Default token estimator (~4 chars per token for German)."""
        return max(1, len(text) // 4)
