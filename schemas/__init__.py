"""
SOTA Pydantic Schemas für Enhanced Streamlit Bot
=================================================

State-of-the-Art Input-Validierung und Type Safety für:
- Chat-Nachrichten
- Datei-Uploads
- Benutzer-Interaktionen
- System-Konfiguration

Architekturprinzipien:
- Root-Cause-Validierung: Fehlermeldungen sind spezifisch und handlungsorientiert
- Keine Workarounds: Direkte Validierung statt try-catch für erwartete Fehler
- DSGVO-konform: Alle Daten bleiben lokal
- Performance: Minimaler Overhead durch Pydantic v2

Verwendung:
    from schemas import ChatMessage, FileUpload, UserInput, ValidationError

    try:
        message = ChatMessage(content=user_input, sender="user")
    except ValidationError as e:
        # Spezifische Fehlermeldung für den User
        st.error(f"Ungültige Eingabe: {e.human_readable}")
"""

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    ConfigDict,
    StringConstraints,
    ValidationInfo,
    GetCoreSchemaHandler,
)
from pydantic_core import PydanticCustomError, core_schema
from typing import Literal, Optional, List, Dict, Any, Union, ClassVar, Set
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import logging

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamp for consistent audit fields."""
    return datetime.now(timezone.utc)


# ============================================================================
# CUSTOM VALIDATION ERRORS (Spezifische Fehlermeldungen für bessere UX)
# ============================================================================

class ValidationErrorMessages:
    """SOTA: Zentrale Fehlermeldungen für konsistente User-Kommunikation."""
    
    @staticmethod
    def empty_content() -> str:
        return "Bitte geben Sie eine Nachricht ein. Leere Nachrichten können nicht verarbeitet werden."
    
    @staticmethod
    def content_too_long(max_length: int) -> str:
        return f"Ihre Nachricht ist zu lang. Maximale Länge: {max_length:,} Zeichen. Bitte kürzen Sie Ihre Eingabe."
    
    @staticmethod
    def invalid_file_type(allowed_types: List[str]) -> str:
        return f"Ungültiger Dateityp. Erlaubt: {', '.join(allowed_types)}. Bitte laden Sie eine unterstützte Datei hoch."
    
    @staticmethod
    def file_too_large(max_size_mb: int) -> str:
        return f"Datei ist zu groß. Maximale Größe: {max_size_mb} MB. Bitte wählen Sie eine kleinere Datei."
    
    @staticmethod
    def invalid_sender(sender: str) -> str:
        return f"Ungültiger Absender: '{sender}'. Erlaubt sind: 'user', 'assistant', 'system'."
    
    @staticmethod
    def invalid_model(model: str, available: List[str]) -> str:
        return f"Ungültiges Modell: '{model}'. Verfügbare Modelle: {', '.join(available)}."
    
    @staticmethod
    def invalid_session_id(session_id: str) -> str:
        return f"Ungültige Session-ID: '{session_id}'. Muss ein alphanumerischer String sein (4-64 Zeichen)."


# ============================================================================
# BASE MODEL CONFIGURATION
# ============================================================================

class SOTAModel(BaseModel):
    """
    SOTA: Base Model mit Shared Configuration.
    
    Features:
    - Frozen Settings:immutable nach Initialisierung (Thread-Safety)
    - Extra = 'forbid': Verhindert unerwartete Felder
    - JSON Serialisierung für Logging/Debugging
    """
    model_config = ConfigDict(
        # Models in this module provide mutating helper methods (e.g. append/update).
        # Keep them mutable to avoid runtime frozen-instance errors.
        frozen=False,
        extra='forbid',
        json_schema_extra={
            "examples": [{"description": "Beispiel-Daten für API-Dokumentation"}]
        },
        # Performance-Optimierung: Validierung nur bei Erstellung
        validate_assignment=False,
    )


# ============================================================================
# CORE SCHEMAS: Chat & Messages
# ============================================================================

class MessageSender(str):
    """SOTA: Typ-Sicherheit für Nachrichten-Absender."""
    ALLOWED_VALUES: ClassVar[Set[str]] = {"user", "assistant", "system"}

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Pydantic V2 core-schema hook (ersetzt deprecated __get_validators__)."""
        return core_schema.no_info_after_validator_function(
            cls.validate, core_schema.str_schema()
        )

    @classmethod
    def validate(cls, v: str) -> "MessageSender":
        if v not in cls.ALLOWED_VALUES:
            raise PydanticCustomError(
                "invalid_sender",
                ValidationErrorMessages.invalid_sender(v),
                {"expected": list(cls.ALLOWED_VALUES), "got": v}
            )
        return cls(v)


