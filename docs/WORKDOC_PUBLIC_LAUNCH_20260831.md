# Workdoc: Public GitHub Launch (v1.0.0)

> **Erstellt:** 2026-08-31 08:15
> **Abschluss-Ziel:** 2026-08-31 (Code-Seite); Screenshots + Push = User-Seite
> **Status:** CODE_SEITE_ABGESCHLOSSEN / STAGE_7_USER_SEITE_OFFEN (Stand 2026-09-01)
> **Autor:** Cline (Agent)
> **Reviewer:** Sabetra

---

## Original-Auftrag

"Prepare the local-first Streamlit LLM chatbot repository for a public GitHub launch
with a polished README, demo screenshots, and community launch posts." (Session-Kontext
vom 2026-08-30/31, Plan-Modus)

Nachtrag (2026-08-31, Act-Modus): "Leg Dir ein Workdoc dazu an, damit Du eine
durchgehende Doku hast, wie in AGENTS.md gefordert. Aktualisiere das Workdoc
regelmässig. Setze alles um, was Du alleine umsetzen kannst. Schreib mir ganz am
Ende eine Beschreibung wegen der Stufe 5 (Screenshots)."

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Repo-Hygiene (gitignore, untrack sensibler Dateien, Examples) | Screenshot-Aufnahmen (Stage 5, User-Seite) |
| Visible Disclaimers Finance + Psychologie (DE/EN/BG) | Push auf GitHub / Remote-Setup (User-Seite) |
| Portable Pfade (Code, Skripte, Doku, Hooks) | Live-LLM-GIF (braucht VRAM + LM-Studio-Down) |
| README-Landing-Page (EN-first) | Feature-Entwicklung / Architekturänderungen |
| CHANGELOG, Hygiene-Checker, Marketing-Post | CI/GitHub Actions (bewusst NICHT — D2-Entscheidung 2026-08-31) |
| Tests + deterministisches Gate grün | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Keine maschinen-spezifischen/lokalen Dateien mehr getrackt | `git ls-files monitoring/ .continue/ settings.json data/` (leer) | ☑ |
| 2 | Disclaimers sichtbar in Finance- + Psychologie-Tab, alle 3 Locales | `tests/test_compliance_disclaimers.py` (8 passed) | ☑ |
| 3 | Keine hartkodierten `C:\Users\...`-Pfade in Code/Skripts | `git grep -nE 'C:\\\\Users' -- '*.py' '*.ps1' '.githooks'` (leer; Doku-Anteil: Sweep 2026-08-31 11:06) | ☑ |
| 4 | README ist EN-first Landing Page ohne interne Pfade | Inspektion (Rebuild 2026-08-31, Commit 1b63f955) | ☑ |
| 5 | CHANGELOG + Hygiene-Checker existieren; **CI bewusst NICHT** (D2-Entscheidung 2026-08-31: Code-Publikation benötigt kein Remote-Runtime; lokales Gate + Pre-commit-Hook genügen, Local-First-Konsistenz) | Datei-Check + `scripts/check_release_hygiene.py` grün (0 FAIL / 0 WARN) | ☑ |
| 6 | Marketing-Posts (r/LocalLLaMA, HN) entworfen | `marketing/*.md` | ☐ (Stage 6/9) |
| 7 | Deterministisches Release-Gate grün (pytest) | `scripts/run_pytest_venv.ps1 tests/ -q` → 932 passed | ☑ |
| 8 | Lizenz-Gate grün | `python scripts/check_licenses.py --strict` → OK | ☑ |
| 9 | Stage-5-Anleitung + Push-Empfehlung in Final-Summary | User-Antwort | ☐ (wird in dieser Session geliefert) |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Entscheidung |
|---|--------|-----|--------|--------------|
| A | Orphan-Branch `release/v1.0.0` + Tag `v1.0.0` (ohne Historie) | 100 % garantiert: keine alten Commits/leaked Daten | Historie geht verloren (lokal via bundle gesichert) | **A** — empfohlen (User-Entscheidung ausstehend) |
| B | Full History pushen | Historie öffentlich | Restrisiko bei alten Commits, lang | — |
| C | README EN-first | Standard für OSS-Launch | DE-Nutzer brauchen Doku-Links | **C** — empfohlen (Doku verlinkt DE/EN/BG) |
| D | README zweisprachig | DE-first | Doppelhaltung, Wartungsaufwand | — |
| E | Screenshots via Playwright-Skript (statische UI) + optionales Live-GIF | reproduzierbar | Live-GIF: VRAM + LM-Studio-Down nötig | **E** — empfohlen; Aufnahme bleibt User-Seite |

