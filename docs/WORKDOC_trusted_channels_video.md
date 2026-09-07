# Workdoc: Trusted-Channel-Allowlist & Video-Pipeline (yt-dlp)

> **Erstellt:** 2026-09-07 16:15
> **Status:** DONE (2026-09-07 20:05)
> **Autor:** Cline-Agent

---

## Original-Auftrag

> "gibt es solche vertrauenswürdigen Channel und können wir mit diesen
> Listen Arbeiten? Recherchiere im Internet." → Folgeauftrag nach
> Recherche: "ja" auf den 4-Schritte-Plan:
> 1. yt-dlp installieren (pinned Version, Unlicense, Scanner + Lizenzcheck)
> 2. config/trusted_channels.json mit ~15 Seed-Channels anlegen
> 3. scripts/verify_trusted_channels.py — löst UC-IDs auf, Fail-Closed-Check
> 4. End-to-End-Test: Video eines erlaubten Channels → Metadaten →
>    Subtitles → Frames → Analyse

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| yt-dlp-Installation (pinned) in Produktiv-venv | Integration in Orchestrator/RAG-Pipeline |
| Channel-Allowlist (UC-ID-basiert) + Auflösung | Audio-Transkription (Whisper) |
| Fail-Closed-Verifikations-Skript | UI/Streamlit-Anbindung |
| 1× E2E-Test mit erlaubt-Channel-Video | Automatische Video-Indexierung |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | yt-dlp==2026.8.19 im venv + requirements.txt | pip show + Diff | ✅ |
| 2 | LICENSES.md frisch; check_licenses --strict grün | Skript-Ausführung | ✅ |
| 3 | dependency_vulnerability_scanner --strict grün | Skript-Ausführung | ⚠️ abgewichen — see §Scanner-Policy (bestehende P1 in pillow u. a., nicht yt-dlp) |
| 4 | trusted_channels.json ≥15 Channels, alle mit channel_id (UC…) | JSON-Inspektion | ✅ (17 Channels) |
| 5 | verify_trusted_channels.py: Auflösung + Fail-Closed; py_compile OK | Testlauf | ✅ |
| 6 | E2E: erlaubt-Channel-Video → JSON (Metadaten, Subtitles, Frames) | Testlauf | ✅ (AI Explained, 1547 Subtitle-Zeilen, 6 Frames 480p) |
| 7 | funktionen.md-Eintrag | Doku-Prüfung | ✅ |
| 8 | Gate integriert: Pipeline lehnt vor Download/Subtitles ab (fail-closed) | E2E + Negativ-Test | ✅ (MKBHD → ABGELEHNT, Exit 2) |
| 9 | Subtitle-Sicherheits-Umhüllung (untrusted data, Flag-Heuristik, PII) | Testlauf | ✅ (0/1547 geflaggt, Envelope in JSON) |
| 10 | Dauerhafte pytest-Tests offline | pytest | ✅ (16/16, Teil der 1173er-Suite) |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | yt-dlp (Python-Lib, Unlicense, 0 Deps) | embeddable, metadata-only Modus, Channel-Felder | YouTube-Wechsel können Brüche erzwingen (Release-Zyklen) | 7 | 6 | 7 | 4 | ✅ |
| B | ffmpeg/ffprobe CLI | etabliert | nicht installiert, kein Channel-Metadaten, Shell-Out | 5 | 5 | 6 | 5 | ❌ |

> **Auswahl:** A — yt-dlp ist die einzige Option mit Channel-Identitäts-
> Metadaten (UC-ID) und ohne externe Binary; Unlicense ist AGPL-kompatibel.

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | Python 3.12.10, pip 26.2 in venv_bot_20260802 | `python --version` (2026-09-07) |
| 2 | yt-dlp 2026.8.19 = neueste stabile PyPI-Version | `pip index versions yt-dlp` (2026-09-07) |
| 3 | config/path_allowlist.json = Format-Präzedenz für Allowlists | Datei:1-7 |
| 4 | YouTube-Channel-ID (UC…) permanent, überlebt Handle-/Name-Änderung | YouTube Help (support.google.com/youtube/answer/3250431) + seocheck.tools, videonest.co (2026) |
| 5 | yt-dlp-Template-Felder uploader_id/uploader/channel; auf YT = Handle | github.com/yt-dlp/yt-dlp/issues/13568 |
| 6 | yt-dlp PyPI-Paket = Unlicense | raw.githubusercontent.com/yt-dlp/yt-dlp/master/README.md |

## Offene Hypothesen (alle aufgelöst, 2026-09-07)

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | `channel_id` (UC…) existiert im yt-dlp-Info-Dict | ✅ bestätigt | extract_info lieferte `channel_id=UC…` in Gate + E2E |
| 2 | Kanal-Handle-URLs sind via yt-dlp als Playlist extrahierbar (für UC-Auflösung) | ✅ bestätigt (mit `extract_flat=True` — ohne hängen die Voll-Enumerations-Runs) | `resolve`-Lauf: 17/17 Channels aufgelöst |
| 3 | Videos erlaubter Channels haben Auto-Subtitles (en) | ✅ bestätigt (AI Explained: automatic/en, 1547 Zeilen) | E2E-Lauf Spuza-KwTJ4 |

## Risiko & Impact-Matrix

