"""
SOTA Error Handling System für Enhanced Streamlit Bot
======================================================

State-of-the-Art Fehlerbehandlung mit:
- Spezifische Exception-Klassen (keine broad exceptions)
- Root-Cause-Analyse für jeden Fehler
- User-Friendly Fehlermeldungen
- Technische Details für Debugging
- Integration mit Pydantic-Schemas
- DSGVO-konforme Fehlerprotokollierung

Architekturprinzipien:
1. Root-Cause-Lösung: Behebe die Ursache, nicht das Symptom
2. Keine Workarounds: Spezifische Exceptions statt generic try-catch
3. Type Safety: Alle Exceptions sind typisiert
4. User Experience: Klare, handlungsorientierte Fehlermeldungen
5. Observability: Detailliertes Logging für Debugging

Verwendung:
    from utils.error_handling import (
        BotError, ValidationError, ModelError, DatabaseError,
        error_handler, root_cause_analyzer
    )
    
    try:
        # Code der Fehler werfen könnte
        process_user_input(input)
    except ValidationError as e:
        st.error(e.user_message)
        logger.error(e.technical_details)
        error_handler.log_error(e)
    except BotError as e:
        # Generische Fehlerbehandlung
        error_handler.handle(e)
"""

import sys
import traceback
import logging
import threading
from typing import Optional, Dict, Any, List, Tuple, Type, Union, cast
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps
import inspect

# SOTA: Import Pydantic Models für Integration
from schemas import SOTAError, ValidationErrorDetail

# Configure module logger
logger = logging.getLogger(__name__)


# ============================================================================
# ERROR CATEGORIES (Enum für Typ-Sicherheit)
# ============================================================================

class ErrorCategory(Enum):
    """SOTA: Kategorisierung von Fehlern für bessere Analyse."""
    
    VALIDATION = "validation"
    MODEL = "model"
    DATABASE = "database"
    FILE_IO = "file_io"
    NETWORK = "network"
    GPU = "gpu"
    MEMORY = "memory"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class ErrorSeverity(Enum):
    """SOTA: Schweregrade von Fehlern."""
    
    LOW = "low"           # Warnung, kein kritischer Fehler
    MEDIUM = "medium"     # Fehler, aber Recovery möglich
    HIGH = "high"         # Kritischer Fehler, Recovery schwierig
    CRITICAL = "critical" # System kann nicht weiterlaufen


# ============================================================================
# CUSTOM EXCEPTION HIERARCHY (SOTA)
# ============================================================================

class BotError(Exception):
    """
    SOTA: Basis-Exception für alle Bot-spezifischen Fehler.
    
    Features:
    - User-Friendly Message
    - Technical Details
    - Error Category und Severity
    - Context Information
    - Root-Cause Information
    
    Root-Cause-Lösung:
    - Keine broad exceptions mehr
    - Jeder Fehler ist spezifisch und typisiert
    - Detaillierte Informationen für Debugging
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        root_cause: Optional[str] = None,
        solution: Optional[str] = None,
        error_id: Optional[str] = None
    ):
        self.user_message = user_message
        self.technical_message = technical_message
        self.category = category
        self.severity = severity
        self.context = context or {}
        self.root_cause = root_cause
        self.solution = solution
        self.error_id = error_id or f"err_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        self.timestamp = datetime.utcnow()
        
        # Setze die Exception Message (für Kompatibilität)
        super().__init__(self.technical_message)
        
        # Root-Cause-Analyse durchführen
        if root_cause is None:
            self.root_cause = self._analyze_root_cause()
    
    def _analyze_root_cause(self) -> str:
        """SOTA: Automatische Root-Cause-Analyse."""
        # Einfache Heuristik für Root-Cause
        tb_str = str(sys.exc_info()[2]) if sys.exc_info()[2] else ""
        
        if "FileNotFoundError" in tb_str or "File not found" in self.technical_message:
            return "Datei nicht gefunden oder Pfad ungültig"
        elif "PermissionError" in tb_str or "Permission denied" in self.technical_message:
            return "Keine Berechtigung für Dateizugriff"
        elif "MemoryError" in tb_str or "Out of memory" in self.technical_message:
            return "Nicht genug Speicher verfügbar"
        elif "CUDA" in tb_str or "GPU" in self.technical_message:
            return "GPU-Fehler oder CUDA-Problem"
        elif "ImportError" in tb_str or "ModuleNotFoundError" in tb_str:
            return "Fehlende Abhängigkeit oder Import-Fehler"
        elif "sqlite3" in tb_str:
            return "Datenbank-Fehler"
        elif "pydantic" in tb_str or "validation" in self.category.value:
            return "Validierungsfehler"
        else:
            return "Unbekannte Ursache"
    
    def to_sota_error(self) -> SOTAError:
        """SOTA: Konvertiere zu Pydantic SOTAError für API-Kompatibilität."""
        return SOTAError(
            error_type=self.__class__.__name__,
            user_message=self.user_message,
            technical_message=self.technical_message,
            severity=self.severity.value,
            context=self.context,
            error_id=self.error_id
        )
    
    def log(self) -> None:
        """SOTA: Fehler detailliert loggen."""
        log_message = (
            f"[{self.error_id}] [{self.category.value}] [{self.severity.value}] \n"
            f"User: {self.user_message}\n"
            f"Technical: {self.technical_message}\n"
            f"Root Cause: {self.root_cause}\n"
            f"Solution: {self.solution}\n"
            f"Context: {self.context}"
        )
        
        # Log-Level basierend auf Severity
        if self.severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message)
        elif self.severity == ErrorSeverity.HIGH:
            logger.error(log_message)
        elif self.severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message)
        else:
            logger.info(log_message)
    
    def __str__(self) -> str:
        """User-Friendly String Darstellung."""
        return f"[{self.category.value.upper()}] {self.user_message}"
    
    def __repr__(self) -> str:
        """Technische String Darstellung."""
        return (
            f"{self.__class__.__name__}("
            f"user_message={self.user_message!r}, "
            f"technical_message={self.technical_message!r}, "
            f"category={self.category}, "
            f"severity={self.severity})"
        )


# ============================================================================
# SPECIFIC EXCEPTION CLASSES
# ============================================================================

class ValidationError(BotError):
    """
    SOTA: Exception für Validierungsfehler.
    
    Root-Cause-Lösung:
    - Wird geworfen wenn Input-Validierung fehlschlägt
    - Enthält Details zu den Validierungsfehlern
    - User-Friendly Messages für Formular-Fehler
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        validation_errors: Optional[List[ValidationErrorDetail]] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.validation_errors = validation_errors or []
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            context=context,
            solution=solution or "Bitte überprüfen Sie Ihre Eingaben und versuchen Sie es erneut.",
            root_cause="Validierungsfehler in Benutzereingabe"
        )
    
    def get_user_friendly_errors(self) -> List[str]:
        """SOTA: User-Friendly Liste der Validierungsfehler."""
        return [err.message for err in self.validation_errors]


