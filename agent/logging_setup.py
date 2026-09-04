"""Centralized logging setup for the agent and GUI.

Provides JSON-formatted logs with a simple configurator.
Usage:
    from agent.logging_setup import configure_logging, set_log_level
    configure_logging()  # Once at startup

Do NOT include any model or image processing logic here.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": self._safe_message(record),
        }
        # Attach common extras if present
        for k in ("event", "elapsed_ms", "module", "funcName", "lineno"):
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = self._safe_serialize(v)
        
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            # Fallback für nicht-serialisierbare Daten
            safe_payload = {
                "ts": payload["ts"],
                "level": payload["level"],
                "logger": payload["logger"],
                "msg": str(payload["msg"]),
                "serialization_error": str(e)
            }
            return json.dumps(safe_payload, ensure_ascii=False)
    
    def _safe_message(self, record: logging.LogRecord) -> str:
        """Sicherer Message-Extraktion mit Fallback"""
        try:
            return record.getMessage()
        except (TypeError, AttributeError) as e:
            # Expected errors for malformed log records
            return str(record.msg)
        except Exception as e:
            # Unexpected errors - log them but don't fail
            return f"[LOG_ERROR: {type(e).__name__}] {str(record.msg)}"
    
    def _safe_serialize(self, obj) -> Any:
        """Sichere Serialisierung mit Fallback auf String-Repr"""
        try:
            # Test ob das Objekt JSON-serialisierbar ist
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)


def _level_from_str(level: str | None) -> int:
    if not level:
        return logging.INFO
    level = level.strip().upper()
    return {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }.get(level, logging.INFO)


_configured = False


def configure_logging(level: str | None = None) -> None:
    """Configure root logger once with JSON formatter.

    Level can be provided or taken from ENV LOG_LEVEL. No-op if already configured.
    """
    global _configured
    if _configured:
        return

    lvl = _level_from_str(level or os.getenv("LOG_LEVEL"))
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(lvl)

    # Reduce verbosity of noisy libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Reduziere pdfplumber/pdfminer debug verbosity
    logging.getLogger("pdfminer").setLevel(logging.WARNING)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)

    _configured = True


def set_log_level(level: str) -> None:
    """Dynamically adjust the root log level at runtime."""
    lvl = _level_from_str(level)
    logging.getLogger().setLevel(lvl)


__all__ = ["configure_logging", "set_log_level", "JsonFormatter"]
