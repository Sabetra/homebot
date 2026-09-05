<!-- last-verified: 2026-09-05 -->

# 16 – Dependency Vulnerability Scanner

## Zweck

Lokaler, privacy-preserving Security-Scanner für Python-Dependencies. Prüft `requirements.txt` auf bekannte CVEs. Primary-Quelle ist die **OSV-Datenbank** (api.osv.dev) mit lokalem per-Package-Cache — der Scanner bleibt nach dem ersten Fetch voll offline-fähig.

## SOTA-Basis

- **OSV (api.osv.dev/v1/query)** — Primary-Engine, direkt abgefragt (kein pip-audit-Subprocess)
- **pip-audit** (pypa/advisory-database) — Zweitquelle, nur wenn OSV komplett down + kein Cache
- **Heuristik** — letzter Fallback (7 Pakete), immer als Scan-Fehler markiert
- **OSV-Schema** — Structured vulnerability data format

Recherche: 2026-07-31 via DuckDuckGo; OSV-Refactor: 2026-09-05

## Warum OSV statt pip-audit?

pip-audit hängt in dieser Umgebung (Subprocess + Advisory-DB-Update, mehrere
Minuten ohne Ergebnis). Der direkte OSV-API-Call ist zuverlässig und schnell
(53 Packages in ~16s, danach <1s aus dem Cache). OSV ist die gleiche Quelle,
die pip-audit intern nutzt, daher kein Qualitätsverlust.

## Architektur

```
requirements.txt
    |
    v
parse_requirements()  ----  List[Tuple[str, str]]
    |
    v
to_concrete_version()  ----  OSV braucht konkrete Versionen (Untergrenze)
    |
    v
VulnerabilityScanner.scan()
    |
    +-- OSV (Primary): _scan_with_osv() -> api.osv.dev/v1/query
    |       +-- frischer per-Package-Cache  (offline, <1s)
    |       +-- sonst Live-Query, dann Cache-Update
    |
    +-- pip-audit (Zweitquelle, subprocess, sandboxed)
    |       [nur wenn OSV down + kein Cache]
    |
    +-- Heuristic Fallback (7 Pakete, IMMER als Fehler markiert)
    |
    v
ScanResult -> ReportFormatter
    |
    +-- Console (human-readable)
    +-- JSON (CI/CD, maschinenlesbar)
```

## Sicherheit

| Aspekt | Maßnahme |
|--------|---------|
| Network | OSV-API (api.osv.dev) ist die einzige externe Quelle; `--offline` erzwingt reines Cache-Verhalten; frischer Cache macht den Scan voll offline-fähig |
| Sandbox | pip-audit-Subprocess (Zweitquelle) hat `timeout`, `capture_output=True`, `text=True`; OSV-Query hat Timeout (15s) |
| Code | Kein `eval()`, kein `exec()`, kein `importlib`; nur Stdlib (urllib, json, re) |
| Privacy | Keine Telemetrie; OSV-Query sendet nur Paketname + Version; kein Schadcode-Download |
| Cache | per-Package-OSV-Cache in `data/vuln_cache/osv/`, 24h TTL |

## Privacy

- Einzige externe Quelle: OSV-API (Paketname + konkrete Version, keine PII)
- Nach dem ersten Fetch: voll offline-fähig per `--offline`
- Cache verbleibt auf lokalem Dateisystem
- Keine Secrets in Logs

## Usage

```powershell
# Basis-Scan (OSV, nutzt frischen Cache falls vorhanden)
python scripts/dependency_vulnerability_scanner.py

# Custom requirements
python scripts/dependency_vulnerability_scanner.py -r my_requirements.txt

# Strict-Mode (Exit 1 bei ANY Vulnerability, für CI/CD)
python scripts/dependency_vulnerability_scanner.py --strict

# JSON-Report
python scripts/dependency_vulnerability_scanner.py -o report.json

# OFFLINE: nur lokalen Cache, KEIN Netzwerk (OSV wird nicht kontaktiert)
python scripts/dependency_vulnerability_scanner.py --offline

# REFRESH: Cache-TTL ignorieren, OSV zwangsweise aktualisieren
python scripts/dependency_vulnerability_scanner.py --refresh

# Cache-Verzeichnis überschreiben
python scripts/dependency_vulnerability_scanner.py --cache-dir C:\tmp\cache
```

## Offline / Refresh-Verhalten

| Modus | Netzwerk | Cache |
|-------|----------|-------|
| Default | frischer Cache -> kein; sonst ja | 24h TTL, dann Refresh |
| `--offline` | **nie** | frischen oder (markierten) Stale-Cache; ohne Cache -> Fehler |
| `--refresh` | **immer** | Cache wird neu geschrieben |

Exit-Codes: `0` = ok, `1` = Vulns gefunden (`--strict`), `2` = Scan-Fehler
(z.B. offline ohne Cache oder Fallback auf Heuristik).

## Tests

```powershell
python -m pytest tests/test_dependency_vulnerability_scanner.py -v
```

60 Tests: Parser (10), Models (7), AdvisoryCache (6), ExtractSeverity (8),
Heuristik (3), Reporter (6), OSV-Scan (5), OSV-Cache (2),
to_concrete_version (6), OSV-Severity (7).

## CI/CD-Integration

```yaml
# Beispiel für GitHub Actions
- name: Security Scan
  run: python scripts/dependency_vulnerability_scanner.py --strict
  continue-on-error: false
```

## RAG-Entscheidung

**NICHT in RAG aufnehmen.** Begründung:
- Security-Scanner ist ein **Tool**, kein Wissensdokument
- Ergebnisse sind zeitabhängig (neue CVEs täglich)
- RAG wäre schnell veraltet
- Stattdessen: Regelmäßiger CLI-Call in CI/CD oder manuell

## Limitationen

- Heuristik-Fallback ist **kein Ersatz** für OSV (nur 7 bekannte Patterns) und wird als Scan-Fehler markiert
- OSV-Query sendet Paketname + Version an api.osv.dev (keine PII, aber kein 100% lokaler Scan)
- Scan prüft nur Python-Dependencies (kein npm, kein NuGet, kein Cargo)
- Version aus einem Bereich (z.B. `>=1.24,<2.0`) wird als **Untergrenze** geprüft (konservativ)

## Wartung

- Bei jedem `requirements.txt`-Update neu scannen (oder `--refresh`)
- Cache-TTL: 24h (via `DEFAULT_CACHE_TTL_HOURS`)
- Heuristik-Liste bei neuen kritischen CVEs erweitern