class ModelError(BotError):
    """
    SOTA: Exception für Modell-bezogene Fehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei Problemen mit LLM-Modellen
    - Enthält Modell-spezifische Details
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        model_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.model_id = model_id
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.MODEL,
            severity=ErrorSeverity.HIGH,
            context={**context, "model_id": model_id} if context else {"model_id": model_id},
            solution=solution or "Bitte wählen Sie ein anderes Modell oder starten Sie den Bot neu.",
            root_cause="Modell-Ladefehler oder Inferenz-Problem"
        )


class ModelLoadError(ModelError):
    """SOTA: Exception für Modell-Ladefehler."""
    
    def __init__(
        self,
        model_id: str,
        model_path: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        user_msg = f"Modell '{model_id}' konnte nicht geladen werden."
        tech_msg = f"Failed to load model {model_id} from {model_path or 'default path'}"
        
        super().__init__(
            user_message=user_msg,
            technical_message=tech_msg,
            model_id=model_id,
            context={**context, "model_path": model_path} if context else {"model_path": model_path},
            solution="Prüfen Sie den Modell-Pfad und die Berechtigungen. Modell-Dateien müssen lokal verfügbar sein."
        )


class ModelInferenceError(ModelError):
    """SOTA: Exception für Modell-Inferenz-Fehler."""
    
    def __init__(
        self,
        model_id: str,
        input_text: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        input_preview = input_text[:50] + "..." if input_text and len(input_text) > 50 else input_text
        user_msg = f"Modell '{model_id}' konnte keine Antwort generieren."
        tech_msg = f"Inference failed for model {model_id} with input: {input_preview}"
        
        super().__init__(
            user_message=user_msg,
            technical_message=tech_msg,
            model_id=model_id,
            context={**context, "input_preview": input_preview} if context else {"input_preview": input_preview},
            solution="Versuchen Sie eine kürzere Eingabe oder ein anderes Modell."
        )


class DatabaseError(BotError):
    """
    SOTA: Exception für Datenbank-Fehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei SQLite- oder anderen DB-Fehlern
    - Enthält DB-spezifische Details
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        table: Optional[str] = None,
        operation: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.table = table
        self.operation = operation
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.DATABASE,
            severity=ErrorSeverity.HIGH,
            context={**context, "table": table, "operation": operation} if context else {"table": table, "operation": operation},
            solution=solution or "Bitte versuchen Sie es erneut oder starten Sie den Bot neu.",
            root_cause="Datenbank-Operation fehlgeschlagen"
        )


