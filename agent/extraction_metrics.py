"""
Extraction Metrics - RAGAS-inspirierte Qualitätsmetriken für Multimodal RAG
===========================================================================

State-of-the-Art Evaluation (2025):
- RAGAS-Framework-Metriken (Faithfulness, Context Precision/Recall)
- LLM-as-Judge für semantische Bewertung
- Vision-spezifische Metriken (Image-Text Alignment, Chart Accuracy)
- Multimodal Coherence Checking
- Automatisches Quality Dashboard

Metriken:
1. FAITHFULNESS - Ist die Beschreibung faktisch korrekt?
2. CONTEXT_PRECISION - Wie relevant sind die extrahierten Chunks?
3. CONTEXT_RECALL - Wurde alles Wichtige erfasst?
4. MULTIMODAL_COHERENCE - Stimmen Text und Bild überein?
5. IMAGE_TEXT_ALIGNMENT - Passt die Vision-Beschreibung zum Bild?
6. CHART_DATA_ACCURACY - Wurden Zahlen/Daten korrekt extrahiert?
7. TABLE_STRUCTURE_SCORE - Wurde Tabellenstruktur erkannt?
8. OCR_CONFIDENCE - Confidence der OCR-Extraktion
"""

import os
import sqlite3
import json
import logging
import time
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from contextlib import contextmanager
import hashlib
import threading

logger = logging.getLogger(__name__)

# Import cuda_lock for thread-safe LLM access.
try:
    from scripts.model_loader import cuda_lock as _cuda_lock
except ImportError:
    _cuda_lock = threading.RLock()  # Fallback


@dataclass
class ExtractionMetrics:
    """Einzelne Extraktions-Metriken"""
    # Identifikation
    source_id: str              # Hash der Quelle
    source_type: str            # "pdf", "url", "image"
    source_path: str            # Pfad/URL
    timestamp: float = 0.0      # Unix Timestamp
    
    # RAGAS-inspirierte Metriken (0.0 - 1.0)
    faithfulness: float = 0.0           # Faktische Korrektheit
    context_precision: float = 0.0      # Relevanz der Chunks
    context_recall: float = 0.0         # Vollständigkeit
    
    # Vision-spezifische Metriken
    image_text_alignment: float = 0.0   # Vision-Beschreibung passt zum Bild
    chart_data_accuracy: float = 0.0    # Zahlen korrekt extrahiert
    table_structure_score: float = 0.0  # Tabellenstruktur erkannt
    ocr_confidence: float = 0.0         # OCR-Confidence
    
    # Multimodal
    multimodal_coherence: float = 0.0   # Text+Bild-Übereinstimmung
    
    # Statistiken
    text_char_count: int = 0
    image_count: int = 0
    table_count: int = 0
    infographic_count: int = 0
    processing_time_ms: float = 0.0
    
    # Kombinierter Score
    overall_quality: float = 0.0
    
    # Metadaten
    extraction_method: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def compute_overall_quality(self) -> float:
        """Berechnet gewichteten Gesamtscore"""
        weights = {
            'faithfulness': 0.25,
            'context_precision': 0.15,
            'context_recall': 0.15,
            'multimodal_coherence': 0.15,
            'image_text_alignment': 0.10,
            'chart_data_accuracy': 0.10,
            'table_structure_score': 0.05,
            'ocr_confidence': 0.05
        }
        
        total = 0.0
        for metric, weight in weights.items():
            value = getattr(self, metric, 0.0)
            total += value * weight
        
        self.overall_quality = total
        return total


