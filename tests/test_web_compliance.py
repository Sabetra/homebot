"""
Tests für utils/web_compliance.py — Web-Compliance & Retention (2026-08-30).

Abgedeckt:
* RobotsChecker — Allow/Disallow, UA-spezifische Rules, per-Domain-Cache,
  TTL-Expiry, Negativ-Cache, Fail-Open bei Fetch-Fehlern, Thread-Sicherheit.
* check_response_headers — X-Robots-Tag / Googlebot / Cache-Control-Matrix.
* check_html_meta — meta robots (name=robots/googlebot).
* decide — kombinierte Entscheidung (robots → headers → meta).
* gate_persistence — Block + Warning-Log, deaktivierter Modus, leere URL.
* Retention-Helfer — WEB_RETENTION_DAYS, retention_until_iso().

Die Netzwerk-Schicht wird vollständig simuliert (eigener fetcher),
es werden in diesen Tests keine echten robots.txt-Downloads ausgelöst.
Siehe docs/18_LEGAL_WEB_PERSIST.md.
"""

import os
import sys
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.web_compliance as wc
from utils.web_compliance import (
    ComplianceDecision,
    RobotsChecker,
    check_html_meta,
    check_response_headers,
    decide,
)


# ── Test-Hilfen ──────────────────────────────────────────────────────────────

def make_checker(robots_body: str = "", fetch_error: Exception | None = None) -> RobotsChecker:
    """RobotsChecker mit simuliertem Netzwerk (kein echter Fetch)."""
    def fetcher(url: str) -> str:
        if fetch_error is not None:
            raise fetch_error
        return robots_body
    return RobotsChecker(user_agent="homebot-test", ttl_seconds=3600.0, fetcher=fetcher)


def allowed_url(path: str = "/page") -> str:
    return f"http://allowed.example{path}"


def blocked_url(path: str = "/private/page") -> str:
    return f"http://blocked.example{path}"


# ── RobotsChecker: Basis-Verhalten ──────────────────────────────────────────

class TestRobotsChecker:
    def test_default_allow_without_robots(self):
        c = make_checker("")
        assert c.is_allowed("http://site.example/page")[0] is True

    def test_disallow_blocks_path_and_subpaths(self):
        c = make_checker("Disallow: /private")
        assert c.is_allowed("http://site.example/private/page")[0] is False
        assert c.is_allowed("http://site.example/private")[0] is False
        # Öffentliche Seite derselben Domain bleibt erlaubt
        assert c.is_allowed("http://site.example/public")[0] is True

    def test_directive_without_user_agent_still_applies(self):
        # CPython-Quirk: urllib.robotparser wirft Direktiven vor dem ersten
        # User-agent-Block still weg. _normalize_robots_text stellt sicher,
        # dass ein „bares" Disallow (häufig bei kleinen Sites) wirkt.
        c = make_checker("Disallow: /private\nDisallow: /admin")
        assert c.is_allowed("http://site.example/private")[0] is False
        assert c.is_allowed("http://site.example/admin/x")[0] is False
        assert c.is_allowed("http://site.example/public")[0] is True

    def test_allowlist_entry_wins(self):
        body = "Disallow: /\nAllow: /public"
        c = make_checker(body)
        assert c.is_allowed("http://site.example/private")[0] is False
        assert c.is_allowed("http://site.example/public/page")[0] is True

    def test_user_agent_specific_rules(self):
        body = (
            "User-agent: googlebot\n"
            "Disallow: /\n"
            "User-agent: homebot\n"
            "Disallow: /secret\n"
        )
        c = RobotsChecker(user_agent="homebot", ttl_seconds=3600.0,
                          fetcher=lambda url: body)
        # Googlebot-Regeln gelten NICHT für uns, nur die homebot-Regeln
        assert c.is_allowed("http://site.example/other")[0] is True
        assert c.is_allowed("http://site.example/secret")[0] is False

    def test_wildcard_user_agent_applies(self):
        c = make_checker("User-agent: *\nDisallow: /wild")
        assert c.is_allowed("http://site.example/wild")[0] is False
        assert c.is_allowed("http://site.example/ok")[0] is True

    def test_crlf_and_whitespace_tolerated(self):
        c = make_checker("User-agent: * \r\n Disallow: /crlf \r\n")
        assert c.is_allowed("http://site.example/crlf")[0] is False

    def test_comment_lines_ignored(self):
        c = make_checker("# Kommentar\nUser-agent: *\nDisallow: /real\n# Disallow: /fake")
        assert c.is_allowed("http://site.example/real")[0] is False
        assert c.is_allowed("http://site.example/fake")[0] is True


    def test_non_http_url_allowed_without_fetch(self):
        fetched = []
        c = RobotsChecker(fetcher=lambda url: (fetched.append(url), "")[1])
        assert c.is_allowed("file:///C:/local/doc.pdf")[0] is True
        assert fetched == []  # kein Netzwerkzugriff für Nicht-HTTP-URLs


