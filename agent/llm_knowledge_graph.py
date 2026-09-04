#!/usr/bin/env python3
"""
LLM-basierte Knowledge Graph Erstellung für RAG-Systeme
Nutzt lokales Mistral 3.2 Small GGUF für hochwertige Entity-Relation-Extraktion
"""

import json
import re
import logging
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ============================================================================
# NORMALISIERUNGSFUNKTION FÜR KG-TRIPLES
# ============================================================================

def normalize_text(text: str) -> str:
    """
    Normalisiert Text für konsistente KG-Triple-Formatierung (Display-Ebene).
    Ersetzt Unterstriche durch Leerzeichen, behält aber technische IDs bei.
    
    Diese Funktion wird automatisch auf alle neuen KG-Triples angewendet,
    um sicherzustellen, dass keine Unterstriche oder inkonsistente Formatierung
    in die Datenbank gelangt.
    
    HINWEIS: Für Matching/Dedup nutze normalize_entity_for_matching().
    """
    if not text:
        return text
    
    # Behalte technische IDs (psych_*, session_*, hash-artige Strings)
    if text.startswith('psych_') or text.startswith('session_'):
        return text
    
    # Ersetze Unterstriche durch Leerzeichen
    normalized = text.replace('_', ' ')
    
    # Entferne doppelte Leerzeichen
    # O(n) statt while-Loop (O(n^2)); verhaltensäquivalent: kollabiert nur
    # ASCII-Leerzeichen-Läufe >=2 zu einem Leerzeichen (Tabs/Newlines bleiben).
    normalized = re.sub(r' {2,}', ' ', normalized)
    
    return normalized.strip()


# ============================================================================
# SOTA ENTITY NORMALISIERUNG FÜR MATCHING/DEDUP
# ============================================================================

# Titel/Honorare die beim Matching entfernt werden (Originaltext bleibt erhalten)
# Periods are MANDATORY in degree abbreviations to avoid matching common words
# (e.g. "M.A." must not match the "ma" in "Max", "Mag." must not match "Magistral")
_TITLE_PREFIXES = re.compile(
    r'^(?:Prof\.?\s*|Dr\.?\s*|Herr\s+|Frau\s+|Dipl\.?[-\s]?\w*\.?\s*|'
    r'Mag\.\s*|Ing\.\s*|Mr\.?\s*|Mrs\.?\s*|Ms\.?\s*|Sir\s+|'
    r'PhD\.?\s*|M\.A\.?\s*|B\.A\.?\s*|M\.Sc\.?\s*|B\.Sc\.?\s*)+',
    re.IGNORECASE
)

# Klammerzusätze wie "(Psychologie)" oder "(Wirtschaft)"
_PARENTHETICAL = re.compile(r'\s*\([^)]*\)\s*')


def normalize_entity_for_matching(text: str) -> str:
    """
    ★ SOTA: Tiefe Entity-Normalisierung für Matching und Dedup.
    
    Wird für `normalized_text` in kg_entities genutzt und für Entity-basierte
    Suche. Erzeugt eine kanonische Form, die Varianten wie:
      "Prof. Dr. Schmidt" → "schmidt"
      "CBT-I" → "cbt-i"
      "Kognitive Verhaltenstherapie (CBT)" → "kognitive verhaltenstherapie"
    zusammenführt.
    
    Das Original bleibt in `entity_text` erhalten — diese Funktion
    dient NUR dem Matching, nicht der Anzeige.
    
    Args:
        text: Originaler Entity-Text
        
    Returns:
        Normalisierte Form für Matching (lowercase, ohne Titel, ohne Klammern)
    """
    if not text:
        return text
    
    # Behalte technische IDs unverändert
    if text.startswith('psych_') or text.startswith('session_'):
        return text.lower().strip()
    
    normalized = text.strip()
    
    # 1. Unterstriche → Leerzeichen
    normalized = normalized.replace('_', ' ')
    
    # 2. Lowercase
    normalized = normalized.lower()
    
    # 3. Klammerzusätze entfernen: "Depression (Wirtschaft)" → "depression"
    normalized = _PARENTHETICAL.sub(' ', normalized)
    
    # 4. Titel/Honorare entfernen: "prof. dr. schmidt" → "schmidt"
    normalized = _TITLE_PREFIXES.sub('', normalized)
    
    # 5. Mehrfache Leerzeichen → eins
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized.strip()


# Sentence-Boundary für Hard-Split überlanger Paragraphen
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])')


def split_oversized_for_kg(text: str, max_chars: int) -> List[str]:
    """
    Teilt einen Text auf, der **alleine schon** länger als ``max_chars`` ist,
    in Stücke ≤ ``max_chars``. Versucht erst Satz-Grenzen, fällt dann auf
    Whitespace-Grenzen, im Notfall auf Hard-Char-Cut zurück.
    
    Hintergrund: Web-Pages liefern oft monolithische Paragraphen (kein \\n\\n).
    Reine Paragraphen-Merger wie ``current_text and would_overflow`` flushen
    erst, wenn ``current_text`` bereits gefüllt ist — ein einzelner Riesen-
    Paragraph rutscht ungeteilt durch und erzeugt 18k+ Char Chunks, die
    den KG-LLM-Call auf 200+ Sekunden hochziehen.
    
    Returns:
        Liste von Strings, jeder ≤ max_chars, in Original-Reihenfolge.
        Wenn ``len(text) <= max_chars``, wird ``[text]`` zurückgegeben.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    
    out: List[str] = []
    # Erst auf Satz-Boundaries splitten
    sentences = _SENTENCE_SPLIT.split(text) if max_chars >= 200 else [text]
    
    buf = ""
    for s in sentences:
        if not s:
            continue
        # Einzelner Satz schon zu groß? → auf Whitespace splitten, Notfall hart cutten
        if len(s) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            words = s.split(" ")
            wbuf = ""
            for w in words:
                if len(w) > max_chars:
                    # extrem-Fall: Hard-Cut
                    if wbuf:
                        out.append(wbuf)
                        wbuf = ""
                    for i in range(0, len(w), max_chars):
                        out.append(w[i:i + max_chars])
                    continue
                if len(wbuf) + len(w) + 1 > max_chars and wbuf:
                    out.append(wbuf)
                    wbuf = w
                else:
                    wbuf = wbuf + " " + w if wbuf else w
            if wbuf:
                buf = wbuf
            continue
        
        if len(buf) + len(s) + 1 > max_chars and buf:
            out.append(buf)
            buf = s
        else:
            buf = buf + " " + s if buf else s
    
    if buf:
        out.append(buf)
    return out

# ============================================================================
# KG QUALITY-GATE KONFIGURATION (SOTA: Keine Hard-Limits)
# ============================================================================
#
# ARCHITEKTUR-ENTSCHEIDUNG (CoT/ToT-validiert, 2026-03-28):
#
# SOTA-Systeme (GraphRAG, LightRAG, SynthKG/ICLR 2026) verwenden KEINE
# harten Count-Limits für Triple-Extraktion. Stattdessen:
#   1. Per-Chunk-Extraktion → natürliche Begrenzung (5-20 Triples/Chunk)
#   2. Confidence-Threshold → Low-Quality-Triples rausfiltern
#   3. Structural Quality Checks → generische/leere Triples blockieren
#   4. Semantic Dedup → exakte Duplikate entfernen
#   5. Cross-Encoder Reranker Grounding → nicht-verankerte Triples filtern
#
# Hard-Limits (result = filtered[:N]) vernichten Information, die ALLE
# Quality-Gates bestanden hat. Das ist kein Filter, sondern Datenverlust.
#
# Die alte _calculate_adaptive_limit() (log+linear, max_limit=2000) war ein
# Workaround für: (a) Aggregate→Re-Chunk zerstört Docling-Struktur,
# (b) max_tokens=1536 zu niedrig → JSON-Truncation, (c) kein natürlicher
# Bound ohne Per-Chunk-Extraktion. Alle drei Root-Causes sind jetzt behoben.
#
KG_CONFIG = {
    # Fester Confidence-Threshold: Nur Triples mit hoher LLM-Konfidenz behalten
    "confidence_threshold": 0.65,

    # Degraded Threshold: Wenn ALLE Triples unter 0.65 liegen (z.B. Regex-Fallback)
    "degraded_confidence_threshold": 0.40,

    # Untergrenze für adaptives Token-Budget (sichert kurze Chunks ab)
    "min_output_tokens": 2048,

    # Maximales Token-Budget pro KG-Call (Kontext-Limit des Modells berücksichtigen)
    "max_output_tokens": 8192,

    # Ausgabe-Token-Skalierung: Tokens pro Eingabezeichen.
    # Herleitung: ~3.5 Zeichen/Input-Token (DE), Extraktionsdichte ~4 Triples/100 Input-Tokens,
    # ~80 Output-Tokens/Triple (kleinere Modelle verbose) + 300 JSON-Overhead.
    # (1/3.5) * (4/100) * 80 ≈ 0.91 → aufgerundet 1.0 als sicherer Puffer.
    "output_tokens_per_input_char": 1.0,

    # Minimale Chunk-Länge für KG-Extraktion (skip Inhaltsverzeichnisse etc.)
    "min_chunk_chars": 200,
}

# ============================================================================
# GBNF GRAMMAR FOR KG TRIPLE JSON (STRUCTURAL DECODER ENFORCEMENT)
# ============================================================================
#
# ROOT CAUSE FIX: The Magistral model's default [THINK] system message causes
# it to output free-text reasoning ("Okay, ich habe den Text durchgelesen...")
# instead of JSON. All downstream JSON parsers then fail.
#
# This GBNF grammar constrains the decoder at the TOKEN-SAMPLING level:
# - The model's first generated token MUST be '{'
# - Every subsequent token is constrained to follow valid JSON structure
# - The model physically CANNOT output reasoning text, markdown, or any non-JSON
# - This actually speeds up generation (fewer valid tokens to sample from)
#
# Combined with the system message (behavioral override) this provides
# double enforcement: structural (grammar) + behavioral (system msg).
#
KG_TRIPLES_GBNF = r"""
root        ::= "{" ws "\"triples\"" ws ":" ws "[" ws triple-list? ws "]" ws "}"