> **Auswahl:** A/C/E — Begründung: minimiert Leak-Risiko (A), maximiert
> Community-Erreichbarkeit (C), Screenshots sind das einzige harte Restrisiko
> und erfordern die Produktionsmaschine (E).

## Abhängigkeiten & Stakeholder

| # | Abhängigkeit / Stakeholder | Art | Impact | Status |
|---|---------------------------|-----|--------|--------|
| 1 | `venv_bot_20260802` (Tests/Gate) | lokal | hoch | vorhanden |
| 2 | Autosave-Watcher + Pre-Commit-Gate | lokal | hoch | aktiv (Log ok) |
| 3 | Sabetra: Remote-Setup + Push + Screenshots | User | blockierend (nur Stage 7) | offen |
| 4 | GitHub-Repo (öffentlich) | extern | blockierend (nur Stage 7) | offen |

## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|
| 1 | Autosave-Watcher läuft | `monitoring/autosave.log` (Zyklen 07:47–08:07) |
| 2 | `monitoring/` enthält ~90 getrackte interne Artefakte | `git ls-files monitoring/` |
| 3 | `.continue/` (4 Dateien), `settings.json`, `.db_root`, `data/websearch_cache.json`, `_search_tmp.txt`, `config/path_allowlist.json` getrackt | `git ls-files` |
| 4 | `feedback_data/user_feedback.json` getrackt (Beispieldatensatz, 1 User) | Dateiinhalt |
| 5 | Psych-UI rendert via `ui_tabs/psychology_tab.py` → `render_complete_interface()` (Zeile 762) | `git grep render_complete_interface` |
| 6 | Finance-Tab rendert via `render_finance_tab()` (Zeile 1324), nutzt `_tr()` | `finance/tab.py:1324` |
| 7 | i18n `t()`-API existiert (`i18n/i18n_manager.py:54`) | `git grep 'def t'` |
| 8 | `docs_archive/` + `ARCHIVE_INDEX.md` existieren | `Test-Path` |
| 9 | Git-Status sauber vor Start | `git status --short` (leer) |
| 10 | `utils/db_path_resolver.get_psychology_path()` existiert | `utils/db_path_resolver.py:100` |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | `config/user_id_config.py` wird nur im App-Kontext importiert (Root auf sys.path) → Resolver-Import sicher | offen | Volltestlauf + neuer Test |
| 2 | `git rm --cached` auf `monitoring/` bricht kein aktives Skript (Autosave schreibt neu dorthin) | offen | Autosave-Log nach Commit |

## Offene Fragen

| # | Frage | Owner | Deadline | Status |
|---|-------|-------|----------|--------|
| 1 | Push-Strategie: Orphan-Branch + Tag vs. Full History? | Sabetra | vor Push | ✔ A (Orphan + Tag) bestätigt 2026-08-31 |
| 2 | README: EN-first ok? | Sabetra | vor Push | ✔ EN-first umgesetzt (Rebuild, Commit 1b63f955) |
| 3 | Live-LLM-GIF erlaubt (LM-Studio-Down + VRAM)? | Sabetra | Stage 5 | offen (optional) |
| 4 | CI-Workflow (GitHub Actions) erwünscht? | Sabetra | vor Push | ✔ NICHT gewünscht (2026-08-31: Code-Publikation braucht kein Remote-Runtime; Local-First-Konsistenz) |
| 5 | Git-Identität (user.name/email) für den Public-Push — betrifft die gesamte Commit-Historie | Sabetra | vor Push | in Arbeit (Name + Email geliefert 2026-08-31; Commit-Autor + Email gesetzt; offene Lizenz-Frage: Sabetra vs. Realname in LICENSE/AGENTS/CONTRIBUTING) |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|-------------------|------------|-------------------|--------|
| 1 | `git rm --cached` löscht lokale Dateien | niedrig | mittel | `--cached` (nur Index); danach `Test-Path` je Datei | offen |
| 2 | i18n-JSON-Korruption bei Edits | niedrig | mittel | `json.load`-Check nach jedem Edit | offen |
| 3 | user_id_config-Import ohne Root auf sys.path | niedrig | mittel | Fallback auf altes Verhalten + Test | offen |
| 4 | README-Image-Links kaputt bis Screenshots existieren | sicher | niedrig | Stage-5-Notiz; Upload vor Push | offen |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | `monitoring/`, `settings.json`, `.continue/` werden untrackt | Lokal bleiben, nie public | `.gitignore` + `git rm --cached` |
| 2 | Disclaimers (Finance/Psych) | Juristische Klarheit für User | i18n-Strings DE/EN/BG + sichtbares Rendern + Test |
| 3 | `feedback_data/user_feedback.json` bleibt tracked | Beispieldatensatz ohne echte PII (verifiziert) | bleibt als Sample; Hygiene-Checker warnt bei echten User-IDs |

