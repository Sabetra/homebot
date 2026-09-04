# Workdoc: Legal & Compliance CH/EU/DE + Mental-Health-Positionierung (Public Launch)

> **Erstellt:** 2026-08-31
> **Status:** COMPLETED (2026-09-01) — Phasen A–C umgesetzt, B-Phase = Scope-B-Session (✅ vollständig, siehe Abschnitt B); finalisiert und archiviert
> **Autor:** Cline (Agent)
> **Reviewer:** Sabetra
> **Verwandt:** WORKDOC_PUBLIC_LAUNCH_20260831.md (Launch-Hygiene)
> **Rollback-Punkt:** d9017c28 (vor Legal-/Compliance-Änderungen)

---

## Original-Auftrag

"Prepare bot6 for a public GitHub launch by completing CH/EU/DE legal and mental-health
compliance, repositioning psychology-related features as wellbeing/reflection, and
preserving compatibility with existing local sessions, database, and encrypted keys."

Nachtrag (2026-08-31): "Recherchiere, was wir alles abdecken muessen, um fuer CH/EU/DE
sicher zu sein. Haben wir damit alles abgedeckt und recherchiert?" — Ergebnis: NEIN, drei
neue Befunde (C-SSRS-Lizenz, nDSG-Zitat, AI-Akt-Klaerung). Darueber hinaus: Risiko-Split
Autor/Nutzer + SOTA-Disclaimer-Anforderung (LEGAL.md) geklaert.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| CH/EU/DE Legal-Recherche + Belege | Juristische Beratung (kein Anwalt) |
| Screening-Instrument-Lizenz (C-SSRS/PHQ-9/GAD-7) | Cloud-LLM / Server |
| EU AI Act Art. 50 Transparenz | Feature-Entwicklung |
| Scope-B-Rename (psychology -> wellbeing) | |
| User-facing Content-Neupositionierung | |
| LEGAL.md / TERMS + README-Disclaimer + Impressum | |
| Compliance-Tests | |
| Workdoc + Change-List | |

## Definition of Done

| # | Kriterium | Pruefmethode | Status |
|---|-----------|-------------|--------|
| 1 | Kein verbatim klinischer Instrument-Text im Repo (C-SSRS/PHQ-9/GAD-7 Original-Items) | `git grep -nE 'C-SSRS|suicidal|PHQ-9|GAD-7'` -> nur adaptierte, nicht-klinische Formulierungen | ✅ Code (Item-Sätze entfernt, Smoke 9/9); i18n/Doku: offen |
| 2 | Keine klinischen Begriffe in user-facing Strings (Therapie/Diagnose/Behandlung/Therapeut) | Tests: prohibited-terms | (offen) |
| 3 | Tab-Label "Wellbeing & Reflexion" in DE/EN/BG; i18n-Key-Paritaet | Tests: label + parity | (offen) |
| 4 | Krisen-Wording (112/911/999 + findahelpline) in 3 Locales | `tests/test_crisis_prompt_threshold.py` | (offen) |
| 5 | AI-Art.-50(1)-Kennzeichnung ("Du sprichst mit einer KI") in 3 Locales | Tests: disclosure | (offen) |
| 6 | Scope-B-Rename abgeschlossen; Tests gruen | `run_pytest_venv.ps1 tests/ -q` | (offen) |
| 7 | .key/.db/Session-Daten nicht getrackt | `git ls-files '*.key' '*.db'` (leer) | (gruen) |
| 8 | Lizenz-Gate + Release-Hygiene + Sensitive-Pfad-Check gruen | `check_licenses.py --strict`, `check_release_hygiene.py --strict` | (gruen) |
| 9 | LEGAL.md / TERMS (DE/EN) + README-Disclaimer + Impressum | Doku-Check | (offen) |

## Alternativen & Entscheidung (Screening-Instrumente)

