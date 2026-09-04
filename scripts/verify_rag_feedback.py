# -*- coding: utf-8 -*-
"""Verifiziert den RAG-Feedback-Pfad (Teil A: Live-Property, Teil B: Logger,
Teil C: Wilson-Scores + SmartFusion-Boost). Läuft ohne App-Start."""
import os
import sys
import sqlite3

# Repo-Root auf sys.path (Skript liegt in scripts/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Teil A: Live-Property (kein init!) ---
from agent_chatbot_logic import AgentChatbotLogic
cl = AgentChatbotLogic.__new__(AgentChatbotLogic)  # bewusst ohne __init__
# Modelliert den Produktionspfad: orchestrator.tools (ToolManager).rag
# -> get_global_rag_store() -> UnifiedRagStore._search_manager (search.py).
class FakeSM:
    _last_search_chunk_ids = ["c1", "c2"]
    _last_search_chunk_scores = [0.9, 0.5]

class FakeRS:
    _search_manager = FakeSM()

class FakeTools:
    rag = FakeRS()

class FakeOrch:
    tools = FakeTools()

cl.orchestrator = FakeOrch()
ok_a = (cl._last_rag_chunk_ids == ["c1", "c2"]) and (cl._last_rag_chunk_scores == [0.9, 0.5])
print(f"A: live-property liefert Chunk-IDs: {'PASS' if ok_a else 'FAIL'}")
assert ok_a, "Property liefert nicht die letzten Chunk-IDs"

# --- Teil B: Feedback-Logger mit echten Chunk-IDs ---
from utils.feedback_logger import FeedbackLogger
import tempfile
tmpdb = os.path.join(tempfile.gettempdir(), "test_rag_feedback.db")
if os.path.exists(tmpdb): os.remove(tmpdb)
fl = FeedbackLogger(db_path=tmpdb)
ok_b = fl.log_feedback(
    query="What is the capital of France?",
    user_rating="up",
    answer_text="Paris is the capital.",
    metadata={"chunk_ids": ["doc1::para3", "doc2::para7"], "chunk_scores": [0.91, 0.78]}
)
con = sqlite3.connect(tmpdb)
rows = con.execute(
    "SELECT query, feedback, metadata FROM retrieval_feedback ORDER BY id DESC LIMIT 1"
).fetchall()
con.close()
row = rows[0]
import json
meta = json.loads(row[2]) if row[2] else {}
ok_b = ok_b and row[1] == "up" and len(meta.get("chunk_ids", [])) == 2
print(f"B: Feedback-Logger speichert Chunk-IDs: {'PASS' if ok_b else 'FAIL'}  (meta={meta.get('chunk_ids')})")
assert ok_b, "chunk_ids wurden nicht gespeichert"

# --- Teil C: Wilson-Update + SmartFusion-Boost ---
from agent.rag_store.core.quality_manager import RAGQualityManager
from agent.smart_fusion_engine import SmartFusionEngine

qm = RAGQualityManager(db_path=tmpdb)
qm.record_retrieval_feedback("doc1::para3", "up")
qm.record_retrieval_feedback("doc1::para3", "up")
qm.record_retrieval_feedback("doc1::para3", "up")
qm.record_retrieval_feedback("doc2::para7", "down")
scores = qm.load_wilson_utility_scores()
print(f"C1: Wilson-Scores: doc1::para3={scores.get('doc1::para3')}, doc2::para7={scores.get('doc2::para7')}")
ok_c1 = scores.get("doc1::para3", 0) > 0.5 and scores.get("doc2::para7", 0) < 0.5
print(f"   doc1 (2x up) > doc2 (1x down): {'PASS' if ok_c1 else 'FAIL'}")
assert ok_c1, "Wilson-Orderung falsch"

# debug=True absichtlich: deckt den Score-Statistiken-Block ab (früher KeyError
# 'original_score' auf den Roh-Input-Dicts — Production-Bug, fixiert 2026-08-21).
fusion = SmartFusionEngine(
    faiss_weight=0.6, kg_weight=0.4, hybrid_bonus=0.15, debug=True,
    use_wilson_boost=True, wilson_boost_weight=0.3,
)
faiss_res = [
    {"chunk_id": "doc1::para3", "score": 0.91, "metadata": {"doc_id": "doc1"}, "content": "a"},
    {"chunk_id": "doc2::para7", "score": 0.78, "metadata": {"doc_id": "doc2"}, "content": "b"},
    {"chunk_id": "doc3::para1", "score": 0.55, "metadata": {"doc_id": "doc3"}, "content": "c"},
]
kg_res = [
    {"chunk_id": "doc3::para1", "score": 0.99, "metadata": {"doc_id": "doc3"}, "content": "c"},
]
out = fusion.fuse(faiss_res, kg_res)
print("C2: Fuse-Ergebnis (Top 3):")
for r in out[:3]:
    print(f"   {r['chunk_id']:<14} combined={r['combined_score']:.3f} wilson={r.get('wilson_score')}")
best = out[0]["chunk_id"]
print(f"   Beste: {best}  (erwartet: doc1::para3, Wilson 0.75 vs doc3::para1 Wilson 0.5)")
ok_c2 = best == "doc1::para3"
print(f"   Wilson-Boost beeinflusst Ranking: {'PASS' if ok_c2 else 'FAIL'}")
assert ok_c2, "Wilson-Boost wurde nicht angewendet"

# Cleanup
os.remove(tmpdb)
print("\n✅ ALLE FEEDBACK-PFAD-TESTS PASSIERT")