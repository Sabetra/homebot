"""
Hybrid Reasoning Components für AgentOrchestrator
==================================================

Dieses Modul implementiert die 3 Kern-Komponenten des Hybrid-Reasoning:

1. Meta-Orchestration: LLM entscheidet Strategie (semantic/hybrid/keyword)
2. Evidence-Optimization: Deduplizierung, Diversität, LLM-Reranking, Contradiction Detection
3. Answer-Validation: Cross-Encoder-basierte semantische Grounding-Prüfung

Autor: Refactored from orchestrator.py (2025-10-08)
Updated: 2025-10-09 (Contradiction Detection Integration)
Updated: 2026-03-20 (SOTA Grounding: Cross-Encoder statt BoW, NLP Sentence Splitting)
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, Callable, TYPE_CHECKING
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
import re

# NEU: Import ContradictionDetector
from agent.contradiction_detector import ContradictionDetector

if TYPE_CHECKING:
    from agent.cross_encoder_reranker import CrossEncoderReranker

logger = logging.getLogger(__name__)


@dataclass
class Evidence:
    """Repräsentiert eine Evidence mit allen Metadaten für Hybrid-Reasoning"""
    content: str
    source: str
    score: float
    domain: str = "general"
    timestamp: Optional[datetime] = None
    content_hash: Optional[str] = None
    
    def __post_init__(self):
        if self.content_hash is None:
            self.content_hash = hashlib.md5(self.content.encode()).hexdigest()
        if self.timestamp is None:
            self.timestamp = datetime.now()


class HybridReasoning:
    """Hybrid-Reasoning Engine: Kombiniert Rules-based + LLM-based Decisions"""
    
    def __init__(self, llm_callable, cross_encoder: Optional['CrossEncoderReranker'] = None,
                 summarize_fn: Optional[Callable] = None,
                 verify_fn: Optional[Callable] = None):
        """
        Args:
            llm_callable: Funktion/Methode für LLM-Calls (signature: llm(prompt, max_tokens) -> str)
            cross_encoder: Optional CrossEncoderReranker-Instanz für semantisches Grounding
            summarize_fn: Optional Orchestrator.summarize für echte Re-Synthese
            verify_fn: Optional Orchestrator.verify_step für echte Re-Verifikation
        """
        self.llm = llm_callable
        self.cross_encoder: Optional['CrossEncoderReranker'] = cross_encoder
        self.summarize_fn = summarize_fn
        self.verify_fn = verify_fn
        # NEU: ContradictionDetector initialisieren
        self.contradiction_detector = ContradictionDetector(llm_callable=llm_callable)
        
        # SOTA Sentence-Splitting: Kompilierte Regex-Patterns
        # Erkennt Satzgrenzen an ". ", "! ", "? " gefolgt von Großbuchstabe oder Zeilenende
        # ABER NICHT nach Abkürzungen (z.B., d.h., u.a., etc., Nr., Dr., Prof., ca., bzw.)
        self._abbreviations = {
            'z.b', 'd.h', 'u.a', 'o.ä', 'etc', 'nr', 'dr', 'prof', 'ca', 'bzw',
            'ggf', 'evtl', 'inkl', 'exkl', 'max', 'min', 'abs', 'vgl', 'sog',
            'bspw', 'usw', 'uvm', 'zzgl', 'mio', 'mrd', 'tel', 'vol',
            'e.g', 'i.e', 'vs', 'fig', 'approx', 'dept', 'est', 'govt',
        }
        
        # Grounding Score Threshold: Satz gilt als verankert wenn
        # Cross-Encoder relevance score > diesen Wert.
        # Scores werden via Sigmoid in [0,1] kalibriert (durchgängig mit
        # agent/reranker.py und cross_encoder_reranker.rerank() / Stage A+C).
        # 0.5 ist die natürliche Entscheidungsgrenze des Modells:
        # bge-reranker-v2-m3 produziert für relevante Paare typ. >0.7,
        # für irrelevante <0.4.
        self.grounding_threshold = 0.5
        
        ce_status = "mit Cross-Encoder" if cross_encoder else "ohne Cross-Encoder (BoW-Fallback)"
        logger.info(f"✅ HybridReasoning initialisiert ({ce_status}, mit Contradiction Detection)")
    
    # ==================== KOMPONENTE 1: Meta-Orchestration ====================
    
    def meta_orchestrate(self, query: str, intent: str) -> str:
        """Meta-Orchestrator: Entscheidet LLM vs. Rules für jede Phase"""
        logger.info(f"[META] Meta-Orchestration für Intent: {intent}")
        
        # Phase 1: Evidence-Beschaffung (Rules-based)
        k = self._get_k_for_intent(intent)
        
        # Phase 2: LLM-Strategie-Auswahl
        strategy = self._llm_resolve_strategy(query, intent)
        logger.info(f"[META] LLM wählte Strategie: {strategy}")
        
        return strategy
    
    def _llm_resolve_strategy(self, query: str, intent: str) -> str:
        """LLM entscheidet zwischen 'semantic', 'hybrid', 'keyword'"""
        prompt = f"""Du bist ein Meta-Orchestrator. Entscheide die beste RAG-Strategie:

