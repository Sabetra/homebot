# Workdoc: Trusted-Channel-Allowlist & Video-Pipeline (yt-dlp)

> **Erstellt:** 2026-09-07 16:15
> **Status:** IN_ARBEIT
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
| 1 | yt-dlp==2026.8.19 im venv + requirements.txt | pip show + Diff | ☐ |
| 2 | LICENSES.md frisch; check_licenses --strict grün | Skript-Ausführung | ☐ |
| 3 | dependency_vulnerability_scanner --strict grün | Skript-Ausführung | ☐ |
| 4 | trusted_channels.json ≥15 Channels, alle mit channel_id (UC…) | JSON-Inspektion | ☐ |
| 5 | verify_trusted_channels.py: Auflösung + Fail-Closed; py_compile OK | Testlauf | ☐ |
| 6 | E2E: erlaubt-Channel-Video → JSON (Metadaten, Subtitles, Frames) | Testlauf | ☐ |
| 7 | funktionen.md-Eintrag | Doku-Prüfung | ☐ |

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

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | `channel_id` (UC…) existiert im yt-dlp-Info-Dict | offen | erster extract_info-Call drucken |
| 2 | Kanal-Handle-URLs sind via yt-dlp als Playlist extrahierbar (für UC-Auflösung) | offen | extract_info auf https://www.youtube.com/@<handle> |
| 3 | Kurzgesagt/NVIDIA-Videos haben Auto-Subtitles (en) | offen | E2E-Lauf |

## Risiko & Impact-Matrix

| # | Risiko | W | Auswirkung | Minderung | Status |
|---|--------|---|------------|-----------|--------|
| 1 | Name-Spoofing (Fälschung "NVIDIA Official") | M | hoch | Allowlist schließt nur auf UC-ID (permanent) | by design |
| 2 | Prompt-Injection in Subtitles | M | mittel | Content = Daten, nie Anweisungen; Suspicion-Flag | offen |
| 3 | Supply-Chain (PyPI) | L | mittel | pinned Version, Scanner, Lizenzgate | by design |
| 4 | YouTube bricht API (PO-Token) | M | mittel | pinned Version + Update-Rhythmus dokumentieren | offen |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Subtitles = fremder Text in LLM-Kontext | Injektionsfläche | Fail-Closed-Channel-Check + Data-not-Instructions-Disziplin |
| 2 | Video-Downloads | lokale Artefakte | test_output/ (git-ignoriert), kein Repo-Content |
| 3 | Keine Account-/Login-Daten | — | anonymous extract_info nur |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | requirements.txt | +yt-dlp==2026.8.19 | ☐ |
| 2 | LICENSES.md | regeneriert | ☐ |
| 3 | config/trusted_channels.json | neu | ☐ |
| 4 | scripts/verify_trusted_channels.py | neu | ☐ |
| 5 | funktionen.md | +Abschnitt | ☐ |

## Rollback-Strategie

| Schritt | Aktion | Befehl |
|---------|--------|--------|
| 1 | yt-dlp entfernen | `pip uninstall -y yt-dlp` |
| 2 | requirements.txt + LICENSES.md restaurieren | `~\homebot_backups\video_channel_allowlist_20260907\*.bak` |
| 3 | neue Dateien löschen | config/trusted_channels.json, scripts/verify_trusted_channels.py |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | pip index versions yt-dlp | 2026.8.19 = latest | 2026-09-07 |
