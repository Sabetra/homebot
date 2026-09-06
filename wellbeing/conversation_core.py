"""
Therapeutischer Kern-Modul (SOTA 2025/2026)
==========================================
Bündelt alle SOTA-Features für psychologische Unterstützung:

#1  PsychResponseValidator    — Post-Generation Antwort-Qualitätsprüfung
#2  PsychGroundingChecker     — Grounding/Halluzinationsschutz im Psycho-Pfad
#3  ScreeningInstruments      — Wellbeing-Selbstchecks: MoodCheck, CalmCheck (nicht-klinisch)
#4  CumulativeRiskScorer      — Kumulative Risikobewertung über Turns/Sessions
#6  AllianceTracker           — Therapeutische Allianz-Tracking
#7  TechniqueLibrary          — Evidenzbasierte Technik-Auswahl
#8  OutcomeMonitor            — Outcome-Monitoring (Mikro-Assessments)
#9  WellbeingRAGBootstrapper     — Psychologie-spezifischer RAG-Korpus
#10 EmotionInterventionMapper — Emotion→Intervention-Zuordnung
#12 CaseFormulator            — Case Formulation (4P-Modell)
#17 HomeworkManager           — Zwischen-Session-Aufgaben
#18 RuptureDetector           — Allianz-Ruptur-Erkennung
"""

import logging
import json
import hashlib
import re
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# #1 — PsychResponseValidator: Post-Generation Antwort-Qualitätsprüfung
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """Ergebnis der Antwort-Validierung."""
    is_valid: bool
    empathy_score: float        # 0.0–1.0
    safety_passed: bool
    repetition_score: float     # 0.0 = keine Repetition, 1.0 = identisch
    length_adequate: bool
    boundary_violations: List[str]
    suggestions: List[str]


class PsychResponseValidator:
    """
    Prüft LLM-Antworten BEVOR sie an den User gehen.
    
    Checks:
    1. Empathie-Marker (Paraphrasierung, Validierung, Fragen)
    2. Repetitions-Check (Cosine vs. letzte Antworten)
    3. Safety-Filter (Diagnosen, Medikamente, Grenzüberschreitungen)
    4. Längen-Adäquanz (nicht zu knapp, nicht zu lang)
    5. Boundary-Violations (explizite Grenzverletzungen)
    """

    # Empathie-Marker (deutsch): Phrasen, die empathisches Verhalten anzeigen
    _EMPATHY_MARKERS = [
        # Paraphrasierung
        r'(?:wenn ich .+ richtig verstehe|es klingt|ich (höre|verstehe|merke|spüre))',
        r'(?:korrigier\w* mich|das scheint|es wirkt|es fühlt sich an)',
        # Validierung
        r'(?:das (ist|klingt|muss) .*(schwer|belastend|schmerzhaft|verständlich|natürlich|mutig))',
        r'(?:ich kann .*(verstehen|nachvollziehen|mir vorstellen))',
        # Offene Fragen
        r'(?:magst du|möchtest du|kannst du|wie (fühlst|geht|erlebst) du)',
        r'(?:was (denkst|meinst|brauchst|wünschst) du)',
        r'(?:erzähl\w* mir|sag\w* mir|beschreib)',
    ]

    # Boundary-Violations: Muster, die NIEMALS in der Antwort vorkommen dürfen
    _BOUNDARY_PATTERNS = [
        (r'(?:du hast|sie haben|ich diagnostiziere)\s+(?:eine?n?|die|der)\s+(?:depression|borderline|ptbs|angststörung|bipolar|schizophreni|persönlichkeitsstörung|adhs|autismus)',
         'Diagnose gestellt'),
        (r'(?:empfehle ich|solltest du|nehmen sie)\s+(?:sertralin|fluoxetin|citalopram|venlafaxin|mirtazapin|quetiapin|lithium|diazepam|lorazepam|medikament)',
         'Medikamenten-Empfehlung'),
        (r'(?:du (?:brauchst|musst)|nehmen sie)\s+\d+\s*(?:mg|milligramm|tablette)',
         'Dosierungs-Empfehlung'),
        (r'(?:als\s+(?:therapeut|psychologe|psychiater|arzt)\s+(?:muss ich|sage ich|Rate ich))',
         'Falsche Rollenbehauptung'),
    ]

    def __init__(self, recent_responses: Optional[List[str]] = None) -> None:
        self._recent_responses: List[str] = recent_responses or []
        self._max_history = 10
        self._empathy_patterns = [re.compile(p, re.IGNORECASE) for p in self._EMPATHY_MARKERS]
        self._boundary_patterns = [(re.compile(p, re.IGNORECASE), label) for p, label in self._BOUNDARY_PATTERNS]

    def validate(self, response: str, user_message: str = "") -> ValidationResult:
        """Validiert eine LLM-Antwort vor der Auslieferung."""
        if not response or not response.strip():
            return ValidationResult(
                is_valid=False, empathy_score=0.0, safety_passed=False,
                repetition_score=0.0, length_adequate=False,
                boundary_violations=["Leere Antwort"], suggestions=["Antwort neu generieren"]
            )

        empathy = self._check_empathy(response)
        safety, violations = self._check_safety(response)
        repetition = self._check_repetition(response)
        length_ok = self._check_length(response, user_message)

        suggestions: List[str] = []
        if empathy < 0.3:
            suggestions.append("Antwort fehlt empathische Validierung des User-Erlebens")
        if repetition > 0.85:
            suggestions.append("Antwort ist fast identisch mit einer früheren Antwort")
        if not length_ok:
            word_count = len(response.split())
            if word_count < 20:
                suggestions.append("Antwort zu kurz für therapeutischen Kontext")
            elif word_count > 500:
                suggestions.append("Antwort möglicherweise zu lang")

        is_valid = safety and empathy >= 0.15 and repetition < 0.92 and length_ok

        # Antwort in History speichern
        self._recent_responses.append(response)
        if len(self._recent_responses) > self._max_history:
            self._recent_responses.pop(0)

        return ValidationResult(
            is_valid=is_valid,
            empathy_score=empathy,
            safety_passed=safety,
            repetition_score=repetition,
            length_adequate=length_ok,
            boundary_violations=violations,
            suggestions=suggestions
        )

    def _check_empathy(self, response: str) -> float:
        """Misst Empathie-Level anhand von Marker-Patterns."""
        hits = sum(1 for p in self._empathy_patterns if p.search(response))
        return min(1.0, hits / max(1, len(self._empathy_patterns) * 0.3))

    def _check_safety(self, response: str) -> Tuple[bool, List[str]]:
        """Prüft auf Boundary-Violations."""
        violations: List[str] = []
        for pattern, label in self._boundary_patterns:
            if pattern.search(response):
                violations.append(label)
        return len(violations) == 0, violations

    def _check_repetition(self, response: str) -> float:
        """Prüft Repetitionsgrad gegen letzte Antworten (Jaccard-Similarity)."""
        if not self._recent_responses:
            return 0.0
        resp_words = set(response.lower().split())
        max_sim = 0.0
        for prev in self._recent_responses[-5:]:
            prev_words = set(prev.lower().split())
            if not resp_words or not prev_words:
                continue
            intersection = resp_words & prev_words
            union = resp_words | prev_words
            sim = len(intersection) / len(union) if union else 0.0
            max_sim = max(max_sim, sim)
        return max_sim

    def _check_length(self, response: str, user_message: str) -> bool:
        """Prüft ob die Antwortlänge angemessen ist."""
        word_count = len(response.split())
        user_words = len(user_message.split()) if user_message else 10
        # Therapeutische Antworten: mindestens 15 Wörter, max 500
        # Bei kurzen User-Messages (Grüße etc.) darf Antwort kürzer sein
        min_words = 10 if user_words < 5 else 15
        return min_words <= word_count <= 500


# ═══════════════════════════════════════════════════════════════════════
# #2 — PsychGroundingChecker: Grounding im Psycho-Pfad
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class GroundingResult:
    """Ergebnis der Grounding-Prüfung."""
    is_grounded: bool
    grounding_score: float      # 0.0–1.0
    ungrounded_claims: List[str]
    evidence_used: List[str]


class PsychGroundingChecker:
    """
    Leichtgewichtiger Grounding-Check für therapeutische Antworten.
    
    Prüft ob therapeutische Aussagen (Techniken, Strategien, Fakten)
    durch RAG-Evidenz oder etablierte Verfahren gestützt sind.
    """

    # Etablierte therapeutische Konzepte die immer grounded sind
    _ESTABLISHED_TECHNIQUES = {
        'atemübung', 'atemtechnik', 'tiefes atmen', 'bauchatmung', '4-7-8',
        'progressive muskelentspannung', 'muskelrelaxation', 'jacobson',
        'achtsamkeit', 'mindfulness', 'body scan', 'meditation',
        'gedankenprotokoll', 'gedankentagebuch', 'thought record',
        'kognitive umstrukturierung', 'cognitive restructuring',
        'verhaltensexperiment', 'behavioral experiment',
        'exposition', 'konfrontation', 'exposure',
        'tagebuch', 'journaling', 'dankbarkeit', 'gratitude',
        'selbstmitgefühl', 'self-compassion',
        'problemlösung', 'problem solving',
        'ressourcenaktivierung', 'stärken',
        'soziale unterstützung', 'social support',
        'schlafhygiene', 'sleep hygiene',
        'bewegung', 'sport', 'spaziergang', 'exercise',
        'entspannung', 'relaxation',
        'tipp', 'distress tolerance', 'emotionsregulation',
        'werteklärung', 'values', 'akzeptanz', 'acceptance',
        'defusion', 'committed action',
        'motivierende gesprächsführung', 'motivational interviewing',
        'paraphrasieren', 'aktives zuhören', 'spiegeln',
        'validierung', 'normalisierung',
    }

    # Patterns die eine therapeutische Behauptung anzeigen
    _CLAIM_PATTERNS = [
        r'(?:studien|forschung|wissenschaft)\s+(?:zeig|beleg|beweise)',
        r'(?:es ist (?:bewiesen|erwiesen|belegt))',
        r'(?:laut (?:experten|forschung|studien))',
        r'(?:\d+(?:\s*%|\s*prozent)\s+(?:der|aller)\s+(?:menschen|patienten|betroffenen))',
    ]

    def __init__(self) -> None:
        self._claim_patterns = [re.compile(p, re.IGNORECASE) for p in self._CLAIM_PATTERNS]

    def check_grounding(
        self,
        response: str,
        rag_evidence: Optional[List[Dict[str, Any]]] = None
    ) -> GroundingResult:
        """
        Prüft ob therapeutische Aussagen in der Antwort grounded sind.
        
        Args:
            response: Die LLM-Antwort
            rag_evidence: Optional RAG-Evidenz (Chunks mit content + score)
        """
        evidence_texts = [e.get('content', '') for e in (rag_evidence or []) if e.get('content')]

        # Extrahiere therapeutische Claims aus der Antwort
        ungrounded: List[str] = []
        for pattern in self._claim_patterns:
            for match in pattern.finditer(response):
                claim_context = response[max(0, match.start() - 40):match.end() + 60]
                # Prüfe ob der Claim durch Evidenz oder etablierte Technik gestützt ist
                if not self._claim_is_grounded(claim_context, evidence_texts):
                    ungrounded.append(claim_context.strip())

        # Prüfe ob empfohlene Techniken etabliert sind
        response_lower = response.lower()
        techniques_mentioned = [t for t in self._ESTABLISHED_TECHNIQUES if t in response_lower]
        evidence_used = techniques_mentioned[:5]

        total_claims = len(ungrounded) + len(techniques_mentioned)
        grounded_claims = len(techniques_mentioned)
        score = grounded_claims / max(1, total_claims)

        # Wenn keine expliziten Claims → high grounding (reine Empathie/Gesprächsführung)
        if total_claims == 0:
            score = 1.0

        return GroundingResult(
            is_grounded=len(ungrounded) == 0,
            grounding_score=score,
            ungrounded_claims=ungrounded,
            evidence_used=evidence_used
        )

    def _claim_is_grounded(self, claim: str, evidence_texts: List[str]) -> bool:
        """Prüft ob ein Claim durch Evidenz gestützt ist."""
        claim_lower = claim.lower()
        # Check gegen etablierte Techniken
        for technique in self._ESTABLISHED_TECHNIQUES:
            if technique in claim_lower:
                return True
        # Check gegen RAG-Evidenz (Wort-Overlap)
        claim_words = set(claim_lower.split())
        for evidence in evidence_texts:
            ev_words = set(evidence.lower().split())
            overlap = len(claim_words & ev_words) / max(1, len(claim_words))
            if overlap > 0.3:
                return True
        return False


