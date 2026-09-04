"""
Tests für P0.5: Tool-Result Eviction (idempotente FS-Read-Tools).

Abgedeckt:
- Eviction-Reihenfolge (älteste zuerst, letzte K pro Tool bleiben intakt)
- Platzhalter-Format (Marker, Tool-Name, Wiederholhinweis)
- User/Assistant-Nachrichten bleiben unverändert
- Nicht-idempotente Tools werden nie evictiert
- Struktur-Erhaltung (role, tool_call_id, Reihenfolge, Länge)
- Trigger-Schwellwert via estimate_prompt_tokens (Heuristik)
- Idempotenter Zweidurchlauf
- Input-Mutation: Eingabe bleibt unverändert
- Robustheit: unbekanntes tool_call_id, String-JSON in tool_calls
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tool_result_eviction import (
    DEFAULT_KEEP_LAST,
    DEFAULT_TRIGGER_TOKENS,
    EVICTABLE_TOOLS,
    PLACEHOLDER_MARKER,
    evict_stale_tool_results,
)

ARG = {"file_path": "C:\\Dokumente\\testfile.py"}
BIG = "x" * 3000  # ~800 Tokens pro Resultat (Heuristik chars/3.8)


def build_history(results):
    """results: List[(tool_name, content)] → realistischer Message-Verlauf."""
    calls = []
    for i, (name, _content) in enumerate(results):
        calls.append({
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": name, "arguments": ARG},
        })
    messages = [
        {"role": "user", "content": "Lies alle Dateien und fasse zusammen"},
        {"role": "assistant", "content": "", "tool_calls": calls},
    ]
    for i, (_name, content) in enumerate(results):
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": content})
    return messages


class TestEvictionOrdering:
    def test_evicts_oldest_keeps_last_k(self):
        results = [("file_reader", BIG) for _ in range(4)]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(messages, keep_last=2, trigger_tokens=0)
        assert stats["evicted"] == 2
        # Indexe der Tool-Messages: user=0, assistant=1, tool=2..5
        assert new[2]["content"].startswith(PLACEHOLDER_MARKER)  # älteste evictiert
        assert new[3]["content"].startswith(PLACEHOLDER_MARKER)
        assert new[4]["content"] == BIG  # letzte K intakt
        assert new[5]["content"] == BIG

    def test_per_tool_last_k_independent(self):
        results = [
            ("file_reader", BIG),
            ("search_files", BIG),
            ("file_reader", BIG),
            ("search_files", BIG),
            ("file_reader", BIG),
            ("search_files", BIG),
        ]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(messages, keep_last=2, trigger_tokens=0)
        assert stats["evicted"] == 2  # je Tool 1 ältestes
        # file_reader: call_0 (idx 2) evictiert; call_2 (idx 4), call_4 (idx 6) intakt
        assert new[2]["content"].startswith(PLACEHOLDER_MARKER)
        assert new[4]["content"] == BIG
        assert new[6]["content"] == BIG
        # search_files: call_1 (idx 3) evictiert; call_3 (idx 5), call_5 (idx 7) intakt
        assert new[3]["content"].startswith(PLACEHOLDER_MARKER)
        assert new[5]["content"] == BIG
        assert new[7]["content"] == BIG

    def test_no_eviction_when_within_keep_last(self):
        results = [("file_reader", BIG), ("file_reader", BIG)]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(messages, keep_last=2, trigger_tokens=0)
        assert stats["evicted"] == 0
        assert new[2]["content"] == BIG
        assert new[3]["content"] == BIG


class TestPlaceholderFormat:
    def test_placeholder_content(self):
        results = [("file_reader", BIG), ("file_reader", BIG), ("file_reader", BIG)]
        messages = build_history(results)
        new, _ = evict_stale_tool_results(messages, keep_last=1, trigger_tokens=0)
        ph = new[2]["content"]
        assert ph.startswith(PLACEHOLDER_MARKER)
        assert "file_reader" in ph
        assert "file_path" in ph  # Parameter kompakt enthalten
        assert "3000 Zeichen" in ph  # Originalgröße dokumentiert
        assert "idempotent" in ph  # Wiederhol-Hinweis für das Modell
        assert len(ph) < 300  # kompakt: deutlich kleiner als Original

    def test_structure_preserved(self):
        results = [("file_reader", BIG) for _ in range(3)]
        messages = build_history(results)
        new, _ = evict_stale_tool_results(messages, keep_last=1, trigger_tokens=0)
        assert len(new) == len(messages)
        assert [m["role"] for m in new] == [m["role"] for m in messages]


class TestNeverEvict:
    def test_user_and_assistant_untouched(self):
        results = [("file_reader", BIG) for _ in range(4)]
        messages = build_history(results)
        user_before = messages[0]["content"]
        assistant_before = copy.deepcopy(messages[1])
        new, _ = evict_stale_tool_results(messages, keep_last=0, trigger_tokens=0)
        assert new[0]["content"] == user_before
        assert new[1] == assistant_before  # Assistant-Messages nie geändert

    def test_non_idempotent_tools_never_evicted(self):
        results = [
            ("file_writer", BIG),
            ("code_executor", BIG),
            ("web_search", BIG),
            ("rag_search", BIG),
            ("pdf_extract", BIG),
        ]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(messages, keep_last=0, trigger_tokens=0)
        assert stats["evicted"] == 0
        for i, (_name, content) in enumerate(results):
            assert new[2 + i]["content"] == BIG


class TestTriggerThreshold:
    def test_small_context_no_eviction_with_default_trigger(self):
        # 5 × 100 Zeichen ≈ 130 Tokens < DEFAULT_TRIGGER_TOKENS (3000)
        results = [("file_reader", "y" * 100) for _ in range(5)]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(messages)
        assert stats["evicted"] == 0
        assert new[2]["content"] == "y" * 100

    def test_large_context_evicts_with_default_trigger(self):
        # 8 × 3000 Zeichen ≈ 6300 Tokens > 3000 → Eviction aktiv
        results = [("file_reader", BIG) for _ in range(8)]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(messages)
        assert stats["evicted"] == 8 - DEFAULT_KEEP_LAST
        assert stats["tokens_before"] > DEFAULT_TRIGGER_TOKENS
        assert stats["tokens_after"] < stats["tokens_before"]


class TestIdempotencyAndSafety:
    def test_double_run_is_stable(self):
        results = [("file_reader", BIG) for _ in range(4)]
        messages = build_history(results)
        first, stats1 = evict_stale_tool_results(messages, keep_last=2, trigger_tokens=0)
        assert stats1["evicted"] == 2
        second, stats2 = evict_stale_tool_results(first, keep_last=2, trigger_tokens=0)
        assert stats2["evicted"] == 0  # bereits evictierte bleiben stehen
        assert second == first  # stabil

    def test_new_result_extends_window(self):
        # Nach Eviction kommt ein neues Resultat dazu → Fenster rückt nach
        results = [("file_reader", BIG) for _ in range(3)]
        messages = build_history(results)
        first, _ = evict_stale_tool_results(messages, keep_last=2, trigger_tokens=0)
        # neues Resultat (call_3) wird angehängt
        first.append({
            "role": "assistant", "content": "", "tool_calls": [
                {"id": "call_3", "type": "function",
                 "function": {"name": "file_reader", "arguments": ARG}},
            ],
        })
        first.append({"role": "tool", "tool_call_id": "call_3", "content": BIG})
        second, stats2 = evict_stale_tool_results(first, keep_last=2, trigger_tokens=0)
        assert stats2["evicted"] == 1  # das nächstälteste echte Ergebnis
        assert not second[4]["content"].startswith(PLACEHOLDER_MARKER)
        assert second[6]["content"] == BIG  # call_3 (neueste) intakt

    def test_input_not_mutated(self):
        results = [("file_reader", BIG) for _ in range(4)]
        messages = build_history(results)
        snapshot = copy.deepcopy(messages)
        evict_stale_tool_results(messages, keep_last=2, trigger_tokens=0)
        assert messages == snapshot

    def test_unknown_tool_call_id_skipped(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "ghost_call", "content": BIG * 2},
        ]
        new, stats = evict_stale_tool_results(messages, keep_last=0, trigger_tokens=0)
        assert stats["evicted"] == 0
        assert new[1]["content"] == BIG * 2

    def test_string_json_in_tool_calls_handled(self):
        # Manche Backends liefern function/arguments als JSON-String
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c0", "type": "function",
                 "function": '{"name": "file_reader", "arguments": "{}"}'},
                {"id": "c1", "type": "function",
                 "function": '{"name": "file_reader", "arguments": "{}"}'},
                {"id": "c2", "type": "function",
                 "function": '{"name": "file_reader", "arguments": "{}"}'},
            ]},
            {"role": "tool", "tool_call_id": "c0", "content": BIG},
            {"role": "tool", "tool_call_id": "c1", "content": BIG},
            {"role": "tool", "tool_call_id": "c2", "content": BIG},
        ]
        new, stats = evict_stale_tool_results(messages, keep_last=1, trigger_tokens=0)
        assert stats["evicted"] == 2
        assert new[2]["content"].startswith(PLACEHOLDER_MARKER)
        assert new[3]["content"].startswith(PLACEHOLDER_MARKER)
        assert new[4]["content"] == BIG  # c2 (neuestes) intakt


class TestDefaultsAndOverrides:
    def test_default_constants_sane(self):
        assert DEFAULT_KEEP_LAST == 2
        assert DEFAULT_TRIGGER_TOKENS >= 1000
        assert EVICTABLE_TOOLS == frozenset(
            {"file_reader", "search_files", "list_directory"}
        )
        assert "file_writer" not in EVICTABLE_TOOLS
        assert "code_executor" not in EVICTABLE_TOOLS

    def test_evictable_tools_override(self):
        results = [
            ("list_directory", BIG),
            ("file_reader", BIG),
            ("list_directory", BIG),
            ("file_reader", BIG),
        ]
        messages = build_history(results)
        new, stats = evict_stale_tool_results(
            messages, keep_last=1, trigger_tokens=0,
            evictable_tools=frozenset({"list_directory"}),
        )
        assert stats["evicted"] == 1  # nur das älteste list_directory
        assert new[2]["content"].startswith(PLACEHOLDER_MARKER)
        assert new[3]["content"] == BIG  # file_reader nie evictiert
        assert new[5]["content"] == BIG

    def test_empty_messages(self):
        new, stats = evict_stale_tool_results([])
        assert new == []
        assert stats == {"evicted": 0, "tokens_before": 0, "tokens_after": 0}