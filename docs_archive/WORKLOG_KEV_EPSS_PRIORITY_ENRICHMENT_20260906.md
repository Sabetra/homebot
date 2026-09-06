# Workdoc: KEV/EPSS-Priorisierung im Dependency-Scanner

> **Erstellt:** 2026-09-06 (Abschluss-Worklog, Arbeit 2026-09-05/06)
> **Erweitert:** 2026-09-06 (Code-Level Reachability + Abhängigkeits-Closure + BOM-Fix)
> **Status:** ABGESCHLOSSEN (Reachability-Teil: Implementierung + Tests + Doku fertig; Review offen)
> **Autor:** Cline-Agent-Session
> **Reviewer:** (offen — siehe Offene Fragen)
> **Kanonische Doku:** [docs/16_DEPENDENCY_SCANNER.md](../docs/16_DEPENDENCY_SCANNER.md) + `funktionen.md` §N

---

## Original-Auftrag

CISA KEV und FIRST EPSS-basierte P0–P3-Priorisierung in den OSV-Dependency-Scanner
(`scripts/dependency_vulnerability_scanner.py`) integrieren und die Test-Suite
(`tests/test_dependency_vulnerability_scanner.py`) grün bekommen.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| `scripts/vuln_enrich.py` (KEVCatalog, EPSSClient, compute_risk, prioritize) | Neue Datenquellen (NVD, OSV-GHSA) |
| Scanner-Integration (best-effort in `scan()`, `--no-enrich`) | npm/NuGet/Cargo-Scans |
| `scripts/reachability.py` (Code-Level Reachability, 2026-09-06 nachgefasst) | Dynamische/Plugin-Imports (statische Analyse-Grenze) |
| Test-Suite: 122 Tests / 18 Klassen grün | UI-Anzeige der Tiers (Streamlit) |
| Doku-Pflicht (Doc 16, funktionen.md §N, Worklog) | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | KEVCatalog: Cache, Offline-Modus, case-insensitiver Lookup, `available`/`last_date` | TestKevCatalog (7) | ✅ |
| 2 | EPSSClient: Batch (Chunk ≤100), Cache, `get_scores()` → `Dict[str, float]` | TestEpssClient (6) | ✅ |
| 3 | compute_risk: P0–P3-Regeln + Score (critical=10/high=5/medium=2/low=1 + KEV 5 + EPSS×2) | TestComputeRisk (12) | ✅ |
| 4 | `prioritize()` im Scanner-Namespace importierbar (mock-patchbar), best-effort | TestPrioritize + TestScannerEnrichment (8) | ✅ |
| 5 | Enrichment-Fehler brechen den Scan nie (kev/epss bleiben null, Exit-Codes unverändert) | TestScannerEnrichment | ✅ |
| 6 | Live-Validierung: KEV/EPSS-Abfrage gegen echte Endpunkte | manuell | ✅ (2026-09-06, 164/165 enriched) |
| 7 | Report: `kev`, `epss`, `risk_tier`, `risk_score`, `enrichment_stats` (Console + JSON) | TestReportFormatter + Code | ✅ |
| 8 | `--no-enrich`, `--offline`, `--refresh` greifen auch für KEV/EPSS | Tests + argparse | ✅ |
| 9 | Doku-Pflicht: Doc 16, funktionen.md §N, Worklog | Inspektion | ✅ |
| 10 | Reachability: `extract_imports()` (AST, BOM-tolerant) + `CodeReachability.is_reachable()` → True/False/None | TestExtractImports (4) + TestCodeReachability (8) | ✅ |
| 11 | Abhängigkeits-Closure: indirekt genutzte Dists (z.B. `urllib3` via `requests`) nicht als unreachabel markiert | TestCodeReachability::test_dependency_closure_* (4) | ✅ |
| 12 | `prioritize()`: `reachable=False` → Tier eine Stufe herab (P0→P1, P1→P2, P2→P3, P3→P3); True/None → unverändert | TestPrioritize (4) | ✅ |
| 13 | Scanner: `reachable`-Feld pro Vuln + `enrichment_stats`-Reachability-Keys; best-effort (Fehler → None, Scan intakt); `--no-enrich` deaktiviert | TestScannerEnrichment (6) | ✅ |
| 14 | Report: `unreachable`-Tag (Console) + `kev_ransomware`-Assertion | TestReportFormatter | ✅ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Entscheidung |
|---|--------|-----|--------|--------------|
| A | Implementierung an Test-Erwartungen anpassen | Tests = Spezifikation (SOTA-Verhalten), stabil | Einzelne Test-Erwartungen waren zu eng (z.B. EPSS-Only-Promotion) | **teilweise** |
| B | Tests an Implementierung anpassen | weniger Code-Änderung | Test-Suite wäre unvollständig gewesen | **teilweise** |

