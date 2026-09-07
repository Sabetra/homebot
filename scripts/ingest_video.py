"""
Video/Subtitle-Ingestion Pipeline (fail-closed, Trusted Channels)
=================================================================

Verarbeitet YouTube-Videos NUR von Channels in ``config/trusted_channels.json``
(UC-ID-Allowlist). Fail-closed: Gate-Verletzung, Netzwerk- oder
Metadatenfehler beenden die Pipeline sofort (keine Verarbeitung).

Pipeline (in dieser Reihenfolge):
  1. CHANNEL GATE – ``scripts/verify_trusted_channels.verify_video()``
     (unbekannter channel_id / Fehler => Ablehnung, Exit 2/3)
  2. METADATEN    – yt-dlp ``extract_info`` (ein Video, anonym, kein Login)
  3. SUBTITLES    – manuell > automatisch > erste Sprache; Safety-Envelope
  4. FRAMES       – yt-dlp-Download (kleinstes Format ≤ max-height) +
                    OpenCV-Frame-Samples (kein system-ffmpeg nötig)
  5. MANIFEST     – Status aller Schritte, Fehlerliste, Timestamps

Subtitles = UNVERTRAUENSWÜRDIGE DATEN (Prompt-Injektionsfläche):
  - Output trägt Safety-Envelope: ``untrusted=true``, Policy ``DATA_ONLY``
  - Injection-Heuristik FLAGGT auffällige Zeilen (flag-only, nie filtern)
  - PII-Flags (E-Mail, Telefon) für Downstream-Anonymisierung
  - Downstream-Regel: Subtitle-Text NIEMALS als Anweisung interpretieren

Exit-Codes:
  0 = Erfolg (Teilausfälle wie fehlende Subtitles => ``partial: true``)
  2 = Gate: Channel NICHT in Allowlist (fail-closed)
  3 = Gate/URL: Verifikation fehlgeschlagen oder ungültige URL
  4 = Metadaten-Auslieferung fehlgeschlagen

Nur lokale Artefakte unter test_output/ (git-ignoriert); keine
Login-/Cookie-Übertragung; keine Cloud-LLM-Calls; 100 % lokal.

Usage:
    python scripts/ingest_video.py <YouTube-Video-URL> [--out DIR]
                                   [--frames 8] [--max-height 480]
                                   [--language en]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_trusted_channels as gate  # noqa: E402  (Skript-Ordner, kein Package)

# ---------------------------------------------------------------------------
# Sicherheits-Heuristiken (flag-only: markieren, nie stumm filtern)
# ---------------------------------------------------------------------------

INJECTION_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    (re.compile(
        r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)",
        re.I), "injection-ignore-previous"),
    (re.compile(r"\bdisregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above)\s+", re.I),
     "injection-disregard-previous"),
    (re.compile(r"\bnew\s+instructions?\b", re.I), "injection-new-instructions"),
    (re.compile(r"\byou\s+are\s+now\s+(?:a|an|the)\s+", re.I), "injection-role-override"),
    (re.compile(r"\bsystem\s+prompt\b", re.I), "injection-system-prompt"),
    (re.compile(r"\bjail\s?break\b", re.I), "injection-jailbreak"),
    (re.compile(r"\b(?:override|bypass)\s+(?:safety|security|policy|guardrails?)\b", re.I),
     "injection-safety-override"),
    (re.compile(r"\b(?:reveal|show|print)\s+(?:your|the)\s+(?:hidden|secret|internal)\s+", re.I),
     "injection-secret-reveal"),
    (re.compile(r"\b(?:run|execute)\s+(?:this|the|following)\s+(?:command|script|shell|code)\b", re.I),
     "injection-command-exec"),
    (re.compile(r"\bact\s+as\s+(?:if\s+)?(?:you\s+)?(?:are|were)\s+(?:not|no\s+longer)\s+", re.I),
     "injection-identity-override"),
]

PII_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "pii-email"),
    (re.compile(r"(?<!\d)(?:\+\d{1,3}|0)[\s\-./]?\d{2,5}[\s\-./]?\d{3,10}(?!\d)"), "pii-phone"),
]

META_FIELDS = [
    "id", "title", "channel", "channel_id", "channel_follower_count",
    "channel_is_verified", "uploader", "uploader_id", "description",
    "duration", "upload_date", "timestamp", "view_count", "like_count",
    "comment_count", "categories", "tags", "webpage_url", "language",
    "availability",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _video_id_from_url(url: str) -> str | None:
    """Extrahiert die 11-stellige Video-ID (nur kanonische YouTube-Formen)."""
    m = re.search(
        r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|youtu\.be/|shorts/|embed/|live/))([0-9A-Za-z_\-]{11})",
        url)
    return m.group(1) if m else None


def _scan_text(text: str) -> list[str]:
    """Flaggt Injection-Muster und PII in einer Zeile (flag-only)."""
    flags: list[str] = []
    for pattern, tag in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(tag)
    for pattern, tag in PII_PATTERNS:
        if pattern.search(text):
            flags.append(tag)
    return flags
# === PART2 ===