"""Common chat command utilities.

Do NOT include any model or image processing logic here.
"""
from __future__ import annotations

from typing import Optional, Dict, Any

# Canonical set of reset/clear commands recognized across UIs
RESET_COMMANDS = {"/clear", "/reset", "/cls"}


def parse_command(text: str) -> Optional[str]:
    """Parse a chat command string.

    Returns one of:
      - "reset" for reset/clear/cls commands
      - None if not a recognized command
    """
    if not text:
        return None
    cmd = text.strip().lower()
    if cmd in RESET_COMMANDS:
        return "reset"
    return None


def is_reset_command(text: str) -> bool:
    """Return True if the input text is a reset command."""
    return parse_command(text) == "reset"


# --- Extended commands (non-breaking) ---
HELP_TEXT = (
    "Verfügbare Befehle:\n"
    "  /reset, /clear, /cls  – Kontext zurücksetzen\n"
    "  /help                 – Diese Hilfe anzeigen\n"
    "  /debug on|off         – Debug-Modus umschalten\n"
    "  /trace dev on|off     – Developer-Ansicht im Trace umschalten\n"
)


def parse_extended_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse extended slash-commands without breaking existing API.

    Returns a dict like {"name": "help"|"reset"|"debug"|"trace_dev", "args": {...}} or None.
    """
    if not text or not text.strip().startswith("/"):
        return None
    raw = text.strip().lower()
    # Reset aliases
    if raw in RESET_COMMANDS:
        return {"name": "reset", "args": {}}
    if raw == "/help":
        return {"name": "help", "args": {}}
    # /debug on|off
    if raw.startswith("/debug"):
        parts = raw.split()
        state = parts[1] if len(parts) > 1 else "toggle"
        if state not in {"on", "off", "toggle"}:
            state = "toggle"
        return {"name": "debug", "args": {"state": state}}
    # /trace dev on|off
    if raw.startswith("/trace"):
        parts = raw.split()
        if len(parts) >= 2 and parts[1] == "dev":
            state = parts[2] if len(parts) > 2 else "toggle"
            if state not in {"on", "off", "toggle"}:
                state = "toggle"
            return {"name": "trace_dev", "args": {"state": state}}
    return None


__all__ = [
    "RESET_COMMANDS",
    "parse_command",
    "is_reset_command",
    "parse_extended_command",
    "HELP_TEXT",
]