@dataclass
class QualityReport:
    """Aggregierter Qualitätsbericht"""
    # Zeitraum
    start_time: float = 0.0
    end_time: float = 0.0
    
    # Anzahlen
    total_extractions: int = 0
    pdf_count: int = 0
    url_count: int = 0
    image_count: int = 0
    
    # Durchschnittliche Metriken
    avg_faithfulness: float = 0.0
    avg_context_precision: float = 0.0
    avg_context_recall: float = 0.0
    avg_multimodal_coherence: float = 0.0
    avg_overall_quality: float = 0.0
    
    # Trends
    quality_trend: str = "stable"  # "improving", "declining", "stable"
    trend_change_percent: float = 0.0
    
    # Probleme
    low_quality_count: int = 0  # Score < 0.5
    error_count: int = 0
    common_errors: List[str] = field(default_factory=list)
    
    # Empfehlungen
    recommendations: List[str] = field(default_factory=list)


class LLMAsJudge:
    """
    Verwendet lokales LLM um Extraktionsqualität zu bewerten.
    
    State-of-the-Art Ansatz: Das LLM bewertet ob die Extraktion
    faktisch korrekt, vollständig und relevant ist.
    """
    
    def __init__(self, model_loader=None):
        self._model_loader = model_loader
        self._enabled = True
        
    @property
    def model_loader(self):
        """Lazy-loads ModelLoader"""
        if self._model_loader is None:
            try:
                from scripts.model_loader import ModelLoader
                self._model_loader = ModelLoader()
            except Exception as e:
                logger.warning(f"ModelLoader nicht verfügbar für LLM-as-Judge: {e}")
                self._enabled = False
        return self._model_loader
    
    def judge_faithfulness(
        self,
        source_text: str,
        extracted_description: str,
        max_length: int = 2000
    ) -> Tuple[float, str]:
        """
        Bewertet ob die Beschreibung dem Quelltext entspricht.
        
        Returns:
            Tuple (score, reasoning)
        """
        if not self._enabled or not self.model_loader:
            return 0.5, "LLM-as-Judge nicht verfügbar"
        
        # Texte kürzen für Effizienz
        source_short = source_text[:max_length] if len(source_text) > max_length else source_text
        desc_short = extracted_description[:max_length] if len(extracted_description) > max_length else extracted_description
        
        prompt = f"""Du bist ein Qualitätsprüfer für Textextraktionen. 
Bewerte ob die EXTRAHIERTE BESCHREIBUNG den QUELLTEXT korrekt wiedergibt.

QUELLTEXT:
{source_short}

EXTRAHIERTE BESCHREIBUNG:
{desc_short}

Bewerte auf einer Skala von 0.0 bis 1.0:
- 1.0: Vollständig korrekt und treu zum Original
- 0.7: Größtenteils korrekt, kleine Auslassungen
- 0.5: Teilweise korrekt, einige Fehler
- 0.3: Viele Fehler oder Auslassungen
- 0.0: Falsch oder irrelevant

Antworte NUR im Format:
SCORE: X.X
BEGRÜNDUNG: [kurze Begründung]"""

        try:
            loader = self.model_loader
            if loader and loader.llm:
                # LLM-Call über llama-cpp-python
                # ── CRITICAL: cuda_lock prevents concurrent llama.cpp access ──
                with _cuda_lock:
                    response_obj = loader.llm.create_chat_completion(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=200,
                        temperature=0.1
                    )
                if isinstance(response_obj, dict) and 'choices' in response_obj:
                    response = response_obj['choices'][0]['message'].get('content', '') or ''
                else:
                    response = str(response_obj) if response_obj else ''
            else:
                return 0.5, "LLM nicht geladen"
            
            # Score extrahieren (sicherstellen dass response ein String ist)
            response = str(response) if response else ''
            score_match = re.search(r'SCORE:\s*([0-9.]+)', response)
            reason_match = re.search(r'BEGRÜNDUNG:\s*(.+)', response, re.DOTALL)
            
            score = float(score_match.group(1)) if score_match else 0.5
            score = max(0.0, min(1.0, score))
            reasoning = reason_match.group(1).strip() if reason_match else "Keine Begründung"
            
            return score, reasoning
            
        except Exception as e:
            logger.warning(f"LLM-as-Judge Fehler: {e}")
            return 0.5, f"Bewertungsfehler: {e}"
    
    def judge_image_text_alignment(
        self,
        image_description: str,
        surrounding_text: str
    ) -> Tuple[float, str]:
        """
        Bewertet ob Bildbeschreibung zum umgebenden Text passt.
        """
        if not self._enabled or not self.model_loader:
            return 0.5, "LLM-as-Judge nicht verfügbar"
        
        prompt = f"""Bewerte ob diese BILDBESCHREIBUNG zum UMGEBENDEN TEXT passt.

BILDBESCHREIBUNG:
{image_description[:1000]}

UMGEBENDER TEXT:
{surrounding_text[:1000]}

Bewerte auf einer Skala von 0.0 bis 1.0:
- 1.0: Bildbeschreibung passt perfekt zum Kontext
- 0.7: Gute Übereinstimmung
- 0.5: Teilweise passend
- 0.3: Schwache Verbindung
- 0.0: Keine Verbindung oder widersprüchlich

Antworte NUR im Format:
SCORE: X.X
BEGRÜNDUNG: [kurze Begründung]"""

        try:
            loader = self.model_loader
            if loader and loader.llm:
                # ── CRITICAL: cuda_lock prevents concurrent llama.cpp access ──
                with _cuda_lock:
                    response_obj = loader.llm.create_chat_completion(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=200,
                        temperature=0.1
                    )
                if isinstance(response_obj, dict) and 'choices' in response_obj:
                    response = response_obj['choices'][0]['message'].get('content', '') or ''
                else:
                    response = str(response_obj) if response_obj else ''
            else:
                return 0.5, "LLM nicht geladen"
            
            # Score extrahieren (sicherstellen dass response ein String ist)
            response = str(response) if response else ''
            score_match = re.search(r'SCORE:\s*([0-9.]+)', response)
            reason_match = re.search(r'BEGRÜNDUNG:\s*(.+)', response, re.DOTALL)
            
            score = float(score_match.group(1)) if score_match else 0.5
            score = max(0.0, min(1.0, score))
            reasoning = reason_match.group(1).strip() if reason_match else "Keine Begründung"
            
            return score, reasoning
            
        except Exception as e:
            logger.warning(f"LLM-as-Judge Fehler: {e}")
            return 0.5, f"Bewertungsfehler: {e}"


