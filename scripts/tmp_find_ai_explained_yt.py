"""Temporaer: YouTube-Channel-URLs in offiziellen AI Explained Quellen finden."""
import re
import sys

import requests

URLS = [
    "https://aiexplainedopodcast.buzzsprout.com/2418777",
    "https://aiexplainedopodcast.buzzsprout.com/2418777/episodes/19753955-gpt-6-astra-so-good-even-openai-are-worried",
    "https://www.patreon.com/AIExplained",
    "https://videos.feedspot.com/ai_youtube_channels/",
]

PAT = re.compile(r"youtube\.com/(?:@|channel/)[A-Za-z0-9_\-\./]+")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def main() -> int:
    for url in URLS:
        try:
            resp = requests.get(url, timeout=20, headers=HEADERS)
        except requests.RequestException as exc:
            print(f"{url} -> ERROR {exc}")
            continue
        found = sorted(set(PAT.findall(resp.text)))
        print(f"{url} -> HTTP {resp.status_code}")
        if found:
            for f in found:
                print(f"   {f}")
        else:
            print("   KEIN YOUTUBE-LINK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
