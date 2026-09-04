"""
Unit tests for VerificationManager Layer 1 (TermOverlapGrounder),
VerificationResult state machine, BASIC verify_answer, and
AtomicClaimDecomposer heuristic path.

These tests require NO external dependencies (no embeddings, no NLI model,
no LLM callable) and cover the deterministic core of the verification
pipeline.

Hypotheses (falsifiable):
  H1: Zero-overlap answer vs evidence  -> grounding_score == 0.0
  H2: Perfect-overlap answer vs evidence -> grounding_score == 1.0
  H3: to_status(grounding=0.1)         -> INSUFFICIENT_EVIDENCE
  H4: to_status(hallucination_risk=0.6) -> HALLUCINATION_RISK
  H5: BASIC verify_answer(short)       -> PASSED, grounding == 1.0
"""

from __future__ import annotations

import pytest
from agent.verification_manager import (
    TermOverlapGrounder,
    VerificationLevel,
    VerificationResult,
    VerificationStatus,
    VerificationManager,
    AtomicClaimDecomposer,
)


# =====================================================================
# TermOverlapGrounder._tokenize
# =====================================================================

class TestTokenize:
    grounder = TermOverlapGrounder()

    def test_stop_words_removed(self) -> None:
        """EN + DE stop words are filtered out."""
        tokens = self.grounder._tokenize(
            "The quick brown fox Der schnelle braune Fuchs"
        )
        # "the" and "der" are in STOP_WORDS; "schnelle" is NOT (content word)
        assert "the" not in tokens
        assert "der" not in tokens
        # Content words survive tokenization
        assert "quick" in tokens
        assert "schnelle" in tokens

    def test_punctuation_stripped(self) -> None:
        """Brackets, commas, periods are removed from tokens."""
        tokens = self.grounder._tokenize("Hello, world! (test).")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens
        # No punctuation should remain
        for t in tokens:
            assert t.isalnum()

    def test_single_char_tokens_dropped(self) -> None:
        """Tokens with length <= 1 are excluded."""
        tokens = self.grounder._tokenize("a I test word x")
        assert "a" not in tokens
        assert "x" not in tokens
        assert "test" in tokens
        assert "word" in tokens

    def test_lowercase_normalization(self) -> None:
        """All tokens are lowercased."""
        tokens = self.grounder._tokenize("Mixed CASE Tokens")
        assert all(t == t.lower() for t in tokens)


# =====================================================================
# TermOverlapGrounder._build_idf
# =====================================================================

class TestBuildIdf:
    grounder = TermOverlapGrounder()

    def test_common_term_lower_idf(self) -> None:
        """Terms appearing in all documents get lower IDF than rare terms."""
        corpus = [
            ["common", "rare_a"],
            ["common", "rare_b"],
            ["common", "rare_c"],
        ]
        idf = self.grounder._build_idf(corpus)
        assert idf["common"] < idf["rare_a"]
        assert idf["common"] < idf["rare_b"]

    def test_empty_corpus_returns_empty_dict(self) -> None:
        idf = self.grounder._build_idf([])
        assert idf == {}


# =====================================================================
# TermOverlapGrounder.score_grounding
# =====================================================================

class TestScoreGrounding:
    grounder = TermOverlapGrounder()

    def test_zero_overlap(self) -> None:
        """H1: Completely disjoint answer/evidence -> score ~ 0."""
        answer = "The moon is made of green cheese and purple clouds."
        evidence = [
            "Solar panels convert sunlight into electricity.",
            "Quantum computing uses qubits for parallel processing.",
        ]
        score, meta = self.grounder.score_grounding(answer, evidence)
        assert score < 0.15, f"Expected near-zero grounding, got {score:.3f}"
        assert meta["method"] == "term_overlap"

    def test_perfect_overlap(self) -> None:
        """H2: Answer is a subset of evidence -> score ~ 1."""
        answer = "Berlin is the capital of Germany."
        evidence = [
            "Berlin is the capital of Germany and has about 3.6 million inhabitants.",
        ]
        score, _ = self.grounder.score_grounding(answer, evidence)
        assert score > 0.85, f"Expected near-perfect grounding, got {score:.3f}"

    def test_no_evidence(self) -> None:
        """No evidence texts -> score = 0."""
        score, meta = self.grounder.score_grounding("Any answer at all", [])
        assert score == 0.0
        assert meta["detail"] == "no_evidence"

    def test_no_answer_tokens_returns_neutral(self) -> None:
        """Answer with only stop words / punctuation -> neutral 0.5."""
        score, meta = self.grounder.score_grounding("The a an is", ["some evidence"])
        assert score == 0.5
        assert meta["detail"] == "no_answer_tokens"

    def test_partial_overlap(self) -> None:
        """Half the sentences grounded, half not -> mid-range score."""
        answer = (
            "Python is a programming language. "
            "The moon has seven moons."
        )
        evidence = [
            "Python is a widely used programming language for data science.",
        ]
        score, _ = self.grounder.score_grounding(answer, evidence)
        # First sentence fully grounded, second not -> ~0.3-0.7
        assert 0.2 < score < 0.9, f"Expected mid-range, got {score:.3f}"


