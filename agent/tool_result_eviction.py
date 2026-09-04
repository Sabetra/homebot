"""
P0.5 (2026-08-24): Tool-Result Eviction für idempotente FS-Read-Tools.

Problem
-------
Der ReAct-Loop trägt in ``state["messages"]`` alle Tool-Ergebnisse aller
Iterationen (bis zu ``max_iterations``) in jedes LLM-Prompt. Idempotente
Dateisystem-Read-Ergebnisse (``file_reader``, ``search_files``,
``list_directory``) werden nach 2-3 Iterationen zum "alten Wissen": Die
Modelle haben die nötigen Fakten bereits genutzt (oder in der Zwischensynthese
fixiert), aber die Resultate belegen weiterhin Kontext-Budget und verwässern
die Aufmerksamkeit der neueren, relevanten Messages (Context Rot).

Lösung
------
:func:`evict_stale_tool_results` ersetzt den *Content* alter
Tool-Result-Messages durch einen kompakten Platzhalter (~150 Zeichen).
Die Struktur (``role``, ``tool_call_id``, Tool-Call-Pairing) bleibt
vollständig erhalten — das Prompt bleibt ein gültiger Chat-Verlauf. Die
letzten ``keep_last`` (K=2) Resultate je evictierbarem Tool bleiben
intakt, damit das Modell immer die aktuellsten Ergebnisse vollständig
sieht. Da nur idempotente Tools evictiert werden, ist ein erneuter
Aufruf des Tools sicher (das Platzhalter teilt dem Modell das mit).

Nicht-Evictierbar
-----------------
- User- und Assistant-Messages (nie)
- Nicht-idempotente Tools (``file_writer``, ``code_executor``,
  ``web_search``, ``rag_search``, ``pdf_extract``, ...)
- Bereits evictierte Platzhalter (idempotenter Zweidurchlauf)

Token-Budget
------------
Das Trigger-Schwellwert nutzt
:func:`utils.token_manager.estimate_prompt_tokens` mit deterministischer
Heuristik (``use_tiktoken=False``) — keine externe Abhängigkeit in dieser
Schicht, stabiles Verhalten in Tests.

Referenz: ``docs/WORKDOC_FILESYSTEM_CONTEXT_SAFETY_20260824.md`` (P0.5)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Nur diese Tools dürfen evictiert werden (idempotente Lese-Operationen).
EVICTABLE_TOOLS: frozenset = frozenset({
    "file_reader",
    "search_files",
    "list_directory",
})

#: Anzahl der letzten Resultate pro Tool, die vollständig erhalten bleiben.
DEFAULT_KEEP_LAST: int = 2

#: Eviction greift erst, wenn die geschätzte Prompt-Länge diesen Wert
#: (Tokens) überschreitet. Darunter lohnt sich die Verdichtung nicht.
DEFAULT_TRIGGER_TOKENS: int = 3000

#: Prefix des Platzhalters — dient auch als Marker für den
#: idempotenten Zweidurchlauf (bereits evictierte Messages werden übersprungen).
PLACEHOLDER_MARKER = "[EVICTED]"


def _tool_call_index(
    messages: List[Dict[str, Any]]
) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """Baue Mapping ``tool_call_id → (tool_name, args)`` aus Assistant-Messages.

    Robust gegenüber String-JSON in ``function`` / ``arguments`` (kommt von
    manchen LLM-Backends / Recovery-Parsern).
    """
    index: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id")
            if not tc_id:
                continue
            func = tc.get("function") or {}
            if isinstance(func, str):
                try:
                    func = json.loads(func)
                except (ValueError, TypeError):
                    func = {}
            if not isinstance(func, dict):
                continue
            name = func.get("name")
            args = func.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = None
            if not isinstance(args, dict):
                args = {}
            if name and str(tc_id) not in index:
                index[str(tc_id)] = (str(name), args)
    return index


def _format_params(args: Dict[str, Any], max_chars: int = 80) -> str:
    """Kompakte, lesbare Darstellung der Tool-Argumente für den Platzhalter."""
    try:
        raw = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raw = str(args)
    if len(raw) > max_chars:
        raw = raw[: max(1, max_chars - 3)] + "..."
    return raw or "{}"


def _placeholder(tool_name: str, args: Dict[str, Any], original_chars: int) -> str:
    return (
        f"{PLACEHOLDER_MARKER} {tool_name}({_format_params(args)}) lieferte "
        f"{original_chars} Zeichen (altes Ergebnis, evictiert). "
        f"Das Tool ist idempotent — bei Bedarf erneut aufrufen."
    )


def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    from utils.token_manager import estimate_prompt_tokens

    text = "\n".join(str(m.get("content") or "") for m in messages)
    return estimate_prompt_tokens(text, use_tiktoken=False)


def evict_stale_tool_results(
    messages: List[Dict[str, Any]],
    keep_last: int = DEFAULT_KEEP_LAST,
    trigger_tokens: int = DEFAULT_TRIGGER_TOKENS,
    evictable_tools: Optional[frozenset] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Ersetze alte idempotente Tool-Resultate durch kompakte Platzhalter.

    Reine Funktion: verändert die Eingabe nicht, liefert eine neue Liste.

    Args:
        messages: Chat-Messages (``user`` / ``assistant`` mit ``tool_calls`` /
            ``tool`` mit ``tool_call_id``).
        keep_last: Anzahl der zuletztsten Resultate pro evictierbarem Tool,
            die vollständig erhalten bleiben (Default: 2).
        trigger_tokens: Mindest-Schwellwert der geschätzten Token-Zahl des
            Gesamtprompts, ab dem Eviction aktiv wird (Default: 3000).
        evictable_tools: Allowlist-Override (Default:
            :data:`EVICTABLE_TOOLS`).

    Returns:
        Tuple ``(new_messages, stats)`` mit
        ``stats = {"evicted": int, "tokens_before": int, "tokens_after": int}``.
    """
    if not messages:
        return messages, {"evicted": 0, "tokens_before": 0, "tokens_after": 0}

    evictable = evictable_tools if evictable_tools is not None else EVICTABLE_TOOLS
    keep_last = max(0, int(keep_last))
    trigger_tokens = max(0, int(trigger_tokens))

    index = _tool_call_index(messages)

    # Kandidaten chronologisch sammeln (nur noch nicht evictierte).
    candidates_by_tool: Dict[str, List[int]] = {}
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        tc_id = msg.get("tool_call_id")
        if tc_id is None:
            continue
        entry = index.get(str(tc_id))
        if entry is None:
            continue
        tool_name = entry[0]
        if tool_name not in evictable:
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.lstrip().startswith(PLACEHOLDER_MARKER):
            continue  # bereits evictiert → idempotenter Zweidurchlauf
        candidates_by_tool.setdefault(tool_name, []).append(i)

    if not any(len(ids) > keep_last for ids in candidates_by_tool.values()):
        return messages, {"evicted": 0, "tokens_before": 0, "tokens_after": 0}

    tokens_before = _estimate_tokens(messages)
    if tokens_before < trigger_tokens:
        # Prompt noch klein genug — Eviction würde kein relevantes Budget
        # freisetzen. Bewusste frühe Rückgabe ohne Side-Effekte.
        return messages, {
            "evicted": 0,
            "tokens_before": tokens_before,
            "tokens_after": tokens_before,
        }

    new_messages = [dict(m) for m in messages]  # Shallow-Copy: Input bleibt unverändert
    evicted = 0
    for tool_name, ids in candidates_by_tool.items():
        evict_ids = ids[: len(ids) - keep_last] if len(ids) > keep_last else []
        for i in evict_ids:
            msg = new_messages[i]
            original = msg.get("content")
            original_chars = len(original) if isinstance(original, str) else 0
            _name, args = index[str(msg.get("tool_call_id"))]
            msg["content"] = _placeholder(_name, args, original_chars)
            evicted += 1

    tokens_after = _estimate_tokens(new_messages)
    logger.info(
        "[EVICT] %d alte Tool-Resultate evictiert (Tokens: %d → %d)",
        evicted, tokens_before, tokens_after,
    )
    return new_messages, {
        "evicted": evicted,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
    }