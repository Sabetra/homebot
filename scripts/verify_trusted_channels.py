"""
Trusted-Channel-Allowlist: Auflösung & Fail-Closed-Verifikation
================================================================

Verwaltet ``config/trusted_channels.json`` (YouTube-Channel-Allowlist für die
lokale Video-Pipeline). Der stabile Schlüssel ist die **permanente
Channel-ID (UC…)** — Channel-Namen und Handles sind vom Owner änderbar und
deshalb kein zulässiger Allowlist-Schlüssel (Spoofing-Risiko).

Usage:
    python scripts/verify_trusted_channels.py resolve        # fehlende UC-IDs auflösen & speichern
    python scripts/verify_trusted_channels.py check          # Integrität der Allowlist prüfen
    python scripts/verify_trusted_channels.py verify <URL>   # Video-URL gegen Allowlist prüfen

Sicherheit:
    - Fail-closed: bei Fehler oder unbekanntem channel_id => NICHT erlaubt.
    - Nur Metadaten-Abfragen (kein Video-Download in diesem Skript).
    - 100 % lokal: yt-dlp (Unlicense) als Library, keine Cloud-LLM-Calls.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "trusted_channels.json"
CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


def _ydl_opts() -> dict:
    """Sichere yt-dlp-Optionen: still, ohne Download, mit Timeout."""
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 2,
    }


def _extract_info(url: str) -> dict:
    """Extrahiert Metadaten für eine YouTube-URL (Video ODER Channel)."""
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


def load_config() -> dict:
    """Lädt die Channel-Allowlist (Schema: _meta + channels[].channel_id)."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    """Schreibt die Allowlist zurück (stabile Formatierung, UTF-8)."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def allowed_channel_ids(cfg: dict) -> set[str]:
    """Menge aller aufgelösten, gültigen Channel-IDs."""
    return {
        c["channel_id"]
        for c in cfg.get("channels", [])
        if c.get("channel_id") and CHANNEL_ID_RE.match(c["channel_id"])
    }


def resolve_missing() -> int:
    """Löst fehlende channel_id-Werte über die Channel-URL auf und speichert sie.

    Liefert die Anzahl fehlgeschlagener Auflösungen (0 = alle ok).
    """
    cfg = load_config()
    failed = 0
    for entry in cfg.get("channels", []):
        if entry.get("channel_id"):
            continue
        handle = entry.get("handle", "")
        if not handle:
            print(f"  ✗ {entry['name']}: kein Handle — kann nicht aufgelöst werden")
            failed += 1
            continue
        url = f"https://www.youtube.com/{handle.lstrip('@')}"
        try:
            info = _extract_info(url)
            cid = info.get("channel_id") or ""
            if not CHANNEL_ID_RE.match(cid):
                print(f"  ✗ {entry['name']}: keine gültige Channel-ID (got: {cid!r})")
                failed += 1
                continue
            entry["channel_id"] = cid
            entry["resolved_channel_name"] = info.get("channel", entry["name"])
            entry["follower_count"] = info.get("channel_follower_count")
            print(f"  ✓ {entry['name']}: {cid} ({info.get('channel', '?')})")
        except Exception as e:  # noqa: BLE001 - Fehler pro Channel isolieren
            print(f"  ✗ {entry['name']}: {type(e).__name__}: {e}")
            failed += 1
    if failed == 0:
        cfg["_meta"]["updated_at"] = "2026-09-07"
        save_config(cfg)
        print("  Alle Channel-IDs aufgelöst, Allowlist gespeichert.")
    else:
        print(f"  {failed} Auflösung(en) fehlgeschlagen — Allowlist NICHT gespeichert.")
    return failed


def check_config() -> bool:
    """Prüft die Integrität der Allowlist (alle IDs vorhanden & gültig)."""
    cfg = load_config()
    channels = cfg.get("channels", [])
    ok = True
    for entry in channels:
        cid = entry.get("channel_id")
        if not cid:
            print(f"  ✗ {entry['name']}: channel_id fehlt (resolve ausführen?)")
            ok = False
            continue
        if not CHANNEL_ID_RE.match(cid):
            print(f"  ✗ {entry['name']}: ungültiges Channel-ID-Format: {cid!r}")
            ok = False
            continue
        print(f"  ✓ {entry['name']}: {cid}")
    if ok:
        print(f"  Allowlist OK: {len(channels)} Channels, alle mit gültiger UC-ID.")
    return ok


def verify_video(url: str) -> tuple[bool, str, str]:
    """Prüft fail-closed, ob das Video einer erlaubten Channel-ID stammt.

    Liefert (erlaubt, channel_id, channel_name). Bei jedem Fehler: (False, …).
    """
    cfg = load_config()
    allowed = allowed_channel_ids(cfg)
    if not allowed:
        return False, "", "Allowlist enthält keine aufgelösten Channel-IDs"
    try:
        info = _extract_info(url)
        cid = info.get("channel_id") or ""
        if not cid and info.get("entries"):  # Playlist-Wrapper
            first = next((e for e in info["entries"] if isinstance(e, dict)), {})
            cid = first.get("channel_id") or ""
        name = info.get("channel") or info.get("uploader") or ""
        return (cid in allowed), cid, name
    except Exception as e:  # noqa: BLE001 - Fail-Closed bei jedem Fehler
        return False, "", f"{type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "resolve":
        return 0 if resolve_missing() == 0 else 1
    if cmd == "check":
        return 0 if check_config() else 1
    if cmd == "verify" and len(argv) == 2:
        allowed, cid, name = verify_video(argv[1])
        verdict = "ERLAUBT" if allowed else "ABGELEHNT"
        print(f"  {verdict}: channel_id={cid or '—'} channel={name or '—'}")
        return 0 if allowed else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