# ═══════════════════════════════════════════════════════════════════════
# #3 — Wellbeing-Selbstchecks: MoodCheck, CalmCheck (nicht-klinisch)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ScreeningResult:
    """Ergebnis eines Wellbeing-Selbstchecks (reflexiv, nicht-klinisch)."""
    instrument: str         # z.B. "MoodCheck", "CalmCheck"
    total_score: int
    severity: str           # "gut ausgeglichen", "geprägt", "gefordert", "stark gefordert"
    risk_level: str         # "niedrig", "mittel", "hoch"
    item_scores: Dict[str, int]
    interpretation: str
    recommendation: str
    timestamp: datetime = field(default_factory=datetime.now)


class ScreeningInstruments:
    """
    Nicht-klinische Wellbeing-Selbstchecks (deutsch).
    
    Reflexive Wohlbefindens-Fragen zu Stimmung, Energie und innerer Ruhe —
    bewusst NICHT klinische Skalen, keine Diagnose, keine Behandlungsindikation.
    
    Implementiert:
    - MoodCheck — Stimmung & Energie (letzte zwei Wochen)
    - CalmCheck — innere Ruhe & Anspannung
    
    Positionierung: Selbstreflexion & Achtsamkeit. Bei anhaltender Belastung
    wird behutsam auf professionelle Unterstützung hingewiesen. Akute Krisen
    werden separat erkannt (Krisen-Hotline) — nicht über diesen Selbstcheck.
    
    Nutzung: Wird triggerbasiert eingesetzt, nicht bei jeder Nachricht.
    """

    # MoodCheck: 9 Items, Skala 0-3 (selten, manchmal, oft, fast immer)
    MOOD_ITEMS = {
        'mood1': 'Wie oft hattest du in den letzten zwei Wochen Freude an Dingen, die dir wichtig sind?',
        'mood2': 'Wie gut kamst du in den letzten zwei Wochen in Schwung?',
        'mood3': 'Wie gut konntest du schlafen und dich erholen?',
        'mood4': 'Hattest du genug Energie für deinen Alltag?',
        'mood5': 'Wie oft fühltest du dich verbunden mit Menschen, die dir wichtig sind?',
        'mood6': 'Wie gut konntest du dich auf das konzentrieren, was du tust?',
        'mood7': 'Wie oft hast du in den letzten zwei Wochen ausgeglichen gefühlt?',
        'mood8': 'Wie gut bist du mit stressigen Situationen zurechtgekommen?',
        'mood9': 'Insgesamt: Wie gut fühlst du dich in deiner aktuellen Lebenssituation?',
    }

    MOOD_LEVELS = [
        (0, 4, 'gut ausgeglichen', 'niedrig'),
        (5, 9, 'geprägt', 'niedrig'),
        (10, 14, 'gefordert', 'mittel'),
        (15, 19, 'gefordert', 'mittel'),
        (20, 27, 'stark gefordert', 'hoch'),
    ]

    # CalmCheck: 7 Items, Skala 0-3 (selten, manchmal, oft, fast immer)
    CALM_ITEMS = {
        'calm1': 'Wie entspannt fühlst du dich im Alltag?',
        'calm2': 'Wie leicht fällt es dir, abzuschalten und zur Ruhe zu kommen?',
        'calm3': 'Wie oft spürst du innere Anspannung?',
        'calm4': 'Wie gut gelingt es dir, den Kopf frei zu bekommen?',
        'calm5': 'Wie oft fühlst du dich von deinen Gedanken mitgerissen?',
        'calm6': 'Wie leicht kannst du bei Anspannung wieder runterkommen?',
        'calm7': 'Wie verankert und stabil fühlst du dich in dir selbst?',
    }

    CALM_LEVELS = [
        (0, 4, 'gut ausgeglichen', 'niedrig'),
        (5, 9, 'geprägt', 'niedrig'),
        (10, 14, 'gefordert', 'mittel'),
        (15, 21, 'stark gefordert', 'hoch'),
    ]

    def score_mood(self, item_scores: Dict[str, int]) -> ScreeningResult:
        """Bewertet MoodCheck-Ergebnis (Stimmung & Energie, nicht-klinisch)."""
        total = sum(item_scores.get(k, 0) for k in self.MOOD_ITEMS)
        total = min(total, 27)

        level, support = 'gut ausgeglichen', 'niedrig'
        for low, high, lvl, sup in self.MOOD_LEVELS:
            if low <= total <= high:
                level, support = lvl, sup
                break

        interp_map = {
            'gut ausgeglichen': 'Deine Stimmung wirkt ausgeglichen. Das ist eine gute Basis — pfleg das, was gut läuft.',
            'geprägt': 'Du gibst an, dass die Stimmung teils durchwachsen war. Pausen und Selbstfürsorge können dir helfen.',
            'gefordert': 'Du bist in einer fordernden Phase. Selbstfürsorge und Austausch mit vertrauten Menschen sind sinnvoll.',
            'stark gefordert': 'Du gibst an, dass du in den letzten Wochen stark gefordert warst. Professionelle Unterstützung kann dir guttun.',
        }

        rec_map = {
            'niedrig': 'Weiter beobachten. Achtsamkeits- und Selbstfürsorge-Strategien anbieten.',
            'mittel': 'Austausch mit vertrauten Menschen und ggf. professionelle Unterstützung anbieten.',
            'hoch': 'Professionelle Unterstützung anbieten und Krisen-Ressourcen (Hotline) bereithalten.',
        }

        return ScreeningResult(
            instrument='MoodCheck',
            total_score=total,
            severity=level,
            risk_level=support,
            item_scores=item_scores,
            interpretation=interp_map.get(level, ''),
            recommendation=rec_map.get(support, '')
        )

    def score_calm(self, item_scores: Dict[str, int]) -> ScreeningResult:
        """Bewertet CalmCheck-Ergebnis (innere Ruhe & Anspannung, nicht-klinisch)."""
        total = sum(item_scores.get(k, 0) for k in self.CALM_ITEMS)
        total = min(total, 21)

        level, support = 'gut ausgeglichen', 'niedrig'
        for low, high, lvl, sup in self.CALM_LEVELS:
            if low <= total <= high:
                level, support = lvl, sup
                break

        interp_map = {
            'gut ausgeglichen': 'Du fühlst dich weitgehend entspannt und ruhig. Das ist eine gute Ausgangslage.',
            'geprägt': 'Du spürst teils innere Anspannung. Atemübungen und bewusste Pausen können dir helfen.',
            'gefordert': 'Du bist in einer anstrengenden Phase. Entspannungstechniken und Selbstfürsorge sind sinnvoll.',
            'stark gefordert': 'Du gibst an, dass Anspannung und innere Unruhe aktuell viel Raum einnehmen. Professionelle Unterstützung kann dir guttun.',
        }

        return ScreeningResult(
            instrument='CalmCheck',
            total_score=total,
            severity=level,
            risk_level=support,
            item_scores=item_scores,
            interpretation=interp_map.get(level, ''),
            recommendation='Achtsamkeits- und Entspannungstechniken anbieten.' if support == 'niedrig' else 'Professionelle Unterstützung anbieten.'
        )

    def generate_screening_prompt(self, instrument: str) -> str:
        """Generiert einen natürlich klingenden LLM-Prompt für einen Wellbeing-Selbstcheck."""
        if instrument == 'MoodCheck':
            items = self.MOOD_ITEMS
            scale_info = "0 = selten, 1 = manchmal, 2 = oft, 3 = fast immer"
            intro = "Stimmung oder anhaltende Erschöpfung"
        elif instrument == 'CalmCheck':
            items = self.CALM_ITEMS
            scale_info = "0 = selten, 1 = manchmal, 2 = oft, 3 = fast immer"
            intro = "innere Anspannung oder Unruhe"
        else:
            return ""

        return f"""<wellbeing_context>
Der User hat Signale gezeigt, die auf {intro} hindeuten.
Führe BEHUTSAM einen reflexiven Wellbeing-Selbstcheck durch — NICHT als Diagnose.
Stelle die Fragen NATÜRLICH im Gespräch, nicht als starren Fragebogen.
Erkläre ZUERST, warum du fragst, und bitte um Einverständnis.
Stell klar, dass es sich um eine Selbstreflexion und keine medizinische Bewertung handelt.

Bewertungsskala: {scale_info}

Fragen (in natürlicher Reihenfolge und einfühlsamer Sprache):
{chr(10).join(f"- {v}" for v in items.values())}
</wellbeing_context>"""

    def estimate_scores_from_conversation(
        self, 
        instrument: str,
        conversation_text: str,
        model_loader: Any = None
    ) -> Optional[ScreeningResult]:
        """
        Schätzt Wellbeing-Selbstcheck-Scores aus dem bisherigen Gesprächsverlauf per LLM.
        
        Ermöglicht eine passive Wohlbefindens-Einschätzung ohne expliziten Fragebogen,
        basierend auf dem, was der User bereits erzählt hat.
        """
        if model_loader is None:
            return None

        if instrument == 'MoodCheck':
            items = self.MOOD_ITEMS
        elif instrument == 'CalmCheck':
            items = self.CALM_ITEMS
        else:
            return None

        prompt = f"""Analysiere den folgenden Gesprächsverlauf und schätze für jedes Item
des {instrument} einen Score basierend auf den Äußerungen des Users.
Wenn keine Information vorliegt, setze 0.

Gesprächsverlauf:
{conversation_text[-3000:]}

Items:
{json.dumps(items, ensure_ascii=False, indent=2)}

Antworte NUR mit validem JSON im Format:
{{"scores": {{{", ".join(f'"{k}": 0' for k in items)}}}, "confidence": 0.5, "reasoning": "kurze Begründung"}}"""

        try:
            messages = [{'role': 'user', 'content': prompt}]
            prompt_tokens = model_loader.count_messages_tokens(messages) if hasattr(model_loader, 'count_messages_tokens') else max(1, len(prompt) // 4)
            n_ctx = model_loader.get_max_context_tokens() if hasattr(model_loader, 'get_max_context_tokens') else 16384
            available = max(256, int(n_ctx) - int(prompt_tokens) - 64)
            adaptive_max_tokens = min(2048, max(896, int(prompt_tokens * 0.6)), available)

            response = model_loader.generate_response(
                messages=messages,
                max_tokens=adaptive_max_tokens,
                temperature=0.2
            )
            # Parse JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                scores = data.get('scores', {})
                if instrument == 'MoodCheck':
                    return self.score_mood(scores)
                elif instrument == 'CalmCheck':
                    return self.score_calm(scores)
        except Exception as e:
            logger.warning(f"Wellbeing-Estimation fehlgeschlagen: {e}")
        return None

    def suggest_periodic_screening(self, user_id: str) -> Optional[str]:
        """
        Prüft ob ein periodischer Wellbeing-Selbstcheck fällig ist (alle ~14 Tage).
        
        Returns:
            Selbstcheck-Name ('MoodCheck', 'CalmCheck') wenn fällig, sonst None.
        """
        # In-memory state: Letzte Check-Zeitpunkte pro User
        if not hasattr(self, '_last_screening'):
            self._last_screening: Dict[str, datetime] = {}

        key = f"{user_id}"
        last = self._last_screening.get(key)
        if last and (datetime.now() - last).days < 14:
            return None

        # Abwechselnd MoodCheck und CalmCheck
        if not hasattr(self, '_screening_counter'):
            self._screening_counter: Dict[str, int] = {}
        count = self._screening_counter.get(user_id, 0)
        instrument = 'MoodCheck' if count % 2 == 0 else 'CalmCheck'

        self._screening_counter[user_id] = count + 1
        self._last_screening[key] = datetime.now()

        return instrument

    def estimate_from_text(self, text: str, instrument: str = 'mood') -> Optional[Dict[str, Any]]:
        """
        Schnelle heuristische Einschätzung basierend auf Keyword-Analyse.
        
        Kein LLM nötig — nur lexikonbasierte Signale für Wohlbefinden & Belastung.
        Erkennt u.a. Krisen-Signale, auf die mit professionellen Hilfsangeboten reagiert wird.
        Für präzisere Ergebnisse: estimate_scores_from_conversation() mit model_loader.
        
        Returns:
            Dict mit 'estimated_level', 'signal_count', 'signals'
            (ggf. 'crisis_signal': True) oder None.
        """
        text_lower = text.lower()
        signals: List[str] = []

        norm = instrument.lower()
        if norm in ('mood', 'phq9', 'phq-9'):
            keywords = {
                'energie': ['keine energie', 'müde', 'erschöpft', 'kraftlos', 'antriebslos', 'kein schwung'],
                'stimmung': ['traurig', 'niedergeschlagen', 'hoffnungslos', 'deprimiert', 'gleichgültig', 'macht keinen spaß'],
                'schlaf': ['schlafe schlecht', 'nicht schlafen', 'schlaflos', 'zu viel schlaf', 'keine erholung'],
                'selbstwert': ['versager', 'wertlos', 'schuldig', 'enttäusch'],
                'konzentration': ['konzentrier', 'nicht fokus', 'abgelenkt', 'vergesslich'],
                'belastung': ['stress', 'überfordert', 'druck', 'angst'],
                'krisensignal': ['tot', 'sterben', 'umbringen', 'nicht mehr leben', 'leid zufügen', 'suizid'],
            }
        elif norm in ('calm', 'gad7', 'gad-7'):
            keywords = {
                'nervosität': ['nervös', 'ängstlich', 'angespannt', 'angst', 'unruhig'],
                'sorgen': ['sorgen', 'grübel', 'gedankenkarussell', 'nicht aufhören', 'was wenn'],
                'entspannung': ['nicht entspannen', 'anspannung', 'verkrampft', 'abspannen'],
                'rastlosigkeit': ['rastlos', 'still sitzen', 'ruhelos', 'getrieben', 'zappelig'],
                'reizbarkeit': ['gereizt', 'verärgert', 'wütend', 'aggressiv'],
                'angstgefühl': ['panik', 'schlimmes passier', 'katastroph', 'furcht'],
            }
        else:
            return None

        for category, kws in keywords.items():
            for kw in kws:
                if kw in text_lower:
                    signals.append(category)
                    break

        if not signals:
            return None

        count = len(signals)
        total = len(keywords)
        ratio = count / total

        if ratio >= 0.6:
            level = 'stark gefordert'
        elif ratio >= 0.4:
            level = 'gefordert'
        elif ratio >= 0.2:
            level = 'geprägt'
        else:
            level = 'gut ausgeglichen'

        result: Dict[str, Any] = {
            'estimated_level': level,
            'estimated_severity': level,  # Backward-Kompatibilität
            'signal_count': count,
            'total_items': total,
            'signals': signals,
            'instrument': instrument.upper(),
        }
        if 'krisensignal' in signals:
            result['crisis_signal'] = True
        return result

@dataclass
class CumulativeRisk:
    """Kumulatives Risiko-Assessment."""
    cumulative_score: float     # 0.0–1.0
    risk_level: str             # "niedrig", "mittel", "hoch", "akut"
    trend: str                  # "improving", "stable", "worsening"
    contributing_factors: List[str]
    recommended_action: str
    turn_scores: List[float]    # Zeitreihe der Einzelbewertungen


class CumulativeRiskScorer:
    """
    Aggregiert Risikobewertungen über mehrere Turns und Sessions.
    
    Kernprinzip: Einzelne Nachricht = "niedrig", aber kumulative Progression
    kann "hoch" sein (z.B. schleichende Verschlechterung).
    
    Signale:
    1. Einzelne Krisen-Bewertungen (aus crisis_detection_prompt)
    2. Mood-Trend (aus MoodProgressionTracker — bereits vorhanden!)
    3. KG-Triples mit Krisenbezug (aus DB)
    4. Response-Länge-Trend (kürzer = Desengagement)
    5. Valenz-Zeitreihe (Verschlechterung über Turns)
    """

    # Risk-Level → numerischer Score
    _RISK_MAP = {'niedrig': 0.1, 'low': 0.1, 'mittel': 0.4, 'medium': 0.4,
                 'hoch': 0.8, 'high': 0.8, 'akut': 1.0, 'critical': 1.0}

    def __init__(self) -> None:
        self._turn_scores: List[Tuple[datetime, float]] = []
        self._max_turns = 50

    def add_turn_assessment(
        self,
        risk_label: str,
        mood_valence: Optional[float] = None,
        crisis_indicators: bool = False,
        response_word_count: int = 0
    ) -> None:
        """Fügt eine Turn-Bewertung hinzu."""
        base = self._RISK_MAP.get(risk_label.lower(), 0.1)

        # Modifikatoren
        if crisis_indicators:
            base = max(base, 0.7)
        if mood_valence is not None and mood_valence < 0.2:
            base = min(1.0, base + 0.15)
        if response_word_count > 0 and response_word_count < 5:
            base = min(1.0, base + 0.1)  # Sehr kurze Antworten = Desengagement

        self._turn_scores.append((datetime.now(), base))
        if len(self._turn_scores) > self._max_turns:
            self._turn_scores.pop(0)

    def get_cumulative_risk(self) -> CumulativeRisk:
        """Berechnet kumulatives Risiko mit zeitgewichteter Aggregation."""
        if not self._turn_scores:
            return CumulativeRisk(0.0, 'niedrig', 'stable', [], 'Weiter beobachten.', [])

        scores = [s for _, s in self._turn_scores]
        now = datetime.now()

        # Exponentiell zeitgewichtete Aggregation (neuere Turns zählen mehr)
        weighted_sum = 0.0
        weight_total = 0.0
        for ts, score in self._turn_scores:
            age_minutes = max(1, (now - ts).total_seconds() / 60)
            weight = math.exp(-0.05 * age_minutes)  # Halbwertszeit ~14 Minuten
            weighted_sum += score * weight
            weight_total += weight

        cumulative = weighted_sum / max(0.01, weight_total)

        # Trend-Erkennung (letzte 5 vs. vorherige 5)
        if len(scores) >= 6:
            recent = sum(scores[-3:]) / 3
            earlier = sum(scores[-6:-3]) / 3
            if recent > earlier + 0.15:
                trend = 'worsening'
            elif recent < earlier - 0.15:
                trend = 'improving'
            else:
                trend = 'stable'
        else:
            trend = 'stable'

        # Contributing Factors
        factors: List[str] = []
        if any(s >= 0.7 for s in scores[-3:]):
            factors.append('Kürzliche Krisensignale erkannt')
        if trend == 'worsening':
            factors.append('Verschlechterungstrend über Zeit')
        if len(scores) >= 3 and all(s >= 0.3 for s in scores[-3:]):
            factors.append('Anhaltend erhöhtes Risikoniveau')

        # Risk Level
        if cumulative >= 0.7 or (trend == 'worsening' and cumulative >= 0.5):
            risk_level = 'hoch'
            action = 'DRINGEND: Proaktive Krisenintervention einleiten. Notfallkontakte anbieten.'
        elif cumulative >= 0.4:
            risk_level = 'mittel'
            action = 'Erhöhte Aufmerksamkeit. Professionelle Hilfe aktiv vorschlagen.'
        else:
            risk_level = 'niedrig'
            action = 'Weiter beobachten. Standard-Unterstützung fortsetzen.'

        return CumulativeRisk(
            cumulative_score=round(cumulative, 3),
            risk_level=risk_level,
            trend=trend,
            contributing_factors=factors,
            recommended_action=action,
            turn_scores=scores[-10:]
        )


# ═══════════════════════════════════════════════════════════════════════
# #6 — AllianceTracker: Therapeutische Allianz-Tracking
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class AllianceScore:
    """Therapeutische Allianz-Bewertung."""
    score: float                # 0.0–1.0 (0 = Ruptur, 1 = starke Allianz)
    engagement_level: str       # "hoch", "mittel", "niedrig", "desengagement"
    trend: str                  # "improving", "stable", "declining"
    signals: Dict[str, float]   # Einzelsignale
    alert: Optional[str]        # Warnung bei Allianz-Problem


class AllianceTracker:
    """
    Trackt therapeutische Allianz über Proxy-Signale.
    
    Signale:
    1. Antwortlänge-Trend (kürzer = Desengagement)
    2. Sentiment zum Bot (explizites Feedback, Tonfall)
    3. Engagement (Fragen stellen, Details teilen, kooperativ)
    4. Response-Latenz-Proxy (kurze Antworten schnell = Abfertigung)
    """

    # Positive Engagement-Marker
    _ENGAGEMENT_PATTERNS = [
        r'(?:danke|vielen dank|das hilft|guter punkt|stimmt|ja.*genau)',
        r'(?:ich (?:denke|glaube|finde|meine) auch)',
        r'(?:das (?:ist|klingt) (?:interessant|hilfreich|gut|sinnvoll))',
        r'(?:erzähl mir mehr|wie meinst du|kannst du.*erklären)',
    ]

    # Desengagement-Marker
    _DISENGAGEMENT_PATTERNS = [
        r'(?:ja|nein|ok|okay|hmm|achso|egal|weiß nicht)$',
        r'(?:du verstehst (?:das|mich) nicht|das hilft (?:nicht|mir nicht))',
        r'(?:lass (?:mal|gut|es)|vergiss es|bringt nichts)',
        r'(?:immer das (?:gleiche|selbe)|du wiederholst dich)',
    ]

    def __init__(self) -> None:
        self._history: List[Dict[str, Any]] = []
        self._engagement_pats = [re.compile(p, re.IGNORECASE) for p in self._ENGAGEMENT_PATTERNS]
        self._disengagement_pats = [re.compile(p, re.IGNORECASE) for p in self._DISENGAGEMENT_PATTERNS]

    def record_interaction(self, user_message: str, bot_response: str) -> AllianceScore:
        """Zeichnet eine Interaktion auf und berechnet den aktuellen Allianz-Score."""
        user_words = len(user_message.split())
        engagement_hits = sum(1 for p in self._engagement_pats if p.search(user_message))
        disengage_hits = sum(1 for p in self._disengagement_pats if p.search(user_message))

        # Signal: Antwortlänge (normalisiert)
        length_signal = min(1.0, user_words / 30)  # 30 Wörter = "volle Engagement"

        # Signal: Engagement vs. Disengagement
        if engagement_hits > 0 and disengage_hits == 0:
            affect_signal = 0.8
        elif disengage_hits > 0 and engagement_hits == 0:
            affect_signal = 0.2
        else:
            affect_signal = 0.5

        # Signal: Detailtiefe (teilt persönliche Details)
        detail_signal = min(1.0, max(0.0, (user_words - 5) / 40))

        # Gewichteter Score
        signals = {
            'length': length_signal,
            'affect': affect_signal,
            'detail': detail_signal,
        }
        weighted = length_signal * 0.3 + affect_signal * 0.4 + detail_signal * 0.3
        score = max(0.0, min(1.0, weighted))

        self._history.append({
            'timestamp': datetime.now(),
            'score': score,
            'signals': signals,
            'user_words': user_words
        })

        # Trend
        trend = self._calculate_trend()

        # Engagement Level
        if score >= 0.7:
            level = 'hoch'
        elif score >= 0.4:
            level = 'mittel'
        elif score >= 0.2:
            level = 'niedrig'
        else:
            level = 'desengagement'

        # Alert wenn Score stark gefallen ist
        alert = None
        if trend == 'declining' and score < 0.4:
            alert = "Therapeutische Allianz nimmt ab. Erwäge Meta-Kommunikation."
        if level == 'desengagement':
            alert = "User zeigt Desengagement. Beziehungsreparatur empfohlen."

        return AllianceScore(score=score, engagement_level=level, trend=trend, signals=signals, alert=alert)

    def _calculate_trend(self) -> str:
        """Berechnet den Trend der letzten Interaktionen."""
        if len(self._history) < 3:
            return 'stable'
        recent = [h['score'] for h in self._history[-3:]]
        earlier = [h['score'] for h in self._history[-6:-3]] if len(self._history) >= 6 else [h['score'] for h in self._history[:3]]
        avg_recent = sum(recent) / len(recent)
        avg_earlier = sum(earlier) / len(earlier)
        if avg_recent > avg_earlier + 0.1:
            return 'improving'
        elif avg_recent < avg_earlier - 0.1:
            return 'declining'
        return 'stable'

    def get_current_alliance(self) -> Optional[AllianceScore]:
        """Gibt den aktuellsten Allianz-Score zurück."""
        if not self._history:
            return None
        last = self._history[-1]
        return AllianceScore(
            score=last['score'],
            engagement_level='mittel',
            trend=self._calculate_trend(),
            signals=last['signals'],
            alert=None
        )


# ═══════════════════════════════════════════════════════════════════════
# #7 — TechniqueLibrary: Evidenzbasierte Technik-Auswahl
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TherapeuticTechnique:
    """Eine therapeutische Technik."""
    name: str
    category: str               # CBT, DBT, ACT, MI, Allgemein
    description: str
    instructions: str           # Schritt-für-Schritt Anleitung
    suitable_emotions: List[str]
    suitable_approaches: List[str]  # anxiety, depression, stress, relationship, general
    min_session_stage: int      # Ab welcher Interaktion sinnvoll (0 = sofort)
    difficulty: str             # "leicht", "mittel", "fortgeschritten"


class TechniqueLibrary:
    """
    Bibliothek evidenzbasierter therapeutischer Techniken mit Auswahl-Logik.
    """

    def __init__(self) -> None:
        self.techniques: List[TherapeuticTechnique] = self._build_library()

    def _build_library(self) -> List[TherapeuticTechnique]:
        """Baut die Technik-Bibliothek auf."""
        return [
            # ── CBT ──
            TherapeuticTechnique(
                name="Gedankenprotokoll",
                category="CBT",
                description="Identifiziert automatische negative Gedanken und hinterfragt sie systematisch.",
                instructions="""Anleitung für den Bot:
1. Frage nach der belastenden Situation
2. Frage: "Welcher Gedanke ging dir durch den Kopf?"
3. Frage: "Wie hat sich das angefühlt? (Emotion + Intensität 0-100)"
4. Hinterfrage: "Welche Beweise gibt es FÜR diesen Gedanken?"
5. Hinterfrage: "Welche Beweise gibt es GEGEN diesen Gedanken?"
6. Erarbeite: "Was wäre ein ausgewogenerer Gedanke?"
7. Frage: "Wie fühlt sich die Situation mit dem neuen Gedanken an? (0-100)" """,
                suitable_emotions=["trauer", "angst", "frustration", "scham", "wut"],
                suitable_approaches=["depression", "anxiety", "general"],
                min_session_stage=3,
                difficulty="mittel"
            ),
            TherapeuticTechnique(
                name="Kognitive Umstrukturierung",
                category="CBT",
                description="Identifiziert und korrigiert verzerrte Denkmuster (Denkfehler).",
                instructions="""Anleitung:
1. Erkläre häufige Denkfehler: Schwarz-Weiß-Denken, Katastrophisieren, Übergeneralisierung, Gedankenlesen, Personalisierung
2. Frage: "Erkennst du eines dieser Muster in deinen Gedanken?"
3. Benenne den Denkfehler sanft und validierend
4. Frage: "Was würdest du einem guten Freund sagen, der so denkt?"
5. Erarbeite alternative, realistischere Sichtweise""",
                suitable_emotions=["angst", "trauer", "frustration", "scham"],
                suitable_approaches=["depression", "anxiety", "general"],
                min_session_stage=5,
                difficulty="mittel"
            ),
            TherapeuticTechnique(
                name="Verhaltensaktivierung",
                category="CBT",
                description="Bricht den Teufelskreis aus Passivität und Niedergeschlagenheit.",
                instructions="""Anleitung:
1. Erkläre den Zusammenhang: Weniger Aktivität → weniger positive Erlebnisse → schlechtere Stimmung → noch weniger Aktivität
2. Frage: "Welche Aktivitäten haben dir früher Freude bereitet?"
3. Erarbeite eine kleine, machbare Aktivität für diese Woche
4. Setze ein konkretes Ziel: Wann, wo, wie lange?
5. Betone: Es geht nicht um Motivation, sondern um Handlung""",
                suitable_emotions=["trauer", "einsamkeit", "stress"],
                suitable_approaches=["depression", "general"],
                min_session_stage=2,
                difficulty="leicht"
            ),
            TherapeuticTechnique(
                name="Verhaltensexperiment",
                category="CBT",
                description="Testet negative Vorhersagen in der Realität.",
                instructions="""Anleitung:
1. Identifiziere eine negative Vorhersage: "Wenn ich X tue, passiert Y"
2. Frage: "Wie sicher bist du, dass das passiert? (0-100%)"
3. Plane ein kleines Experiment: "Was wäre die kleinste Version davon?"
4. Definiere: Was genau wirst du beobachten?
5. Nach dem Experiment: "Was ist tatsächlich passiert?"
6. Vergleiche Vorhersage vs. Realität""",
                suitable_emotions=["angst", "scham", "verwirrung"],
                suitable_approaches=["anxiety", "general"],
                min_session_stage=5,
                difficulty="fortgeschritten"
            ),

            # ── DBT ──
            TherapeuticTechnique(
                name="TIPP-Skill (Distress-Toleranz)",
                category="DBT",
                description="Sofort-Hilfe bei überwältigenden Emotionen: Temperatur, Intensive Bewegung, Paced Breathing, Progressive Relaxation.",
                instructions="""Anleitung:
1. T — Temperatur: "Halte einen Eiswürfel in der Hand oder spritze kaltes Wasser ins Gesicht. Das aktiviert den Tauchreflex und beruhigt das Nervensystem."
2. I — Intensive Bewegung: "Mache 2 Minuten intensive Bewegung — Hampelmänner, Treppen steigen, schnelles Gehen. Das baut Stresshormone ab."
3. P — Paced Breathing: "Atme ein (4 Sek.) — halte (7 Sek.) — atme aus (8 Sek.). Wiederhole 4x. Das aktiviert den Parasympathikus."
4. P — Progressive Relaxation: "Spanne alle Muskeln 5 Sek. an, dann locker lassen. Von den Füßen nach oben arbeiten." """,
                suitable_emotions=["angst", "wut", "stress", "frustration"],
                suitable_approaches=["anxiety", "stress", "general"],
                min_session_stage=0,
                difficulty="leicht"
            ),
            TherapeuticTechnique(
                name="Emotionsregulation (Welle reiten)",
                category="DBT",
                description="Beobachtet Emotionen als Wellen, die kommen und gehen, ohne gegen sie zu kämpfen.",
                instructions="""Anleitung:
1. Erkläre: "Emotionen sind wie Wellen im Meer — sie steigen an, erreichen einen Höhepunkt und klingen wieder ab."
2. Frage: "Kannst du die Emotion benennen, die gerade da ist?"
3. "Wo spürst du sie im Körper?"
4. "Beobachte sie, ohne sie zu bewerten. Sie ist nicht gut oder schlecht, sie ist einfach da."
5. "Atme ruhig weiter und beobachte, wie sich die Intensität verändert."
6. "Die Welle wird abklingen. Du musst sie nicht stoppen." """,
                suitable_emotions=["angst", "wut", "trauer", "stress"],
                suitable_approaches=["anxiety", "stress", "general"],
                min_session_stage=1,
                difficulty="mittel"
            ),

            # ── ACT ──
            TherapeuticTechnique(
                name="Kognitive Defusion",
                category="ACT",
                description="Löst die Verschmelzung mit negativen Gedanken durch Distanzierung.",
                instructions="""Anleitung:
1. Identifiziere den belastenden Gedanken
2. Frage: "Statt 'Ich bin ein Versager', sag mal: 'Ich habe den Gedanken, dass ich ein Versager bin.'"
3. Dann: "Und jetzt: 'Ich bemerke gerade, dass ich den Gedanken habe, dass...'"
4. "Wie fühlt sich der Unterschied an?"
5. "Der Gedanke ist nur ein Gedanke — nicht die Realität. Du kannst ihn beobachten, ohne ihm zu gehorchen." """,
                suitable_emotions=["trauer", "scham", "angst", "frustration"],
                suitable_approaches=["depression", "anxiety", "general"],
                min_session_stage=3,
                difficulty="mittel"
            ),
            TherapeuticTechnique(
                name="Werte-Klärung",
                category="ACT",
                description="Identifiziert persönliche Kernwerte als Kompass für Handlungen.",
                instructions="""Anleitung:
1. Frage: "Stell dir vor, du könntest dein Leben genau so gestalten, wie du es willst — wie würde das aussehen?"
2. Erkunde Lebensbereiche: Beziehungen, Arbeit, Gesundheit, Freizeit, persönliches Wachstum
3. Für jeden Bereich: "Was ist dir hier am wichtigsten?"
4. Destilliere 3-5 Kernwerte (z.B. Verbundenheit, Wachstum, Authentizität)
5. Frage: "Wie sehr lebst du aktuell nach diesen Werten? (1-10)"
6. Erarbeite: "Was wäre ein kleiner Schritt in Richtung deiner Werte?" """,
                suitable_emotions=["verwirrung", "einsamkeit", "trauer"],
                suitable_approaches=["depression", "general", "relationship"],
                min_session_stage=5,
                difficulty="mittel"
            ),

            # ── MI (Motivierende Gesprächsführung) ──
            TherapeuticTechnique(
                name="Ambivalenz-Erkundung",
                category="MI",
                description="Erkundet das Für und Wider einer Veränderung ohne Druck.",
                instructions="""Anleitung (OARS-Methode):
O — Open Questions: "Was gefällt dir an deiner aktuellen Situation? Und was möchtest du ändern?"
A — Affirmations: "Es zeigt Stärke, dass du darüber nachdenkst."
R — Reflections: "Einerseits... andererseits..." (Doppelseitige Reflexion)
S — Summaries: Fasse beide Seiten der Ambivalenz zusammen

WICHTIG: Kein Druck zur Veränderung! Die Entscheidung liegt beim User.""",
                suitable_emotions=["verwirrung", "frustration", "stress"],
                suitable_approaches=["general", "relationship", "stress"],
                min_session_stage=2,
                difficulty="leicht"
            ),

            # ── Allgemein ──
            TherapeuticTechnique(
                name="Atemübung 4-7-8",
                category="Allgemein",
                description="Beruhigende Atemtechnik für akuten Stress oder Angst.",
                instructions="""Anleitung:
"Lass uns eine bewährte Atemübung machen:
1. Atme durch die Nase ein — zähle bis 4
2. Halte den Atem — zähle bis 7
3. Atme langsam durch den Mund aus — zähle bis 8
4. Wiederhole das 4 Mal

Das aktiviert deinen Parasympathikus — den Teil des Nervensystems, der für Entspannung zuständig ist." """,
                suitable_emotions=["angst", "stress", "wut"],
                suitable_approaches=["anxiety", "stress", "general"],
                min_session_stage=0,
                difficulty="leicht"
            ),
            TherapeuticTechnique(
                name="Dankbarkeits-Übung",
                category="Allgemein",
                description="Lenkt den Fokus auf positive Aspekte des Lebens.",
                instructions="""Anleitung:
1. "Magst du kurz 3 Dinge aufzählen, für die du heute dankbar bist?"
2. "Das können ganz kleine Dinge sein — ein Kaffee, Sonnenschein, ein freundliches Wort."
3. Bei jeder Nennung: Nachfragen, warum es bedeutsam ist
4. "Wie fühlt es sich an, wenn du an diese Dinge denkst?"
5. Schlage vor, dies täglich zu wiederholen (Journaling)""",
                suitable_emotions=["trauer", "einsamkeit", "stress"],
                suitable_approaches=["depression", "general"],
                min_session_stage=1,
                difficulty="leicht"
            ),
            TherapeuticTechnique(
                name="Ressourcen-Mapping",
                category="Allgemein",
                description="Identifiziert vorhandene Stärken und Unterstützungsquellen.",
                instructions="""Anleitung:
1. Innere Ressourcen: "Welche Stärken hast du, die dir in schwierigen Zeiten geholfen haben?"
2. Soziale Ressourcen: "Wer in deinem Leben unterstützt dich? Wen könntest du um Hilfe bitten?"
3. Praktische Ressourcen: "Welche konkreten Hilfsangebote könntest du nutzen?"
4. Vergangene Bewältigung: "Wie hast du ähnliche Situationen früher gemeistert?"
5. Erstelle eine „Ressourcen-Karte" als Übersicht""",
                suitable_emotions=["einsamkeit", "verwirrung", "angst", "trauer"],
                suitable_approaches=["depression", "general", "stress"],
                min_session_stage=2,
                difficulty="leicht"
            ),
            TherapeuticTechnique(
                name="Progressive Muskelentspannung",
                category="Allgemein",
                description="Systematische Anspannung und Entspannung einzelner Muskelgruppen nach Jacobson.",
                instructions="""Anleitung:
"Wir machen eine kurze Muskelentspannung nach Jacobson. Für jede Muskelgruppe:
- Spanne die Muskeln 5-7 Sekunden an
- Dann lass locker und spüre die Entspannung 15-20 Sekunden

Reihenfolge: Hände → Unterarme → Oberarme → Schultern → Nacken → Gesicht → Bauch → Beine → Füße

Spüre den Unterschied zwischen Anspannung und Entspannung.
Das Ziel ist, die Fähigkeit zu entwickeln, Anspannung bewusst loszulassen." """,
                suitable_emotions=["stress", "angst", "wut"],
                suitable_approaches=["anxiety", "stress", "general"],
                min_session_stage=0,
                difficulty="leicht"
            ),
        ]

    def select_technique(
        self,
        dominant_emotion: str,
        approach: str = "general",
        interaction_count: int = 0,
        max_difficulty: str = "fortgeschritten",
        exclude_names: Optional[List[str]] = None
    ) -> Optional[TherapeuticTechnique]:
        """Wählt die passendste Technik basierend auf Kontext."""
        difficulty_order = {"leicht": 0, "mittel": 1, "fortgeschritten": 2}
        max_diff_val = difficulty_order.get(max_difficulty, 2)
        exclude = set(exclude_names or [])

        candidates: List[Tuple[float, TherapeuticTechnique]] = []
        for tech in self.techniques:
            if tech.name in exclude:
                continue
            if difficulty_order.get(tech.difficulty, 0) > max_diff_val:
                continue
            if interaction_count < tech.min_session_stage:
                continue

            # Scoring
            score = 0.0
            if dominant_emotion in tech.suitable_emotions:
                score += 3.0
            if approach in tech.suitable_approaches:
                score += 2.0
            # Leichte Techniken bevorzugen in frühen Sessions
            if interaction_count < 5 and tech.difficulty == "leicht":
                score += 1.0

            if score > 0:
                candidates.append((score, tech))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def get_technique_prompt_addition(self, technique: TherapeuticTechnique) -> str:
        """Generiert einen Prompt-Zusatz für die gewählte Technik."""
        return f"""<technique_guidance>
THERAPEUTISCHE TECHNIK FÜR DIESE ANTWORT: {technique.name} ({technique.category})

Beschreibung: {technique.description}

{technique.instructions}

WICHTIG: Integriere diese Technik NATÜRLICH ins Gespräch. Kein starrer Fragebogen!
Passe Sprache und Tempo an den User an. Wenn der User nicht bereit ist, dränge nicht.
</technique_guidance>"""


# ═══════════════════════════════════════════════════════════════════════
# #8 — OutcomeMonitor: Mikro-Assessments für Outcome-Monitoring
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class OutcomeAssessment:
    """Ein Mikro-Assessment am Session-Ende."""
    session_id: str
    distress_level: int         # 1-10 (10 = maximaler Distress)
    hope_level: int             # 1-10 (10 = sehr hoffnungsvoll)
    functioning_level: int      # 1-10 (10 = sehr gut)
    session_helpfulness: int    # 1-10 (10 = sehr hilfreich)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def overall_score(self) -> float:
        """Gesamtscore (höher = besser, 0-1)."""
        # Distress ist invertiert (weniger = besser)
        return ((10 - self.distress_level) + self.hope_level + self.functioning_level + self.session_helpfulness) / 40.0


class OutcomeMonitor:
    """
    Trackt therapeutischen Outcome über Sessions hinweg.
    
    Implementiert vereinfachte Version von ORS/SRS (Outcome/Session Rating Scale):
    - 4 Items am Session-Ende (Distress, Hoffnung, Funktionsniveau, Session-Hilfreich)
    - Longitudinale Zeitreihe für Fortschrittsmessung
    - Automatische Alerts bei Stagnation/Verschlechterung
    """

    def __init__(self, db: Optional[Any] = None) -> None:
        self._assessments: List[OutcomeAssessment] = []
        self._db = db

    def record_assessment(self, assessment: OutcomeAssessment) -> None:
        """Speichert ein Assessment."""
        self._assessments.append(assessment)
        if self._db:
            self._save_to_db(assessment)

    def get_trend(self, last_n: int = 5) -> Dict[str, Any]:
        """Berechnet den Outcome-Trend über die letzten N Sessions."""
        if len(self._assessments) < 2:
            return {'trend': 'insufficient_data', 'sessions': len(self._assessments)}

        recent = self._assessments[-last_n:]
        scores = [a.overall_score for a in recent]

        # Trend: Lineare Regression (einfach)
        n = len(scores)
        x_mean = (n - 1) / 2
        y_mean = sum(scores) / n
        num = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(scores))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den > 0 else 0

        if slope > 0.02:
            trend = 'improving'
        elif slope < -0.02:
            trend = 'declining'
        else:
            trend = 'stable'

        # Alert bei Verschlechterung
        alert = None
        if trend == 'declining' and len(scores) >= 3:
            alert = "Outcome verschlechtert sich über die letzten Sessions. Ansatz überdenken."
        if len(scores) >= 4 and all(s < 0.4 for s in scores[-3:]):
            alert = "Anhaltend niedriger Outcome. Professionelle Hilfe dringend empfehlen."

        return {
            'trend': trend,
            'slope': round(slope, 4),
            'current_score': round(scores[-1], 3),
            'average_score': round(sum(scores) / len(scores), 3),
            'sessions_tracked': len(self._assessments),
            'alert': alert,
            'history': [{'session': a.session_id, 'score': round(a.overall_score, 3)} for a in recent]
        }

    def generate_assessment_prompt(self) -> str:
        """Generiert den Prompt für das Session-Ende-Assessment."""
        return """Bevor wir die heutige Session beenden, würde ich gerne kurz wissen, wie es dir geht:

1. **Belastung**: Auf einer Skala von 1-10, wie belastet fühlst du dich gerade? (1 = kaum, 10 = sehr stark)
2. **Hoffnung**: Wie hoffnungsvoll bist du, dass sich deine Situation verbessern kann? (1-10)
3. **Funktionsfähigkeit**: Wie gut kommst du gerade im Alltag zurecht? (1-10)
4. **Hilfe**: Wie hilfreich war unsere heutige Session für dich? (1-10)

Du kannst einfach vier Zahlen nennen, z.B. "3, 7, 6, 8"."""

    def parse_assessment_response(self, response: str, session_id: str) -> Optional[OutcomeAssessment]:
        """Parst die User-Antwort auf das Assessment."""
        numbers = re.findall(r'\b(\d{1,2})\b', response)
        valid_numbers = [int(n) for n in numbers if 1 <= int(n) <= 10]

        if len(valid_numbers) >= 4:
            return OutcomeAssessment(
                session_id=session_id,
                distress_level=valid_numbers[0],
                hope_level=valid_numbers[1],
                functioning_level=valid_numbers[2],
                session_helpfulness=valid_numbers[3]
            )
        return None

    def _save_to_db(self, assessment: OutcomeAssessment) -> None:
        """Speichert Assessment in der DB."""
        if not self._db:
            return
        try:
            with self._db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO progress_assessments 
                       (session_id, distress_level, hope_level, functioning_level, 
                        session_helpfulness, overall_score, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (assessment.session_id, assessment.distress_level, assessment.hope_level,
                     assessment.functioning_level, assessment.session_helpfulness,
                     assessment.overall_score, assessment.timestamp.isoformat())
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Outcome-Assessment speichern fehlgeschlagen: {e}")

    def record_session_end(
        self,
        user_id: str,
        session_id: str,
        message_count: int = 0,
    ) -> None:
        """
        Automatisches Mikro-Assessment am Session-Ende.
        
        Ohne explizite User-Eingabe: schätzt Outcome anhand Session-Metadaten.
        Dient als Fallback, wenn der User das Assessment-Prompt nicht beantwortet.
        """
        # Heuristik: Mehr Nachrichten → engagierter → vermutlich hilfreicher
        engagement = min(1.0, message_count / 20.0)

        # Default-Mittelwerte mit leichtem Engagement-Bonus
        estimated = OutcomeAssessment(
            session_id=session_id,
            distress_level=5,  # Default-Mitte
            hope_level=5,
            functioning_level=5,
            session_helpfulness=round(4 + engagement * 3),  # 4–7 basierend auf Engagement
        )

        self.record_assessment(estimated)
        logger.info(
            "📊 Auto-Outcome für %s: overall=%.2f, engagement=%.2f, msgs=%d",
            session_id[:8], estimated.overall_score, engagement, message_count
        )


# ═══════════════════════════════════════════════════════════════════════
# #10 — EmotionInterventionMapper: Emotion→Intervention-Zuordnung
# ═══════════════════════════════════════════════════════════════════════

class EmotionInterventionMapper:
    """
    Mappt erkannte Emotionen auf passende therapeutische Interventionen.
    
    Basiert auf evidenzbasierten Zuordnungen:
    - Angst → Atemübungen, Exposition, kognitive Umstrukturierung
    - Trauer → Validierung, Verhaltensaktivierung, Ressourcen-Mapping
    - Wut → TIPP-Skill, Emotionsregulation, Defusion
    - etc.
    """

    # Emotion → priorisierte Technik-Namen
    _MAPPING: Dict[str, List[str]] = {
        'angst': ['Atemübung 4-7-8', 'TIPP-Skill (Distress-Toleranz)', 'Kognitive Umstrukturierung', 'Verhaltensexperiment'],
        'trauer': ['Ressourcen-Mapping', 'Verhaltensaktivierung', 'Dankbarkeits-Übung', 'Werte-Klärung'],
        'wut': ['TIPP-Skill (Distress-Toleranz)', 'Emotionsregulation (Welle reiten)', 'Progressive Muskelentspannung', 'Kognitive Defusion'],
        'stress': ['Atemübung 4-7-8', 'Progressive Muskelentspannung', 'TIPP-Skill (Distress-Toleranz)', 'Verhaltensaktivierung'],
        'einsamkeit': ['Ressourcen-Mapping', 'Verhaltensaktivierung', 'Werte-Klärung', 'Ambivalenz-Erkundung'],
        'frustration': ['Kognitive Umstrukturierung', 'Kognitive Defusion', 'Ambivalenz-Erkundung', 'Gedankenprotokoll'],
        'scham': ['Kognitive Defusion', 'Gedankenprotokoll', 'Kognitive Umstrukturierung', 'Ressourcen-Mapping'],
        'verwirrung': ['Werte-Klärung', 'Ambivalenz-Erkundung', 'Ressourcen-Mapping'],
        'hoffnung': [],  # Positiv — keine Intervention nötig
        'freude': [],
        'zufriedenheit': [],
        'überraschung': [],
    }

    def __init__(self, technique_library: TechniqueLibrary) -> None:
        self._library = technique_library

    def get_recommended_techniques(
        self,
        emotions: Dict[str, float],
        approach: str = "general",
        interaction_count: int = 0,
        exclude_used: Optional[List[str]] = None
    ) -> List[Tuple[TherapeuticTechnique, float]]:
        """
        Gibt priorisierte Techniken basierend auf erkannten Emotionen zurück.
        
        Args:
            emotions: Dict emotion_name → intensity (0-1)
            approach: Therapeutischer Ansatz
            interaction_count: Aktuelle Interaktionsnummer
            exclude_used: Bereits verwendete Techniken (diese Session)
            
        Returns:
            Liste von (Technik, Relevanz-Score) absteigend sortiert
        """
        exclude = set(exclude_used or [])
        scored: Dict[str, float] = {}

        # Für jede aktive Emotion: gewichtete Technik-Empfehlung
        for emotion, intensity in emotions.items():
            if intensity < 0.3:
                continue
            tech_names = self._MAPPING.get(emotion, [])
            for rank, name in enumerate(tech_names):
                if name in exclude:
                    continue
                # Score = Emotions-Intensität × Priorität (1. Platz = 1.0, 2. = 0.75, etc.)
                priority = 1.0 - rank * 0.25
                score = intensity * max(0.25, priority)
                scored[name] = max(scored.get(name, 0), score)

        # Techniken aus Library holen
        results: List[Tuple[TherapeuticTechnique, float]] = []
        for tech in self._library.techniques:
            if tech.name in scored:
                if interaction_count >= tech.min_session_stage:
                    results.append((tech, scored[tech.name]))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:3]  # Top 3


