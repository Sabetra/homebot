<!-- last-verified: 2026-07-31 -->

# 16 – Dependency Vulnerability Scanner

## Zweck

Lokaler, privacy-preserving Security-Scanner für Python-Dependencies. Prüft `requirements.txt` auf bekannte CVEs **ohne Cloud-Calls** — konform mit Local-First-Prinzip.

## SOTA-Basis

- **pip-audit** (pypa/advisory-database) — Primary-Engine, nutzt Python-Advisory-DB
- **OSV-Schema** — Structured vulnerability data format
- **safety-check** — Referenz für CLI-UX und Reporting

Recherche: 2026-07-31 via DuckDuckGo (`@fetch-mcp` MCP-Server)

## Architektur

```
requirements.txt
    |
    v
parse_requirements()  ----  List[Tuple[str, str]]
    |
    v
VulnerabilityScanner.scan()
    |
    +-- pip-audit (Primary, subprocess, sandboxed)
    |
    +-- Heuristic Fallback (wenn pip-audit nicht installiert)
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
| Network | **Kein externer Call** — pip-audit arbeitet lokal; `--skip-db-update` erzwingt Offline-Modus |
| Sandbox | Alle `subprocess.run()` Calls haben `timeout`, `capture_output=True`, `text=True` |
| Code | Kein `eval()`, kein `exec()`, kein `importlib` |
| Privacy | Keine Telemetrie, keine Daten nach außen, kein Schadcode-Download |
| Cache | Advisory-Daten lokal in `data/vuln_cache/`, 24h TTL |

## Privacy

- **Zero external network calls** im Produktivpfad
- Alle Scans lokal
- Cache verbleibt auf lokalem Dateisystem
- Keine Secrets in Logs

## Usage

```powershell
# Basis-Scan
python scripts/dependency_vulnerability_scanner.py

# Custom requirements
python scripts/dependency_vulnerability_scanner.py -r my_requirements.txt

# Strict-Mode (Exit 1 bei ANY Vulnerability, für CI/CD)
python scripts/dependency_vulnerability_scanner.py --strict

# JSON-Report
python scripts/dependency_vulnerability_scanner.py -o report.json

# Cache aktualisieren
python scripts/dependency_vulnerability_scanner.py --update-cache
```

## Tests

```powershell
python -m pytest tests/test_dependency_vulnerability_scanner.py -v
```

43 Tests: Parser (10), Models (7), Cache (6), Severity (8), Heuristik (3), Reporter (6), Integration (3).

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

- Heuristik-Fallback ist **kein Ersatz** für pip-audit (nur 7 bekannte Patterns)
- pip-audit's Advisory-DB muss aktuell sein (manuell via `pip install --upgrade pip-audit`)
- Scan prüft nur Python-Dependencies (kein npm, kein NuGet, kein Cargo)

## Wartung

- Bei jedem `requirements.txt`-Update neu scannen
- pip-audit regelmäßig updaten
- Heuristik-Liste bei neuen kritischen CVEs erweitern