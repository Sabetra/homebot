#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  RAGAS SOTA Evaluation - Lokale RAG-Pipeline Bewertung                 ║
║  ─────────────────────────────────────────────────────────────────────  ║
║  Evaluiert die RAG-Pipeline mit RAGAS v0.4.3 vollständig lokal:        ║
║  • Lokales LLM (Magistral Small 2509 via llama-cpp)                    ║
║  • Lokale Embeddings (multilingual-e5-large)                           ║
║  • Keine OpenAI/Cloud API nötig                                       ║
║                                                                        ║
║  RAGAS Metriken:                                                       ║
║  1. Faithfulness         - Antworttreue zum Kontext                    ║
║  2. Answer Relevancy     - Relevanz der Antwort zur Frage              ║
║  3. Context Precision    - Präzision der abgerufenen Kontexte          ║
║  4. Context Recall       - Vollständigkeit der abgerufenen Kontexte    ║
║  5. Context Relevance    - Relevanz der Kontextchunks                  ║
║  6. Factual Correctness  - Faktische Korrektheit                      ║
║  7. Answer Correctness   - Gesamtqualität vs. Referenz                 ║
║  8. Noise Sensitivity    - Robustheit gegen irrelevante Kontexte       ║
║                                                                        ║
║  Zusätzlich: Non-LLM Metriken (keine LLM-Calls nötig):                ║
║  • BLEU, ROUGE, StringSimilarity                                       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import sys
import os
import json
import time
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from scripts.model_loader import DEFAULT_MODEL

# ── OFFLINE-MODUS: Kein Download-Versuch bei jedem Start ──
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# Project root
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(PROJECT_ROOT, "ragas_evaluation.log"),
            mode="w", encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("ragas_eval")

# ═══════════════════════════════════════════════════════════════════════
# 1. LOKALER LLM-WRAPPER für RAGAS (via LangChain)
# ═══════════════════════════════════════════════════════════════════════

from langchain_core.language_models.llms import LLM
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from pydantic import Field, PrivateAttr


class LocalLlamaLLM(LLM):
    """LangChain-kompatibler Wrapper um unseren ModelLoader (llama-cpp).
    
    RAGAS braucht einen LangChain-LLM. Dieser Wrapper delegiert an den
    Singleton ModelLoader, der das konfigurierte Standardmodell lädt.
    """
    model_id: str = Field(default=DEFAULT_MODEL)
    temperature: float = Field(default=0.1)
    max_tokens: int = Field(default=2048)
    _model_loader: Any = PrivateAttr(default=None)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from scripts.model_loader import get_model_loader
        self._model_loader = get_model_loader()
        if self._model_loader.llm is None:
            logger.info(f"Lade LLM: {self.model_id}...")
            self._model_loader.load_model_by_config(self.model_id)
            logger.info("LLM geladen ✓")
    
    @property
    def _llm_type(self) -> str:
        return f"local-{self.model_id}"
    
    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> str:
        """Generiert Text mit dem lokalen LLM."""
        messages = [
            {"role": "system", "content": "Du bist ein hilfreicher KI-Assistent. Antworte präzise und sachlich auf Deutsch oder Englisch, je nach Fragesprache. Gib strukturierte, faktenbasierte Antworten."},
            {"role": "user", "content": prompt},
        ]
        try:
            response = self._model_loader.generate_response(
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stop=stop,
            )
            return response if response else ""
        except Exception as e:
            logger.error(f"LLM-Fehler: {e}")
            return f"[LLM Error: {e}]"


