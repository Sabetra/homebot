# Workdoc: Profil-Cache-Import im Sync-/Async-Handler

> **Erstellt:** 2026-08-04
> **Abschluss-Ziel:** 2026-08-04
> **Status:** ABGESCHLOSSEN
> **Autor:** GitHub Copilot

## Original-Auftrag

behebe dieses Problem per rootcause: Es bleibt lediglich ein bereits bestehender Pylance-Hinweis zum optionalen Import [utils.persistent_user_profile](vscode-file://vscode-app/c:/Users/PC/AppData/Local/Programs/Microsoft%20VS%20Code/1b6a188127/resources/app/out/vs/code/electron-browser/workbench/workbench.html) im älteren Sync-Handler; er gehört nicht zu dieser Änderung und beeinflusst die bestandenen Tests nicht.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Profil-Cache-Invalidierung in Sync-/Async-Handlern | Profil-Synthese oder Cache-Schema |
| Pylance-Importfehler an der Ursache entfernen | Unabhängige optionale Integrationsmodule |
| Fokussierte Vertrags- und Regressionstests | UI- oder Treatment-Verhalten |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Kein Handler importiert das nicht existente Modul | Suche/Pylance | ✅ |
| 2 | Injizierter Cache wird mit kanonischen Argumenten invalidiert | Unit-Test Sync/Async | ✅ |
| 3 | Fehlende User-ID erzeugt keine Invalidierung | Unit-Test | ✅ |
| 4 | Bestehende Psychoregression bleibt grün | Pytest | ✅ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | Fehlendes Legacy-Modul neu anlegen | Import wird auflösbar | Dupliziert Capability-State | 2 | 2 | 6 | 3 | Verworfen |
| B | Flag aus Integrationsmodul importieren | Bestehendes Flag | Falscher Owner, unnötige Kopplung | 3 | 3 | 6 | 4 | Verworfen |
| C | Injizierten Cache direkt verwenden | Folgt DI-Vertrag, kein Stale-State | Keine | 7 | 7 | 7 | 7 | Umsetzen |

> **Auswahl:** Option C. `profile_cache is not None` belegt die Verfügbarkeit bereits; `invalidate_profile()` ist der kanonische Vertrag.

## Abhängigkeiten & Stakeholder

| # | Abhängigkeit / Stakeholder | Art | Impact | Status |
|---|---------------------------|-----|--------|--------|
| 1 | `ProfileCacheManager.invalidate_profile()` | kanonische API | unverändert | verifiziert |
| 2 | `ServiceContainer` | Dependency Injection | unverändert | verifiziert |
| 3 | Sync-/Async-MessageHandler | Aufrufer | Guard wird entfernt | abgeschlossen |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | `utils/persistent_user_profile.py` existiert nicht | Verzeichnisinspektion |
| 2 | Das Flag existiert nur in `utils/psychological_orchestrator_integration.py` und beschreibt dessen Imports | Quellcode-Suche |
| 3 | Beide Handler erhalten `profile_cache` per Konstruktor | Handler-Konstruktoren/ServiceContainer |
| 4 | `ProfileCacheManager` besitzt `invalidate_profile(user_id, trigger_type, trigger_source_id)` | kanonische Implementierung |

## Hypothese und Falsifizierung

**Hypothese:** Der Pylance-Fehler und die wirkungslose Invalidierung stammen ausschließlich vom veralteten dynamischen Import. Direkter Aufruf des injizierten Cache-Vertrags invalidiert Sync und Async korrekt.

**Falsifizierung:** Ein Unit-Test mit injiziertem Cache-Spy muss exakt einen kanonischen Aufruf beobachten; Pylance oder Suche darf danach den Import weiterhin nicht finden.

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|-------------------|------------|-------------------|--------|
| 1 | Cache-Fehler stört Nachrichtenpfad | niedrig | mittel | Handler loggt Fehler und behält bisherigen best-effort-Vertrag | gemindert |
| 2 | Async-/Sync-Drift | niedrig | mittel | parametrisierter Test über beide Klassen | geschlossen |

## Sicherheits- & PII-Implikationen

Es werden keine neuen Daten gespeichert. User-ID und Session-ID werden wie bisher ausschließlich an den lokalen Profil-Cache übergeben.

## Rollback-Strategie

Gezielte Backups der beiden Handler und Testdatei unter `backups/psych_profile_cache_import_20260804/`.

## Änderungen und Testergebnisse

- Reproduktion vor Fix: `test_profile_cache_handler_invalidation.py` meldete `2 failed, 2 passed`; Sync und Async riefen den Cache nicht auf.
- Nicht existenten Import und integrationsfremdes Capability-Flag aus beiden Handlern entfernt.
- Injizierten `ProfileCacheManager.invalidate_profile()` direkt als kanonischen Vertrag verwendet.
- Generische Silent-Fallbacks durch nicht-fatale Warning-Protokollierung ersetzt.
- Fokussierter Vertrag: `6 passed`.
- Handler-/Cache-Regression: `16 passed`.
- Breite Psych-/Profil-Regression: `83 passed`.
- Pylance `textDocument/diagnostic`: ursprünglicher Missing-Import-Fehler in beiden Handlern entfernt; `get_errors`: keine Fehler in geänderten Dateien.