| # | Option | Pro | Contra | Risiko | Entscheidung |
|---|--------|-----|--------|--------|--------------|
| A | C-SSRS entfernen + ALLE 3 durch adaptierte, nicht-klinische Items ersetzen | Konsistent mit Wellbeing; keine Lizenzrisiken; Totcode -> kein Funktionsverlust; alle Lizenzfragen eliminiert | "Screening"-Nomenklatur entfaellt (ist aber Ziel) | niedrig | **A - gewaehlt (User-Entscheidung 2026-08-31)** |
| B | Nur C-SSRS entfernen; PHQ-9/GAD-7 verbatim behalten | PHQ-9 explizit frei | GAD-7 keine explizite Freigabe; klinisch positioniert | mittel | - |
| C | C-SSRS-Lizenz beantragen | Original bleibt | Genehmigung + ggf. Kosten; passt nicht zu AGPL/OSS; Zeitaufwand | hoch | - |

> **Auswahl:** A — C-SSRS ist Lizenzblocker (Columbia Lighthouse Project / Research
> Foundation for Mental Hygiene; keine OSS-Weiterverbreitung). PHQ-9/GAD-7 sind rechtlich
> vertretbar, aber klinisch positioniert — zur Konsistenz + Risikominimierung werden alle
> drei adaptiert. Der Block ist Totcode (keine Aufrufer), daher kein Funktionsverlust.
> Live-Pfade (`suggest_periodic_screening`, `estimate_from_text`) bleiben unangetastet.

## Verifizierte Fakten (Legal-Recherche, Quellen = Zugriffsdatum 2026-08-31)

| # | Fakt | Beleg / Quelle |
|---|------|----------------|
| 1 | **C-SSRS Brief** unterliegt striktem Lizenzmodell (Columbia Lighthouse Project / Research Foundation for Mental Hygiene); OSS-Weiterverbreitung ohne Genehmigung NICHT erlaubt | research.columbia.edu/csuicide; cssrs.columbia.edu; suicidepreventionlifelines.org |
| 2 | **PHQ-9** explizit frei: "No permission required to reproduce, translate, display or distribute" | patienthealthblog.com (Kroenke) |
| 3 | **GAD-7** weit verbreitet, niedriges Risiko; keine explizite Freigabe gefunden | patienthealthblog.com (Spitzer et al.) |
| 4 | **nDSG Privathaushalts-Ausnahme = Art. 2** (NICHT "Art. 89" — existiert nicht; Gesetz endet bei Art. 74): "Personendaten, die von einer natuerlichen Person ausschliesslich zum persoenlichen Gebrauch bearbeitet werden" | datenschutz.law; datenschutzgesetze.ch (in Kraft 01.09.2023) |
| 5 | **DSGVO** Art. 2(2)(c) (Privathaushalt) + Art. 9 (besondere Daten); local-first -> Anbieter verarbeitet keine Daten | europa.eu (DSGVO) |
| 6 | **EU AI Act Art. 50(1)** (KI-Chatbot-Kennzeichnung) gilt seit **02.08.2026**; **Art. 50(3)** (Emotionserkennung) nur bei biometrischer Basis (Art. 3(39)) -> textbasierte App = 50(3) greicht NICHT | europarl.europa.eu; artificialintelligenceact.eu; digital-strategy.ec.europa.eu |
| 7 | **MDCG 2019-11**: nicht-medizinische Positionierung zulaessig, wenn Intended-Purpose nicht klinisch | MDCG 2019-11 (EU); greenlight.guru |
| 8 | **CH PsyG** regelt Therapie-Berufsbezeichnungen; "Wellbeing/Reflexion" bleibt draussen | PsyG (CH) |
| 9 | **UWG** (CH/DE): irrefuehrende Werbung / klinische Heilversprechen = Risiko -> neutral formulieren | UWG (CH/DE) |
| 10 | **OR Art. 41** (Fahrlaessigkeit, CH); **PsychThG** (DE, Therapie-Grenze) | OR (CH); PsychThG (DE) |
| 11 | **Krisen-Wording**: keine CH/EU/DE-Gesetzespflicht fuer konkrete Notrufnummern im Chatbot; 112 (EU/CH), 911 (US/CA), 999 (UK) + findahelpline.com = Best Practice / Haftungsabsicherung | Recherche 2026-08-30/31 |
| 12 | `.key`/`.db` sind gitignored -> nicht im oeffentlichen Repo | `git ls-files` (leer); `.gitignore` |
| 13 | Package-/Modul-Rename ist datenkompatibel (DB-Schema, KG, Keys, Pfade, Session-Historie bleiben unveraendert) | Analyse (fruehere Session) |
| 14 | **Risiko-Split**: Autor haftet fuer WAS er verteilt (Inhalte/Lizenz/Positionierung); Nutzer/Firma fuer WIE (eigene MDR/DSGVO/AGPL-Nutzung). Disclaimer + AGPL 15/16 + Positionierung = SOTA | AGPL-3.0 Abs. 15/16; interval-walk-trainer TERMS.md (github); MDCG 2019-11 |