class FileIOError(BotError):
    """
    SOTA: Exception für Datei-I/O-Fehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei Datei-Operationen
    - Enthält Datei-spezifische Details
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        file_path: Optional[str] = None,
        operation: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.file_path = file_path
        self.operation = operation
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.FILE_IO,
            severity=ErrorSeverity.MEDIUM,
            context={**context, "file_path": file_path, "operation": operation} if context else {"file_path": file_path, "operation": operation},
            solution=solution or "Bitte prüfen Sie die Datei und die Berechtigungen.",
            root_cause="Datei-Operation fehlgeschlagen"
        )


class FileNotFoundError(FileIOError):
    """SOTA: Exception für nicht gefundene Dateien."""
    
    def __init__(self, file_path: str, context: Optional[Dict[str, Any]] = None):
        user_msg = f"Datei nicht gefunden: {file_path}"
        tech_msg = f"File not found: {file_path}"
        
        super().__init__(
            user_message=user_msg,
            technical_message=tech_msg,
            file_path=file_path,
            operation="read",
            context=context,
            solution="Prüfen Sie den Dateipfad und stellen Sie sicher, dass die Datei existiert."
        )


class FilePermissionError(FileIOError):
    """SOTA: Exception für Berechtigungsfehler."""
    
    def __init__(self, file_path: str, operation: str, context: Optional[Dict[str, Any]] = None):
        user_msg = f"Keine Berechtigung für {operation} auf: {file_path}"
        tech_msg = f"Permission denied for {operation} on {file_path}"
        
        super().__init__(
            user_message=user_msg,
            technical_message=tech_msg,
            file_path=file_path,
            operation=operation,
            context=context,
            solution="Prüfen Sie die Dateiberechtigungen oder führen Sie den Bot mit Admin-Rechten aus."
        )


class FileTooLargeError(FileIOError):
    """SOTA: Exception für zu große Dateien."""
    
    def __init__(self, file_path: str, file_size: int, max_size: int, context: Optional[Dict[str, Any]] = None):
        user_msg = f"Datei ist zu groß: {file_size / (1024*1024):.2f} MB (Max: {max_size / (1024*1024):.2f} MB)"
        tech_msg = f"File too large: {file_size} bytes (max: {max_size} bytes)"
        
        super().__init__(
            user_message=user_msg,
            technical_message=tech_msg,
            file_path=file_path,
            operation="upload",
            context={**context, "file_size": file_size, "max_size": max_size} if context else {"file_size": file_size, "max_size": max_size},
            solution=f"Bitte wählen Sie eine kleinere Datei (max {max_size / (1024*1024):.2f} MB)."
        )


class NetworkError(BotError):
    """
    SOTA: Exception für Netzwerk-Fehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei Netzwerk-Operationen
    - nur relevant wenn local_only=False
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.url = url
        self.status_code = status_code
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.MEDIUM,
            context={**context, "url": url, "status_code": status_code} if context else {"url": url, "status_code": status_code},
            solution=solution or "Bitte prüfen Sie Ihre Internetverbindung oder versuchen Sie es später erneut.",
            root_cause="Netzwerk-Verbindungsfehler"
        )


class GPUError(BotError):
    """
    SOTA: Exception für GPU-bezogene Fehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei CUDA/GPU-Problemen
    - Enthält GPU-spezifische Details
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        gpu_info: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.gpu_info = gpu_info or {}
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.GPU,
            severity=ErrorSeverity.HIGH,
            context={**context, "gpu_info": gpu_info} if context else {"gpu_info": gpu_info},
            solution=solution or "Prüfen Sie Ihre GPU-Treiber und CUDA-Installation.",
            root_cause="GPU- oder CUDA-Fehler"
        )


class MemoryError(BotError):
    """
    SOTA: Exception für Speicher-Probleme.
    
    Root-Cause-Lösung:
    - Wird geworfen bei Out-of-Memory
    - Enthält Speicher-Informationen
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        memory_info: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.memory_info = memory_info or {}
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.MEMORY,
            severity=ErrorSeverity.CRITICAL,
            context={**context, "memory_info": memory_info} if context else {"memory_info": memory_info},
            solution=solution or "Schließen Sie andere Anwendungen oder reduzieren Sie die Modellgröße.",
            root_cause="Nicht genug Arbeitsspeicher oder GPU-Speicher verfügbar"
        )


