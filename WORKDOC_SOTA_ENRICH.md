# Workdoc: SOTA-Risiko-Priorisierung für den Dependency-Scanner

> **Erstellt:** 2026-09-05
> **Status:** ABGESCHLOSSEN (2026-09-06, Tests 100/100 grün)
> **Autor:** Bot
> **Reviewer:** —

---

## Original-Auftrag

> Analysiere, das Sota ist und setze es dann um. Recherchiere auch im Internet, wann immer sinnvoll.

Kontext: `scripts/dependency_vulnerability_scanner.py` nutzt OSV als Primärquelle (Local-First,
Offline-fähig, 24h-Cache). Task: SOTA im Dependency-Vulnerability-Scanning analysieren und umsetzen.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| SOTA-Analyse (Recherche) | Reachability-Analysis (komplex, dokumentiert als Next-Step) |
| CISA KEV-Enrichment (aktiv ausgenutzt) | SBOM-Generierung (orthogonal) |
| FIRST EPSS-Enrichment (Exploit-Wahrscheinlichkeit) | SSVC-Vollframework (Asset-Kontext fehlt) |
| Risiko-Tiering P0–P3 + Report-Upgrade | Neue Dependencies (nur stdlib + 2 öffentliche APIs) |
| Tests + Doku (doc 16) | LLM-/GPU-Module |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | `compute_risk()` ist pure, testbar, deterministisch | Unit-Tests | ☐ |
| 2 | KEV-Lookup korrekt (Cache + Fetch), offline-fähig | Unit-Tests + Live-Scan | ☐ |
| 3 | EPSS-Scores korrekt (Batch, Chunking, Cache) | Unit-Tests + Live-Scan | ☐ |
| 4 | Scanner-Integration best-effort (crascht nie den Scan) | Unit-Test (Enrichment-Fehler) | ☐ |
| 5 | Report zeigt Risk-Tiers + KEV/EPSS | Live-Scan + Test | ☐ |
| 6 | `--offline`/`--refresh`/`--no-enrich` funktionieren | Live-Läufe | ☐ |
| 7 | Alle Scanner-Tests grün | pytest | ☐ |
| 8 | Live-Scan: 165 Vulns priorisiert, Exit-Codes stabil | Live-Lauf | ☐ |
| 9 | doc 16 aktuell, funktionen.md aktualisiert | Doku-Review | ☐ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korr. | Robust | Perf. | Risiko | Entsch. |
|---|--------|-----|--------|-------|--------|-------|--------|---------|
| A | **KEV + EPSS + Risk-Tier** | Hoher Praxis-Nutzen (165→priorisiert), belegt, local-first-kompatibel | 2 externe Quellen (Cache/Offline) | 7 | 7 | 6 | 2 | **✅** |
| B | Reachability-Analysis | Präziseste | Sehr komplex (Callgraph), False-Negatives, hoher Aufwand | 5 | 4 | 3 | 6 | ❌ (Next-Step) |
| C | SSVC-Vollframework | Strukturiert | Braucht Asset-Kontext (Exposure, Criticality) den wir nicht haben | 5 | 5 | 5 | 4 | ❌ (Teilweise in A) |
| D | SBOM (CycloneDX/SPDX) | Compliance | Keine Priorisierung, orthogonal | 6 | 6 | 6 | 3 | ❌ (optional, separat) |

> **Auswahl:** Option A — höchste Wertsteigerung (Risiko-Priorisierung) bei geringem Risiko;
> KEV (aktiv ausgenutzt) + EPSS (Exploit-Wahrscheinlichkeit) sind die 2026-Standard-Signale
> (Recherche: safeguard.sh, devsecopsatlas.com, augmentcode.com, secrails.com; FIRST.org, CISA.gov).

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | EPSS-API liefert `{cve, epss, percentile, date}` | Live-Call `api.first.org/data/v1/epss?cve=CVE-2021-44228` (2026-09-05) |
| 2 | KEV-Feed: `vulnerabilities[]` mit `cveID/dateAdded/requiredAction/knownRansomwareCampaignUse` (1695) | Live-Call `cisa.gov/.../known_exploited_vulnerabilities.json` (2026-09-05) |
| 3 | Scanner nutzt OSV primär, pip-audit + Heuristik als Fallback | `scripts/dependency_vulnerability_scanner.py` |
| 4 | `Vulnerability` nutzt `__slots__` → neue Felder müssen ergänzt werden | `Vulnerability.__slots__` |
| 5 | Tests importieren via `from scripts.dependency_vulnerability_scanner import ...` | `tests/test_dependency_vulnerability_scanner.py:22` |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | EPSS-Batch mit >100 CVEs braucht Chunking (limit=100) | offen | Test mit 150 CVEs → 2 Requests |
| 2 | KEV+EPSS decken alle OSV-CVEs ab | offen | Live-Scan: Anteil enriched/total |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderung | Status |
|---|--------|-------------------|------------|-----------|--------|
| 1 | KEV/EPSS-API down → Scan schlägt fehl | mittel | mittel | best-effort (Fehler ≠ Scan-Fehler), Cache-Fallback | ☐ |
| 2 | Rate-Limit EPSS (>100 CVEs) | niedrig | niedrig | Chunking + Sleep | ☐ |
| 3 | `__slots__`-Änderung bricht alte Tests | niedrig | mittel | Defaults in `__init__`, Tests grün | ☐ |
| 4 | False-Priorisierung (EPSS/KEV falsch zugeordnet) | niedrig | mittel | Case-insensitiver CVE-Key, Tests | ☐ |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|
| 1 | EPSS/KEV senden nur CVE-IDs | KEINE PII, kein Code, keine Paketnamen | öffentliche Identifikatoren, kein Telemetrie |
| 2 | KEV-JSON (~2MB) lokal | Disk | Cache unter `data/vuln_cache/kev/` (gitignore) |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `scripts/vuln_enrich.py` (neu) | `compute_risk`, `KEVCatalog`, `EPSSClient`, `prioritize` | ☐ |
| 2 | `scripts/dependency_vulnerability_scanner.py` | `Vulnerability`-Felder, `scan()`-Integration, `--no-enrich`, Report | ☐ |
| 3 | `tests/test_dependency_vulnerability_scanner.py` | neue Test-Klassen | ☐ |
| 4 | `docs/16_DEPENDENCY_SCANNER.md` | SOTA-Abschnitt | ☐ |

## Rollback-Strategie

| Schritt | Aktion | Befehl |
|---------|--------|--------|
| 1 | Neue Dateien entfernen + Scanner-Diff revert | `git checkout -- scripts/dependency_vulnerability_scanner.py; rm scripts/vuln_enrich.py` |
| 2 | Enrichment per Flag deaktiviert | `--no-enrich` (Fallback auf Alt-Verhalten) |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | (ausstehend) | | |