# =====================================================================
# TermOverlapGrounder.score_hallucination_risk
# =====================================================================

class TestScoreHallucinationRisk:
    grounder = TermOverlapGrounder()
    strong_keywords = ["definitely", "certainly", "always", "never"]

    def test_high_risk_no_overlap(self) -> None:
        """Completely unsupported answer -> high risk."""
        answer = (
            "The earth is flat. "
            "Dinosaurs live in Antarctica. "
            "Water is made of fire."
        )
        evidence = [
            "The solar system has eight planets orbiting the sun.",
            "Photosynthesis converts light energy into chemical energy.",
        ]
        risk, _ = self.grounder.score_hallucination_risk(
            answer, evidence, self.strong_keywords
        )
        assert risk > 0.5, f"Expected high risk, got {risk:.3f}"

    def test_low_risk_good_overlap(self) -> None:
        """Well-supported answer -> low risk."""
        answer = "Paris is the capital of France and known for the Eiffel Tower."
        evidence = [
            "Paris, the capital of France, is famous for the Eiffel Tower and the Louvre.",
        ]
        risk, _ = self.grounder.score_hallucination_risk(
            answer, evidence, self.strong_keywords
        )
        assert risk < 0.3, f"Expected low risk, got {risk:.3f}"

    def test_no_evidence_returns_high_risk(self) -> None:
        risk, meta = self.grounder.score_hallucination_risk(
            "Some claim", [], self.strong_keywords
        )
        assert risk == 0.8
        assert meta["detail"] == "no_evidence"

    def test_strong_claim_unsupported_boosts_risk(self) -> None:
        """Strong keywords without evidence amplify risk."""
        answer = "This is definitely true and will always happen."
        evidence = ["Unrelated topic about rainbows."]
        risk, meta = self.grounder.score_hallucination_risk(
            answer, evidence, self.strong_keywords
        )
        assert meta["strong_unsupported"] >= 1
        assert risk > 0.5


# =====================================================================
# VerificationResult.to_status()  —  State Machine Contract
# =====================================================================

