#!/usr/bin/env python3
"""
ENHANCED LLM KNOWLEDGE GRAPH EXTRACTOR
=======================================

Refactored mit:
- GuaranteedLLMCaller (niemals leere Responses)
- RobustResponseHandler (multi-method JSON parsing)
- Detailed Logging & Transparency
- Improved Error Handling

Author: AI System Evolution
Date: 2025-10-05 (Refactored)
"""

import json
import re
import logging
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass

# Import neue Utilities
from llm_utils.guaranteed_caller import GuaranteedLLMCaller
from llm_utils.robust_response_handler import RobustResponseHandler

logger = logging.getLogger(__name__)


# ============================================================================
# NORMALISIERUNGSFUNKTION FÜR KG-TRIPLES
# ============================================================================

def normalize_text(text: str) -> str:
    """
    Normalisiert Text für konsistente KG-Triple-Formatierung.
    Ersetzt Unterstriche durch Leerzeichen, behält aber technische IDs bei.
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


@dataclass
class KGTriple:
    """Repräsentiert ein Knowledge Graph Triple"""
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0
    source_text: str = ""
    
    def to_tuple(self) -> Tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)
    
    def __str__(self) -> str:
        return f"{self.subject} → {self.predicate} → {self.object}"


class EnhancedLLMKnowledgeGraphExtractor:
    """
    Enhanced LLM-basierte Knowledge Graph Extraktion.
    
    VERBESSERUNGEN:
    ✅ Garantiert niemals leere LLM-Responses
    ✅ Multi-Methoden JSON-Parsing
    ✅ Robuste Fehlerbehandlung
    ✅ Detailed Logging ohne stille Fehler
    ✅ Graceful Degradation mit informativen Fallbacks
    """
    
    def __init__(self, llm_client=None, max_chunk_size: int = 1000):
        """
        Args:
            llm_client: LLM-Client (wird automatisch geladen falls None)
            max_chunk_size: Maximale Chunk-Größe für LLM-Verarbeitung
        """
        self.llm_raw = llm_client
        self.max_chunk_size = max_chunk_size
        self.llm_caller: Optional[GuaranteedLLMCaller] = None
        
        # Initialisiere LLM falls nicht vorhanden
        if self.llm_raw is None:
            self.llm_raw = self._init_llm_client()
        
        # Wrape LLM mit Guaranteed Caller
        if self.llm_raw:
            self.llm_caller = GuaranteedLLMCaller(
                self.llm_raw,
                max_retries=3,
                default_temperature=0.1
            )
        else:
            self.llm_caller = None
            logger.warning("⚠️ KG-Extractor ohne LLM - verwende Fallback-Methoden")
        
        # Response Handler für JSON-Parsing
        self.response_handler = RobustResponseHandler()
        
        logger.info("✅ Enhanced KG Extractor initialisiert")
    
    def extract_knowledge_graph(
        self,
        text: str,
        doc_context: Optional[Dict[str, Any]] = None
    ) -> List[KGTriple]:
        """
        Extrahiert Knowledge Graph aus Text mit robusten Fallbacks.
        
        Args:
            text: Eingabetext
            doc_context: Dokumentkontext (Metadaten, Titel, etc.)
            
        Returns:
            Liste von KG-Triples
        """
        
        if not text or len(text.strip()) < 20:
            logger.warning("⚠️ Text zu kurz für KG-Extraktion")
            return []
        
        if not self.llm_caller:
            logger.warning("❌ Kein LLM verfügbar - verwende Fallback")
            return self._fallback_extraction(text)
        
        try:
            # Text in verarbeitbare Chunks aufteilen
            chunks = self._chunk_text(text)
            all_triples = []
            
            for i, chunk in enumerate(chunks):
                logger.debug(f"🧠 Verarbeite Chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
                
                # LLM-Prompt für KG-Extraktion
                prompt = self._create_kg_prompt(chunk, doc_context)
                
                # 🔥 CRITICAL: System-Prompt um empathisches Verhalten zu unterdrücken
                system_prompt = """You are a precise data extraction system. Your ONLY task is to output valid JSON.
