#!/usr/bin/env python3
"""secret_guard.py — Fail-Closed Secret-/Key-Guard fuer Git-Workflows.

Erkennt Private Keys, Keystores, .env/Credential-Dateien und hochsichere
Secret-Strings in Dateien, die committet oder gepusht werden sollen.

Abwehrschichten (alle fail-closed):
  L1  .gitignore                    (Secrets werden gar nicht erst gestaged)
  L2  scripts/autosave_snapshot.ps1 (unstaged + Log vor Auto-Commit)
  L2b .githooks/pre-commit          (blockt den bewussten Commit)
  L3  .githooks/pre-push            (blockt den Push; deckt --no-verify ab)
  L4  scripts/check_release_hygiene.py  (Audit aller tracked Dateien)

Design:
  - stdlib-only, deterministisch, offline.
  - Hohe Praezision (wenig False Positives): Das Gate blockt Commits/Pushes.
  - Fail-closed: interner Fehler -> Exit 2 (der Aufrufer MUSS blockieren).

Exit-Codes:
  0 = sauber
  1 = geflaggte Dateien (Pfade auf stdout, eine Zeile pro Datei)
  2 = Fehler (fail-closed -> blockieren)

Verwendung:
  python scripts/secret_guard.py --staged
  python scripts/secret_guard.py --tracked
  python scripts/secret_guard.py --push <local_sha> <remote_sha>
  python scripts/secret_guard.py --files <pfad> [<pfad> ...]
  python scripts/secret_guard.py --staged --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- Name-basiert (hohe Konfidenz; posix-Pfad) ----------------------------
NAME_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519|id_ed448)(\.old)?$"),
     "private-key-file"),
    (re.compile(r"(^|/)(authorized_keys|\.netrc)$"), "credential-file"),
    (re.compile(r"\.(p12|pfx|pkcs12|keystore|jks|ppk|p7b)$", re.I),
     "keystore-file"),
    (re.compile(r"\.(pem|key)$", re.I), "crypto-file"),
    (re.compile(r"(^|/)\.env(\..*)?$"), "env-file"),
    (re.compile(r"(^|/)secrets\.(ya?ml|json|toml)$", re.I), "secrets-file"),
    (re.compile(r"(^|/)(credentials|service-account[^/]*)\.json$"),
     "credential-file"),
    (re.compile(r"^settings\.json$"), "local-settings"),
)

# .env-Vorlagen sind dokumentiert und enthalten keine echten Secrets.
ENV_EXAMPLE_RE = re.compile(r"\.env(\..*)?\.(example|sample|template|dist)$", re.I)

# --- Inhalts-basiert (hohe Konfidenz) -------------------------------------
CONTENT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?"
                r"PRIVATE KEY(?: BLOCK)?-----"), "private-key-material"),
    # Marker fuer OpenSSH-Private-Keys (base64 von "openssh-key-v1").
    # WICHTIG: String bewusst gesplittet - das Guard darf beim Scan seiner
    # eigenen Quelle nicht auf den eigenen Treffer-String treffen (Selbst-Scan).
    (re.compile(r"b3BlbnNza" + r"C1rZXktdjE"), "openssh-private-key"),
    (re.compile(r"PuTTY-User-Key-File-(?:2|3)"), "putty-private-key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-access-key-id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), "github-pat"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), "github-fine-grained-pat"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "openai-style-key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "slack-token"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*"
                r"['\"]([A-Za-z0-9+/=_\-]{20,})['\"]"), "secret-assignment"),
)

# Platzhalter-Werte, die keine echten Secrets sind (False-Positive-Schutz).
ALLOW_TOKENS = ("your", "xxx", "example", "placeholder", "changeme",
                "dummy", "redacted")
def _git(*args: str) -> tuple[str | None, str]:
    """git-Aufruf; liefert (stdout, stderr). stdout=None bei Fehler.

    WICHTIG: Explizit UTF-8 dekodieren. `text=True` ohne Encoding nutzt die
    Windows-Locale (cp1252) und wirft UnicodeDecodeError bei UTF-8-Dateiinhalten
    (z. B. Umlauten) - das wuerde Scans still truncieren (Fail-Open-Risiko).
    """
    try:
        p = subprocess.run(["git", *args], cwd=REPO_ROOT,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
    except Exception as exc:  # fail-closed
        return None, str(exc)
    if p.returncode != 0:
        return None, (p.stderr or p.stdout or "").strip()
    return p.stdout, ""


def staged_files() -> list[str]:
    """Staged Dateien; Loeschungen (D) werden NICHT gescannt -
    eine Loeschung entfernt Inhalt und kann kein neues Secret einfuehren."""
    out, err = _git("diff", "--cached", "--name-only", "-z",
                    "--diff-filter=ACMR")
    if out is None:
        raise RuntimeError(f"git diff --cached fehlgeschlagen: {err}")
    return [x for x in out.split("\0") if x]


def tracked_files() -> list[str]:
    out, err = _git("ls-files", "-z")
    if out is None:
        raise RuntimeError(f"git ls-files fehlgeschlagen: {err}")
    return [x for x in out.split("\0") if x]


def push_files(local: str, remote: str) -> list[str]:
    if all(c == "0" for c in remote):  # neue Branche: alles in local
        out, err = _git("ls-tree", "-r", "--name-only", "-z", local)
    else:
        # Nur Dateien, die auf dem Remote neu/geaendert werden (ACMR).
        # Loeschungen (D) nicht scannen: sie entfernen Inhalt vom Remote
        # und koennen kein neues Secret einfuehren.
        out, err = _git("diff", "--name-only", "-z",
                        "--diff-filter=ACMR", remote, local)
    if out is None:
        raise RuntimeError(f"git diff/ls-tree fehlgeschlagen: {err}")
    return [x for x in out.split("\0") if x]


def read_blob(path: str, sha: str | None = None) -> str | None:
    """Inhalt einer Datei aus dem Git-Objekt-Speicher (Index/SHA) oder Worktree."""
    posix = path.replace("\\", "/")
    if sha:
        out, _ = _git("show", f"{sha}:{posix}")
        if out is not None:
            return out
    out, _ = _git("show", f":0:{posix}")  # Index (staged)
    if out is not None:
        return out
    try:
        return (REPO_ROOT / posix).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def is_placeholder(value: str, line: str) -> bool:
    """Erkennt offensichtliche Platzhalter/Hashes, die keine Secrets sind."""
    low = line.lower()
    if any(tok in low for tok in ALLOW_TOKENS):
        return True
    v = value.strip()
    if re.fullmatch(r"(.)\1{19,}", v):  # "aaaa..." -> Platzhalter
        return True
    if re.fullmatch(r"[0-9a-fA-F]+", v) and len(v) % 2 == 0 and len(v) >= 32:
        return True  # langer Hex-String ist i. d. R. ein Hash
    return False


def scan_path(path: str, content: str | None,
              allow: tuple[str, ...]) -> list[str]:
    """Prueft einen Pfad (Name + Inhalt); liefert Liste der Gruende (leer=OK)."""
    posix = path.replace("\\", "/")
    if any(re.search(a, posix) for a in allow):
        return []
    reasons: list[str] = []
    for pat, label in NAME_PATTERNS:
        if not pat.search(posix):
            continue
        if label == "env-file" and ENV_EXAMPLE_RE.search(posix):
            continue
        if label not in reasons:
            reasons.append(label)
    if content is not None and "\x00" not in content[:8192]:
        for pat, label in CONTENT_PATTERNS:
            m = pat.search(content)
            if not m:
                continue
            if label == "secret-assignment" and m.lastindex:
                if is_placeholder(m.group(2), m.group(0)):
                    continue
            if label not in reasons:
                reasons.append(label)
    return reasons
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secret-/Key-Guard (fail-closed)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true",
                      help="staged Dateien pruefen (Default)")
    mode.add_argument("--tracked", action="store_true",
                      help="alle tracked Dateien pruefen (Audit)")
    mode.add_argument("--push", nargs=2, metavar=("LOCAL", "REMOTE"),
                      help="Diff LOCAL vs REMOTE pruefen (pre-push)")
    mode.add_argument("--files", nargs="+", metavar="PFAD",
                      help="explizite Datei-Liste pruefen")
    parser.add_argument("--json", action="store_true",
                        help="maschinell lesbare Ausgabe (JSON)")
    parser.add_argument("--allow", action="append", default=[],
                        metavar="REGEX", help="Pfad-Regex erlauben (wiederholbar)")
    args = parser.parse_args(argv)

    try:
        if args.push:
            files = push_files(args.push[0], args.push[1])
            sha: str | None = args.push[0]
        elif args.files:
            files = [f.replace("\\", "/") for f in args.files]
            sha = None
        elif args.tracked:
            files = tracked_files()
            sha = None
        else:
            files = staged_files()
            sha = None
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"secret_guard: FEHLER (fail-closed): {exc}", file=sys.stderr)
        return 2

    flagged: dict[str, list[str]] = {}
    for f in files:
        reasons = scan_path(f, read_blob(f, sha), tuple(args.allow))
        if reasons:
            flagged[f] = reasons

    if args.json:
        print(json.dumps({
            "ok": not flagged,
            "flagged": [{"path": k, "reasons": v} for k, v in flagged.items()],
        }, ensure_ascii=False))
    elif flagged:
        for path, reasons in flagged.items():
            print(f"  [FLAG] {path}  ({', '.join(reasons)})", file=sys.stderr)
        print(f"secret_guard: {len(flagged)} Datei(en) enthalten vermutliche "
              f"Secrets/Keys - Commit/Push blockiert.", file=sys.stderr)
    else:
        print("secret_guard: sauber (keine Secrets/Keys erkannt).", file=sys.stderr)

    # stdout-Vertrag: eine Pfad-Zeile pro geflaggter Datei.
    # Wird von autosave_snapshot.ps1 / pre-commit zum Unstage/Blocken genutzt.
    for path in flagged:
        print(path)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())