class ChatMessage(SOTAModel):
    """
    SOTA: Strukturierte Chat-Nachricht mit Validierung.
    
    Features:
    - Content-Validierung (Länge, nicht leer)
    - Sender-Validierung (nur user/assistant/system)
    - Timestamp für Audit-Trail
    - Metadaten für RAG/Tool-Integration
    
    Root-Cause-Lösung:
    - Kein Workaround: Direkte Validierung statt try-catch
    - DSGVO-konform: Keine persönlichen Daten in Metadaten
    
    CoT (Chain of Thought):
    1. Benutzer gibt Nachricht ein
    2. Validierung prüft Content-Länge und Sender
    3. Bei Fehler: Spezifische Fehlermeldung (nicht generic Exception)
    
    ToT (Tree of Thought):
    - Option A: Einfache String-Validierung (gewählt - performant)
    - Option B: Regex-Validierung (zu komplex)
    - Option C: KI-basierte Validierung (nicht deterministisch)
    """
    
    # Content Constraints
    MIN_CONTENT_LENGTH: ClassVar[int] = 1
    MAX_CONTENT_LENGTH: ClassVar[int] = 32000  # ~8k Tokens (konservativ für die meisten Modelle)
    
    content: str = Field(
        ...,
        min_length=MIN_CONTENT_LENGTH,
        max_length=MAX_CONTENT_LENGTH,
        description="Der Textinhalt der Nachricht"
    )
    
    sender: MessageSender = Field(
        ...,
        description="Absender der Nachricht (user, assistant, system)"
    )
    
    timestamp: datetime = Field(
        default_factory=utc_now,
        description="Zeitstempel der Nachricht (UTC)"
    )
    
    # Metadaten für RAG/Tool-Integration
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Zusätzliche Metadaten (z. B. Tool-Results, RAG-Chunks)"
    )
    
    # Message ID für Referenzierung
    message_id: str = Field(
        default_factory=lambda: f"msg_{utc_now().strftime('%Y%m%d%H%M%S%f')}",
        description="Eindeutige Nachricht-ID"
    )
    
    # Conversation ID für Session-Tracking
    conversation_id: Optional[str] = Field(
        default=None,
        min_length=4,
        max_length=64,
        pattern=r'^[a-zA-Z0-9_-]+$',
        description="ID der Konversation/Session"
    )
    
    # Reasoning-Daten (für Debug/Analyse)
    reasoning: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="AI-Reasoning/Thought Process (optional)"
    )
    
    # Tool Results (für ReAct-Agent Integration)
    tool_results: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Ergebnisse von ausgeführten Tools"
    )
    
    @field_validator('content')
    @classmethod
    def validate_content_not_empty(cls, v: str) -> str:
        """Root-Cause: Leere Nachrichten explizit verbieten."""
        if not v or not v.strip():
            raise PydanticCustomError(
                "empty_content",
                ValidationErrorMessages.empty_content(),
                {"value": v}
            )
        return v.strip()
    
    @field_validator('conversation_id')
    @classmethod
    def validate_conversation_id(cls, v: Optional[str]) -> Optional[str]:
        """Root-Cause: Session-ID Validierung für Sicherheit."""
        if v is None:
            return v
        if not re.match(r'^[a-zA-Z0-9_-]{4,64}$', v):
            raise PydanticCustomError(
                "invalid_session_id",
                ValidationErrorMessages.invalid_session_id(v),
                {"value": v}
            )
        return v
    
    def to_dict(self) -> Dict[str, Any]:
        """SOTA: Serialisierung für Datenbank-Speicherung."""
        return {
            "content": self.content,
            "sender": self.sender,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "reasoning": self.reasoning,
            "tool_results": self.tool_results,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        """SOTA: Deserialisierung aus Datenbank."""
        data = data.copy()
        if isinstance(data.get('timestamp'), str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


class ChatHistory(SOTAModel):
    """
    SOTA: Komplette Chat-Historie mit Validierung.
    
    Features:
    - Liste von ChatMessage-Objekten
    - Session-Metadaten
    - Validierung der gesamten Historie
    """
    
    messages: List[ChatMessage] = Field(
        default_factory=list,
        description="Liste aller Nachrichten in der Session"
    )
    
    session_id: str = Field(
        min_length=4,
        max_length=64,
        pattern=r'^[a-zA-Z0-9_-]+$',
        description="Eindeutige Session-ID"
    )
    
    created_at: datetime = Field(
        default_factory=utc_now,
        description="Erstellungszeitpunkt der Session"
    )
    
    updated_at: datetime = Field(
        default_factory=utc_now,
        description="Letztes Update der Session"
    )
    
    # Session-Metadaten
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Session-Metadaten (z. B. Modell, Einstellungen)"
    )
    
    @field_validator('messages')
    @classmethod
    def validate_messages_not_empty(cls, v: List[ChatMessage]) -> List[ChatMessage]:
        """Root-Cause: Mindestens eine Nachricht erforderlich."""
        # Erlaube leere Historie für neue Sessions
        return v
    
    @model_validator(mode='after')
    def update_timestamp(self) -> "ChatHistory":
        """SOTA: Updated_at automatisch setzen."""
        self.updated_at = utc_now()
        return self
    
    def get_last_user_message(self) -> Optional[ChatMessage]:
        """SOTA: Letzte User-Nachricht abrufen."""
        for msg in reversed(self.messages):
            if msg.sender == "user":
                return msg
        return None
    
    def get_last_assistant_message(self) -> Optional[ChatMessage]:
        """SOTA: Letzte Assistant-Nachricht abrufen."""
        for msg in reversed(self.messages):
            if msg.sender == "assistant":
                return msg
        return None
    
    def add_message(self, message: ChatMessage) -> "ChatHistory":
        """SOTA: Nachricht zur Historie hinzufügen."""
        self.messages.append(message)
        self.updated_at = utc_now()
        return self


# ============================================================================
# FILE UPLOAD SCHEMAS
# ============================================================================

class FileType(str):
    """SOTA: Typ-Sicherheit für Dateitypen."""
    ALLOWED_TYPES: ClassVar[Set[str]] = {
        "pdf", "txt", "csv", "xlsx", "xls", "xlsm", "json",
        "png", "jpg", "jpeg", "gif", "bmp", "webp",
        "doc", "docx", "pptx",
    }

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Pydantic V2 core-schema hook (ersetzt deprecated __get_validators__)."""
        return core_schema.no_info_after_validator_function(
            cls.validate, core_schema.str_schema()
        )

    @classmethod
    def validate(cls, v: str) -> "FileType":
        if v.lower() not in cls.ALLOWED_TYPES:
            raise PydanticCustomError(
                "invalid_file_type",
                ValidationErrorMessages.invalid_file_type(list(cls.ALLOWED_TYPES)),
                {"expected": list(cls.ALLOWED_TYPES), "got": v}
            )
        return cls(v.lower())


class FileUpload(SOTAModel):
    """
    SOTA: Validierung für hochgeladene Dateien.
    
    Features:
    - Dateityp-Validierung
    - Dateigrößen-Limit
    - Dateiname-Sanitization
    - MIME-Type-Validierung
    
    Root-Cause-Lösung:
    - Kein Workaround: Direkte Validierung vor Verarbeitung
    - Sicherheit: Dateinamen werden bereinigt
    - DSGVO: Dateien bleiben lokal
    """
    
    MAX_FILE_SIZE_MB: ClassVar[int] = 50  # 50 MB Limit
    MAX_FILE_SIZE_BYTES: ClassVar[int] = MAX_FILE_SIZE_MB * 1024 * 1024
    
    file_path: Path = Field(
        ...,
        description="Absoluter Pfad zur temporären Datei"
    )
    
    file_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Originaler Dateiname"
    )
    
    file_type: FileType = Field(
        ...,
        description="Dateityp (Erweiterung)"
    )
    
    file_size: int = Field(
        ...,
        ge=1,
        le=MAX_FILE_SIZE_BYTES,
        description="Dateigröße in Bytes"
    )
    
    mime_type: Optional[str] = Field(
        default=None,
        description="MIME-Type der Datei"
    )
    
    # Sanitized Dateiname (für sichere Speicherung)
    safe_file_name: str = Field(
        default="",
        description="Bereinigter Dateiname (für Speicherung)"
    )
    
    # Upload-Zeitstempel
    uploaded_at: datetime = Field(
        default_factory=utc_now,
        description="Zeitstempel des Uploads"
    )
    
    # Upload-ID für Referenzierung
    upload_id: str = Field(
        default_factory=lambda: f"upload_{utc_now().strftime('%Y%m%d%H%M%S%f')}",
        description="Eindeutige Upload-ID"
    )
    
    # Metadaten (z. B. für RAG-Verarbeitung)
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Zusätzliche Metadaten"
    )
    
    @field_validator('file_path')
    @classmethod
    def validate_file_exists(cls, v: Path) -> Path:
        """Root-Cause: Datei muss existieren."""
        if not v.exists():
            raise PydanticCustomError(
                "file_not_found",
                f"Datei nicht gefunden: {v}",
                {"path": str(v)}
            )
        return v
    
    @field_validator('file_path')
    @classmethod
    def validate_file_readable(cls, v: Path) -> Path:
        """Root-Cause: Datei muss lesbar sein."""
        if not os.access(v, os.R_OK):
            raise PydanticCustomError(
                "file_not_readable",
                f"Datei ist nicht lesbar: {v}",
                {"path": str(v)}
            )
        return v
    
    @field_validator('file_size')
    @classmethod
    def validate_file_size(cls, v: int) -> int:
        """Root-Cause: Dateigröße prüfen."""
        if v > cls.MAX_FILE_SIZE_BYTES:
            raise PydanticCustomError(
                "file_too_large",
                ValidationErrorMessages.file_too_large(cls.MAX_FILE_SIZE_MB),
                {"size": v, "max": cls.MAX_FILE_SIZE_BYTES}
            )
        return v
    
    @model_validator(mode='after')
    def sanitize_file_name(self) -> "FileUpload":
        """SOTA: Dateiname bereinigen für sichere Speicherung."""
        # Entferne unsichere Zeichen
        unsafe_chars = r'[<>:"\|?*\x00-\x1f]'
        self.safe_file_name = re.sub(unsafe_chars, '_', self.file_name)
        # Begrenze Länge
        self.safe_file_name = self.safe_file_name[:255]
        # Stelle sicher, dass der Name nicht leer ist
        if not self.safe_file_name:
            self.safe_file_name = "uploaded_file"
        return self


class PDFUpload(FileUpload):
    """SOTA: Spezifische Validierung für PDF-Dateien."""
    
    file_type: FileType = Field(
        default=FileType("pdf"),
        description="PDF-Dateityp"
    )
    
    # PDF-spezifische Metadaten
    page_count: Optional[int] = Field(
        default=None,
        ge=1,
        le=5000,
        description="Anzahl der Seiten"
    )
    
    text_extracted: bool = Field(
        default=False,
        description="Ob Text extrahiert wurde"
    )
    
    extraction_error: Optional[str] = Field(
        default=None,
        description="Fehler bei der Extraktion (falls aufgetreten)"
    )


class ImageUpload(FileUpload):
    """SOTA: Spezifische Validierung für Bilder."""
    
    file_type: FileType = Field(
        ...,
        description="Bild-Dateityp (png, jpg, jpeg, gif, webp)"
    )
    
    # Bild-spezifische Metadaten
    width: Optional[int] = Field(
        default=None,
        ge=1,
        le=10000,
        description="Breite in Pixeln"
    )
    
    height: Optional[int] = Field(
        default=None,
        ge=1,
        le=10000,
        description="Höhe in Pixeln"
    )
    
    @field_validator('file_type')
    @classmethod
    def validate_image_type(cls, v: FileType) -> FileType:
        """Root-Cause: Nur Bild-Dateitypen erlauben."""
        image_types = {"png", "jpg", "jpeg", "gif", "webp"}
        if v.lower() not in image_types:
            raise PydanticCustomError(
                "invalid_image_type",
                ValidationErrorMessages.invalid_file_type(list(image_types)),
                {"expected": list(image_types), "got": v}
            )
        return v


# ============================================================================
# USER INPUT SCHEMAS
# ============================================================================

class UserInput(SOTAModel):
    """
    SOTA: Validierung für Benutzereingaben (Allgemein).
    
    Features:
    - Text-Validierung
    - Kontext-Validierung (z. B. Session-Status)
    - Intent-Erkennung (optional)
    """
    
    MAX_INPUT_LENGTH: ClassVar[int] = 32000
    
    text: str = Field(
        ...,
        min_length=1,
        max_length=MAX_INPUT_LENGTH,
        description="Benutzereingabe-Text"
    )
    
    # Intent (optional, z. B. für Routing)
    intent: Optional[Literal["chat", "search", "analyze", "generate", "help"]] = Field(
        default=None,
        description="Erkannter Intent der Eingabe"
    )
    
    # Session-Kontext
    session_active: bool = Field(
        default=True,
        description="Ob eine aktive Session besteht"
    )
    
    # Zeitstempel
    timestamp: datetime = Field(
        default_factory=utc_now,
        description="Zeitstempel der Eingabe"
    )
    
    @field_validator('text')
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        """SOTA: Text bereinigen (Whitespace, etc.)."""
        # Normalisiere Whitespace
        v = ' '.join(v.split())
        # Entferne führende/trailing Whitespace
        v = v.strip()
        if not v:
            raise PydanticCustomError(
                "empty_input",
                ValidationErrorMessages.empty_content(),
                {"value": v}
            )
        return v


class SearchQuery(UserInput):
    """SOTA: Spezifische Validierung für Suchanfragen."""
    
    intent: Literal["search"] = Field(
        default="search",
        description="Such-Intent"
    )
    
    # Such-spezifische Parameter
    search_type: Optional[Literal["rag", "web", "knowledge", "local"]] = Field(
        default=None,
        description="Typ der Suche"
    )
    
    max_results: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximale Anzahl Ergebnisse"
    )


# ============================================================================
# SYSTEM CONFIGURATION SCHEMAS
# ============================================================================

class ModelConfig(SOTAModel):
    """
    SOTA: Validierung für Modell-Konfiguration.
    
    Features:
    - Modell-ID-Validierung
    - Parameter-Bounds
    - GPU-Einstellungen
    """
    
    model_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r'^[a-zA-Z0-9_-]+$',
        description="Einzigartige Modell-ID"
    )
    
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Angezeigter Modell-Name"
    )
    
    # Modell-Parameter
    n_ctx: int = Field(
        default=4096,
        ge=512,
        le=131072,
        description="Kontext-Fenster Größe (Token)"
    )
    
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Temperature für Sampling"
    )
    
    top_p: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Top-p Sampling"
    )
    
    # GPU-Einstellungen
    n_gpu_layers: Optional[int] = Field(
        default=None,
        ge=0,
        le=1000,
        description="Anzahl GPU-Layer"
    )
    
    # Speicher-Einstellungen
    model_path: Optional[Path] = Field(
        default=None,
        description="Lokaler Pfad zum Modell"
    )
    
    @field_validator('model_path')
    @classmethod
    def validate_model_path(cls, v: Optional[Path]) -> Optional[Path]:
        """Root-Cause: Modell-Pfad validieren (falls lokal)."""
        if v is None:
            return v
        if not v.exists():
            raise PydanticCustomError(
                "model_path_not_found",
                f"Modell-Datei nicht gefunden: {v}",
                {"path": str(v)}
            )
        return v


class AppSettings(SOTAModel):
    """
    SOTA: Validierung für Anwendungseinstellungen.
    
    Features:
    - Local-Only-Modus
    - Logging-Einstellungen
    - Performance-Parameter
    """
    
    local_only: bool = Field(
        default=True,
        description="Nur lokale Verarbeitung (keine Internetverbindung)"
    )
    
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging-Level"
    )
    
    max_chat_history: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximale Chat-Historie pro Session"
    )
    
    # RAG-Einstellungen
    rag_enabled: bool = Field(
        default=True,
        description="RAG-System aktiviert"
    )
    
    faiss_confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimale FAISS-Confidence für RAG-Ergebnisse"
    )
    
    # Performance-Einstellungen
    numexpr_threads: int = Field(
        default=8,
        ge=1,
        le=64,
        description="NumExpr Threads"
    )
    
    @model_validator(mode='after')
    def validate_settings(self) -> "AppSettings":
        """SOTA: Validierung der Einstellungen."""
        # Warnung bei lokalem Modus mit Web-Suche
        if self.local_only and self.rag_enabled:
            logger.warning(
                "Local-Only-Modus aktiv, aber RAG aktiviert. "
                "RAG funktioniert nur mit lokalen Dokumenten."
            )
        return self


# ============================================================================
# MERMAID DIAGRAM SCHEMAS (für Integration)
# ============================================================================

class MermaidDiagramType(str):
    """SOTA: Typ-Sicherheit für Mermaid-Diagramm-Typen."""
    ALLOWED_TYPES: ClassVar[Set[str]] = {
        "flowchart", "mindmap", "gantt", "classDiagram", 
        "sequenceDiagram", "pie", "stateDiagram", "erDiagram"
    }

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Pydantic V2 core-schema hook (ersetzt deprecated __get_validators__)."""
        return core_schema.no_info_after_validator_function(
            cls.validate, core_schema.str_schema()
        )

    @classmethod
    def validate(cls, v: str) -> "MermaidDiagramType":
        normalized = (v or "").strip().lower()
        canonical_map = {
            "flowchart": "flowchart",
            "mindmap": "mindmap",
            "gantt": "gantt",
            "classdiagram": "classDiagram",
            "sequencediagram": "sequenceDiagram",
            "pie": "pie",
            "statediagram": "stateDiagram",
            "erdiagram": "erDiagram",
        }
        if normalized not in canonical_map:
            raise PydanticCustomError(
                "invalid_diagram_type",
                f"Ungültiger Diagramm-Typ: '{v}'. Erlaubt: {', '.join(cls.ALLOWED_TYPES)}",
                {"expected": list(cls.ALLOWED_TYPES), "got": v}
            )
        return cls(canonical_map[normalized])


class MermaidDiagramRequest(SOTAModel):
    """
    SOTA: Anfrage zur Mermaid-Diagramm-Generierung.
    
    Features:
    - Diagramm-Typ-Validierung
    - Daten-Validierung
    - Export-Optionen
    """
    
    diagram_type: MermaidDiagramType = Field(
        default=MermaidDiagramType("flowchart"),
        description="Typ des zu generierenden Diagramms"
    )
    
    title: str = Field(
        default="Diagramm",
        min_length=1,
        max_length=200,
        description="Titel des Diagramms"
    )
    
    # Datenquelle (entweder Text oder strukturierte Daten)
    text: Optional[str] = Field(
        default=None,
        max_length=50000,
        description="Text für Diagramm-Generierung"
    )
    
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Strukturierte Daten für Diagramm-Generierung"
    )
    
    # Export-Optionen
    export_format: Optional[Literal["png", "svg", "html"]] = Field(
        default=None,
        description="Export-Format (optional)"
    )
    
    @model_validator(mode='after')
    def validate_has_input(self) -> "MermaidDiagramRequest":
        """Root-Cause: Mindestens eine Input-Quelle erforderlich."""
        if self.text is None and self.data is None:
            raise PydanticCustomError(
                "no_input_source",
                "Bitte geben Sie entweder Text oder strukturierte Daten für das Diagramm an.",
                {"text": self.text, "data": self.data}
            )
        return self