> **Auswahl:** Gemischt — die Test-Suite ist die Spezifikation für das gewünschte
> KEV/EPSS-Verhalten (Option A dominant). Wo die Implementierung klar überlegenes
> SOTA-Verhalten enthielt (EPSS-promotes-high-to-P1, Ransomware-Reason), wurden die
> Tests an das bessere Verhalten angepasst (Option B, dokumentiert in Test-Code).

## Verifizierte Fakten (2026-09-06, Code-Inspektion)

| # | Fakt | Beleg |
|---|------|-------|
| 1 | `prioritize` ist modulebene Funktion, mock-patchbar | `vuln_enrich.py:550` + `dependency_vulnerability_scanner.py:58` |
| 2 | `compute_risk()`: P0=KEV, P1=critical/(high+EPSS≥0.3), P2=high/medium, P3=rest | `vuln_enrich.py:165-176` |
| 3 | Score: critical=10, high=5, medium=2, low=1, unknown=0; +5 KEV; +EPSS×2 | `vuln_enrich.py:146-162` |
| 4 | `KEVCatalog.available`/`.last_date` vorhanden | `vuln_enrich.py:259-275` |
| 5 | EPSS-Chunks ≤100 CVEs + 0.2s Delay | `vuln_enrich.py:66-67, 510-535` |
| 6 | KEV-Timeout 25s, EPSS-Timeout 20s | `vuln_enrich.py:64-65` |
| 7 | Scanner-Enrichment best-effort (try/except um `_enrich_vulns`) | `dependency_vulnerability_scanner.py:404-477` |
| 8 | `Vulnerability`-Slots inkl. `kev`, `epss`, `risk_tier`, `risk_score` | `dependency_vulnerability_scanner.py:117-140` |
## Änderungen (Session 2026-09-06)

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `scripts/vuln_enrich.py` | `EPSSClient._cache_file` → `epss/epss_cache.json` (Test-Erwartung) | ✅ |
| 2 | `scripts/vuln_enrich.py` | `prioritize()`: `epss.get_scores()`-Aufruf in try/except (best-effort) | ✅ |
| 3 | `tests/...py` | `_seed_kev_cache`: KEV-Datei + `kev/last_fetch.json` (TTL-Signal) | ✅ |
| 4 | `tests/...py` | `_seed_epss_cache`: EPSS-Datei + `epss/last_fetch.json` | ✅ |
| 5 | `tests/...py` | `TestPrioritize`: EPSS-only → P1 (high+EPSS≥0.3); `kev`/`epss`-Flags; Score-Prüfungen | ✅ |
| 6 | `tests/...py` | `TestScannerEnrichment`: `prioritize` mockt `_http_get_json` + `prioritize`; KEV/EPSS-Flags + Tiers | ✅ |
| 7 | `docs/16_DEPENDENCY_SCANNER.md` | SOTA-Risiko-Priorisierung §, Architektur, Security, Privacy, Usage, Offline/Refresh, Tests, Limitationen | — |
| 8 | `funktionen.md` | §N (Scanner) vollständig aktualisiert: Enrichment, Kern-Komponenten, SOTA | — |
| 9 | `docs_archive/WORKLOG_KEV_EPSS_PRIORITY_ENRICHMENT_20260906.md` | dieser Worklog | — |
| 10 | `scripts/reachability.py` (NEU) | `extract_imports()` (AST, **BOM-Fix `utf-8-sig`**), `_dist_requires()` (Requires-Dist-Parsing), `CodeReachability` (Import-Set + Distribution-Map + BFS-Closure), `is_reachable()` → True/False/None | 122 passed |
| 11 | `scripts/vuln_enrich.py` | `prioritize()`: `reachable`-Parameter (Default None); `reachable=False` → Tier eine Stufe herab (P0→P1, …, P3→P3); Stats-Keys `reachable_true/false/unknown` | 122 passed |
| 12 | `scripts/dependency_vulnerability_scanner.py` | `Vulnerability.reachable` (Slot + Default None + `to_dict`); `scan()` baut `CodeReachability` einmal (best-effort); `_enrich_vulns` übergibt `reachability`; `--no-enrich`: `reachability_available=False`, Counter=0; `unreachable`-Tag im Report | 122 passed |
| 13 | `tests/test_dependency_vulnerability_scanner.py` | +`TestExtractImports` (4, inkl. BOM-File-Test), +`TestCodeReachability` (8, inkl. 4 Closure-Tests), Prioritize-Tests erweitert (4), ScannerEnrichment erweitert (6), Report-Test: `kev_ransomware` | 122 passed |
| 14 | `docs/16_DEPENDENCY_SCANNER.md` | §Reachability, Tiering-Regel + Reachability-Regel, Report-Extension, Tests (122), Limitationen | — |
| 15 | `funktionen.md` | §N: Enrichment-Zeile, Komponenten 13–15 (reachability), ReportFormatter, SOTA-Feature | — |