class TestVerificationResultToStatus:

    def _make_result(
        self,
        is_verified: bool = True,
        confidence_score: float = 0.9,
        quality_score: float = 0.8,
        hallucination_risk: float = 0.1,
        grounding_score: float = 0.7,
        issues: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> VerificationResult:
        if issues is None:
            issues = []
        if warnings is None:
            warnings = []
        return VerificationResult(
            is_verified=is_verified,
            confidence_score=confidence_score,
            quality_score=quality_score,
            hallucination_risk=hallucination_risk,
            grounding_score=grounding_score,
            issues=issues,
            warnings=warnings,
        )

    def test_passed(self) -> None:
        """H: All scores good -> PASSED."""
        assert self._make_result().to_status() == VerificationStatus.PASSED

    def test_hallucination_risk(self) -> None:
        """H4: hallucination_risk > 0.55 -> HALLUCINATION_RISK."""
        assert (
            self._make_result(hallucination_risk=0.6).to_status()
            == VerificationStatus.HALLUCINATION_RISK
        )

    def test_insufficient_evidence(self) -> None:
        """H3: grounding_score < 0.30 -> INSUFFICIENT_EVIDENCE."""
        assert (
            self._make_result(grounding_score=0.1).to_status()
            == VerificationStatus.INSUFFICIENT_EVIDENCE
        )

    def test_failed_low_quality(self) -> None:
        """quality_score < 0.40 -> FAILED."""
        assert (
            self._make_result(quality_score=0.3).to_status()
            == VerificationStatus.FAILED
        )

    def test_failed_not_verified(self) -> None:
        """is_verified=False -> FAILED."""
        assert (
            self._make_result(is_verified=False, quality_score=0.5).to_status()
            == VerificationStatus.FAILED
        )

    def test_hallucination_beats_insufficient(self) -> None:
        """Priority: HALLUCINATION_RISK wins over INSUFFICIENT_EVIDENCE."""
        assert (
            self._make_result(
                hallucination_risk=0.6,
                grounding_score=0.1,
            ).to_status()
            == VerificationStatus.HALLUCINATION_RISK
        )


# =====================================================================
# VerificationManager.verify_answer()  —  BASIC level (no NLI, no LLM)
# =====================================================================

class TestVerifyAnswerBasic:

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        """Reset singleton before each test to avoid cross-test pollution."""
        VerificationManager.reset_singleton()

    def test_basic_well_written_answer_passes(self) -> None:
        """H5: Short, coherent answer at BASIC level -> verified."""
        vm = VerificationManager()
        result = vm.verify_answer(
            answer="Python is a high-level programming language created by Guido van Rossum.",
            evidence_list=[
                {"text": "Python was created by Guido van Rossum and first released in 1991."}
            ],
            query="Who created Python?",
            level=VerificationLevel.BASIC,
        )
        # BASIC: grounding defaults to 1.0, hallucination to 0.0
        assert result.grounding_score == 1.0
        assert result.hallucination_risk == 0.0
        assert result.is_verified is True

    def test_basic_very_short_answer_fails_quality(self) -> None:
        """Very short answer -> low quality -> not verified."""
        vm = VerificationManager()
        result = vm.verify_answer(
            answer="idk",
            evidence_list=[{"text": "Some evidence."}],
            query="Explain quantum mechanics.",
            level=VerificationLevel.BASIC,
        )
        assert result.is_verified is False
        assert result.quality_score < vm.min_quality_score

    def test_basic_empty_answer_fails(self) -> None:
        vm = VerificationManager()
        result = vm.verify_answer(
            answer="",
            evidence_list=[],
            query="What is 2+2?",
            level=VerificationLevel.BASIC,
        )
        assert result.is_verified is False


# =====================================================================
# AtomicClaimDecomposer  —  heuristic path (no LLM)
# =====================================================================

class TestHeuristicDecompose:

    def test_decomposes_multiple_sentences(self) -> None:
        """Two sentences -> at least 2 claims."""
        decomposer = AtomicClaimDecomposer(llm_fn=None)
        claims = decomposer._heuristic_decompose(
            "Paris is the capital of France. Berlin is the capital of Germany."
        )
        assert len(claims) >= 2

    def test_splits_long_compound_sentence(self) -> None:
        """Long sentence with conjunction -> split into multiple claims."""
        decomposer = AtomicClaimDecomposer(llm_fn=None)
        long_sentence = (
            "Python is a programming language und "
            "Java ist eine Programmiersprache sowie "
            "C++ ist ebenfalls eine Programmiersprache und "
            "Ruby wird auch fuer Webentwicklung verwendet."
        )
        claims = decomposer._heuristic_decompose(long_sentence)
        # Should split at "und", "sowie"
        assert len(claims) >= 2

    def test_short_answer_returns_single_claim(self) -> None:
        decomposer = AtomicClaimDecomposer(llm_fn=None)
        claims = decomposer._heuristic_decompose("The sky is blue.")
        assert len(claims) == 1

    def test_empty_answer_returns_empty(self) -> None:
        decomposer = AtomicClaimDecomposer(llm_fn=None)
        claims = decomposer.decompose("")
        assert claims == []

    def test_decompose_with_llm_none_falls_back_heuristic(self) -> None:
        """When llm_fn is None, decompose() uses heuristic path."""
        decomposer = AtomicClaimDecomposer(llm_fn=None)
        claims = decomposer.decompose(
            "The sun rises in the east. The sun sets in the west."
        )
        assert len(claims) >= 2