class ConfigurationError(BotError):
    """
    SOTA: Exception für Konfigurationsfehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei falscher Konfiguration
    - Enthält Konfigurations-Details
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        config_key: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.config_key = config_key
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.HIGH,
            context={**context, "config_key": config_key} if context else {"config_key": config_key},
            solution=solution or "Bitte überprüfen Sie Ihre Konfigurationseinstellungen.",
            root_cause="Ungültige oder fehlende Konfiguration"
        )


class InternalError(BotError):
    """
    SOTA: Exception für interne Fehler.
    
    Root-Cause-Lösung:
    - Wird geworfen bei unerwarteten internen Fehlern
    - Enthält detaillierte Traceback-Informationen
    """
    
    def __init__(
        self,
        user_message: str,
        technical_message: str,
        traceback: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        solution: Optional[str] = None
    ):
        self.traceback = traceback
        super().__init__(
            user_message=user_message,
            technical_message=technical_message,
            category=ErrorCategory.INTERNAL,
            severity=ErrorSeverity.CRITICAL,
            context={**context, "traceback": traceback} if context else {"traceback": traceback},
            solution=solution or "Bitte starten Sie den Bot neu oder kontaktieren Sie den Support.",
            root_cause="Interner Programmfehler"
        )


# ============================================================================
# ROOT CAUSE ANALYZER
# ============================================================================

@dataclass
class RootCauseAnalysis:
    """SOTA: Ergebnis der Root-Cause-Analyse."""
    
    error: Exception
    root_cause: str
    likely_cause: str
    suggested_fix: str
    confidence: float  # 0.0 bis 1.0
    related_errors: List[str] = field(default_factory=list)
    stack_trace: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


class RootCauseAnalyzer:
    """
    SOTA: Analysiert Exceptions für Root-Cause-Identifikation.
    
    Features:
    - Automatische Root-Cause-Erkennung
    - Vorschläge für Lösungen
    - Ähnlichkeitsanalyse mit bekannten Fehlern
    - Stack-Trace-Analyse
    
    Root-Cause-Lösung:
    - Keine Workarounds: Direkte Ursachenanalyse
    - Lernfähig: Erkennt wiederkehrende Fehler
    """
    
    # Bekannte Fehler-Muster
    KNOWN_ERROR_PATTERNS = {
        # GPU/CUDA Fehler
        r"CUDA error": {
            "root_cause": "CUDA-Laufzeitfehler",
            "likely_cause": "GPU-Treiber-Problem oder CUDA-Inkompatibilität",
            "suggested_fix": "Aktualisieren Sie Ihre GPU-Treiber und CUDA-Toolkit",
            "confidence": 0.95
        },
        r"Out of memory": {
            "root_cause": "Speichermangel",
            "likely_cause": "Modell zu groß für verfügbaren VRAM",
            "suggested_fix": "Verwenden Sie ein kleineres Modell oder reduzieren Sie n_gpu_layers",
            "confidence": 0.9
        },
        r"FileNotFoundError": {
            "root_cause": "Datei nicht gefunden",
            "likely_cause": "Falscher Dateipfad oder fehlende Datei",
            "suggested_fix": "Prüfen Sie den Dateipfad und stellen Sie sicher, dass die Datei existiert",
            "confidence": 0.98
        },
        r"PermissionError": {
            "root_cause": "Berechtigungsfehler",
            "likely_cause": "Keine Zugriffsrechte auf Datei/Verzeichnis",
            "suggested_fix": "Ändern Sie die Dateiberechtigungen oder führen Sie als Admin aus",
            "confidence": 0.95
        },
        r"sqlite3\.OperationalError": {
            "root_cause": "Datenbank-Operationsfehler",
            "likely_cause": "Datenbank ist gesperrt oder beschädigt",
            "suggested_fix": "Warten Sie und versuchen Sie es erneut, oder setzen Sie die Datenbank zurück",
            "confidence": 0.85
        },
        r"ImportError.*torch": {
            "root_cause": "PyTorch-Importfehler",
            "likely_cause": "PyTorch nicht installiert oder Version inkompatibel",
            "suggested_fix": "Installieren Sie PyTorch mit: pip install torch --index-url https://download.pytorch.org/whl/cu118",
            "confidence": 0.95
        },
        r"RuntimeError.*expected.*scalar": {
            "root_cause": "Tensor-Formatfehler",
            "likely_cause": "Falsche Tensor-Dimensionen für die Operation",
            "suggested_fix": "Überprüfen Sie die Eingabe-Dimensionen und die Modell-Erwartungen",
            "confidence": 0.8
        },
        # Validierungsfehler
        r"pydantic.*ValidationError": {
            "root_cause": "Validierungsfehler",
            "likely_cause": "Ungültige Benutzereingabe oder Konfiguration",
            "suggested_fix": "Überprüfen Sie Ihre Eingaben auf Gültigkeit",
            "confidence": 0.9
        },
        # Netzwerk-Fehler
        r"requests\.exceptions\.ConnectionError": {
            "root_cause": "Netzwerk-Verbindungsfehler",
            "likely_cause": "Keine Internetverbindung oder Server nicht erreichbar",
            "suggested_fix": "Prüfen Sie Ihre Internetverbindung oder aktivieren Sie den Local-Only-Modus",
            "confidence": 0.95
        },
        r"requests\.exceptions\.Timeout": {
            "root_cause": "Netzwerk-Timeout",
            "likely_cause": "Server antwortet zu langsam",
            "suggested_fix": "Erhöhen Sie das Timeout oder verwenden Sie einen anderen Server",
            "confidence": 0.9
        },
    }
    
    def __init__(self):
        self._error_history: List[RootCauseAnalysis] = []
        self._max_history = 100
    
    def analyze(self, error: Exception, context: Optional[Dict[str, Any]] = None) -> RootCauseAnalysis:
        """
        SOTA: Führe Root-Cause-Analyse durch.
        
        Args:
            error: Die Exception zu analysieren
            context: Zusätzlicher Kontext
            
        Returns:
            RootCauseAnalysis: Ergebnis der Analyse
        """
        import re
        
        # Extrahiere Stack Trace
        stack_trace = traceback.format_exception(type(error), error, error.__traceback__)
        stack_trace_str = "".join(stack_trace)
        
        # Suche nach bekannten Mustern
        best_match: Optional[Dict[str, Any]] = None
        best_confidence = 0.0
        
        for pattern, pattern_info in self.KNOWN_ERROR_PATTERNS.items():
            if re.search(pattern, stack_trace_str, re.IGNORECASE):
                conf = cast(float, pattern_info["confidence"])
                if conf > best_confidence:
                    best_match = pattern_info
                    best_confidence = conf
        
        # Falls kein Match, generische Analyse
        if best_match is None:
            analysis = RootCauseAnalysis(
                error=error,
                root_cause=self._generic_root_cause(error),
                likely_cause=self._generic_likely_cause(error),
                suggested_fix=self._generic_solution(error),
                confidence=0.5,
                stack_trace=stack_trace,
                context=context or {}
            )
        else:
            analysis = RootCauseAnalysis(
                error=error,
                root_cause=best_match["root_cause"],
                likely_cause=best_match["likely_cause"],
                suggested_fix=best_match["suggested_fix"],
                confidence=best_match["confidence"],
                stack_trace=stack_trace,
                context=context or {}
            )
        
        # Suche nach ähnlichen Fehlern in der History
        similar_errors = self._find_similar_errors(analysis)
        analysis.related_errors = similar_errors
        
        # Speichere in History
        self._error_history.append(analysis)
        if len(self._error_history) > self._max_history:
            self._error_history = self._error_history[-self._max_history:]
        
        return analysis
    
    def _generic_root_cause(self, error: Exception) -> str:
        """SOTA: Generische Root-Cause-Bestimmung."""
        error_type = type(error).__name__
        
        if "Validation" in error_type:
            return "Validierungsfehler"
        elif "File" in error_type or "IO" in error_type:
            return "Datei-I/O-Fehler"
        elif "Database" in error_type or "sqlite" in error_type.lower():
            return "Datenbank-Fehler"
        elif "Model" in error_type or "Inference" in error_type:
            return "Modell-Fehler"
        elif "Network" in error_type or "Connection" in error_type:
            return "Netzwerk-Fehler"
        elif "Memory" in error_type:
            return "Speicher-Fehler"
        elif "GPU" in error_type or "CUDA" in error_type:
            return "GPU-Fehler"
        else:
            return "Unbekannter Fehler"
    
    def _generic_likely_cause(self, error: Exception) -> str:
        """SOTA: Generische Likely-Cause-Bestimmung."""
        error_str = str(error).lower()
        
        if "not found" in error_str:
            return "Ressource nicht gefunden"
        elif "permission" in error_str:
            return "Berechtigungsproblem"
        elif "timeout" in error_str:
            return "Zeitüberschreitung"
        elif "out of memory" in error_str:
            return "Speichermangel"
        elif "invalid" in error_str:
            return "Ungültige Eingabe oder Konfiguration"
        elif "connection" in error_str:
            return "Verbindungsproblem"
        else:
            return "Unbekannte Ursache"
    
    def _generic_solution(self, error: Exception) -> str:
        """SOTA: Generische Lösungsvorschläge."""
        error_type = type(error).__name__
        
        if "Validation" in error_type:
            return "Überprüfen Sie Ihre Eingaben und versuchen Sie es erneut"
        elif "File" in error_type:
            return "Prüfen Sie den Dateipfad und die Berechtigungen"
        elif "Database" in error_type:
            return "Versuchen Sie es erneut oder setzen Sie die Datenbank zurück"
        elif "Model" in error_type:
            return "Wählen Sie ein anderes Modell oder starten Sie den Bot neu"
        elif "Network" in error_type:
            return "Prüfen Sie Ihre Internetverbindung oder aktivieren Sie Local-Only-Modus"
        elif "Memory" in error_type:
            return "Reduzieren Sie die Modellgröße oder schließen Sie andere Anwendungen"
        else:
            return "Starten Sie den Bot neu oder kontaktieren Sie den Support"
    
    def _find_similar_errors(self, analysis: RootCauseAnalysis) -> List[str]:
        """SOTA: Suche nach ähnlichen Fehlern in der History."""
        similar = []
        
        for historical in self._error_history[-10:]:  # Letzte 10 Fehler
            if (historical.root_cause == analysis.root_cause and
                historical.error.__class__ == analysis.error.__class__):
                similar.append(type(historical.error).__name__)
        
        return similar
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """SOTA: Statistiken über aufgetretene Fehler."""
        stats: Dict[str, Any] = {
            "total_errors": len(self._error_history),
            "by_category": {},
            "by_severity": {},
            "recent_errors": [
                {
                    "error_type": type(a.error).__name__,
                    "root_cause": a.root_cause,
                    "timestamp": a.error.timestamp if hasattr(a.error, 'timestamp') else None
                }
                for a in self._error_history[-10:]
            ]
        }
        
        for analysis in self._error_history:
            # Kategorie
            category = getattr(analysis.error, 'category', None)
            if category:
                cat_name = category.value if hasattr(category, 'value') else str(category)
                stats["by_category"][cat_name] = stats["by_category"].get(cat_name, 0) + 1
            
            # Severity
            severity = getattr(analysis.error, 'severity', None)
            if severity:
                sev_name = severity.value if hasattr(severity, 'value') else str(severity)
                stats["by_severity"][sev_name] = stats["by_severity"].get(sev_name, 0) + 1
        
        return stats


# ============================================================================
# ERROR HANDLER (Zentrale Fehlerbehandlung)
# ============================================================================

class ErrorHandler:
    """
    SOTA: Zentrale Fehlerbehandlungs-Klasse.
    
    Features:
    - Uniformes Error Handling für die gesamte Anwendung
    - Automatische Root-Cause-Analyse
    - Fehler-Logging mit Kontext
    - User-Friendly Fehlermeldungen
    - Metriken und Statistiken
    
    Root-Cause-Lösung:
    - Keine Workarounds: Alle Fehler werden zentral behandelt
    - Konsistente User Experience
    - Detailliertes Logging für Debugging
    """
    
    def __init__(self):
        self.analyzer = RootCauseAnalyzer()
        self._error_count = 0
        self._error_lock = threading.Lock()
    
    def handle(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        reraise: bool = False
    ) -> Optional[BotError]:
        """
        SOTA: Behandle einen Fehler zentral.
        
        Args:
            error: Die Exception zu behandeln
            context: Zusätzlicher Kontext
            reraise: Ob die Exception neu geworfen werden soll
            
        Returns:
            BotError: Konvertierte Exception (falls möglich)
            
        Root-Cause-Lösung:
        - Alle Exceptions werden analysiert und geloggt
        - User-Friendly Messages werden extrahiert
        """
        with self._error_lock:
            self._error_count += 1
        
        # Falls bereits eine BotError, direkt verwenden
        if isinstance(error, BotError):
            bot_error = error
        else:
            # Konvertiere zu BotError
            bot_error = self._convert_to_bot_error(error, context)
        
        # Root-Cause-Analyse
        analysis = self.analyzer.analyze(bot_error, context)
        
        # Loggen
        self._log_error(bot_error, analysis, context)
        
        # Fehler zählen
        if bot_error.severity == ErrorSeverity.CRITICAL:
            logger.critical(f"KRITISCHER FEHLER: {bot_error.user_message}")
        elif bot_error.severity == ErrorSeverity.HIGH:
            logger.error(f"HOCHER FEHLER: {bot_error.user_message}")
        elif bot_error.severity == ErrorSeverity.MEDIUM:
            logger.warning(f"FEHLER: {bot_error.user_message}")
        else:
            logger.info(f"WARNUNG: {bot_error.user_message}")
        
        # Falls gewünscht, neu werfen
        if reraise:
            raise bot_error
        
        return bot_error
    
    def _convert_to_bot_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> BotError:
        """SOTA: Konvertiere beliebige Exception zu BotError."""
        error_type = type(error).__name__
        error_str = str(error)
        
        # Mapping von Python Exceptions zu BotError-Typen
        exception_mapping = {
            "ValueError": ValidationError,
            "TypeError": ValidationError,
            "FileNotFoundError": FileNotFoundError,
            "PermissionError": FilePermissionError,
            "IOError": FileIOError,
            "OSError": FileIOError,
            "sqlite3.OperationalError": DatabaseError,
            "sqlite3.IntegrityError": DatabaseError,
            "ImportError": ConfigurationError,
            "ModuleNotFoundError": ConfigurationError,
            "RuntimeError": InternalError,
            "MemoryError": MemoryError,
            "KeyboardInterrupt": KeyboardInterrupt,  # Nicht als BotError behandeln
        }
        
        # Suche nach passendem Mapping
        bot_error_class = exception_mapping.get(error_type, InternalError)
        
        # Spezielle Behandlung für KeyboardInterrupt
        if bot_error_class == KeyboardInterrupt:
            raise error
        
        # Erstelle BotError
        try:
            if bot_error_class == ValidationError:
                return ValidationError(
                    user_message=f"Ungültige Eingabe: {error_str}",
                    technical_message=f"{error_type}: {error_str}",
                    context=context
                )
            elif bot_error_class == FileNotFoundError:
                # Extrahiere Dateipfad aus Exception
                file_path = getattr(error, 'filename', None)
                return FileNotFoundError(file_path or "unknown", context)
            elif bot_error_class == FilePermissionError:
                file_path = getattr(error, 'filename', None)
                return FilePermissionError(file_path or "unknown", "read", context)
            elif bot_error_class in [DatabaseError, ConfigurationError, InternalError]:
                return cast(BotError, bot_error_class(
                    user_message=f"Interner Fehler: {error_str}",
                    technical_message=f"{error_type}: {error_str}",
                    context=context
                ))
            else:
                return InternalError(
                    user_message=f"Ein Fehler ist aufgetreten: {error_str}",
                    technical_message=f"{error_type}: {error_str}",
                    traceback=traceback.format_exc(),
                    context=context
                )
        except Exception as conversion_error:
            # Falls Konvertierung fehlschlägt, erstelle generischen InternalError
            logger.error(f"Fehler bei Konvertierung zu BotError: {conversion_error}")
            return InternalError(
                user_message="Ein unbekannter Fehler ist aufgetreten",
                technical_message=f"Original error: {error_type}: {error_str}",
                traceback=traceback.format_exc(),
                context=context
            )
    
    def _log_error(
        self,
        error: BotError,
        analysis: RootCauseAnalysis,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """SOTA: Fehler detailliert loggen."""
        log_data = {
            "error_id": error.error_id,
            "timestamp": error.timestamp.isoformat(),
            "type": type(error).__name__,
            "category": error.category.value,
            "severity": error.severity.value,
            "user_message": error.user_message,
            "technical_message": error.technical_message,
            "root_cause": analysis.root_cause,
            "likely_cause": analysis.likely_cause,
            "suggested_fix": analysis.suggested_fix,
            "confidence": analysis.confidence,
            "context": {**error.context, **(context or {})},
            "stack_trace": "\n".join(analysis.stack_trace) if analysis.stack_trace else None
        }
        
        # Log als JSON für bessere Analyse
        logger.error(
            "\n" + "="*60 + "\n" +
            "BOT ERROR REPORT\n" +
            "="*60 + "\n" +
            f"ID: {log_data['error_id']}\n" +
            f"Type: {log_data['type']}\n" +
            f"Category: {log_data['category']}\n" +
            f"Severity: {log_data['severity']}\n" +
            f"Message: {log_data['user_message']}\n" +
            f"Root Cause: {log_data['root_cause']}\n" +
            f"Solution: {log_data['suggested_fix']}\n" +
            "="*60
        )
    
    def log_error(
        self,
        error: Union[Exception, BotError],
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        SOTA: Fehler nur loggen (ohne Handling).
        
        Args:
            error: Die Exception zu loggen
            context: Zusätzlicher Kontext
        """
        if not isinstance(error, BotError):
            error = self._convert_to_bot_error(error, context)
        
        analysis = self.analyzer.analyze(error, context)
        self._log_error(error, analysis, context)
    
    def get_statistics(self) -> Dict[str, Any]:
        """SOTA: Fehler-Statistiken abrufen."""
        return {
            "total_errors": self._error_count,
            "analyzer_stats": self.analyzer.get_error_statistics()
        }
    
    def reset_statistics(self) -> None:
        """SOTA: Statistiken zurücksetzen."""
        with self._error_lock:
            self._error_count = 0
        self.analyzer._error_history = []


