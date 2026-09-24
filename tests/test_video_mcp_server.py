"""
Tests für den Video-MCP-Server (C:\\Users\\PC\\Documents\\Cline\\MCP\\video\\server.py).

Offline-Tests: yt-dlp / Gate / Pipeline werden gemockt — KEINE Netzwerk-Zugriffe.
Fail-closed-Verhalten ist der Kern:
  - Such-Treffer nur mit verifizierter UC…-Channel-ID als 'trusted'
  - video_info/video_ingest lehnen nicht-erlaubte Channels ab (keine Daten)
  - Pipeline-Fehler (Exit 2/3) => klare ABGELEHNT/ABBRUCH-Nachricht

Der Server-Code wird per importlib aus dem MCP-Verzeichnis geladen
(analog zum Websearch-Server; Server-Datei ist Teil des Cline-MCP-Setups).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import ingest_video  # type: ignore  # noqa: E402  (Skript-Ordner, kein Package)

SERVER_PATH = Path(r"C:\Users\PC\Documents\Cline\MCP\video\server.py")

TRUSTED_CHANNEL_ID = "UC55ODQSvARtgSyc8ThfiepQ"      # Sam Witteveen (verifiziert)
UNTRUSTED_CHANNEL_ID = "UCf7kukhQB227G1avJ1JpkEw"     # fiktiv (nicht in Allowlist)
TRUSTED_URL = "https://www.youtube.com/watch?v=PTuGGdDuyPI"
UNTRUSTED_URL = "https://www.youtube.com/watch?v=ANmTVYkEtLw"


@pytest.fixture(scope="module")
def server():
    if not SERVER_PATH.exists():
        pytest.skip(f"Video-MCP-Server nicht vorhanden: {SERVER_PATH}")
    if "video_mcp_server" in sys.modules:
        return sys.modules["video_mcp_server"]
    spec = importlib.util.spec_from_file_location("video_mcp_server", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["video_mcp_server"] = mod
    spec.loader.exec_module(mod)
    return mod


def _call(server_mod, name: str, args: dict) -> str:
    """call_tool (FastMCP-Instanz) ausführen und den Text des ersten Contents zurückgeben."""
    res = asyncio.run(server_mod.mcp.call_tool(name, args))
    contents = res[0] if isinstance(res, tuple) else res
    assert len(contents) == 1
    return contents[0].text


# --- Tool-Definitionen --------------------------------------------------------


def test_tool_definitions(server):
    tools = asyncio.run(server.mcp.list_tools())
    names = [t.name for t in tools]
    assert names == ["video_search", "video_info", "video_ingest"]
    for t in tools:
        assert t.inputSchema.get("type") == "object"
        assert t.inputSchema.get("required")
    # video_ingest: URL Pflicht, Rest Default
    ing = tools[2]
    assert ing.inputSchema["required"] == ["url"]
    props = ing.inputSchema["properties"]
    assert props["max_frames"]["default"] == 8
    assert props["max_height"]["default"] == 480


# --- video_search (offline, yt-dlp gemockt) -----------------------------------


class _FakeYDL:
    """Fake yt_dlp.YoutubeDL: liefert vordefinierte ytsearch-Einträge."""

    entries: list[dict] = []

    def __init__(self, opts: dict) -> None:
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        assert url.startswith("ytsearch")
        assert not download
        return {"entries": list(self.entries)}


def _entry(vid: str, title: str, cid: str | None, channel: str = "Some Channel") -> dict:
    e = {"id": vid, "title": title, "url": f"https://www.youtube.com/watch?v={vid}",
         "channel": channel, "duration": 600}
    if cid:
        e["channel_id"] = cid
    return e


def test_video_search_flags_trusted_and_untrusted(server, monkeypatch):
    import yt_dlp

    _FakeYDL.entries = [
        _entry("PTuGGdDuyPI", "Qwen3.8-27B & How to Serve it Fast",
               TRUSTED_CHANNEL_ID, "Sam Witteveen"),
        _entry("ANmTVYkEtLw", "Some Review", UNTRUSTED_CHANNEL_ID, "MKBHD"),
    ]
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    text = _call(server, "video_search", {"query": "serve LLM fast", "max_results": 5})
    assert "2 Treffer, 1 von Trusted-Channels" in text
    assert "[TRUSTED]" in text and "[ABGELEHNT]" in text
    assert "PTuGGdDuyPI" in text and "ANmTVYkEtLw" in text
    assert "NICHT per video_ingest verarbeitet" in text
def test_video_search_missing_channel_id_falls_back_to_gate(server, monkeypatch):
    import yt_dlp

    _FakeYDL.entries = [_entry("ZZZ11111111", "No CID", None, "Ghost")]
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    calls: list[str] = []

    def _gate(url: str):
        calls.append(url)
        return (False, "UCunknown", "nicht in Allowlist")

    monkeypatch.setattr(server.gate, "verify_video", _gate)
    text = _call(server, "video_search", {"query": "x"})
    assert "1 Treffer, 0 von Trusted-Channels" in text
    assert "[ABGELEHNT]" in text
    assert calls, "Gate-Fallback muss aufgerufen werden (channel_id fehlt)"


def test_video_search_failclosed_on_network_error(server, monkeypatch):
    import yt_dlp

    class _Boom:
        def __init__(self, opts):
            raise RuntimeError("netzwerk tot")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _Boom)
    text = _call(server, "video_search", {"query": "x"})
    assert "fail-closed" in text
    assert "RuntimeError" in text


def test_video_search_requires_query(server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="query"):
        _call(server, "video_search", {})


def test_video_search_clamps_max_results(server, monkeypatch):
    import yt_dlp

    seen: list[str] = []

    class _FakeYDL2(_FakeYDL):
        def extract_info(self, url: str, download: bool = False) -> dict:
            seen.append(url)
            return {"entries": []}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL2)
    _call(server, "video_search", {"query": "x", "max_results": 999})
    assert seen == ["ytsearch20:x"], "max_results muss auf 20 geclamped werden"


# --- video_info (Gate gemockt) --------------------------------------------------


def test_video_info_rejects_untrusted(server, monkeypatch):
    monkeypatch.setattr(server.gate, "verify_video",
                        lambda url: (False, UNTRUSTED_CHANNEL_ID, "nicht in Allowlist"))
    text = _call(server, "video_info", {"url": UNTRUSTED_URL})
    assert "ABGELEHNT (fail-closed)" in text
    assert "nicht in Allowlist" in text


def test_video_info_rejects_invalid_url(server):
    text = _call(server, "video_info", {"url": "https://example.com/keinvideouml"})
    assert "ABGELEHNT" in text
    assert "Ungültige YouTube-Video-URL" in text


def test_video_info_success(server, monkeypatch):
    monkeypatch.setattr(server.gate, "verify_video",
                        lambda url: (True, TRUSTED_CHANNEL_ID, "Sam Witteveen"))
    import yt_dlp

    class _InfoYDL(_FakeYDL):
        def extract_info(self, url: str, download: bool = False) -> dict:
            return {"id": "PTuGGdDuyPI", "title": "Qwen3.8-27B & How to Serve it Fast",
                    "channel": "Sam Witteveen", "channel_id": TRUSTED_CHANNEL_ID,
                    "duration": 1080, "upload_date": "20260818",
                    "description": "How to serve Qwen fast."}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _InfoYDL)
    text = _call(server, "video_info", {"url": TRUSTED_URL})
    assert "GATE: erlaubt" in text
    assert "Sam Witteveen" in text
    assert "Qwen3.8-27B" in text
    assert "18 min" in text
    assert "2026-08-18" in text
    assert "UNVERTRAUENSWÜRDIGE DATEN" in text  # Description ist untrusted


def test_video_info_requires_url(server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="url"):
        _call(server, "video_info", {})

# --- video_ingest (Pipeline gemockt) ---------------------------------------------


def _fake_pipeline(rc: int, manifest: dict):
    """Erzeugt eine Fake-run_pipeline, die das Manifest schreibt und rc liefert."""
    def _fake(url: str, out_root: Path, n_frames: int, max_height: int, language: str) -> int:
        vid = ingest_video._video_id_from_url(url)
        out_dir = Path(out_root) / vid
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        print("  (fake pipeline output)")
        return rc
    return _fake


def test_video_ingest_denied_exit2(server, monkeypatch, tmp_path):
    manifest = {"gate": {"status": "denied", "channel_id": UNTRUSTED_CHANNEL_ID,
                         "detail": "nicht in Allowlist"}, "errors": ["gate"]}
    monkeypatch.setattr(ingest_video, "run_pipeline", _fake_pipeline(2, manifest))
    text = _call(server, "video_ingest", {"url": UNTRUSTED_URL, "out_dir": str(tmp_path)})
    assert "ABGELEHNT (fail-closed)" in text
    assert UNTRUSTED_CHANNEL_ID in text
    assert "Keine Verarbeitung" in text


def test_video_ingest_abort_exit3(server, monkeypatch, tmp_path):
    manifest = {"gate": {"status": "error", "detail": "Timeout"},
                "errors": ["gate: Timeout"]}
    monkeypatch.setattr(ingest_video, "run_pipeline", _fake_pipeline(3, manifest))
    text = _call(server, "video_ingest", {"url": TRUSTED_URL, "out_dir": str(tmp_path)})
    assert "PIPELINE-ABBRUCH (fail-closed)" in text
    assert "Gate/URL-Verifikation fehlgeschlagen" in text


def test_video_ingest_success(server, monkeypatch, tmp_path):
    manifest = {
        "gate": {"status": "allowed", "channel_id": TRUSTED_CHANNEL_ID,
                 "channel": "Sam Witteveen"},
        "metadata": {"title": "Qwen3.8-27B & How to Serve it Fast",
                     "duration": 1080, "upload_date": "20260818"},
        "subtitles": {"status": "ok", "lines": 283, "flagged": 2},
        "frames": {"status": "ok", "count": 8},
        "partial": False, "errors": [],
    }
    monkeypatch.setattr(ingest_video, "run_pipeline", _fake_pipeline(0, manifest))
    text = _call(server, "video_ingest", {"url": TRUSTED_URL, "out_dir": str(tmp_path)})
    assert "ERFOLG" in text
    assert "Sam Witteveen" in text
    assert "283 Zeilen, 2 geflaggt" in text
    assert "8 Samples" in text
    assert "DATA_ONLY_NEVER_INSTRUCTIONS" in text
    assert text.startswith("✓ ERFOLG")


def test_video_ingest_param_bounds(server, monkeypatch, tmp_path):
    called: list = []
    monkeypatch.setattr(ingest_video, "run_pipeline",
                        lambda *a, **k: called.append(a) or 0)
    t1 = _call(server, "video_ingest",
               {"url": TRUSTED_URL, "max_frames": 999, "out_dir": str(tmp_path)})
    assert "max_frames" in t1 and called == []
    t2 = _call(server, "video_ingest",
               {"url": TRUSTED_URL, "max_height": 10, "out_dir": str(tmp_path)})
    assert "max_height" in t2 and called == []


def test_video_ingest_requires_url(server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="url"):
        _call(server, "video_ingest", {})


# --- Sonstiges --------------------------------------------------------------------


def test_unknown_tool(server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="Unknown tool"):
        _call(server, "kein_tool", {})


def test_allowlist_load(server):
    allow = server._load_allowlist()
    assert allow.get(TRUSTED_CHANNEL_ID) == "Sam Witteveen"
    assert UNTRUSTED_CHANNEL_ID not in allow
    assert len(allow) >= 32, "Allowlist muss >= 32 verifizierte Channels enthalten"