class ExtractionQualityEvaluator:
    """
    RAGAS-inspirierter Evaluator für Extraktionsqualität.
    
    Bewertet automatisch:
    - Faithfulness (Faktentreue)
    - Context Precision (Relevanz)
    - Context Recall (Vollständigkeit)
    - Multimodal Coherence (Text-Bild-Übereinstimmung)
    """
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        enable_llm_judge: bool = True,
        model_loader=None
    ):
        if db_path is None:
            db_path = Path(os.path.expanduser("~")) / ".cache" / "rag_metrics" / "metrics.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_db()
        self._lock = threading.RLock()
        
        # LLM-as-Judge
        self.llm_judge = LLMAsJudge(model_loader) if enable_llm_judge else None
        
        logger.info(f"ExtractionQualityEvaluator initialized: {self.db_path}")
    
    def _init_db(self):
        """Initialisiert Metriken-Datenbank"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS extraction_metrics (
                source_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                timestamp REAL NOT NULL,
                
                faithfulness REAL DEFAULT 0,
                context_precision REAL DEFAULT 0,
                context_recall REAL DEFAULT 0,
                multimodal_coherence REAL DEFAULT 0,
                image_text_alignment REAL DEFAULT 0,
                chart_data_accuracy REAL DEFAULT 0,
                table_structure_score REAL DEFAULT 0,
                ocr_confidence REAL DEFAULT 0,
                overall_quality REAL DEFAULT 0,
                
                text_char_count INTEGER DEFAULT 0,
                image_count INTEGER DEFAULT 0,
                table_count INTEGER DEFAULT 0,
                infographic_count INTEGER DEFAULT 0,
                processing_time_ms REAL DEFAULT 0,
                
                extraction_method TEXT DEFAULT '',
                errors TEXT DEFAULT '[]',
                warnings TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp 
            ON extraction_metrics(timestamp)
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_type 
            ON extraction_metrics(source_type)
        """)
        
        conn.commit()
        conn.close()
    
    def _source_id(self, source_path: str) -> str:
        """Generiert eindeutige ID für Quelle"""
        return hashlib.sha256(source_path.encode()).hexdigest()[:32]
    
    def evaluate_pdf_extraction(
        self,
        pdf_path: str,
        extracted_text: str,
        vision_descriptions: Optional[List[str]] = None,
        tables_found: int = 0,
        infographics_found: int = 0,
        processing_time_ms: float = 0.0,
        extraction_method: str = "unknown"
    ) -> ExtractionMetrics:
        """
        Bewertet PDF-Extraktion.
        """
        metrics = ExtractionMetrics(
            source_id=self._source_id(pdf_path),
            source_type="pdf",
            source_path=pdf_path,
            timestamp=time.time(),
            text_char_count=len(extracted_text),
            table_count=tables_found,
            infographic_count=infographics_found,
            image_count=len(vision_descriptions) if vision_descriptions else 0,
            processing_time_ms=processing_time_ms,
            extraction_method=extraction_method
        )
        
        # Text-basierte Heuristiken
        metrics.context_precision = self._evaluate_text_quality(extracted_text)
        metrics.context_recall = self._estimate_completeness(
            extracted_text, tables_found, infographics_found
        )
        
        # Vision-Metriken
        if vision_descriptions:
            metrics.image_text_alignment = self._evaluate_vision_descriptions(
                vision_descriptions
            )
            
            # Chart-Accuracy (Zahlen in Beschreibungen)
            metrics.chart_data_accuracy = self._evaluate_chart_accuracy(
                vision_descriptions
            )
        
        # Tabellen-Score
        if tables_found > 0:
            metrics.table_structure_score = self._evaluate_table_extraction(
                extracted_text, tables_found
            )
        
        # Multimodal Coherence
        if vision_descriptions and extracted_text:
            metrics.multimodal_coherence = self._evaluate_coherence(
                extracted_text, vision_descriptions
            )
        
        # LLM-as-Judge für Faithfulness (optional, kostenintensiv)
        if self.llm_judge and len(extracted_text) > 100:
            # Nur für kürzere Texte um Performance zu sparen
            if len(extracted_text) < 5000:
                metrics.faithfulness, _ = self.llm_judge.judge_faithfulness(
                    extracted_text[:2000], 
                    extracted_text[:2000]  # Self-consistency check
                )
            else:
                metrics.faithfulness = metrics.context_precision  # Approximation
        
        # Gesamtscore berechnen
        metrics.compute_overall_quality()
        
        # Speichern
        self._save_metrics(metrics)
        
        return metrics
    
    def evaluate_url_extraction(
        self,
        url: str,
        extracted_text: str,
        image_descriptions: Optional[List[str]] = None,
        processing_time_ms: float = 0.0
    ) -> ExtractionMetrics:
        """
        Bewertet URL/Web-Extraktion.
        """
        metrics = ExtractionMetrics(
            source_id=self._source_id(url),
            source_type="url",
            source_path=url,
            timestamp=time.time(),
            text_char_count=len(extracted_text),
            image_count=len(image_descriptions) if image_descriptions else 0,
            processing_time_ms=processing_time_ms,
            extraction_method="web_extractor"
        )
        
        # Text-Qualität
        metrics.context_precision = self._evaluate_text_quality(extracted_text)
        metrics.context_recall = min(1.0, len(extracted_text) / 1000)  # Approximation
        
        # Vision-Metriken
        if image_descriptions:
            metrics.image_text_alignment = self._evaluate_vision_descriptions(
                image_descriptions
            )
            metrics.multimodal_coherence = self._evaluate_coherence(
                extracted_text, image_descriptions
            )
        
        metrics.compute_overall_quality()
        self._save_metrics(metrics)
        
        return metrics
    
    def evaluate_image_analysis(
        self,
        image_path: str,
        vision_description: str,
        surrounding_text: str = "",
        ocr_text: str = "",
        ocr_confidence: float = 0.0
    ) -> ExtractionMetrics:
        """
        Bewertet einzelne Bild-Analyse.
        """
        metrics = ExtractionMetrics(
            source_id=self._source_id(image_path),
            source_type="image",
            source_path=image_path,
            timestamp=time.time(),
            text_char_count=len(vision_description),
            image_count=1,
            ocr_confidence=ocr_confidence,
            extraction_method="vision_llm"
        )
        
        # Vision-Beschreibung bewerten
        metrics.image_text_alignment = self._evaluate_single_description(
            vision_description
        )
        
        # Chart/Daten-Accuracy
        metrics.chart_data_accuracy = self._evaluate_chart_accuracy([vision_description])
        
        # Coherence mit Umgebungstext
        if surrounding_text and self.llm_judge:
            metrics.multimodal_coherence, _ = self.llm_judge.judge_image_text_alignment(
                vision_description, surrounding_text
            )
        
        # OCR-Vergleich wenn verfügbar
        if ocr_text:
            metrics.faithfulness = self._compare_vision_ocr(
                vision_description, ocr_text
            )
        
        metrics.compute_overall_quality()
        self._save_metrics(metrics)
        
        return metrics
    
    def _evaluate_text_quality(self, text: str) -> float:
        """Bewertet Textqualität basierend auf Heuristiken"""
        if not text:
            return 0.0
        
        score = 0.0
        
        # Länge (min 100 Zeichen für guten Score)
        length_score = min(1.0, len(text) / 500)
        score += length_score * 0.3
        
        # Satzstruktur (Punkte, Absätze)
        sentence_count = text.count('.') + text.count('!') + text.count('?')
        paragraph_count = text.count('\n\n') + 1
        structure_score = min(1.0, (sentence_count / 10 + paragraph_count / 3) / 2)
        score += structure_score * 0.3
        
        # Keine übermäßigen Sonderzeichen
        special_ratio = len(re.findall(r'[^\w\s.,!?;:\-]', text)) / max(len(text), 1)
        cleanliness_score = 1.0 - min(1.0, special_ratio * 10)
        score += cleanliness_score * 0.2
        
        # Keine übermäßigen Wiederholungen
        words = text.lower().split()
        if len(words) > 10:
            unique_ratio = len(set(words)) / len(words)
            diversity_score = min(1.0, unique_ratio * 2)
            score += diversity_score * 0.2
        else:
            score += 0.1
        
        return min(1.0, score)
    
    def _estimate_completeness(
        self,
        text: str,
        tables: int,
        infographics: int
    ) -> float:
        """Schätzt Vollständigkeit der Extraktion"""
        score = 0.0
        
        # Text-Vollständigkeit
        if len(text) > 1000:
            score += 0.5
        elif len(text) > 500:
            score += 0.3
        elif len(text) > 100:
            score += 0.1
        
        # Tabellen erkannt
        if tables > 0:
            score += 0.25
        
        # Infografiken erkannt
        if infographics > 0:
            score += 0.25
        
        return min(1.0, score)
    
    def _evaluate_vision_descriptions(self, descriptions: List[str]) -> float:
        """Bewertet Qualität der Vision-Beschreibungen"""
        if not descriptions:
            return 0.0
        
        scores = []
        for desc in descriptions:
            scores.append(self._evaluate_single_description(desc))
        
        return sum(scores) / len(scores) if scores else 0.0
    
    def _evaluate_single_description(self, description: str) -> float:
        """Bewertet einzelne Vision-Beschreibung"""
        if not description:
            return 0.0
        
        score = 0.0
        
        # Länge (min 50 Zeichen für Details)
        length_score = min(1.0, len(description) / 200)
        score += length_score * 0.4
        
        # Enthält spezifische Details (Zahlen, Farben, Beschreibungen)
        has_numbers = bool(re.search(r'\d+', description))
        has_colors = bool(re.search(r'(rot|blau|grün|gelb|schwarz|weiß|grau|red|blue|green|yellow|black|white|gray)', description, re.I))
        has_descriptors = bool(re.search(r'(zeigt|darstellt|illustriert|beschreibt|shows|displays|illustrates|depicts)', description, re.I))
        
        detail_score = (0.3 if has_numbers else 0) + (0.2 if has_colors else 0) + (0.3 if has_descriptors else 0)
        score += detail_score * 0.6
        
        return min(1.0, score)
    
    def _evaluate_chart_accuracy(self, descriptions: List[str]) -> float:
        """Bewertet ob Zahlen/Daten korrekt extrahiert wurden"""
        if not descriptions:
            return 0.0
        
        total_score = 0.0
        chart_count = 0
        
        for desc in descriptions:
            # Prüfe ob es sich um Chart/Diagramm handelt
            is_chart = bool(re.search(
                r'(chart|graph|diagram|statistik|prozent|percent|%|tabelle|table)',
                desc, re.I
            ))
            
            if is_chart:
                chart_count += 1
                
                # Zahlen gefunden?
                numbers = re.findall(r'\d+(?:[.,]\d+)?(?:\s*%)?', desc)
                if len(numbers) >= 2:
                    total_score += 0.8
                elif len(numbers) >= 1:
                    total_score += 0.5
                else:
                    total_score += 0.2
        
        if chart_count == 0:
            return 0.5  # Neutral wenn keine Charts
        
        return total_score / chart_count
    
    def _evaluate_table_extraction(self, text: str, table_count: int) -> float:
        """Bewertet Tabellenextraktion"""
        if table_count == 0:
            return 0.0
        
        # Suche nach Tabellenstrukturen im Text
        # Markdown-Tabellen
        md_tables = len(re.findall(r'\|[^\n]+\|', text))
        
        # Tab-separierte Strukturen
        tab_lines = len(re.findall(r'[^\t\n]+\t[^\t\n]+', text))
        
        # Bewertung
        structure_found = md_tables + tab_lines
        
        if structure_found >= table_count * 3:  # Mehrere Zeilen pro Tabelle
            return 0.9
        elif structure_found >= table_count:
            return 0.7
        elif structure_found > 0:
            return 0.5
        else:
            return 0.3
    
    def _evaluate_coherence(self, text: str, descriptions: List[str]) -> float:
        """Bewertet Text-Bild-Kohärenz"""
        if not text or not descriptions:
            return 0.5
        
        # Einfache Keyword-Überlappung
        text_words = set(re.findall(r'\b\w{4,}\b', text.lower()))
        
        overlap_scores = []
        for desc in descriptions:
            desc_words = set(re.findall(r'\b\w{4,}\b', desc.lower()))
            if desc_words:
                overlap = len(text_words & desc_words) / len(desc_words)
                overlap_scores.append(min(1.0, overlap * 2))
        
        return sum(overlap_scores) / len(overlap_scores) if overlap_scores else 0.5
    
    def _compare_vision_ocr(self, vision_desc: str, ocr_text: str) -> float:
        """Vergleicht Vision-Beschreibung mit OCR-Text"""
        if not vision_desc or not ocr_text:
            return 0.5
        
        # Keyword-Überlappung
        vision_words = set(re.findall(r'\b\w{3,}\b', vision_desc.lower()))
        ocr_words = set(re.findall(r'\b\w{3,}\b', ocr_text.lower()))
        
        if not ocr_words:
            return 0.5
        
        overlap = len(vision_words & ocr_words) / len(ocr_words)
        return min(1.0, overlap * 1.5)
    
    def _save_metrics(self, metrics: ExtractionMetrics):
        """Speichert Metriken in Datenbank"""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("""
                INSERT OR REPLACE INTO extraction_metrics (
                    source_id, source_type, source_path, timestamp,
                    faithfulness, context_precision, context_recall,
                    multimodal_coherence, image_text_alignment,
                    chart_data_accuracy, table_structure_score, ocr_confidence,
                    overall_quality, text_char_count, image_count,
                    table_count, infographic_count, processing_time_ms,
                    extraction_method, errors, warnings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics.source_id, metrics.source_type, metrics.source_path,
                metrics.timestamp, metrics.faithfulness, metrics.context_precision,
                metrics.context_recall, metrics.multimodal_coherence,
                metrics.image_text_alignment, metrics.chart_data_accuracy,
                metrics.table_structure_score, metrics.ocr_confidence,
                metrics.overall_quality, metrics.text_char_count, metrics.image_count,
                metrics.table_count, metrics.infographic_count, metrics.processing_time_ms,
                metrics.extraction_method, json.dumps(metrics.errors),
                json.dumps(metrics.warnings)
            ))
            conn.commit()
            conn.close()
    
    def get_metrics(self, source_path: str) -> Optional[ExtractionMetrics]:
        """Holt Metriken für eine Quelle"""
        source_id = self._source_id(source_path)
        
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM extraction_metrics WHERE source_id = ?",
            (source_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return ExtractionMetrics(
            source_id=row['source_id'],
            source_type=row['source_type'],
            source_path=row['source_path'],
            timestamp=row['timestamp'],
            faithfulness=row['faithfulness'],
            context_precision=row['context_precision'],
            context_recall=row['context_recall'],
            multimodal_coherence=row['multimodal_coherence'],
            image_text_alignment=row['image_text_alignment'],
            chart_data_accuracy=row['chart_data_accuracy'],
            table_structure_score=row['table_structure_score'],
            ocr_confidence=row['ocr_confidence'],
            overall_quality=row['overall_quality'],
            text_char_count=row['text_char_count'],
            image_count=row['image_count'],
            table_count=row['table_count'],
            infographic_count=row['infographic_count'],
            processing_time_ms=row['processing_time_ms'],
            extraction_method=row['extraction_method'],
            errors=json.loads(row['errors']),
            warnings=json.loads(row['warnings'])
        )
    
    def generate_report(
        self,
        hours: float = 24.0,
        source_type: Optional[str] = None
    ) -> QualityReport:
        """
        Generiert aggregierten Qualitätsbericht.
        
        Args:
            hours: Zeitraum in Stunden
            source_type: Optional Filter ("pdf", "url", "image")
        """
        end_time = time.time()
        start_time = end_time - (hours * 3600)
        
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        
        # Filter bauen
        where_clause = "timestamp >= ? AND timestamp <= ?"
        params: List[Any] = [start_time, end_time]
        
        if source_type:
            where_clause += " AND source_type = ?"
            params.append(source_type)
        
        # Aggregierte Statistiken
        cursor = conn.execute(f"""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN source_type = 'pdf' THEN 1 ELSE 0 END) as pdf_count,
                SUM(CASE WHEN source_type = 'url' THEN 1 ELSE 0 END) as url_count,
                SUM(CASE WHEN source_type = 'image' THEN 1 ELSE 0 END) as image_count,
                AVG(faithfulness) as avg_faithfulness,
                AVG(context_precision) as avg_context_precision,
                AVG(context_recall) as avg_context_recall,
                AVG(multimodal_coherence) as avg_multimodal_coherence,
                AVG(overall_quality) as avg_overall_quality,
                SUM(CASE WHEN overall_quality < 0.5 THEN 1 ELSE 0 END) as low_quality_count
            FROM extraction_metrics
            WHERE {where_clause}
        """, params)
        
        row = cursor.fetchone()
        
        report = QualityReport(
            start_time=start_time,
            end_time=end_time,
            total_extractions=row['total'] or 0,
            pdf_count=row['pdf_count'] or 0,
            url_count=row['url_count'] or 0,
            image_count=row['image_count'] or 0,
            avg_faithfulness=row['avg_faithfulness'] or 0.0,
            avg_context_precision=row['avg_context_precision'] or 0.0,
            avg_context_recall=row['avg_context_recall'] or 0.0,
            avg_multimodal_coherence=row['avg_multimodal_coherence'] or 0.0,
            avg_overall_quality=row['avg_overall_quality'] or 0.0,
            low_quality_count=row['low_quality_count'] or 0
        )
        
        # Trend berechnen (Vergleich mit vorherigem Zeitraum)
        prev_start = start_time - (hours * 3600)
        cursor = conn.execute(f"""
            SELECT AVG(overall_quality) as prev_avg
            FROM extraction_metrics
            WHERE timestamp >= ? AND timestamp < ?
            {"AND source_type = ?" if source_type else ""}
        """, [prev_start, start_time] + ([source_type] if source_type else []))
        
        prev_row = cursor.fetchone()
        prev_avg = prev_row['prev_avg'] or report.avg_overall_quality
        
        if prev_avg > 0:
            change = (report.avg_overall_quality - prev_avg) / prev_avg * 100
            report.trend_change_percent = change
            
            if change > 5:
                report.quality_trend = "improving"
            elif change < -5:
                report.quality_trend = "declining"
            else:
                report.quality_trend = "stable"
        
        # Häufige Fehler
        cursor = conn.execute(f"""
            SELECT errors FROM extraction_metrics
            WHERE {where_clause} AND errors != '[]'
            LIMIT 100
        """, params)
        
        all_errors: List[str] = []
        for err_row in cursor:
            all_errors.extend(json.loads(err_row['errors']))
        
        # Top 5 häufigste Fehler
        from collections import Counter
        error_counts = Counter(all_errors)
        report.common_errors = [err for err, _ in error_counts.most_common(5)]
        report.error_count = len(all_errors)
        
        # Empfehlungen generieren
        report.recommendations = self._generate_recommendations(report)
        
        conn.close()
        return report
    
    def _generate_recommendations(self, report: QualityReport) -> List[str]:
        """Generiert Verbesserungsempfehlungen"""
        recommendations = []
        
        if report.avg_faithfulness < 0.6:
            recommendations.append(
                "⚠️ Niedrige Faithfulness: Prüfen Sie die Extraktions-Prompts"
            )
        
        if report.avg_context_precision < 0.5:
            recommendations.append(
                "📝 Niedrige Precision: Chunk-Größe oder Filterung anpassen"
            )
        
        if report.avg_multimodal_coherence < 0.5:
            recommendations.append(
                "🖼️ Niedrige Kohärenz: Vision-Prompts für Kontextbezug optimieren"
            )
        
        if report.low_quality_count > report.total_extractions * 0.2:
            recommendations.append(
                f"❌ {report.low_quality_count} Extraktionen mit niedriger Qualität - Review empfohlen"
            )
        
        if report.quality_trend == "declining":
            recommendations.append(
                f"📉 Qualität sinkt ({report.trend_change_percent:.1f}%) - Ursachenanalyse empfohlen"
            )
        
        if not recommendations:
            recommendations.append("✅ Alle Metriken im grünen Bereich!")
        
        return recommendations


# Singleton
_evaluator: Optional[ExtractionQualityEvaluator] = None


def get_quality_evaluator() -> ExtractionQualityEvaluator:
    """Gibt Singleton Evaluator zurück"""
    global _evaluator
    if _evaluator is None:
        _evaluator = ExtractionQualityEvaluator()
    return _evaluator