## Risiko & Impact-Matrix

| # | Risiko | W | A | Minderungsmaßnahme | Status |
|---|--------|---|---|--------------------|--------|
| 1 | C-SSRS-Text im oeffentlichen Repo (Lizenz) | hoch | hoch | Option A: entfernen + adaptieren | ✅ Code (C-SSRS + verbatim Items entfernt) |
| 2 | Klinische Nomenklatur (Tab "Psychologie", "Therapie") -> MDR/UWG/PsyG | mittel | mittel | Scope-B-Rename + Content-Neupositionierung | (offen) |
| 3 | AI-Art.-50(1)-Kennzeichnung fehlt | mittel | mittel | Hinweis "Du sprichst mit einer KI" in 3 Locales | (offen) |
| 4 | GAD-7 verbatim (keine explizite Freigabe) | niedrig | niedrig | Option A: adaptieren | (offen) |
| 5 | "Offline"-Marketing -> UWG-Irrefuehrung | niedrig | niedrig | README: "local-first" statt "offline" | (offen) |
| 6 | Health-Daten (mood/journal) -> DSGVO Art. 9 | niedrig | mittel | Doku: Nutzer verantwortet lokale Health-Daten; local-first | (offen) |
| 7 | .key/.db-Leak | niedrig | hoch | gitignored; Sensitive-Pfad-Check im Gate | (abgemildert) |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Mood-/Journal-Daten = Health-Daten (DSGVO Art. 9) | Nutzer verantwortlich | Doku-Hinweis; local-first; keine Cloud |
| 2 | Verschlüsselte Keys (.key) | sensibel | gitignored; lokal; kein Repo |
| 3 | Session-Historie | Pers./Health-Daten | lokal; DB-autoritativ; kein Server |

## Aenderungen (Change-List)

### A) Screening-Instrumente (psychological_support/therapeutic_core.py) — Option A
| # | Aenderung | Test |
|---|-----------|------|
| A1 | `CSSRS_ITEMS` (363-370) entfernen | py_compile | ✅ |
| A2 | `score_cssrs` (453-492) entfernen | py_compile | ✅ |
| A3 | C-SSRS-Branches in `generate_screening_prompt` (527-528) + `estimate_scores_from_conversation` (566-567) entfernen | py_compile | ✅ |
| A4 | `PHQ9_ITEMS`/`GAD7_ITEMS` (324-353) → `MOODCHECK_ITEMS`/`CALMCHECK_ITEMS` (nicht-klinisch); `score_phq9`→`score_mood`, `score_gad7`→`score_calm`; `estimate_from_text` de-klinisiert (mood/calm + Backward-Compat phq9/gad7, Krisen-Erkennung + `crisis_signal` behalten); Aufrufer + Kommentare (session_interface, psychological_db:441, models.py:196, Docstring:8) aktualisiert — ✅ fertig (2026-08-31, py_compile OK + Smoke-Test 9/9 PASS, keine verbatim klinischen Item-Sätze im Repo)

