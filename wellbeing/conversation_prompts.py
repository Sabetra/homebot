"""
Therapeutische Prompts für psychologische Unterstützung
======================================================
Spezialisierte Prompt-Templates und Gesprächsführung für:
- Empathische Kommunikation
- Strukturierte therapeutische Ansätze
- Krisenintervention
- Ressourcen-aktivierung
"""

import logging
from typing import Dict, List, Optional, Any, Tuple, Callable
from datetime import datetime
import json
import random

# Logger für therapeutische Prompts
logger = logging.getLogger(__name__)

class ConversationPromptManager:
    """
    Verwaltet therapeutische Prompts und Gesprächsführungs-Templates
    
    Features:
    - Verschiedene therapeutische Ansätze
    - Kontextuelle Prompt-Auswahl
    - Krisenintervention-Prompts
    - Empathische Gesprächsführung
    """
    
    def __init__(self) -> None:
        """Initialisiert Therapeutic Prompt Manager"""
        
        # Basis-Prompts für psychologische Unterstützung
        self.base_prompts: Dict[str, Any] = {}
        self._init_base_prompts()
        
        # Spezialisierte therapeutische Ansätze
        self.therapeutic_approaches: Dict[str, Any] = {}
        self._init_therapeutic_approaches()
        
        # Krisen- und Notfall-Prompts
        self.crisis_prompts: Dict[str, Any] = {}
        self._init_crisis_prompts()
        
        # Ressourcen und Bewältigungsstrategien
        self.coping_resources: Dict[str, Any] = {}
        self._init_coping_resources()
        
        logger.info("✓ ConversationPromptManager initialisiert")
    
    def _init_base_prompts(self) -> None:
        """Initialisiert Basis-Prompts für psychologische Unterstützung - LLM-zentriert"""
        
        self.base_prompts = {
            'system_prompt': """<role>
Du bist ein warmherziger, einfühlsamer Begleiter für psychologische Unterstützung.
Du begegnest jedem Menschen mit echter Wärme und Mitgefühl. Du verstehst, dass es Mut erfordert,
über schwierige Themen zu sprechen, und schätzt das Vertrauen, das dir entgegengebracht wird.
</role>

<responsibilities>
- Sei ein verständnisvoller Zuhörer, der wirklich verstehen möchte
- Zeige aufrichtige Anteilnahme an Gefühlen und Erlebnissen
- Stelle einfühlsame Fragen, die zur Selbstreflexion einladen
- Hilf dabei, Bewältigungsstrategien zu entwickeln
- Erkenne und würdige vorhandene Stärken und Ressourcen
</responsibilities>

<active_listening>
Wie ein einfühlsamer Gesprächspartner paraphrasierst du das Gesagte — aber nicht nur wortwörtlich:

1. PARAPHRASIEREN (in eigenen Worten zusammenfassen):
   - "Wenn ich dich richtig verstehe, fühlst du dich..."
   - "Es klingt so, als ob..."
   - "Korrigiere mich, wenn ich falsch liege, aber es scheint, als..."

2. BEISPIELE GEBEN (um zu zeigen, dass du verstehst):
   - "Das ist ein bisschen so, wie wenn man..."
   - "Ich stelle mir das vor wie..."
   - "Viele Menschen in ähnlichen Situationen beschreiben das als..."

3. METAPHERN VERWENDEN (um Gefühle greifbar zu machen):
   - "Es klingt, als würdest du gerade einen schweren Rucksack tragen..."
   - "Wie ein Boot im Sturm, das nach einem sicheren Hafen sucht..."
   - "Wie wenn man in einem Nebel steht und den Weg nicht mehr sieht..."
</active_listening>

<communication_style>
- Beginne Antworten oft mit einer kurzen Paraphrase des Gesagten
- Verwende warme, persönliche Formulierungen statt distanzierter Fachsprache
- Drücke echte Anteilnahme aus ("Das klingt wirklich belastend", "Ich kann verstehen, dass...")
- Zeige, dass du die Person als ganzen Menschen siehst, nicht nur das Problem
- Sei ermutigend und vermittle Hoffnung, ohne zu verharmlosen
- Biete konkrete Hilfe an, wenn gewünscht
</communication_style>

<boundaries>
STRIKTE GRENZEN — Diese Regeln gelten IMMER, ohne Ausnahme:
- Stelle NIEMALS Diagnosen (z.B. "Du hast Depression/Borderline/PTBS")
- Empfehle NIEMALS Medikamente oder Dosierungen
- Gib KEINE medizinischen Ratschläge
- Du bist KEIN Ersatz für eine professionelle Therapie — weise bei schweren Fällen IMMER auf professionelle Hilfe hin
- Respektiere stets die Autonomie und Entscheidungen der Person
- Bei Äußerungen über Suizid, Selbstverletzung oder akute Gefahr: Verweise SOFORT auf Notfallkontakte (Telefonseelsorge: 0800 111 0 111)
- Erfinde KEINE therapeutischen Fakten, Studien oder Techniken — wenn du unsicher bist, sage es ehrlich
</boundaries>

<thinking_instructions>
Bevor du antwortest, durchlaufe INTERN (nicht sichtbar für den User) folgende Schritte:
1. EMOTION: Welche Emotionen drückt die Person aus? (Primär + Sekundär)
2. BEDÜRFNIS: Was braucht die Person gerade? (Validation, Information, Strategie, Entlastung?)
3. SICHERHEIT: Gibt es Krisensignale? (Suizidgedanken, Selbstverletzung, akute Gefahr?)
4. STRATEGIE: Welche Technik passt? (Validation, Paraphrasieren, Ressourcenaktivierung, Psychoedukation, Problemlösung?)
5. ANTWORT: Formuliere deine Antwort basierend auf 1-4
</thinking_instructions>

<examples>
Hier sind Beispiele für ideales Gesprächsverhalten:

Beispiel 1 — Validation bei Trauer:
User: "Seit meine Mutter gestorben ist, fühle ich mich so leer. Nichts macht mehr Sinn."
Assistent: "Der Verlust deiner Mutter hat eine tiefe Leere hinterlassen — als ob ein Teil deiner Welt fehlt. Dieses Gefühl der Sinnlosigkeit nach einem so großen Verlust ist ganz natürlich und zeigt, wie wichtig sie für dich war. Du musst das nicht alleine durchstehen. Magst du mir erzählen, was dir gerade am meisten fehlt?"

Beispiel 2 — Ressourcenaktivierung bei Überforderung:
User: "Arbeit, Kinder, Haushalt — ich schaffe das alles nicht mehr."
Assistent: "Das klingt, als würdest du gerade an mehreren Fronten gleichzeitig kämpfen — und jede einzelne davon wäre schon eine Herausforderung für sich. Dass du trotzdem weitermachst, zeigt eine enorme Stärke, auch wenn es sich gerade nicht so anfühlt. Lass uns mal schauen: Gibt es eine Sache davon, bei der du dir als Erstes Entlastung wünschen würdest?"

Beispiel 3 — Grenzen bei Diagnose-Anfragen:
User: "Glaubst du, ich habe eine Depression?"
Assistent: "Ich kann und darf keine Diagnosen stellen — das wäre nicht verantwortungsvoll dir gegenüber. Was ich sagen kann: Die Gefühle, die du beschreibst, klingen belastend und verdienen professionelle Aufmerksamkeit. Wäre es für dich eine Option, das mit einem Therapeuten oder deinem Hausarzt zu besprechen? Sie können dir die Klarheit geben, die du suchst."
</examples>

Führe ein unterstützendes Gespräch mit echter menschlicher Wärme. Deine Antworten sollen sich
anfühlen wie ein Gespräch mit einem verständnisvollen Freund, der auch fachlich fundiert beraten kann.
Antworte auf Deutsch.""",

            # LLM-basierte Erkennung statt hart gecodeter Keywords
            'wellbeing_detection_prompt': """
Analysiere folgende Nachricht und bestimme, ob sie psychologische Unterstützung benötigt.

KRITERIEN FÜR PSYCHOLOGISCHE UNTERSTÜTZUNG:
- Emotionale Belastung oder Leid
- Stress, Überforderung, Burnout
- Beziehungsprobleme oder zwischenmenschliche Konflikte  
- Ängste, Sorgen, Depression
- Trauer, Verlust, Einsamkeit
- Krisensituationen oder Hilflosigkeit
- Explizite Bitte um emotionale Unterstützung
- Selbstreflexion zu emotionalen Themen

NICHT psychologisch:
- Sachfragen und Wissensfragen
- Technische Probleme
- Alltägliche Konversation ohne emotionale Komponente
- Fakten-basierte Anfragen

Antworte nur mit "JA" oder "NEIN" - keine Erklärung nötig.

Nachricht: "{message}"
""",

            'crisis_detection_prompt': """
Analysiere diese Nachricht SEHR SORGFÄLTIG auf akute Krisensignale:

AKUTE KRISENSIGNALE:
- Suizidgedanken oder Selbstverletzungsabsichten
- Hoffnungslosigkeit ohne Ausweg
- Akute Gefahr für sich oder andere
- Schwere psychische Dekompensation
- Dringender Hilfebedarf

Bewerte das Risikolevel:
- HOCH: Akute Gefahr, sofortiges Handeln erforderlich
- MITTEL: Erhöhte Aufmerksamkeit und Unterstützung nötig
- NIEDRIG: Normale psychologische Unterstützung ausreichend

Antworte im Format: "RISIKO: [HOCH/MITTEL/NIEDRIG]"

Nachricht: "{message}"
""",

            # Minimaler Fallback für Offline-Situationen (nur wenige kritische Keywords)
            'fallback_crisis_indicators': [
                'suizid', 'selbstmord', 'ende machen', 'kann nicht mehr'
            ],
            
            'fallback_wellbeing_indicators': [
                'hilfe', 'stress', 'traurig', 'angst', 'probleme'
            ]
        }
    
    def _init_therapeutic_approaches(self) -> None:
        """Initialisiert therapeutische Ansätze - LLM-zentriert mit Guidance"""
        
        self.therapeutic_approaches = {
            'anxiety_support': {
                'prompt_addition': """

💚 FOKUS: ANGST-UNTERSTÜTZUNG MIT WÄRME

Ich verstehe, wie belastend Ängste sein können. Begegne der Person mit besonderer Sanftheit:

- Validiere zunächst die Gefühle ("Es ist völlig verständlich, dass du dich so fühlst...")
- Entwickle behutsam individuell angepasste Bewältigungsstrategien
- Vermittle Sicherheit und Geborgenheit in deinen Worten
- Biete konkrete, kleine Schritte an, die nicht überfordern
- Ermutige und stärke das Vertrauen in die eigenen Fähigkeiten

EINFÜHLSAME UNTERSTÜTZUNG:
- Zeige echtes Mitgefühl für die Angsterfahrung
- Passe Techniken sanft an die individuellen Möglichkeiten an
- Feiere auch kleine Fortschritte gemeinsam
"""
            },
            
            'depression_support': {
                'prompt_addition': """

💚 FOKUS: DEPRESSION-UNTERSTÜTZUNG MIT MITGEFÜHL

Depression kann sich sehr einsam anfühlen. Zeige, dass du wirklich da bist:

- Beginne mit echter Anerkennung ("Es braucht Kraft, darüber zu sprechen...")
- Vermittle Hoffnung, ohne die Schwere zu minimieren
- Sei geduldig - auch kleine Schritte sind wertvoll
- Erkenne die Stärke, die es braucht, überhaupt weiterzumachen
- Biete sanfte, erreichbare Aktivierungsideen an

LIEBEVOLLE BEGLEITUNG:
- Zeige, dass die Person nicht allein ist
- Würdige jeden noch so kleinen Fortschritt
- Vermittle: "Du bist wertvoll, genau so wie du bist"
"""
            },
            
            'stress_management': {
                'prompt_addition': """

💚 FOKUS: STRESS-MANAGEMENT MIT VERSTÄNDNIS

Stress kann überwältigend sein. Zeige Verständnis für die Belastung:

- Anerkenne zunächst die Überforderung ("Das klingt wirklich anstrengend...")
- Biete praktische, sofort umsetzbare Entlastung an
- Entwickle gemeinsam realistische Strategien ohne Druck
- Ermutige zu Selbstfürsorge ohne Schuldgefühle
- Stärke das Gefühl, die Situation bewältigen zu können

UNTERSTÜTZENDE BEGLEITUNG:
- Zeige Verständnis für die Grenzen der Belastbarkeit
- Biete konkrete, praktikable Lösungen an
- Feiere Momente der Entspannung und des Loslassens
"""
            },
            
            'relationship_support': {
                'prompt_addition': """

💚 FOKUS: BEZIEHUNGS-UNTERSTÜTZUNG MIT EINFÜHLUNG

Beziehungsthemen berühren oft tief. Begegne diesen mit besonderer Sensibilität:

- Höre zunächst wirklich zu, ohne zu urteilen
- Zeige Verständnis für alle Gefühle - auch die widersprüchlichen
- Respektiere die Komplexität von Beziehungen
- Stärke das Selbstwertgefühl und die eigenen Grenzen
- Biete neue Perspektiven sanft und ohne Druck an

EINFÜHLSAME BEGLEITUNG:
- Validiere die emotionale Erfahrung vollständig
- Unterstütze dabei, eigene Bedürfnisse zu erkennen
- Ermutige zu gesunder Selbstfürsorge in Beziehungen
"""
            },
            
            'general_support': {
                'prompt_addition': """

💚 ALLGEMEINE PSYCHOLOGISCHE UNTERSTÜTZUNG MIT HERZ

- Höre mit echtem Interesse und Wärme zu
- Zeige aufrichtige Anteilnahme an der Situation
- Erkenne und würdige vorhandene Stärken
- Ermutige sanft zur Selbstreflexion
- Biete praktische Hilfe an, wenn gewünscht
- Vermittle: "Du verdienst Unterstützung"
"""
            }
        }
    
    def _init_crisis_prompts(self) -> None:
        """Initialisiert Krisen-Interventions-Templates - minimal und flexibel"""
        
        self.crisis_prompts = {
            'emergency_contacts': {
                'germany': {
                    'telefonseelsorge': '0800 111 0 111 oder 0800 111 0 222',
                    'kinder_jugendliche': '116 111',
                    'eltern': '0800 111 0 550',
                    'gewalt_frauen': '08000 116 016',
                    'notarzt': '112'
                }
            },
            
            'crisis_guidance': """
🚨 KRISENINTERVENTION - WICHTIGE RICHTLINIEN:

1. SOFORTIGE SICHERHEIT:
   - Bei akuter Suizidgefahr: Sofort an Telefonseelsorge oder Notarzt verweisen
   - Bei Gewalt oder Missbrauch: Schutz und Hilfsangebote bereitstellen
   
2. PROFESSIONELLE HILFE:
   - Betone dass professionelle Hilfe verfügbar ist
   - Keine Diagnosen stellen oder medizinische Ratschläge geben
   - Bei psychiatrischen Notfällen an Fachkräfte verweisen

3. EMPATHISCHE UNTERSTÜTZUNG:
   - Hoffnung vermitteln ohne zu minimieren
   - Validiere die Schwere der Situation
   - Betone dass die Person wertvoll ist

Lass das LLM individuell und empathisch auf die konkrete Krisensituation eingehen.
"""
        }
    
    def _init_coping_resources(self) -> None:
        """Initialisiert Bewältigungsstrategien und Ressourcen - LLM-generiert"""
        
        self.coping_resources = {
            # Nur Notfall-Kontakte bleiben hart-codiert (Sicherheit)
            'emergency_contacts': {
                'germany': [
                    "Telefonseelsorge: 0800 111 0 111 oder 0800 111 0 222",
                    "Nummer gegen Kummer (Kinder/Jugendliche): 116 111",
                    "Nummer gegen Kummer (Eltern): 0800 111 0 550",
                    "Hilfetelefon Gewalt gegen Frauen: 08000 116 016",
                    "Muslimisches Seelsorgetelefon: 030 443 509 821"
                ]
            },
            
            # LLM-basierte Strategie-Generierung - Prompts statt Listen
            'stress_management_prompt': """
Erstelle 5-7 konkrete, sofort umsetzbare Stress-Management-Strategien für diese Person.
Berücksichtige verschiedene Situationen (Zuhause, Arbeit, unterwegs).
Fokus auf wissenschaftlich fundierte, aber einfache Techniken.
Formatiere als nummerierte Liste mit kurzen, klaren Anweisungen.
""",
            
            'mood_boosters_prompt': """
Erstelle 5-7 konkrete Aktivitäten zur Stimmungsverbesserung.
Berücksichtige verschiedene Energielevel und Situationen.
Fokus auf schnell wirksame, praktische Vorschläge.
Formatiere als nummerierte Liste mit motivierenden Beschreibungen.
""",
            
            'self_care_activities_prompt': """
Erstelle 5-7 konkrete Selbstfürsorge-Aktivitäten.
Berücksichtige körperliche, emotionale und mentale Bedürfnisse.
Fokus auf nachhaltige, regelmäßig durchführbare Aktivitäten.
Formatiere als nummerierte Liste mit praktischen Tipps.
""",
            
            'anxiety_coping_prompt': """
Erstelle 5-7 konkrete Strategien zur Angstbewältigung.
Berücksichtige akute Angst und längerfristige Bewältigung.
Fokus auf Grounding, Atmung und kognitive Techniken.
Formatiere als nummerierte Liste mit Schritt-für-Schritt Anleitungen.
""",
            
            'crisis_coping_prompt': """
Erstelle 5-7 konkrete Strategien für Krisensituationen.
Berücksichtige Sicherheit und sofortige Stabilisierung.
Fokus auf praktische, umsetzbare Sofortmaßnahmen.
Formatiere als nummerierte Liste mit klaren Prioritäten.
"""
        }
    
    def get_system_prompt(self, session_context: Optional[Dict[str, Any]] = None) -> str:
        """
        Liefert System-Prompt basierend auf Session-Kontext
        
        Args:
            session_context: Aktueller Session-Kontext
            
        Returns:
            Angepasster System-Prompt
        """
        try:
            base_prompt_raw = self.base_prompts.get('system_prompt', '')
            # Ensure string type
            base_prompt = str(base_prompt_raw) if base_prompt_raw is not None else ''
            
            # Ergänze kontextuelle Informationen
            if session_context:
                # Frühere Stimmungslagen berücksichtigen
                if 'mood_trend' in session_context:
                    mood = session_context['mood_trend']
                    if mood in ['negativ', 'deprimiert', 'ängstlich']:
                        base_prompt += f"\n\n🔍 KONTEXT: Der Benutzer zeigt Anzeichen von {mood}er Stimmung. Sei besonders aufmerksam für Krisensignale."
                
                # Session-Historie berücksichtigen
                if 'interaction_count' in session_context and session_context['interaction_count'] > 10:
                    base_prompt += "\n\n📚 KONTEXT: Dies ist eine längere Unterhaltung. Beziehe dich auf vorherige Gespräche und zeige Kontinuität."
            
            return base_prompt
            
        except Exception as e:
            logger.error(f"❌ System-Prompt-Generierung fehlgeschlagen: {e}")
            fallback = self.base_prompts.get('system_prompt', '')
            # Ensure we return str
            return str(fallback) if fallback is not None else ''
    
    def get_welcome_message(self, chat_function: Optional[Callable[..., Any]] = None, user_context: Optional[Dict[str, Any]] = None) -> str:
        """
        Liefert eine empathische Begrüßungsnachricht - LLM-generiert oder Fallback
        
        Args:
            chat_function: Chat-Funktion für LLM-Generierung
            user_context: Optional - Kontext über den Benutzer (Zeit, frühere Sessions etc.)
            
        Returns:
            Personalisierte Begrüßungsnachricht
        """
        try:
            # LLM-basierte Begrüßung generieren
            if chat_function and callable(chat_function):
                try:
                    context_info = ""
                    if user_context:
                        time_info = user_context.get('time_of_day', '')
                        returning_user = user_context.get('is_returning_user', False)
                        
                        if time_info:
                            context_info += f"Tageszeit: {time_info}\n"
                        if returning_user:
                            context_info += "Benutzer: Wiederkehrender Benutzer\n"
                    
                    welcome_prompt = f"""
Erstelle eine warmherzige, empathische Begrüßung für ein psychologisches Unterstützungsgespräch.

{context_info}

ANFORDERUNGEN:
- Warm und einladend
- Zeigt dass du da bist zum Zuhören
- Macht keine Annahmen über Probleme
- Öffnet den Raum für Gespräch
- 1-2 kurze, natürliche Sätze
- Authentisch und nicht übertrieben

Erstelle eine einzigartige, situationsangemessene Begrüßung.
"""
                    
                    llm_response = chat_function(welcome_prompt)
                    welcome_text = str(llm_response).strip()
                    
                    if welcome_text and len(welcome_text) > 10:  # Mindestlänge
                        logger.info("✨ LLM-generierte personalisierte Begrüßung")
                        return welcome_text
                        
                except Exception as e:
                    logger.warning(f"⚠️ LLM-Begrüßung fehlgeschlagen: {e}")
            
            # Fallback: Einfache, aber warme Begrüßung
            simple_welcomes = [
                "Hallo! Ich bin hier, um dir zuzuhören. Wie geht es dir heute?",
                "Hi! Schön, dass du da bist. Wie fühlst du dich gerade?",
                "Hallo! Ich bin für dich da. Was beschäftigt dich?",
                "Hi! Du bist wichtig. Erzähl mir, wie es dir geht."
            ]
            return random.choice(simple_welcomes)
            
        except Exception as e:
            logger.error(f"❌ Welcome-Message-Abruf fehlgeschlagen: {e}")
            return "Hallo! Ich bin hier, um dir zuzuhören. Wie geht es dir?"
    
    def get_care_prompt(self, approach: str, 
                             prompt_type: str = 'general', 
                             user_input: Optional[str] = None) -> Optional[str]:
        """
        Liefert spezifischen therapeutischen Prompt
        
        Args:
            approach: Therapeutischer Ansatz ('anxiety_support', 'depression_support', etc.)
            prompt_type: Typ des Prompts (wird ignoriert, da wir prompt_addition verwenden)
            user_input: Benutzer-Eingabe für Kontext
            
        Returns:
            Therapeutischer Prompt oder None
        """
        try:
            if approach not in self.therapeutic_approaches:
                logger.warning(f"⚠️ Unbekannter therapeutischer Ansatz: {approach}")
                return None
            
            approach_data = self.therapeutic_approaches[approach]
            
            # Kombiniere Basis-Prompt mit therapeutischem Ansatz
            base_prompt = self.get_system_prompt()
            addition = approach_data.get('prompt_addition', '')
            
            return base_prompt + str(addition) if addition else base_prompt
            
        except Exception as e:
            logger.error(f"❌ Therapeutic-Prompt-Abruf fehlgeschlagen: {e}")
            return None
    
    def assess_crisis_risk(self, user_input: str, chat_function: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
        """
        Bewertet Krisensignale in Benutzer-Eingabe - LLM-basiert
        
        Args:
            user_input: Benutzer-Eingabe
            chat_function: Optional - Chat-Funktion für LLM-basierte Bewertung
            
        Returns:
            Risiko-Bewertung
        """
        try:
            # Standard Risiko-Assessment Struktur
            risk_assessment = {
                'suicide_risk': False,
                'severe_depression': False,
                'anxiety_panic': False,
                'risk_level': 'low',
                'assessment_method': 'fallback',
                'immediate_action_needed': False,
                'llm_analysis': None
            }
            
            # Primär: LLM-basierte Bewertung (wenn Chat-Funktion verfügbar)
            if chat_function and callable(chat_function):
                try:
                    crisis_prompt = self.base_prompts['crisis_detection_prompt'].format(message=user_input)
                    llm_response = chat_function(crisis_prompt)
                    
                    risk_assessment['llm_analysis'] = llm_response
                    risk_assessment['assessment_method'] = 'llm'
                    
                    # Parse LLM Response
                    llm_text = str(llm_response) if llm_response else ""
                    if 'RISIKO: HOCH' in llm_text.upper():
                        risk_assessment['risk_level'] = 'high'
                        risk_assessment['suicide_risk'] = True
                        risk_assessment['immediate_action_needed'] = True
                        logger.warning(f"🚨 LLM erkannte HOHES Krisensignal: {user_input[:50]}...")
                    elif 'RISIKO: MITTEL' in llm_text.upper():
                        risk_assessment['risk_level'] = 'medium'
                        risk_assessment['severe_depression'] = True
                        logger.info(f"⚠️ LLM erkannte MITTLERES Risiko: {user_input[:50]}...")
                    elif 'RISIKO: NIEDRIG' in llm_text.upper():
                        risk_assessment['risk_level'] = 'low'
                        
                    return risk_assessment
                    
                except Exception as e:
                    logger.warning(f"⚠️ LLM-Krisenbewertung fehlgeschlagen: {e}, nutze Fallback")
            
            # Fallback: Minimal-Keyword-Check (nur für kritische Situationen)
            user_lower = user_input.lower()
            
            # Kritische Suizid-Indikatoren (minimal)
            crisis_indicators = self.base_prompts.get('fallback_crisis_indicators', [])
            for indicator in crisis_indicators:
                if indicator in user_lower:
                    risk_assessment['suicide_risk'] = True
                    risk_assessment['risk_level'] = 'high'
                    risk_assessment['immediate_action_needed'] = True
                    logger.warning(f"🚨 Fallback erkannte Krisensignal: {indicator}")
                    break
            
            return risk_assessment
            
        except Exception as e:
            logger.error(f"❌ Krisen-Risiko-Bewertung fehlgeschlagen: {e}")
            return {
                'risk_level': 'unknown', 
                'error': str(e),
                'assessment_method': 'error'
            }
    
    def get_crisis_response(self, risk_type: str) -> List[str]:
        """
        Liefert Krisen-Antworten basierend auf Risiko-Typ
        
        Args:
            risk_type: Typ des Risikos
            
        Returns:
            Liste von Krisen-Antworten
        """
        try:
            if risk_type in self.crisis_prompts:
                crisis_data = self.crisis_prompts[risk_type]
                
                responses = []
                
                # Füge alle verfügbaren Response-Typen hinzu
                for response_type, prompts in crisis_data.items():
                    if isinstance(prompts, list):
                        responses.extend(prompts)
                
                return responses
            
            return []
            
        except Exception as e:
            logger.error(f"❌ Krisen-Response-Abruf fehlgeschlagen: {e}")
            return []
    
    def get_coping_strategies(self, category: str, chat_function: Optional[Callable[..., Any]] = None, 
                            user_context: Optional[str] = None) -> List[str]:
        """
        Liefert LLM-generierte Bewältigungsstrategien für Kategorie
        
        Args:
            category: Strategie-Kategorie
            chat_function: Chat-Funktion für LLM-Generierung
            user_context: Optional - Zusätzlicher Kontext über den Benutzer
            
        Returns:
            Liste von Bewältigungsstrategien (LLM-generiert oder Fallback)
        """
        try:
            # Notfall-Kontakte: Direkt zurückgeben (hart-codiert für Sicherheit)
            if category == 'emergency_contacts':
                contacts = self.coping_resources.get('emergency_contacts', {}).get('germany', [])
                # Ensure we return list[str]
                return [str(item) for item in contacts] if isinstance(contacts, list) else []
            
            # LLM-basierte Strategien-Generierung
            if chat_function and callable(chat_function):
                try:
                    prompt_key = f"{category}_prompt"
                    if prompt_key in self.coping_resources:
                        base_prompt = self.coping_resources[prompt_key]
                        
                        # Erweitere Prompt mit Benutzer-Kontext
                        full_prompt = base_prompt
                        if user_context:
                            full_prompt += f"\n\nBerücksichtige folgenden Kontext: {user_context}"
                        
                        llm_response = chat_function(full_prompt)
                        
                        # Parse LLM-Response zu Liste
                        response_text = str(llm_response)
                        strategies = []
                        
                        # Extrahiere nummerierte Liste
                        lines = response_text.split('\n')
                        for line in lines:
                            line = line.strip()
                            if line and (line[0].isdigit() or line.startswith('-') or line.startswith('•')):
                                # Entferne Nummerierung/Bullets
                                clean_line = line.lstrip('0123456789.- •').strip()
                                if clean_line:
                                    strategies.append(clean_line)
                        
                        if strategies:
                            logger.info(f"✨ LLM generierte {len(strategies)} Strategien für: {category}")
                            return strategies[:7]  # Max 7 Strategien
                        else:
                            logger.warning(f"⚠️ LLM-Response konnte nicht geparst werden für: {category}")
                            
                except Exception as e:
                    logger.warning(f"⚠️ LLM-Strategien-Generierung fehlgeschlagen: {e}")
            
            # Dynamischer LLM-basierter Fallback für generische Strategien
            try:
                if chat_function:
                    fallback_prompt = f"""
                    
Du bist ein erfahrener Experte für emotionale Unterstützung. Erstelle 3-5 praktische, evidenzbasierte Bewältigungsstrategien für die Kategorie: {category}

RICHTLINIEN:
- Gib nur die Strategien aus, eine pro Zeile
- Beginne jede Zeile mit einem Bindestrich (-)
- Verwende einfache, umsetzbare Sprache
- Basiere auf bewährten psychologischen Prinzipien
- Berücksichtige unterschiedliche Persönlichkeitstypen
- Keine langen Erklärungen, nur die Strategien

Beispielformat:
- Strategie 1
- Strategie 2
- Strategie 3
"""
                    
                    response = chat_function(fallback_prompt)
                    if response and isinstance(response, str):
                        strategies = []
                        for line in response.split('\n'):
                            clean_line = line.strip()
                            if clean_line.startswith('-'):
                                strategies.append(clean_line[1:].strip())
                        
                        if strategies:
                            logger.info(f"✨ LLM-Fallback generierte {len(strategies)} Strategien für: {category}")
                            return strategies[:5]
                            
            except Exception as e:
                logger.warning(f"⚠️ LLM-Fallback fehlgeschlagen: {e}")
            
            # Minimaler Hard-Coded Notfall-Fallback nur für absolute Krisensituationen
            emergency_fallback = {
                'crisis_coping': [
                    "Sicherheit sicherstellen",
                    "Notfallkontakte anrufen", 
                    "Sofortige professionelle Hilfe suchen"
                ]
            }
            
            # Für alle anderen Kategorien: Leere Liste, damit das System andere Wege findet
            if category == 'crisis_coping':
                return emergency_fallback.get(category, [])
            else:
                logger.info(f"ℹ️ Keine Fallback-Strategien für '{category}' - System wird alternative Wege nutzen")
                return []
            
        except Exception as e:
            logger.error(f"❌ Coping-Strategien-Abruf fehlgeschlagen: {e}")
            return []
    
    def suggest_therapeutic_approach(self, user_input: str, 
                                   session_history: Optional[List[Dict]] = None) -> str:
        """
        Schlägt therapeutischen Ansatz vor - LLM-first mit Fallback
        
        Args:
            user_input: Aktuelle Benutzer-Eingabe
            session_history: Bisherige Session-Historie
            
        Returns:
            Empfohlener therapeutischer Ansatz
        """
        try:
            # Fallback für Offline-Situation: Einfache Heuristik
            user_lower = user_input.lower()
            
            # Minimale Fallback-Heuristik (ohne hart-codierte Listen)
            if len([word for word in ['angst', 'panik'] if word in user_lower]) > 0:
                return 'anxiety_support'
            elif len([word for word in ['traurig', 'deprimiert'] if word in user_lower]) > 0:
                return 'depression_support'
            elif len([word for word in ['stress', 'burnout'] if word in user_lower]) > 0:
                return 'stress_management'
            elif len([word for word in ['beziehung', 'partner'] if word in user_lower]) > 0:
                return 'relationship_support'
            
            # Standard: Allgemeine Unterstützung
            return 'general_support'
            
        except Exception as e:
            logger.error(f"❌ Therapeutic-Approach-Suggestion fehlgeschlagen: {e}")
            return 'general_support'
    
    def get_all_approaches(self) -> Dict[str, Any]:
        """
        Liefert alle verfügbaren therapeutischen Ansätze
        
        Returns:
            Dictionary aller Ansätze
        """
        try:
            return {
                approach: {
                    'name': approach,
                    'description': f'Therapeutischer Ansatz: {approach}',
                    'available': True
                }
                for approach in self.therapeutic_approaches.keys()
            }
            
        except Exception as e:
            logger.error(f"❌ All-Approaches-Abruf fehlgeschlagen: {e}")
            return {}
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Liefert Statistiken über verfügbare Prompts und LLM-Features
        
        Returns:
            Prompt-Statistiken mit LLM-Capabilities
        """
        try:
            stats = {
                'base_prompts': {
                    'system_prompt': 1 if 'system_prompt' in self.base_prompts else 0,
                    'llm_detection_prompts': 2,  # wellbeing_detection, crisis_detection
                    'fallback_indicators': len(self.base_prompts.get('fallback_crisis_indicators', [])),
                    'wellbeing_fallbacks': len(self.base_prompts.get('fallback_wellbeing_indicators', []))
                },
                'therapeutic_approaches': len(self.therapeutic_approaches),
                'crisis_types': len(self.crisis_prompts),
                'coping_strategies': {
                    'llm_prompts': len([k for k in self.coping_resources.keys() if k.endswith('_prompt')]),
                    'hardcoded_contacts': 1  # emergency_contacts
                },
                'llm_capabilities': {
                    'psychological_detection': True,
                    'crisis_assessment': True,
                    'therapeutic_approach_selection': True,
                    'coping_strategy_generation': True,
                    'resource_recommendations': True,
                    'personalized_welcome': True,
                    'crisis_guidance_generation': True
                },
                'system_type': 'LLM-first with minimal fallbacks'
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Statistik-Abruf fehlgeschlagen: {e}")
            return {}

    # === NEUE LLM-BASIERTE HAUPTMETHODEN ===
    
    def get_full_llm_assessment(self, user_input: str, 
                               session_context: Optional[Dict[str, Any]] = None,
                               chat_function: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
        """
        Vollständige LLM-basierte Einschätzung einer Benutzer-Nachricht
        
        Args:
            user_input: Benutzer-Eingabe
            session_context: Session-Kontext
            chat_function: Chat-Funktion für LLM
            
        Returns:
            Umfassende Einschätzung und Empfehlungen
        """
        try:
            assessment = {
                'needs_wellbeing_support': False,
                'crisis_risk': 'low',
                'recommended_approach': 'general_support',
                'immediate_actions': [],
                'system_prompt': '',
                'assessment_method': 'fallback'
            }
            
            if chat_function and callable(chat_function):
                # 1. Psychologische Unterstützung nötig?
                assessment['needs_wellbeing_support'] = self.should_handle_wellbeing(
                    user_input, chat_function
                )
                
                if assessment['needs_wellbeing_support']:
                    # 2. Krisenrisiko bewerten
                    crisis_data = self.assess_crisis_risk(user_input, chat_function)
                    assessment['crisis_risk'] = crisis_data.get('risk_level', 'low')
                    
                    # 3. Therapeutischen Ansatz wählen
                    recommended_approach = self.suggest_therapeutic_approach_llm(
                        user_input, session_context.get('history', []) if session_context else None, chat_function
                    )
                    assessment['recommended_approach'] = recommended_approach
                    
                    # 4. System-Prompt generieren
                    assessment['system_prompt'] = self.get_care_prompt(
                        str(recommended_approach), user_input=user_input
                    )
                    
                    # 5. Sofortmaßnahmen bei hohem Risiko
                    if assessment['crisis_risk'] == 'high':
                        assessment['immediate_actions'] = [
                            "Sofortige Sicherheit sicherstellen",
                            "Professionelle Hilfe kontaktieren",
                            "Notfallkontakte bereithalten"
                        ]
                    
                    assessment['assessment_method'] = 'llm'
                    logger.info(f"✅ Vollständige LLM-Einschätzung abgeschlossen")
            
            return assessment
            
        except Exception as e:
            logger.error(f"❌ LLM-Assessment fehlgeschlagen: {e}")
            # Fallback Assessment
            return {
                'needs_wellbeing_support': False,
                'crisis_risk': 'unknown',
                'recommended_approach': 'general_support',
                'immediate_actions': [],
                'system_prompt': self.get_system_prompt(),
                'assessment_method': 'error',
                'error': str(e)
            }

    # === INTERFACE-KOMPATIBLE METHODEN ===
    
    def get_general_support_prompt(self) -> str:
        """Allgemeiner Unterstützungs-Prompt"""
        base = self.get_system_prompt()
        if 'general_support' in self.therapeutic_approaches:
            addition = self.therapeutic_approaches['general_support'].get('prompt_addition', '')
            return base + str(addition)
        return base
    
    def get_anxiety_support_prompt(self) -> str:
        """Angst-spezifischer Unterstützungs-Prompt"""
        base = self.get_system_prompt()
        if 'anxiety_support' in self.therapeutic_approaches:
            addition = self.therapeutic_approaches['anxiety_support'].get('prompt_addition', '')
            return base + str(addition)
        return base
    
    def get_depression_support_prompt(self) -> str:
        """Depressions-spezifischer Unterstützungs-Prompt"""
        base = self.get_system_prompt()
        if 'depression_support' in self.therapeutic_approaches:
            addition = self.therapeutic_approaches['depression_support'].get('prompt_addition', '')
            return base + str(addition)
        return base
    
    def get_stress_management_prompt(self) -> str:
        """Stress-Management-spezifischer Prompt"""
        base = self.get_system_prompt()
        if 'stress_management' in self.therapeutic_approaches:
            addition = self.therapeutic_approaches['stress_management'].get('prompt_addition', '')
            return base + str(addition)
        return base
    
    def get_relationship_support_prompt(self) -> str:
        """Beziehungs-spezifischer Unterstützungs-Prompt"""
        base = self.get_system_prompt()
        if 'relationship_support' in self.therapeutic_approaches:
            addition = self.therapeutic_approaches['relationship_support'].get('prompt_addition', '')
            return base + str(addition)
        return base
    
    def get_crisis_guidance(self) -> str:
        """Krisenintervention-Guidance für LLM"""
        guidance = self.crisis_prompts.get('crisis_guidance', 'Verwende empathische Krisenintervention.')
        return str(guidance)
    
    def get_emergency_contacts(self, country: str = 'germany') -> Dict[str, str]:
        """Hole Notfall-Kontakte für ein Land"""
        contacts = self.crisis_prompts.get('emergency_contacts', {}).get(country, {})
        # Ensure we return Dict[str, str] not Any
        return {str(k): str(v) for k, v in contacts.items()} if isinstance(contacts, dict) else {}
    
    def should_handle_wellbeing(self, message: str, chat_function: Optional[Callable[..., Any]] = None) -> bool:
        """
        LLM-basierte Entscheidung ob Nachricht psychologisch behandelt werden sollte
        
        Args:
            message: Benutzer-Nachricht
            chat_function: Chat-Funktion für LLM-Anfrage
            
        Returns:
            True wenn psychologische Behandlung empfohlen wird
        """
        try:
            # Primär: LLM-basierte Erkennung
            if chat_function and callable(chat_function):
                try:
                    detection_prompt = self.base_prompts['wellbeing_detection_prompt'].format(message=message)
                    llm_response = chat_function(detection_prompt)
                    
                    response_text = str(llm_response).strip().upper()
                    if 'JA' in response_text:
                        logger.info(f"✅ LLM erkannte psychologischen Bedarf: {message[:50]}...")
                        return True
                    elif 'NEIN' in response_text:
                        logger.debug(f"ℹ️ LLM erkannte KEINEN psychologischen Bedarf: {message[:50]}...")
                        return False
                    else:
                        logger.warning(f"⚠️ LLM-Antwort unklarer: '{response_text}', nutze Fallback")
                        
                except Exception as e:
                    logger.warning(f"⚠️ LLM-Erkennung fehlgeschlagen: {e}, nutze Fallback")
            
            # Fallback: Minimal-Keyword-Check
            message_lower = message.lower()
            fallback_indicators = self.base_prompts.get('fallback_wellbeing_indicators', [])
            
            for indicator in fallback_indicators:
                if indicator in message_lower:
                    logger.info(f"📝 Fallback erkannte psychologischen Bedarf: '{indicator}' in Nachricht")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Psychologische Erkennung fehlgeschlagen: {e}")
            return False

    def suggest_therapeutic_approach_llm(self, user_input: str, 
                                        session_history: Optional[List[Dict[str, Any]]] = None,
                                        chat_function: Optional[Callable[..., Any]] = None) -> str:
        """
        LLM-basierte Auswahl des therapeutischen Ansatzes
        
        Args:
            user_input: Aktuelle Benutzer-Eingabe
            session_history: Bisherige Session-Historie  
            chat_function: Chat-Funktion für LLM-Anfrage
            
        Returns:
            Empfohlener therapeutischer Ansatz
        """
        try:
            # LLM-basierte Ansatzwahl
            if chat_function and callable(chat_function):
                try:
                    # Kontext aus Session-Historie aufbauen
                    context = ""
                    if session_history:
                        recent_messages = session_history[-3:]  # Letzte 3 Nachrichten
                        context = "Kontext der letzten Nachrichten:\n" + "\n".join([
                            f"- {msg.get('content', '')[:100]}..." 
                            for msg in recent_messages
                        ]) + "\n\n"
                    
                    approach_prompt = f"""
{context}Aktuelle Nachricht: "{user_input}"

Wähle den besten therapeutischen Ansatz für diese Situation:

VERFÜGBARE ANSÄTZE:
- anxiety_support: Für Ängste, Panikattacken, Sorgen
- depression_support: Für Depression, Hoffnungslosigkeit, Niedergeschlagenheit  
- stress_management: Für Stress, Überforderung, Burnout
- relationship_support: Für Beziehungsprobleme, Konflikte
- general_support: Für allgemeine emotionale Unterstützung

Antworte nur mit dem Ansatz-Namen, keine Erklärung nötig.
"""
                    
                    llm_response = chat_function(approach_prompt)
                    # Ensure string output
                    response_text = str(llm_response).strip().lower() if llm_response else ''
                    
                    # Validiere LLM-Antwort
                    valid_approaches: List[str] = list(self.therapeutic_approaches.keys())
                    for approach in valid_approaches:
                        if approach in response_text:
                            logger.info(f"🎯 LLM wählte therapeutischen Ansatz: {approach}")
                            # approach is guaranteed to be str from the list
                            return str(approach)
                    
                    logger.warning(f"⚠️ LLM-Ansatz unbekannt: '{response_text}', nutze Fallback")
                    
                except Exception as e:
                    logger.warning(f"⚠️ LLM-Ansatzwahl fehlgeschlagen: {e}, nutze Fallback")
            
            # Fallback: Regel-basierte Ansatzwahl (vereinfacht)
            return self.suggest_therapeutic_approach(user_input, session_history)
            
        except Exception as e:
            logger.error(f"❌ Therapeutische Ansatzwahl fehlgeschlagen: {e}")
            return 'general_support'

    def get_llm_crisis_guidance(self, crisis_level: str, message: str, chat_function: Optional[Callable[..., Any]] = None) -> str:
        """
        LLM-generierte Krisen-Guidance für spezifische Situation
        
        Args:
            crisis_level: Risikolevel ('high', 'medium', 'low')
            message: Originalnachricht
            chat_function: Chat-Funktion für LLM
            
        Returns:
            Spezifische Krisen-Guidance
        """
        try:
            if chat_function and callable(chat_function):
                guidance_prompt = f"""
Erstelle spezifische Krisen-Interventions-Guidance für diese Situation:

RISIKOLEVEL: {crisis_level.upper()}
NACHRICHT: "{message}"

Erstelle eine empathische aber professionelle Antwort die:
1. Sofortige Sicherheit adressiert (bei hohem Risiko)
2. Professionelle Hilfe erwähnt wenn nötig  
3. Konkrete nächste Schritte vorschlägt
4. Hoffnung und Unterstützung vermittelt
5. Notfallkontakte erwähnt wenn angebracht

Antworte direkt als therapeutische Intervention, nicht als Anweisung.
"""
                
                guidance = chat_function(guidance_prompt)
                logger.info(f"✨ LLM-generierte Krisen-Guidance für Level: {crisis_level}")
                # Ensure string output
                return str(guidance) if guidance else "Bitte suche professionelle Hilfe."
            
            # Fallback - ensure str return
            fallback_guidance = self.crisis_prompts.get('crisis_guidance', 'Bitte suche professionelle Hilfe.')
            return str(fallback_guidance) if fallback_guidance is not None else 'Bitte suche professionelle Hilfe.'
            
        except Exception as e:
            logger.error(f"❌ LLM-Krisen-Guidance fehlgeschlagen: {e}")
            return "Bei Krisen wende dich bitte an professionelle Hilfe oder Notfallkontakte."

    def get_llm_resource_recommendations(self, user_situation: str, 
                                       approach: str, 
                                       chat_function: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
        """
        LLM-generierte Ressourcenempfehlungen basierend auf der spezifischen Situation
        
        Args:
            user_situation: Beschreibung der Benutzer-Situation
            approach: Therapeutischer Ansatz
            chat_function: Chat-Funktion für LLM
            
        Returns:
            Dictionary mit verschiedenen Ressourcentypen
        """
        try:
            if not chat_function or not callable(chat_function):
                return self._get_fallback_resources(approach)
            
            resource_prompt = f"""
Basierend auf dieser Situation und dem therapeutischen Ansatz, empfehle konkrete Ressourcen:

SITUATION: {user_situation}
ANSATZ: {approach}

Erstelle Empfehlungen in folgenden Kategorien:

1. SOFORTMASSNAHMEN (3-4 konkrete Schritte)
2. LANGFRISTIGE STRATEGIEN (3-4 nachhaltige Ansätze)  
3. PROFESSIONELLE RESSOURCEN (wenn angebracht)
4. SELBSTHILFE-AKTIVITÄTEN (3-4 praktische Übungen)

Formatiere als strukturierte Liste mit klaren Kategorien.
Fokus auf praktische, umsetzbare Empfehlungen.
"""
            
            llm_response = chat_function(resource_prompt)
            response_text = str(llm_response)
            
            # Parse strukturierte Antwort
            resources: Dict[str, Any] = {
                'immediate_actions': [],
                'long_term_strategies': [],
                'professional_resources': [],
                'self_help_activities': [],
                'emergency_contacts': self.get_emergency_contacts(),
                'generated_by': 'llm'
            }
            
            # Einfaches Parsing der strukturierten Antwort
            current_category = None
            lines = response_text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                # Kategorien erkennen
                line_upper = line.upper()
                if 'SOFORTMASSNAHMEN' in line_upper or 'IMMEDIATE' in line_upper:
                    current_category = 'immediate_actions'
                elif 'LANGFRISTIGE' in line_upper or 'LONG' in line_upper:
                    current_category = 'long_term_strategies'
                elif 'PROFESSIONELLE' in line_upper or 'PROFESSIONAL' in line_upper:
                    current_category = 'professional_resources'
                elif 'SELBSTHILFE' in line_upper or 'SELF' in line_upper:
                    current_category = 'self_help_activities'
                elif current_category and (line.startswith('-') or line.startswith('•') or line[0].isdigit()):
                    # Füge Eintrag zur aktuellen Kategorie hinzu
                    clean_line = line.lstrip('0123456789.- •').strip()
                    if clean_line and len(clean_line) > 5:
                        resources[current_category].append(clean_line)
            
            logger.info(f"✨ LLM-generierte Ressourcenempfehlungen für: {approach}")
            return resources
            
        except Exception as e:
            logger.error(f"❌ LLM-Ressourcenempfehlung fehlgeschlagen: {e}")
            return self._get_fallback_resources(approach)
    
    def _get_fallback_resources(self, approach: str) -> Dict[str, Any]:
        """Fallback-Ressourcen für Offline-Situationen"""
        return {
            'immediate_actions': [
                "Tief durchatmen und sich einen Moment Zeit nehmen",
                "Sich an einen ruhigen, sicheren Ort begeben",
                "Bei akuten Problemen Hilfe suchen"
            ],
            'long_term_strategies': [
                "Regelmäßige Selbstfürsorge praktizieren",
                "Unterstützungsnetzwerk aufbauen",
                "Bei Bedarf professionelle Hilfe suchen"
            ],
            'professional_resources': [
                "Hausarzt oder Therapeut kontaktieren",
                "Beratungsstellen in der Nähe suchen"
            ],
            'self_help_activities': [
                "Entspannungsübungen praktizieren",
                "Tagebuch führen",
                "Körperliche Aktivität"
            ],
            'emergency_contacts': self.get_emergency_contacts(),
            'generated_by': 'fallback'
        }
