# Workdoc: Phase E – Wellbeing-DB de-klinisieren (Namen)

> **Erstellt:** 2026-09-01 20:50
> **Abschluss-Ziel:** vor Public-Launch (unverzüglich)
> **Status:** IN_ARBEIT
> **Autor:** Cline (Agent)
> **Reviewer:** Projekt-Owner

---

## Original-Auftrag

> **Ziel (aus dem laufenden Auftrag):** Plan und Verifikation einer sicheren
> De-Klinifizierungs-Migration für lokale SQLite-Bezeichner, klinische
> Tabellen-/Spalten-/Indexnamen und die sichtbare DB-/Dateinamensgebung im
> öffentlichen Repository.
>
> **Auslösendes Szenario:** Jemand lädt das Repository, testet den Bot, es wird
> eine DB angelegt; neugierige Nutzer:innen inspizieren den DB-Inhalt und
> sehen klinische Bezeichnungen (z. B. `psychological_sessions`,
> `treatment_plans`, `alliance_scores`, `mbc_observations`).
>
> **Freigaben (2026-09-01):**
> 1. Scope **S3** (komplett: Schema + Datei/Key/Ordner + Code-Identifikatoren).
> 2. Renaming-Matrix **OK**.
> 3. Timing: **vor Launch, also jetzt**.

## Scope & Nicht-Scope

| Im Scope (S3) | Nicht im Scope (eigene Workstreams) |
|---------------|--------------------------------------|
| E1: In-place Schema-Rename (Tabellen, klinische Indizes, `planned_interventions`) | **F – Inhalts-De-Id:** Plaintext-Klinikinhalte (`mbc_observations.response_text`, `stage_assessments.rationale`, `plan_goals.*`, `triples.*`, KG-Entities) – Rename kaschiert das nicht |
| E2: Datei/Key/Ordner-Rename (key-sicher, atomar) | **G – SOTA-Key-Mgmt:** Fernet-Key ko-loziert (`<db>.key`) – OWASP-Anti-Pattern, Fix = DPAPI/Credential-Manager |
| E3: Code-Identifikatoren (Klassen, Attribute, Dateien, Tests) | Automatisches Downgrade (Forward-Only-Policy) |
| E4: Doku-/Repo-Hygiene | Neue Features / Verhaltensänderungen |
| E5: Migrationstests + Release-Gate | |

**Framing (verbindlich):** Rename = **De-Stigmatisierung / Naming**, ausdrücklich
**kein** Security-Feature. Echter Schutz bleibt Local-First + lokale Verschlüsselung.

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Frische Wellbeing-DB enthält in `sqlite_master` **keinen** klinischen String (Tabelle/Index/Spalte/Dateiname) | `tests/test_wellbeing_fresh_db_no_clinical_names.py` | ☐ |
| 2 | Legacy-DB (Alt-Namen) wird idempotent migriert; Daten bleiben **bitgenau** erhalten (Zeilen-/Wertvergleich) | `tests/test_wellbeing_schema_migration.py` | ☐ |
| 3 | Zweiter Migrationslauf ist ein No-Op (Marker `_schema_meta`) | dito | ☐ |
| 4 | `PRAGMA foreign_key_check` nach Migration = 0 Verletzungen | dito | ☐ |
| 5 | Datei-Rename nur mit Key-Move; fehlender Key = **lauter Fehler** (kein Auto-Key) | Code-Inspektion + Test | ☐ |
| 6 | Vollsuite grün (Baseline 946 + neue Tests) | `run_pytest_venv.ps1 tests/ -q` | ☐ |
| 7 | i18n-Parity 558/558/558 (Regressionsschutz) | i18n-Parity-Check | ☐ |
| 8 | `check_licenses.py --strict` + `dependency_vulnerability_scanner.py --strict` grün | beide Skripte | ☐ |
| 9 | `git grep` klinische Strings: nur noch Workdocs/Archiv | `git grep` | ☐ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | In-place `ALTER TABLE/INDEX RENAME TO` (SQLite ≥3.26) | O(1), FK/Indizes werden mitgetragen, kein Daten-Kopieren, reversibel | Ältere Clients (<3.26) – nicht relevant (venv 3.49.1) | 7 | 6 | 7 | 5 | **gewählt** |
| B | Copy-to-new-DB (`VACUUM INTO` + Schema-Export) | Vollständige Datei-Neuanlage | Hoher Speicher, langsam, komplexe Key-/Pfad-Pflege, höherer Ausfallradius | 6 | 4 | 4 | 3 | verworfen |
| C | Nichts ändern, nur Doku | 0 Risiko | Zielszenario bleibt bestehen | 2 | 3 | 7 | 7 | verworfen |