DO NOT engage in conversation. DO NOT provide explanations. DO NOT be empathetic.
ONLY output the requested JSON structure, nothing else."""
                
                # LLM-Call mit Garantie und System-Prompt
                llm_result = self.llm_caller.call_with_guarantee(
                    prompt=prompt,
                    max_tokens=2048,
                    temperature=0.1,
                    system_prompt=system_prompt,
                    response_validator=self._is_valid_kg_response,
                )
                
                logger.debug(f"📊 LLM KG Call: success={llm_result.success}, "
                            f"attempts={llm_result.attempts}, "
                            f"response_length={len(llm_result.response)}")
                
                if not llm_result.success:
                    logger.warning(
                        "KG LLM call failed for chunk %d/%d after %d attempts: %s",
                        i + 1,
                        len(chunks),
                        llm_result.attempts,
                        llm_result.error_message or "unknown error",
                    )
                    chunk_triples = self._fallback_extraction(chunk)
                else:
                    chunk_triples = self._parse_llm_response(llm_result.response, chunk)
                
                logger.debug(f"✅ Chunk {i+1}: {len(chunk_triples)} Triples extrahiert")
                all_triples.extend(chunk_triples)
            
            # Duplikate entfernen und Qualitätsfilterung
            filtered_triples = self._filter_and_deduplicate(all_triples)
            
            logger.info(f"✅ {len(filtered_triples)} hochwertige KG-Triples extrahiert "
                       f"aus {len(chunks)} Chunks")
            return filtered_triples
            
        except Exception as e:
            logger.error(f"❌ LLM-KG-Extraktion fehlgeschlagen: {e}", exc_info=True)
            return self._fallback_extraction(text)

    @staticmethod
    def _is_valid_kg_response(response: str) -> bool:
        """Validate the JSON envelope independently of its character length."""
        candidate = response.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL)
        if fenced:
            candidate = fenced.group(1).strip()

        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return False

        return isinstance(payload, dict) and isinstance(payload.get("triples"), list)
    
    def _parse_llm_response(self, response: str, source_text: str) -> List[KGTriple]:
        """Parsed LLM-Response und extrahiert KG-Triples mit robusten Methoden"""
        
        logger.debug(f"📄 Parsing KG Response ({len(response)} chars)")
        
        # Verwende RobustResponseHandler für JSON-Parsing
        parsing_result = self.response_handler.parse_llm_response(
            response,
            expected_keys=["triples"],
            required_keys=["triples"],
            default_values={"triples": []}
        )
        
        if not parsing_result.success:
            logger.warning(f"⚠️ KG Response parsing used fallback: {parsing_result.method_used}")
            # Versuche Regex-Fallback
            return self._regex_parse_response(response, source_text)
        
        # Extrahiere Triples aus geparsten Daten
        data = parsing_result.data
        # ✅ FIX: Sicherer Zugriff auf data (könnte None sein)
        if data is None:
            logger.warning("⚠️ Parsing result data is None, using regex fallback")
            return self._regex_parse_response(response, source_text)
        
        triples_data = data.get("triples", [])
        
        if not isinstance(triples_data, list):
            logger.warning(f"⚠️ 'triples' ist kein Array: {type(triples_data)}")
            return self._regex_parse_response(response, source_text)
        
        triples = []
        for triple_data in triples_data:
            if not isinstance(triple_data, dict):
                logger.debug(f"❌ Triple ist kein Dict: {triple_data}")
                continue
            
            # Prüfe Required Fields
            if not all(key in triple_data for key in ["subject", "predicate", "object"]):
                logger.debug(f"❌ Triple fehlt required fields: {triple_data}")
                continue
            
            # Erstelle Triple mit Normalisierung
            triple = KGTriple(
                subject=normalize_text(str(triple_data["subject"]).strip()),
                predicate=normalize_text(str(triple_data["predicate"]).strip()),
                object=normalize_text(str(triple_data["object"]).strip()),
                confidence=float(triple_data.get("confidence", 0.8)),
                source_text=source_text[:200] + "..."
            )
            
            # Validiere Triple-Qualität
            if self._is_valid_triple(triple):
                triples.append(triple)
                logger.debug(f"✅ Valid triple: {triple.subject} | {triple.predicate} | {triple.object}")
            else:
                logger.debug(f"❌ Invalid triple: {triple}")
        
        logger.debug(f"✅ Parsed {len(triples)} valid triples")
        return triples
    
    def _regex_parse_response(self, response: str, source_text: str) -> List[KGTriple]:
        """Fallback: Regex-basiertes Parsing"""
        logger.debug("🔄 Using regex fallback parsing...")
        triples = []
        
        # Pattern 1: Subject | Predicate | Object
        pattern1 = r'([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|\n]+)'
        for match in re.finditer(pattern1, response):
            subject, predicate, obj = match.groups()
            triple = KGTriple(
                subject=normalize_text(subject.strip()),
                predicate=normalize_text(predicate.strip()),
                object=normalize_text(obj.strip()),
                confidence=0.7,
                source_text=source_text[:200] + "..."
            )
            if self._is_valid_triple(triple):
                triples.append(triple)
        
        # Pattern 2: Subject → Predicate → Object
        pattern2 = r'([^→]+)\s*→\s*([^→]+)\s*→\s*([^→\n]+)'
        for match in re.finditer(pattern2, response):
            subject, predicate, obj = match.groups()
            triple = KGTriple(
                subject=normalize_text(subject.strip()),
                predicate=normalize_text(predicate.strip()),
                object=normalize_text(obj.strip()),
                confidence=0.7,
                source_text=source_text[:200] + "..."
            )
            if self._is_valid_triple(triple):
                triples.append(triple)
        
        logger.debug(f"📊 Regex fallback found {len(triples)} triples")
        return triples
    
    # ★ PRONOMEN-SET für Pronomen-Erkennung in Triples
    _PRONOUN_SET = {
        # Deutsch - Personalpronomen (alle Kasus)
        'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr',
        'mich', 'dich', 'ihn', 'uns', 'euch',
        'mir', 'dir', 'ihm', 'ihnen',
        'mein', 'dein', 'sein', 'unser', 'euer', 'ihrer',
        'meiner', 'deiner', 'seiner', 'unserer', 'eurer',
        'meine', 'deine', 'seine', 'unsere', 'eure', 'ihre',
        # Deutsch - Demonstrativ/Relativ/Indefinit
        'dieser', 'diese', 'dieses', 'jener', 'jene', 'jenes',
        'man', 'jemand', 'niemand', 'einer', 'keiner',
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
    }

    def _is_valid_triple(self, triple: KGTriple) -> bool:
        """Validiert Triple-Qualität (strukturell + Pronomen-Check)"""
        # Mindestlängen
        if len(triple.subject) < 2 or len(triple.predicate) < 3 or len(triple.object) < 2:
            return False
        
        # Keine leeren oder nur Whitespace-Werte
        if not triple.subject.strip() or not triple.predicate.strip() or not triple.object.strip():
            return False
        
        # ★ PRONOMEN-FIX: Reject triples mit Pronomen als Subject/Object
        if triple.subject.strip().lower() in self._PRONOUN_SET:
            logger.debug(f"⚠️ Pronomen als Subject abgelehnt: '{triple.subject}'")
            return False
        if triple.object.strip().lower() in self._PRONOUN_SET:
            logger.debug(f"⚠️ Pronomen als Object abgelehnt: '{triple.object}'")
            return False
        
        # ★ None/Null-Check
        _NONE_VALUES = {'none', 'null', 'n/a', '-', 'undefined', 'unbekannt', 'na', 'nil', '...', 'unknown'}
        if triple.subject.strip().lower() in _NONE_VALUES or triple.object.strip().lower() in _NONE_VALUES:
            return False
        
        # Keine Duplikate (Subject == Object)
        if triple.subject.lower() == triple.object.lower():
            return False
        
        # ✅ KEINE hartcodierten Keywords mehr!
        # Meta-Triple-Detection erfolgt DURCH DEN LLM-PROMPT selbst
        return True
    
    def _filter_and_deduplicate(self, triples: List[KGTriple]) -> List[KGTriple]:
        """Entfernt Duplikate und filtert niedrige Qualität"""
        seen = set()
        filtered = []
        
        for triple in triples:
            # Normalisiere für Duplikat-Check
            key = (
                triple.subject.lower().strip(),
                triple.predicate.lower().strip(),
                triple.object.lower().strip()
            )
            
            if key not in seen:
                seen.add(key)
                filtered.append(triple)
        
        logger.debug(f"🔍 Deduplicated {len(triples)} → {len(filtered)} triples")
        return filtered
    
    def _chunk_text(self, text: str) -> List[str]:
        """Teilt Text in LLM-verarbeitbare Chunks"""
        if len(text) <= self.max_chunk_size:
            return [text]
        
        # Intelligente Chunk-Aufteilung an Satzgrenzen
        sentences = re.split(r'[.!?]+', text)
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= self.max_chunk_size:
                current_chunk += sentence + ". "
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence + ". "
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    def _create_kg_prompt(self, text: str, doc_context: Optional[Dict]) -> str:
        """Erstellt Prompt für KG-Extraktion mit LLM-basierter Meta-Triple-Filterung"""
        
        context_info = ""
        is_therapeutic = False
        user_name = ""  # Echter Name für konsistente Triples
        
        if doc_context:
            is_therapeutic = doc_context.get("source_type") == "psychology"
            user_name = doc_context.get("user_name", "")  # Name aus Session-Context
            context_info = f"\n\nKONTEXT:\n{json.dumps(doc_context, indent=2, ensure_ascii=False)}"
        
        # Bestimme Subject-Bezeichnung (Name bevorzugt, sonst "User")
        subject_name = user_name if user_name and len(user_name.strip()) > 0 else "User"
        
        # Therapeutischer Prompt (nur User-Fakten, LLM filtert semantisch)
        if is_therapeutic:
            return f"""Du bist ein präziser Knowledge Graph Extractor für therapeutische Gespräche. 