| # | Risiko | W | Auswirkung | Minderung | Status |
|---|--------|---|------------|-----------|--------|
| 1 | Name-Spoofing (Fälschung "NVIDIA Official") | M | hoch | Allowlist schließt nur auf UC-ID (permanent) | by design |
| 2 | Prompt-Injection in Subtitles | M | mittel | `subtitles.json` trägt Sicherheits-Envelope (DATA_ONLY_NEVER_INSTRUCTIONS) + Injektions-/PII-Flag-Heuristik (flag-only, nie Block) | mitigiert (0/1547 geflaggt im E2E; Heuristik in pytest gedeckt) |
| 3 | Supply-Chain (PyPI) | L | mittel | pinned Version, Scanner, Lizenzgate | by design |
| 4 | YouTube bricht API (PO-Token) | M | mittel | pinned Version + Update-Rhythmus dokumentiert (funktionen.md §Z) | dokumentiert |
| 5 | Lookalike-Channel wird manuell in Allowlist aufgenommen | M | hoch | Nur verifizierte UC-IDs (Quellen-Nachweis im JSON); AI Explained über offizielle Website gekreuzt; keine Name-basierte Freigabe | by design |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Subtitles = fremder Text in LLM-Kontext | Injektionsfläche | Fail-Closed-Channel-Check + Data-not-Instructions-Disziplin |
| 2 | Video-Downloads | lokale Artefakte | test_output/ (git-ignoriert), kein Repo-Content |
| 3 | Keine Account-/Login-Daten | — | anonymous extract_info nur |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | requirements.txt | +yt-dlp==2026.8.19 | pip show: 2026.8.19 ✅ |
| 2 | LICENSES.md | regeneriert | check_licenses --strict: grün ✅ |
| 3 | config/trusted_channels.json | neu (17 Channels, UC-IDs mit Quellen) | check: 17/17 ✅ |
| 4 | scripts/verify_trusted_channels.py | neu (resolve/check/verify, fail-closed) | py_compile + CLI ✅ |
| 5 | scripts/ingest_video.py | neu (Gate → Metadaten → Subtitles → Frames → manifest) | E2E ✅ + 16 pytest ✅ |
| 6 | tests/test_ingest_video_safety.py | neu (offline-Sicherheits-Tests) | 16/16 PASS ✅ |
| 7 | funktionen.md | +Abschnitt Z „Video-Ingestion & Trusted-Channel-Gate“ | — |

## Rollback-Strategie

| Schritt | Aktion | Befehl |
|---------|--------|--------|
| 1 | yt-dlp entfernen | `pip uninstall -y yt-dlp` |
| 2 | requirements.txt + LICENSES.md restaurieren | `~\homebot_backups\video_channel_allowlist_20260907\*.bak` |
| 3 | neue Dateien löschen | config/trusted_channels.json, scripts/verify_trusted_channels.py, scripts/ingest_video.py, tests/test_ingest_video_safety.py |
| 4 | funktionen.md-Eintrag entfernen | Abschnitt S löschen |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | pip index versions yt-dlp | 2026.8.19 = latest | 2026-09-07 |
| 2 | check_licenses.py --strict | grün (yt-dlp Unlicense ok) | 2026-09-07 |
| 3 | verify_trusted_channels.py check | 17 Channels, alle UC-IDs gültig, Exit 0 | 2026-09-07 |
| 4 | verify … (trusted: AI Explained Spuza-KwTJ4) | ERLAUBT, UC…N1Ymd…, Exit 0 | 2026-09-07 |
| 5 | verify … (untrusted: MKBHD ANmTVYkEtLw) | ABGELEHNT, Exit 1 | 2026-09-07 |
| 6 | ingest_video.py E2E (trusted-Video) | Gate ✅ · Metadaten ✅ · 1547 Subtitle-Zeilen (automatic/en, 0 geflaggt) ✅ · 6 Frames 480p ✅ · manifest.json ✅ · Exit 0 | 2026-09-07 |
| 7 | ingest_video.py Negativ-Test (untrusted-Video) | GATE ABGELEHNT vor Download, kein Artefakt, Exit 2 | 2026-09-07 |
| 8 | tests/test_ingest_video_safety.py | 16/16 PASS | 2026-09-07 |
| 9 | tests/ (komplette Suite) | 1173 passed in 152.7s | 2026-09-07 |

## Scanner-Policy (Entscheidung 2026-09-07)

**Stand:** Scanner (non-strict) Exit 0; `--strict` Exit 1. 165 bekannte
Vulnerabilities in 54 Packages: P0=0, P1=19, P2=125, P3=21 (Enrichment:
KEV+EPSS+Reachability). P1-Kern: `pillow==12.0.0` (TGA-RLE-Heap-Leak,
PDF-Trailer-DoS, OOB-Read) — **bestehende** Abhängigkeiten; dazu P3 in
`aiohttp==3.13.2`, `tqdm==4.66.0`, `cryptography==45.0.0`.

**Wichtig:** `yt-dlp==2026.8.19` selbst ist in **keiner** der gemeldeten
Vulnerabilities enthalten — die neue Abhängigkeit ist scanner-sauber.

**Entscheidung:** Bestehendes Risiko wird hiermit **explizit akzeptiert**
(Option c), dokumentiert statt stillschweigend ignoriert. Begründung:
1. Remediation (pillow/aiohttp-Upgrade) ist ein eigenständiges,
   regressionsbehaftetes Upgrade im 24-GB-LLM-Venv — nicht in den Scope
   dieses Tasks.
2. Alle P1-Findinge betreffen Parsing-Code (TGA/PDF/McIdas), der auf dem
   Video-Ingestion-Pfad (OpenCV-Frame-Extraktion, yt-dlp-Metadaten) nicht
   angelaufen wird; EPSS-Werte niedrig (≤0.005).
3. Folge-Auftrag (Tracking): pillow-Upgrade + Re-Test der
   Bild-/Frame-Pipeline, wenn ein neues pillow-Release die CVEs deckt.

**Abweichung von DoD #3 ist damit beabsichtigt und dokumentiert.**
