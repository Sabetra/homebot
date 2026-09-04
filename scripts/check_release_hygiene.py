#!/usr/bin/env python3
"""Public-Launch-Hygiene-Check (deterministisch, ohne Netzwerk und Modelle).

Prüft, dass das Repository öffentlich gemacht werden kann:

  FAIL (blockt, Exit 1)
    1. Maschinen-spezifische Windows-Pfade mit realen User-Names
       (z. B. ``C:\\Users\\bot6``, ``C:\\Users\\PC``) in aktiven Dateien
       (Code, Skripte, aktive Doku, Root-Konfig).
       Erlaubt bleiben generische Platzhalter wie ``C:\\Users\\<user>``.
    2. Sensitive tracked Dateien: ``settings.json``, ``monitoring/``,
       ``.continue/``, ``data/``, ``*.db``, ``*.key``, ``.env*``, Venvs.
    3. Secret-Patterns im tracked Inhalt (API-Keys, Private Keys).
    4. Fehlende Release-Pflicht-Dateien (LICENSE, README, CoC, CHANGELOG,
       AGENTS).

  WARN (nicht blockierend)
    5. Maschinen-spezifische Pfade in historischen Archiven
       (``docs_archive/``, ``docs/09_archived/``) — dokumentierte Historie.
       Mit ``--strict`` blockieren WARNs ebenfalls.

Nutzung:
    python scripts/check_release_hygiene.py
    python scripts/check_release_hygiene.py --strict

Exit 0 = grün, Exit 1 = rot.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Historische Archive: Pfade dort sind dokumentierte Historie -> nur WARN.
WARN_PREFIXES = ("docs_archive/", "docs/09_archived/")

# Reale Windows-User-Pfade: mindestens 2 Buchstaben/Ziffern nach \Users\,
# generische Platzhalter (z. B. <user>) bleiben erlaubt.
# Case-insensitiv: Auf Windows-Dateisystemen ist die Groessen-Schreibung
# nicht semantisch - "c:\Users\..." ist genauso ein Maschinenpfad wie "C:\Users\...".
USER_PATH_RE = re.compile(
    r"C:\\Users\\(?!<user>)[A-Za-z0-9][A-Za-z0-9._-]+",
    re.IGNORECASE,
)

SENSITIVE_FILE_PATTERNS = (
    re.compile(r"^settings\.json$"),
    re.compile(r"^(monitoring|\.continue|data)(/|$)"),
    re.compile(r"\.(db|db-wal|db-shm|sqlite3?|key|p12|pfx|pem)$", re.IGNORECASE),
    re.compile(r"^\.env(\..*)?$"),
    re.compile(r"^(venv_|\.venv)(/|$)"),
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # OpenAI-Stil API-Keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS Access Key IDs
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"']"
        r"([A-Za-z0-9+/=_\-]{20,})[\"']"
    ),
)

SECRET_ALLOW_TOKENS = ("your", "xxx", "example", "placeholder", "changeme", "dummy")

REQUIRED_FILES = (
    "LICENSE",
    "README.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "AGENTS.md",
)


def tracked_files() -> list[str]:
    """Alle im Git-Index erfassten Dateien (das ist, was öffentlich wird)."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def read_text(path: Path) -> str | None:
    """Datei als Text lesen; Binärdateien (NUL-Byte) werden übersprungen."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def is_secret_value(value: str, line: str) -> bool:
    """Erkenne offensichtliche Platzhalter/Hashes, die keine Secrets sind."""
    low_line = line.lower()
    if any(tok in low_line for tok in SECRET_ALLOW_TOKENS):
        return False
    v = value.strip()
    if re.fullmatch(r"(.)\1{19,}", v):  # "aaaaaaaa..." -> Platzhalter
        return False
    # Reine, lange Hex-Strings sind i. d. R. Hashes (z. B. SHA-256), keine Secrets.
    if re.fullmatch(r"[0-9a-fA-F]+", v) and len(v) % 2 == 0 and len(v) >= 32:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Public-Launch-Hygiene-Check (deterministisch).")
    parser.add_argument("--strict", action="store_true",
                        help="WARNs (Historie-Archive) wie FAILs behandeln")
    args = parser.parse_args()

    fails: list[str] = []
    warns: list[str] = []

    # Pflicht-Dateien (Existenz im Working Tree).
    for name in REQUIRED_FILES:
        if not (REPO_ROOT / name).is_file():
            fails.append(f"PFFLICHT-DATEI FEHLT: {name}")

    files = tracked_files()

    # Sensitive tracked Dateien.
    for f in files:
        posix = f.replace("\\", "/")
        for pat in SENSITIVE_FILE_PATTERNS:
            if pat.search(posix):
                fails.append(f"SENSITIVE DATEI TRACKED: {posix}")
                break

    # Inhalts-Checks (Pfade + Secrets).
    for f in files:
        posix = f.replace("\\", "/")
        text = read_text(REPO_ROOT / f)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = USER_PATH_RE.search(line)
            if m:
                msg = f"MASCHINEN-PFAD {posix}:{lineno}: {m.group(0)}"
                if any(posix.startswith(p) for p in WARN_PREFIXES):
                    warns.append(msg)
                else:
                    fails.append(msg)
            for pat in SECRET_PATTERNS:
                m = pat.search(line)
                if not m:
                    continue
                value = m.group(1) if m.lastindex else m.group(0)
                if is_secret_value(value, line):
                    fails.append(f"SECRET-PATTERN {posix}:{lineno}")

    print("=" * 60)
    print("Public-Launch-Hygiene-Check")
    print("=" * 60)
    for msg in fails:
        print(f"[FAIL] {msg}")
    for msg in warns:
        print(f"[WARN] {msg}")
    if not fails and not warns:
        print("[OK]   keine Probleme gefunden")
    print("-" * 60)
    print(f"FAIL: {len(fails)}   WARN: {len(warns)}   Dateien: {len(files)}")

    if fails or (args.strict and warns):
        print("ERGEBNIS: ROT")
        return 1
    suffix = "  (strict: WARNs würden blocken)" if args.strict and warns else ""
    print(f"ERGEBNIS: GRUEN{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