triple-list ::= triple ( ws "," ws triple )*

triple      ::= "{" ws "\"subject\"" ws ":" ws string ws "," ws "\"predicate\"" ws ":" ws string ws "," ws "\"object\"" ws ":" ws string ws "," ws "\"confidence\"" ws ":" ws confidence ws "}"

string      ::= "\"" chars "\""
chars       ::= char*
char        ::= [^"\\\x00-\x1f] | "\\" escape
escape      ::= ["\\bfnrt/] | "u" hex hex hex hex
hex         ::= [0-9a-fA-F]

confidence  ::= "0" "." digit digit? digit? | "1" ( ".0" )? | "0"
digit       ::= [0-9]

ws          ::= [ \t\n\r]*
"""

# ============================================================================
# SYSTEM MESSAGE FOR KG EXTRACTION (BEHAVIORAL OVERRIDE)
# ============================================================================
#
# This system message overrides the Magistral model's GGUF-embedded default
# system message (which includes [THINK] reasoning instructions).
# It forces JSON-only output behavior as a complement to the GBNF grammar.
#
KG_SYSTEM_MESSAGE = (
    "Du bist eine präzise Knowledge-Graph-Extraktionsmaschine. "
    "Deine EINZIGE Aufgabe: Extrahiere strukturierte Triples aus Text und gib sie als JSON aus.\n\n"
    "STRIKTE REGELN:\n"
    "- Gib AUSSCHLIESSLICH valides JSON aus, NICHTS anderes\n"
    "- KEIN Denken, KEINE Erklärungen, KEINE Kommentare, KEIN Markdown\n"
    "- Das erste Zeichen deiner Antwort MUSS '{' sein\n"
    '- Format: {"triples": [{"subject": "...", "predicate": "...", "object": "...", "confidence": 0.9}]}\n'
    "- Extrahiere nur faktische, verifizierbare Relationen\n"
    "- Jedes Triple MUSS ein konkretes Subject UND ein konkretes Object haben\n"
    "- KEINE leeren, None oder null Werte für Subject/Object\n"
    "- Confidence: 0.9+ explizit genannte Fakten, 0.7-0.9 implizite Relationen, <0.7 unsichere\n\n"
    "PRONOMEN-AUFLÖSUNG (KRITISCH):\n"
    "- NIEMALS Pronomen wie 'Ich', 'er', 'sie', 'es', 'wir', 'man', 'dieser', 'jener' als Subject oder Object verwenden!\n"
    "- Ersetze JEDES Pronomen durch die konkrete Entität, auf die es sich bezieht.\n"
    "- Beispiel: Wenn der Text 'Ich leide unter Schlafstörungen' lautet und der Autor 'Max Mustermann' ist, "
    "dann ist das Subject 'Max Mustermann', NICHT 'Ich'.\n"
    "- Beispiel: 'Er empfiehlt CBT' → Subject muss der Name des Therapeuten sein, NICHT 'Er'.\n"
    "- Wenn der Name/die Entität nicht aus dem Kontext erschließbar ist, überspringe das Triple."
)


@dataclass
class KGTriple:
    """Repräsentiert ein Knowledge Graph Triple"""
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0
    source_text: str = ""
    source_chunk_id: Optional[int] = None  # ★ SOTA v2: Track origin chunk
    
    def to_tuple(self) -> Tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)
    
    def __str__(self) -> str:
        return f"{self.subject} → {self.predicate} → {self.object}"

class LLMKnowledgeGraphExtractor:
    """
    LLM-basierte Knowledge Graph Extraktion mit Mistral 3.2 Small
    """
    
    def __init__(self, llm_client=None):
        """
        Initialisiert den KG-Extraktor
        
        SOTA-Architektur (2026-03):
        - Per-Chunk-Extraktion: Caller liefert semantische Chunks (Docling)
        - Keine interne Re-Chunking mehr (_chunk_text entfällt)
        - Qualitäts-Gates statt Hard-Limits
        - GBNF-Grammar + System-Message für JSON-Enforcement
        
        Args:
            llm_client: LLM-Client (wird automatisch geladen falls None)
        """
        self.llm_client = llm_client
        self.min_chunk_length = int(KG_CONFIG["min_chunk_chars"])
        
        logger.info(f"📋 KG-Extraktor: SOTA Per-Chunk (keine Hard-Limits)")
        logger.info(
            f"   Confidence ≥ {KG_CONFIG['confidence_threshold']} | "
            f"output_tokens=[{KG_CONFIG['min_output_tokens']}..{KG_CONFIG['max_output_tokens']}] "
            f"(adaptiv, {KG_CONFIG['output_tokens_per_input_char']}×chars+300) | "
            f"min_chunk={self.min_chunk_length} chars"
        )
        
        self._init_llm_client()
        
    def _init_llm_client(self):
        """
        Initialisiert den LLM-Client falls nicht vorhanden
        
        WICHTIG: Holt die Singleton-Instanz von ModelLoader.
        Die Prüfung ob ein Modell geladen ist, erfolgt zur LAUFZEIT (nicht zur Init-Zeit),
        da das Modell später vom User geladen werden kann.
        """
        if self.llm_client is None:
            try:
                import sys
                import os
                
                # 1. Versuche zuerst die bestehende Instanz aus dem Streamlit-Session-State zu holen.
                # Zugriff nur bei aktivem ScriptRunContext, damit Headless-CLI keine
                # Streamlit-Warnungen ausloest.
                try:
                    streamlit_module = sys.modules.get("streamlit")
                    scriptrunner_module = sys.modules.get("streamlit.runtime.scriptrunner")
                    get_ctx = getattr(scriptrunner_module, "get_script_run_ctx", None)

                    if streamlit_module is not None and callable(get_ctx) and get_ctx() is not None:
                        model_loader = streamlit_module.session_state.get('model_loader')
                        if model_loader is not None:
                            self.llm_client = model_loader
                            logger.info("✅ LLM-Client verwendet bestehende ModelLoader-Instanz (Streamlit)")
                except Exception:
                    # Streamlit nicht verfuegbar oder ohne aktiven Kontext.
                    pass
                
                # 2. Falls Streamlit-Pfad keinen Client lieferte: ModelLoader-Singleton holen
                if self.llm_client is None:
                    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
                    try:
                        from scripts.model_loader import ModelLoader
                        
                        logger.info("🔄 Hole ModelLoader-Singleton-Instanz für KG-Extraktion...")
                        model_loader = ModelLoader()
                        
                        # Speichere Referenz IMMER (auch wenn kein Modell geladen ist)
                        # Die Prüfung ob ein Modell geladen ist erfolgt zur LAUFZEIT in _query_llm()
                        self.llm_client = model_loader
                        logger.info("✅ LLM-Client mit ModelLoader-Singleton verbunden")
                            
                    except ImportError as ie:
                        logger.warning(f"⚠️ ModelLoader nicht importierbar: {ie} - verwende Fallback-Extraktion")
                        self.llm_client = None
                    
            except Exception as e:
                logger.warning(f"⚠️ LLM-Client nicht verfügbar: {e} - verwende Fallback-Extraktion")
                self.llm_client = None

        # Wrap client with adapter for capability checking and repairs
        # WICHTIG: Muss IMMER erreicht werden (kein early-return davor!)
        # WICHTIG: Den ModelLoader wrappen (hat generate_response + generate_with_grammar),
        # NICHT das rohe Llama-Objekt (.llm) — das hat nur low-level token-basierte APIs.
        try:
            from agent.llm_adapter import DefaultLLMAdapter
            self.llm_adapter = DefaultLLMAdapter(self.llm_client)
        except Exception:
            self.llm_adapter = None
    
    def extract_from_chunks(
        self,
        chunks: List[Dict[str, Any]],
        doc_context: Optional[Dict[str, Any]] = None,
    ) -> List[KGTriple]:
        """
        SOTA: Per-Chunk KG-Extraktion — Haupteinstiegspunkt.
        
        ═══════════════════════════════════════════════════════════════════
        GPU-PIPELINE-OPTIMIERUNG (Root-Cause: Inter-Inference CPU-Gaps)
        ═══════════════════════════════════════════════════════════════════
        
        Problem: Sequenzielle Verarbeitung (prompt→infer→parse→prompt→...)
        lässt die GPU zwischen Inference-Calls idle (10-50ms Python-Overhead
        pro Chunk bei 30-50 Chunks = 0.5-2.5s verschwendete GPU-Zeit).
        
        Lösung: 2-Phasen-Pipeline:
          Phase 1: Pre-compute ALLER Extraction-Texte (CPU, kein GPU/Lock)
          Phase 2: Inference-Loop mit asynchroner JSON-Verarbeitung:
                   - llama-cpp-python released GIL während C-Inference
                   - JSON-Parsing von Chunk N-1 läuft parallel auf CPU-Thread
                   - GPU wird nie durch JSON-Parsing blockiert
        
        Empfängt vorbereitete Chunks (Docling-Chunks, ggf. heading-aware gemerged)
        und extrahiert pro Chunk Triples. Kein internes Re-Chunking.
        
        Jeder Chunk muss mindestens {'text': str} enthalten.
        Optional: {'chunk_id': int/str, 'headings': str}
        
        Args:
            chunks: Liste von Chunk-Dicts mit 'text' und optional 'chunk_id', 'headings'
            doc_context: Dokumentkontext (source_type, doc_id, etc.)
            
        Returns:
            Liste von KGTriple mit gesetztem source_chunk_id
        """
        if not self.llm_client or not hasattr(self.llm_client, 'llm') or self.llm_client.llm is None:
            logger.warning("⚠️ LLM nicht verfügbar — verwende Fallback-Extraktion")
            fallback_text = "\n\n".join(c.get("text", "") for c in chunks)
            return self._fallback_extraction(fallback_text)
        
        import time as _time
        _pipeline_start = _time.time()
        
        # ═══ Phase 1: Pre-compute extraction texts (CPU only, no GPU/locks) ═══
        # Front-loads ALL Python work so the GPU-inference loop runs with
        # minimal inter-call gaps.
        prepared: List[tuple] = []  # (original_idx, chunk_text, chunk_id, extraction_text)
        skipped = 0
        
        for i, chunk_data in enumerate(chunks):
            chunk_text = chunk_data.get("text", "").strip()
            chunk_id = chunk_data.get("chunk_id")
            headings = chunk_data.get("headings", "")
            
            if len(chunk_text) < self.min_chunk_length:
                skipped += 1
                logger.debug(
                    f"⏭️ Chunk {i+1}/{len(chunks)} übersprungen: "
                    f"{len(chunk_text)} Zeichen (min: {self.min_chunk_length})"
                )
                continue
            
            # Heading-Kontext dem Chunk voranstellen (wie SynthKG Decontextualization-Light)
            extraction_text = chunk_text
            if headings:
                extraction_text = f"[Abschnitt: {headings}]\n\n{chunk_text}"
            
            prepared.append((i, chunk_text, chunk_id, extraction_text, headings))
        
        if not prepared:
            logger.info(f"⏭️ Alle {len(chunks)} Chunks übersprungen (< {self.min_chunk_length} Zeichen)")
            return []
        
        logger.info(
            f"🚀 GPU-Pipeline: {len(prepared)} Chunks vorbereitet, "
            f"{skipped} übersprungen, starte Inference..."
        )
        
        # ═══ Phase 2: Pipelined GPU inference + async JSON parsing ═══
        # llama-cpp-python releases GIL during C inference →
        # JSON-Parsing von Chunk N-1 läuft ECHT parallel auf CPU-Thread
        # während GPU Chunk N generiert.
        from concurrent.futures import ThreadPoolExecutor
        
        all_triples: List[KGTriple] = []
        
        def _parse_and_tag(response: str, chunk_text: str, doc_ctx: Optional[Dict[str, Any]], 
                           chunk_id: Optional[int]) -> List[KGTriple]:
            """Parse LLM response and tag triples with source chunk ID.
            Runs in background thread while GPU processes next chunk."""
            triples = self._parse_llm_response(response, chunk_text, doc_ctx)
            for t in triples:
                t.source_chunk_id = chunk_id
            return triples
        
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="kg-parse") as parse_pool:
            pending_future = None
            pending_info = ""  # For logging
            
            for idx, (i, chunk_text, chunk_id, extraction_text, headings) in enumerate(prepared):
                _chunk_start = _time.time()
                
                logger.info(
                    f"🧠 KG-Chunk {i+1}/{len(chunks)} [{idx+1}/{len(prepared)}]: "
                    f"{len(chunk_text)} Zeichen"
                    + (f", headings='{headings[:60]}'" if headings else "")
                )
                
                # ── GPU: Run LLM inference (GIL released during C call) ──
                response = self._query_llm(extraction_text, doc_context)
                
                _chunk_elapsed = _time.time() - _chunk_start
                
                # ── Collect previous parse result (was running in parallel) ──
                if pending_future is not None:
                    prev_triples = pending_future.result()
                    all_triples.extend(prev_triples)
                    logger.info(f"✅ {pending_info}: {len(prev_triples)} Triples (async parsed)")
                
                # ── Submit current response for async parsing ──
                # Runs on CPU thread while NEXT chunk's GPU inference executes
                pending_future = parse_pool.submit(
                    _parse_and_tag, response, chunk_text, doc_context, chunk_id
                )
                pending_info = f"KG-Chunk {i+1}/{len(chunks)}"
                
                logger.debug(
                    f"⏱️ KG-Chunk {i+1}/{len(chunks)}: LLM inference {_chunk_elapsed:.1f}s"
                )
            
            # ── Collect last parse result ──
            if pending_future is not None:
                last_triples = pending_future.result()
                all_triples.extend(last_triples)
                logger.info(f"✅ {pending_info}: {len(last_triples)} Triples (async parsed)")
        
        # Quality-Gates: Confidence + Structural + Dedup (KEIN Hard-Limit)
        filtered = self._filter_and_deduplicate(all_triples)
        
        _pipeline_elapsed = _time.time() - _pipeline_start
        
        if skipped > 0:
            logger.info(f"⏭️ {skipped}/{len(chunks)} Chunks übersprungen (< {self.min_chunk_length} Zeichen)")
        
        logger.info(
            f"✅ {len(filtered)} KG-Triples extrahiert aus {len(chunks)} Chunks "
            f"({len(all_triples)} roh → {len(filtered)} nach Quality-Gates) "
            f"in {_pipeline_elapsed:.1f}s total"
        )
        return filtered
    
    def extract_knowledge_graph(self, text: str, doc_context: Optional[Dict[str, Any]] = None) -> List[KGTriple]:
        """
        Legacy-Kompatibilität: Extrahiert KG aus einem einzelnen Text-Blob.
        
        WARNUNG: Diese Methode existiert nur für Abwärtskompatibilität.
        Für neue Aufrufe extract_from_chunks() verwenden!
        
        Bei Texten > 3500 Chars wird intern in Absatz-Chunks aufgeteilt,
        um wenigstens eine rudimentäre Chunk-Grenze zu haben.
        """
        if not self.llm_client or not hasattr(self.llm_client, 'llm') or self.llm_client.llm is None:
            logger.warning("⚠️ LLM nicht verfügbar — verwende Fallback-Extraktion")
            return self._fallback_extraction(text)
        
        logger.warning(
            "⚠️ extract_knowledge_graph(text) aufgerufen statt extract_from_chunks(). "
            "Per-Chunk-Extraktion liefert bessere Ergebnisse!"
        )
        
        # Erstelle minimale Chunk-Dicts aus Absätzen
        MAX_LEGACY_CHUNK_CHARS = 3500
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text]
        
        # Hard-Split überlanger Einzel-Paragraphen (Web-Content ohne \n\n)
        normalized_paragraphs: List[str] = []
        for p in paragraphs:
            normalized_paragraphs.extend(split_oversized_for_kg(p, MAX_LEGACY_CHUNK_CHARS))
        
        # Merge kurze Absätze zu ~3500-Char-Chunks (Heading-aware nicht möglich hier)
        chunks: List[Dict[str, Any]] = []
        current = ""
        for para in normalized_paragraphs:
            if len(current) + len(para) + 2 > MAX_LEGACY_CHUNK_CHARS and current:
                chunks.append({"text": current, "chunk_id": None})
                current = para
            else:
                current = current + "\n\n" + para if current else para
        if current:
            chunks.append({"text": current, "chunk_id": None})
        
        return self.extract_from_chunks(chunks, doc_context)
    
    def _create_kg_prompt(self, text: str, doc_context: Optional[Dict[str, Any]] = None) -> str:
        """Erstellt optimierten Prompt für KG-Extraktion"""
        
        # Basis-Kontext
        context_info = ""
        if doc_context:
            source_type = doc_context.get("source_type", "generic")
            
            if source_type == "business":
                context_info = "Kontext: Business/Geschäftsdokument mit Fokus auf Personen, Organisationen und Finanzkennzahlen."
            elif source_type == "psychology":
                context_info = "Kontext: Psychologie-Dokument mit Fokus auf Therapiemethoden, Konzepte und Behandlungsansätze."
            elif source_type == "web":
                source_url = doc_context.get("source_url", "unknown")
                context_info = f"Kontext: Web-Dokument von {source_url}. Extrahiere faktische Informationen."
            elif source_type == "generic":
                context_info = "Kontext: Allgemeines Dokument. Extrahiere relevante Fakten und Konzepte."
            else:
                # Fallback: Wenn source_url vorhanden, nutze das
                if doc_context.get("source_url"):
                    context_info = f"Kontext: Dokument von {doc_context.get('source_url')}"
        
        # Pronomen-Auflösungs-Hinweis aus doc_context
        author_hint = ""
        if doc_context:
            user_name = doc_context.get("user_name", "").strip()
            author_name = doc_context.get("author", "").strip()
            resolved_name = user_name or author_name
            if resolved_name:
                author_hint = (
                    f"\nWICHTIG - PRONOMEN-AUFLÖSUNG: "
                    f"Der Autor/Sprecher dieses Textes ist '{resolved_name}'. "
                    f"Ersetze 'Ich', 'mir', 'mich', 'mein' durch '{resolved_name}'. "
                    f"Ersetze auch 'er/sie/es' durch die konkrete Person oder das Konzept, auf das verwiesen wird.\n"
                )
            else:
                author_hint = (
                    "\nWICHTIG - PRONOMEN-AUFLÖSUNG: "
                    "Verwende NIEMALS Pronomen (Ich, er, sie, es, wir, man) als Subject oder Object. "
                    "Ersetze sie durch die konkrete Entität aus dem Kontext. "
                    "Wenn die Entität nicht bestimmbar ist, überspringe das Triple.\n"
                )

        prompt = f"""Extrahiere strukturierte Knowledge Graph Triples aus dem folgenden Text.

{context_info}{author_hint}

ANWEISUNGEN:
1. Finde Entitäten (Personen, Organisationen, Konzepte, Orte, etc.)
2. Identifiziere semantische Relationen zwischen Entitäten
3. Erstelle Triples im Format: Subject | Predicate | Object
4. Verwende klare, präzise Relationen (z.B. "ist CEO von", "behandelt", "gehört zu")
5. Fokussiere auf faktische, verifizierbare Informationen
6. Überspringe triviale oder redundante Relationen
7. KRITISCH: KEINE Pronomen (Ich, er, sie, es, wir, man, dieser) als Subject oder Object! Immer die konkrete Entität verwenden.

TEXT:
{text}

AUSGABE (STRIKT JSON-FORMAT):
{{
  "triples": [
    {{"subject": "Entity1", "predicate": "relation", "object": "Entity2", "confidence": 0.9}},
    {{"subject": "Entity2", "predicate": "andere_relation", "object": "Entity3", "confidence": 0.8}}
  ]
}}

WICHTIG: 
- Nur gültiges JSON ausgeben, keine zusätzlichen Texte oder Erklärungen!
- Keine Markdown-Code-Blocks (```json), nur pures JSON!
- Subject, Predicate und Object müssen strings sein (in "quotes")
- Confidence muss ein float zwischen 0.0 und 1.0 sein

JSON:"""
        
        return prompt
    
    def _get_context_info(self, doc_context: Optional[Dict[str, Any]] = None) -> str:
        """Builds context info string from document context for KG extraction."""
        if not doc_context:
            return ""
        source_type = doc_context.get("source_type", "generic")
        if source_type == "business":
            return "Kontext: Business/Geschäftsdokument. Fokus auf Personen, Organisationen, Finanzkennzahlen.\n\n"
        elif source_type == "psychology":
            return "Kontext: Psychologie-Dokument. Fokus auf Therapiemethoden, Konzepte, Behandlungsansätze.\n\n"
        elif source_type == "web":
            source_url = doc_context.get("source_url", "unknown")
            return f"Kontext: Web-Dokument von {source_url}. Extrahiere faktische Informationen.\n\n"
        elif doc_context.get("source_url"):
            return f"Kontext: Dokument von {doc_context.get('source_url')}.\n\n"
        return ""

    def _estimate_max_tokens_for_text(self, text_length: int) -> int:
        """
        Adaptives Output-Token-Budget skaliert mit der Eingabelänge.

        Herleitung:
          - ~3.5 Zeichen/Input-Token (deutschsprachiger Text)
          - Extraktionsdichte: ~4 Triples pro 100 Input-Tokens
          - ~80 Output-Tokens/Triple (kleinere Modelle benötigen mehr Tokens
            für dieselbe JSON-Struktur als größere Modelle)
          - JSON-Overhead (Klammern, Keys): ~300 Tokens pro Call

        Ergibt: budget ≈ text_length * 1.0 + 300
        Geclampt auf [min_output_tokens, max_output_tokens].
        """
        raw = int(text_length * KG_CONFIG["output_tokens_per_input_char"]) + 300
        return max(
            int(KG_CONFIG["min_output_tokens"]),
            min(int(KG_CONFIG["max_output_tokens"]), raw),
        )
    
    def _query_llm(self, text: str, doc_context: Optional[Dict[str, Any]] = None) -> str:
        """Sendet KG-Extraktions-Anfrage an LLM mit GBNF-Grammar-Enforcement.
        
        ═══════════════════════════════════════════════════════════════════
        SOTA ROOT-CAUSE FIX für LLM-JSON-Parse-Fehler
        ═══════════════════════════════════════════════════════════════════
        
        PROBLEM: Das Magistral-Modell hat eine GGUF-eingebettete Default-System-
        Message mit [THINK]-Anweisungen. Ohne explizite System-Message generiert
        das Modell Freitext-Reasoning ("Okay, ich habe den Text durchgelesen...")
        statt JSON. Alle JSON-Parser schlagen dann fehl.
        
        ROOT-CAUSE FIXES (kein Workaround!):
        
        1. GBNF-Grammar (Primary) — Constrains den Decoder auf Token-Sampling-Level:
           - Erstes generiertes Token MUSS '{' sein
           - Jedes folgende Token wird durch die Grammatik erzwungen
           - Modell KANN keinen Freitext, Markdown oder Reasoning ausgeben
           - Beschleunigt Generation (weniger valide Tokens zum Samplen)
        
        2. System-Message (Secondary) — Überschreibt Magistrals [THINK]-Verhalten:
           - Explizit: "Du bist eine JSON-Extraktionsmaschine"
           - Verbietet Denken, Erklärungen, Kommentare
        
        3. Strukturierte Messages (Tertiary) — System/User Rollentrennung:
           - System: JSON-Format-Anweisungen
           - User: Nur der zu extrahierende Text
        
        FALLBACK: Wenn Grammar fehlschlägt → generate_response mit System-Message
        → JSON-Parser → Regex-Fallback (bestehende Pipeline)
        
        Args:
            text: Quelltext für Triple-Extraktion
            doc_context: Optionaler Dokumentkontext
            
        Returns:
            LLM-Response (sollte valides JSON sein)
        """
        try:
            # RUNTIME-CHECK: LLM verfügbar?
            if self.llm_client is None:
                logger.debug("⚠️ LLM-Client ist None - verwende Fallback")
                return ""
            if not hasattr(self.llm_client, 'llm') or self.llm_client.llm is None:
                logger.debug("⚠️ LLM-Modell ist nicht geladen - verwende Fallback")
                return ""
            
            import time
            start_time = time.time()
            TIMEOUT_WARNING_SECONDS = 180
            
            # ── Structured Messages: System + User ──────────────────────
            context_info = self._get_context_info(doc_context)
            
            # Pronomen-Auflösungs-Hinweis für den LLM
            pronoun_hint = ""
            if doc_context:
                resolved_name = (doc_context.get("user_name", "") or doc_context.get("author", "")).strip()
                if resolved_name:
                    pronoun_hint = (
                        f"WICHTIG: Der Autor/Sprecher ist '{resolved_name}'. "
                        f"Ersetze alle Pronomen (Ich/er/sie/es/wir) durch konkrete Entitäten. "
                        f"'Ich' = '{resolved_name}'.\n\n"
                    )
                else:
                    pronoun_hint = (
                        "WICHTIG: Verwende KEINE Pronomen (Ich/er/sie/es/wir/man) als Subject oder Object. "
                        "Ersetze sie durch die konkrete Entität oder überspringe das Triple.\n\n"
                    )
            else:
                pronoun_hint = (
                    "WICHTIG: Verwende KEINE Pronomen (Ich/er/sie/es/wir/man) als Subject oder Object. "
                    "Ersetze sie durch die konkrete Entität oder überspringe das Triple.\n\n"
                )
            
            user_content = (
                f"{context_info}"
                f"{pronoun_hint}"
                f"Extrahiere alle faktischen Relationen als Knowledge-Graph-Triples "
                f"aus folgendem Text.\n\n"
                f"TEXT:\n{text}\n\n"
                f"Antwort als JSON:"
            )
            
            messages = [
                {"role": "system", "content": KG_SYSTEM_MESSAGE},
                {"role": "user", "content": user_content},
            ]
            
            # Adaptive max_tokens (proportional zur Textlänge)
            adaptive_max_tokens = self._estimate_max_tokens_for_text(len(text))
            
            response = ""
            
            # ════════════════════════════════════════════════════════════
            # PRIMARY: GBNF Grammar Enforcement (Root-Cause Fix)
            # ════════════════════════════════════════════════════════════
            # The grammar physically constrains the decoder — the model
            # CANNOT output free-text reasoning, only valid KG JSON.
            if self.llm_adapter and self.llm_adapter.supports_grammar():
                logger.debug(
                    f"🧠 KG-Extraktion: GBNF-Grammar + System-Message "
                    f"({len(text)} Zeichen Text, max_tokens={adaptive_max_tokens})"
                )
                try:
                    response = self.llm_adapter.generate_with_grammar(
                        messages=messages,
                        grammar_str=KG_TRIPLES_GBNF,
                        max_tokens=adaptive_max_tokens,
                        temperature=0.0,
                    )
                    if response and len(response.strip()) > 20:
                        logger.debug(f"✅ GBNF-Grammar Response: {len(response)} Zeichen")
                    else:
                        logger.warning(f"⚠️ GBNF-Grammar Response leer/zu kurz ({len(response) if response else 0} Zeichen), versuche Fallback...")
                        response = ""
                except Exception as e:
                    logger.warning(f"⚠️ GBNF-Grammar fehlgeschlagen ({e}), versuche Fallback...")
                    response = ""
            
            # ════════════════════════════════════════════════════════════
            # FALLBACK: generate_response with System-Message
            # ════════════════════════════════════════════════════════════
            # If grammar-based generation fails, use standard method but
            # WITH system message to at least behaviorally constrain output.
            if not response:
                logger.debug(f"🧠 KG-Extraktion Fallback: generate_response mit System-Message")
                legacy_prompt = self._create_kg_prompt(text, doc_context)
                fallback_messages = [
                    {"role": "system", "content": KG_SYSTEM_MESSAGE},
                    {"role": "user", "content": legacy_prompt},
                ]

                if self.llm_adapter:
                    try:
                        response = self.llm_adapter.generate_response(
                            messages=fallback_messages,
                            max_tokens=adaptive_max_tokens,
                            temperature=0.0,
                            stop=None,
                        )
                    except Exception as e:
                        logger.warning(f"⚠️ Fallback generate_response fehlgeschlagen: {e}")
                        response = ""
                else:
                    response = ""
            
            if not response:
                logger.error("❌ Weder Grammar noch Fallback haben eine Response geliefert")
                return ""
            
            # ── UTF-8 Normalisierung ────────────────────────────────────
            try:
                if isinstance(response, bytes):
                    response = response.decode('utf-8', errors='replace')
                elif isinstance(response, str):
                    response = response.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            except Exception:
                pass
            
            # ── Performance-Logging ─────────────────────────────────────
            elapsed = time.time() - start_time
            if elapsed > TIMEOUT_WARNING_SECONDS:
                logger.warning(
                    f"⚠️ LLM-Query sehr langsam: {elapsed:.1f}s "
                    f"(max_tokens={adaptive_max_tokens}, text={len(text)} chars)"
                )
            else:
                logger.debug(f"⏱️ LLM-Query abgeschlossen in {elapsed:.1f}s")
            
            # ── Response-Qualitätscheck & opportunistische Reparatur ───
            if not response or len(response.strip()) < 10:
                logger.warning(f"⚠️ LLM-Response ist leer oder zu kurz: '{response}'")
                return ""

            # Opportunistic parse-check + repair: try to validate JSON early and repair using adapter if available
            try:
                from utils.llm_json_parser import parse_llm_json, validate_kg_schema
                parsed = None
                try:
                    parsed = parse_llm_json(response, schema_validator=validate_kg_schema, default_on_error=None, debug=False)
                except Exception:
                    parsed = None

                if (not parsed or 'triples' not in parsed) and self.llm_adapter:
                    logger.debug("🔁 Versuch: Repariere LLM-Response via adapter.repair_response()")
                    repaired = self.llm_adapter.repair_response(messages, KG_TRIPLES_GBNF, response, adaptive_max_tokens)
                    if repaired and repaired.strip() != response.strip():
                        try:
                            parsed2 = parse_llm_json(repaired, schema_validator=validate_kg_schema, default_on_error=None, debug=False)
                            if parsed2 and 'triples' in parsed2:
                                logger.info("✅ Reparierte Response validiert erfolgreich")
                                response = repaired
                        except Exception:
                            pass
            except Exception:
                pass
            
            logger.debug(f"✅ LLM-Response erhalten: {len(response)} Zeichen")
            logger.debug(f"Response preview: {response[:200]}...")
            
            return str(response).strip()
            
        except Exception as e:
            logger.error(f"❌ LLM-Query fehlgeschlagen: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return ""
    
    def _parse_llm_response(self, response: str, source_text: str, 
                             doc_context: Optional[Dict[str, Any]] = None) -> List[KGTriple]:
        """
        Parst LLM-Response und extrahiert KG-Triples mit robustem Multi-Methoden-Fallback.
        Inkl. Pronomen-Auflösung aus doc_context (user_name/author).
        """
        triples = []
        
        try:
            # Importiere robusten Parser
            from utils.llm_json_parser import parse_llm_json, validate_kg_schema
            
            logger.debug(f"[DEBUG] Raw response length: {len(response)}")
            logger.debug(f"[DEBUG] Raw response preview: {repr(response[:200])}")
            
            # Robustes JSON-Parsing mit Multi-Methoden-Fallback
            data = parse_llm_json(
                response,
                schema_validator=validate_kg_schema,
                default_on_error=None,  # Bei KG wollen wir Regex-Fallback nutzen
                debug=True
            )
            
            # Extrahiere Triples aus geparsten Daten
            if "triples" in data and isinstance(data["triples"], list):
                logger.debug(f"[DEBUG] Found triples array with {len(data['triples'])} items")
                for triple_data in data["triples"]:
                    if (isinstance(triple_data, dict) and 
                        all(key in triple_data for key in ["subject", "predicate", "object"])):
                        
                        # WICHTIG: Normalisiere alle Triple-Felder (entfernt Unterstriche etc.)
                        subject_raw = normalize_text(str(triple_data["subject"]).strip())
                        predicate_raw = normalize_text(str(triple_data["predicate"]).strip())
                        object_raw = normalize_text(str(triple_data["object"]).strip())
                        
                        # ★ Post-Processing Pronomen-Auflösung:
                        # Wenn der LLM trotz Instruktion Pronomen durchlässt,
                        # versuche sie aus doc_context aufzulösen
                        subject_resolved = self._resolve_pronoun_if_needed(subject_raw, source_text, doc_context)
                        object_resolved = self._resolve_pronoun_if_needed(object_raw, source_text, doc_context)
                        
                        triple = KGTriple(
                            subject=subject_resolved,
                            predicate=predicate_raw, 
                            object=object_resolved,
                            confidence=float(triple_data.get("confidence", 0.8)),
                            source_text=source_text[:200] + "..."
                        )
                        
                        # Validiere Triple-Qualität (inkl. Pronomen-Check)
                        if self._is_valid_triple(triple):
                            triples.append(triple)
                            logger.debug(f"[DEBUG] Added valid triple: {triple.subject} | {triple.predicate} | {triple.object}")
                
                if triples:
                    logger.debug(f"✅ JSON-Parsing erfolgreich - {len(triples)} gültige Triples")
                    return triples
            
            # Wenn JSON-Parsing keine Triples lieferte, versuche Regex-Fallback
            logger.debug("[DEBUG] JSON-Parsing lieferte keine Triples, versuche Regex-Fallback...")
            triples = self._regex_parse_response(response, source_text)
                
        except Exception as e:
            logger.debug(f"[DEBUG] LLM-Response-Parsing Fehler: {e}")
            triples = self._regex_parse_response(response, source_text)
        
        # ✅ FIX: Chunk-Level-Logging (debug/warning statt info)
        if not triples:
            logger.warning(f"⚠️ Chunk-Parsing fehlgeschlagen - keine Triples extrahiert")
        else:
            logger.debug(f"✅ Chunk: {len(triples)} Triples extrahiert")
        
        return triples

    # ========================================================================
    # PRONOMEN-AUFLÖSUNG (Post-Processing Safety-Net)
    # ========================================================================
    
    # Vollständiges Set deutscher und englischer Pronomen für die Erkennung
    _PRONOUN_SET = {
        # Deutsch - Personalpronomen (alle Kasus)
        'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr',
        'mich', 'dich', 'ihn', 'uns', 'euch',
        'mir', 'dir', 'ihm', 'ihnen',
        'mein', 'dein', 'sein', 'unser', 'euer', 'ihrer',
        'meiner', 'deiner', 'seiner', 'unserer', 'eurer',
        'meine', 'deine', 'seine', 'unsere', 'eure', 'ihre',
        'meinem', 'deinem', 'seinem', 'unserem', 'eurem', 'ihrem',
        'meinen', 'deinen', 'seinen', 'unseren', 'euren', 'ihren',
        # Deutsch - Demonstrativ/Relativ/Indefinit
        'dieser', 'diese', 'dieses', 'jener', 'jene', 'jenes',
        'man', 'jemand', 'niemand', 'einer', 'keiner',
        'derjenige', 'diejenige', 'dasjenige',
        # Deutsch - Reflexiv
        'sich', 'selbst',
        # Englisch - Personal
        'i', 'he', 'she', 'it', 'we', 'they', 'you',
        'me', 'him', 'her', 'us', 'them',
        'my', 'his', 'its', 'our', 'their', 'your',
        'myself', 'himself', 'herself', 'itself', 'ourselves', 'themselves',
        # Englisch - Demonstrativ/Indefinit
        'this', 'that', 'these', 'those',
        'someone', 'anyone', 'everyone', 'nobody',
        # Meta-Referenzen
        'the author', 'der autor', 'der verfasser', 'die autorin',
        'der sprecher', 'die sprecherin', 'der erzähler', 'die erzählerin',
        'the speaker', 'the narrator',
    }
    
    def _resolve_pronoun_if_needed(self, entity_text: str, source_text: str = "",
                                     doc_context: Optional[Dict[str, Any]] = None) -> str:
        """
        ★ Post-Processing Pronomen-Auflösung für KG-Triples.
        
        Wenn der LLM trotz Prompt-Instruktion ein Pronomen als Subject/Object
        durchlässt, versucht diese Methode es aufzulösen.
        
        Strategie:
        1. Prüfe ob entity_text ein reines Pronomen ist
        2. Wenn ja und doc_context einen user_name/author hat → ersetze Ich-Formen
        3. Sonst: gib das Pronomen zurück (wird dann von _is_valid_triple abgelehnt)
        
        Args:
            entity_text: Der zu prüfende Entity-Text (z.B. "Ich", "er")
            source_text: Original-Quelltext für potenzielle Auflösung
            doc_context: Dokumentkontext mit user_name/author für Ersetzung
            
        Returns:
            Aufgelöster Entity-Text oder Original wenn keine Auflösung möglich
        """
        if not entity_text:
            return entity_text
        
        cleaned = entity_text.strip().lower()
        
        # Kein Pronomen → nichts zu tun
        if cleaned not in self._PRONOUN_SET:
            return entity_text
        
        # Pronomen erkannt → versuche Auflösung aus doc_context
        if doc_context:
            resolved_name = (
                (doc_context.get("user_name", "") or doc_context.get("author", "")).strip()
            )
            # Erste-Person-Pronomen (Ich, mir, mich, mein etc.) → user_name/author
            _FIRST_PERSON = {'ich', 'mir', 'mich', 'mein', 'meiner', 'meine',
                             'meinem', 'meinen', 'i', 'me', 'my', 'myself'}
            if cleaned in _FIRST_PERSON and resolved_name:
                logger.info(
                    f"✅ Pronomen-Auflösung: '{entity_text}' → '{resolved_name}' "
                    f"(aus doc_context)"
                )
                return resolved_name
        
        # Pronomen ohne Auflösung → Logging, wird von _is_valid_triple abgelehnt
        logger.debug(
            f"⚠️ Pronomen '{entity_text}' in Triple erkannt — "
            f"keine Auflösung möglich, wird abgelehnt"
        )
        return entity_text
    
    def _is_valid_triple(self, triple: KGTriple) -> bool:
        """Validiert Triple-Qualität"""
        # Mindestlängen
        if len(triple.subject) < 2 or len(triple.predicate) < 3 or len(triple.object) < 2:
            return False
        
        # Keine leeren oder nur Whitespace-Werte
        if not triple.subject.strip() or not triple.predicate.strip() or not triple.object.strip():
            return False
        
        # ★ ROOT-CAUSE FIX: Reject None/null/placeholder objects and subjects
        _NONE_VALUES = {'none', 'null', 'n/a', '-', 'undefined', 'unbekannt', 'na', 'nil', '...', 'unknown'}
        if triple.subject.strip().lower() in _NONE_VALUES or triple.object.strip().lower() in _NONE_VALUES:
            return False
        
        # ★ PRONOMEN-FIX: Reject triples where subject or object is just a pronoun.
        # These are uninformative and cause downstream query errors.
        _PRONOUNS = {
            # Deutsch - Personalpronomen
            'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr',
            'mich', 'dich', 'ihn', 'uns', 'euch',
            'mir', 'dir', 'ihm', 'ihnen',
            'mein', 'dein', 'sein', 'unser', 'euer',
            'meiner', 'deiner', 'seiner', 'unserer', 'eurer',
            # Deutsch - Demonstrativ/Relativ/Indefinit
            'dieser', 'diese', 'dieses', 'jener', 'jene', 'jenes',
            'man', 'jemand', 'niemand', 'einer', 'keiner',
            'derjenige', 'diejenige', 'dasjenige',
            # Deutsch - Reflexiv
            'sich', 'selbst',
            # Englisch - Personal
            'i', 'he', 'she', 'it', 'we', 'they', 'you',
            'me', 'him', 'her', 'us', 'them',
            'my', 'his', 'its', 'our', 'their', 'your',
            'myself', 'himself', 'herself', 'itself', 'ourselves', 'themselves',
            # Englisch - Demonstrativ/Indefinit
            'this', 'that', 'these', 'those',
            'someone', 'anyone', 'everyone', 'nobody',
            'the author', 'der autor', 'der verfasser', 'die autorin',
        }
        if triple.subject.strip().lower() in _PRONOUNS:
            logger.debug(f"⚠️ Pronomen als Subject abgelehnt: '{triple.subject}' in Triple: {triple}")
            return False
        if triple.object.strip().lower() in _PRONOUNS:
            logger.debug(f"⚠️ Pronomen als Object abgelehnt: '{triple.object}' in Triple: {triple}")
            return False
            
        # Keine Duplikate (Subject == Object)
        if triple.subject.lower() == triple.object.lower():
            return False
        
        return True
    
    def _regex_parse_response(self, response: str, source_text: str) -> List[KGTriple]:
        """Fallback-Parsing mit Regex"""
        triples = []
        
        # Suche nach Triple-Patterns
        patterns = [
            r'([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|\n]+)',  # Subject | Predicate | Object
            r'([^→]+)\s*→\s*([^→]+)\s*→\s*([^→\n]+)',     # Subject → Predicate → Object
            r'"([^"]+)"\s*"([^"]+)"\s*"([^"]+)"'          # "Subject" "Predicate" "Object"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, response)
            for match in matches:
                if len(match) == 3:
                    subj, pred, obj = match
                    if all(len(x.strip()) > 2 for x in [subj, pred, obj]):
                        # WICHTIG: Normalisiere alle Triple-Felder (entfernt Unterstriche etc.)
                        triple = KGTriple(
                            subject=normalize_text(subj.strip()),
                            predicate=normalize_text(pred.strip()),
                            object=normalize_text(obj.strip()),
                            confidence=0.7,  # Niedrigere Confidence für Regex-Parsing
                            source_text=source_text[:200] + "..."
                        )
                        triples.append(triple)
        
        # ✅ FIX: Explizites Regex-Fallback-Logging
        if triples:
            logger.debug(f"✅ Regex-Fallback erfolgreich: {len(triples)} Triples extrahiert")
        else:
            logger.debug(f"⚠️ Regex-Fallback fehlgeschlagen - keine Triples gefunden")
        
        return triples
    
    def _get_confidence_threshold(self) -> float:
        """
        Gibt den festen Confidence-Threshold zurück.
        
        Returns:
            Fester Confidence-Threshold (0.65)
        """
        return KG_CONFIG["confidence_threshold"]
    
    def _filter_and_deduplicate(self, triples: List[KGTriple]) -> List[KGTriple]:
        """
        SOTA: Qualitäts-Gates OHNE Hard-Limit.
        
        4 Gate-Schichten (alle notwendig, zusammen hinreichend):
          1. _is_quality_triple(): Länge, Blocklist, None-Check, Confidence
          2. Exakte (s,p,o)-Dedup via set
          3. Sort by confidence (beste zuerst für Logging)
          4. KEIN result[:limit] — alle qualitätsgeprüften Triples behalten
        
        Args:
            triples: Rohe Triple-Liste aus LLM-Extraktion
            
        Returns:
            Gefilterte und deduplizierte Triples (ALLE die Quality-Gates bestehen)
        """
        if not triples:
            return []
        
        confidence_threshold = self._get_confidence_threshold()
        
        # Quality-Filter + Dedup
        filtered: List[KGTriple] = []
        seen: set = set()
        quality_rejected = 0
        dedup_rejected = 0
        
        for triple in triples:
            if not self._is_quality_triple(triple, confidence_threshold):
                quality_rejected += 1
                continue
            
            triple_key = (triple.subject.lower(), triple.predicate.lower(), triple.object.lower())
            if triple_key in seen:
                dedup_rejected += 1
                continue
            seen.add(triple_key)
            filtered.append(triple)
        
        # DEGRADED THRESHOLD: Wenn strenger Filter ALLES verwirft
        if not filtered and triples:
            degraded = KG_CONFIG["degraded_confidence_threshold"]
            logger.info(
                f"⚠️ Alle {len(triples)} Triples unter {confidence_threshold:.2f} — "
                f"verwende degraded threshold {degraded:.2f}"
            )
            seen.clear()
            for triple in triples:
                if not self._is_quality_triple(triple, degraded):
                    continue
                triple_key = (triple.subject.lower(), triple.predicate.lower(), triple.object.lower())
                if triple_key in seen:
                    continue
                seen.add(triple_key)
                filtered.append(triple)
        
        # Sort by confidence (beste zuerst)
        filtered.sort(key=lambda x: x.confidence, reverse=True)
        
        # KEIN Hard-Limit: result = filtered (nicht filtered[:limit])
        logger.info(
            f"🎯 Quality-Gates: {len(triples)} roh → {len(filtered)} behalten "
            f"(Quality: -{quality_rejected}, Dedup: -{dedup_rejected}, "
            f"Confidence ≥ {confidence_threshold})"
        )
        
        return filtered
    
    def _is_quality_triple(self, triple: KGTriple, confidence_threshold: float = 0.45) -> bool:
        """
        Prüft ob Triple hohe Qualität hat (mit ADAPTIVEM Confidence-Threshold).
        
        Strategie:
        - Basis-Checks (Länge, Semantik) sind fix
        - Confidence-Threshold ist adaptiv (abhängig von Dokumentgröße)
        
        Args:
            triple: Zu prüfendes Triple
            confidence_threshold: Adaptiver Threshold (von _get_adaptive_confidence_threshold)
        """
        
        # Mindestlängen
        if any(len(x) < 2 for x in [triple.subject, triple.predicate, triple.object]):
            return False
        
        # Keine leeren oder numerischen Werte (nur Zahlen)
        # ★ FIX: .lower() für case-insensitiven Vergleich — vorher war 'None' != 'none'!
        _NONE_VALUES = {'', 'null', 'none', 'n/a', '-', 'undefined', 'unbekannt', 'na', 'nil', '...', 'unknown'}
        if any(x.strip().lower() in _NONE_VALUES for x in [triple.subject, triple.object]):
            return False
        
        # ★ PRONOMEN-FIX: Auch in _is_quality_triple Pronomen ablehnen
        if any(x.strip().lower() in self._PRONOUN_SET for x in [triple.subject, triple.object]):
            return False
        
        # ★ SOTA: Garbage Entity Filter — reject entities that are not real entities
        # Root cause fix for 6.2% garbage entities (numbers, sentence fragments)
        _NUMERIC_ENTITY_RE = re.compile(r'^\d+([.,]\d+)?[%°]?$')
        for field in [triple.subject, triple.object]:
            text = field.strip()
            # Pure numeric values ("3.92", "25%", "100") are not entities
            if _NUMERIC_ENTITY_RE.match(text):
                return False
            # Overlength text (>80 chars) = sentence fragment, not an entity
            if len(text) > 80:
                return False
            # Too many words (>8) = description/sentence, not an entity
            if len(text.split()) > 8:
                return False
        
        # Predicate sollte semantisch sein
        if triple.predicate.isdigit() or len(triple.predicate.split()) == 0:
            return False
        
        # ★ SOTA: Erweiterte generische Predicate-Blocklist (DE + EN)
        # Enthält auch Stoppwörter und low-info Verben die als Predicate nutzlos sind
        _GENERIC_PREDICATES = {
            # Deutsch — Stoppwörter / Artikel / Konjunktionen
            'der', 'die', 'das', 'ein', 'eine', 'und', 'oder', 'von', 'zu', 'mit',
            'auf', 'in', 'an', 'für', 'als', 'nach', 'bei', 'aus', 'um', 'über',
            'durch', 'vor', 'zwischen', 'unter', 'bis', 'gegen', 'seit', 'ohne',
            # Deutsch — zu generische Verben (allein stehend = nutzlos)
            'ist', 'hat', 'wird', 'war', 'sind', 'haben', 'werden', 'wurde',
            'kann', 'soll', 'muss', 'darf', 'mag',
            # Englisch — Stoppwörter
            'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'for',
            'with', 'by', 'from', 'as', 'but', 'not', 'be',
            # Englisch — zu generische Verben
            'is', 'has', 'was', 'are', 'have', 'were', 'can', 'will', 'may',
            'shall', 'should', 'do', 'does', 'did',
        }
        if triple.predicate.strip().lower() in _GENERIC_PREDICATES:
            return False
        
        # Predicate mit nur 1-2 Buchstaben ist nutzlos
        if len(triple.predicate.strip()) < 3:
            return False
        
        # ADAPTIVE Confidence-Check
        # ✅ Threshold wird von _filter_and_deduplicate() übergeben
        # → Kleine Docs: 0.65 (streng)
        # → Große Docs: 0.45 (permissiv)
        if triple.confidence < confidence_threshold:
            return False
        
        return True
    
    def _fallback_extraction(self, text: str) -> List[KGTriple]:
        """Einfache Fallback-Extraktion falls LLM nicht verfügbar"""
        logger.info("🔄 Verwende Fallback-KG-Extraktion")
        
        # Sehr einfache regelbasierte Extraktion
        triples = []
        
        # Suche nach einfachen Patterns
        patterns = [
            (r'(\w+(?:\s+\w+)*)\s+ist\s+(\w+(?:\s+\w+)*)\s+von\s+(\w+(?:\s+\w+)*)', 'ist {} von'),
            (r'(\w+(?:\s+\w+)*)\s+arbeitet\s+bei\s+(\w+(?:\s+\w+)*)', 'arbeitet bei'),
            (r'(\w+(?:\s+\w+)*)\s+leitet\s+(\w+(?:\s+\w+)*)', 'leitet'),
        ]
        
        for pattern, relation_template in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    if len(match) == 3:
                        subj, role, org = match
                        relation = relation_template.format(role)
                        # WICHTIG: Normalisiere alle Triple-Felder (entfernt Unterstriche etc.)
                        # ★ SOTA: Regex-Fallback bekommt ehrliche niedrige Confidence (0.45)
                        # statt der alten 0.7, die den adaptiven Threshold automatisch passierte.
                        # So kann der downstream Quality-Gate entscheiden, ob der Triple gut genug ist.
                        triple = KGTriple(normalize_text(subj), normalize_text(relation), normalize_text(org), confidence=0.45)
                    else:
                        subj, obj = match
                        # WICHTIG: Normalisiere alle Triple-Felder (entfernt Unterstriche etc.)
                        # ★ SOTA: Ehrliche Confidence 0.45 für Regex-Fallback
                        triple = KGTriple(normalize_text(subj), normalize_text(relation_template), normalize_text(obj), confidence=0.45)
                    
                    triples.append(triple)
        
        return triples[:10]  # Limitiert für Fallback