## Phase A — Klinische Positionierung entfernen + EU-AI-Act-Disclosure (2026-08-31)

> Ziel: Repo launch-tauglich machen, indem (a) jede **nutzer-sichtbare** klinische
> Selbstpositionierung entfernt und (b) eine **sichtbare AI-System-Disclosure**
> (EU AI Act Art. 50(1)) hinzugefügt wird. Interne Umbenennungen (Scope B/C/D)
> bleiben bewusst offen.

| # | Maßnahme | Dateien | Status |
|---|----------|---------|--------|
| A1 | i18n-Werte DE/EN/BG auf „Wellbeing & Reflexion"-Wording; `psych_ui.welcome.ai_note` in allen 3 Loci; BG-Zeichenfehler (escaped/typo'd Cyrillic) behoben | `i18n/locales/{de,en,bg}.json` | ☑ |
| A2 | Klinische UI-Fallbacks (DE/EN/BG) durch „Wellbeing"-Wording ersetzt | `ui/welcome_renderer.py`, `ui/active_session_renderer.py`, `ui/session_management_renderer.py`, `lifecycle/session_lifecycle_manager.py` | ☑ |
| A3 | AI-Disclosure (EU AI Act Art. 50(1)) in **Welcome- UND Active-View** sichtbar + Fallback bei fehlgeschlagenem i18n-Lookup | `psychological_session_interface.py` (heute: `wellbeing_session_interface.py`, C8 2026-09-01) | ☑ |
| A4 | AI-System-Disclosure in README (EN-first) ergänzt; bestehende Non-Medical-Disclaimer + Krise „112" + AGPL + Local-First bleiben | `README.md` | ☑ |
| A5 | Compliance-Tests: AI-Disclosure-Key in 3 Loci, UI rendert Disclosure, keine klinische UI-Positionierung, Krisen-Ressourcen vorhanden, i18n-Parität | `tests/test_compliance_disclaimers.py` (8 → 13 passed) | ☑ |

**Triage verbleibender klinischer Begriffe (alle NICHT nutzer-sichtbare UI):**
- `psychological.disclaimer` (DE/EN/BG): **beabsichtigt** — verneint medizinischen Ersatz + Krisen-Ressourcen (112).
- Finance-Strings („Diagnosis of open statement settlements", „Extended diagnostics window"): **falsch-positiv** (Buchhaltung, nicht medizinisch).
- `context/` + `adapters/` (``therapeutic_goals``, ``treatment_plan``, ``THERAPEUTISCHER KONTEXT``): **interne Architektur / LLM-Prompt-Kontext** — Scope B/C/D, bewusst vertagt.