Query: {query}
Intent: {intent}

Strategien:
- semantic: Für konzeptuelle Fragen (Warum? Was bedeutet?)
- hybrid: Für Faktenfragen mit Context (Wer ist? Was ist die Hauptstadt?)
- keyword: Für Definitionen/Listen (Liste alle...)

Antworte NUR mit einem Wort: semantic, hybrid oder keyword"""
        
        response = self.llm(prompt, max_tokens=10)
        strategy: str = str(response).strip().lower()
        
        if strategy not in ["semantic", "hybrid", "keyword"]:
            logger.warning(f"[META] Ungültige Strategie '{strategy}', Fallback auf 'hybrid'")
            return "hybrid"
        
        return strategy
    
    def _get_k_for_intent(self, intent: str) -> int:
        """Rules-based: Bestimmt k basierend auf Intent"""
        intent_k_map = {
            "greeting": 1,
            "farewell": 1,
            "factual": 5,
            "analytical": 8,
            "conversational": 3
        }
        return intent_k_map.get(intent, 5)
    
    # ==================== KOMPONENTE 2: Evidence-Optimization ====================
    
    def optimize_evidence(self, evidences: List[Evidence], intent: str, 
                         enable_contradiction_check: bool = True) -> List[Evidence]:
        """Optimiert Evidence durch Deduplizierung, Diversität, Reranking und Contradiction Detection"""
        logger.info(f"[OPTIMIZE] Starte mit {len(evidences)} Evidences")
        
        # Schritt 1: Deduplizierung
        evidences = self._deduplicate_by_content_hash(evidences)
        logger.info(f"[OPTIMIZE] Nach Deduplizierung: {len(evidences)} Evidences")
        
        # Schritt 2: Domain-Diversität
        evidences = self._ensure_domain_diversity(evidences, max_per_domain=2)
        logger.info(f"[OPTIMIZE] Nach Domain-Diversität: {len(evidences)} Evidences")
        
        # NEU: Schritt 2.5: Contradiction Detection & Filtering
        if enable_contradiction_check and len(evidences) >= 2:
            evidences = self._check_and_resolve_contradictions(evidences)
            logger.info(f"[OPTIMIZE] Nach Contradiction-Check: {len(evidences)} Evidences")
        
        # Schritt 3: Semantic Reranking (LLM-basiert)
        if self._needs_semantic_reranking(intent):
            evidences = self._llm_semantic_rerank(evidences, intent)
            logger.info(f"[OPTIMIZE] Nach LLM-Reranking: Top-3 Scores: {[e.score for e in evidences[:3]]}")
        
        return evidences
    
    def _deduplicate_by_content_hash(self, evidences: List[Evidence]) -> List[Evidence]:
        """Entfernt exakte Duplikate basierend auf Content-Hash"""
        seen_hashes = set()
        unique = []
        for ev in evidences:
            if ev.content_hash not in seen_hashes:
                seen_hashes.add(ev.content_hash)
                unique.append(ev)
        return unique
    
    def _ensure_domain_diversity(self, evidences: List[Evidence], max_per_domain: int = 2) -> List[Evidence]:
        """Begrenzt Evidences pro Domain für Diversität"""
        domain_counts: Dict[str, int] = {}
        diverse = []
        for ev in evidences:
            count = domain_counts.get(ev.domain, 0)
            if count < max_per_domain:
                diverse.append(ev)
                domain_counts[ev.domain] = count + 1
        return diverse
    
    def _needs_semantic_reranking(self, intent: str) -> bool:
        """Rules-based: Prüft ob LLM-Reranking nötig ist"""
        return intent in ["analytical", "comparative", "explanatory"]
    
    def _llm_semantic_rerank(self, evidences: List[Evidence], intent: str) -> List[Evidence]:
        """LLM bewertet Relevanz jeder Evidence neu"""
        if not evidences:
            return evidences
        
        prompt = f"""Bewerte die Relevanz jeder Evidence für Intent: {intent}

