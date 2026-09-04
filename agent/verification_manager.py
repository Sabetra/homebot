"""
VerificationManager - SOTA Layered Answer Verification & Hallucination Detection.

This module provides comprehensive answer verification capabilities with a
three-layer architecture:

  Layer 1 (Fast): TF-IDF-weighted term overlap for rapid pre-screening
  Layer 2 (Semantic): Embedding cosine similarity for semantic grounding
  Layer 3 (LLM, optional): LLM-based factual verification for STRICT mode

Features:
- Answer quality assessment
- Multi-layer hallucination detection using evidence grounding
- Semantic embedding-based grounding (via EmbeddingSingleton)
- Optional LLM-based factual verification
- Fact validation against retrieved evidence
- Confidence scoring
- Source attribution verification

Author: SOTA refactor 2026 -- Layered verification architecture
"""

import logging
import math
import string
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import Counter

logger = logging.getLogger(__name__)

# ========================================================================
# SOTA Sentence Tokenization (replaces naive split('.'))
# ========================================================================
_nltk_sent_tokenize: Any = None
_SENT_TOKENIZE_AVAILABLE = False

try:
    from nltk.tokenize import sent_tokenize as _nltk_st
    _nltk_sent_tokenize = _nltk_st
    _SENT_TOKENIZE_AVAILABLE = True
except ImportError:
    logger.info("NLTK sent_tokenize unavailable -- using regex fallback")

import re
# Sentence boundary: split on ". " followed by uppercase, but skip common abbreviations.
# Python re lookbehind requires fixed-width, so we use a simpler negative lookbehind
# for the most frequent short abbreviations (all exactly 2-4 chars before the dot)
# and post-filter the rest.
_ABBREV_SET = frozenset([
    'dr', 'prof', 'mr', 'mrs', 'ms', 'nr', 'abs', 'art', 'bd', 'bzw',
    'ca', 'etc', 'evtl', 'ggf', 'inkl', 'max', 'min', 'usw', 'vgl',
])
_ABBREV_MULTI = re.compile(
    r'\b(?:z\.\s*B|d\.\s*h|o\.\s*[Ää]|u\.\s*a|u\.\s*U|s\.\s*o|i\.\s*d\.\s*R)\.\s*$',
    re.UNICODE,
)
_SENTENCE_SPLIT_RE = re.compile(
    r'\.\s+(?=[A-ZÄÖÜÉÈÊ])',
    re.UNICODE,
)

def _split_into_sentences(text: str) -> list[str]:
    """
    SOTA sentence splitting: NLTK German tokenizer with robust regex fallback.
    
    Handles abbreviations (Dr., Prof., z.B., etc.), numbered lists,
    and bullet points correctly -- unlike naive split('.').
    """
    if not text or len(text.strip()) < 10:
        return []
    
    if _SENT_TOKENIZE_AVAILABLE and _nltk_sent_tokenize is not None:
        try:
            sentences = _nltk_sent_tokenize(text, language='german')
            return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]
        except Exception:
            pass  # Fall through to regex
    
    # Regex fallback: split on ". " followed by uppercase, then rejoin false splits
    # caused by abbreviations (Dr. Prof. z. B. etc.)
    raw_parts = _SENTENCE_SPLIT_RE.split(text)
    merged: list[str] = []
    for part in raw_parts:
        stripped = part.strip()
        if not stripped:
            continue
        # Check if previous segment ended with a known abbreviation
        if merged:
            prev = merged[-1]
            # Single-word abbreviation: last word before the split dot
            last_word = prev.rstrip('.').rsplit(None, 1)[-1].lower() if prev else ''
            if last_word in _ABBREV_SET or _ABBREV_MULTI.search(prev):
                # False split -- rejoin with the dot that was consumed by the regex
                merged[-1] = prev + '. ' + stripped
                continue
        merged.append(stripped)
    return [s for s in merged if len(s) > 10]

# Optional: Embedding-based semantic grounding (Layer 2)
_np_module: Any = None
_np_norm_func: Any = None
_get_embedding_model_func: Any = None
EMBEDDINGS_AVAILABLE = False

try:
    from utils.embedding_singleton import get_embedding_model as _gem
    import numpy as _np
    from numpy.linalg import norm as _nlnorm
    _get_embedding_model_func = _gem
    _np_module = _np
    _np_norm_func = _nlnorm
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    logger.info("Embedding-based grounding unavailable -- Layer 2 disabled")


class VerificationLevel(Enum):
    """Verification strictness levels."""
    BASIC = "basic"  # Basic quality checks
    STANDARD = "standard"  # Standard verification with grounding checks
    STRICT = "strict"  # Strict verification with hallucination detection


class VerificationStatus(Enum):
    """
    Declarative outcome of a verify_step.

    Enables the AdaptivePlanner to act on verification results without
    inspecting raw numeric scores — replacing ad-hoc ``if failed`` branches
    with a proper state-machine contract:

        PASSED                → accept answer, stop loop
        INSUFFICIENT_EVIDENCE → trigger NEW_WEB_SEARCH / RAG expansion
        HALLUCINATION_RISK    → trigger RE_SYNTHESIS with stricter grounding
        FAILED                → fallback answer / degrade gracefully
    """
    PASSED = "passed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    HALLUCINATION_RISK = "hallucination_risk"
    FAILED = "failed"


@dataclass
class VerificationResult:
    """Result of answer verification."""
    is_verified: bool
    confidence_score: float  # 0.0 to 1.0
    quality_score: float  # 0.0 to 1.0
    hallucination_risk: float  # 0.0 to 1.0
    grounding_score: float  # How well grounded in evidence
    issues: List[str]  # List of verification issues found
    warnings: List[str]  # Non-critical warnings
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_status(self) -> "VerificationStatus":
        """
        Derive a VerificationStatus from numeric scores.

        Priority order (most actionable condition wins):
          1. HALLUCINATION_RISK  — high hallucination signal
          2. INSUFFICIENT_EVIDENCE — answer not grounded
          3. FAILED              — low quality / general failure
          4. PASSED              — everything OK
        """
        if self.hallucination_risk > 0.55:
            return VerificationStatus.HALLUCINATION_RISK
        if self.grounding_score < 0.30:
            return VerificationStatus.INSUFFICIENT_EVIDENCE
        if not self.is_verified or self.quality_score < 0.40:
            return VerificationStatus.FAILED
        return VerificationStatus.PASSED


# ========================================================================
# Layer 1: TF-IDF Weighted Term Overlap (Fast, deterministic)
# ========================================================================