**Validierung (2026-08-31):**
- Vollsuite: **937 passed**, 1 Third-Party-FutureWarning (torch/pynvml — fremd, nicht unterdrückt).
- `tests/test_compliance_disclaimers.py`: **13 passed** (vorher 8).
- `scripts/check_licenses.py --strict`: **OK** (Frische + Policy).
- `scripts/check_release_hygiene.py --strict`: **0 FAIL / 0 WARN (GRUEN)**.
- `git ls-files monitoring/ .continue/ settings.json data/` + `*.key`/`*.db`: **leer** (ungetrackt).
- `git grep 'C:\Users' -- '*.py' '*.ps1'`: **leer**.

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `docs/WORKDOC_PUBLIC_LAUNCH_20260831.md` | angelegt | — |
| 2 | 14 aktive Doku-Dateien: `AGENTS.md`, `CONTRIBUTING.md`, `DEVELOPER_QUICK_START.md`, `README.md`, `docs/00_CONTEXT_MASTER.md`, `docs/01_ARCHITECTURE_DEEP_DIVE.md`, `docs/06_CONTEXT_ENGINEERING_SOTA.md`, `docs/08_PSYCH_MODULE_OPTIMIZATION.md`, `docs/14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md`, `docs/18_LEGAL_WEB_PERSIST.md`, `docs/PROMPT_STANDARD.md`, `docs/README.md`, `funktionen.md` | 31 × `C:\Users\...`-Pfade → `<PROJEKT_ROOT>\...` bzw. `~\...` (Doku-Anteil von DoD #3) | Verifizierungs-Sweep: keine Treffer mehr in aktiven Docs (ausgenommen DoD-Metareferenz in diesem Workdoc; `.vibe/` und `monitoring/tmp` sind gitignored) |
| 3 | `scripts/run_pytest_venv.ps1`, `code_executor_engine.py` | Test-Gate Root-Cause-Fix: (a) Venv-Auswahl auf Produktiv-venv (`venv_bot_20260802` zuerst, zweistufiger pytest-Import-Check, Auswahl sichtbar loggen) — Reihenfolge jetzt identisch zum Pre-Commit-Hook; (b) 4 undefinierte `logger.debug`-Calls im eingebetteten Worker-Template entfernt/ersetzt (NameError in Umgebungen ohne plotly; Plotly-Handler schreibt jetzt auf `captured_err` wie der matplotlib-Handler) | Vorher/nachher in plotly-freier `.venv`: 5 failed → 5 passed + 1 skipped; Vollsuite: 932 passed / 0 failed (Commits 6ded2b67, d74d1726) |
| 4 | `docs/WORKDOC_PUBLIC_LAUNCH_20260831.md` | abgeschnittenes Dateiende (Endigung mitten in "Änderungen") wiederhergestellt; DoD-Status, Offene Fragen/Risiken, Testergebnisse, Change-Log aktualisiert | Read-back-Verifikation |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Datei-Stand vor Commit via Git zurückholen | `git log --oneline -- <pfad>` / `git checkout <sha> -- <pfad>` |
| 2 | Untracked gemachte Dateien lokal verifizieren (nie gelöscht) | `Test-Path <datei>` |
| 3 | Ältester vor-Launch-Commit als Basis | `git log --oneline` (vor Stage-1-Commit) |
| 4 | Backup-Ordner (falls nötig) | `~\bot6_backups\` |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|
| 1 | Screenshots fehlen bei Launch-Tag | mittel | Stage-5-Checkliste an Sabetra (Final-Summary) |
| 2 | User-Entscheidungen (1–5) ausstehend | niedrig | Empfehlungen dokumentiert; blockiert nur Stage 7 |
| 3 | Git-Identität war Platzhalter (`IHR_GITHUB_USERNAME`) — betrifft nur die private Lokalhistorie (wird nicht gepusht) | mittel | Behoben 2026-08-31: repo-lokal `user.name`/`user.email` = „Michaël Artebas <michaelartebas@proton.me>"; trägt nur der neue Initial-Commit |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `git status --short` (Start) | sauber | 2026-08-31 |
| 2 | `scripts/run_pytest_venv.ps1 tests/ -q` (Produktiv-venv, nach Gate-Fix) | 932 passed, 0 failed (1 Third-Party-FutureWarning: torch/pynvml — fremd, nicht unterdrückt) | 2026-08-31 |
| 3 | `tests/test_code_executor_delivery.py` in plotly-freier `.venv` (Beweis des Worker-Fix) | 5 passed + 1 skipped (vorher: 5 failed) | 2026-08-31 |
| 4 | `tests/test_compliance_disclaimers.py` | 8 passed | 2026-08-31 |
| 5 | `scripts/check_licenses.py --strict` | OK (Frische + Policy) | 2026-08-31 |
| 6 | `git ls-files monitoring/ .continue/ settings.json data/` (DoD #1) | leer (ungetrackt) | 2026-08-31 |

## Token-Budget & Kosten (optional, bei LLM-intensiven Tasks)

| Komponente | Geschätzte Tokens | Budget | Status |
|------------|-------------------|--------|--------|
| n/a | — | — | nicht LLM-intensiv |

---

## Change-Log (regelmässig aktualisieren)

- 2026-08-31 08:15 — Workdoc angelegt; Kontext-Checks abgeschlossen (Fakten 1–10).
- 2026-08-31 11:06 — Doku-Pfad-Sweep (Doku-Anteil von DoD #3): 31 × `C:\Users\...`-Pfade in 14 aktiven Doku-Dateien durch `<PROJEKT_ROOT>\...` bzw. `~\...` ersetzt. Verifizierungs-Sweep: keine Treffer mehr in aktiven Docs (ausgenommen DoD-Metareferenz in diesem Workdoc; `.vibe/` und `monitoring/tmp` sind bereits gitignored und ungetrackt).
- 2026-08-31 12:47 — Test-Gate Root-Causes behoben: (1) `run_pytest_venv.ps1` wählte stale `.venv` (ohne plotly/aiosqlite) → jetzt Produktiv-venv zuerst + zweistufiger pytest-Check + sichtbare Auswahl; (2) `code_executor_engine.py`: 4 undefinierte `logger.debug`-Calls im Worker-Template (NameError ohne plotly → Code-Execution + Plotly-Tests defekt). Beweisläufe + Vollsuite 932 passed. Commits 6ded2b67, d74d1726.
- 2026-08-31 12:55 — Workdoc-Ende (ab Zeile 122) war durch unterbrochenen Write abgeschnitten; wiederhergestellt und aktualisiert (DoD-Status, Offene Fragen #5, Offene Risiken #3, Testergebnisse #2–#6, Änderungen #3–#4). DoD #1–#4, #7, #8 verifiziert und grün.
- 2026-08-31 13:52 — **D3 (Archive) abgeschlossen:** 44 Maschinen-Pfade (`C:\Users\…`) in 11 Archiv-Dateien (`docs_archive/` + `docs/09_archived/`) portabel ersetzt (`<PROJEKT_ROOT>` bzw. `%USERPROFILE%`). Hygiene-Gate (`scripts/check_release_hygiene.py`) jetzt **0 FAIL / 0 WARN**, auch `--strict` grün. Repo-weiter `git grep`: keine echten Maschinen-Pfade mehr (nur `<user>`-Platzhalter in `.db_root.example` + DoD-Metareferenzen mit Ellipse). **D1=A** (Orphan-Branch) vom Nutzer bestätigt — wird zum Push angewendet.
- 2026-08-31 14:05 — **D2 = CI bewusst NICHT** (Nutzer-Entscheidung): Code-Publikation/Sponsoren-Zugang benötigt keine Remote-Runtime; Push ist reine Dateiübertragung. Lokales deterministisches Gate + Pre-commit-Hook genügen für Single-Maintainer; CI wäre der einzige Schritt mit Code-Ausführung auf fremder Infrastruktur (widerspricht Local-First-Konsistenz). Scope-Tabelle + DoD #5 angepasst (DoD #5 jetzt ☑). Falls später gewünscht: eine Datei (`.github/workflows/`) genügt zur Nachrüstung.
- 2026-08-31 14:20 — **Identität final (D2b):** Commit-Autor repo-lokal gesetzt: `Michaël Artebas <michaelartebas@proton.me>` (globaler Platzhalter `IHR_GITHUB_USERNAME` bleibt bewusst — er betrifft nur die private Lokalhistorie, die nicht öffentlich wird). **Lizenz-Entscheidung:** Copyright-Inhaber = **Michaël Artebas** in LICENSE + 10 Doku-Dateien (AGENTS, CODE_OF_CONDUCT, CONTRIBUTING ×2, README, SECURITY, SUPPORT, `00_CONTEXT_MASTER`, `19_LICENSES` ×3, `docs/README` ×2); **„Sabetra" bleibt GitHub-Account/Maintainer-Kontakt** (u. a. SECURITY.md „Maintainer Sabetra").
- 2026-08-31 19:55 — **Phase A (Klinische Positionierung + EU-AI-Act) abgeschlossen:** i18n DE/EN/BG auf „Wellbeing"-Wording, `ai_note` in 3 Loci, klinische UI-Fallbacks entfernt, AI-Disclosure in Welcome + Active View, README-Disclosure, Compliance-Tests 8 → 13. Validierung: Vollsuite **937 passed**, Lizenz OK, Hygiene **GRUEN**, keine sensiblen Dateien getrackt, keine Maschinen-Pfade. Triage: verbleibende klinische Begriffe = beabsichtigte Disclaimer / Finance-FP / interne Architektur (Scope B/C/D vertagt). Temp-Skripts bereinigt.
- 2026-08-31 21:25 — **Phase A.5 (De-Klinisierung: UI-Status + LLM-Stimme) abgeschlossen:** Klinische, **nutzer-/LLM-sichtbare** Strings entfernt, ohne Class-/Paket-/DB-Änderungen. Änderungen: (1) `agent_chatbot_logic.py` — `Psychologischer Chat`→`Wellbeing-Chat`, `Therapeutischer Modus`→`Reflexions-Modus`, `Psychologisches Profil von …`→`Wellbeing-Profil von dir`, `Fehler beim psychologischen Chat`→`Fehler im Wellbeing-Chat`, 2 Konsole-Prints; (2) `response_generator.py` — `THERAPEUTIC_SYSTEM_PROMPT_BASE`: `psychologischer Gesprächsbegleiter`→`Gesprächs- und Reflexionsbegleiter`, `psychologische Hypothese`→`vorsichtige Hypothese`, `Reflexion oder Intervention`→`Reflexion oder einen praktischen Schritt`, `psychologische Einordnung`→`die Reflexion/Einordnung` (GRENZEN-Negation „keine Psychotherapie/Diagnostik" **bewusst behalten**); (3) `context_formatter.py` — `THERAPEUTISCHER KONTEXT`→`REFLEXIONS-KONTEXT` (Z58) + zusätzlich gefundene LLM-Labels `Therapeutische Schwerpunkte`→`Schwerpunkte` (Z295), `🎯 THERAPEUTISCHE ZIELE`→`🎯 PERSÖNLICHE ZIELE` (Z386). **Neu 3 Regression-Tests** in `tests/test_compliance_disclaimers.py` (13→16): Chatbot-UI-Strings, LLM-Prompt-Selbstidentifikation, LLM-Kontext-Labels. Bewusst NICHT geändert (→ Session 2): interne Kommentare/Logs (`[PSYCHO-CHAT]`, Z3582), interne Keys/Methoden (`therapeutic_goals`, `_format_therapeutic_goals`), i18n-Key-Präfixe, Dateinamen. **Validierung:** `py_compile` OK, Vollsuite **940 passed**, Lizenz **OK**, Hygiene **GRUEN**. Commits: `13ac32f5` + `d4efa96c` (Autosave), Tag **`wellbeing-a5-v1.0`** an HEAD. Backup: `~\bot6_backups\a5_20260831_210200\`.
- 2026-09-01 — **Code-Seite vollständig abgeschlossen (Phase C = Scope-B-Rename):** C6 `therapeutic_goals`→`care_goals` (inkl. idempotenter DB-Migration, Daten erhalten), C7 `psych_ui.*`→`wellbeing_ui.*` (+ `insight_types`-Lokalisierung de/bg), C8 `psychological_session_interface.py`→`wellbeing_session_interface.py` (Klasse `WellbeingSessionInterface`). **Validierung:** Vollsuite **946 passed** (nach C6/C7/C8 jeweils), i18n-Parität 558/558/558, Release-Gate (Lizenz + pytest + Profile-Fixture) GRÜN. Belege: `WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` §14 (docs_archive/). **Verbleibend = Stage 7 (User-Seite):** Remote-Setup, Push, Screenshots, User-Entscheidungen 1–5.