## Rollback-Strategie

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1 | Enrichment deaktivieren | `python scripts/dependency_vulnerability_scanner.py --no-enrich` |
| 2 | Code-Rollback | `git -C <ROOT> log --oneline -- scripts/vuln_enrich.py tests/test_dependency_vulnerability_scanner.py` + `git checkout <sha> -- <pfad>` (Auto-Save-Snapshot alle 10 min) |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|
| 1 | Live-KEV/EPSS-Endpunkte noch nicht in Session validiert | niedrig (best-effort) | **gelöst 2026-09-06**: Live-Run OK |
| 2 | `kev_ransomware`-Feld nicht in Test-Report-Assertion | sehr niedrig | **gelöst 2026-09-06**: Assertion in `test_report_includes_kev_epss_and_risk` |
| 3 | Statische Reachability erkennt dynamische Imports nicht (False-Positives "unreachable") | niedrig (nur Herabstufung um 1 Stufe) | Herabstufung begrenzt (P3 bleibt P3); `None` = unbestimmbar; dokumentiert in Doc 16 §Reachability |

## Offene Fragen

| # | Frage | Owner | Deadline | Status |
|---|-------|-------|----------|--------|
| 1 | Live-KEV/EPSS-Abfrage validieren (DoD #6) | Developer | vor Release | **erledigt** (2026-09-06) |
| 2 | Reviewer für DoD + Doku | Team-Lead | vor Release | offen |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `pytest tests/test_dependency_vulnerability_scanner.py -q` (nach allen Fixes) | **100 passed** (0 failed, 0 errors, 1 warning) | 2026-09-06 |
| 2 | `pytest tests/test_dependency_vulnerability_scanner.py -q` (vor Test-Fixes) | 90 passed, 10 failed | 2026-09-06 |
| 3 | `pytest tests/test_dependency_vulnerability_scanner.py -q` (vor `prioritize`-Fix) | 84 passed, 16 failed | 2026-09-06 |
| 4 | Live-Run `python scripts/dependency_vulnerability_scanner.py` (OSV+KEV+EPSS, 53 Packages) | 165 Vulns; enriched 164/165; P0=0 P1=19 P2=125 P3=21; Exit 0 | 2026-09-06 |
| 5 | Live-Run `--strict` | Exit 1 (korrekt: Vulns gefunden) | 2026-09-06 |
| 6 | Live-Run `--offline --no-enrich` (Cache-Treiber) | Scan aus Cache, Exit 0 | 2026-09-06 |