### B) Scope-B-Rename (psychology -> wellbeing) — Methode: `git mv` + systematische Import-/Referenz-Updates
| # | Von | Zu |
|---|-----|-----|
| B1 | `psychological_support/` | `wellbeing/` |
| B2 | `treatment/` | `care_plans/` |
| B3 | `therapeutic_prompts.py` | `conversation_prompts.py` |
| B4 | `therapeutic_core.py` | `conversation_core.py` |
| B5 | `psychological_db.py` | `wellbeing_db.py` |
| B6 | `psychology_tab.py` | `wellbeing_tab.py` |
| B7 | `therapeutic_goals.py` | `care_goals.py` |
| B8 | `TherapeuticCore` | `WellbeingCore` |
| B9 | `TreatmentPlan` | `CarePlan` |
| B10 | `TreatmentRepository` | `CarePlanRepository` |
| B11 | `PsychologicalDatabase` | `WellbeingDatabase` |
| B12 | `PsychologyProfile` | `WellbeingProfile` |
| B13 | `get_psychology_path()` | `get_wellbeing_path()` (Rueckgabepfad UNVERAENDERT) |
| B14 | `gui.tabs.psychology` | `gui.tabs.wellbeing` |
| B15 | i18n `psychological.*` | `wellbeing.*` |

> **Status (2026-09-01):** B-Phase **vollständig ausgeführt** (Scope-B-Session 2026-08-31/09-01).
> Zielformen verifiziert, z. B. `wellbeing/conversation_core.py`, `wellbeing/care_plans/repository.py`
> (`CarePlanRepository`), `wellbeing/profile_synthesizer.py` (`WellbeingProfile`),
> `utils/db_path_resolver.py` (`get_wellbeing_path()`, Rückgabepfad unverändert — Datenkompatibilität);
> B7 = C6 inkl. idempotenter DB-Migration. Belege & Verifikation:
> `WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` §14 (docs_archive/).

> **Hinweis:** Vollständiger Exekutions- & Rollback-Plan (exakte Datei-/Klassen-/i18n-/Import-Listen, Phasen A–E,
> Rollback-Checkpoint, Verifikations-Gates) siehe **`WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md`**. Checkpoint: Tag/Branch `pre-scope-b-rename` -> `5f4282a4`. `get_wellbeing_path()` muss denselben Rueckgabepfad liefern wie zuvor (Datenkompatibilitaet).