WICHTIG: Antworte AUSSCHLIESSLICH mit dem JSON-Objekt! Keine Erklärungen, keine Begrüßungen, nur JSON!

🔑 WICHTIG - ICH-FORM ZU NAME KONVERTIEREN:
Die Person, die spricht, heißt "{subject_name}".
Wenn im Text "ich", "mich", "mir", "mein", "meine" steht, ersetze es durch "{subject_name}"!

🔑 WICHTIG - PRONOMEN AUFLÖSEN (er/sie/es/wir):
Verwende NIEMALS Pronomen wie "er", "sie", "es", "wir", "man" als Subject oder Object!
Ersetze jedes Pronomen durch die konkrete Person/Entität, auf die es sich bezieht.
- "Er ist streng" → Wenn vorher von "Vater" die Rede war: "Vater → ist → streng"
- "Sie lebt in Berlin" → Wenn vorher von "Mutter" die Rede war: "Mutter → lebt in → Berlin"
- Wenn nicht klar ist, auf wen sich ein Pronomen bezieht → Triple ÜBERSPRINGEN!

Beispiele:
- "Ich habe Angst" → "{subject_name} → hat → Angst"
- "Mein Vater ist streng" → "Vater → ist → streng" (Vater von {subject_name})
- "Ich liebe meine Mutter" → "{subject_name} → liebt → Mutter"

