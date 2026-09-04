#!/usr/bin/env python3
"""
Guaranteed LLM Caller
=====================

Garantiert NIEMALS leere oder invalide LLM-Responses.

Features:
- Pre-Flight LLM Validation (Model geladen? Ready?)
- Post-Flight Response Validation (Nicht leer? Min-Länge?)
- Retry-Logic mit progressiven Temperaturen
- Detailed Logging für Debugging
- Graceful Degradation mit informativen Fallbacks

Author: AI System Evolution
Date: 2025-10-05
"""

import logging
import time
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LLMCallResult:
    """Ergebnis eines LLM-Calls mit Metadaten"""
    response: str
    success: bool
    attempts: int
    total_time: float
    temperature_used: float
    error_message: Optional[str] = None


class GuaranteedLLMCaller:
    """
    Wrapper für LLM-Calls der IMMER ein valides Ergebnis liefert.
    
    Philosophie:
    - Besser ein generischer Fallback als ein Crash
    - Besser 3 Retries als ein leerer String
    - Besser transparentes Logging als stille Fehler
    
    Garantien:
    1. Response ist NIEMALS None oder ""
    2. Response hat IMMER Mindestlänge
    3. Errors werden IMMER geloggt
    4. Caller erfährt IMMER ob es ein Fallback war
    """
    
    def __init__(
        self,
        llm_client,
        max_retries: int = 3,
        min_response_length: int = 10,
        default_temperature: float = 0.1,
        timeout_seconds: int = 30
    ):
        """
        Args:
            llm_client: LLM-Client (ModelLoader, etc.)
            max_retries: Maximale Anzahl Retry-Versuche
            min_response_length: Minimale Response-Länge (Zeichen)
            default_temperature: Standard-Temperatur
            timeout_seconds: Timeout pro Call
        """
        self.llm = llm_client
        self.max_retries = max_retries
        self.min_response_length = min_response_length
        self.default_temperature = default_temperature
        self.timeout_seconds = timeout_seconds
        
        # Progressive Temperaturen für Retries
        self.retry_temperatures = [
            default_temperature,
            min(default_temperature + 0.2, 0.5),
            min(default_temperature + 0.4, 0.7)
        ]
        
        logger.info(f"✅ GuaranteedLLMCaller initialisiert (max_retries={max_retries})")
    
    def call_with_guarantee(
        self,
        prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        max_tokens: int = 512,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
        fallback_response: Optional[str] = None,
        response_validator: Optional[Callable[[str], bool]] = None,
    ) -> LLMCallResult:
        """
        Führt LLM-Call aus mit Garantie auf valide Response.
        
        Args:
            prompt: User-Prompt (verwendet wenn messages=None)
            messages: Chat-Messages (bevorzugt wenn vorhanden)
            max_tokens: Maximale Token-Anzahl
            temperature: Temperatur (None = default)
            system_prompt: System-Prompt (optional)
            fallback_response: Custom Fallback (optional)
            response_validator: Optional domain-specific response validator.
                When provided, it replaces the generic minimum-length check.
            
        Returns:
            LLMCallResult mit garantiert nicht-leerem response
        """
        start_time = time.time()
        
        # Validierung
        if not prompt and not messages:
            logger.error("❌ Weder prompt noch messages angegeben!")
            return LLMCallResult(
                response=fallback_response or "ERROR: No prompt or messages provided",
                success=False,
                attempts=0,
                total_time=0.0,
                temperature_used=0.0,
                error_message="No prompt or messages"
            )
        
        # Pre-Flight Validation
        if not self._validate_llm_ready():
            logger.error("❌ LLM ist nicht bereit!")
            return LLMCallResult(
                response=fallback_response or "ERROR: LLM not ready",
                success=False,
                attempts=0,
                total_time=time.time() - start_time,
                temperature_used=0.0,
                error_message="LLM not ready"
            )
        
        # Retry-Loop mit progressiven Temperaturen
        for attempt in range(self.max_retries):
            # Wähle Temperatur für diesen Versuch
            temp = temperature if temperature is not None else self.retry_temperatures[min(attempt, len(self.retry_temperatures)-1)]
            
            try:
                logger.debug(f"🔄 LLM-Call Attempt {attempt+1}/{self.max_retries} (temp={temp})")
                
                # LLM-Call
                response = self._call_llm(
                    prompt=prompt,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temp,
                    system_prompt=system_prompt
                )
                
                # Post-Flight Validation
                if self._validate_response(response, response_validator):
                    logger.debug(f"✅ LLM-Call erfolgreich (attempt {attempt+1}, {len(response)} chars)")
                    return LLMCallResult(
                        response=response,
                        success=True,
                        attempts=attempt + 1,
                        total_time=time.time() - start_time,
                        temperature_used=temp
                    )
                else:
                    validation_mode = (
                        "domain"
                        if response_validator is not None
                        else f"min_length:{self.min_response_length}"
                    )
                    logger.warning(
                        "⚠️ Response validation failed (attempt %d): length=%d, mode=%s",
                        attempt + 1,
                        len(response) if response else 0,
                        validation_mode,
                    )
                    
            except Exception as e:
                logger.error(f"❌ LLM-Call Exception (attempt {attempt+1}): {e}", exc_info=True)
        
        # Alle Retries fehlgeschlagen - verwende Fallback
        logger.error(f"❌ Alle {self.max_retries} LLM-Call Attempts fehlgeschlagen!")
        fallback = fallback_response or self._generate_informative_fallback(prompt or str(messages))
        
        return LLMCallResult(
            response=fallback,
            success=False,
            attempts=self.max_retries,
            total_time=time.time() - start_time,
            temperature_used=self.retry_temperatures[-1],
            error_message=f"All {self.max_retries} attempts failed"
        )
    
    def _validate_llm_ready(self) -> bool:
        """Pre-Flight: Prüft ob LLM bereit ist"""
        try:
            # Check 1: Client existiert
            if self.llm is None:
                logger.error("❌ Pre-Flight: LLM client is None")
                return False
            
            # Check 2: Hat generate_response oder invoke Methode
            if not (hasattr(self.llm, 'generate_response') or 
                   hasattr(self.llm, 'invoke') or 
                   callable(self.llm)):
                logger.error("❌ Pre-Flight: LLM hat keine bekannte Call-Methode")
                return False
            
            # Check 3: Für ModelLoader - prüfe ob Model geladen
            try:
                if hasattr(self.llm, 'llm'):
                    llm_attr = getattr(self.llm, 'llm', None)
                    if llm_attr is None:
                        logger.error("❌ Pre-Flight: ModelLoader.llm ist None (kein Model geladen)")
                        return False
            except (AttributeError, TypeError):
                # Ignoriere Fehler wenn self.llm kein ModelLoader ist
                pass
            
            logger.debug("✅ Pre-Flight: LLM ready")
            return True
            
        except Exception as e:
            logger.error(f"❌ Pre-Flight Validation Error: {e}")
            return False
    
    def _validate_response(
        self,
        response: Optional[str],
        response_validator: Optional[Callable[[str], bool]] = None,
    ) -> bool:
        """Post-Flight: Prüft ob Response valide ist"""
        if response is None:
            logger.debug("❌ Post-Flight: Response is None")
            return False

        if not response.strip():
            logger.debug("❌ Post-Flight: Response is empty")
            return False

        if response_validator is not None:
            try:
                is_valid = bool(response_validator(response))
            except Exception as exc:
                logger.warning("Response validator failed: %s", exc)
                return False
            if not is_valid:
                logger.debug("❌ Post-Flight: Domain validator rejected response")
            return is_valid

        if len(response.strip()) < self.min_response_length:
            logger.debug(f"❌ Post-Flight: Response zu kurz ({len(response)} < {self.min_response_length})")
            return False
        
        logger.debug(f"✅ Post-Flight: Response valid ({len(response)} chars)")
        return True
    
    def _call_llm(
        self,
        prompt: Optional[str],
        messages: Optional[List[Dict[str, str]]],
        max_tokens: int,
        temperature: float,
        system_prompt: Optional[str]
    ) -> str:
        """Führt den eigentlichen LLM-Call aus"""
        
        # Methode 1: generate_response (ModelLoader)
        if hasattr(self.llm, 'generate_response'):
            # Baue messages falls nur prompt gegeben
            if messages is None:
                # Ensure prompt is not None before building messages
                if not prompt:
                    raise ValueError("Either prompt or messages must be provided")
                
                msgs: List[Dict[str, str]] = []
                if system_prompt:
                    # Type narrowing: ensure system_prompt is not None
                    msgs.append({"role": "system", "content": str(system_prompt)})
                msgs.append({"role": "user", "content": prompt})  # prompt is guaranteed str here
                messages = msgs
            
            response = self.llm.generate_response(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                image_path=None
            )
            
            # Handle dict response
            if isinstance(response, dict):
                return str(response.get('content', response))
            return str(response)
        
        # Methode 2: invoke (LangChain)
        elif hasattr(self.llm, 'invoke'):
            text = prompt if prompt else "\n".join([m.get('content', '') for m in (messages or [])])
            response = self.llm.invoke(text)
            
            if hasattr(response, 'content'):
                return str(response.content)
            return str(response)
        
        # Methode 3: Callable
        elif callable(self.llm):
            text = prompt if prompt else "\n".join([m.get('content', '') for m in (messages or [])])
            return str(self.llm(text))
        
        raise ValueError("LLM client has no known call interface")
    
    def _generate_informative_fallback(self, original_query: str) -> str:
        """Generiert informativen Fallback statt leerem String"""
        return f"""{{
    "error": "LLM call failed after {self.max_retries} attempts",
    "query": "{original_query[:100]}...",
    "fallback": true,
    "suggestion": "Please check LLM connection and try again"
}}"""


# Convenience function
def call_llm_safe(
    llm_client,
    prompt: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    **kwargs
) -> str:
    """
    Convenience function für schnellen, sicheren LLM-Call.
    
    Returns:
        String (garantiert nicht leer)
    """
    caller = GuaranteedLLMCaller(llm_client)
    result = caller.call_with_guarantee(prompt=prompt, messages=messages, **kwargs)
    return result.response
