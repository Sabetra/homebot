"""Temporaer: AI Explained Kanal-Verifizierung (zwei unabhaengige Quellen).

Quellen:
  A) Offizielle Patreon (AIExplained, "AI Insiders network"):
     https://www.youtube.com/channel/UCNJ1Ymd5yFuUPtn21xtRbbw
  B) FeedSpot-Top-100-Kuenstlerliste (AI Explained):
     https://www.youtube.com/@aiexplained-official

Erfolgskriterium: Beide loesen auf dieselbe UC-ID, Name ~"AI Explained",
Beschreibung verweist auf Patreon/Podcast/Simple Bench (Cross-Reference).
"""
from __future__ import annotations

import sys

URLS = {
    "A (Patreon-Link)": "https://www.youtube.com/channel/UCNJ1Ymd5yFuUPtn21xtRbbw",
    "B (FeedSpot-Handle)": "https://www.youtube.com/aiexplained-official",
}


def main() -> int:
    import yt_dlp

    results: dict[str, dict] = {}
    for label, url in URLS.items():
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noprogress": True,
            "socket_timeout": 30,
            "retries": 2,
            "extract_flat": "in_playlist",
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            info = info or {}
            r = {
                "channel_id": info.get("channel_id"),
                "channel": info.get("channel") or info.get("uploader"),
                "handle_url": info.get("channel_url") or info.get("uploader_url"),
                "follower_count": info.get("channel_follower_count"),
                "description": (info.get("description") or "")[:900],
            }
            results[label] = r
            print(f"--- {label} ---")
            for k, v in r.items():
                print(f"  {k}: {v}")
        except Exception as exc:  # noqa: BLE001
            print(f"--- {label} ---")
            print(f"  FEHLER {type(exc).__name__}: {exc}")
            del results[label]
    ids = {r.get("channel_id") for r in results.values() if r.get("channel_id")}
    if len(ids) == 1:
        print("==> IDENTISCHE UC-ID:", next(iter(ids)))
        return 0
    print("!!! ERGEBNISSE UNVOLLSTAENDIG/ABWEICHEND:", ids)
    return 1


if __name__ == "__main__":
    sys.exit(main())