KONSISTENZ:
Verwende IMMER "{subject_name}" als Subject für die sprechende Person.
NIEMALS "User", "Benutzer", "Klient", "Ich", "der Nutzer" - NUR "{subject_name}"!

AUFGABE:
Extrahiere **NUR KONKRETE, ÜBERPRÜFBARE FAKTEN ÜBER {subject_name.upper()}** aus dem folgenden Text.

⚠️ KRITISCH: FRAGEN UND META-AUSSAGEN ENTHALTEN KEINE FAKTEN!
Wenn der Text NUR eine Frage ist (z.B. "Was weißt Du über X?"), dann gibt es NICHTS zu extrahieren!
→ Antworte mit: {{"triples": []}}

✅ EXTRAHIERE NUR:
1. Fakten über Personen und ihre Eigenschaften:
   - "Vater → arbeitet als → Ingenieur"
   - "Mutter → lebt in → Berlin"
   - "Schwester → ist → 25 Jahre alt"

2. Fakten über Beziehungen und Dynamiken:
   - "Vater → hat Konflikt mit → Sohn"
   - "{subject_name} → liebt → Hristina"

3. Fakten über Ereignisse und Erlebnisse:
   - "{subject_name} → erlebte → Scheidung der Eltern"
   - "Vater → verließ → Familie im Jahr 2010"

❌ IGNORIERE KOMPLETT:
1. NEGATIONEN (nicht/keine/kein):
   - ❌ "{subject_name} → hat nicht → über X gesprochen"
   - ❌ "Vater → zeigt keine → Emotionen"
   
2. GESPRÄCHSVERLÄUFE (sagt/fragt/spricht/erwähnt):
   - ❌ "{subject_name} → sagt → etwas"
   - ❌ "{subject_name} → fragt → nach X"
   - ❌ "{subject_name} → spricht über → Familie"
   
3. ABSICHTEN & WÜNSCHE (will/möchte/sollte/könnte):
   - ❌ "{subject_name} → will → über X sprechen"
   - ❌ "{subject_name} → möchte → Hilfe"
   - ❌ "{subject_name} → sollte → etwas tun"
   
4. META-INFORMATIONEN über das Gespräch:
   - ❌ "{subject_name} → hat Erlaubnis → gegeben"
   - ❌ "{subject_name} → ist erster Kontakt → true"
   