Evidences:
{chr(10).join([f"{i+1}. {ev.content[:100]}..." for i, ev in enumerate(evidences)])}

Gib die Reihenfolge als Nummern zurück (z.B. "3,1,5,2,4"):"""
        
        response = self.llm(prompt, max_tokens=50)
        try:
            order = [int(x.strip()) - 1 for x in response.split(",")]
            reranked = [evidences[i] for i in order if 0 <= i < len(evidences)]
            return reranked if reranked else evidences
        except Exception as e:
            logger.warning(f"[RERANK] LLM-Parsing fehlgeschlagen: {e}, behalte Original-Reihenfolge")
            return evidences
    
    def _check_and_resolve_contradictions(self, evidences: List[Evidence]) -> List[Evidence]:
        """Prüft und löst Widersprüche zwischen Evidences via ContradictionDetector"""
        if len(evidences) < 2:
            return evidences
        
        logger.info(f"[CONTRADICTION-CHECK] Überprüfe {len(evidences)} Evidences auf Widersprüche")
        
        # Nutze den dedizierten ContradictionDetector
        contradictions = self.contradiction_detector.detect_contradictions(
            evidences=evidences,
            query="",  # Kein spezifischer Query im Optimization-Kontext
            use_llm=True  # Nutze LLM für semantische Prüfung
        )
        
        if contradictions:
            logger.info(f"[CONTRADICTION-CHECK] {len(contradictions)} Widersprüche erkannt")
            
            # Löse Widersprüche auf und entferne unzuverlässige Sources
            filtered_evidences, resolution_report = self.contradiction_detector.resolve_contradictions(
                contradictions=contradictions,
                evidences=evidences
            )
            
            logger.info(f"[CONTRADICTION-CHECK] Resolution: {resolution_report.get('removed', 0)} entfernt, {resolution_report.get('kept', 0)} behalten")
            
            # Update Reliability-Tracking
            for ev in evidences:
                had_contradiction = ev not in filtered_evidences
                self.contradiction_detector.update_reliability(ev, had_contradiction)
            
            return list(filtered_evidences)
        else:
            logger.info("[CONTRADICTION-CHECK] Keine Widersprüche gefunden")
            # Alle als validiert markieren
            for ev in evidences:
                self.contradiction_detector.update_reliability(ev, had_contradiction=False)
            
            return evidences
    
    # ==================== KOMPONENTE 3: Answer-Validation ====================
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """
        SOTA Sentence-Splitting für deutsch/englisch gemischte Texte.
        
        Berücksichtigt:
        - Satzenden: ". ", "! ", "? " gefolgt von Großbuchstabe/Zeilenumbruch
        - Markdown: Überschriften, Bullet-Points, Aufzählungen als eigene "Sätze"
        - Abkürzungen: z.B., d.h., etc. werden NICHT als Satzende erkannt
        - Aufzählungsnummern: "1. Punkt" wird NICHT gesplittet
        """
        if not text or not text.strip():
            return []
        
        # Phase 1: Markdown-Strukturelemente normalisieren
        # Entferne Markdown-Header-Marker (### Header → Header)
        clean = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Entferne Bold/Italic Marker
        clean = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', clean)
        # Entferne Inline-Code
        clean = re.sub(r'`([^`]+)`', r'\1', clean)
        
        # Phase 2: Splitte an expliziten Absatzgrenzen (Doppel-Newlines, Bullet-Points)
        # Zuerst: Trenne an Doppel-Newlines
        blocks = re.split(r'\n\s*\n', clean)
        
        sentences = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            
            # Prüfe ob Block ein Bullet-Point / Aufzählung ist
            lines = block.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Entferne Bullet-Point-Marker (-, *, •)
                line = re.sub(r'^[\-\*•]\s+', '', line)
                # Entferne Nummern-Marker (1. , 2. , a) , b) )
                line = re.sub(r'^(?:\d+[.)]\s+|[a-z][.)]\s+)', '', line, flags=re.IGNORECASE)
                
                if not line.strip():
                    continue
                
                # Phase 3: Innerhalb einer Zeile an Satzgrenzen splitten
                # ABER: Nicht nach Abkürzungen und nicht nach Listennummern
                line_sentences = self._split_line_into_sentences(line)
                sentences.extend(line_sentences)
        
        # Phase 4: Filtere zu kurze Fragmente (< 15 Zeichen = kein sinnvoller Satz)
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) >= 15]
    
    def _split_line_into_sentences(self, line: str) -> List[str]:
        """Splittet eine einzelne Zeile an Satzgrenzen unter Berücksichtigung von Abkürzungen."""
        # Regex: Satzende-Zeichen gefolgt von Leerzeichen + Großbuchstabe
        # Lookahead: nächstes Wort beginnt mit Großbuchstabe
        # Lookbehind: mindestens 2 Zeichen vor dem Satzende (schließt "1. " aus)
        parts = []
        current = ""
        
        # Tokenisiere an potenziellen Satzenden
        i = 0
        while i < len(line):
            current += line[i]
            
            # Prüfe ob wir an einem potenziellen Satzende sind
            if line[i] in '.!?' and i + 1 < len(line):
                next_char = line[i + 1] if i + 1 < len(line) else ''
                
                # Bedingung für echtes Satzende:
                # 1. Nächstes Zeichen ist Leerzeichen
                # 2. Danach kommt Großbuchstabe (oder Zeilenende)
                # 3. Kein Abkürzungs-Match
                if next_char == ' ':
                    # Prüfe ob nach dem Leerzeichen ein Großbuchstabe kommt
                    rest = line[i + 2:] if i + 2 < len(line) else ''
                    starts_upper = rest and rest[0].isupper()
                    
                    # Prüfe ob es eine Abkürzung ist
                    is_abbreviation = self._is_abbreviation(current)
                    
                    # Prüfe ob es eine Aufzählungsnummer ist ("1. ", "2. ")
                    is_list_number = bool(re.search(r'\d\.$', current.rstrip()))
                    
                    if starts_upper and not is_abbreviation and not is_list_number:
                        # Echtes Satzende
                        parts.append(current.strip())
                        current = ""
                        i += 2  # Überspringe Leerzeichen
                        continue
            
            i += 1
        
        if current.strip():
            parts.append(current.strip())
        
        return parts
    
    def _is_abbreviation(self, text: str) -> bool:
        """Prüft ob das Ende von text eine bekannte Abkürzung ist."""
        text_lower = text.lower().rstrip('.')
        # Letztes Wort extrahieren
        words = text_lower.split()
        if not words:
            return False
        last_word = words[-1].rstrip('.')
        return last_word in self._abbreviations
    
    def validate_answer(self, answer: str, evidences: List[Evidence], query: str) -> dict:
        """
        SOTA Hybrid-Validierung: Cross-Encoder Semantic Grounding + LLM Completeness.
        
        Workflow:
        1. Evidence-aware Mindestlänge prüfen
        2. Answer bereinigen (Citations, Markdown, Quellen-Blöcke)
        3. SOTA Sentence-Splitting (NLP-aware, Abkürzungs-sicher)
        4. Semantic Grounding via Cross-Encoder (multilingual, paraphrase-robust)
           Fallback: BoW-Overlap wenn kein Cross-Encoder verfügbar
        5. LLM Completeness Check
        """
        logger.info("[VALIDATE] Starte Antwort-Validierung")
        
        # Evidence-aware minimum length check
        length = len(answer)
        min_length = self._determine_min_length(query, evidences)
        length_ok = length >= min_length
        
        # WICHTIG: Citation/Sources-Blöcke vor Grounding-Check entfernen
        answer_clean = self._clean_answer_for_grounding(answer)
        
        # SOTA Sentence Splitting (statt primitiver ". "-Split)
        sentences = self._split_into_sentences(answer_clean)
        
        if not sentences:
            logger.warning("[VALIDATE] Keine Sätze nach Splitting -- Antwort ist leer oder nur Formatierung")
            return {
                "length_ok": length_ok,
                "min_length": min_length,
                "actual_length": length,
                "grounding_ratio": 0.0,
                "completeness_score": 0.0,
                "passed": False,
                "sentence_count": 0,
                "method": "empty"
            }
        
        # ★ SOTA: Handle 0-evidence case explicitly.
        # When no evidences survived the pipeline (e.g. web search → cross-encoder
        # dropped all), grounding is NOT APPLICABLE, not "failed".
        # The answer was synthesized from LLM knowledge / web snippets that were lost.
        # Failing grounding here would trigger futile re-synthesis loops.
        if not evidences:
            logger.warning(
                "[VALIDATE] 0 Evidences vorhanden — Grounding nicht anwendbar. "
                "Antwort basiert auf LLM-Synthese ohne verifizierbare Evidence."
            )
            completeness_score = self._llm_check_completeness(answer, query)
            # Pass validation based on completeness alone (grounding N/A)
            grounding_na_passed = length_ok and completeness_score > 0.5
            return {
                "length_ok": length_ok,
                "min_length": min_length,
                "actual_length": length,
                "grounding_ratio": -1.0,  # Sentinel: not applicable
                "completeness_score": completeness_score,
                "passed": grounding_na_passed,
                "sentence_count": len(sentences),
                "grounded_count": 0,
                "method": "no-evidence",
            }
        
        # Semantic Grounding Check
        if self.cross_encoder is not None:
            grounded_count, grounding_details = self._cross_encoder_grounding(sentences, evidences)
            grounding_method = "cross-encoder"
        else:
            grounded_count, grounding_details = self._bow_grounding_fallback(sentences, evidences)
            grounding_method = "bow-fallback"
        
        grounding_ratio = grounded_count / len(sentences) if sentences else 0
        
        logger.info(
            f"[VALIDATE] Grounding ({grounding_method}): "
            f"{grounded_count}/{len(sentences)} Sätze verankert = {grounding_ratio:.2f}"
        )
        if grounding_details:
            # Log die besten/schlechtesten Scores für Debugging
            scores = [d['score'] for d in grounding_details if 'score' in d]
            if scores:
                logger.debug(
                    f"[VALIDATE] Score-Range: min={min(scores):.3f}, max={max(scores):.3f}, "
                    f"mean={sum(scores)/len(scores):.3f}"
                )
        
        # LLM-Completeness Check
        completeness_score = self._llm_check_completeness(answer, query)
        
        validation = {
            "length_ok": length_ok,
            "min_length": min_length,
            "actual_length": length,
            "grounding_ratio": grounding_ratio,
            "completeness_score": completeness_score,
            "passed": length_ok and grounding_ratio > 0.3 and completeness_score > 0.5,
            "sentence_count": len(sentences),
            "grounded_count": grounded_count,
            "method": grounding_method,
        }
        
        logger.info(f"[VALIDATE] Ergebnis: {validation}")
        return validation
    
    def _clean_answer_for_grounding(self, answer: str) -> str:
        """
        Bereinigt die Antwort für Grounding-Analyse.
        Entfernt alle Elemente die NICHT aus der Evidence stammen können:
        Citations, Quellen-Blöcke, URLs, Markdown-Formatierung.
        """
        text = answer
        # Entferne Quellen-Blöcke am Ende
        text = re.split(r'\n\s*(?:Quellen|Sources|Referenzen|References|Quellenangaben)\s*[:\-]', text)[0]
        # Entferne Zitationsmarker [1], [2], etc.
        text = re.sub(r'\[\d+\]', '', text)
        # Entferne HTML-Links
        text = re.sub(r'<a\s[^>]*>.*?</a>', '', text)
        # Entferne Markdown-Link-Syntax (behalte Link-Text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # Entferne Trennlinien (--- , ___ , ***)
        text = re.sub(r'\n\s*[-_*]{3,}\s*\n', '\n', text)
        # Entferne URL-Zeilen
        text = re.sub(r'https?://\S+', '', text)
        # Entferne leere Klammern die nach URL-Entfernung übrig bleiben
        text = re.sub(r'\(\s*\)', '', text)
        return text.strip()
    
    def _cross_encoder_grounding(
        self, sentences: List[str], evidences: List[Evidence]
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        SOTA Semantic Grounding via Cross-Encoder.
        
        Für jeden Satz wird die maximale semantische Relevanz zu allen Evidences
        via bge-reranker-v2-m3 berechnet. Vorteile:
        - Multilingual: Deutsch ↔ Englisch funktioniert
        - Paraphrase-robust: Synonyme, Umformulierungen werden erkannt
        - Kontextbewusst: Berücksichtigt Bedeutung, nicht nur Wort-Overlap
        
        Returns:
            (grounded_count, details_list)
        """
        if not evidences or not sentences:
            return 0, []

        # Satisfy mypy: this method is only called when self.cross_encoder is not None
        # (guarded at validate_answer line 429). Assert for static narrowing.
        assert self.cross_encoder is not None
        
        # Lazy-Init des Cross-Encoders. Fail-fast: BoW-Fallback ist ein
        # Keyword-basierter Quality-Downgrade und versteckt Modell-Faults.
        self.cross_encoder._lazy_init()
        
        # Erstelle alle (sentence, evidence) Paare
        evidence_texts = [ev.content or '' for ev in evidences]
        
        details = []
        grounded = 0
        
        # Batch-Scoring: Für jeden Satz scores gegen ALLE evidences berechnen
        all_pairs = []
        pair_map = []  # (sentence_idx, evidence_idx)
        for s_idx, sentence in enumerate(sentences):
            for e_idx, ev_text in enumerate(evidence_texts):
                if ev_text.strip():
                    all_pairs.append([sentence, ev_text])
                    pair_map.append((s_idx, e_idx))
        
        if not all_pairs:
            return 0, []
        
        try:
            import numpy as np
            scores_arr = self.cross_encoder.model.predict(
                all_pairs,
                batch_size=self.cross_encoder.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True
            )

            # SOTA Calibration: bge-reranker-v2-m3 emits unbounded logits via
            # sentence-transformers' default Identity activation. Apply sigmoid
            # to map to [0, 1] interpretable as relevance probability — same
            # behaviour as agent/reranker.py and CrossEncoderReranker.rerank().
            # This makes the threshold semantically equivalent to a probability cutoff.
            scores_arr = np.asarray(scores_arr, dtype=np.float32)
            scores_arr = 1.0 / (1.0 + np.exp(-scores_arr))
            scores = scores_arr.tolist()

        except Exception as e:
            # Fail-fast: BoW-Fallback ist Keyword-basiert und maskiert echte
            # Reranker-Faults. Quality-Gate muss sichtbar bleiben.
            raise RuntimeError(
                f"Cross-Encoder grounding scoring failed: {e}"
            ) from e
        
        # Aggregiere: Für jeden Satz den maximalen Score über alle Evidences
        sentence_max_scores: Dict[int, float] = {}
        sentence_best_evidence: Dict[int, int] = {}
        
        for pair_idx, (s_idx, e_idx) in enumerate(pair_map):
            score = scores[pair_idx]
            if s_idx not in sentence_max_scores or score > sentence_max_scores[s_idx]:
                sentence_max_scores[s_idx] = score
                sentence_best_evidence[s_idx] = e_idx
        
        for s_idx, sentence in enumerate(sentences):
            max_score = sentence_max_scores.get(s_idx, -999.0)
            is_grounded = max_score > self.grounding_threshold
            if is_grounded:
                grounded += 1
            
            best_ev = sentence_best_evidence.get(s_idx, -1)
            details.append({
                "sentence": sentence[:80],
                "score": max_score,
                "grounded": is_grounded,
                "best_evidence_idx": best_ev,
            })
        
        # Log Übersicht
        ungrounded = [d for d in details if not d['grounded']]
        if ungrounded:
            logger.debug(
                f"[VALIDATE] Ungrounded Sätze ({len(ungrounded)}):"
            )
            for d in ungrounded[:3]:  # Max 3 loggen
                logger.debug(f"  Score={d['score']:.3f}: {d['sentence']}")
        
        return grounded, details
    
    def _bow_grounding_fallback(
        self, sentences: List[str], evidences: List[Evidence]
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        Bag-of-Words Fallback wenn kein Cross-Encoder verfügbar.
        Verwendet Word-Overlap als deterministischen Last-Resort, wenn weder
        Cross-Encoder noch NLI-Modell geladen werden können.
        """
        stopwords = {
            'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'einen',
            'einem', 'einer', 'und', 'oder', 'aber', 'auch', 'als', 'von',
            'für', 'mit', 'auf', 'aus', 'bei', 'nach', 'über', 'vor', 'zum',
            'zur', 'bis', 'wie', 'was', 'wer', 'ist', 'sind', 'hat', 'haben',
            'wird', 'werden', 'kann', 'können', 'sich', 'nicht', 'nur',
            'noch', 'dann', 'wenn', 'dass', 'diese', 'dieser', 'dieses',
            'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
            'was', 'one', 'our', 'out', 'with', 'from', 'this', 'that',
            'which', 'their', 'will', 'each', 'about', 'into', 'been',
        }
        
        # Sammle Evidence-Wörter einmalig
        evidence_words: set[str] = set()
        for ev in evidences:
            ev_text = (ev.content or '').lower()
            evidence_words.update(
                w for w in re.findall(r'\b\w{3,}\b', ev_text)
                if w not in stopwords
            )
        
        details = []
        grounded = 0
        
        for sentence in sentences:
            sentence_lower = sentence.lower().strip()
            sentence_words = set(
                w for w in re.findall(r'\b\w{3,}\b', sentence_lower)
                if w not in stopwords
            )
            
            if not sentence_words:
                details.append({"sentence": sentence[:80], "score": 0.0, "grounded": False})
                continue
            
            overlap = sentence_words & evidence_words
            ratio = len(overlap) / len(sentence_words)
            is_grounded = ratio >= 0.25
            
            if is_grounded:
                grounded += 1
            
            details.append({
                "sentence": sentence[:80],
                "score": ratio,
                "grounded": is_grounded,
            })
        
        return grounded, details
    
    def _determine_min_length(self, query: str, evidences: Optional[List[Evidence]] = None) -> int:
        """
        Evidence-aware adaptive MINIMUM Antwortlänge.
        
        Statt eines fixen "optimalen" Werts mit symmetrischer Toleranz wird nur
        die minimale erwartete Länge berechnet. Logik:
        
        1. Basis-Minimum nach Query-Typ (Statement/Fakt/Erklärung)
        2. Skalierung nach Evidence-Volumen: Mehr Evidence → höheres Minimum
           (wenn 12 Evidences à 500 Zeichen vorhanden sind, aber die Antwort
           nur 30 Zeichen hat, wurde die Evidence nicht genutzt)
        3. Kein Maximum: Eine umfassende, gut verankerte Antwort ist immer besser
           als eine gekürzte
        """
        query_lower = query.lower()
        
        # Basis-Minimum nach Query-Typ
        if "?" not in query:
            base_min = 30   # Statements können sehr kurz sein
        elif any(w in query_lower for w in [
            "warum", "erkläre", "wie funktioniert", "beschreibe",
            "erläutere", "analysiere", "vergleiche",
            "why", "explain", "how", "describe", "analyze", "compare"
        ]):
            base_min = 100  # Erklärungen brauchen mindestens einen Absatz
        else:
            base_min = 50   # Faktenfragen können ein Satz sein
        
        if not evidences:
            return base_min
        
        # Skalierung nach Evidence-Volumen
        evidence_chars = sum(len(ev.content or '') for ev in evidences)
        
        if evidence_chars == 0:
            return base_min
        
        # Bei reicher Evidence: Antwort sollte mindestens 5% des Evidence-Volumens sein
        # Beispiel: 12 Evidences × 500 Zeichen = 6000 → min 300 Zeichen
        # Das fängt den Fall ab, dass der LLM trotz vieler Evidences nur einen Einzeiler gibt
        evidence_scaled_min = max(base_min, int(evidence_chars * 0.05))
        
        # Deckelung bei 500 Zeichen -- wir wollen nicht 5000 Zeichen Minimum
        # nur weil 100K Zeichen Evidence vorhanden sind
        return min(evidence_scaled_min, 500)
    
    def _llm_check_completeness(self, answer: str, query: str) -> float:
        """LLM bewertet Vollständigkeit der Antwort"""
        prompt = f"""Bewerte die Vollständigkeit dieser Antwort auf einer Skala von 0.0 bis 1.0:

Frage: {query}
Antwort: {answer}

Gib NUR eine Zahl zurück (z.B. 0.85):"""
        
        response = self.llm(prompt, max_tokens=10)
        try:
            score = float(response.strip())
            return max(0.0, min(1.0, score))
        except Exception as e:
            logger.warning(f"[VALIDATE] LLM-Score-Parsing fehlgeschlagen: {e}, verwende 0.5")
            return 0.5
    
    def regenerate_with_full_pipeline(
        self, 
        query: str, 
        history: List[Dict[str, Any]],
        sources: Any,
        extras: List[str],
        evidences: List[Evidence]
    ) -> Optional[str]:
        """
        SOTA Re-Synthese: Nutzt den vollständigen Summarizer+Verifier-Flow
        statt eines simplen Prompts. Bewahrt Chat-History und Tool-Kontext.
        
        Fallback auf simplen Prompt wenn summarize_fn/verify_fn nicht verfügbar.
        
        Args:
            query: Original-Query
            history: Chat-History
            sources: Original Source-Objekte (für Summarizer)
            extras: Extra-Ergebnisse (Calculator, Code-Exec)
            evidences: Optimierte Evidences
        
        Returns:
            Regenerierte Antwort oder None wenn nicht möglich
        """
        logger.info("[VALIDATE] Regeneriere Antwort")
        
        # Bevorzugt: Vollständiger Pipeline-Aufruf
        if self.summarize_fn is not None:
            try:
                logger.info("[VALIDATE] Nutze vollständigen Summarizer+Verifier für Regenerierung")
                draft, _ = self.summarize_fn(query, history, sources, extras, fallback=False)
                
                if self.verify_fn is not None:
                    final, _ = self.verify_fn(query=query, draft=draft, evidence=sources, fallback=False)
                    return str(final)
                
                return str(draft)
            except Exception as e:
                logger.warning(f"[VALIDATE] Pipeline-Regenerierung fehlgeschlagen: {e}")
                # Fall through zu Fallback
        
        # Fallback: Prompt-basierte Regenerierung (legacy)
        logger.info("[VALIDATE] Fallback: Prompt-basierte Regenerierung")
        return self._regenerate_with_strict_prompt(query, evidences)
    
    def _regenerate_with_strict_prompt(self, query: str, evidences: List[Evidence]) -> str:
        """Fallback-Regenerierung mit strengerem Prompt."""
        evidence_text = "\n".join([
            f"[{i+1}] ({ev.source}): {ev.content}" 
            for i, ev in enumerate(evidences[:10])
        ])
        
        strict_prompt = f"""Du MUSST diese Regeln befolgen:
1. Nutze NUR Informationen aus den Evidences
2. Zitiere die Quelle für jede Aussage als [n]
3. Beantworte die Frage umfassend -- nutze alle relevanten Evidences
4. Jede Aussage muss direkt auf eine Evidence zurückführbar sein

Evidences:
{evidence_text}

Frage: {query}

Antwort:"""
        
        return str(self.llm(strict_prompt, max_tokens=1024))
    
    def log_quality_metrics(self, validation: dict, evidences: List[Evidence]):
        """Loggt Qualitätsmetriken für Monitoring"""
        logger.info(f"[METRICS] Evidence-Count: {len(evidences)}")
        logger.info(f"[METRICS] Grounding-Ratio: {validation['grounding_ratio']:.2f}")
        logger.info(f"[METRICS] Completeness: {validation['completeness_score']:.2f}")
        logger.info(f"[METRICS] Validation-Passed: {validation['passed']}")