# ── RobotsChecker: Cache, TTL, Fail-Open, Threads ───────────────────────────

class TestRobotsCheckerCache:
    def test_per_domain_caching(self):
        calls = []
        c = RobotsChecker(ttl_seconds=3600.0,
                          fetcher=lambda url: (calls.append(url), "")[1])
        c.is_allowed("http://a.example/x")
        c.is_allowed("http://a.example/y")   # Cache-Treffer
        c.is_allowed("http://b.example/x")   # neue Domain → zweiter Fetch
        assert len(calls) == 2

    def test_ttl_expiry_triggers_refetch(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            return "Disallow: /private"
        c = RobotsChecker(ttl_seconds=0.0, fetcher=fetcher)  # TTL=0 → sofort abgelaufen
        assert c.is_allowed("http://site.example/private")[0] is False
        assert len(calls) == 1
        assert c.is_allowed("http://site.example/private")[0] is False
        assert len(calls) == 2  # erneuter Fetch nach TTL-Expiry

    def test_fail_open_on_fetch_error(self):
        c = make_checker("", fetch_error=ConnectionError("offline"))
        allowed, reason = c.is_allowed("http://site.example/page")
        assert allowed is True
        assert reason == "fail_open"

    def test_negative_cache_avoids_hammering(self):
        calls = []
        def fetcher(url):
            calls.append(url)
            raise ConnectionError("offline")
        c = RobotsChecker(ttl_seconds=3600.0, fetcher=fetcher)
        allowed, reason = c.is_allowed("http://site.example/page")
        assert (allowed, reason) == (True, "fail_open")
        assert len(calls) == 1
        # Negativ-Cache (60 s): zweiter Call aus dem Cache, kein zweiter Fetch
        allowed2, reason2 = c.is_allowed("http://site.example/page")
        assert (allowed2, reason2) == (True, "fail_open")
        assert len(calls) == 1

    def test_thread_safety_fail_open(self):
        def fetcher(url):
            time.sleep(0.001)
            raise ConnectionError("offline")
        c = RobotsChecker(ttl_seconds=0.0, fetcher=fetcher)
        errors: list = []

        def worker():
            try:
                for _ in range(25):
                    allowed, _ = c.is_allowed("http://site.example/page")
                    assert allowed is True
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ── Response-Header-Prüfung ─────────────────────────────────────────────────

class TestCheckResponseHeaders:
    def test_none_headers_allowed(self):
        assert check_response_headers(None).allowed is True

    def test_empty_headers_allowed(self):
        assert check_response_headers({}).allowed is True

    def test_x_robots_tag_noindex_blocks(self):
        d = check_response_headers({"X-Robots-Tag": "noindex"})
        assert d.allowed is False
        assert "noindex" in d.detail()

    def test_directive_case_insensitive(self):
        d = check_response_headers({"x-robots-tag": "NOARCHIVE"})
        assert d.allowed is False
        assert "noarchive" in d.detail()

    def test_googlebot_header_blocks(self):
        d = check_response_headers({"Googlebot": "nofollow"})
        assert d.allowed is False
        assert "nofollow" in d.detail()

    def test_index_follow_allowed(self):
        assert check_response_headers({"X-Robots-Tag": "index,follow"}).allowed is True

    def test_cache_control_no_store_blocks(self):
        d = check_response_headers({"Cache-Control": "no-store"})
        assert d.allowed is False
        assert "no-store" in d.detail()

    def test_cache_control_max_age_allowed(self):
        assert check_response_headers({"Cache-Control": "max-age=60"}).allowed is True

    def test_pragma_no_store_blocks(self):
        d = check_response_headers({"Pragma": "no-store"})
        assert d.allowed is False
        assert "no-store" in d.detail()



# ── HTML-Meta-Prüfung ────────────────────────────────────────────────────────

class TestCheckHtmlMeta:
    def test_empty_html_allowed(self):
        assert check_html_meta("").allowed is True
        assert check_html_meta(None).allowed is True

    def test_no_robots_meta_allowed(self):
        assert check_html_meta("<html><head><title>OK</title></head></html>").allowed is True

    def test_meta_robots_noindex_blocks(self):
        html = '<html><head><meta name="robots" content="noindex"></head></html>'
        d = check_html_meta(html)
        assert d.allowed is False
        assert "noindex" in d.detail()

    def test_meta_googlebot_blocks(self):
        html = '<meta name="googlebot" content="noarchive">'
        d = check_html_meta(html)
        assert d.allowed is False
        assert "noarchive" in d.detail()

    def test_meta_case_insensitive(self):
        html = '<META NAME="ROBOTS" CONTENT="NOINDEX">'
        assert check_html_meta(html).allowed is False

    def test_meta_index_follow_allowed(self):
        html = '<meta name="robots" content="index,follow">'
        assert check_html_meta(html).allowed is True


# ── Zusammengesetzte decide() ────────────────────────────────────────────────

class TestDecide:
    def test_all_clear_allows(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_COMPLIANCE_ENABLED, raising=False)
        decision = decide(
            "http://site.example/page",
            headers={"Content-Type": "text/html"},
            html="<html></html>",
            robots_checker=make_checker(""),
        )
        assert decision.allowed is True
        assert decision.reasons == ()

    def test_robots_disallow_blocks(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_COMPLIANCE_ENABLED, raising=False)
        decision = decide(
            "http://site.example/private/page",
            robots_checker=make_checker("Disallow: /private"),
        )
        assert decision.allowed is False
        assert "robots.txt" in decision.detail()

    def test_header_block_when_robots_allow(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_COMPLIANCE_ENABLED, raising=False)
        decision = decide(
            "http://site.example/page",
            headers={"X-Robots-Tag": "noindex"},
            robots_checker=make_checker(""),
        )
        assert decision.allowed is False
        assert "noindex" in decision.detail()

    def test_meta_block_when_robots_allow(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_COMPLIANCE_ENABLED, raising=False)
        decision = decide(
            "http://site.example/page",
            html='<meta name="robots" content="noindex">',
            robots_checker=make_checker(""),
        )
        assert decision.allowed is False
        assert "meta[robots]" in decision.detail()

    def test_multiple_reasons_aggregated(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_COMPLIANCE_ENABLED, raising=False)
        decision = decide(
            "http://site.example/private/page",
            headers={"X-Robots-Tag": "noindex"},
            robots_checker=make_checker("Disallow: /private"),
        )
        assert decision.allowed is False
        assert "robots.txt" in decision.detail()
        assert "noindex" in decision.detail()

    def test_disabled_allows_with_reason(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_COMPLIANCE_ENABLED, "0")
        decision = decide(
            "http://site.example/private",
            robots_checker=make_checker("Disallow: /"),
        )
        assert decision.allowed is True
        assert "disabled" in decision.reasons[0]



# ── gate_persistence (Callsite-Gate) ─────────────────────────────────────────

class TestGatePersistence:
    def test_blocked_returns_false_and_logs_warning(self, monkeypatch, caplog):
        checker = make_checker("Disallow: /private")
        monkeypatch.setattr(wc, "default_robots_checker", lambda: checker)
        with caplog.at_level("WARNING", logger="utils.web_compliance"):
            ok = wc.gate_persistence("upsert_url", "http://site.example/private/page")
        assert ok is False
        assert any("BLOCKED persistence" in rec.message for rec in caplog.records)

    def test_allowed_returns_true(self, monkeypatch):
        monkeypatch.setattr(wc, "default_robots_checker", lambda: make_checker(""))
        assert wc.gate_persistence("upsert_url", "http://site.example/page") is True

    def test_empty_url_skips_gate(self):
        assert wc.gate_persistence("upsert_url", "") is True

    def test_disabled_mode_allows(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_COMPLIANCE_ENABLED, "0")
        monkeypatch.setattr(wc, "default_robots_checker", lambda: make_checker("Disallow: /"))
        assert wc.gate_persistence("upsert_url", "http://site.example/x") is True


# ── Retention-Helfer ─────────────────────────────────────────────────────────

class TestRetentionHelpers:
    def test_default_30_days(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_RETENTION_DAYS, raising=False)
        assert wc.get_retention_days() == 30

    def test_custom_days(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_RETENTION_DAYS, "45")
        assert wc.get_retention_days() == 45

    def test_zero_means_unlimited(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_RETENTION_DAYS, "0")
        assert wc.get_retention_days() == 0

    def test_negative_means_unlimited(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_RETENTION_DAYS, "-5")
        assert wc.get_retention_days() is None

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_RETENTION_DAYS, "abc")
        assert wc.get_retention_days() == 30

    def test_retention_until_iso_default_window(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_RETENTION_DAYS, raising=False)
        ts = wc.retention_until_iso()
        assert ts is not None
        parsed = datetime.fromisoformat(ts)
        lo = datetime.now(timezone.utc) + timedelta(days=29, hours=23)
        hi = datetime.now(timezone.utc) + timedelta(days=31)
        assert lo < parsed < hi

    def test_retention_until_iso_unlimited(self, monkeypatch):
        monkeypatch.setenv(wc.ENV_RETENTION_DAYS, "0")
        assert wc.retention_until_iso() is None

    def test_is_compliance_enabled_switches(self, monkeypatch):
        monkeypatch.delenv(wc.ENV_COMPLIANCE_ENABLED, raising=False)
        assert wc.is_compliance_enabled() is True
        monkeypatch.setenv(wc.ENV_COMPLIANCE_ENABLED, "0")
        assert wc.is_compliance_enabled() is False
        monkeypatch.setenv(wc.ENV_COMPLIANCE_ENABLED, "off")
        assert wc.is_compliance_enabled() is False

