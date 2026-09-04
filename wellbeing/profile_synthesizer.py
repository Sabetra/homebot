#!/usr/bin/env python3
"""
PSYCHOLOGICAL PROFILE SYNTHESIZER
==================================

State-of-the-Art LLM-based profile synthesis engine.
Aggregates KG triples, session summaries, and user insights into a holistic psychological profile.

Features:
- LLM-based synthesis (no hardcoded keywords)
- Confidence scoring for all profile sections
- JSON-structured output with validation
- Evidence-based only (no speculation)
- Therapeutic focus
"""

import logging
import json
import inspect
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from utils.llm_json_parser import parse_llm_json

logger = logging.getLogger(__name__)


PROFILE_OUTPUT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "core_personality",
        "current_state",
        "relationships",
        "goals_and_growth",
        "coping_and_resources",
        "therapeutic_focus",
        "overall_confidence",
    ],
    "additionalProperties": False,
    "properties": {
        "core_personality": {
            "type": "object",
            "required": ["traits", "communication_style", "decision_making", "confidence"],
            "additionalProperties": True,
            "properties": {
                "traits": {"type": "array", "items": {"type": "string"}},
                "communication_style": {"type": "string"},
                "decision_making": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "current_state": {
            "type": "object",
            "required": [
                "emotional_tone",
                "stress_level",
                "life_phase",
                "primary_concerns",
                "confidence",
            ],
            "additionalProperties": True,
            "properties": {
                "emotional_tone": {"type": "string"},
                "stress_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "unknown"],
                },
                "life_phase": {"type": "string"},
                "primary_concerns": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "relationships": {
            "type": "object",
            "required": ["family_dynamics", "social_style", "attachment_patterns", "confidence"],
            "additionalProperties": True,
            "properties": {
                "family_dynamics": {"type": "object", "additionalProperties": True},
                "social_style": {"type": "string"},
                "attachment_patterns": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "goals_and_growth": {
            "type": "object",
            "required": ["current_goals", "growth_areas", "progress_indicators", "confidence"],
            "additionalProperties": True,
            "properties": {
                "current_goals": {"type": "array", "items": {"type": "string"}},
                "growth_areas": {"type": "array", "items": {"type": "string"}},
                "progress_indicators": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "coping_and_resources": {
            "type": "object",
            "required": ["strategies", "strengths", "support_systems", "confidence"],
            "additionalProperties": True,
            "properties": {
                "strategies": {"type": "array", "items": {"type": "string"}},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "support_systems": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "therapeutic_focus": {
            "type": "object",
            "required": ["priority_areas", "intervention_suggestions", "progress_markers", "confidence"],
            "additionalProperties": True,
            "properties": {
                "priority_areas": {"type": "array", "items": {"type": "string"}},
                "intervention_suggestions": {"type": "array", "items": {"type": "string"}},
                "progress_markers": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "overall_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

@dataclass
class WellbeingProfile:
    """Holistic psychological profile (synthesized by LLM)"""
    user_id: str
    version: int
    synthesis_type: str  # 'full' or 'delta' — tracks how this version was created
    
    # Core sections (from LLM synthesis)
    core_personality: Dict[str, Any]  # traits, communication_style, decision_making
    current_state: Dict[str, Any]  # emotional_tone, stress_level, life_phase, concerns
    relationships: Dict[str, Any]  # family_dynamics, social_style, attachment_patterns
    goals_and_growth: Dict[str, Any]  # current_goals, growth_areas, progress
    coping_and_resources: Dict[str, Any]  # strategies, strengths, support_systems
    therapeutic_focus: Dict[str, Any]  # priority_areas, intervention_suggestions
    
    # Metadata
    overall_confidence: float
    data_sources: Dict[str, int]  # kg_triples_used, sessions_used, insights_used
    synthesis_model: str
    synthesis_prompt_hash: str
    created_at: str
    updated_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    def to_context_dict(self) -> Dict[str, Any]:
        """Convert to context-pipeline-friendly dict (excludes internal metadata)."""
        return {
            'core_personality': self.core_personality,
            'current_state': self.current_state,
            'relationships': self.relationships,
            'goals_and_growth': self.goals_and_growth,
            'coping_and_resources': self.coping_and_resources,
            'therapeutic_focus': self.therapeutic_focus,
            'overall_confidence': self.overall_confidence,
            'version': self.version,
            'synthesis_type': self.synthesis_type,
            'updated_at': self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WellbeingProfile':
        """Create from dictionary (backward-compatible with older serialized profiles)."""
        # Ensure synthesis_type exists for profiles created before delta-merge was added
        if 'synthesis_type' not in data:
            data['synthesis_type'] = 'full'
        return cls(**data)


class ProfileSynthesizer:
    """
    LLM-based psychological profile synthesis engine
    
    Aggregates:
    1. KG triples (user facts)
    2. Session summaries (temporal context)
    3. User insights (deep personality analysis)
    
    Output:
    - Holistic psychological profile (JSON)
    - Confidence scores per section
    - Evidence-based only
    """
    
    def __init__(self, psychological_db: Any, model_loader: Any) -> None:
        """
        Initialize profile synthesizer
        
        Args:
            psychological_db: WellbeingDatabase instance
            model_loader: ModelLoader for LLM calls
        """
        self.db = psychological_db
        self.model_loader = model_loader
        self._profile_json_grammar: Any = None
        
        if not model_loader:
            raise ValueError("ModelLoader is REQUIRED for ProfileSynthesizer")
        
        logger.info("✅ ProfileSynthesizer initialisiert (LLM-based)")

    @staticmethod
    def _truncate_text(value: Any, max_chars: int) -> str:
        """Return compact one-line text with hard character budget."""
        text = str(value or "").strip().replace("\n", " ").replace("\r", " ")
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 3)].rstrip() + "..."

    @staticmethod
    def _clamp_confidence(value: Any) -> float:
        """Normalize confidence values into [0.0, 1.0]."""
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        if parsed < 0.0:
            return 0.0
        if parsed > 1.0:
            return 1.0
        return parsed

    def _estimate_messages_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Estimate token count using ModelLoader tokenization when available."""
        try:
            if hasattr(self.model_loader, 'count_messages_tokens'):
                return int(self.model_loader.count_messages_tokens(messages))
        except Exception as exc:
            logger.warning(f"⚠️ count_messages_tokens failed, fallback estimation used: {exc}")

        # Conservative fallback for environments without loaded tokenizer.
        content = "\n".join(str(m.get('content', '')) for m in messages)
        return max(1, len(content) // 4)

    def _build_prompt_with_budget(self,
                                  user_id: str,
                                  kg_triples: List[Dict[str, Any]],
                                  session_summaries: List[Dict[str, Any]],
                                  user_insights: List[Dict[str, Any]],
                                  *,
                                  previous_profile: Optional['WellbeingProfile'],
                                  synthesis_type: str,
                                  max_kg_triples: int,
                                  max_sessions: int) -> tuple[str, int, int]:
        """Build a synthesis prompt that fits into model context with output reserve.

        This prevents JSON truncation structurally by reserving generation space
        before the call instead of relying on parse-time fallbacks.
        """
        try:
            n_ctx = int(self.model_loader.get_max_context_tokens())
        except Exception:
            n_ctx = 16384

        # Reserve enough output tokens for full JSON profile generation.
        output_budget = max(1024, min(3072, int(n_ctx * 0.22)))
        target_prompt_tokens = max(512, n_ctx - output_budget - 192)

        kg_limit = max(0, min(max_kg_triples, len(kg_triples)))
        session_limit = max(0, min(max_sessions, len(session_summaries)))
        insight_limit = max(0, min(20, len(user_insights)))
        summary_char_limit = 1000

        prompt = ""
        prompt_tokens = 0

        for _ in range(20):
            if synthesis_type == 'delta' and previous_profile is not None:
                prompt = self._build_merge_prompt(
                    user_id, previous_profile, kg_triples, session_summaries, user_insights,
                    kg_limit=kg_limit,
                    session_limit=session_limit,
                    insight_limit=insight_limit,
                    summary_char_limit=summary_char_limit,
                )
            else:
                prompt = self._build_synthesis_prompt(
                    user_id, kg_triples, session_summaries, user_insights,
                    kg_limit=kg_limit,
                    session_limit=session_limit,
                    insight_limit=insight_limit,
                    summary_char_limit=summary_char_limit,
                )

            prompt_tokens = self._estimate_messages_tokens([
                {"role": "user", "content": prompt}
            ])

            if prompt_tokens <= target_prompt_tokens:
                logger.info(
                    f"📏 Prompt budget OK: prompt={prompt_tokens}, target<={target_prompt_tokens}, "
                    f"output_budget={output_budget}, n_ctx={n_ctx}, "
                    f"kg={kg_limit}, sessions={session_limit}, insights={insight_limit}, "
                    f"summary_chars={summary_char_limit}"
                )
                return prompt, output_budget, prompt_tokens

            # Deterministic shrinking strategy.
            if summary_char_limit > 220:
                summary_char_limit = max(220, int(summary_char_limit * 0.75))
                continue
            if session_limit > 3:
                session_limit -= 1
                continue
            if kg_limit > 40:
                kg_limit = max(40, kg_limit - 15)
                continue
            if insight_limit > 8:
                insight_limit -= 1
                continue
            break

        raise ValueError(
            f"Prompt exceeds context budget even after compaction "
            f"(prompt={prompt_tokens}, target<={target_prompt_tokens}, n_ctx={n_ctx})"
        )

    def _validate_profile_schema(self, data: Dict[str, Any]) -> bool:
        """Strict top-level schema guard for profile synthesis output."""
        required_dict_sections = [
            'core_personality', 'current_state', 'relationships',
            'goals_and_growth', 'coping_and_resources', 'therapeutic_focus',
        ]

        if not isinstance(data, dict):
            return False

        for section in required_dict_sections:
            if section not in data or not isinstance(data.get(section), dict):
                return False

        if 'overall_confidence' not in data:
            return False

        try:
            overall = float(data['overall_confidence'])
        except (TypeError, ValueError):
            return False

        if overall < 0.0 or overall > 1.0:
            return False

        # Confidence consistency: all main sections must carry confidence in [0, 1].
        for section in required_dict_sections:
            section_data = data.get(section, {})
            if not isinstance(section_data, dict) or 'confidence' not in section_data:
                return False
            try:
                section_conf = float(section_data['confidence'])
            except (TypeError, ValueError):
                return False
            if section_conf < 0.0 or section_conf > 1.0:
                return False

        return True

    def _validate_semantic_consistency(self, data: Dict[str, Any]) -> Optional[str]:
        """Validate cross-field semantic consistency for profile quality gates.

        Returns:
            None when consistent, else a short error reason.
        """
        try:
            allowed_stress_levels = {"low", "medium", "high", "unknown"}
            stress_level = str(data.get('current_state', {}).get('stress_level', '')).strip().lower()
            if stress_level not in allowed_stress_levels:
                return f"invalid_stress_level:{stress_level or 'empty'}"

            section_keys = [
                'core_personality',
                'current_state',
                'relationships',
                'goals_and_growth',
                'coping_and_resources',
                'therapeutic_focus',
            ]

            section_confidences: List[float] = []
            for key in section_keys:
                section = data.get(key, {})
                if isinstance(section, dict):
                    section_confidences.append(self._clamp_confidence(section.get('confidence')))

            if not section_confidences:
                return "missing_section_confidences"

            avg_conf = sum(section_confidences) / len(section_confidences)
            overall = self._clamp_confidence(data.get('overall_confidence'))

            # Prevent implausible confidence jumps between section-level and global score.
            if abs(overall - avg_conf) > 0.35:
                return (
                    f"confidence_mismatch:overall={overall:.3f},"
                    f"avg_sections={avg_conf:.3f}"
                )

            core_traits = data.get('core_personality', {}).get('traits', [])
            concerns = data.get('current_state', {}).get('primary_concerns', [])
            goals = data.get('goals_and_growth', {}).get('current_goals', [])
            priorities = data.get('therapeutic_focus', {}).get('priority_areas', [])
            evidence_items = 0
            for value in [core_traits, concerns, goals, priorities]:
                if isinstance(value, list):
                    evidence_items += len([item for item in value if str(item).strip()])

            # Reject high-confidence empty profiles (common failure mode in truncated/degenerate generations).
            if evidence_items == 0 and overall >= 0.60:
                return f"empty_profile_with_high_confidence:{overall:.3f}"

            return None
        except Exception as exc:
            return f"semantic_validation_error:{type(exc).__name__}"

    def validate_profile_payload(self, data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate a synthesized profile payload end-to-end.

        Returns:
            (True, None) when payload is valid, otherwise (False, reason).
        """
        if not self._validate_profile_schema(data):
            return False, "schema_validation_failed"
        semantic_error = self._validate_semantic_consistency(data)
        if semantic_error:
            return False, semantic_error
        return True, None

    @staticmethod
    def _supports_grammar_argument(model_loader: Any) -> bool:
        """Capability probe: check if loader supports grammar-constrained decoding."""
        try:
            fn = getattr(model_loader, 'generate_response', None)
            if fn is None:
                return False
            return 'grammar' in inspect.signature(fn).parameters
        except Exception:
            return False

    def _compile_profile_json_grammar(self) -> Any:
        """Compile and cache JSON-schema grammar for profile synthesis output."""
        if self._profile_json_grammar is not None:
            return self._profile_json_grammar

        try:
            from llama_cpp import LlamaGrammar
        except Exception as exc:
            raise RuntimeError(
                "llama_cpp.LlamaGrammar ist nicht verfügbar; constrained decoding kann nicht aktiviert werden"
            ) from exc

        try:
            schema_json = json.dumps(PROFILE_OUTPUT_JSON_SCHEMA, ensure_ascii=False)
            self._profile_json_grammar = LlamaGrammar.from_json_schema(schema_json)
            return self._profile_json_grammar
        except Exception as exc:
            raise RuntimeError("JSON-Schema-Grammar-Kompilierung fehlgeschlagen") from exc

    def _log_synthesis_metrics(self,
                               *,
                               user_id: str,
                               synthesis_type: str,
                               prompt_tokens: int,
                               output_budget: int,
                               response_chars: int,
                               profile_data: Dict[str, Any]) -> None:
        """Emit synthesis quality metrics for trend monitoring and regression checks."""
        overall = self._clamp_confidence(profile_data.get('overall_confidence'))
        section_confidences = []
        for key in [
            'core_personality',
            'current_state',
            'relationships',
            'goals_and_growth',
            'coping_and_resources',
            'therapeutic_focus',
        ]:
            value = profile_data.get(key, {})
            if isinstance(value, dict):
                section_confidences.append(self._clamp_confidence(value.get('confidence')))

        avg_section_conf = (sum(section_confidences) / len(section_confidences)) if section_confidences else 0.0
        logger.info(
            "[PROFILE-SYNTHESIS-METRICS] "
            f"user={user_id[:10]} synthesis_type={synthesis_type} "
            f"prompt_tokens={prompt_tokens} output_budget={output_budget} response_chars={response_chars} "
            f"overall_confidence={overall:.3f} avg_section_confidence={avg_section_conf:.3f}"
        )
    
    def synthesize_profile(self, user_id: str, 
                          max_kg_triples: int = 200,
                          max_sessions: int = 20,
                          force_regenerate: bool = False,
                          previous_profile: Optional['WellbeingProfile'] = None,
                          synthesis_type: str = 'auto') -> Optional[WellbeingProfile]:
        """
        Synthesize or update a holistic psychological profile using LLM.

        Args:
            user_id: User ID
            max_kg_triples: Max KG triples to include
            max_sessions: Max sessions to include
            force_regenerate: Force full regeneration
            previous_profile: Existing profile for delta-merge (if None → full synthesis)
            synthesis_type: 'full', 'delta', or 'auto' (auto = delta if previous_profile, else full)

        Returns:
            WellbeingProfile or None if insufficient data
        """
        # Determine synthesis type
        if synthesis_type == 'auto':
            synthesis_type = 'delta' if previous_profile and not force_regenerate else 'full'
        
        logger.info(f"🧠 Synthesizing profile ({synthesis_type}) for user {user_id[:10]}...")
        
        # 1. GATHER DATA SOURCES
        kg_triples = self._load_kg_triples(user_id, limit=max_kg_triples)
        session_summaries = self._load_session_summaries(user_id, limit=max_sessions)
        user_insights = self._load_user_insights(user_id)
        
        if not kg_triples and not session_summaries and not user_insights:
            logger.warning(f"⚠️ No data sources available for user {user_id}")
            return None
        
        logger.info(f"📊 Data sources: {len(kg_triples)} KG, {len(session_summaries)} sessions, {len(user_insights)} insights")
        
        # 2. BUILD PROMPT (full or delta-merge)
        if synthesis_type == 'delta' and previous_profile is None:
            synthesis_type = 'full'

        synthesis_prompt, max_output_tokens, prompt_tokens = self._build_prompt_with_budget(
            user_id,
            kg_triples,
            session_summaries,
            user_insights,
            previous_profile=previous_profile,
            synthesis_type=synthesis_type,
            max_kg_triples=max_kg_triples,
            max_sessions=max_sessions,
        )
        
        # 3. CALL LLM
        try:
            response = self._call_llm_for_synthesis(synthesis_prompt, max_output_tokens)
            
            # 4. PARSE AND VALIDATE
            profile_data = self._parse_llm_response(response)
            if not profile_data:
                logger.error("❌ Failed to parse LLM response")
                return None

            self._log_synthesis_metrics(
                user_id=user_id,
                synthesis_type=synthesis_type,
                prompt_tokens=prompt_tokens,
                output_budget=max_output_tokens,
                response_chars=len(response),
                profile_data=profile_data,
            )
            
            # 5. CREATE PROFILE OBJECT
            new_version = (previous_profile.version + 1) if previous_profile else 1
            profile = self._create_profile_object(
                user_id, profile_data,
                kg_count=len(kg_triples),
                session_count=len(session_summaries),
                insight_count=len(user_insights),
                synthesis_prompt=synthesis_prompt,
                version=new_version,
                synthesis_type=synthesis_type,
            )
            
            logger.info(
                f"✅ Profile synthesized (v{new_version}, {synthesis_type}): "
                f"confidence={profile.overall_confidence:.2f}"
            )
            return profile
            
        except Exception as e:
            logger.error(f"❌ Profile synthesis failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _load_kg_triples(self, user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Load KG triples for user from canonical ``triples`` table.

        ``triples`` has no ``user_id`` column; the user scope is established
        via ``session_id`` → ``wellbeing_sessions(id, user_id)``.
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT t.subject, t.predicate, t.object, t.confidence,
                           t.created_at, t.session_id AS context
                    FROM triples t
                    INNER JOIN wellbeing_sessions ps ON t.session_id = ps.id
                    WHERE ps.user_id = ?
                    ORDER BY t.confidence DESC, t.created_at DESC
                    LIMIT ?
                """, (user_id, limit))

                triples = [
                    {
                        'subject': row['subject'],
                        'predicate': row['predicate'],
                        'object': row['object'],
                        'confidence': row['confidence'],
                        'created_at': row['created_at'],
                        'context': row['context'],
                    }
                    for row in cursor.fetchall()
                ]
                logger.info(f"✅ Loaded {len(triples)} KG triples")
                return triples

        except Exception as e:
            logger.warning(f"⚠️ Failed to load KG triples: {e}")
            return []

    def _load_session_summaries(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Load session summaries from canonical ``wellbeing_sessions`` table.

        Schema reminder (see :mod:`wellbeing.wellbeing_db`):
        ``wellbeing_sessions`` stores per-session mood as a single
        ``mood_progression`` TEXT column — there is no ``mood_start`` /
        ``mood_end``. Decryption of ``session_summary`` is delegated to the
        DB-layer convention: callers receive the raw stored value and feed
        it through whatever decryptor the project uses elsewhere; here we
        only forward what the canonical query returns.
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id, session_summary, mood_progression, start_time, end_time
                    FROM wellbeing_sessions
                    WHERE user_id = ?
                      AND session_summary IS NOT NULL
                      AND session_summary != ''
                    ORDER BY start_time DESC
                    LIMIT ?
                """, (user_id, limit))

                summaries = [
                    {
                        'session_id': row['id'],
                        'summary': self.db._maybe_decrypt(row['session_summary'])
                            if hasattr(self.db, '_maybe_decrypt') else row['session_summary'],
                        'mood_progression': row['mood_progression'],
                        'start_time': row['start_time'],
                        'end_time': row['end_time'],
                    }
                    for row in cursor.fetchall()
                ]
                logger.info(f"✅ Loaded {len(summaries)} session summaries")
                return summaries

        except Exception as e:
            logger.warning(f"⚠️ Failed to load session summaries: {e}")
            return []

    def _load_user_insights(self, user_id: str) -> List[Dict[str, Any]]:
        """Load user insights from canonical ``wellbeing_insights`` table.

        Schema (see :mod:`psychological_user_insight_extractor`):
        ``insight_type, category, value, confidence, temporal_context,
        created_at, mention_count``.
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT insight_type, category, value, confidence,
                           temporal_context, created_at,
                           COALESCE(mention_count, 1) AS mention_count
                    FROM wellbeing_insights
                    WHERE user_id = ?
                    ORDER BY confidence DESC, mention_count DESC
                    LIMIT 20
                """, (user_id,))

                insights = [
                    {
                        'type': row['insight_type'] or 'general',
                        'category': row['category'] or 'personality',
                        'title': row['category'] or 'Insight',
                        'description': row['value'] or '',
                        'confidence': row['confidence'] or 0.5,
                        'mention_count': row['mention_count'],
                        'temporal_context': row['temporal_context'],
                        'therapeutic_relevance': 'medium',
                    }
                    for row in cursor.fetchall()
                ]
                logger.info(f"✅ Loaded {len(insights)} user insights")
                return insights

        except Exception as e:
            logger.warning(f"⚠️ Failed to load user insights: {e}")
            return []

    def _build_synthesis_prompt(self, user_id: str,
                               kg_triples: List[Dict],
                               session_summaries: List[Dict],
                               user_insights: List[Dict],
                               *,
                               kg_limit: int = 100,
                               session_limit: int = 10,
                               insight_limit: int = 20,
                               summary_char_limit: int = 1000) -> str:
        """Build LLM synthesis prompt"""
        
        # Format KG triples
        kg_text = "\n".join([
            f"- {t['subject']} {t['predicate']} {t['object']} (confidence: {t['confidence']:.2f})"
            for t in kg_triples[:kg_limit]
        ]) if kg_triples else "Keine KG-Triples vorhanden."
        
        # Format session summaries
        session_text = "\n".join([
            f"Session {i+1} ({str(s.get('start_time', ''))[:10]}):\n  "
            f"{self._truncate_text(s.get('summary', ''), summary_char_limit)}"
            + (f"\n  Stimmungsverlauf: {s['mood_progression']}" if s.get('mood_progression') else "")
            for i, s in enumerate(session_summaries[:session_limit])
        ]) if session_summaries else "Keine Session-Summaries vorhanden."
        
        # Format user insights
        insights_text = "\n".join([
            f"- [{ins['category']}] {ins['title']}: {ins['description']} (confidence: {ins['confidence']:.2f})"
            for ins in user_insights[:insight_limit]
        ]) if user_insights else "Keine User-Insights vorhanden."
        
        prompt = f"""Du bist ein erfahrener Psychotherapeut, der ein ganzheitliches psychologisches Profil erstellt.

INPUT-DATEN:

1. KNOWLEDGE GRAPH TRIPLES (User-Fakten):
{kg_text}

2. SESSION SUMMARIES (Verlauf):
{session_text}

3. USER INSIGHTS (tiefe Persönlichkeitsanalyse):
{insights_text}

AUFGABE:
Synthetisiere ein **holistisches psychologisches Profil** dieser Person mit:
1. Kern-Persönlichkeit (stabil über Zeit)
2. Aktuelle emotionale/mentale Zustände (veränderlich)
3. Beziehungsmuster (Familie, Freunde, Partner)
4. Ziele und Wachstumsbereiche
5. Bewältigungsstrategien und Ressourcen
6. Therapeutisch relevante Muster

REGELN:
- Nur evidenzbasierte Aussagen (aus den Input-Daten!)
- Keine Spekulationen oder Annahmen
- Confidence-Score für jede Aussage (0.0-1.0)
- Respektiere die Komplexität und Würde der Person
- Keine Crisis-Detection (wird separat gehandhabt)
- Wenn Daten fehlen, gib niedrige Confidence und lasse Felder leer

OUTPUT FORMAT (STRICT JSON):
{{
  "core_personality": {{
    "traits": ["trait1", "trait2"],
    "communication_style": "beschreibung",
    "decision_making": "beschreibung",
    "confidence": 0.85
  }},
  "current_state": {{
    "emotional_tone": "beschreibung",
    "stress_level": "low/medium/high",
    "life_phase": "beschreibung",
    "primary_concerns": ["concern1", "concern2"],
    "confidence": 0.75
  }},
  "relationships": {{
    "family_dynamics": {{
      "members": ["Person1", "Person2"],
      "patterns": ["pattern1"],
      "current_concerns": ["concern1"],
      "confidence": 0.80
    }},
    "social_style": "beschreibung",
    "attachment_patterns": "beschreibung",
    "confidence": 0.70
  }},
  "goals_and_growth": {{
    "current_goals": ["goal1"],
    "growth_areas": ["area1"],
    "progress_indicators": ["indicator1"],
    "confidence": 0.65
  }},
  "coping_and_resources": {{
    "strategies": ["strategy1"],
    "strengths": ["strength1"],
    "support_systems": ["support1"],
    "confidence": 0.70
  }},
  "therapeutic_focus": {{
    "priority_areas": ["area1"],
    "intervention_suggestions": ["suggestion1"],
        "progress_markers": ["marker1"],
        "confidence": 0.70
  }},
  "overall_confidence": 0.75
}}

WICHTIG: Nur valides JSON zurückgeben, keine zusätzlichen Kommentare!"""
        
        return prompt
    
    def _build_merge_prompt(self, user_id: str,
                           previous_profile: 'WellbeingProfile',
                           kg_triples: List[Dict],
                           session_summaries: List[Dict],
                           user_insights: List[Dict],
                           *,
                           kg_limit: int = 100,
                           session_limit: int = 10,
                           insight_limit: int = 20,
                           summary_char_limit: int = 1000) -> str:
        """Build LLM delta-merge prompt: update existing profile with NEW data only.
        
        This mirrors how a therapist updates their case formulation after each session:
        - Stable traits are preserved (not re-derived every time)
        - Dynamic states (mood, stress) are updated
        - Contradictions between old and new observations are flagged
        - Temporal evolution is tracked
        """
        # Serialize existing profile for the LLM
        prev_json = json.dumps(previous_profile.to_context_dict(), indent=2, ensure_ascii=False)
        
        # Format new data (same formatters as _build_synthesis_prompt)
        kg_text = "\n".join([
            f"- {t['subject']} {t['predicate']} {t['object']} (confidence: {t['confidence']:.2f})"
            for t in kg_triples[:kg_limit]
        ]) if kg_triples else "Keine neuen KG-Triples."
        
        session_text = "\n".join([
            f"Session {i+1} ({str(s.get('start_time', ''))[:10]}):\n  "
            f"{self._truncate_text(s.get('summary', ''), summary_char_limit)}"
            + (f"\n  Stimmungsverlauf: {s['mood_progression']}" if s.get('mood_progression') else "")
            for i, s in enumerate(session_summaries[:session_limit])
        ]) if session_summaries else "Keine neuen Session-Summaries."
        
        insights_text = "\n".join([
            f"- [{ins['category']}] {ins['title']}: {ins['description']} (confidence: {ins['confidence']:.2f})"
            for ins in user_insights[:insight_limit]
        ]) if user_insights else "Keine neuen Insights."
        
        prompt = f"""Du bist ein erfahrener Psychotherapeut, der sein bestehendes Fallverständnis aktualisiert.

BESTEHENDES PROFIL (Version {previous_profile.version}, zuletzt aktualisiert: {previous_profile.updated_at}):
{prev_json}

NEUE BEOBACHTUNGEN SEIT DER LETZTEN ANALYSE:

1. NEUE KG-TRIPLES:
{kg_text}

2. NEUE SESSION-SUMMARIES:
{session_text}

3. NEUE INSIGHTS:
{insights_text}

AUFGABE:
Aktualisiere das bestehende Profil basierend auf den NEUEN Daten. Gehe dabei vor wie ein Therapeut bei der Fallkonzept-Aktualisierung:

1. **Stabile Traits beibehalten**: Persönlichkeitsmerkmale ändern sich selten. Übernimm sie, es sei denn neue Evidenz widerspricht klar.
2. **Dynamische Zustände aktualisieren**: Emotionale Verfassung, Stresslevel, aktuelle Sorgen — diese verändern sich zwischen Sitzungen.
3. **Widersprüche dokumentieren**: Wenn neue Beobachtungen dem alten Profil widersprechen, beschreibe den Widerspruch und justiere die Confidence herunter.
4. **Entwicklung verfolgen**: Notiere Veränderungen über die Zeit (z.B. "Stresslevel war hoch, hat sich reduziert").
5. **Neue Erkenntnisse integrieren**: Füge neue Beziehungsmuster, Ziele, Bewältigungsstrategien hinzu.
6. **Confidence anpassen**: Erhöhe Confidence bei bestätigter Evidenz, senke sie bei Widersprüchen.

REGELN:
- Nur evidenzbasierte Aussagen
- Keine Spekulationen
- Bestehende Informationen NICHT verwerfen, nur ergänzen oder korrigieren
- Bei fehlenden neuen Daten: Bestehendes Profil weitgehend unverändert lassen

OUTPUT FORMAT (STRICT JSON — gleiches Format wie das bestehende Profil):
{{{{
  "core_personality": {{{{
    "traits": ["trait1", "trait2"],
    "communication_style": "beschreibung",
    "decision_making": "beschreibung",
    "confidence": 0.85
  }}}},
  "current_state": {{{{
    "emotional_tone": "beschreibung",
    "stress_level": "low/medium/high",
    "life_phase": "beschreibung",
    "primary_concerns": ["concern1", "concern2"],
    "confidence": 0.75
  }}}},
  "relationships": {{{{
    "family_dynamics": {{{{
      "members": ["Person1", "Person2"],
      "patterns": ["pattern1"],
      "current_concerns": ["concern1"],
      "confidence": 0.80
    }}}},
    "social_style": "beschreibung",
    "attachment_patterns": "beschreibung",
    "confidence": 0.70
  }}}},
  "goals_and_growth": {{{{
    "current_goals": ["goal1"],
    "growth_areas": ["area1"],
    "progress_indicators": ["indicator1"],
    "confidence": 0.65
  }}}},
  "coping_and_resources": {{{{
    "strategies": ["strategy1"],
    "strengths": ["strength1"],
    "support_systems": ["support1"],
    "confidence": 0.70
  }}}},
  "therapeutic_focus": {{{{
    "priority_areas": ["area1"],
    "intervention_suggestions": ["suggestion1"],
        "progress_markers": ["marker1"],
        "confidence": 0.70
  }}}},
  "overall_confidence": 0.75
}}}}

WICHTIG: Nur valides JSON zurückgeben, keine zusätzlichen Kommentare!"""
        
        return prompt
    
    def _call_llm_for_synthesis(self, prompt: str, max_output_tokens: int) -> str:
        """Call LLM for profile synthesis.

        Uses the canonical ``ModelLoader.generate_response`` API — both the
        production loader (``scripts/model_loader.py``) and the test mock
        (``model_loader.py``) implement it with the same keyword contract,
        so this single call works in every environment.
        """
        grammar = None
        if self._supports_grammar_argument(self.model_loader):
            grammar = self._compile_profile_json_grammar()
        else:
            logger.warning(
                "⚠️ ModelLoader unterstützt kein `grammar`-Argument — constrained decoding deaktiviert"
            )

        request_kwargs: Dict[str, Any] = {
            'messages': [{"role": "user", "content": prompt}],
            'temperature': 0.2,
            'max_tokens': max_output_tokens,
            'stop': ['```'],
        }
        if grammar is not None:
            request_kwargs['grammar'] = grammar

        response: Any = self.model_loader.generate_response(**request_kwargs)
        return str(response) if response is not None else ""
    
    def _parse_llm_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse and validate LLM response"""
        try:
            if not response or not response.strip():
                logger.error("❌ Empty LLM response")
                return None

            parsed = parse_llm_json(
                response=response,
                schema_validator=self._validate_profile_schema,
                default_on_error=None,
                debug=False,
            )

            if not isinstance(parsed, dict):
                logger.error(f"❌ Parsed JSON is not a dict: {type(parsed)}")
                return None

            valid, reason = self.validate_profile_payload(parsed)
            if not valid:
                logger.error(f"❌ Parsed JSON failed profile payload validation: {reason}")
                return None

            return parsed
            
        except Exception as e:
            logger.error(f"❌ JSON parsing failed: {e}")
            logger.debug(f"Raw response: {response[:500]}...")
            return None
    
    def _create_profile_object(self, user_id: str, profile_data: Dict[str, Any],
                              kg_count: int, session_count: int, insight_count: int,
                              synthesis_prompt: str,
                              version: int = 1,
                              synthesis_type: str = 'full') -> WellbeingProfile:
        """Create WellbeingProfile object from parsed data"""
        import hashlib
        
        now = datetime.now(timezone.utc).isoformat()
        
        # Calculate prompt hash for version tracking
        prompt_hash = hashlib.md5(synthesis_prompt.encode()).hexdigest()
        
        profile = WellbeingProfile(
            user_id=user_id,
            version=version,
            synthesis_type=synthesis_type,
            core_personality=profile_data.get('core_personality', {}),
            current_state=profile_data.get('current_state', {}),
            relationships=profile_data.get('relationships', {}),
            goals_and_growth=profile_data.get('goals_and_growth', {}),
            coping_and_resources=profile_data.get('coping_and_resources', {}),
            therapeutic_focus=profile_data.get('therapeutic_focus', {}),
            overall_confidence=self._clamp_confidence(profile_data.get('overall_confidence', 0.0)),
            data_sources={
                'kg_triples_used': kg_count,
                'sessions_used': session_count,
                'insights_used': insight_count
            },
            synthesis_model=getattr(self.model_loader, 'model_name', 'unknown'),
            synthesis_prompt_hash=prompt_hash,
            created_at=now,
            updated_at=now
        )
        
        return profile


# Factory function
def create_profile_synthesizer(psychological_db: Any, model_loader: Any) -> "ProfileSynthesizer":
    """Create ProfileSynthesizer instance"""
    return ProfileSynthesizer(psychological_db, model_loader)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("🧠 Psychological Profile Synthesizer")
    print("=" * 60)
    print("✅ LLM-based holistic profile synthesis")
    print("📋 Features:")
    print("   • Aggregates KG + Sessions + Insights")
    print("   • Evidence-based only (no speculation)")
    print("   • Confidence scoring")
    print("   • JSON-structured output")
