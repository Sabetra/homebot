#!/usr/bin/env python3
"""
🔧 ROBUSTER LLM-JSON-PARSER
============================

Zentralisierte, fehlertolerante JSON-Extraktion aus LLM-Responses.

Problem:
- LLMs generieren oft ungültiges JSON (trailing commas, missing quotes, etc.)
- Markdown-Code-Blocks (```json ... ```)
- Text vor/nach dem JSON
- Kommentare im JSON
- Single-quotes statt double-quotes

Lösung:
- Multi-Methoden-Parsing mit Fallbacks
- Automatische Bereinigung häufiger Fehler
- Transparente Logging für Debugging
- Wiederverwendbar für alle LLM-JSON-Parser

Usage:
    >>> from utils.llm_json_parser import parse_llm_json
    >>> data = parse_llm_json(llm_response, schema_validator=validate_emotion_schema)
"""

import json
import re
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class LLMJSONParser:
    """
    Robuster JSON-Parser für LLM-Responses mit Multi-Methoden-Fallback
    """
    
    def __init__(self, debug: bool = False):
        """
        Initialize parser
        
        Args:
            debug: Enable verbose logging
        """
        self.debug = debug
        
    def parse(
        self, 
        response: str, 
        schema_validator: Optional[Callable[[Dict], bool]] = None,
        default_on_error: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Parsed LLM-Response zu JSON mit Multi-Methoden-Fallback
        
        Args:
            response: LLM-Response-String
            schema_validator: Optional validation function (returns True if valid)
            default_on_error: Default dict to return if all methods fail
            
        Returns:
            Parsed JSON dict
            
        Raises:
            ValueError: If all parsing methods fail and no default provided
        """
        if not response or not response.strip():
            if default_on_error is not None:
                return default_on_error
            raise ValueError("Response ist leer")
        
        # Methode 1: Code-Block-Extraktion (```json ... ```)
        try:
            data = self._parse_code_block(response)
            if schema_validator is None or schema_validator(data):
                if self.debug:
                    logger.debug("✅ JSON aus Code-Block extrahiert")
                return data
        except Exception as e:
            if self.debug:
                logger.debug(f"Methode 1 (Code-Block) fehlgeschlagen: {e}")
        
        # Methode 2: Bereinigtes JSON (cleanup + parse)
        try:
            data = self._parse_cleaned_json(response)
            if schema_validator is None or schema_validator(data):
                if self.debug:
                    logger.debug("✅ JSON nach Bereinigung geparst")
                return data
        except Exception as e:
            if self.debug:
                logger.debug(f"Methode 2 (Cleaned JSON) fehlgeschlagen: {e}")
        
        # Methode 3: Regex-basierte JSON-Extraktion
        try:
            data = self._parse_regex_json(response)
            if schema_validator is None or schema_validator(data):
                if self.debug:
                    logger.debug("✅ JSON via Regex extrahiert")
                return data
        except Exception as e:
            if self.debug:
                logger.debug(f"Methode 3 (Regex) fehlgeschlagen: {e}")
        
        # Methode 4: Aggressive Bereinigung (fix common JSON errors)
        try:
            data = self._parse_aggressive_cleanup(response)
            if schema_validator is None or schema_validator(data):
                logger.warning("⚠️ JSON nur nach aggressiver Bereinigung geparst")
                return data
        except Exception as e:
            if self.debug:
                logger.debug(f"Methode 4 (Aggressive Cleanup) fehlgeschlagen: {e}")
        
        # Methode 5: Truncated JSON Repair (max_tokens cutoff)
        try:
            data = self._repair_truncated_json(response)
            if schema_validator is None or schema_validator(data):
                logger.warning(
                    f"⚠️ JSON war durch max_tokens abgeschnitten — "
                    f"repariert mit {len(data.get('triples', []))} vollständigen Elementen"
                )
                return data
        except Exception as e:
            if self.debug:
                logger.debug(f"Methode 5 (Truncated Repair) fehlgeschlagen: {e}")
        
        # Alle Methoden fehlgeschlagen
        logger.error(f"❌ Alle JSON-Parse-Methoden fehlgeschlagen")
        logger.error(f"   Response-Preview: {response[:200]}...")
        logger.debug(f"   Vollständige Response: {response}")
        
        # ✅ FIX 6: Detailliertes Diagnostic-Logging
        logger.error(f"   📊 Response-Länge: {len(response)} Zeichen")
        logger.error(f"   🔤 Enthält ```json: {'```json' in response}")
        logger.error(f"   🔤 Enthält closing ```: {'```' in response[10:] if len(response) > 10 else False}")
        logger.error(f"   📋 Erste 50 chars: {repr(response[:50])}")
        logger.error(f"   📋 Letzte 50 chars: {repr(response[-50:])}")
        
        # UTF-8 Diagnostic
        try:
            encoded = response.encode('utf-8', errors='backslashreplace')
            # Decode bytes for proper string representation
            logger.error(f"   🔧 UTF-8 Bytes (erste 100): {encoded[:100]!r}")
            
            # Prüfe auf häufige Encoding-Probleme
            if b'\\x' in encoded[:500]:
                logger.error(f"   ⚠️ UTF-8 Encoding-Problem erkannt! (\\x escape sequences)")
        except Exception as e:
            logger.error(f"   ❌ UTF-8 Diagnostic fehlgeschlagen: {e}")
        
        if default_on_error is not None:
            logger.warning(f"⚠️ Verwende Default-Wert: {default_on_error}")
            return default_on_error
        
        raise ValueError(f"JSON-Parsing fehlgeschlagen nach 4 Versuchen")
    
    def _parse_code_block(self, response: str) -> Dict[str, Any]:
        """
        Methode 1: Extrahiere JSON aus Markdown-Code-Block
        
        ✅ FIX 3: Robuster gegen truncated Responses (ohne closing ```)
        """
        # Pattern-Set 1: Vollständige Code-Blocks (mit closing ```)
        patterns_complete = [
            r'```json\s*(\{.*?\})\s*```',  # ```json {...} ```
            r'```\s*(\{.*?\})\s*```',       # ``` {...} ```
        ]
        
        # Versuche zuerst vollständige Code-Blocks
        for pattern in patterns_complete:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                json_str = match.group(1)
                try:
                    result = json.loads(json_str)
                    # Type validation: ensure it's a dict
                    if not isinstance(result, dict):
                        if self.debug:
                            logger.debug(f"Code-Block ist kein Dict: {type(result)}")
                        continue
                    return result
                except json.JSONDecodeError as e:
                    if self.debug:
                        logger.debug(f"Code-Block gefunden, aber JSON invalid: {e}")
                    continue
        
        # ✅ FIX 3: Pattern-Set 2: Truncated Responses (ohne closing ```)
        # Wichtig für LLM-Responses, die durch max_tokens abgeschnitten wurden
        patterns_truncated = [
            r'```json\s*(\{.*)',           # ```json {...  (kein closing)
            r'```\s*(\{.*)',                # ``` {...     (kein closing)
        ]
        
        for pattern in patterns_truncated:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                json_str = match.group(1)
                
                # Entferne mögliche trailing ``` falls doch vorhanden
                json_str = re.sub(r'```\s*$', '', json_str)
                
                # Versuche JSON zu vervollständigen wenn Bracket fehlt
                open_braces = json_str.count('{')
                close_braces = json_str.count('}')
                if open_braces > close_braces:
                    # Fehlende closing brackets hinzufügen
                    missing_brackets = open_braces - close_braces
                    json_str += '}' * missing_brackets
                    if self.debug:
                        logger.debug(f"🔧 {missing_brackets} fehlende closing brackets hinzugefügt")
                
                try:
                    result = json.loads(json_str)
                    # Type validation: ensure it's a dict
                    if not isinstance(result, dict):
                        if self.debug:
                            logger.debug(f"Truncated Code-Block ist kein Dict: {type(result)}")
                        continue
                    return result
                except json.JSONDecodeError as e:
                    if self.debug:
                        logger.debug(f"Truncated Code-Block Parsing fehlgeschlagen: {e}")
                    continue
        
        raise ValueError("Kein Code-Block gefunden")
    
    def _parse_cleaned_json(self, response: str) -> Dict[str, Any]:
        """Methode 2: Bereinige Response und parse direkt"""
        # Entferne Code-Block-Marker
        cleaned = re.sub(r'```json\s*', '', response)
        cleaned = re.sub(r'```\s*', '', cleaned)
        
        # Finde JSON-Grenzen
        json_start = cleaned.find('{')
        json_end = cleaned.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError("Keine JSON-Grenzen gefunden")
        
        json_str = cleaned[json_start:json_end]
        result = json.loads(json_str)
        
        # Type validation: ensure it's a dict
        if not isinstance(result, dict):
            raise ValueError(f"Parsed JSON ist kein Dict sondern {type(result)}")
        
        return result
    
    def _parse_regex_json(self, response: str) -> Dict[str, Any]:
        """Methode 3: Regex-basierte JSON-Extraktion"""
        # Suche nach JSON-ähnlichen Strukturen
        pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        matches = re.findall(pattern, response, re.DOTALL)
        
        # Versuche alle Matches zu parsen
        for match in matches:
            try:
                result = json.loads(match)
                # Type validation: ensure it's a dict
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue
        
        raise ValueError("Kein gültiges JSON via Regex gefunden")
    
    def _parse_aggressive_cleanup(self, response: str) -> Dict[str, Any]:
        """Methode 4: Aggressive Bereinigung häufiger JSON-Fehler"""
        # Extrahiere JSON-Bereich
        json_start = response.find('{')
        json_end = response.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            raise ValueError("Keine JSON-Grenzen")
        
        json_str = response[json_start:json_end]
        
        # Fix 1: Entferne Trailing Commas
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        
        # Fix 2: Entferne Kommentare
        json_str = re.sub(r'//.*?\n', '\n', json_str)
        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
        
        # Fix 3: Ersetze Single-Quotes durch Double-Quotes (vorsichtig!)
        # Nur wenn keine Double-Quotes vorhanden
        if '"' not in json_str and "'" in json_str:
            json_str = json_str.replace("'", '"')
        
        # Fix 4: Entferne nicht-druckbare Zeichen
        json_str = ''.join(char for char in json_str if char.isprintable() or char in '\n\r\t')
        
        result = json.loads(json_str)
        
        # Type validation: ensure it's a dict
        if not isinstance(result, dict):
            raise ValueError(f"Parsed JSON ist kein Dict sondern {type(result)}")
        
        return result

    def _repair_truncated_json(self, response: str) -> Dict[str, Any]:
        """
        Methode 5: Repariert durch max_tokens abgeschnittenes JSON.
        
        Problem: LLM-Output wird mitten im JSON-Array abgeschnitten, z.B.:
          {"triples": [{"subject": "A", ...}, {"subject": "B", ...}, {"subject": "C", "pred
        
        Lösung: State-Machine tracked Brace/Bracket-Depth, findet das letzte
        vollständige Array-Element, schneidet dort ab, schließt offene Strukturen.
        """
        json_start = response.find('{')
        if json_start == -1:
            raise ValueError("Kein JSON-Objekt gefunden")
        
        json_str = response[json_start:]
        
        # Quick check: ist JSON tatsächlich abgeschnitten?
        open_braces = json_str.count('{')
        close_braces = json_str.count('}')
        open_brackets = json_str.count('[')
        close_brackets = json_str.count(']')
        
        if open_braces <= close_braces and open_brackets <= close_brackets:
            raise ValueError("JSON scheint nicht abgeschnitten (Klammern balanciert)")
        
        # State-Machine: Finde letztes vollständiges Objekt bei Tiefe 2
        # (= Array-Element in {"triples": [HERE]})
        depth = 0
        in_string = False
        escape_next = False
        last_complete_obj_end = -1
        
        for i, ch in enumerate(json_str):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 1:
                    # Objekt bei Tiefe 2 geschlossen → vollständiges Array-Element
                    last_complete_obj_end = i
            # [ und ] tracken wir nicht für depth, weil die Struktur
            # immer {"triples": [{...}, {...}]} ist — depth=1 nach outer {,
            # depth=2 innerhalb eines Triple-Objekts
        
        if last_complete_obj_end == -1:
            raise ValueError("Kein vollständiges verschachteltes Objekt gefunden")
        
        # Abschneiden nach letztem vollständigen Objekt
        repaired = json_str[:last_complete_obj_end + 1]
        
        # Trailing Comma entfernen
        repaired = repaired.rstrip()
        if repaired.endswith(','):
            repaired = repaired[:-1]
        
        # Offene Strukturen schließen
        remaining_brackets = repaired.count('[') - repaired.count(']')
        remaining_braces = repaired.count('{') - repaired.count('}')
        
        repaired += '\n' + ']' * remaining_brackets + '}' * remaining_braces
        
        if self.debug:
            logger.debug(
                f"🔧 Truncated JSON repariert: "
                f"abgeschnitten bei Pos {last_complete_obj_end}, "
                f"{remaining_brackets}x ']' + {remaining_braces}x '}}' angehängt"
            )
        
        result = json.loads(repaired)
        
        if not isinstance(result, dict):
            raise ValueError(f"Repariertes JSON ist kein Dict: {type(result)}")
        
        return result


# Globale Parser-Instanz
_global_parser = LLMJSONParser(debug=False)


def parse_llm_json(
    response: str,
    schema_validator: Optional[Callable[[Dict], bool]] = None,
    default_on_error: Optional[Dict[str, Any]] = None,
    debug: bool = False
) -> Dict[str, Any]:
    """
    Convenience-Funktion für robustes JSON-Parsing
    
    Args:
        response: LLM-Response
        schema_validator: Optional validation function
        default_on_error: Default dict if parsing fails
        debug: Enable verbose logging
        
    Returns:
        Parsed JSON dict
        
    Example:
        >>> def validate_emotions(data):
        ...     return 'emotions' in data and 'dominant_emotion' in data
        >>> 
        >>> response = "Here's the analysis: ```json\\n{...}\\n```"
        >>> data = parse_llm_json(response, schema_validator=validate_emotions)
    """
    if debug:
        parser = LLMJSONParser(debug=True)
    else:
        parser = _global_parser
    
    return parser.parse(response, schema_validator, default_on_error)


# Schema-Validators für häufige Use-Cases
def validate_emotion_schema(data: Dict) -> bool:
    """Validiert Emotions-Analyse-Schema"""
    required = ['emotions', 'dominant_emotion', 'overall_valence']
    return all(field in data for field in required)


def validate_kg_schema(data: Dict) -> bool:
    """Validiert Knowledge-Graph-Schema"""
    if 'triples' not in data:
        return False
    if not isinstance(data['triples'], list):
        return False
    # Validiere Triple-Struktur
    for triple in data['triples']:
        if not isinstance(triple, dict):
            return False
        if not all(key in triple for key in ['subject', 'predicate', 'object']):
            return False
    return True