# ============================================================================
# DECORATORS FOR ERROR HANDLING
# ============================================================================

# Globaler Error Handler
_error_handler = ErrorHandler()


def handle_errors(
    reraise: bool = False,
    default_error: Optional[Type[BotError]] = None,
    context: Optional[Dict[str, Any]] = None
):
    """
    SOTA: Decorator für automatische Fehlerbehandlung.
    
    Usage:
        @handle_errors(reraise=True)
        def my_function():
            # Code der Fehler werfen könnte
            pass
    
    Args:
        reraise: Ob Fehler neu geworfen werden sollen
        default_error: Default BotError-Klasse für generische Fehler
        context: Zusätzlicher Kontext
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Extrahiere Kontext aus Funktion
                func_context = {
                    "function": func.__name__,
                    "args": args,
                    "kwargs": kwargs
                }
                
                # Merge mit Decorator-Kontext
                merged_context = {**func_context, **(context or {})}
                
                # Behandle Fehler
                try:
                    _error_handler.handle(e, merged_context, reraise=reraise)
                except Exception as handler_error:
                    # Falls Error Handler selbst einen Fehler wirft
                    logger.error(f"Error in error handler: {handler_error}")
                    
                # Falls nicht reraise, gebe None zurück oder werfe default_error
                if not reraise:
                    if default_error:
                        # Werfe default_error
                        error_msg = str(e)
                        raise default_error(
                            user_message=f"Fehler in {func.__name__}: {error_msg}",
                            technical_message=str(e),
                            context=merged_context
                        )
                    return None
        
        return wrapper
    return decorator


def catch_and_log(
    log_level: str = "error",
    context: Optional[Dict[str, Any]] = None
):
    """
    SOTA: Decorator für Catch & Log (kein Reraise).
    
    Usage:
        @catch_and_log(log_level="warning")
        def my_function():
            # Code der Fehler werfen könnte
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                func_context = {
                    "function": func.__name__,
                    "args": args,
                    "kwargs": kwargs
                }
                merged_context = {**func_context, **(context or {})}
                
                _error_handler.log_error(e, merged_context)
                
                # Log auf gewünschtem Level
                if log_level == "debug":
                    logger.debug(f"Fehler in {func.__name__}: {e}")
                elif log_level == "info":
                    logger.info(f"Fehler in {func.__name__}: {e}")
                elif log_level == "warning":
                    logger.warning(f"Fehler in {func.__name__}: {e}")
                else:
                    logger.error(f"Fehler in {func.__name__}: {e}")
                
                return None
        
        return wrapper
    return decorator