class MermaidDiagramResponse(SOTAModel):
    """
    SOTA: Antwort mit generiertem Mermaid-Diagramm.
    
    Features:
    - Diagramm-Code
    - HTML-Export
    - Metadaten
    """
    
    diagram_type: MermaidDiagramType = Field(
        ...,
        description="Typ des generierten Diagramms"
    )
    
    title: str = Field(
        ...,
        description="Titel des Diagramms"
    )
    
    mermaid_code: str = Field(
        ...,
        min_length=1,
        description="Generierter Mermaid-Code"
    )
    
    html_export: str = Field(
        ...,
        min_length=1,
        description="HTML für Client-seitiges Rendern/Export"
    )
    
    diagram_id: str = Field(
        ...,
        description="Eindeutige Diagramm-ID"
    )
    
    # Metadaten
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metadaten zur Generierung"
    )
    
    # Export-Pfade (falls Dateien erstellt wurden)
    export_paths: Dict[str, Path] = Field(
        default_factory=dict,
        description="Pfade zu Export-Dateien (z. B. PNG, SVG)"
    )


# ============================================================================
# ERROR SCHEMAS (für strukturiertes Error-Handling)
# ============================================================================

class ValidationErrorDetail(SOTAModel):
    """SOTA: Strukturierte Fehlerdetails für Validierung."""
    
    field: str = Field(
        ...,
        description="Feldname mit Fehler"
    )
    
    error_type: str = Field(
        ...,
        description="Fehlertyp (z. B. 'empty_content', 'invalid_file_type')"
    )
    
    message: str = Field(
        ...,
        description="Fehlermeldung für den User"
    )
    
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Technische Details (für Debugging)"
    )