class TermOverlapGrounder:
    """
    Layer 1: Fast term-overlap grounding with IDF weighting.
    
    Uses inverse-document-frequency weighting so rare, content-bearing terms
    contribute more to the grounding score than stop words.
    """

    # Extended stop words for EN + DE
    STOP_WORDS: frozenset[str] = frozenset({
        # English
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
        'for', 'of', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'shall', 'can', 'that',
        'this', 'these', 'those', 'it', 'its', 'with', 'from', 'by',
        'as', 'not', 'no', 'so', 'if', 'what', 'how', 'why', 'when',
        'where', 'which', 'who', 'whom',
        # German
        'der', 'die', 'das', 'ein', 'eine', 'und', 'oder', 'aber',
        'ist', 'sind', 'war', 'waren', 'hat', 'haben', 'wird', 'werden',
        'kann', 'können', 'mit', 'von', 'für', 'auf', 'aus', 'bei',
        'nach', 'über', 'unter', 'vor', 'hinter', 'neben', 'zwischen',
        'nicht', 'auch', 'nur', 'noch', 'schon', 'sehr', 'mehr',
        'sich', 'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr',
    })

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Tokenize text, strip punctuation, lowercase, remove stop words."""
        return [
            t for t in (
                tok.strip(string.punctuation).lower()
                for tok in text.split()
            )
            if t and t not in TermOverlapGrounder.STOP_WORDS and len(t) > 1
        ]

    @staticmethod
    def _build_idf(documents: List[List[str]]) -> Dict[str, float]:
        """Build IDF dictionary from a corpus of tokenized documents."""
        n_docs = len(documents)
        if n_docs == 0:
            return {}
        df: Counter[str] = Counter()
        for doc_tokens in documents:
            for term in set(doc_tokens):
                df[term] += 1
        return {
            term: math.log((n_docs + 1) / (count + 1)) + 1.0
            for term, count in df.items()
        }

    def score_grounding(
        self,
        answer: str,
        evidence_texts: List[str],
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Compute IDF-weighted term overlap grounding score.
        
        Returns:
            (grounding_score, metadata)
        """
        if not evidence_texts:
            return 0.0, {"method": "term_overlap", "detail": "no_evidence"}

        # Tokenize
        answer_tokens = self._tokenize(answer)
        evidence_token_lists = [self._tokenize(ev) for ev in evidence_texts]
        
        if not answer_tokens:
            return 0.5, {"method": "term_overlap", "detail": "no_answer_tokens"}

        # Build IDF from evidence corpus + answer as "documents"
        all_docs = evidence_token_lists + [answer_tokens]
        idf = self._build_idf(all_docs)

        # SOTA: Proper sentence tokenization (handles Dr., z.B., et al.)
        sentences = _split_into_sentences(answer)
        if not sentences:
            return 0.5, {"method": "term_overlap", "detail": "no_sentences"}

        evidence_term_sets = [set(toks) for toks in evidence_token_lists]
        grounded_weight = 0.0
        total_weight = 0.0

        for sentence in sentences:
            sent_tokens = self._tokenize(sentence)
            if not sent_tokens:
                continue

            # Compute IDF-weighted coverage against best-matching evidence
            sent_idf_total = sum(idf.get(t, 1.0) for t in sent_tokens)
            total_weight += sent_idf_total

            best_coverage = 0.0
            for ev_set in evidence_term_sets:
                covered_idf = sum(idf.get(t, 1.0) for t in sent_tokens if t in ev_set)
                best_coverage = max(best_coverage, covered_idf)

            grounded_weight += best_coverage

        score = grounded_weight / total_weight if total_weight > 0 else 0.0
        return score, {"method": "term_overlap", "sentences": len(sentences)}

    def score_hallucination_risk(
        self,
        answer: str,
        evidence_texts: List[str],
        strong_claim_keywords: List[str],
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Detect hallucination risk via IDF-weighted term overlap.
        
        Returns:
            (risk_score, metadata)
        """
        if not evidence_texts:
            return 0.8, {"method": "term_overlap", "detail": "no_evidence"}

        answer_tokens = self._tokenize(answer)
        evidence_token_lists = [self._tokenize(ev) for ev in evidence_texts]
        all_docs = evidence_token_lists + [answer_tokens]
        idf = self._build_idf(all_docs)

        sentences = _split_into_sentences(answer)
        if not sentences:
            return 0.0, {"method": "term_overlap", "detail": "no_sentences"}

        evidence_term_sets = [set(toks) for toks in evidence_token_lists]
        unsupported = 0
        strong_unsupported = 0
        total = 0

        for sentence in sentences:
            sent_tokens = self._tokenize(sentence)
            if not sent_tokens:
                continue
            total += 1

            is_strong = any(kw in sentence.lower() for kw in strong_claim_keywords)
            threshold = 0.4 if is_strong else 0.25

            sent_idf_total = sum(idf.get(t, 1.0) for t in sent_tokens)
            best_ratio = 0.0
            for ev_set in evidence_term_sets:
                covered = sum(idf.get(t, 1.0) for t in sent_tokens if t in ev_set)
                ratio = covered / sent_idf_total if sent_idf_total > 0 else 0.0
                best_ratio = max(best_ratio, ratio)

            if best_ratio < threshold:
                unsupported += 1
                if is_strong:
                    strong_unsupported += 1

        base_risk = unsupported / total if total > 0 else 0.0
        if strong_unsupported > 0:
            base_risk = min(1.0, base_risk + 0.2 * strong_unsupported)

        return base_risk, {
            "method": "term_overlap",
            "unsupported": unsupported,
            "total": total,
            "strong_unsupported": strong_unsupported,
        }


# ========================================================================
# Layer 2: Embedding Cosine Similarity (Semantic grounding)
# ========================================================================

class SemanticGrounder:
    """
    Layer 2: Embedding-based semantic grounding.
    
    Uses the project's EmbeddingSingleton for cosine similarity between
    answer sentences and evidence chunks.
    """

    def __init__(self, similarity_threshold: float = 0.55) -> None:
        self.similarity_threshold = similarity_threshold
        self._available: Optional[bool] = None

    @property
    def available(self) -> bool:
        """Check if embedding model is usable (lazy)."""
        if self._available is None:
            if not EMBEDDINGS_AVAILABLE or _get_embedding_model_func is None:
                self._available = False
            else:
                try:
                    model = _get_embedding_model_func()
                    self._available = model.is_loaded() or model.load_model()
                except Exception:
                    self._available = False
        return bool(self._available)

    def score_grounding(
        self,
        answer: str,
        evidence_texts: List[str],
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Compute semantic grounding via embedding cosine similarity.
        
        Returns:
            (semantic_grounding_score, metadata)
        """
        if not self.available or not evidence_texts:
            return -1.0, {"method": "semantic", "detail": "unavailable" if not self.available else "no_evidence"}

        sentences = _split_into_sentences(answer)
        if not sentences:
            return 0.5, {"method": "semantic", "detail": "no_sentences"}

        try:
            model = _get_embedding_model_func()

            # Encode everything in one batch for efficiency
            all_texts = sentences + evidence_texts
            all_embeddings = model.encode(all_texts, batch_size=32)

            sent_embs = all_embeddings[:len(sentences)]
            ev_embs = all_embeddings[len(sentences):]

            grounded_count = 0
            max_sims: List[float] = []

            for s_emb in sent_embs:
                s_norm = float(_np_norm_func(s_emb))
                if s_norm == 0:
                    continue
                best_sim = 0.0
                for e_emb in ev_embs:
                    e_norm = float(_np_norm_func(e_emb))
                    if e_norm == 0:
                        continue
                    sim = float(_np_module.dot(s_emb, e_emb) / (s_norm * e_norm))
                    best_sim = max(best_sim, sim)
                max_sims.append(best_sim)
                if best_sim >= self.similarity_threshold:
                    grounded_count += 1

            score = grounded_count / len(sentences) if sentences else 0.0
            avg_sim = sum(max_sims) / len(max_sims) if max_sims else 0.0

            return score, {
                "method": "semantic",
                "grounded_sentences": grounded_count,
                "total_sentences": len(sentences),
                "avg_max_similarity": round(avg_sim, 4),
            }

        except Exception as e:
            logger.warning("Semantic grounding failed: %s", e)
            return -1.0, {"method": "semantic", "error": str(e)}


# ========================================================================
# Layer 2.5: NLI-based Entailment Verification (SOTA: Honovich et al. 2022)
# ========================================================================

class NLIEntailmentChecker:
    """
    Layer 2.5: Natural Language Inference for claim-level hallucination detection.
    
    Uses a pre-trained NLI model (DeBERTa/BART-MNLI) to check whether
    each claim in the answer is ENTAILED by the evidence. This is more
    precise than embedding similarity (Layer 2) because NLI models are
    trained specifically for textual entailment.
    
    Architecture:
        For each sentence in the answer:
          - Pair with each evidence chunk as (premise, hypothesis)
          - NLI model outputs: entailment / neutral / contradiction
          - Sentence is "grounded" if any evidence chunk entails it
    
    References:
        - Honovich et al. (2022): "TRUE: Re-evaluating Factual Consistency"
        - Laban et al. (2022): "SummaC: Re-Visiting NLI-based Models for
          Inconsistency Detection in Summarization"
    """
    
    _instance: Optional['NLIEntailmentChecker'] = None
    _model = None
    _tokenizer = None
    _available: Optional[bool] = None
    
    # NLI model fallback chain (accuracy-first)
    _NLI_MODELS = [
        "cross-encoder/nli-deberta-v3-base",      # Best accuracy, ~400M params
        "cross-encoder/nli-deberta-base",          # Good accuracy, ~300M params  
        "cross-encoder/nli-MiniLM2-L6-H768",      # Fast, ~66M params
    ]
    
    def __new__(cls) -> 'NLIEntailmentChecker':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Lazy-load: ~400-800 MB CrossEncoder is only materialised on the
        # first availability probe or check_entailment() call. This keeps
        # orchestrator startup-VRAM low for sessions that never trigger
        # the verification layer.
        return

    @classmethod
    def _ensure_loaded(cls) -> None:
        """Idempotent lazy-load.

        Calling this method more than once is a no-op once a load has been
        attempted (success *or* failure), so it is safe to call from any
        access path (``.available`` getter, ``check_entailment``).
        """
        if cls._available is not None:
            return
        # Singleton instance is guaranteed by __new__; reuse it to load.
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        cls._instance._load_model()
    
    def _load_model(self) -> None:
        """Load NLI model with fallback chain.

        Device selection is *VRAM-aware*: if a co-resident LLM has already
        consumed most of the GPU, the NLI checker stays on CPU instead of
        forcing an OOM. Operators can hard-pin via env vars:
          - ``NLI_DEVICE=cpu`` / ``NLI_DEVICE=cuda``: explicit override.
          - ``NLI_MIN_FREE_VRAM_GB`` (default 2.0): threshold below which
            CUDA is rejected and CPU is used.
        """
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            NLIEntailmentChecker._available = False
            logger.debug("[NLI] sentence-transformers not installed")
            return

        import os as _os
        import torch as _torch

        device = self._select_device(_torch, _os)

        for model_name in self._NLI_MODELS:
            try:
                import time as _time
                start = _time.time()
                NLIEntailmentChecker._model = CrossEncoder(
                    model_name, max_length=512, device=device
                )
                elapsed = _time.time() - start
                NLIEntailmentChecker._available = True
                logger.info(
                    f"[NLI] Loaded {model_name} on {device} in {elapsed:.1f}s"
                )
                return
            except Exception as e:
                logger.debug(f"[NLI] Failed to load {model_name}: {e}")
                continue

        NLIEntailmentChecker._available = False
        logger.warning("[NLI] All NLI models failed to load")

    @staticmethod
    def _select_device(_torch: Any, _os: Any) -> str:
        """VRAM-aware device selector for the NLI CrossEncoder (AUX GPU)."""
        override = (_os.getenv("NLI_DEVICE") or "").strip().lower()
        if override == "cpu":
            return "cpu"
        if override.startswith("cuda"):
            return override  # "cuda" oder "cuda:N" (explizite Operator-Pin)
        if not _torch.cuda.is_available():
            return "cpu"
        # Dual-GPU: NLI läuft auf der AUX-GPU (RTX 3060 Ti), nicht der LLM-GPU
        try:
            from utils.gpu_devices import get_placement
            aux_idx = get_placement().aux_cuda
        except Exception:
            aux_idx = 0
        try:
            free, total = _torch.cuda.mem_get_info(aux_idx)
        except Exception:
            return "cpu"
        try:
            min_free_gb = float(_os.getenv("NLI_MIN_FREE_VRAM_GB", "2.0"))
        except ValueError:
            min_free_gb = 2.0
        min_free = int(min_free_gb * 1024 ** 3)
        if free < min_free:
            logger.info(
                "[NLI] Free VRAM on cuda:%d %.1f GB < %.1f GB threshold -> CPU",
                aux_idx, free / 1024 ** 3, min_free_gb,
            )
            return "cpu"
        return "cuda" if aux_idx == 0 else f"cuda:{aux_idx}"
    
    @property
    def available(self) -> bool:
        # Triggers the lazy-load on first access so callers see the true
        # post-load state. Once attempted, subsequent accesses are O(1).
        NLIEntailmentChecker._ensure_loaded()
        return bool(NLIEntailmentChecker._available) and NLIEntailmentChecker._model is not None
    
    def check_entailment(
        self,
        answer: str,
        evidence_texts: List[str],
        entailment_threshold: float = 0.5,
    ) -> Tuple[float, float, Dict[str, Any]]:
        """Check claim-level entailment of answer against evidence.
        
        Args:
            answer: Generated answer text
            evidence_texts: List of evidence passages
            entailment_threshold: Min probability for "entailment" label
            
        Returns:
            (grounding_score, hallucination_risk, metadata)
            - grounding_score: fraction of claims entailed by evidence [0,1]
            - hallucination_risk: fraction of claims contradicted [0,1]
        """
        if not self.available or not evidence_texts:
            return -1.0, -1.0, {"method": "nli", "detail": "unavailable" if not self.available else "no_evidence"}
        
        # SOTA: Use NLTK-aware sentence splitter (handles "Dr.", "z.B.", etc.).
        # Previously used naive answer.split('.') which destroyed German abbreviations
        # and produced incorrect entailment scores at sentence boundaries.
        sentences = _split_into_sentences(answer)
        if not sentences:
            return 0.5, 0.5, {"method": "nli", "detail": "no_claims"}
        
        model = NLIEntailmentChecker._model
        # _model is guaranteed non-None here because self.available checks it
        assert model is not None, "NLI model must be loaded when self.available is True"
        
        try:
            entailed_count = 0
            contradicted_count = 0
            neutral_count = 0
            max_entailment_scores: List[float] = []
            
            for sentence in sentences:
                # Build (evidence, claim) pairs for NLI
                # FIX 2026-03-10: Previously used ev[:512] (chars) and evidence_texts[:5].
                # (1) ev[:512] chars was far too aggressive -- DeBERTa max_length=512 TOKENS
                #     (~2000+ chars for German). The tokenizer handles truncation internally.
                #     512 chars lost 50-66% of each evidence chunk (800-1498 chars).
                # (2) evidence_texts[:5] ignored 11 of 16 available chunks, missing
                #     relevant supporting evidence for many claims.
                # Use generous char limit (tokenizer truncates to fit), check all evidence.
                max_evidence_chunks = min(len(evidence_texts), 20)  # Safety cap
                pairs = [(ev[:2000], sentence[:512]) for ev in evidence_texts[:max_evidence_chunks]]
                
                # NLI CrossEncoder: predict returns scores for [contradiction, entailment, neutral]
                # or a single relevance score depending on the model
                scores = model.predict(pairs)
                
                # Handle different score formats
                if hasattr(scores, 'ndim') and scores.ndim == 2:
                    # Multi-class NLI: [contradiction, entailment, neutral] per pair
                    # Get max entailment score across all evidence chunks
                    entailment_scores = scores[:, 1] if scores.shape[1] > 1 else scores[:, 0]
                    contradiction_scores = scores[:, 0] if scores.shape[1] > 2 else None
                    
                    max_entailment = float(max(entailment_scores))
                    max_entailment_scores.append(max_entailment)
                    
                    if max_entailment >= entailment_threshold:
                        entailed_count += 1
                    elif contradiction_scores is not None and float(max(contradiction_scores)) >= entailment_threshold:
                        contradicted_count += 1
                    else:
                        neutral_count += 1
                else:
                    # Single-score model: higher = more entailment
                    max_score = float(max(scores)) if len(scores) > 0 else 0.0
                    max_entailment_scores.append(max_score)
                    
                    if max_score >= entailment_threshold:
                        entailed_count += 1
                    elif max_score <= -entailment_threshold:
                        contradicted_count += 1
                    else:
                        neutral_count += 1
            
            total = len(sentences)
            grounding = entailed_count / total if total > 0 else 0.0
            hallucination = contradicted_count / total if total > 0 else 0.0
            avg_entailment = sum(max_entailment_scores) / len(max_entailment_scores) if max_entailment_scores else 0.0
            
            logger.info(
                f"[NLI] {entailed_count}/{total} entailed, "
                f"{contradicted_count}/{total} contradicted, "
                f"{neutral_count}/{total} neutral "
                f"(avg_entailment={avg_entailment:.3f})"
            )
            
            return grounding, hallucination, {
                "method": "nli",
                "entailed": entailed_count,
                "contradicted": contradicted_count,
                "neutral": neutral_count,
                "total_claims": total,
                "avg_entailment": round(avg_entailment, 4),
            }
            
        except Exception as e:
            logger.warning(f"[NLI] Entailment check failed: {e}")
            return -1.0, -1.0, {"method": "nli", "error": str(e)}


# ========================================================================
# Layer 3: LLM-based Factual Verification (optional, STRICT only)
# ========================================================================

class LLMFactVerifier:
    """
    Layer 3: LLM-based factual verification.
    
    Sends answer + evidence to an LLM to detect unsupported or contradictory claims.
    Only invoked at STRICT verification level.
    """

    def __init__(self, llm_callable: Optional[Callable[..., str]] = None) -> None:
        self.llm = llm_callable

    @property
    def available(self) -> bool:
        return self.llm is not None

    def verify(
        self,
        answer: str,
        evidence_texts: List[str],
        query: str,
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        LLM-based verification.
        
        Returns:
            (grounding_score, hallucination_risk, metadata)
        """
        if not self.available or not self.llm:
            return -1.0, -1.0, {"method": "llm", "detail": "unavailable"}

        # SOTA: Use full evidence -- previous truncation (ev[:500], [:5], [:3000]) caused
        # critical information loss. Now pass all evidence with a generous safety cap.
        combined_evidence = "\n---\n".join(evidence_texts)
        # Safety cap: ~8000 chars to fit within LLM context alongside prompt overhead
        if len(combined_evidence) > 8000:
            combined_evidence = combined_evidence[:8000]

        prompt = f"""<role>Du bist ein strenger Faktenprüfer.</role>

<task>Bewerte, ob die folgende Antwort durch die Evidenz gestützt wird.</task>

<query>{query}</query>

<answer>{answer[:2000]}</answer>

<evidence>
{combined_evidence}
</evidence>

<instructions>
Bewerte auf einer Skala von 0.0 bis 1.0:
1. grounding_score: Wie gut ist die Antwort durch die Evidenz belegt? (1.0 = vollständig belegt)
2. hallucination_risk: Wie wahrscheinlich sind Halluzinationen? (0.0 = keine, 1.0 = sehr hoch)

Antworte NUR mit JSON:
{{"grounding_score": 0.X, "hallucination_risk": 0.X, "reasoning": "kurze Begründung"}}
</instructions>"""

        try:
            import json as json_module
            import re

            response = self.llm(prompt, max_tokens=200)
            
            # Parse JSON from response
            response_clean = response.strip()
            if "```json" in response_clean:
                response_clean = response_clean.split("```json")[1].split("```")[0]
            elif "```" in response_clean:
                response_clean = response_clean.split("```")[1].split("```")[0]

            # Try direct JSON parse, then regex fallback
            try:
                data = json_module.loads(response_clean.strip())
            except json_module.JSONDecodeError:
                json_match = re.search(r'\{[^}]+\}', response_clean, re.DOTALL)
                if json_match:
                    data = json_module.loads(json_match.group())
                else:
                    raise ValueError("No JSON found in LLM response")

            grounding = float(data.get("grounding_score", 0.5))
            hallucination = float(data.get("hallucination_risk", 0.5))
            reasoning = str(data.get("reasoning", ""))

            return (
                max(0.0, min(1.0, grounding)),
                max(0.0, min(1.0, hallucination)),
                {"method": "llm", "reasoning": reasoning},
            )

        except Exception as e:
            logger.warning("LLM fact verification failed: %s", e)
            return -1.0, -1.0, {"method": "llm", "error": str(e)}


class AtomicClaimDecomposer:
    """SOTA: FActScore-style atomic claim decomposition.
    
    Decomposes a generated answer into individual atomic claims
    (single factual assertions) so each can be verified independently.
    
    This catches fine-grained hallucinations that sentence-level
    verification misses -- e.g., when a sentence contains 3 facts
    and only 1 is hallucinated, sentence-level scoring dilutes the signal.
    
    Uses LLM for decomposition with NLTK fallback.
    """
    
    def __init__(self, llm_fn=None):
        """
        Args:
            llm_fn: Callable(prompt, max_tokens) → str for LLM-based decomposition
        """
        self.llm = llm_fn
    
    def decompose(self, answer: str, query: str = "") -> List[str]:
        """Decompose answer into atomic claims.
        
        Args:
            answer: The generated answer text
            query: Original query (for context)
            
        Returns:
            List of atomic claim strings, each containing exactly one factual assertion
        """
        if not answer or len(answer.strip()) < 20:
            return [answer.strip()] if answer.strip() else []
        
        # Try LLM-based decomposition first (higher quality)
        if self.llm:
            try:
                claims = self._llm_decompose(answer, query)
                if claims and len(claims) >= 2:
                    return claims
            except Exception as e:
                logger.debug(f"LLM claim decomposition failed, using heuristic: {e}")
        
        # Fallback: Heuristic sentence-based decomposition
        return self._heuristic_decompose(answer)
    
    def _llm_decompose(self, answer: str, query: str) -> List[str]:
        """Use LLM to decompose into atomic claims."""
        llm_fn = self.llm
        if llm_fn is None:
            raise RuntimeError("AtomicClaimDecomposer requires llm_fn for _llm_decompose")

        query_block = f"\n<query>{query[:500]}</query>\n" if query and query.strip() else "\n"
        prompt = f"""<task>Zerlege die folgende Antwort in atomare Behauptungen.
Jede Behauptung soll GENAU EINE überprüfbare Tatsache enthalten.</task>
{query_block}

<answer>{answer[:2000]}</answer>

<instructions>
- Jede Zeile = eine atomare Behauptung
- Keine Nummerierung, keine Aufzählungszeichen
- Meinungen und Grußformeln ignorieren
- NUR faktische Aussagen extrahieren
</instructions>

Atomare Behauptungen:"""

        response = llm_fn(prompt, max_tokens=1024)
        
        # Parse: one claim per line
        claims = []
        for line in response.strip().split('\n'):
            line = line.strip().lstrip('- •·0123456789.)').strip()
            if line and len(line) > 10:
                claims.append(line)
        
        return claims
    
    def _heuristic_decompose(self, answer: str) -> List[str]:
        """Heuristic decomposition: split by sentences, then by conjunctions."""
        sentences = _split_into_sentences(answer)
        
        claims = []
        # Split compound sentences at conjunctions
        conjunction_pattern = re.compile(
            r'\b(?:und|sowie|außerdem|darüber hinaus|zusätzlich|ferner|wobei)\b',
            re.IGNORECASE
        )
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 15:
                continue
            
            # Try splitting at conjunctions if sentence is long
            if len(sentence) > 100:
                parts = conjunction_pattern.split(sentence)
                for part in parts:
                    part = part.strip().rstrip(',').strip()
                    if len(part) > 15:
                        claims.append(part)
            else:
                claims.append(sentence)
        
        return claims if claims else [answer.strip()]


class VerificationManager:
    """
    SOTA Layered Answer Verification Manager.
    
    Architecture:
      Layer 1 (TermOverlapGrounder): Fast IDF-weighted term overlap
      Layer 2 (SemanticGrounder): Embedding cosine similarity
      Layer 2.5 (NLIEntailmentChecker): NLI-based claim entailment (SOTA)
      Layer 3 (LLMFactVerifier): LLM-based factual verification (STRICT only)
      Layer 4 (AtomicClaimDecomposer): FActScore-style per-claim verification (STRICT only)
    
    The final grounding and hallucination scores are computed by aggregating
    available layers with configurable weights.
    """
    
    _instance: Optional['VerificationManager'] = None
    _initialized: bool = False
    
    # Declare instance attributes for mypy (set in __init__)
    verification_level: VerificationLevel
    term_grounder: 'TermOverlapGrounder'
    semantic_grounder: 'SemanticGrounder'
    nli_checker: 'NLIEntailmentChecker'
    llm_verifier: 'LLMFactVerifier'
    min_quality_score: float
    min_grounding_score: float
    max_hallucination_risk: float
    _grounding_weights: List[float]
    _hallucination_weights: List[float]
    uncertainty_keywords: List[str]
    strong_claim_keywords: List[str]
    
    def __new__(cls, **kwargs: Any) -> 'VerificationManager':
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        llm_callable: Optional[Callable[..., str]] = None,
        semantic_similarity_threshold: float = 0.55,
    ) -> None:
        """Initialize the verification manager with layered architecture."""
        if self._initialized:
            # Allow updating LLM callable on re-init
            if llm_callable is not None and hasattr(self, 'llm_verifier') and self.llm_verifier.llm is None:
                self.llm_verifier = LLMFactVerifier(llm_callable)
            return
        
        self._initialized: bool = True
        self.verification_level = VerificationLevel.STANDARD
        
        # Layer instances
        self.term_grounder = TermOverlapGrounder()
        self.semantic_grounder = SemanticGrounder(similarity_threshold=semantic_similarity_threshold)
        self.nli_checker = NLIEntailmentChecker()
        self.llm_verifier = LLMFactVerifier(llm_callable)
        self.claim_decomposer = AtomicClaimDecomposer(llm_fn=llm_callable)
        
        # Thresholds for verification
        self.min_quality_score: float = 0.6
        self.min_grounding_score: float = 0.5
        self.max_hallucination_risk: float = 0.3
        
        # Layer weights for aggregation: [term_overlap, semantic, nli, llm]
        # RATIONALE (Feb 2026, post-ablation):
        #   - Term overlap: 78% stress-test detection → reliable for factual grounding
        #   - Semantic (cosine sim): 0% detection → measures topical relatedness, NOT factual correctness
        #     (hallucinated claims on the same topic have near-identical embeddings)
        #   - NLI (cross-encoder): 72% detection → trained for textual entailment, best factual signal
        #   - LLM: 25% detection → useful but slow, only at STRICT level
        self._grounding_weights: List[float] = [0.30, 0.10, 0.45, 0.15]
        self._hallucination_weights: List[float] = [0.25, 0.10, 0.50, 0.15]
        
        # Per-layer alert thresholds (if ANY layer exceeds these, override aggregated result)
        # This prevents weighted averaging from masking strong per-layer signals
        self._grounding_alert_threshold: float = 0.30   # if any layer grounding < this → alert
        self._hallucination_alert_threshold: float = 0.55  # if any layer risk > this → alert
        self._nli_contradiction_alert: float = 0.25  # if NLI contradiction_ratio > this → alert
        
        # Keywords indicating uncertainty (should be backed by evidence)
        self.uncertainty_keywords: List[str] = [
            "might", "could", "possibly", "perhaps", "maybe",
            "likely", "probably", "seems", "appears", "suggests"
        ]
        
        # Keywords indicating strong claims (require strong evidence)
        self.strong_claim_keywords: List[str] = [
            "definitely", "certainly", "always", "never",
            "all", "none", "every", "must", "will"
        ]
        
        # NLI is reported as 'lazy' here on purpose: probing
        # ``self.nli_checker.available`` would force the ~400-800 MB
        # CrossEncoder load at orchestrator startup even when no
        # verification is ever triggered. The real load happens on the
        # first verify_answer() call.
        logger.info(
            "VerificationManager initialized -- Level: %s, Semantic: %s, NLI: lazy, LLM: %s",
            self.verification_level.value,
            self.semantic_grounder.available,
            self.llm_verifier.available,
        )
    
    @classmethod
    def reset_singleton(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
    
    # ==================== MAIN VERIFICATION ====================
    
    def verify_answer(
        self,
        answer: str,
        evidence_list: List[Dict[str, Any]],
        query: str,
        level: Optional[VerificationLevel] = None
    ) -> VerificationResult:
        """
        Verify an answer against evidence and query using layered architecture.
        
        Args:
            answer: The generated answer to verify
            evidence_list: List of evidence chunks used for answer
            query: Original user query
            level: Verification level (uses default if None)
        
        Returns:
            VerificationResult with verification status and metrics
        """
        level = level or self.verification_level
        
        logger.info("Verifying answer with level: %s", level.value)
        
        issues: List[str] = []
        warnings: List[str] = []
        metadata: Dict[str, Any] = {"level": level.value}
        
        # Extract evidence texts once
        evidence_texts = self._extract_evidence_texts(evidence_list)
        
        # 1. Basic quality checks (all levels)
        quality_score = self._assess_quality(answer, query)
        metadata['quality_score'] = quality_score
        
        if quality_score < self.min_quality_score:
            issues.append(f"Low quality score: {quality_score:.2f}")
        
        # Per-call NLI memoization: STRICT mode invokes both _layered_grounding
        # AND _layered_hallucination on the same (answer, evidence) pair. The NLI
        # cross-encoder (DeBERTa-v3) is the heaviest layer (~400M params, GPU).
        # Compute exactly once, share between layers.
        nli_cache: Dict[str, Any] = {}
        
        # 2. Layered grounding check (standard and strict)
        if level in [VerificationLevel.STANDARD, VerificationLevel.STRICT]:
            grounding_score, grounding_meta = self._layered_grounding(
                answer, evidence_texts, query,
                use_llm=(level == VerificationLevel.STRICT),
                nli_cache=nli_cache,
            )
            metadata['grounding_score'] = grounding_score
            metadata['grounding_layers'] = grounding_meta
            
            if grounding_score < self.min_grounding_score:
                issues.append(f"Low grounding score: {grounding_score:.2f}")
        else:
            grounding_score = 1.0  # Skip for basic level
        
        # 3. Layered hallucination detection (strict only)
        if level == VerificationLevel.STRICT:
            hallucination_risk, hallucination_meta = self._layered_hallucination(
                answer, evidence_texts, query, nli_cache=nli_cache,
            )
            metadata['hallucination_risk'] = hallucination_risk
            metadata['hallucination_layers'] = hallucination_meta
            
            if hallucination_risk > self.max_hallucination_risk:
                issues.append(f"High hallucination risk: {hallucination_risk:.2f}")
        else:
            hallucination_risk = 0.0  # Skip for basic/standard
        
        # 4. Citation verification
        citation_quality = self._verify_citations(answer, evidence_list)
        metadata['citation_quality'] = citation_quality
        
        if citation_quality < 0.5:
            warnings.append("Some claims lack proper citations")
        
        # 5. Claim strength vs evidence check
        claim_warnings = self._check_claim_strength(answer, evidence_list)
        warnings.extend(claim_warnings)
        
        # 6. Atomic claim decomposition (STRICT only)
        if level == VerificationLevel.STRICT and self.claim_decomposer is not None:
            try:
                atomic_claims = self.claim_decomposer.decompose(answer, query)
                if atomic_claims and len(atomic_claims) > 0:
                    combined_evidence = " ".join(evidence_texts[:10])
                    claim_scores: List[Dict[str, Any]] = []
                    unsupported_claims: List[str] = []
                    
                    for claim in atomic_claims:
                        # Use NLI entailment check per claim
                        # check_entailment returns (grounding_score, hallucination_risk, metadata)
                        try:
                            nli_grounding, nli_hall_risk, nli_meta = self.nli_checker.check_entailment(
                                claim, [combined_evidence]
                            )
                        except Exception:
                            nli_grounding = 0.5  # neutral fallback
                            nli_hall_risk = 0.5
                            nli_meta = {}
                        
                        claim_entry = {
                            "claim": claim,
                            "nli_score": round(float(nli_grounding), 3),
                            "supported": float(nli_grounding) >= 0.5,
                        }
                        claim_scores.append(claim_entry)
                        
                        if float(nli_grounding) < 0.3:
                            unsupported_claims.append(claim)
                    
                    # Aggregate
                    supported_count = sum(1 for c in claim_scores if c["supported"])
                    total_claims = len(claim_scores)
                    atomic_support_ratio = supported_count / total_claims if total_claims > 0 else 1.0
                    avg_nli = sum(c["nli_score"] for c in claim_scores) / total_claims if total_claims > 0 else 1.0
                    
                    metadata["atomic_claims"] = {
                        "total": total_claims,
                        "supported": supported_count,
                        "unsupported": total_claims - supported_count,
                        "support_ratio": round(atomic_support_ratio, 3),
                        "avg_nli_score": round(avg_nli, 3),
                        "per_claim": claim_scores,
                    }
                    
                    if unsupported_claims:
                        issues.append(
                            f"{len(unsupported_claims)}/{total_claims} atomic claims "
                            f"not supported by evidence"
                        )
                    
                    if atomic_support_ratio < 0.5:
                        warnings.append(
                            f"Only {supported_count}/{total_claims} claims supported"
                        )
                    
                    logger.info(
                        "Atomic claim analysis: %d/%d claims supported (avg NLI: %.3f)",
                        supported_count, total_claims, avg_nli,
                    )
            except Exception as e:
                logger.warning("Atomic claim decomposition failed: %s", e, exc_info=True)
                metadata["atomic_claims_error"] = str(e)
        
        # Calculate overall confidence
        confidence_score = self._calculate_confidence(
            quality_score,
            grounding_score,
            hallucination_risk,
            citation_quality
        )
        
        # Determine if verified
        # Also check per-layer alerts: if ANY factual-detection layer flagged a problem,
        # the answer should not be marked as verified (prevents weighted-average masking)
        grounding_alert = metadata.get("grounding_layers", {}).get("per_layer_alert", False)
        hallucination_alert = metadata.get("hallucination_layers", {}).get("per_layer_alert", False)
        
        is_verified = (
            len(issues) == 0 and
            confidence_score >= 0.7 and
            quality_score >= self.min_quality_score and
            grounding_score >= self.min_grounding_score and
            hallucination_risk <= self.max_hallucination_risk and
            not grounding_alert and
            not hallucination_alert
        )
        
        logger.info(
            "Verification complete -- Verified: %s, Confidence: %.2f, Quality: %.2f, "
            "Grounding: %.2f, Hallucination Risk: %.2f",
            is_verified, confidence_score, quality_score, grounding_score, hallucination_risk
        )
        
        return VerificationResult(
            is_verified=is_verified,
            confidence_score=confidence_score,
            quality_score=quality_score,
            hallucination_risk=hallucination_risk,
            grounding_score=grounding_score,
            issues=issues,
            warnings=warnings,
            metadata=metadata,
        )
    
    # ==================== LAYERED AGGREGATION ====================
    
    def _layered_grounding(
        self,
        answer: str,
        evidence_texts: List[str],
        query: str,
        use_llm: bool = False,
        nli_cache: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Aggregate grounding scores from available layers.
        
        Returns:
            (aggregated_score, per_layer_metadata)
        """
        layer_scores: List[Tuple[float, float]] = []  # (score, weight)
        layer_meta: Dict[str, Any] = {}

        # Layer 1: Term overlap (always available)
        l1_score, l1_meta = self.term_grounder.score_grounding(answer, evidence_texts)
        layer_scores.append((l1_score, self._grounding_weights[0]))
        layer_meta["layer1_term_overlap"] = {"score": round(l1_score, 4), **l1_meta}

        # Layer 2: Semantic grounding (if available)
        if self.semantic_grounder.available:
            l2_score, l2_meta = self.semantic_grounder.score_grounding(answer, evidence_texts)
            if l2_score >= 0:  # -1 means unavailable/error
                layer_scores.append((l2_score, self._grounding_weights[1]))
                layer_meta["layer2_semantic"] = {"score": round(l2_score, 4), **l2_meta}

        # Layer 2.5: NLI entailment (if available)
        if self.nli_checker.available:
            if nli_cache is not None and "nli_result" in nli_cache:
                nli_result = nli_cache["nli_result"]
            else:
                nli_result = self.nli_checker.check_entailment(answer, evidence_texts)
                if nli_cache is not None:
                    nli_cache["nli_result"] = nli_result
            # check_entailment returns (grounding_score, hallucination_risk, metadata_dict)
            if isinstance(nli_result, tuple):
                nli_score = nli_result[0]  # grounding_score
                nli_meta_dict = nli_result[2] if len(nli_result) > 2 else {}
            elif isinstance(nli_result, dict):
                nli_score = nli_result.get("entailment_score", -1.0)
                nli_meta_dict = nli_result
            else:
                nli_score = -1.0
                nli_meta_dict = {}
            if nli_score >= 0:
                layer_scores.append((nli_score, self._grounding_weights[2]))
                layer_meta["layer2_5_nli"] = {
                    "score": round(nli_score, 4),
                    "claims_checked": nli_meta_dict.get("total_claims", nli_meta_dict.get("claims_checked", 0)),
                    "contradiction_ratio": nli_meta_dict.get("contradiction_ratio", 0.0),
                }

        # Layer 3: LLM (only at STRICT level)
        if use_llm and self.llm_verifier.available:
            l3_ground, _, l3_meta = self.llm_verifier.verify(answer, evidence_texts, query)
            if l3_ground >= 0:
                layer_scores.append((l3_ground, self._grounding_weights[3]))
                layer_meta["layer3_llm"] = {"score": round(l3_ground, 4), **l3_meta}

        # Weighted average with normalization
        total_weight = sum(w for _, w in layer_scores)
        if total_weight > 0:
            aggregated = sum(s * w for s, w in layer_scores) / total_weight
        else:
            aggregated = 0.0

        # ── Per-layer alert detection (prevents weighted average from masking signals) ──
        # Only fires when BOTH factual layers agree on low grounding, or when
        # NLI detects significant contradiction. A single low layer (e.g. term_overlap)
        # alone does NOT cap the score, because paraphrasing LLMs naturally have
        # low term overlap while NLI correctly identifies entailment.
        per_layer_alert = False
        alert_reasons = []
        
        l1_data = layer_meta.get("layer1_term_overlap", {})
        nli_data = layer_meta.get("layer2_5_nli", {})
        
        l1_low = l1_data.get("score", 1.0) < self._grounding_alert_threshold
        nli_low = nli_data.get("score", 1.0) < self._grounding_alert_threshold
        nli_contradiction = nli_data.get("contradiction_ratio", 0.0)
        nli_contradiction_high = nli_contradiction > self._nli_contradiction_alert
        
        # Case 1: Both factual layers agree → genuine low grounding
        if l1_low and nli_low:
            per_layer_alert = True
            alert_reasons.append(
                f"both_low: term_overlap={l1_data.get('score', 0):.3f}, "
                f"nli={nli_data.get('score', 0):.3f} < {self._grounding_alert_threshold}"
            )
        
        # Case 2: NLI detects significant contradictions → evidence conflicts with answer
        if nli_contradiction_high:
            per_layer_alert = True
            alert_reasons.append(
                f"nli_contradiction={nli_contradiction:.3f}>{self._nli_contradiction_alert}"
            )
        
        # If alert fires, cap at the MAXIMUM of factual layers (trust the better signal)
        if per_layer_alert:
            factual_scores = []
            if "layer1_term_overlap" in layer_meta:
                factual_scores.append(layer_meta["layer1_term_overlap"].get("score", 1.0))
            if "layer2_5_nli" in layer_meta:
                factual_scores.append(layer_meta["layer2_5_nli"].get("score", 1.0))
            if factual_scores:
                factual_max = max(factual_scores)
                aggregated = min(aggregated, factual_max)
                logger.info(
                    "[GROUNDING] Per-layer alert: %s → capped aggregated to %.3f",
                    "; ".join(alert_reasons), aggregated
                )
        
        layer_meta["aggregated"] = round(aggregated, 4)
        layer_meta["layers_used"] = len(layer_scores)
        layer_meta["per_layer_alert"] = per_layer_alert
        if alert_reasons:
            layer_meta["alert_reasons"] = alert_reasons

        return aggregated, layer_meta

    def _layered_hallucination(
        self,
        answer: str,
        evidence_texts: List[str],
        query: str,
        nli_cache: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Aggregate hallucination risk from available layers.
        
        Returns:
            (aggregated_risk, per_layer_metadata)
        """
        layer_scores: List[Tuple[float, float]] = []
        layer_meta: Dict[str, Any] = {}

        # Layer 1: Term overlap hallucination risk
        l1_risk, l1_meta = self.term_grounder.score_hallucination_risk(
            answer, evidence_texts, self.strong_claim_keywords
        )
        layer_scores.append((l1_risk, self._hallucination_weights[0]))
        layer_meta["layer1_term_overlap"] = {"risk": round(l1_risk, 4), **l1_meta}

        # Layer 2: Invert semantic grounding score → risk
        if self.semantic_grounder.available:
            l2_score, l2_meta = self.semantic_grounder.score_grounding(answer, evidence_texts)
            if l2_score >= 0:
                l2_risk = 1.0 - l2_score
                layer_scores.append((l2_risk, self._hallucination_weights[1]))
                layer_meta["layer2_semantic"] = {"risk": round(l2_risk, 4), **l2_meta}

        # Layer 2.5: NLI contradiction → hallucination risk
        if self.nli_checker.available:
            if nli_cache is not None and "nli_result" in nli_cache:
                nli_result = nli_cache["nli_result"]
            else:
                nli_result = self.nli_checker.check_entailment(answer, evidence_texts)
                if nli_cache is not None:
                    nli_cache["nli_result"] = nli_result
            # check_entailment returns (grounding_score, hallucination_risk, metadata_dict)
            if isinstance(nli_result, tuple):
                nli_score: float = float(nli_result[0])  # grounding_score
                nli_hall_risk: float = float(nli_result[1]) if len(nli_result) > 1 else 0.0  # hallucination_risk
                nli_meta_dict: Dict[str, Any] = nli_result[2] if len(nli_result) > 2 else {}
                contradiction_ratio: float = float(nli_meta_dict.get("contradiction_ratio", nli_hall_risk))
            elif isinstance(nli_result, dict):
                nli_score = float(nli_result.get("entailment_score", -1.0))
                contradiction_ratio = float(nli_result.get("contradiction_ratio", 0.0))
            else:
                nli_score = -1.0
                contradiction_ratio = 0.0
            if nli_score >= 0:
                # High contradiction ratio and low entailment → high hallucination risk
                nli_risk = max(contradiction_ratio, 1.0 - nli_score)
                layer_scores.append((nli_risk, self._hallucination_weights[2]))
                layer_meta["layer2_5_nli"] = {
                    "risk": round(nli_risk, 4),
                    "entailment_score": round(nli_score, 4),
                    "contradiction_ratio": round(contradiction_ratio, 4),
                }

        # Layer 3: LLM
        if self.llm_verifier.available:
            _, l3_risk, l3_meta = self.llm_verifier.verify(answer, evidence_texts, query)
            if l3_risk >= 0:
                layer_scores.append((l3_risk, self._hallucination_weights[3]))
                layer_meta["layer3_llm"] = {"risk": round(l3_risk, 4), **l3_meta}

        total_weight = sum(w for _, w in layer_scores)
        if total_weight > 0:
            aggregated = sum(s * w for s, w in layer_scores) / total_weight
        else:
            aggregated = 0.8  # High risk if nothing works

        # ── Per-layer alert detection (prevents weighted average from masking signals) ──
        per_layer_alert = False
        alert_reasons = []
        
        # Check Term Overlap hallucination risk
        l1_data = layer_meta.get("layer1_term_overlap", {})
        if l1_data.get("risk", 0.0) > self._hallucination_alert_threshold:
            per_layer_alert = True
            alert_reasons.append(f"term_risk={l1_data.get('risk', 0):.3f}>{self._hallucination_alert_threshold}")
        
        # Check NLI hallucination risk and contradiction ratio
        nli_data = layer_meta.get("layer2_5_nli", {})
        if nli_data.get("risk", 0.0) > self._hallucination_alert_threshold:
            per_layer_alert = True
            alert_reasons.append(f"nli_risk={nli_data.get('risk', 0):.3f}>{self._hallucination_alert_threshold}")
        nli_contradiction = nli_data.get("contradiction_ratio", 0.0)
        if nli_contradiction > self._nli_contradiction_alert:
            per_layer_alert = True
            alert_reasons.append(f"nli_contradiction={nli_contradiction:.3f}>{self._nli_contradiction_alert}")
        
        # If per-layer alert fires, raise aggregated risk to at least the max factual-layer risk
        if per_layer_alert:
            factual_risks = []
            if "layer1_term_overlap" in layer_meta:
                factual_risks.append(layer_meta["layer1_term_overlap"].get("risk", 0.0))
            if "layer2_5_nli" in layer_meta:
                factual_risks.append(layer_meta["layer2_5_nli"].get("risk", 0.0))
            if factual_risks:
                factual_max = max(factual_risks)
                aggregated = max(aggregated, factual_max)
                logger.info(
                    "[HALLUCINATION] Per-layer alert: %s → raised aggregated to %.3f",
                    "; ".join(alert_reasons), aggregated
                )

        layer_meta["aggregated"] = round(aggregated, 4)
        layer_meta["layers_used"] = len(layer_scores)
        layer_meta["per_layer_alert"] = per_layer_alert
        if alert_reasons:
            layer_meta["alert_reasons"] = alert_reasons

        return aggregated, layer_meta
    
    @staticmethod
    def _extract_evidence_texts(evidence_list: List[Dict[str, Any]]) -> List[str]:
        """Extract text content from evidence list."""
        texts: List[str] = []
        for ev in evidence_list:
            if isinstance(ev, dict):
                text = ev.get('text', '') or ev.get('content', '') or ev.get('snippet', '')
                if text:
                    texts.append(text)
        return texts
    
    # ==================== QUALITY ASSESSMENT ====================
    
    def _assess_quality(self, answer: str, query: str) -> float:
        """
        Assess the quality of the answer.
        
        Checks:
        - Length appropriateness
        - Structure and formatting
        - Relevance to query (IDF-aware)
        - Completeness
        
        Returns:
            Quality score (0.0 to 1.0)
        """
        score = 1.0
        
        # Check length
        answer_len = len(answer.strip())
        if answer_len < 20:
            score -= 0.3  # Too short
        elif answer_len > 5000:
            score -= 0.1  # Very long (might be verbose)
        
        # Check if answer is empty or just whitespace
        if not answer.strip():
            return 0.0
        
        # Check for basic structure (sentences)
        sentences = [s.strip() for s in answer.split('.') if s.strip()]
        if len(sentences) < 1:
            score -= 0.2
        
        # Check for query term relevance (IDF-weighted)
        query_terms = set(
            term.strip(string.punctuation).lower()
            for term in query.split()
        )
        answer_terms = set(
            term.strip(string.punctuation).lower()
            for term in answer.split()
        )
        
        # Remove stop words for better relevance check
        query_terms = {t for t in query_terms if t and t not in TermOverlapGrounder.STOP_WORDS}
        
        if query_terms:
            relevance = sum(1 for term in query_terms if term in answer_terms) / len(query_terms)
            score = score * (0.5 + 0.5 * relevance)
        
        # Check for common quality issues
        if answer.count('?') > 5:
            score -= 0.1  # Too many questions
        
        if '...' in answer or '???' in answer:
            score -= 0.1  # Indicates uncertainty
        
        return max(0.0, min(1.0, score))
    
    # ==================== CITATION VERIFICATION ====================
    
    def _verify_citations(self, answer: str, evidence_list: List[Dict[str, Any]]) -> float:
        """
        Verify that important claims have proper citations.
        
        Args:
            answer: The generated answer
            evidence_list: List of evidence chunks
        
        Returns:
            Citation quality score (0.0 to 1.0)
        """
        # Count citation markers (e.g., [1], [Source 1], etc.)
        import re
        citation_pattern = r'\[(?:\d+|Source\s*\d+|Evidence\s*\d+)\]'
        citations = re.findall(citation_pattern, answer, re.IGNORECASE)
        
        # Split answer into claims
        answer_sentences = [s.strip() for s in answer.split('.') if s.strip() and len(s.strip()) > 10]
        
        if not answer_sentences:
            return 1.0  # No claims to cite
        
        # Count sentences with citations
        cited_sentences = 0
        for sentence in answer_sentences:
            if re.search(citation_pattern, sentence, re.IGNORECASE):
                cited_sentences += 1
        
        # Calculate citation ratio
        citation_ratio = cited_sentences / len(answer_sentences) if answer_sentences else 0.0
        
        # Bonus if evidence is available and citations are used
        if evidence_list and citations:
            citation_ratio = min(1.0, citation_ratio + 0.2)
        
        return citation_ratio
    
    def _check_claim_strength(self, answer: str, evidence_list: List[Dict[str, Any]]) -> List[str]:
        """
        Check if claim strength matches evidence strength.
        
        Strong claims should be backed by strong evidence.
        
        Args:
            answer: The generated answer
            evidence_list: List of evidence chunks
        
        Returns:
            List of warnings about claim strength mismatches
        """
        warnings: List[str] = []
        
        answer_lower = answer.lower()
        
        # Check for strong claims
        strong_claims = [kw for kw in self.strong_claim_keywords if kw in answer_lower]
        
        if strong_claims and len(evidence_list) < 2:
            warnings.append(
                f"Answer contains strong claims ({', '.join(strong_claims[:3])}) "
                f"but only {len(evidence_list)} evidence chunk(s)"
            )
        
        # Check for uncertainty without evidence
        uncertainty_claims = [kw for kw in self.uncertainty_keywords if kw in answer_lower]
        
        if uncertainty_claims and not evidence_list:
            warnings.append(
                f"Answer expresses uncertainty ({', '.join(uncertainty_claims[:3])}) "
                "and lacks supporting evidence"
            )
        
        return warnings
    
    def _calculate_confidence(
        self,
        quality_score: float,
        grounding_score: float,
        hallucination_risk: float,
        citation_quality: float
    ) -> float:
        """
        Calculate overall confidence score for the answer.
        
        Args:
            quality_score: Answer quality (0-1)
            grounding_score: Evidence grounding (0-1)
            hallucination_risk: Hallucination risk (0-1)
            citation_quality: Citation quality (0-1)
        
        Returns:
            Confidence score (0.0 to 1.0)
        """
        # Weighted combination
        confidence = (
            0.3 * quality_score +
            0.35 * grounding_score +
            0.25 * (1.0 - hallucination_risk) +
            0.1 * citation_quality
        )
        
        return max(0.0, min(1.0, confidence))
    
    def set_verification_level(self, level: VerificationLevel) -> None:
        """Set the verification level."""
        self.verification_level = level
        logger.info("Verification level set to: %s", level.value)
    
    def set_thresholds(
        self,
        min_quality: Optional[float] = None,
        min_grounding: Optional[float] = None,
        max_hallucination: Optional[float] = None
    ) -> None:
        """
        Set verification thresholds.
        
        Args:
            min_quality: Minimum quality score (0-1)
            min_grounding: Minimum grounding score (0-1)
            max_hallucination: Maximum hallucination risk (0-1)
        """
        if min_quality is not None:
            self.min_quality_score = max(0.0, min(1.0, min_quality))
        
        if min_grounding is not None:
            self.min_grounding_score = max(0.0, min(1.0, min_grounding))
        
        if max_hallucination is not None:
            self.max_hallucination_risk = max(0.0, min(1.0, max_hallucination))
        
        logger.info(
            "Thresholds updated - Quality: %.2f, Grounding: %.2f, Max Hallucination: %.2f",
            self.min_quality_score, self.min_grounding_score, self.max_hallucination_risk
        )