5. FRAGEN in Object oder Predicate:
   - ❌ Alles mit "?" im Triple

BEISPIELE FÜR GUTE TRIPLES:
- ✅ "Vater → ist → Alkoholiker"
- ✅ "Mutter → wohnt in → Hamburg"
- ✅ "{subject_name} → hat → zwei Kinder"
- ✅ "Oma → starb → 2015"

BEISPIELE FÜR SCHLECHTE TRIPLES (IGNORIEREN!):
- ❌ "{subject_name} → hat nicht gesprochen über → Vater"
- ❌ "{subject_name} → fragt → was ich über Vater weiß"
- ❌ "{subject_name} → will sprechen über → Familie"
- ❌ "{subject_name} → hat keine Erfahrung mit → Snowflake"

DOPPELCHECK - IST DAS TRIPLE EIN FAKT?
Frage dich bei jedem Triple:
1. Ist das eine überprüfbare Tatsache? (JA → extrahieren, NEIN → ignorieren)
2. Enthält es Negation? (JA → ignorieren)
3. Ist es eine Gesprächs-Meta-Info? (JA → ignorieren)
4. Ist es eine Absicht/Wunsch? (JA → ignorieren)
   - "kann nicht helfen" → NICHT extrahieren
   - "zeigt Interesse an" → NICHT extrahieren (ist Meta-Info!)

PRÜFUNG NACH DER EXTRAKTION:
Für jedes Triple, das du extrahieren willst, stelle dir diese Frage:
"Ist das eine KONKRETE TATSACHE über {subject_name} oder seine/ihre Welt?"
- Falls JA → extrahieren
- Falls NEIN (Frage, Meta-Aussage, Absichtserklärung, etc.) → NICHT extrahieren

TRIPLE-STRUKTUR:
1. Subject = "{subject_name}", Familienmitglied, oder relevante Person/Entität
2. Predicate = Konkrete Beziehung (z.B. "hat", "fühlt", "erlebt", "ist")
3. Object = Eigenschaft, Gefühl, Ereignis
4. Confidence = 0.0-1.0

BEISPIELE - KORREKT (Fakten extrahieren mit Ich-Form-Konvertierung):
Text: "Ich habe Probleme mit meinem Vater. Er ist sehr streng."
→ "Ich" wird zu "{subject_name}"
Output:
{{
  "triples": [
    {{"subject": "{subject_name}", "predicate": "hat_Probleme_mit", "object": "Vater", "confidence": 1.0}},
    {{"subject": "Vater", "predicate": "ist", "object": "streng", "confidence": 0.9}}
  ]
}}

Text: "Mir geht es heute nicht gut, ich fühle mich erschöpft."
Output:
{{
  "triples": [
    {{"subject": "{subject_name}", "predicate": "fühlt_sich", "object": "erschöpft", "confidence": 0.9}}
  ]
}}

BEISPIELE - KORREKT (Keine Fakten → leeres Array):
Text: "Was weißt Du über meinen Vater?"
Output:
{{
  "triples": []
}}
→ Begründung: Dies ist eine FRAGE, keine Tatsachenaussage. Enthält KEINE extrahierbaren Fakten.

Text: "Ich möchte über meine Beziehung zu meinem Vater sprechen."
Output:
{{
  "triples": []
}}
→ Begründung: "möchte sprechen" ist eine Meta-Absicht, keine Tatsache über den User

Text: "Kannst Du mir sagen, welche Informationen Du über meine Mutter hast?"
Output:
{{
  "triples": []
}}
→ Begründung: Dies ist eine Informationsanfrage, enthält KEINE neuen Fakten

BEISPIELE - FALSCH (Das darfst Du NICHT machen):
Text: "Was weißt Du über meinen Vater?"
Output FALSCH:
{{
  "triples": [
    {{"subject": "{subject_name}", "predicate": "zeigt_Interesse_an", "object": "Vater", "confidence": 0.8}},
    {{"subject": "{subject_name}", "predicate": "fragt_nach", "object": "Informationen_über_Vater", "confidence": 0.9}}
  ]
}}
→ WARUM FALSCH: "zeigt Interesse" und "fragt nach" sind META-Informationen über die Frage selbst, KEINE Fakten über {subject_name}!

TEXT ZUR ANALYSE ({subject_name.upper()}-NACHRICHT):
{text}
{context_info}

ANTWORT (NUR JSON, keine Erklärungen!):
"""
        
        # Standard-Prompt (für nicht-therapeutische Texte)
        return f"""Du bist ein präziser Knowledge Graph Extractor.

