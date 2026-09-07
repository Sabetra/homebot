"""Temporaer: Erste Video-URL je Channel holen (Gate-Tests Trusted/Untrusted)."""
from __future__ import annotations

import sys


def first_video_url(channel_url: str) -> str | None:
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 1,
        "extract_flat": "in_playlist",
        "playlist_items": "1:1",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False) or {}
    # Ebene 1: direkte Video-Einträge; Ebene 2: Playlist-/Channel-Wrapper
    candidates = [info] + [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    for c in candidates:
        for e in c.get("entries") or []:
            if isinstance(e, dict) and "watch?v=" in str(e.get("url", "")):
                return e["url"]
    return None


def main() -> int:
    targets = {
        "TRUSTED (AI Explained)": "https://www.youtube.com/channel/UCNJ1Ymd5yFuUPtn21xtRbbw",
        "TRUSTED (Kurzgesagt)": "https://www.youtube.com/Kurzgesagt",
        "UNTRUSTED (mkbhd)": "https://www.youtube.com/mkbhd",
    }
    for label, url in targets.items():
        try:
            v = first_video_url(url)
            print(f"{label}: {v}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: FEHLER {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