class LocalEmbeddings(Embeddings):
    """LangChain-kompatibler Wrapper um unseren EmbeddingSingleton.
    
    Nutzt intfloat/multilingual-e5-large (1024 dim) -- dasselbe Modell
    das auch die RAG-Pipeline für die Indexierung verwendet.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model = None
    
    def _get_model(self):
        if self._model is None:
            try:
                from utils.embedding_singleton import EmbeddingSingleton
                singleton = EmbeddingSingleton()
                if not singleton.load_model():
                    raise RuntimeError("EmbeddingSingleton konnte das Modell nicht laden!")
                self._model = singleton.model
                if self._model is None:
                    raise RuntimeError("EmbeddingSingleton.model ist None nach load_model()!")
                logger.info(f"Embedding-Modell geladen: {singleton.model_name} ✓")
            except Exception as e:
                logger.error(f"Fehler beim Laden des Embedding-Modells: {e}")
                try:
                    from sentence_transformers import SentenceTransformer
                    # Dual-GPU: Fallback-Embeddings auf der AUX-GPU (RTX 3060 Ti)
                    try:
                        from utils.gpu_devices import get_placement
                        _fb_dev = get_placement().aux_device_string
                    except Exception:
                        _fb_dev = "cuda"
                    self._model = SentenceTransformer(
                        "intfloat/multilingual-e5-large",
                        device=_fb_dev
                    )
                    logger.info("Embedding-Modell (Fallback) geladen ✓")
                except Exception as fallback_e:
                    logger.error(f"Fallback-Embedding-Modell konnte nicht geladen werden: {fallback_e}")
                    raise RuntimeError(f"Kein Embedding-Modell konnte geladen werden: {e}, {fallback_e}")
        if self._model is None:
            raise RuntimeError("_get_model() gibt None zurück! Modell konnte nicht geladen werden.")
        return self._model
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        model = self._get_model()
        # E5-Modelle verwenden "query: " / "passage: " prefix
        prefixed = [f"passage: {t}" for t in texts]
        embeddings = model.encode(
            prefixed, normalize_embeddings=True,
            batch_size=32, show_progress_bar=False,
            convert_to_numpy=True
        )
        return embeddings.tolist()
    
    def embed_query(self, text: str) -> List[float]:
        model = self._get_model()
        prefixed = f"query: {text}"
        embedding = model.encode(
            [prefixed], normalize_embeddings=True,
            convert_to_numpy=True
        )
        return embedding[0].tolist()


# ═══════════════════════════════════════════════════════════════════════
# 2. TEST-DATENSATZ (Deutsche + Englische Fragen mit Ground Truth)
# ═══════════════════════════════════════════════════════════════════════

def create_evaluation_dataset() -> List[Dict[str, Any]]:
    """Erstellt einen Evaluierungsdatensatz mit diversen Fragen.
    
    Der Datensatz testet verschiedene RAG-Szenarien:
    - Faktenfragen (direkte Informationsextraktion)
    - Verständnisfragen (Zusammenfassung/Synthese)
    - Domänenspezifische Fragen (Psychologie, Stress, Management)
    - Tabellenfragen (strukturierte Datenextraktion)
    - Vergleichsfragen (mehrere Quellen nötig)
    """
    
    # Diese Fragen basieren auf den Inhalten der RAG-Datenbank
    # (PDF Sammlung: Psychologie, Stress, Management, SBB Geschäftsbericht etc.)
    dataset = [
        {
            "question": "Was ist Stress laut der WHO und welche Gesundheitsgefahren gehen davon aus?",
            "reference": "Stress ist laut WHO die größte Gesundheitsgefährdung im 21. Jahrhundert. Die durch Stress verursachten Krankheitskosten erreichen bereits jährlich die Milliarden-Euro-Grenze.",
        },
        {
            "question": "Was versteht man unter Flow-Erleben und welche Voraussetzungen müssen dafür erfüllt sein?",
            "reference": "Flow-Erleben entsteht, wenn man der Aktivität gewachsen ist, sich konzentrieren kann, deutliche Ziele verfolgt, unmittelbare Rückmeldung erhält, Kontrolle über die Aktivität hat und Sorgen um das Selbst verschwinden.",
        },
        {
            "question": "Welche Strategien gibt es zur Stressbewältigung im beruflichen Kontext?",
            "reference": "Strategien zur Stressbewältigung umfassen u.a. Achtsamkeitstraining, kognitive Umstrukturierung, Zeitmanagement, Work-Life-Balance, Resilienztraining und soziale Unterstützung.",
        },
        {
            "question": "Was sind die Kernkomponenten von Resilienz?",
            "reference": "Resilienz umfasst die Fähigkeit, Krisen zu bewältigen, sich anzupassen und gestärkt daraus hervorzugehen. Kernkomponenten sind Selbstregulation, Optimismus, Selbstwirksamkeit und soziale Netzwerke.",
        },
        {
            "question": "Wie unterscheiden sich Eustress und Distress?",
            "reference": "Eustress ist positiver Stress, der motivierend wirkt und die Leistungsfähigkeit steigert. Distress ist negativer Stress, der die Gesundheit beeinträchtigt und zu Erschöpfung führen kann.",
        },
        {
            "question": "Was ist das Burnout-Syndrom und welche Symptome sind typisch?",
            "reference": "Burnout ist ein Zustand emotionaler, körperlicher und geistiger Erschöpfung durch übermäßigen und langfristigen Stress. Typische Symptome sind Erschöpfung, Zynismus, reduzierte Leistungsfähigkeit und Depersonalisation.",
        },
        {
            "question": "Welche Rolle spielt die Selbstwirksamkeit bei der Stressbewältigung?",
            "reference": "Selbstwirksamkeit ist die Überzeugung, Anforderungen aus eigener Kraft bewältigen zu können. Hohe Selbstwirksamkeit fördert aktive Coping-Strategien und schützt vor Stress.",
        },
        {
            "question": "Was sind kognitive Verzerrungen und wie wirken sie sich auf Stress aus?",
            "reference": "Kognitive Verzerrungen sind systematische Denkfehler wie Katastrophisieren, Schwarz-Weiß-Denken oder Übergeneralisierung. Sie verstärken subjektives Stresserleben.",
        },
        {
            "question": "Welche Bedeutung hat Achtsamkeit für die psychische Gesundheit?",
            "reference": "Achtsamkeit ist die bewusste, nicht-wertende Wahrnehmung des gegenwärtigen Moments. Sie reduziert Stress, verbessert die emotionale Regulation und fördert das Wohlbefinden.",
        },
        {
            "question": "Wie funktioniert das Transaktionale Stressmodell nach Lazarus?",
            "reference": "Das Transaktionale Stressmodell nach Lazarus beschreibt Stress als Ergebnis der kognitiven Bewertung einer Situation. Es unterscheidet primäre Bewertung (Bedrohung/Herausforderung) und sekundäre Bewertung (verfügbare Ressourcen). Coping kann problem- oder emotionsfokussiert sein.",
        },
        {
            "question": "Was sind die physiologischen Stressreaktionen im Körper?",
            "reference": "Bei Stress werden die Stresshormone Cortisol und Adrenalin ausgeschüttet. Das sympathische Nervensystem wird aktiviert: Herzfrequenz steigt, Blutdruck erhöht sich, Muskeln spannen sich an. Die HPA-Achse (Hypothalamus-Hypophyse-Nebennierenrinde) reguliert die langfristige Stressreaktion.",
        },
        {
            "question": "Was versteht man unter psychologischer Sicherheit in Teams?",
            "reference": "Psychologische Sicherheit beschreibt die Überzeugung, dass man in einem Team Risiken eingehen kann, ohne negative Konsequenzen befürchten zu müssen. Sie fördert Kreativität, Lernbereitschaft und offene Kommunikation.",
        },
        {
            "question": "Welche Faktoren beeinflussen die Arbeitsmotivation?",
            "reference": "Arbeitsmotivation wird beeinflusst durch Autonomie, Kompetenzerleben, soziale Eingebundenheit (Selbstbestimmungstheorie), Anerkennung, Zielsetzung, Feedback und die Passung zwischen Anforderungen und Fähigkeiten.",
        },
        {
            "question": "Wie wirkt sich chronischer Stress auf das Immunsystem aus?",
            "reference": "Chronischer Stress schwächt das Immunsystem durch dauerhaft erhöhte Cortisolspiegel. Dies führt zu erhöhter Anfälligkeit für Infektionen, verzögerter Wundheilung und chronischen Entzündungsprozessen.",
        },
        {
            "question": "Was sind evidenzbasierte Interventionen gegen Angststörungen?",
            "reference": "Evidenzbasierte Interventionen gegen Angststörungen umfassen kognitive Verhaltenstherapie (KVT), Expositionstherapie, EMDR, Achtsamkeitsbasierte Stressreduktion (MBSR) und in schweren Fällen Pharmakotherapie mit SSRIs oder SNRIs.",
        },
    ]
    
    return dataset


# ═══════════════════════════════════════════════════════════════════════
# 3. RAG-PIPELINE AUSFÜHRUNG (Retrieval + Generation)
# ═══════════════════════════════════════════════════════════════════════

def run_rag_pipeline(
    questions: List[str],
    rag_store,
    model_loader,
    k: int = 5,
) -> Tuple[List[List[str]], List[str]]:
    """Führt die RAG-Pipeline für alle Fragen aus.
    
    Returns:
        (retrieved_contexts_per_question, generated_answers)
    """
    all_contexts = []
    all_answers = []
    
    for i, question in enumerate(questions):
        logger.info(f"[{i+1}/{len(questions)}] Verarbeite: {question[:80]}...")
        t0 = time.time()
        
        # ── Retrieval ──
        try:
            results = rag_store.search(
                query=question,
                k=k,
                min_score=0.0,
                adaptive_confidence=True,
            )
            contexts = [r["text"] for r in results if r.get("text")]
            scores = [r.get("score", 0.0) for r in results]
            logger.info(
                f"  Retrieval: {len(contexts)} Chunks, "
                f"Scores: {[f'{s:.3f}' for s in scores[:5]]}"
            )
        except Exception as e:
            logger.error(f"  Retrieval-Fehler: {e}")
            contexts = []
            scores = []
        
        all_contexts.append(contexts)
        
        # ── Generation ──
        try:
            context_text = "\n\n---\n\n".join(contexts[:k]) if contexts else "(Kein Kontext gefunden)"
            
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Du bist ein wissenschaftlicher Assistent. "
                        "Beantworte die Frage NUR basierend auf dem gegebenen Kontext. "
                        "Wenn der Kontext die Frage nicht beantwortet, sage das ehrlich. "
                        "Antworte auf Deutsch, präzise und faktenbasiert."
                    ),
                },
                {
                    "role": "user",
                    "content": f"### Kontext:\n{context_text}\n\n### Frage:\n{question}\n\n### Antwort:",
                },
            ]
            
            answer = model_loader.generate_response(
                messages=messages,
                max_tokens=1024,
                temperature=0.1,
            )
            answer = answer.strip() if answer else "(Keine Antwort generiert)"
            logger.info(f"  Generation: {len(answer)} Zeichen, {time.time()-t0:.1f}s")
        except Exception as e:
            logger.error(f"  Generation-Fehler: {e}")
            answer = f"(Fehler: {e})"
        
        all_answers.append(answer)
    
    return all_contexts, all_answers


# ═══════════════════════════════════════════════════════════════════════
# 4. RAGAS EVALUATION
# ═══════════════════════════════════════════════════════════════════════

def run_ragas_evaluation(
    questions: List[str],
    answers: List[str],
    contexts: List[List[str]],
    references: List[str],
    llm_wrapper,
    embedding_wrapper,
) -> Dict[str, Any]:
    """Führt die vollständige RAGAS-Evaluation durch.
    
    Verwendet sowohl LLM-basierte als auch nicht-LLM Metriken.
    """
    from ragas import evaluate
    from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
        factual_correctness,
        answer_correctness,
        noise_sensitivity,
        rouge_score,
        bleu_score,
        non_llm_string_similarity,
        semantic_similarity,
    )
    
    # ── Samples erstellen ──
    samples = []
    for q, a, ctx, ref in zip(questions, answers, contexts, references):
        sample = SingleTurnSample(
            user_input=q,
            response=a,
            retrieved_contexts=ctx if ctx else ["(kein Kontext)"],
            reference=ref,
        )
        samples.append(sample)
    
    eval_dataset = EvaluationDataset(samples=samples)
    logger.info(f"Evaluation-Dataset: {len(samples)} Samples erstellt")
    
    # ── RAGAS Wrappers ──
    ragas_llm = LangchainLLMWrapper(llm_wrapper)
    ragas_emb = LangchainEmbeddingsWrapper(embedding_wrapper)
    
    # ── Metriken definieren ──
    # LLM-basierte Metriken (SOTA)
    faithfulness_metric = faithfulness(llm=ragas_llm)
    answer_relevancy_metric = answer_relevancy(llm=ragas_llm, embeddings=ragas_emb)
    context_precision_metric = context_precision(llm=ragas_llm)
    context_recall_metric = context_recall(llm=ragas_llm)
    factual_correctness_metric = factual_correctness(llm=ragas_llm)
    answer_correctness_metric = answer_correctness(llm=ragas_llm)
    noise_sensitivity_metric = noise_sensitivity(llm=ragas_llm)

    # Non-LLM Metriken (schnell, deterministisch)
    rouge_score_metric = rouge_score()
    bleu_score_metric = bleu_score()
    non_llm_string_similarity_metric = non_llm_string_similarity()
    semantic_similarity_metric = semantic_similarity(embeddings=ragas_emb)

    all_metrics = [
        faithfulness_metric,
        answer_relevancy_metric,
        context_precision_metric,
        context_recall_metric,
        factual_correctness_metric,
        answer_correctness_metric,
        noise_sensitivity_metric,
        rouge_score_metric,
        bleu_score_metric,
        non_llm_string_similarity_metric,
        semantic_similarity_metric,
    ]
    
    logger.info(f"Starte RAGAS Evaluation...")
    
    # ── Run-Config für lokales LLM (längere Timeouts) ──
    from ragas.run_config import RunConfig
    
    results_combined = {}
    interim_path = os.path.join("ragas_results", "interim_nonllm_results.json")
    
    # ── PHASE 1: Non-LLM Metriken (schnell, deterministisch) ──
    logger.info("=" * 60)
    logger.info("PHASE 1: Non-LLM Metriken (ROUGE, BLEU, StringSim, SemanticSim)")
    logger.info("=" * 60)
    non_llm_metrics = [rouge_score_metric, bleu_score_metric, non_llm_string_similarity_metric, semantic_similarity_metric]
    t0 = time.time()
    try:
        run_config_fast = RunConfig(timeout=300, max_retries=2, max_workers=4)
        result_nonllm = evaluate(
            dataset=eval_dataset,
            metrics=non_llm_metrics,
            embeddings=ragas_emb,
            show_progress=True,
            raise_exceptions=False,
            run_config=run_config_fast,
        )
        d1 = time.time() - t0
        logger.info(f"Phase 1 (Non-LLM) abgeschlossen in {d1:.1f}s")
        # SOTA: Use public API for summary metrics
        import numpy as np
        nonllm_scores = getattr(result_nonllm, "scores", None)
        if isinstance(nonllm_scores, list) and nonllm_scores:
            metric_names = nonllm_scores[0].keys()
            for k in metric_names:
                values = [d[k] for d in nonllm_scores if isinstance(d, dict) and k in d]
                mean_val = float(np.nanmean(values)) if values else float('nan')
                results_combined[k] = mean_val
                logger.info(f"  {k}: {mean_val:.4f}")
    except Exception as e:
        logger.error(f"Phase 1 Fehler: {e}", exc_info=True)
        d1 = time.time() - t0
    
    # ── Zwischenergebnis speichern ──
    try:
        with open(interim_path, "w", encoding="utf-8") as f:
            json.dump(results_combined, f, indent=2, ensure_ascii=False)
        logger.info(f"Zwischenergebnis gespeichert: {interim_path}")
    except Exception:
        pass
    
    # ── PHASE 2: LLM-basierte Metriken (einzeln, mit Fehlertoleranz) ──
    logger.info("=" * 60)
    logger.info("PHASE 2: LLM-basierte Metriken (einzeln, lokales LLM)")
    logger.info("=" * 60)
    
    llm_metrics_list = [
        ("faithfulness", faithfulness_metric),
        ("answer_relevancy", answer_relevancy_metric),
        ("context_precision", context_precision_metric),
        ("context_recall", context_recall_metric),
        ("factual_correctness", factual_correctness_metric),
        ("answer_correctness", answer_correctness_metric),
        ("noise_sensitivity", noise_sensitivity_metric),
    ]
    
    run_config_llm = RunConfig(timeout=1800, max_retries=2, max_workers=1)
    t_llm_start = time.time()
    
    for metric_name, metric in llm_metrics_list:
        logger.info(f"\n  → Metrik: {metric_name}...")
        t_metric = time.time()
        try:
            result_single = evaluate(
                dataset=eval_dataset,
                metrics=[metric],
                llm=ragas_llm,
                embeddings=ragas_emb,
                show_progress=True,
                raise_exceptions=False,
                batch_size=1,
                run_config=run_config_llm,
            )
            d_metric = time.time() - t_metric
            # SOTA: Use public API for summary metrics
            import numpy as np
            single_scores = getattr(result_single, "scores", None)
            if isinstance(single_scores, list) and single_scores:
                metric_names = single_scores[0].keys()
                for k in metric_names:
                    values = [d[k] for d in single_scores if isinstance(d, dict) and k in d]
                    mean_val = float(np.nanmean(values)) if values else float('nan')
                    results_combined[k] = mean_val
                    logger.info(f"    {k}: {mean_val:.4f} ({d_metric:.1f}s)")
        except Exception as e:
            logger.error(f"    {metric_name} FEHLGESCHLAGEN: {e}")
            results_combined[metric_name] = -1.0
        # Inkrementell speichern
        try:
            with open(interim_path, "w", encoding="utf-8") as f:
                json.dump(results_combined, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    total_duration = time.time() - t0
    logger.info(f"\nGesamt-Evaluation abgeschlossen in {total_duration:.1f}s")
    return {
        "scores": results_combined,
        "duration_seconds": total_duration,
        "num_samples": len(samples),
    }
    
    # ── Per-Sample Scores extrahieren ──
    result_obj = eval_result.get("result_object")
    per_sample_df = None
    if result_obj is not None and hasattr(result_obj, "to_pandas"):
        try:
            per_sample_df = result_obj.to_pandas()
        except Exception:
            pass
    
    # ── SOTA Bewertung ──
    sota_status = {}
    for metric_name, score in scores.items():
        if metric_name in SOTA_BENCHMARKS and isinstance(score, (int, float)):
            bench = SOTA_BENCHMARKS[metric_name]
            if score >= bench["sota"]:
                status = "🏆 SOTA"
            elif score >= bench["good"]:
                status = "✅ GUT"
            elif score >= bench["acceptable"]:
                status = "⚠️ AKZEPTABEL"
            else:
                status = "❌ VERBESSERUNG NÖTIG"
            sota_status[metric_name] = {
                "score": score,
                "status": status,
                "sota_threshold": bench["sota"],
                "gap_to_sota": max(0, bench["sota"] - score),
            }
    
    # ── Bericht erstellen ──
    report_lines = [
        "=" * 78,
        "  RAGAS SOTA EVALUATION REPORT",
        f"  Datum: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Samples: {num_samples} | Dauer: {duration:.1f}s",
        "=" * 78,
        "",
        "┌─────────────────────────┬────────┬────────┬──────────┬──────────────────────┐",
        "│ Metrik                  │ Score  │ SOTA   │ Gap      │ Status               │",
        "├─────────────────────────┼────────┼────────┼──────────┼──────────────────────┤",
    ]
    
    sota_count = 0
    total_metrics = 0
    
    for metric_name in SOTA_BENCHMARKS:
        if metric_name in sota_status:
            info = sota_status[metric_name]
            total_metrics += 1
            if "SOTA" in info["status"]:
                sota_count += 1
            gap_str = f"{info['gap_to_sota']:.3f}" if info['gap_to_sota'] > 0 else "  --  "
            report_lines.append(
                f"│ {metric_name:<23} │ {info['score']:.4f} │ {info['sota_threshold']:.2f}   │ {gap_str:>8} │ {info['status']:<20} │"
            )
    
    report_lines.extend([
        "└─────────────────────────┴────────┴────────┴──────────┴──────────────────────┘",
        "",
    ])
    
    # ── Gesamt-SOTA-Status ──
    if total_metrics > 0:
        pct = (sota_count / total_metrics) * 100
        if pct >= 80:
            overall = "🏆 SYSTEM IST SOTA"
        elif pct >= 60:
            overall = "✅ SYSTEM IST ÜBERWIEGEND SOTA"
        elif pct >= 40:
            overall = "⚠️ TEILWEISE SOTA -- OPTIMIERUNGEN MÖGLICH"
        else:
            overall = "❌ ERHEBLICHE VERBESSERUNGEN NÖTIG"
        
        report_lines.extend([
            f"GESAMT: {sota_count}/{total_metrics} Metriken auf SOTA-Niveau ({pct:.0f}%)",
            f"STATUS: {overall}",
            "",
        ])
    
    # ── Empfehlungen ──
    report_lines.append("─" * 78)
    report_lines.append("DETAILANALYSE & EMPFEHLUNGEN:")
    report_lines.append("─" * 78)
    
    weak_metrics = [
        (name, info) for name, info in sota_status.items()
        if info["gap_to_sota"] > 0.05
    ]
    weak_metrics.sort(key=lambda x: x[1]["gap_to_sota"], reverse=True)
    
    if not weak_metrics:
        report_lines.append("  Alle Metriken auf oder über SOTA-Niveau! 🎉")
    else:
        for name, info in weak_metrics:
            report_lines.append(f"\n  📊 {name} ({info['score']:.4f} → Ziel: {info['sota_threshold']:.2f}):")
            
            if name == "faithfulness":
                report_lines.extend([
                    "    → Halluzination reduzieren:",
                    "    • Chunk-Größe anpassen (512-1024 Token optimal)",
                    "    • System-Prompt: 'Antworte NUR basierend auf dem Kontext'",
                    "    • Cross-Encoder Score als Confidence-Gate nutzen",
                    "    • Verification-Step nach Generation einbauen",
                ])
            elif name in ("context_precision", "context_relevance"):
                report_lines.extend([
                    "    → Retrieval-Qualität verbessern:",
                    "    • HyDE-Qualität prüfen (Hypothetical Document Embeddings)",
                    "    • Cross-Encoder Reranking-Threshold anpassen (aktuell: 0.3)",
                    "    • BM25-Gewichtung im RRF-Fusion tunen",
                    "    • k-Wert reduzieren (weniger, bessere Chunks)",
                ])
            elif name == "context_recall":
                report_lines.extend([
                    "    → Vollständigkeit der Retrieval verbessern:",
                    "    • k erhöhen (mehr Chunks abrufen, dann filtern)",
                    "    • Multi-Query Expansion einsetzen",
                    "    • Knowledge Graph breiter abfragen",
                    "    • Chunk-Overlap erhöhen für bessere Abdeckung",
                ])
            elif name in ("answer_relevancy", "answer_correctness"):
                report_lines.extend([
                    "    → Antwortqualität verbessern:",
                    "    • System-Prompt optimieren (mehr Struktur)",
                    "    • Temperature senken (0.1-0.2)",
                    "    • Chain-of-Thought prompting für komplexe Fragen",
                    "    • Max-Tokens anpassen für vollständigere Antworten",
                ])
            elif name == "factual_correctness":
                report_lines.extend([
                    "    → Faktentreue verbessern:",
                    "    • Verification-Pipeline aktivieren (post-generation check)",
                    "    • Extractive QA als Fallback für einfache Fragen",
                    "    • Source-Attribution im Response erzwingen",
                ])
    
    # ── Pipeline-Architektur Assessment ──
    report_lines.extend([
        "",
        "─" * 78,
        "RAG-PIPELINE ARCHITEKTUR ASSESSMENT:",
        "─" * 78,
        "",
        "Implementierte SOTA-Komponenten:",
        "  ✅ Hybrid Search (Dense FAISS + Sparse BM25)",
        "  ✅ Reciprocal Rank Fusion (Cormack+, 2009)",
        "  ✅ Cross-Encoder Reranking (BGE-Reranker-v2-m3, 568M)",
        "  ✅ HyDE - Hypothetical Document Embeddings (Gao+, 2023)",
        "  ✅ CRAG - Corrective RAG Filtering (Yan+, 2024)",
        "  ✅ Extractive Compression (CE-basiert)",
        "  ✅ Knowledge Graph Integration (Triple Store + FTS5)",
        "  ✅ Multilingual Embeddings (E5-Large, 1024 dim)",
        "  ✅ Dual-Index FAISS (Recent + Full mit HNSW)",
        "  ✅ Adaptive Confidence Thresholds",
        "  ✅ GPU-beschleunigte Embedding (FP16, ~1.1 GB VRAM)",
        "  ✅ ONNX-beschleunigter Cross-Encoder (~3x CPU-Speedup)",
        "",
        "Mögliche Ergänzungen für Spitzenwerte:",
        "  🔲 Late Interaction Models (ColBERT v2)",
        "  🔲 Query Decomposition (komplexe Multi-Hop Fragen)",
        "  🔲 Self-Reflection/Self-RAG (Asai+, 2024)",
        "  🔲 Learned Sparse Retrieval (SPLADE v2)",
        "  🔲 Document-Level Reranking (vor Chunking)",
        "  🔲 Adaptive Retrieval (Query-abhängig k wählen)",
        "",
    ])
    
    # ── Per-Sample Details ──
    if per_sample_df is not None:
        report_lines.extend([
            "─" * 78,
            "PER-SAMPLE ERGEBNISSE:",
            "─" * 78,
        ])
        try:
            report_lines.append(per_sample_df.to_string(max_rows=30))
        except Exception:
            report_lines.append("  (DataFrame-Ausgabe fehlgeschlagen)")
    
    report = "\n".join(report_lines)
    
    # ── Speichern ──
    os.makedirs(output_dir, exist_ok=True)
    
    report_path = os.path.join(output_dir, "ragas_evaluation_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    # JSON für programmatischen Zugriff
    json_path = os.path.join(output_dir, "ragas_evaluation_results.json")
    json_data = {
        "timestamp": datetime.now().isoformat(),
        "num_samples": num_samples,
        "duration_seconds": duration,
        "scores": {k: v for k, v in scores.items() if isinstance(v, (int, float))},
        "sota_status": {
            k: {sk: sv for sk, sv in v.items() if sk != "status"}
            for k, v in sota_status.items()
        },
        "sota_summary": {
            "sota_count": sota_count,
            "total_metrics": total_metrics,
            "sota_percentage": (sota_count / total_metrics * 100) if total_metrics > 0 else 0,
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    # Per-Sample CSV
    if per_sample_df is not None:
        csv_path = os.path.join(output_dir, "ragas_per_sample_results.csv")
        try:
            per_sample_df.to_csv(csv_path, index=False, encoding="utf-8")
            logger.info(f"Per-Sample CSV: {csv_path}")
        except Exception:
            pass
    
    logger.info(f"Report: {report_path}")
    logger.info(f"JSON:   {json_path}")
    
    return report


def analyze_and_report(eval_result: Dict[str, Any], output_dir: str) -> str:
    """Erstellt einen kompakten Bericht aus den aggregierten Evaluationswerten."""
    scores = eval_result.get("scores", {}) if isinstance(eval_result, dict) else {}
    duration = float(eval_result.get("duration_seconds", 0.0)) if isinstance(eval_result, dict) else 0.0
    num_samples = int(eval_result.get("num_samples", 0)) if isinstance(eval_result, dict) else 0

    lines = [
        "=" * 78,
        "  RAGAS EVALUATION REPORT (Kompakt)",
        f"  Datum: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Samples: {num_samples} | Dauer: {duration:.1f}s",
        "=" * 78,
        "",
    ]

    for key in sorted(scores.keys()):
        val = scores.get(key)
        if isinstance(val, (int, float)):
            lines.append(f"- {key}: {val:.4f}")

    report = "\n".join(lines)
    os.makedirs(output_dir, exist_ok=True)

    report_path = os.path.join(output_dir, "ragas_evaluation_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    json_path = os.path.join(output_dir, "ragas_evaluation_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "num_samples": num_samples,
                "duration_seconds": duration,
                "scores": {k: v for k, v in scores.items() if isinstance(v, (int, float))},
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    return report


# ═══════════════════════════════════════════════════════════════════════
# 6. HAUPTPROGRAMM
# ═══════════════════════════════════════════════════════════════════════

def main():
    """Hauptfunktion: Führt vollständige RAGAS SOTA Evaluation durch."""
    
    print("=" * 78)
    print("  🔬 RAGAS SOTA EVALUATION -- Lokale RAG-Pipeline")
    print("  Vollständig lokal, keine Cloud-APIs nötig")
    print("=" * 78)
    print()
    
    output_dir = os.path.join(PROJECT_ROOT, "ragas_results")
    
    # ── Check ob Pipeline-Outputs bereits existieren (Resume-Modus) ──
    intermediate_path = os.path.join(output_dir, "pipeline_outputs.json")
    resume_mode = os.path.exists(intermediate_path) and "--fresh" not in sys.argv
    
    if resume_mode:
        print("[RESUME] Pipeline-Outputs gefunden, überspringe Retrieval+Generation...")
        with open(intermediate_path, "r", encoding="utf-8") as f:
            intermediate = json.load(f)
        
        questions = [s["question"] for s in intermediate["samples"]]
        answers = [s["answer"] for s in intermediate["samples"]]
        contexts = [s["contexts"] for s in intermediate["samples"]]
        references = [s["reference"] for s in intermediate["samples"]]
        
        print(f"  → {len(questions)} Samples geladen aus vorherigem Run")
        print(f"  → Pipeline-Dauer war: {intermediate.get('pipeline_duration_seconds', '?'):.1f}s\n")
        
        # LLM trotzdem laden für RAGAS LLM-Metriken
        print("[RESUME] Lade LLM für RAGAS-Metriken...")
        from scripts.model_loader import get_model_loader
        model_loader = get_model_loader()
        if model_loader.llm is None:
            model_loader.load_model_by_config(DEFAULT_MODEL)
        print(f"  → LLM geladen: {model_loader.current_model_id}\n")
        
    else:
    
    # ── 1. Test-Datensatz laden ──
        print("[1/6] Erstelle Evaluierungsdatensatz...")
        dataset = create_evaluation_dataset()
        questions = [d["question"] for d in dataset]
        references = [d["reference"] for d in dataset]
        print(f"  → {len(dataset)} Fragen vorbereitet\n")
    
        # ── 2. RAG Store initialisieren ──
        print("[2/6] Initialisiere RAG Store...")
        try:
            from agent.unified_rag_store import UnifiedRagStore
            db_path = os.path.join(PROJECT_ROOT, "rag_store.db")
            rag_store = UnifiedRagStore(db_path=db_path)
            
            # Schnelltest
            test_results = rag_store.search("Stress", k=2)
            print(f"  → RAG Store OK: {len(test_results)} Ergebnisse für Testquery")
            try:
                db_manager = getattr(rag_store, "_db_manager", None)
                execute_query = getattr(db_manager, "execute_query", None)
                if callable(execute_query):
                    chunk_count_raw = execute_query("SELECT COUNT(*) FROM chunks")
                    chunk_count = 0
                    if isinstance(chunk_count_raw, (list, tuple)) and chunk_count_raw:
                        first_row = chunk_count_raw[0]
                        if isinstance(first_row, (list, tuple)) and first_row:
                            try:
                                chunk_count = int(first_row[0])
                            except (TypeError, ValueError):
                                chunk_count = 0
                    print(f"  → {chunk_count:,} Chunks in der Datenbank\n")
                else:
                    print("  → Chunk-Anzahl konnte nicht ermittelt werden\n")
            except Exception:
                print(f"  → Chunk-Anzahl konnte nicht ermittelt werden\n")
        except Exception as e:
            print(f"  ❌ RAG Store Fehler: {e}")
            print("  Stelle sicher, dass rag_store.db existiert und initialisiert ist.")
            sys.exit(1)
        
        # ── 3. LLM laden ──
        print(f"[3/6] Lade lokales LLM ({DEFAULT_MODEL})...")
        try:
            from scripts.model_loader import get_model_loader
            model_loader = get_model_loader()
            if model_loader.llm is None:
                model_loader.load_model_by_config(DEFAULT_MODEL)
            print(f"  → LLM geladen: {model_loader.current_model_id}\n")
        except Exception as e:
            print(f"  ❌ LLM Fehler: {e}")
            sys.exit(1)
        
        # ── 4. RAG-Pipeline für alle Fragen ausführen ──
        print("[4/6] Führe RAG-Pipeline aus (Retrieval + Generation)...")
        print(f"  → {len(questions)} Fragen mit k=5 Chunks\n")
        
        t_pipeline = time.time()
        contexts, answers = run_rag_pipeline(
            questions=questions,
            rag_store=rag_store,
            model_loader=model_loader,
            k=5,
        )
        pipeline_duration = time.time() - t_pipeline
        
        print(f"\n  → Pipeline abgeschlossen in {pipeline_duration:.1f}s")
        print(f"  → Durchschnitt: {pipeline_duration/len(questions):.1f}s pro Frage\n")
        
        # ── Zwischenergebnisse speichern ──
        os.makedirs(output_dir, exist_ok=True)
        intermediate = {
            "timestamp": datetime.now().isoformat(),
            "pipeline_duration_seconds": pipeline_duration,
            "samples": [],
        }
        for q, a, ctx, ref in zip(questions, answers, contexts, references):
            intermediate["samples"].append({
                "question": q,
                "answer": a,
                "contexts": ctx,
                "reference": ref,
                "num_contexts": len(ctx),
            })
        
        with open(intermediate_path, "w", encoding="utf-8") as f:
            json.dump(intermediate, f, indent=2, ensure_ascii=False)
        print(f"  → Pipeline-Outputs gespeichert: {intermediate_path}\n")
    
    # ── 5. RAGAS Evaluation ──
    print("[5/6] Starte RAGAS Evaluation...")
    print("  → LLM-basierte + Non-LLM Metriken")
    print("  → Dies kann einige Minuten dauern (lokales LLM)...\n")
    
    llm_wrapper = LocalLlamaLLM(temperature=0.1, max_tokens=2048)
    emb_wrapper = LocalEmbeddings()
    
    eval_result = run_ragas_evaluation(
        questions=questions,
        answers=answers,
        contexts=contexts,
        references=references,
        llm_wrapper=llm_wrapper,
        embedding_wrapper=emb_wrapper,
    )
    
    # ── 6. Report ──
    print("\n[6/6] Erstelle SOTA-Bericht...")
    report = analyze_and_report(eval_result, output_dir)
    
    print("\n" + report)
    print(f"\nErgebnisse gespeichert in: {output_dir}/")
    print("  • ragas_evaluation_report.txt  (Bericht)")
    print("  • ragas_evaluation_results.json (Maschinenlesbar)")
    print("  • ragas_per_sample_results.csv  (Per-Sample Details)")
    print("  • pipeline_outputs.json         (RAG-Pipeline Outputs)")


if __name__ == "__main__":
    main()