WICHTIG: Antworte AUSSCHLIESSLICH mit dem JSON-Objekt! Keine Erklärungen, nur JSON!

AUFGABE:
Extrahiere Knowledge Graph Triples (Subject-Predicate-Object) aus dem folgenden Text.
Konzentriere dich auf KONKRETE FAKTEN, nicht auf Meta-Aussagen oder Absichten.

REGELN:
1. Subject = Hauptentität (Person, Konzept, etc.)
2. Predicate = Beziehung (z.B. "hat", "entwickelt", "ist")
3. Object = Zielentität
4. Confidence = 0.0-1.0
5. NUR konkrete Fakten, KEINE Meta-Aussagen!
6. KEINE Pronomen (Ich, er, sie, es, wir, man) als Subject oder Object! Immer die konkrete Entität verwenden.
   Falls der konkrete Name nicht bestimmbar ist, überspringe das Triple.

BEISPIEL:
Text: "Das System verwendet Python 3.11 und FastAPI."
Output:
{{
  "triples": [
    {{"subject": "System", "predicate": "verwendet", "object": "Python 3.11", "confidence": 1.0}},
    {{"subject": "System", "predicate": "verwendet", "object": "FastAPI", "confidence": 1.0}}
  ]
}}

TEXT ZUR ANALYSE:
{text}
{context_info}

ANTWORT (NUR JSON):
"""
    
    def _fallback_extraction(self, text: str) -> List[KGTriple]:
        """Fallback: Pattern-basierte Extraktion ohne LLM"""
        logger.info("🔄 Using pattern-based fallback extraction...")
        triples = []
        
        # Einfache Pattern-Erkennung
        patterns = [
            (r'(\w+)\s+ist\s+(?:ein|eine|der|die|das)\s+(\w+)', "ist_eine"),
            (r'(\w+)\s+hat\s+(?:ein|eine|einen|der|die|das)?\s*(\w+)', "hat"),
            (r'(\w+)\s+(?:entwickelt|erstellt|gemacht)\s+(?:von|durch)\s+(\w+)', "entwickelt_von")
        ]
        
        for pattern, predicate in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                subject, obj = match.groups()
                triple = KGTriple(
                    subject=normalize_text(subject.strip()),
                    predicate=normalize_text(predicate),
                    object=normalize_text(obj.strip()),
                    confidence=0.5,
                    source_text=text[:200] + "..."
                )
                if self._is_valid_triple(triple):
                    triples.append(triple)
        
        logger.info(f"✅ Fallback extraction found {len(triples)} triples")
        return triples
    
    def _init_llm_client(self):
        """Initialisiert LLM-Client falls nicht vorhanden"""
        try:
            # Versuche bestehende Instanz zu holen (nur mit aktivem Streamlit-Kontext).
            import sys

            streamlit_module = sys.modules.get("streamlit")
            scriptrunner_module = sys.modules.get("streamlit.runtime.scriptrunner")
            get_ctx = getattr(scriptrunner_module, "get_script_run_ctx", None)

            if streamlit_module is not None and callable(get_ctx) and get_ctx() is not None:
                model_loader = streamlit_module.session_state.get('model_loader')
                if model_loader is not None:
                    logger.info("✅ Using existing ModelLoader from Streamlit")
                    return model_loader
        except Exception:
            pass
        
        # Versuche ModelLoader zu importieren
        try:
            from scripts.model_loader import ModelLoader
            
            # ✅ FIX: Prüfe ob globale Instanz existiert (ohne AttributeError)
            try:
                # Versuche direkt auf die Instanz zuzugreifen
                shared_instance = getattr(ModelLoader, '_shared_instance', None)
                if shared_instance is not None:
                    logger.info("✅ Using existing global ModelLoader instance")
                    return shared_instance
            except (AttributeError, TypeError):
                pass  # Kein Problem, erstelle neue Instanz
            
            # Erstelle neue Instanz
            logger.info("🔄 Creating new ModelLoader instance...")
            model_loader = ModelLoader()
            
            if hasattr(model_loader, 'llm') and model_loader.llm is not None:
                logger.info("✅ ModelLoader initialized successfully")
                return model_loader
            else:
                logger.warning("⚠️ ModelLoader has no loaded model")
                return None
                
        except Exception as e:
            logger.warning(f"⚠️ Could not initialize ModelLoader: {e}")
            return None


# Backward compatibility alias
LLMKnowledgeGraphExtractor = EnhancedLLMKnowledgeGraphExtractor