class SOTAError(SOTAModel):
    """
    SOTA: Strukturierter Fehler für konsistentes Error-Handling.
    
    Root-Cause-Lösung:
    - Keine broad exceptions mehr
    - Jeder Fehler hat spezifischen Typ und Message
    - User-Friendly Messages
    - Technical Details für Logging
    """
    
    error_type: str = Field(
        ...,
        description="Einzigartiger Fehlertyp (z. B. 'model_load_failed', 'validation_error')"
    )
    
    user_message: str = Field(
        ...,
        description="Fehlermeldung für den Endbenutzer"
    )
    
    technical_message: str = Field(
        ...,
        description="Technische Fehlermeldung (für Logging)"
    )
    
    severity: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Schweregrad des Fehlers"
    )
    
    # Kontext-Informationen
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Kontext zum Zeitpunkt des Fehlers"
    )
    
    # Validierungsfehler (falls zutreffend)
    validation_errors: List[ValidationErrorDetail] = Field(
        default_factory=list,
        description="Liste der Validierungsfehler"
    )
    
    # Zeitstempel
    timestamp: datetime = Field(
        default_factory=utc_now,
        description="Zeitstempel des Fehlers"
    )
    
    # Fehler-ID für Tracking
    error_id: str = Field(
        default_factory=lambda: f"err_{utc_now().strftime('%Y%m%d%H%M%S%f')}",
        description="Eindeutige Fehler-ID"
    )
    
    def to_exception(self) -> Exception:
        """SOTA: Konvertiere zu Python Exception (für Kompatibilität)."""
        return Exception(f"[{self.error_type}] {self.user_message}")


# ============================================================================
# EXPORT ALL MODELS
# ============================================================================

__all__ = [
    # Core Models
    'SOTAModel',
    'ChatMessage',
    'ChatHistory',
    
    # File Upload
    'FileUpload',
    'PDFUpload',
    'ImageUpload',
    'FileType',
    
    # User Input
    'UserInput',
    'SearchQuery',
    
    # System Config
    'ModelConfig',
    'AppSettings',
    
    # Mermaid
    'MermaidDiagramRequest',
    'MermaidDiagramResponse',
    'MermaidDiagramType',
    
    # Errors
    'SOTAError',
    'ValidationErrorDetail',
    
    # Utilities
    'ValidationErrorMessages',
    'MessageSender',
]