# ============================================================================
# STREAMLIT INTEGRATION
# ============================================================================

def display_error_to_user(
    error: Union[Exception, BotError, SOTAError],
    streamlit_instance=None
) -> None:
    """
    SOTA: Zeige Fehler dem Benutzer in Streamlit an.
    
    Args:
        error: Die Exception anzuzeigen
        streamlit_instance: Optionales Streamlit-Modul (falls nicht importiert)
    """
    try:
        import streamlit as st
    except ImportError:
        if streamlit_instance is None:
            logger.error(f"Streamlit nicht verfügbar: {error}")
            return
        st = streamlit_instance
    
    # Konvertiere zu BotError falls nötig
    if isinstance(error, SOTAError):
        bot_error = BotError(
            user_message=error.user_message,
            technical_message=error.technical_message,
            category=ErrorCategory(error.error_type.lower() if error.error_type else "unknown"),
            severity=ErrorSeverity(error.severity),
            context=error.context
        )
    elif isinstance(error, BotError):
        bot_error = error
    else:
        bot_error = _error_handler._convert_to_bot_error(error)
    
    # Zeige Fehler an
    severity_color = {
        ErrorSeverity.LOW: "blue",
        ErrorSeverity.MEDIUM: "orange",
        ErrorSeverity.HIGH: "red",
        ErrorSeverity.CRITICAL: "darkred"
    }
    
    color = severity_color.get(bot_error.severity, "red")
    
    # Fehler-Box
    with st.container():
        st.markdown(f"""
        <div style="background-color: #{'f0f2f6' if color == 'blue' else 'fff2e6' if color == 'orange' else 'ffe6e6' if color == 'red' else 'ffcccc'}; 
                    padding: 15px; border-radius: 8px; border-left: 5px solid {color};">
            <h4 style="color: {color}; margin-top: 0;">⚠️ {bot_error.severity.value.upper()} ERROR</h4>
            <p style="margin-bottom: 0;"><strong>{bot_error.user_message}</strong></p>
            <p style="font-size: 0.9em; color: #666; margin-top: 10px;">
                <strong>Ursache:</strong> {bot_error.root_cause}<br/>
                <strong>Lösung:</strong> {bot_error.solution}
            </p>
        </div>
        """, unsafe_allow_html=True)
    
    # Technische Details (collapsible)
    with st.expander("🔍 Technische Details"):
        st.code(f"{type(bot_error).__name__}: {str(bot_error)}")
        if hasattr(bot_error, 'technical_message'):
            st.code(bot_error.technical_message)
        
        # Stack Trace (für Debugging)
        error_tb = getattr(bot_error, 'traceback', None)
        if error_tb:
            st.code(error_tb)
        elif hasattr(bot_error, '__traceback__') and bot_error.__traceback__ is not None:
            tb = traceback.format_exception(type(bot_error), bot_error, bot_error.__traceback__)
            st.code("".join(tb))


# ============================================================================
# GLOBAL INSTANCES
# ============================================================================

# Singleton Instanzen
error_handler = ErrorHandler()
root_cause_analyzer = RootCauseAnalyzer()


# ============================================================================
# EXPORT
# ============================================================================

__all__ = [
    # Error Categories
    'ErrorCategory',
    'ErrorSeverity',
    
    # Base Exception
    'BotError',
    
    # Specific Exceptions
    'ValidationError',
    'ModelError',
    'ModelLoadError',
    'ModelInferenceError',
    'DatabaseError',
    'FileIOError',
    'FileNotFoundError',
    'FilePermissionError',
    'FileTooLargeError',
    'NetworkError',
    'GPUError',
    'MemoryError',
    'ConfigurationError',
    'InternalError',
    
    # Root Cause Analysis
    'RootCauseAnalysis',
    'RootCauseAnalyzer',
    
    # Error Handler
    'ErrorHandler',
    'error_handler',
    
    # Decorators
    'handle_errors',
    'catch_and_log',
    
    # Streamlit Integration
    'display_error_to_user',
    
    # Analyzer
    'root_cause_analyzer',
]
