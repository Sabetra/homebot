#!/usr/bin/env python3
"""
Robust Response Handler
========================

Multi-Methoden JSON-Parsing für LLM-Responses mit Fallbacks.

Features:
- 5 Progressive Parsing-Methoden
- JSON-Schema Validation
- Detailed Error Logging
- Garantierte Struktur

Author: AI System Evolution
Date: 2025-10-05
"""

import json
import re
import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ParsingResult:
    """Ergebnis eines Parsing-Versuchs"""
    data: Optional[Dict[str, Any]]
    success: bool
    method_used: str
    error_message: Optional[str] = None


class RobustResponseHandler:
    """
    Multi-Methoden Parser für LLM-Responses.
    
    Parsing-Strategie (Progressive Fallbacks):
    1. Direct JSON Parse (sauberste Methode)
    2. Code-Block Extraction (```json ... ```)
    3. Markdown Code-Block (```\n{...}\n```)
    4. Regex JSON-Finder (findet {...} im Text)
    5. Structured Text-Extraction (Key: Value Patterns)
    
    Falls ALLES fehlschlägt: Informative Error-Struktur
    """
    
    def __init__(self, schema_validator: Optional[Callable] = None):
        """
        Args:
            schema_validator: Optional function(data, schema) -> bool
                             für zusätzliche Schema-Validierung
        """
        self.schema_validator = schema_validator
        self.parsing_stats = {
            "direct_json": 0,
            "code_block": 0,
            "markdown_block": 0,
            "regex_json": 0,
            "text_extraction": 0,
            "failures": 0
        }
    
    def parse_llm_response(
        self,
        response: str,
        expected_keys: Optional[List[str]] = None,
        required_keys: Optional[List[str]] = None,
        default_values: Optional[Dict[str, Any]] = None
    ) -> ParsingResult:
        """
        Parsed LLM-Response mit robusten Fallback-Mechanismen.
        
        Args:
            response: LLM-Response String
            expected_keys: Liste erwarteter Keys (für Validation)
            required_keys: Liste ZWINGEND erforderlicher Keys
            default_values: Default-Werte falls Parsing fehlschlägt
            
        Returns:
            ParsingResult mit data (Dict oder None)
        """
        
        if not response or not response.strip():
            logger.warning("⚠️ Leere Response - verwende Defaults")
            return ParsingResult(
                data=default_values or {},
                success=False,
                method_used="none",
                error_message="Empty response"
            )
        
        # Versuch 1: Direct JSON Parse
        result = self._try_direct_json(response)
        if result.success:
            if self._validate_structure(result.data, expected_keys, required_keys):
                self.parsing_stats["direct_json"] += 1
                return result
        
        # Versuch 2: Code-Block Extraction
        result = self._try_code_block_extraction(response)
        if result.success:
            if self._validate_structure(result.data, expected_keys, required_keys):
                self.parsing_stats["code_block"] += 1
                return result
        
        # Versuch 3: Markdown Code-Block
        result = self._try_markdown_block(response)
        if result.success:
            if self._validate_structure(result.data, expected_keys, required_keys):
                self.parsing_stats["markdown_block"] += 1
                return result
        
        # Versuch 4: Regex JSON-Finder
        result = self._try_regex_json_finder(response)
        if result.success:
            if self._validate_structure(result.data, expected_keys, required_keys):
                self.parsing_stats["regex_json"] += 1
                return result
        
        # Versuch 5: Structured Text-Extraction
        result = self._try_text_extraction(response, expected_keys)
        if result.success:
            if self._validate_structure(result.data, expected_keys, required_keys):
                self.parsing_stats["text_extraction"] += 1
                return result
        
        # ALLE Methoden fehlgeschlagen
        logger.error(f"❌ Alle Parsing-Methoden fehlgeschlagen für Response: {response[:200]}...")
        self.parsing_stats["failures"] += 1
        
        return ParsingResult(
            data=default_values or self._create_error_structure(response),
            success=False,
            method_used="fallback",
            error_message="All parsing methods failed"
        )
    
    def _try_direct_json(self, response: str) -> ParsingResult:
        """Versuch 1: Direktes JSON-Parsing"""
        try:
            data = json.loads(response.strip())
            logger.debug("✅ Direct JSON parse erfolgreich")
            return ParsingResult(
                data=data,
                success=True,
                method_used="direct_json"
            )
        except json.JSONDecodeError as e:
            logger.debug(f"❌ Direct JSON failed: {e}")
            return ParsingResult(
                data=None,
                success=False,
                method_used="direct_json",
                error_message=str(e)
            )
    
    def _try_code_block_extraction(self, response: str) -> ParsingResult:
        """Versuch 2: Extrahiere JSON aus ```json ... ``` Blöcken"""
        try:
            # Suche nach ```json ... ```
            match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                logger.debug("✅ Code-Block extraction erfolgreich")
                return ParsingResult(
                    data=data,
                    success=True,
                    method_used="code_block"
                )
            
            logger.debug("❌ Kein ```json``` Code-Block gefunden")
            return ParsingResult(
                data=None,
                success=False,
                method_used="code_block",
                error_message="No code block found"
            )
        except Exception as e:
            logger.debug(f"❌ Code-Block extraction failed: {e}")
            return ParsingResult(
                data=None,
                success=False,
                method_used="code_block",
                error_message=str(e)
            )
    
    def _try_markdown_block(self, response: str) -> ParsingResult:
        """Versuch 3: Extrahiere JSON aus ``` ... ``` (ohne json tag)"""
        try:
            # Suche nach ``` ... ``` (ohne "json")
            match = re.search(r'```\s*(\{.*?\})\s*```', response, re.DOTALL)
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                logger.debug("✅ Markdown-Block extraction erfolgreich")
                return ParsingResult(
                    data=data,
                    success=True,
                    method_used="markdown_block"
                )
            
            logger.debug("❌ Kein Markdown Code-Block gefunden")
            return ParsingResult(
                data=None,
                success=False,
                method_used="markdown_block",
                error_message="No markdown block found"
            )
        except Exception as e:
            logger.debug(f"❌ Markdown-Block extraction failed: {e}")
            return ParsingResult(
                data=None,
                success=False,
                method_used="markdown_block",
                error_message=str(e)
            )
    
    def _try_regex_json_finder(self, response: str) -> ParsingResult:
        """Versuch 4: Finde {...} Pattern im Text"""
        try:
            # Finde alle {...} Blöcke
            matches = re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            
            for match in matches:
                try:
                    json_str = match.group(0)
                    data = json.loads(json_str)
                    logger.debug("✅ Regex JSON-Finder erfolgreich")
                    return ParsingResult(
                        data=data,
                        success=True,
                        method_used="regex_json"
                    )
                except json.JSONDecodeError:
                    continue
            
            logger.debug("❌ Kein valides JSON-Pattern gefunden")
            return ParsingResult(
                data=None,
                success=False,
                method_used="regex_json",
                error_message="No valid JSON pattern found"
            )
        except Exception as e:
            logger.debug(f"❌ Regex JSON-Finder failed: {e}")
            return ParsingResult(
                data=None,
                success=False,
                method_used="regex_json",
                error_message=str(e)
            )
    
    def _try_text_extraction(self, response: str, expected_keys: Optional[List[str]]) -> ParsingResult:
        """Versuch 5: Extrahiere Key-Value Pairs aus Text"""
        try:
            if not expected_keys:
                logger.debug("❌ Text-Extraction benötigt expected_keys")
                return ParsingResult(
                    data=None,
                    success=False,
                    method_used="text_extraction",
                    error_message="No expected_keys provided"
                )
            
            data: Dict[str, Any] = {}
            
            # Suche nach "key: value" oder "key = value" Patterns
            for key in expected_keys:
                # Pattern: "key": "value" oder key: value oder key = value
                patterns = [
                    rf'"{key}"\s*:\s*"([^"]*)"',  # "key": "value"
                    rf'{key}\s*:\s*"([^"]*)"',    # key: "value"
                    rf'{key}\s*:\s*([^\n,}}]+)',  # key: value
                    rf'{key}\s*=\s*([^\n,}}]+)'   # key = value
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, response, re.IGNORECASE)
                    if match:
                        value = match.group(1).strip()
                        # Try to parse as boolean/number
                        if value.lower() == 'true':
                            data[key] = True
                        elif value.lower() == 'false':
                            data[key] = False
                        elif value.isdigit():
                            data[key] = int(value)
                        elif re.match(r'^\d+\.\d+$', value):
                            data[key] = float(value)
                        else:
                            data[key] = value
                        break
            
            if data:
                logger.debug(f"✅ Text-Extraction erfolgreich: {len(data)} keys")
                return ParsingResult(
                    data=data,
                    success=True,
                    method_used="text_extraction"
                )
            
            logger.debug("❌ Keine Key-Value Pairs gefunden")
            return ParsingResult(
                data=None,
                success=False,
                method_used="text_extraction",
                error_message="No key-value pairs found"
            )
        except Exception as e:
            logger.debug(f"❌ Text-Extraction failed: {e}")
            return ParsingResult(
                data=None,
                success=False,
                method_used="text_extraction",
                error_message=str(e)
            )
    
    def _validate_structure(
        self,
        data: Optional[Dict],
        expected_keys: Optional[List[str]],
        required_keys: Optional[List[str]]
    ) -> bool:
        """Validiert ob geparste Daten die erwartete Struktur haben"""
        if data is None:
            return False
        
        # data is now Dict (type narrowed from Optional[Dict])
        # No need for isinstance check
        
        # Prüfe required keys
        if required_keys:
            missing = [k for k in required_keys if k not in data]
            if missing:
                logger.debug(f"❌ Fehlende required keys: {missing}")
                return False
        
        # Optional: Custom Schema Validator
        if self.schema_validator:
            try:
                if not self.schema_validator(data):
                    logger.debug("❌ Custom schema validation failed")
                    return False
            except Exception as e:
                logger.debug(f"❌ Schema validation error: {e}")
                return False
        
        logger.debug("✅ Structure validation passed")
        return True
    
    def _create_error_structure(self, original_response: str) -> Dict[str, Any]:
        """Erstellt informative Error-Struktur als Fallback"""
        return {
            "error": "Parsing failed",
            "original_response_preview": original_response[:200] + "...",
            "parsing_stats": self.parsing_stats.copy()
        }
    
    def get_parsing_stats(self) -> Dict[str, int]:
        """Gibt Statistiken über verwendete Parsing-Methoden zurück"""
        return self.parsing_stats.copy()


# Convenience Functions

def parse_json_safe(
    response: str,
    expected_keys: Optional[List[str]] = None,
    required_keys: Optional[List[str]] = None,
    default_values: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convenience function für schnelles, sicheres JSON-Parsing.
    
    Returns:
        Dict (garantiert nicht None)
    """
    handler = RobustResponseHandler()
    result = handler.parse_llm_response(
        response,
        expected_keys=expected_keys,
        required_keys=required_keys,
        default_values=default_values
    )
    return result.data or {}


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Extrahiert erstes valides JSON aus Text.
    
    Returns:
        Dict oder None
    """
    handler = RobustResponseHandler()
    result = handler.parse_llm_response(text)
    return result.data if result.success else None