> **Auswahl:** Option A (in-place Schema-Rename) + optionale, key-sichere
> Dateibewegung (E2). Begründung: SQLite 3.49.1 im venv garantiert modernes
> `ALTER TABLE RENAME` (FK/Indizes mitgetragen); kein Views/Triggers/FTS in der
> Wellbeing-DB → minimaler Blast Radius; reversibel; keine Datenkopie.

## Abhängigkeiten & Stakeholder

| # | Abhängigkeit / Stakeholder | Art | Impact | Status |
|---|---------------------------|-----|--------|--------|
| 1 | `wellbeing/wellbeing_db.py` (Monolith ~3.8k) | Chirurgisch ändern (Schicht 3) | hoch | offen |
| 2 | `wellbeing/care_plans/repository.py` (`_SCHEMA_DDL`) | DDL + Queries | hoch | offen |
| 3 | `utils/db_path_resolver.py` | Dateiname + Key-Pfad | hoch | offen |
| 4 | `scripts/db_backup.py` | Datei-/Key-Liste | mittel | offen |
| 5 | 12 `tests/test_psych*.py` | Rename + Referenzen | mittel | offen |
| 6 | LM Studio (hält VRAM) | **nicht** per Kill beenden | – | beachtet |

## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|
| 1 | SQLite im venv = 3.49.1 (≥3.26, `ALTER TABLE RENAME` trägt FK/Indizes mit) | `venv_bot_20260802` Runtime-Check |
| 2 | Fernet-Key ko-loziert, **auto-generiert** wenn fehlt (Datenverlust-Risiko bei reiner Dateibewegung) | `wellbeing/wellbeing_db.py:259-268` |
| 3 | `_schema_meta`-Marker-Muster bereits etabliert (`entity_norm_v4`, `triple_hash_v2`) | `wellbeing/wellbeing_db.py:961,1062-1075` |
| 4 | Klinische DB entsteht **nicht** im normalen Chat-Pfad (Privacy-Guard, default OFF) | `agent_chatbot_logic.py:761-798` (deprecated 2026-08-28) |
| 5 | Lazy-Package-Importe (keine Side-Effects bei `import wellbeing`) | `wellbeing/__init__.py:28-48` |
| 6 | Migrations-Vorbilder vorhanden (Modul + Test) | `agent/rag_store/core/multimodal_db_migration.py`, `tests/test_care_goals_migration.py` |
| 7 | Baseline: 946 Tests grün, i18n 558/558/558, Release-Gate grün | C6–C8-Release (vorherige Runde) |
| 8 | Produktive klinischen Artefakte: `psychological_support.db` (66 MB), `.key` (44 B), `psychological_support_kg_cache/` | `~\.local\share\bot6_dbs\` Listing |
| 9 | Plaintext-Klinikinhalte (Nicht-Scope F): `response_text`, `rationale` | `care_plans/repository.py:837,875` |
| 10 | 21 Non-Test-Dateien + 12 Testdateien betroffen | `git grep`-Inventar (dieser Session) |
| 11 | **Matrix-Addendum (reales Schema):** zusätzlich `psychological_embeddings`, `psychological_kg_triples` → `wellbeing_*` (Prinzip `psychological_*`→`wellbeing_*`) | `sqlite_master`-Dump (44 Objekte) |
| 12 | **10 klinische Indizes** final: `idx_alliance_session`, `idx_case_user`, `idx_case_form_v2_user`, `idx_homework_user`, `idx_mbc_user_instrument`, `idx_outcome_session`, `idx_stage_assessments_user`, `idx_screening_session`, `idx_screening_user`, `idx_treatment_plans_user_status` | `sqlite_master`-Dump |
| 13 | **Keine** Views/Triggers/FTS in der Wellbeing-DB (nur `table`+`index`) | `sqlite_master`-Dump |
| 14 | Rollback-Position: Git-Tag `pre-phase-e` @ `b7177884` + DB-Backup `2026-09-01` (quick_check ok, Key dabei) | `git tag -l`, `~/bot6_backups/db/auto/2026-09-01/manifest.json` |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | `plan_goals.*`, `goal_updates.*`, `triples.*`, KG-Entities sind ebenfalls Plaintext | offen (nur `mbc`/`stage` direkt verifiziert) | INSERT-Pfade in `repository.py` lesen |
| 2 | Kein View/Trigger/FTS in der Wellbeing-DB (nur Tabellen+Indizes) | hoch, aus `sqlite_master`-Dump | `SELECT type,name FROM sqlite_master` |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|-------------------|------------|--------------------|--------|
| 1 | Datei-Rename ohne Key-Move → App generiert neuen Key → alte Daten undekodierbar (still) | mittel (E2) | **hoch (Datenverlust)** | Fail-Fast: Key **muss** am neuen Pfad vorliegen, sonst lauter Fehler; atomare Moves; Altdatei behalten | offen |
| 2 | Vergessene SQL-Referenz auf Alt-Tabellenname → RuntimeError zur Laufzeit | mittel | mittel | Vollsuite + `git grep`-Sweep + Frisch-DB-Smoke-Test | offen |
| 3 | Monolith `wellbeing_db.py` durch große Neuausgabe korrupt | niedrig (Schicht 3) | hoch | Nur chirurgische Blöcke; `py_compile` nach jedem Schreiben | offen |
| 4 | `therapeutic_focus`-Payload-Key ändern bricht gespeicherte Profile | niedrig | mittel | One-Shot-Migration (decrypt→Key-Rename→re-encrypt) + Test | offen |
| 5 | i18n-Strings/Keywords berühren klinische Begriffe | niedrig | niedrig | i18n-Parity-Re-Check | offen |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | Rename verändert **keine** Verschlüsselung oder Daten | kein Security-Gewinn, nur Wahrnehmung | Explizit als De-Stigmatisierung rahmen; Workstreams F/G separat |
| 2 | Key-Kolokation (Workstream G) | Für Besitzer durchschaubar | Aus Scope; separat dokumentiert |
| 3 | Plaintext-Klinikinhalte (Workstream F) | Rename kaschiert Inhalt nicht | Aus Scope; separat dokumentiert |
| 4 | Backup/Restore-Beispiele in AGENTS.md nennen Altdateinamen | Doku-Drift | E4: AGENTS.md aktualisieren |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Code-Stand zurück (vor Phase E) | `git checkout <pre-phase-e-sha> -- <dateien>` bzw. Revert |
| 2 | Bestehende Legacy-DB | Backup `pre-phase-e` (db_backup.py) zurückkopieren |
| 3 | Datei-Rename zurück | `wellbeing_store.db`→`psychological_support.db` + `.key` + Ordner (Altdatei wird in E2 behalten) |
| 4 | Forward-Only: Downgrade wird als „nicht unterstützt" dokumentiert; Rename ist reversibel, Daten bleiben identisch | Workdoc-Notiz |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|
| 1 | Workstream F (Plaintext-Inhalte) bleibt nach Rename sichtbar | mittel | Eigener, nachrangiger Security-Workstream |
| 2 | Workstream G (Key-Kolokation) | mittel | Eigener, nachrangiger Security-Workstream |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| | (füllt sich während E1–E5) | | |