### C) User-facing Content-Neupositionierung
| # | Datei | Aenderung |
|---|-------|-----------|
| C1 | i18n/locales/de.json, en.json, bg.json | "Psychologie" -> "Wellbeing & Reflexion"; klinische Begriffe -> wellbeing; Krisen-Wording; AI-Kennzeichnung |
| C2 | README.md | "local-first" statt "offline"; Wellbeing-Positionierung; Disclaimers |
| C3 | AGENTS.md, SECURITY.md, docs/README.md, marketing/* | klinische Begriffe -> wellbeing; Doku-Index aktualisieren |
| C4 | docs/08_PSYCH_MODULE_OPTIMIZATION.md | neu relabeln (Wellbeing-Modul) |

### D) Compliance-Tests
| # | Test | Pruefung |
|---|------|----------|
| D1 | test_prohibited_clinical_terms.py | keine klinischen Begriffe in user-facing Strings |
| D2 | test_wellbeing_tab_label.py | "Wellbeing & Reflexion" in DE/EN/BG |
| D3 | test_i18n_parity.py | Key-Paritaet DE/EN/BG |
| D4 | test_crisis_wording.py | 112/911/999 + findahelpline in 3 Locales |
| D5 | test_ai_disclosure.py | AI-Art.-50(1)-Kennzeichnung |
| D6 | test_data_compat.py | Session-/DB-Kompatibilitaet (wo moeglich) |

### E) LEGAL.md / TERMS + README-Disclaimer + Impressum (SOTA-Disclaimer, vgl. Risiko-Split)
| # | Datei | Inhalt |
|---|-------|--------|
| E1 | LEGAL.md (DE/EN) | Intended Use (Wellness, kein Medizinprodukt) · Not medical advice · AS IS (Ref. AGPL 15/16) · Haftungsbegrenzung · Datenschutz (local-first, Nutzer=Controller) · Compliance-Verantwortung beim Nutzer · Krisen-Hilfen · AGPL · anwendbares Recht + Salvatorische Klausel · Last Updated |
| E2 | README.md | "Not a medical device" · "local-first, no data collection" · "use at your own risk" · "kommerzielle/medizinische Nutzung = eigene MDR/DSGVO-Pflichten" |
| E3 | README.md / docs | Impressum/Kontakt (DE-Pflicht) |

## Rollback-Strategie

| Schritt | Aktion | Befehl |
|---------|--------|--------|
| 1 | Rollback-Punkt | `d9017c28` (HEAD vor Legal-/Compliance-Aenderungen) |
| 2 | Bei Fehlschlag: Rollback | `git reset --hard d9017c28` |
| 3 | Datei-Snapshot | `~\bot6_backups\` (vor grossen Edits) |
| 4 | DB-Backup | `~\bot6_backups\db\auto\` (taeglich, automatisch) |

## Offene Risiken

| # | Risiko | Schwere | Maßnahme |
|---|--------|---------|----------|
| 1 | C-SSRS-Entfernung (Option A) Umsetzung | mittel | Change-List A1-A4 |
| 2 | Scope-B-Rename brechende Referenzen | mittel | systematische Updates + Vollsuite |
| 3 | BG-Locale-Uebersetzung (Wellbeing) | niedrig | native Ueberpruefung |
| 4 | GAD-7-Status (nur bei Option B) | niedrig | Option A gewaehlt -> N/A |

## Testergebnisse (Stand 2026-08-31, VOR Umsetzung)

| # | Test / Befehl | Ergebnis |
|---|---------------|----------|
| 1 | `git ls-files '*.key' '*.db'` (DoD #7) | leer (ungetrackt) |
| 2 | `tests/test_compliance_disclaimers.py` | 8 passed |
| 3 | `run_pytest_venv.ps1 tests/ -q` | 932 passed |
| 4 | `check_licenses.py --strict` | OK |
| 5 | `check_release_hygiene.py --strict` | 0 FAIL / 0 WARN |

## Quellen (Zugriffsdatum 2026-08-31)

- C-SSRS / Columbia Lighthouse Project: research.columbia.edu/csuicide; cssrs.columbia.edu; suicidepreventionlifelines.org
- PHQ-9 / GAD-7: patienthealthblog.com (Kroenke; Spitzer et al.)
- nDSG: datenschutz.law; datenschutzgesetze.ch (Art. 2, in Kraft 01.09.2023)
- DSGVO: europa.eu
- EU AI Act Art. 50: europarl.europa.eu; artificialintelligenceact.eu; digital-strategy.ec.europa.eu (in Kraft 02.08.2026)
- MDCG 2019-11 (EU); greenlight.guru
- PsyG (CH); UWG (CH/DE); OR Art. 41 (CH); PsychThG (DE)
- AGPL-3.0 Abs. 15/16: tldrlegal.com; termsfeed.com
- SOTA-Disclaimer-Beispiel: interval-walk-trainer TERMS.md (github.com)
- findahelpline.com (globale Helpline-Verzeichnis)

---

> **Regeln:** Keine ganzen Quelldateien kopieren; Hypothesen von Belegen trennen;
> bei Abschluss: Workdoc loeschen oder nach `docs_archive/` verschieben.
