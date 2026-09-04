"""
User Insight Extraction from KG triples.

Provides LLM-based and fallback insight extraction from Knowledge Graph triples.
Recognises recurring themes, emotional patterns, relationship dynamics and
behavioural tendencies.

Extracted from wellbeing_session_interface.py as part of Phase 6b refactoring.
"""

import json
import logging
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from utils.llm_json_parser import parse_llm_json

logger = logging.getLogger(__name__)


class UserInsightExtractor:
    """
    Extracts psychological insights from KG triples.

    Supports two modes:
    - **LLM-based**: uses the model to analyse triples and produce structured
      insights (preferred when model is available).
    - **Fallback**: simple frequency analysis (no LLM required).
    """

    def __init__(
        self,
        chat_logic: Optional[Any] = None,
        structured_outputs_available: bool = False,
        llm_structured_wrapper_cls: Optional[type] = None,
        insights_extraction_output_cls: Optional[type] = None,
    ) -> None:
        """
        Args:
            chat_logic: Chat logic with ``model_loader`` attribute.
            structured_outputs_available: Whether structured output classes are usable.
            llm_structured_wrapper_cls: ``LLMStructuredWrapper`` class (optional).
            insights_extraction_output_cls: ``InsightsExtractionOutput`` class (optional).
        """
        self.chat_logic = chat_logic
        self.structured_outputs_available = structured_outputs_available
        self._llm_structured_wrapper_cls = llm_structured_wrapper_cls
        self._insights_extraction_output_cls = insights_extraction_output_cls
        self._insights_wrapper: Optional[Any] = None  # Lazy-created wrapper

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_user_insights(
        self, triples: List[Dict[str, Any]], user_input: str = ""
    ) -> List[Dict[str, Any]]:
        """
        LLM-based insight extraction from KG triples.

        Falls back to frequency analysis when no LLM is available.

        Args:
            triples: List of KG triple dicts.
            user_input: Current user message for context.

        Returns:
            List of insight dicts ``{insight, confidence, type}``.
        """
        if not triples or len(triples) < 3:
            return []

        # Check if LLM is available
        if not self.chat_logic or not hasattr(self.chat_logic, "model_loader"):
            logger.debug("💡 [INSIGHTS] Kein LLM verfügbar - verwende Fallback")
            return self.extract_user_insights_fallback(triples)

        try:
            return self._extract_via_llm(triples, user_input)
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"💡 [INSIGHTS-LLM] Fehler: {e}")
            return self.extract_user_insights_fallback(triples)

    def extract_user_insights_fallback(
        self, triples: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Graph-stat fallback without keyword heuristics."""
        insights: List[Dict[str, Any]] = []

        entity_counter: Counter[str] = Counter()
        predicate_counter: Counter[str] = Counter()
        confidences: List[float] = []

        for triple in triples:
            subject = str(triple.get("subject") or "").strip().lower()
            predicate = str(triple.get("predicate") or "").strip().lower()
            obj = str(triple.get("object") or "").strip().lower()

            if subject and subject not in {"benutzer", "user", "ich", "person"}:
                entity_counter[subject] += 1
            if obj and obj not in {"benutzer", "user", "ich", "person"}:
                entity_counter[obj] += 1
            if predicate:
                predicate_counter[predicate] += 1

            try:
                confidences.append(float(triple.get("confidence") or 0.0))
            except Exception:
                confidences.append(0.0)

        total = max(1, len(triples))
        avg_conf = sum(confidences) / max(1, len(confidences))
        repeated_ratio = (
            sum(1 for _, count in entity_counter.items() if count >= 2)
            / max(1, len(entity_counter))
        )

        if entity_counter:
            entity, count = entity_counter.most_common(1)[0]
            insights.append(
                {
                    "insight": f"Starker Fokus auf '{entity}' mit {count} Bezügen im Wissensgraph.",
                    "type": "theme",
                    "confidence": min(0.85, 0.45 + (count / total)),
                }
            )

        if predicate_counter:
            predicate, count = predicate_counter.most_common(1)[0]
            insights.append(
                {
                    "insight": f"Dominantes Beziehungsmuster über Prädikat '{predicate}' ({count} Vorkommen).",
                    "type": "behavior",
                    "confidence": min(0.85, 0.40 + (count / total)),
                }
            )

        insights.append(
            {
                "insight": (
                    "Gesamtkonsistenz der Evidenz: "
                    f"Ø-Konfidenz {avg_conf:.2f}, Wiederholungsanteil {repeated_ratio:.2f}."
                ),
                "type": "meta",
                "confidence": min(0.8, 0.35 + avg_conf * 0.5 + repeated_ratio * 0.2),
            }
        )

        return insights[:3]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_via_llm(
        self, triples: List[Dict[str, Any]], user_input: str
    ) -> List[Dict[str, Any]]:
        """Use LLM to extract structured insights."""
        # Format triples for prompt
        triples_text = "\n".join(
            [
                f"- {t.get('subject', '?')} → {t.get('predicate', '?')} → {t.get('object', '?')}"
                for t in triples[:30]
            ]
        )

        prompt = self._build_insight_prompt(triples_text, user_input)

        # Generate with LLM (chat_logic is guaranteed non-None by caller)
        assert self.chat_logic is not None
        response: str = self.chat_logic.model_loader.generate_response(
            prompt, max_tokens=1024, temperature=0.3
        )

        # Try structured output first
        structured_result = self._try_structured_parse(response)
        if structured_result is not None:
            return structured_result

        # Legacy JSON parsing fallback
        return self._try_legacy_parse(response, triples)

    def _build_insight_prompt(self, triples_text: str, user_input: str) -> str:
        """Build the LLM prompt for insight extraction."""
        context_line = f"\nAKTUELLER KONTEXT: {user_input}" if user_input else ""

        return f"""Analysiere diese Knowledge-Graph-Triples aus psychologischen Gesprächen und extrahiere 3-5 wichtige Erkenntnisse über die Person.

TRIPLES:
{triples_text}
{context_line}

Extrahiere psychologische Insights im folgenden JSON-Format:
{{
    "insights": [
        {{"insight": "Kurze Beschreibung des erkannten Musters", "type": "theme|emotion|relationship|behavior", "confidence": 0.0-1.0}},
        ...
    ]
}}

Fokussiere auf:
- Wiederkehrende Themen (type: "theme")
- Emotionale Muster (type: "emotion")
- Beziehungsdynamiken (type: "relationship")
- Verhaltenstendenzen (type: "behavior")

Antworte NUR mit dem JSON-Objekt, keine weitere Erklärung."""

    def _try_structured_parse(self, response: str) -> Optional[List[Dict[str, Any]]]:
        """Attempt structured output parsing via ``LLMStructuredWrapper``."""
        if (
            not self.structured_outputs_available
            or self._llm_structured_wrapper_cls is None
        ):
            return None

        try:
            # Lazy-create wrapper
            if self._insights_wrapper is None:
                assert self.chat_logic is not None  # guaranteed by caller guard
                self._insights_wrapper = self._llm_structured_wrapper_cls(
                    self.chat_logic.model_loader,
                    max_retries=2,
                    retry_delay=0.5,
                    temperature=0.3,
                    enable_logging=False,
                )

            assert self._insights_wrapper is not None
            json_str: str = self._insights_wrapper._extract_json_from_response(response)

            # Try full InsightsExtractionOutput schema
            InsightsOutput = self._insights_extraction_output_cls
            if InsightsOutput is not None and hasattr(InsightsOutput, "model_validate_json"):
                try:
                    validate_json: Callable[[str], Any] = InsightsOutput.model_validate_json
                    insights_output = validate_json(json_str)
                    if hasattr(insights_output, "insights") and insights_output.insights:
                        insights = [
                            {
                                "insight": ins.insight,
                                "type": ins.category,
                                "confidence": ins.confidence,
                            }
                            for ins in insights_output.insights
                        ]
                        logger.info(
                            f"💡 [INSIGHTS-STRUCTURED] {len(insights)} Insights "
                            f"mit vollständigem Schema extrahiert"
                        )
                        return insights[:5]
                except Exception:
                    pass

            # Fallback: parse as plain JSON array
            data = json.loads(json_str)
            if isinstance(data, list) and InsightsOutput is not None:
                from llm_output_schemas import PersonalityInsight

                insights_list: List[Any] = []
                for item in data[:5]:
                    if isinstance(item, dict):
                        try:
                            insight = PersonalityInsight(
                                category=item.get("type", "Other"),
                                insight=item.get("insight", ""),
                                evidence=[],
                                confidence=float(item.get("confidence", 0.7)),
                            )
                            insights_list.append(insight)
                        except Exception:
                            pass

                if insights_list:
                    insights = [
                        {
                            "insight": ins.insight,
                            "type": ins.category,
                            "confidence": ins.confidence,
                        }
                        for ins in insights_list
                    ]
                    logger.info(
                        f"💡 [INSIGHTS-STRUCTURED] {len(insights)} Insights "
                        f"mit Array-Fallback extrahiert"
                    )
                    return insights[:5]

        except Exception as e:
            logger.warning(f"💡 [INSIGHTS-STRUCTURED] Fehler: {e}, falling back to legacy")

        return None

    def _try_legacy_parse(
        self, response: str, triples: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Compatibility JSON parsing with robust object extraction."""

        def _normalize_insights(items: List[Any]) -> List[Dict[str, Any]]:
            normalized: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                insight_text = str(item.get("insight", "")).strip()
                insight_type = str(item.get("type", item.get("category", "theme"))).strip() or "theme"
                try:
                    confidence = float(item.get("confidence", 0.6))
                except Exception:
                    confidence = 0.6
                if insight_text:
                    normalized.append(
                        {
                            "insight": insight_text,
                            "type": insight_type,
                            "confidence": max(0.0, min(1.0, confidence)),
                        }
                    )
            return normalized[:5]

        def _validate_payload(data: Dict[str, Any]) -> bool:
            insights = data.get("insights")
            return isinstance(insights, list)

        try:
            parsed_obj = parse_llm_json(
                response,
                schema_validator=_validate_payload,
                default_on_error=None,
                debug=False,
            )
            if isinstance(parsed_obj, dict):
                normalized = _normalize_insights(parsed_obj.get("insights") or [])
                if normalized:
                    logger.info(
                        f"💡 [INSIGHTS-LEGACY] {len(normalized)} Insights via robust parser extrahiert"
                    )
                    return normalized
        except Exception as legacy_e:
            logger.warning(f"💡 [INSIGHTS-LEGACY] Fehler: {legacy_e}")

        # Backward compatibility: older prompt versions emitted plain arrays.
        try:
            raw = json.loads(response)
            if isinstance(raw, list):
                normalized = _normalize_insights(raw)
                if normalized:
                    return normalized
            if isinstance(raw, dict) and isinstance(raw.get("insights"), list):
                normalized = _normalize_insights(raw["insights"])
                if normalized:
                    return normalized
        except Exception:
            pass

        logger.warning("💡 [INSIGHTS] Kein JSON in LLM-Antwort gefunden")
        return self.extract_user_insights_fallback(triples)