# ═══════════════════════════════════════════════════════════════════════
# #12 — CaseFormulator: Case Formulation (4P-Modell)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CaseFormulation:
    """Strukturierte Fallkonzeptualisierung nach dem 4P-Modell."""
    user_id: str
    predisposing: List[str]     # Prädisponierende Faktoren (Vorgeschichte)
    precipitating: List[str]    # Auslösende Faktoren (aktuelle Trigger)
    perpetuating: List[str]     # Aufrechterhaltende Faktoren (Vermeidung, Muster)
    protective: List[str]       # Schutzfaktoren (Stärken, Ressourcen)
    hypotheses: List[str]       # Therapeutische Hypothesen
    last_updated: datetime = field(default_factory=datetime.now)
    confidence: float = 0.3    # Steigt mit mehr Daten


class CaseFormulator:
    """
    Erstellt und aktualisiert eine strukturierte Fallkonzeptualisierung (4P-Modell).
    
    Integriert KG-Triples, Mood-Daten und Session-Zusammenfassungen zu einem
    kohärenten Verständnismodell des Users.
    """

    def __init__(self, model_loader: Optional[Any] = None) -> None:
        self._model_loader = model_loader
        self._formulations: Dict[str, CaseFormulation] = {}

    def get_or_create(self, user_id: str) -> CaseFormulation:
        """Holt oder erstellt eine Case Formulation."""
        if user_id not in self._formulations:
            self._formulations[user_id] = CaseFormulation(
                user_id=user_id,
                predisposing=[], precipitating=[], perpetuating=[], protective=[],
                hypotheses=[]
            )
        return self._formulations[user_id]

    def update_from_kg_triples(
        self,
        user_id: str,
        triples: List[Dict[str, Any]]
    ) -> CaseFormulation:
        """
        Aktualisiert die Case Formulation basierend auf KG-Triples.
        
        Klassifiziert Triples in die 4P-Kategorien basierend auf Prädikaten.
        """
        cf = self.get_or_create(user_id)

        predisposing_predicates = {'hat_vorgeschichte', 'aufgewachsen', 'kindheit', 'früher', 'eltern', 'familie'}
        precipitating_predicates = {'ausgelöst_durch', 'seit', 'kürzlich', 'aktuell', 'trigger', 'stressor'}
        perpetuating_predicates = {'vermeidet', 'muster', 'gewohnheit', 'immer_wieder', 'kreislauf'}
        protective_predicates = {'stärke', 'ressource', 'unterstützung', 'kann_gut', 'hobby', 'freund'}

        for triple in triples:
            pred = triple.get('predicate', '').lower().replace(' ', '_')
            obj = triple.get('object', '')
            subj = triple.get('subject', '')
            fact = f"{subj} {triple.get('predicate', '')} {obj}"

            categorized = False
            for keywords, category_list in [
                (predisposing_predicates, cf.predisposing),
                (precipitating_predicates, cf.precipitating),
                (perpetuating_predicates, cf.perpetuating),
                (protective_predicates, cf.protective),
            ]:
                if any(kw in pred for kw in keywords):
                    if fact not in category_list:
                        category_list.append(fact)
                    categorized = True
                    break

            # Heuristik für nicht-kategorisierte Triples
            if not categorized:
                obj_lower = obj.lower()
                if any(w in obj_lower for w in ['hobby', 'sport', 'freund', 'partner', 'stärke', 'kann gut']):
                    if fact not in cf.protective:
                        cf.protective.append(fact)
                elif any(w in obj_lower for w in ['angst', 'vermeidung', 'grübeln', 'schlafstörung']):
                    if fact not in cf.perpetuating:
                        cf.perpetuating.append(fact)

        # Confidence basierend auf Datenmenge
        total_facts = len(cf.predisposing) + len(cf.precipitating) + len(cf.perpetuating) + len(cf.protective)
        cf.confidence = min(0.9, 0.2 + total_facts * 0.05)
        cf.last_updated = datetime.now()

        return cf

    def update_from_llm(
        self,
        user_id: str,
        conversation_text: str,
        existing_formulation: Optional[CaseFormulation] = None
    ) -> CaseFormulation:
        """
        Aktualisiert Case Formulation per LLM-Analyse des Gesprächsverlaufs.
        """
        if not self._model_loader:
            return self.get_or_create(user_id)

        cf = existing_formulation or self.get_or_create(user_id)

        prompt = f"""Analysiere den folgenden Gesprächsverlauf und aktualisiere die Fallkonzeptualisierung
nach dem 4P-Modell. Berücksichtige die bisherige Konzeptualisierung.

BISHERIGE KONZEPTUALISIERUNG:
- Prädisponierend (Vorgeschichte): {json.dumps(cf.predisposing, ensure_ascii=False)}
- Auslösend (aktuelle Trigger): {json.dumps(cf.precipitating, ensure_ascii=False)}
- Aufrechterhaltend (Muster/Vermeidung): {json.dumps(cf.perpetuating, ensure_ascii=False)}
- Schutzfaktoren (Stärken/Ressourcen): {json.dumps(cf.protective, ensure_ascii=False)}

GESPRÄCHSVERLAUF (letzte Teile):
{conversation_text[-2000:]}

Antworte NUR mit validem JSON:
{{
  "predisposing": ["Faktor 1", "Faktor 2"],
  "precipitating": ["Trigger 1"],
  "perpetuating": ["Muster 1"],
  "protective": ["Stärke 1"],
  "hypotheses": ["Hypothese zur Dynamik"],
  "confidence": 0.5
}}"""

        try:
            messages = [{'role': 'user', 'content': prompt}]
            prompt_tokens = self._model_loader.count_messages_tokens(messages) if hasattr(self._model_loader, 'count_messages_tokens') else max(1, len(prompt) // 4)
            n_ctx = self._model_loader.get_max_context_tokens() if hasattr(self._model_loader, 'get_max_context_tokens') else 16384
            available = max(256, int(n_ctx) - int(prompt_tokens) - 64)
            adaptive_max_tokens = min(2048, max(1024, int(prompt_tokens * 0.65)), available)

            response = self._model_loader.generate_response(
                messages=messages,
                max_tokens=adaptive_max_tokens,
                temperature=0.3
            )
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                for key in ['predisposing', 'precipitating', 'perpetuating', 'protective']:
                    existing = getattr(cf, key)
                    for item in data.get(key, []):
                        if item and item not in existing:
                            existing.append(item)
                cf.hypotheses = data.get('hypotheses', cf.hypotheses)
                cf.confidence = min(0.9, max(cf.confidence, data.get('confidence', 0.3)))
                cf.last_updated = datetime.now()
        except Exception as e:
            logger.warning(f"LLM Case Formulation Update fehlgeschlagen: {e}")

        self._formulations[user_id] = cf
        return cf

    def get_formulation_prompt(self, user_id: str) -> str:
        """Generiert einen Prompt-Zusatz aus der Case Formulation."""
        cf = self.get_or_create(user_id)
        if cf.confidence < 0.2:
            return ""

        parts = ["<case_formulation>"]
        if cf.predisposing:
            parts.append(f"Vorgeschichte: {'; '.join(cf.predisposing[:5])}")
        if cf.precipitating:
            parts.append(f"Aktuelle Auslöser: {'; '.join(cf.precipitating[:5])}")
        if cf.perpetuating:
            parts.append(f"Aufrechterhaltende Muster: {'; '.join(cf.perpetuating[:5])}")
        if cf.protective:
            parts.append(f"Stärken/Ressourcen: {'; '.join(cf.protective[:5])}")
        if cf.hypotheses:
            parts.append(f"Therapeutische Hypothese: {cf.hypotheses[0]}")
        parts.append("</case_formulation>")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# #17 — HomeworkManager: Zwischen-Session-Aufgaben
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class HomeworkTask:
    """Eine Zwischen-Session-Aufgabe."""
    task_id: str
    user_id: str
    session_id: str
    description: str
    technique_name: Optional[str]   # Zugehörige Technik
    assigned_at: datetime
    due_description: str            # z.B. "bis zur nächsten Session"
    completed: bool = False
    completion_notes: Optional[str] = None
    completed_at: Optional[datetime] = None


class HomeworkManager:
    """
    Verwaltet Zwischen-Session-Aufgaben (Homework).
    
    Funktionen:
    1. Aufgabe am Session-Ende zuweisen
    2. Zu Beginn der nächsten Session nachfragen
    3. Completion tracken
    4. In Case Formulation integrieren
    """

    def __init__(self, db: Optional[Any] = None) -> None:
        self._db = db
        self._tasks: Dict[str, List[HomeworkTask]] = {}  # user_id → tasks

    def assign_task(
        self,
        user_id: str,
        session_id: str,
        description: str,
        technique_name: Optional[str] = None
    ) -> HomeworkTask:
        """Weist eine neue Aufgabe zu."""
        task = HomeworkTask(
            task_id=hashlib.sha256(f"{user_id}{session_id}{datetime.now().isoformat()}".encode()).hexdigest()[:16],
            user_id=user_id,
            session_id=session_id,
            description=description,
            technique_name=technique_name,
            assigned_at=datetime.now(),
            due_description="bis zur nächsten Session"
        )

        if user_id not in self._tasks:
            self._tasks[user_id] = []
        self._tasks[user_id].append(task)

        if self._db:
            self._save_task_to_db(task)

        return task

    def get_pending_tasks(self, user_id: str) -> List[HomeworkTask]:
        """Holt alle offenen Aufgaben eines Users."""
        tasks = self._tasks.get(user_id, [])
        # Auch aus DB laden falls vorhanden
        if self._db:
            db_tasks = self._load_tasks_from_db(user_id)
            task_ids = {t.task_id for t in tasks}
            for dt in db_tasks:
                if dt.task_id not in task_ids and not dt.completed:
                    tasks.append(dt)
        return [t for t in tasks if not t.completed]

    def complete_task(self, task_id: str, notes: Optional[str] = None) -> bool:
        """Markiert eine Aufgabe als erledigt."""
        for user_tasks in self._tasks.values():
            for task in user_tasks:
                if task.task_id == task_id:
                    task.completed = True
                    task.completion_notes = notes
                    task.completed_at = datetime.now()
                    if self._db:
                        self._update_task_in_db(task)
                    return True
        return False

    def generate_followup_prompt(self, user_id: str) -> Optional[str]:
        """Generiert einen Nachfrage-Prompt für offene Aufgaben."""
        pending = self.get_pending_tasks(user_id)
        if not pending:
            return None

        if len(pending) == 1:
            task = pending[0]
            return f"""<homework_followup>
Letzte Session habe ich dir eine kleine Übung vorgeschlagen:
"{task.description}"
Magst du mir erzählen, wie es damit gelaufen ist? Es ist völlig okay, wenn du nicht dazu gekommen bist — manchmal braucht es einfach Zeit.
</homework_followup>"""

        task_list = "\n".join(f"- {t.description}" for t in pending[:3])
        return f"""<homework_followup>
Aus unseren letzten Gesprächen haben wir ein paar Übungen besprochen:
{task_list}
Hast du eine davon ausprobiert? Es ist kein Problem, wenn nicht — erzähl mir einfach, wie es dir geht.
</homework_followup>"""

    def generate_assignment_prompt(
        self,
        technique: Optional[TherapeuticTechnique] = None,
        session_theme: str = ""
    ) -> str:
        """Generiert einen natürlichen Aufgaben-Zuweisungs-Prompt."""
        if technique:
            return f"""Zum Abschluss unserer heutigen Session möchte ich dir eine kleine Übung mitgeben,
die auf der Technik "{technique.name}" basiert. Sie wird dir helfen, das Besprochene zu vertiefen.
Das ist nur ein Vorschlag — du entscheidest, ob und wann du es machst."""
        return """Möchtest du bis zu unserem nächsten Gespräch etwas ausprobieren?
Ich könnte dir eine kleine Übung vorschlagen, die zu dem passt, worüber wir heute gesprochen haben."""

    def _save_task_to_db(self, task: HomeworkTask) -> None:
        """Speichert Task in DB."""
        if not self._db:
            return
        try:
            with self._db.get_connection() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO practice_tasks
                       (task_id, user_id, session_id, description, technique_name,
                        assigned_at, due_description, completed, completion_notes, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task.task_id, task.user_id, task.session_id, task.description,
                     task.technique_name, task.assigned_at.isoformat(), task.due_description,
                     1 if task.completed else 0, task.completion_notes,
                     task.completed_at.isoformat() if task.completed_at else None)
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Homework-Task speichern fehlgeschlagen: {e}")

    def _update_task_in_db(self, task: HomeworkTask) -> None:
        """Aktualisiert Task in DB."""
        self._save_task_to_db(task)  # Upsert via INSERT OR REPLACE

    def _load_tasks_from_db(self, user_id: str) -> List[HomeworkTask]:
        """Lädt Tasks aus DB."""
        if not self._db:
            return []
        try:
            with self._db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM practice_tasks WHERE user_id = ? ORDER BY assigned_at DESC LIMIT 20",
                    (user_id,)
                ).fetchall()
                tasks = []
                for row in rows:
                    tasks.append(HomeworkTask(
                        task_id=row['task_id'],
                        user_id=row['user_id'],
                        session_id=row['session_id'],
                        description=row['description'],
                        technique_name=row['technique_name'],
                        assigned_at=datetime.fromisoformat(row['assigned_at']),
                        due_description=row['due_description'],
                        completed=bool(row['completed']),
                        completion_notes=row['completion_notes'],
                        completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None
                    ))
                return tasks
        except Exception as e:
            logger.warning(f"Homework-Tasks laden fehlgeschlagen: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════════
# #18 — RuptureDetector: Allianz-Ruptur-Erkennung
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RuptureEvent:
    """Ein erkanntes Allianz-Ruptur-Ereignis."""
    severity: str           # "leicht", "mittel", "schwer"
    type: str               # "withdrawal" (Rückzug) oder "confrontation" (Konfrontation)
    trigger_message: str
    repair_suggestion: str
    timestamp: datetime = field(default_factory=datetime.now)


class RuptureDetector:
    """
    Erkennt therapeutische Allianz-Rupturen und schlägt Reparatur-Strategien vor.
    
    Ruptur-Typen (nach Safran & Muran):
    1. Withdrawal (Rückzug): User zieht sich zurück, gibt kurze Antworten, wechselt Thema
    2. Confrontation (Konfrontation): User zeigt Ärger/Unzufriedenheit mit dem Bot
    """

    # Confrontation-Marker
    _CONFRONTATION_PATTERNS = [
        r'(?:du verstehst (?:das|mich|es) (?:nicht|gar nicht|überhaupt nicht))',
        r'(?:das (?:stimmt|passt|hilft) (?:so |gar |überhaupt )?nicht)',
        r'(?:du (?:bist|klingst|redest) wie (?:ein|eine) (?:maschine|roboter|computer|ki))',
        r'(?:hör auf (?:damit|so zu|zu))',
        r'(?:das ist (?:blödsinn|quatsch|unsinn|sinnlos|nicht hilfreich))',
        r'(?:du wiederholst (?:dich|immer|nur))',
        r'(?:ich (?:brauche|will) (?:keine|keinen|kein) (?:ratschläge|tipps|übungen))',
    ]

    # Withdrawal-Marker (Rückzug)
    _WITHDRAWAL_PATTERNS = [
        r'^(?:ja|nein|ok|okay|hmm|aha|achso|mhm|gut|naja)\s*[.!?]?\s*$',
        r'^(?:weiß (?:ich )?nicht|keine ahnung|egal)\s*[.!?]?\s*$',
        r'^.{1,10}$',  # Sehr kurze Antworten (< 10 Zeichen)
    ]

    # Reparatur-Strategien
    _REPAIR_STRATEGIES = {
        'withdrawal_leicht': (
            "Ich merke, dass sich das Gespräch gerade etwas verändert hat. "
            "Gibt es etwas, worüber du lieber sprechen möchtest?"
        ),
        'withdrawal_mittel': (
            "Es ist mir wichtig, dass unser Gespräch für dich hilfreich ist. "
            "Habe ich etwas gesagt, das nicht ganz passend war? "
            "Du kannst mir ehrlich sagen, was du gerade brauchst."
        ),
        'withdrawal_schwer': (
            "Ich habe das Gefühl, dass meine letzten Antworten vielleicht nicht das waren, was du brauchst. "
            "Das tut mir leid. Magst du mir sagen, was dir gerade helfen würde? "
            "Oder wir können auch einfach kurz innehalten."
        ),
        'confrontation_leicht': (
            "Danke, dass du mir das sagst. Dein Feedback ist wichtig für mich. "
            "Was kann ich anders machen, damit es für dich hilfreicher ist?"
        ),
        'confrontation_mittel': (
            "Du hast Recht, das war nicht hilfreich. Es tut mir leid. "
            "Lass mich anders herangehen — was brauchst du gerade wirklich?"
        ),
        'confrontation_schwer': (
            "Ich entschuldige mich aufrichtig. Ich habe dein Anliegen nicht richtig verstanden. "
            "Du weißt am besten, was du brauchst. "
            "Magst du mir noch eine Chance geben und mir erzählen, was dir gerade wichtig ist?"
        ),
    }

    def __init__(self) -> None:
        self._confrontation_pats = [re.compile(p, re.IGNORECASE) for p in self._CONFRONTATION_PATTERNS]
        self._withdrawal_pats = [re.compile(p, re.IGNORECASE) for p in self._WITHDRAWAL_PATTERNS]
        self._recent_lengths: List[int] = []

    def check_for_rupture(self, user_message: str) -> Optional[RuptureEvent]:
        """Prüft eine User-Nachricht auf Ruptur-Signale."""
        # Check Confrontation
        confrontation_hits = sum(1 for p in self._confrontation_pats if p.search(user_message))
        if confrontation_hits > 0:
            severity = 'schwer' if confrontation_hits >= 2 else 'mittel' if confrontation_hits >= 1 else 'leicht'
            key = f'confrontation_{severity}'
            return RuptureEvent(
                severity=severity,
                type='confrontation',
                trigger_message=user_message[:100],
                repair_suggestion=self._REPAIR_STRATEGIES.get(key, self._REPAIR_STRATEGIES['confrontation_leicht'])
            )

        # Check Withdrawal
        withdrawal_hits = sum(1 for p in self._withdrawal_pats if p.search(user_message.strip()))

        # Zusätzlich: Längen-Trend (3+ aufeinanderfolgende kurze Antworten)
        self._recent_lengths.append(len(user_message.split()))
        if len(self._recent_lengths) > 10:
            self._recent_lengths.pop(0)

        consecutive_short = 0
        for length in reversed(self._recent_lengths):
            if length <= 3:
                consecutive_short += 1
            else:
                break

        if withdrawal_hits > 0 or consecutive_short >= 3:
            if consecutive_short >= 4:
                severity = 'schwer'
            elif consecutive_short >= 3 or withdrawal_hits >= 2:
                severity = 'mittel'
            else:
                severity = 'leicht'
            key = f'withdrawal_{severity}'
            return RuptureEvent(
                severity=severity,
                type='withdrawal',
                trigger_message=user_message[:100],
                repair_suggestion=self._REPAIR_STRATEGIES.get(key, self._REPAIR_STRATEGIES['withdrawal_leicht'])
            )

        return None


# ═══════════════════════════════════════════════════════════════════════
# #9 — WellbeingRAGBootstrapper: Psychologie-spezifischer RAG-Korpus
# ═══════════════════════════════════════════════════════════════════════

class WellbeingRAGBootstrapper:
    """
    Bootstraps einen psychologiespezifischen RAG-Korpus mit evidenzbasierten Inhalten.
    
    Quellen (Open Access, keine Copyright-Probleme):
    - Psychoedukation (Was ist Angst? Depression? Stress?)
    - Bewältigungsstrategien (Atemübungen, PMR, Achtsamkeit)
    - Therapeutische Konzepte (CBT-Grundlagen, Verhaltensaktivierung)
    - Notfall-Informationen (Krisenhotlines, Selbsthilfe)
    
    Diese Texte sind Eigenformulierungen basierend auf öffentlich
    verfügbarem psychologischem Grundwissen.
    """

    PSYCHO_CORPUS: List[Dict[str, str]] = [
        {
            'title': 'Was ist Angst? — Psychoedukation',
            'content': """Angst ist eine natürliche und überlebensnotwendige Emotion. Sie warnt uns vor Gefahren 
und bereitet den Körper auf Kampf oder Flucht vor (Fight-or-Flight-Reaktion). Bei einer Angststörung 
tritt diese Reaktion jedoch auch in Situationen auf, die objektiv nicht gefährlich sind. 
Der Körper reagiert mit Herzrasen, Schwitzen, Zittern, Atemnot und Schwindel. 
Diese Symptome sind unangenehm, aber NICHT gefährlich. Sie sind die Reaktion eines übervorsichtigen 
Alarmsystems. Angst hat eine Wellen-Eigenschaft: Sie steigt an, erreicht einen Höhepunkt und klingt 
von alleine wieder ab — auch ohne Vermeidung. Dieses Wissen ist die Grundlage für die Behandlung: 
Durch schrittweise Konfrontation lernt das Gehirn, dass die befürchtete Gefahr nicht eintritt.""",
            'category': 'psychoedukation',
            'topic': 'angst'
        },
        {
            'title': 'Was ist Depression? — Psychoedukation',
            'content': """Depression ist mehr als "traurig sein". Es ist eine behandelbare Erkrankung, 
die Denken, Fühlen und Handeln beeinflusst. Kernsymptome sind anhaltende Niedergeschlagenheit, 
Interessenverlust (Anhedonie) und Erschöpfung. Häufig kommen Schlafstörungen, Appetitveränderungen, 
Konzentrationsprobleme und negative Gedankenspiralen dazu. 
Der Teufelskreis der Depression: Weniger Aktivität → weniger positive Erlebnisse → schlechtere 
Stimmung → noch weniger Motivation → noch weniger Aktivität. 
Durchbrechen dieses Kreislaufs ist der Schlüssel zur Besserung (Verhaltensaktivierung). 
WICHTIG: Depressive Gedanken wie "Es wird nie besser" oder "Ich bin wertlos" sind SYMPTOME 
der Depression, nicht die Realität. Sie verändern sich mit der Besserung.""",
            'category': 'psychoedukation',
            'topic': 'depression'
        },
        {
            'title': 'Stress und Burnout — Psychoedukation',
            'content': """Stress ist die körperliche und psychische Reaktion auf Anforderungen, die unsere 
Ressourcen übersteigen. Kurzfristiger Stress (Eustress) kann leistungsfördernd sein. 
Chronischer Stress (Distress) schädigt Körper und Psyche. 
Burnout entwickelt sich in Phasen: Enthusiasmus → Stagnation → Frustration → Apathie → Zusammenbruch.
Warnsignale: Dauermüdigkeit, Zynismus, reduzierte Leistungsfähigkeit, Rückzug, körperliche Beschwerden.
Gegenmaßnahmen: Grenzen setzen, Erholung priorisieren, "Nein" sagen lernen, soziale Kontakte pflegen, 
Perfektionismus hinterfragen, professionelle Hilfe bei anhaltender Erschöpfung.""",
            'category': 'psychoedukation',
            'topic': 'stress'
        },
        {
            'title': 'Atemtechniken zur Stressreduktion',
            'content': """Kontrolliertes Atmen ist eine der schnellsten Methoden zur Beruhigung des 
Nervensystems. Die 4-7-8-Technik (Einatmen 4 Sek., Halten 7 Sek., Ausatmen 8 Sek.) aktiviert 
den Parasympathikus und senkt Herzfrequenz und Blutdruck innerhalb von 2-3 Zyklen messbar.
Die Bauchatmung (Zwerchfellatmung) ist die Grundlage: Hand auf den Bauch, beim Einatmen 
hebt sich der Bauch, beim Ausatmen senkt er sich. Brustatmung (flach, schnell) verstärkt 
Angstreaktionen, Bauchatmung wirkt ihnen entgegen.
Box-Breathing (4-4-4-4): Einatmen 4 Sek., Halten 4 Sek., Ausatmen 4 Sek., Halten 4 Sek. 
Besonders geeignet für akute Stresssituationen am Arbeitsplatz.""",
            'category': 'techniken',
            'topic': 'atemübung'
        },
        {
            'title': 'Progressive Muskelentspannung nach Jacobson',
            'content': """Die Progressive Muskelentspannung (PME/PMR) nach Edmund Jacobson basiert auf dem 
Prinzip, dass Muskelentspannung und Angst/Anspannung nicht gleichzeitig bestehen können 
(reziproke Inhibition). Durch systematisches Anspannen und Loslassen einzelner Muskelgruppen 
lernt man, Anspannung bewusst zu erkennen und aufzulösen.
Vorgehen: Jede Muskelgruppe 5-7 Sekunden anspannen, dann 15-20 Sekunden entspannen.
Reihenfolge: Dominante Hand → Unterarm → Oberarm → Stirn → Augen → Kiefer → Nacken → 
Schultern → Brustmuskulatur → Bauch → Oberschenkel → Unterschenkel → Füße.
Übungsdauer: Anfangs 20-30 Min., mit Übung auf 5-10 Min. (Kurzform) reduzierbar.
Evidenz: Wirksam bei Angst, Schlafstörungen, chronischen Schmerzen, Spannungskopfschmerzen.""",
            'category': 'techniken',
            'topic': 'entspannung'
        },
        {
            'title': 'Kognitive Verhaltenstherapie (CBT) — Grundlagen',
            'content': """Die Kognitive Verhaltenstherapie (KVT/CBT) ist die am besten erforschte 
Psychotherapieform mit nachgewiesener Wirksamkeit bei Depression, Angststörungen, PTBS, 
Zwangsstörungen und vielen weiteren psychischen Problemen.
Grundprinzip: Nicht die Situation selbst macht uns Probleme, sondern unsere BEWERTUNG der Situation.
Das ABC-Modell: A (Activating event — Auslöser) → B (Belief — Bewertung/Gedanke) → C (Consequence — Gefühl/Verhalten).
Veränderung bei B (der Bewertung) verändert die emotionale Reaktion (C).
Häufige Denkfehler: Schwarz-Weiß-Denken, Katastrophisieren, Übergeneralisierung, 
Gedankenlesen, Emotionales Argumentieren, Personalisierung, "Sollte"-Denken.
Kerninterventionen: Gedankenprotokoll, Kognitive Umstrukturierung, Verhaltensexperimente, 
Exposition, Verhaltensaktivierung.""",
            'category': 'therapiemethoden',
            'topic': 'cbt'
        },
        {
            'title': 'Achtsamkeit (Mindfulness) — Grundlagen und Übungen',
            'content': """Achtsamkeit bedeutet, den gegenwärtigen Moment bewusst und ohne Bewertung 
wahrzunehmen. Jon Kabat-Zinn entwickelte daraus MBSR (Mindfulness-Based Stress Reduction), 
das die Wirksamkeit in über 600 Studien nachgewiesen hat.
Grundhaltung: Beobachten ohne zu urteilen. Gedanken und Gefühle kommen und gehen wie 
Wolken am Himmel — man muss ihnen nicht folgen.
Einstiegsübung "5-4-3-2-1": 5 Dinge sehen, 4 Dinge hören, 3 Dinge fühlen, 2 Dinge riechen, 
1 Ding schmecken. Bringt den Fokus in den Moment und unterbricht Grübelspiralen.
Body Scan: Aufmerksamkeit langsam durch den ganzen Körper wandern lassen, von den Füßen 
bis zum Kopf. Ohne die Empfindungen zu verändern — nur wahrnehmen.
Evidenz: Wirksam bei Stress, Angst, Depression-Rückfallprophylaxe, chronischen Schmerzen.""",
            'category': 'techniken',
            'topic': 'achtsamkeit'
        },
        {
            'title': 'Selbstmitgefühl (Self-Compassion) nach Kristin Neff',
            'content': """Selbstmitgefühl bedeutet, sich selbst so freundlich zu behandeln, wie man 
einen guten Freund in einer schwierigen Situation behandeln würde. 
Drei Komponenten: 
1. Selbstfreundlichkeit statt Selbstkritik 
2. Gemeinsames Menschsein (Leiden gehört zum Leben, du bist nicht allein damit)
3. Achtsamkeit (Gefühle wahrnehmen, ohne darin zu versinken)
Übung "Mitfühlende Pause": Bei Stress oder Selbstkritik:
- "Dies ist ein Moment des Leidens" (Achtsamkeit)
- "Leiden gehört zum Menschsein" (Gemeinsames Menschsein)
- "Möge ich freundlich mit mir sein" (Selbstfreundlichkeit)
Evidenz: Reduziert Angst, Depression und Stress. Stärkt emotionale Resilienz. 
Besonders wirksam bei Perfektionismus und hoher Selbstkritik.""",
            'category': 'techniken',
            'topic': 'selbstmitgefühl'
        },
        {
            'title': 'Trauerbewältigung — die Phasen der Trauer',
            'content': """Das Dual-Prozess-Modell der Trauer (Stroebe & Schut) hat das starre 
Phasenmodell abgelöst: Trauernde pendeln zwischen verlustorientierter Bewältigung 
(Schmerz zulassen, weinen, erinnern) und wiederherstellungsorientierter Bewältigung 
(neue Rollen, neue Aktivitäten, Zukunft gestalten). Beides ist notwendig und normal.
Es gibt kein "richtiges" Trauern. Jeder Mensch trauert anders und in seinem eigenen Tempo.
Hilfreich: Über den Verlust sprechen, Rituale schaffen, Erinnerungen pflegen, 
sich erlauben traurig zu sein, ABER auch sich erlauben glücklich zu sein.
Warnsignale für komplizierte Trauer (>6 Monate ohne Besserung): 
Intensive Sehnsucht, Vermeidung aller Erinnerungen, Funktionseinschränkungen, 
Sinnverlust, Identitätsverlust → professionelle Hilfe empfehlen.""",
            'category': 'psychoedukation',
            'topic': 'trauer'
        },
        {
            'title': 'Beziehungskonflikte — Kommunikationsstrategien',
            'content': """Gewaltfreie Kommunikation (GFK) nach Marshall Rosenberg bietet 
ein bewährtes Modell für konstruktive Konfliktlösung:
4 Schritte: 1. Beobachtung (ohne Bewertung), 2. Gefühl (was fühle ich?), 
3. Bedürfnis (was brauche ich?), 4. Bitte (konkreter Wunsch).
Beispiel: NICHT "Du hörst mir nie zu!" (Vorwurf mit Übergeneralisierung)
SONDERN: "Als du während meiner Erzählung auf dein Handy geschaut hast (Beobachtung), 
habe ich mich unwichtig gefühlt (Gefühl), weil mir Aufmerksamkeit wichtig ist (Bedürfnis). 
Könntest du das Handy weglegen, wenn ich dir etwas erzähle? (Bitte)"
Aktives Zuhören: Paraphrasieren ("Habe ich richtig verstanden, dass...?"), 
Gefühle spiegeln ("Es klingt so, als ob du dich...fühlst"), 
Zusammenfassen ("Also geht es dir vor allem um...").
Destruktive Muster vermeiden: Kritik, Verachtung, Mauern, Defensivität (Gottmans "4 Reiter der Apokalypse").""",
            'category': 'psychoedukation',
            'topic': 'beziehung'
        },
    ]

    def get_corpus(self) -> List[Dict[str, str]]:
        """Gibt den psychologiespezifischen Korpus zurück."""
        return self.PSYCHO_CORPUS

    def bootstrap_into_rag(self, rag_manager: Any) -> int:
        """
        Lädt den Psycho-Korpus in den RAG-Manager (UnifiedRagStore).

        Schreibt jedes Dokument mit ``corpus_domain='psych'`` und
        ``safety_flag='safe'`` in den geteilten RAG-Store. ``add_document``
        dedupliziert intern über den Text-Hash.

        Erwartet eine ``UnifiedRagStore``-kompatible Instanz mit
        ``add_document(content, *, metadata, corpus_domain, safety_flag)``.
        Eine fehlende API ist ein Programmierfehler und wird nicht stumm
        ignoriert.

        Returns:
            Anzahl der hinzugefügten/aktualisierten Dokumente.
        """
        if not rag_manager:
            raise ValueError(
                "WellbeingRAGBootstrapper.bootstrap_into_rag: rag_manager is None"
            )

        if not hasattr(rag_manager, 'add_document'):
            raise TypeError(
                f"WellbeingRAGBootstrapper.bootstrap_into_rag: rag_manager "
                f"({type(rag_manager).__name__}) does not expose add_document(). "
                f"Pass a UnifiedRagStore instance (e.g. via "
                f"agent.tools.get_global_rag_store())."
            )

        added = 0
        for doc in self.PSYCHO_CORPUS:
            rag_manager.add_document(
                content=doc['content'],
                metadata={
                    'title': doc['title'],
                    'category': doc['category'],
                    'topic': doc['topic'],
                    'source': 'psycho_corpus_sota',
                },
                corpus_domain='psych',
                safety_flag='safe',
            )
            added += 1

        logger.info(
            f"✅ Psycho-RAG-Korpus: {added}/{len(self.PSYCHO_CORPUS)} Dokumente "
            f"in domain='psych' geladen"
        )
        return added


# ═══════════════════════════════════════════════════════════════════════
# Zentrale Integration: WellbeingPipeline
# ═══════════════════════════════════════════════════════════════════════

class WellbeingPipeline:
    """
    Zentrale Orchestrierung aller therapeutischen SOTA-Komponenten.
    
    Wird in agent_chatbot_logic.py und wellbeing_session_interface.py verwendet.
    """

    def __init__(
        self,
        db: Optional[Any] = None,
        model_loader: Optional[Any] = None
    ) -> None:
        self.response_validator = PsychResponseValidator()
        self.grounding_checker = PsychGroundingChecker()
        self.risk_scorer = CumulativeRiskScorer()
        self.screening = ScreeningInstruments()
        self.alliance_tracker = AllianceTracker()
        self.technique_library = TechniqueLibrary()
        self.outcome_monitor = OutcomeMonitor(db=db)
        self.emotion_mapper = EmotionInterventionMapper(self.technique_library)
        self.case_formulator = CaseFormulator(model_loader=model_loader)
        self.homework_manager = HomeworkManager(db=db)
        self.rupture_detector = RuptureDetector()
        self.rag_bootstrapper = WellbeingRAGBootstrapper()

        logger.info("✅ WellbeingPipeline initialisiert (13 SOTA-Komponenten)")

    def pre_response_analysis(
        self,
        user_message: str,
        session_context: Optional[Dict[str, Any]] = None,
        emotions: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Analyse VOR der Antwort-Generierung.
        
        Returns:
            Dict mit Empfehlungen für den Response-Generator:
            - technique_guidance: Optional Technik-Prompt
            - risk_info: Kumulative Risiko-Info
            - rupture: Optional Ruptur-Erkennung
            - homework_followup: Optional Homework-Nachfrage
            - case_formulation: Optional Case-Formulation Prompt-Zusatz
            - alliance: Aktueller Allianz-Score
        """
        result: Dict[str, Any] = {}

        # Ruptur-Check
        rupture = self.rupture_detector.check_for_rupture(user_message)
        if rupture:
            result['rupture'] = rupture
            logger.warning(f"⚠️ Allianz-Ruptur erkannt: {rupture.type} ({rupture.severity})")

        # Kumulative Risiko-Bewertung
        risk_label = 'niedrig'
        mood_valence = None
        crisis = False
        if session_context:
            risk_label = session_context.get('crisis_risk', session_context.get('risk_level', 'niedrig'))
            mood_data = session_context.get('mood_progression')
            if isinstance(mood_data, dict):
                mood_valence = mood_data.get('current_valence')
            crisis = session_context.get('crisis_indicators', False)

        self.risk_scorer.add_turn_assessment(
            risk_label=str(risk_label),
            mood_valence=mood_valence,
            crisis_indicators=crisis,
            response_word_count=len(user_message.split())
        )
        cumulative_risk = self.risk_scorer.get_cumulative_risk()
        result['risk_info'] = cumulative_risk

        # Technik-Empfehlung basierend auf Emotionen
        if emotions:
            dominant = max(emotions, key=lambda k: emotions[k]) if emotions else 'neutral'
            approach = session_context.get('approach', 'general') if session_context else 'general'
            interaction_count = session_context.get('interaction_count', 0) if session_context else 0

            recommendations = self.emotion_mapper.get_recommended_techniques(
                emotions=emotions,
                approach=approach,
                interaction_count=interaction_count
            )
            if recommendations:
                tech, score = recommendations[0]
                result['technique_guidance'] = self.technique_library.get_technique_prompt_addition(tech)
                result['recommended_technique'] = tech

        # Homework Follow-up (nur bei Session-Start)
        if session_context:
            user_id = session_context.get('user_id', '')
            interaction_count = session_context.get('interaction_count', 0)
            if interaction_count <= 1 and user_id:
                followup = self.homework_manager.generate_followup_prompt(user_id)
                if followup:
                    result['homework_followup'] = followup

            # Case Formulation Prompt
            if user_id:
                cf_prompt = self.case_formulator.get_formulation_prompt(user_id)
                if cf_prompt:
                    result['case_formulation'] = cf_prompt

        return result

    def post_response_validation(
        self,
        response: str,
        user_message: str,
        rag_evidence: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Validierung NACH der Antwort-Generierung.
        
        Returns:
            (validated_response, validation_info)
            Falls Validierung fehlschlägt, wird die Antwort modifiziert.
        """
        info: Dict[str, Any] = {}

        # 1. Response-Qualitätsprüfung
        validation = self.response_validator.validate(response, user_message)
        info['validation'] = {
            'is_valid': validation.is_valid,
            'empathy_score': validation.empathy_score,
            'safety_passed': validation.safety_passed,
            'repetition_score': validation.repetition_score,
            'boundary_violations': validation.boundary_violations,
        }

        # 2. Grounding-Check
        grounding = self.grounding_checker.check_grounding(response, rag_evidence)
        info['grounding'] = {
            'is_grounded': grounding.is_grounded,
            'grounding_score': grounding.grounding_score,
            'ungrounded_claims': grounding.ungrounded_claims[:3],
        }

        # 3. Allianz-Tracking
        alliance = self.alliance_tracker.record_interaction(user_message, response)
        info['alliance'] = {
            'score': alliance.score,
            'engagement': alliance.engagement_level,
            'trend': alliance.trend,
            'alert': alliance.alert,
        }

        # Entscheidung: Response modifizieren?
        final_response = response

        # Safety-Violation → Response blockieren und ersetzen
        if not validation.safety_passed:
            logger.error(f"🚨 Safety-Violation erkannt: {validation.boundary_violations}")
            final_response = (
                "Ich möchte vorsichtig sein und keine Aussagen machen, die dir schaden könnten. "
                "Für fachliche Einschätzungen und Diagnosen empfehle ich dir, "
                "einen Therapeuten oder Arzt aufzusuchen. "
                "Ich bin aber gerne weiter als unterstützender Gesprächspartner für dich da. "
                "Was beschäftigt dich gerade am meisten?"
            )

        # Ungrounded Claims → Warnung anhängen (nur bei schweren Fällen)
        if not grounding.is_grounded and grounding.grounding_score < 0.3:
            logger.warning(f"⚠️ Ungrounded Claims: {grounding.ungrounded_claims}")
            # Nicht blockieren, aber loggen für Monitoring

        return final_response, info

