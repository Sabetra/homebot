<!-- last-verified: 2026-09-04 -->

# Security

Dies ist ein **Local-First, local-only** Projekt (Copyright: Michaël Artebas,
AGPL-3.0). Sicherheit ist by-design: keine Cloud-LLM-Calls im Produktivpfad,
keine Telemetrie, PII- und Finance-Daten bleiben lokal.

## Sicherheitsmodell
- **Local-only Runtime:** Der Produktivpfad ruft keine Cloud-LLMs auf.
- **Netzwerk:** Egress ist nur im explizit opt-in-Modus erlaubt;
  `APP_LOCAL_ONLY=1` erzwingt deny-by-default (zentral: `utils/runtime_policy.py`).
- **PII-Schutz:** Über `pii_protection/` – private Daten werden vor der
  Persistierung/Weitergabe geschützt.
- **Finance-Daten:** Bleiben lokal (SQLite); kein Upload, keine Telemetrie.
- **Abhängigkeiten:** Lizenzen via `LICENSES.md` + `scripts/check_licenses.py`;
  Vulnerabilities via `scripts/dependency_vulnerability_scanner.py`.

## Supportierte Komponenten
| Komponente | Status |
|-----------|--------|
| Python | 3.12 (Virtuelle Umgebung, s. `docs/05_DEVELOPER_GUIDE.md`) |
| LLM-Inference | llama-cpp-python (lokal, GGUF) |
| GPU | NVIDIA CUDA (Referenz-Setup: RTX 4090 [LLM] + RTX 3060 Ti [AUX]) |

Entwickler-Maschine und -Venv sind **kein** Teil des Support-Vertrags.

## Dependency-Security & Advisory-Status
Stand 2026-09-04 (strict `pip-audit`-Scan, PyPA-Advisory-DB + GitHub-Advisories):

| Paket | Befund | Maßnahme |
|-------|--------|----------|
| gitpython 3.1.57 | 10 Advisories (1 CRITICAL 9.8, 7 HIGH, 2 MEDIUM) | → **3.1.61** (≥ 3.1.59) |
| pypdf 6.14.2 | 6 Advisories (MEDIUM, DoS-Klasse) | → **6.17.0** (≥ 6.16.1, requirements-Pin) |
| h2 4.4.0 | 1 Advisory (MEDIUM, Request-Smuggling) | → **4.4.1** |
| diskcache 5.6.3 | 1 Advisory (MEDIUM, unsafe pickle-Deserialisierung) | **Risikofreigabe:** kein gepatchtes Release; lokaler Cache nur auf local-only-Maschine; nachführen, sobald ein Fix erscheint |

- **False-Negative-Schutz (2026-09-04):** `scripts/dependency_vulnerability_scanner.py`
  meldet einen pip-audit-Fallback-Scan nicht mehr als „0 Vulnerabilities"
  (Exit 2 statt Exit 0). Regressionstest:
  `tests/test_dependency_vulnerability_scanner.py::test_fallback_marks_scan_as_error`.
- Detail-Report (lokal, git-ignoriert): `monitoring/dependency_audit/audit_20260904.md`.

## Schwachstellen melden
- Melde Sicherheits-Schwachstellen **diskret** über GitHub:
  `Settings → Security → Private vulnerability reporting`
  (oder direkt an den Maintainer Sabetra).
- Öffne für Sicherheitsprobleme **keine** öffentlichen Issues.
- **Keine** öffentliche Offenlegung, bis ein Fix verfügbar ist.
- Bitte liefere: betroffene Komponente/Datei, Reproduktionsschritte,
  Schweregrad und – falls vorhanden – einen POC.
- Es gibt **keinen** Bug-Bounty.

## Selbst-Checks
```powershell
# Dependency-Vulnerability-Scan (lokal, privacy-preserving)
python scripts/dependency_vulnerability_scanner.py
python scripts/dependency_vulnerability_scanner.py --strict   # CI: Exit 1 bei ANY vuln

# Lizenz-Compliance
python scripts/check_licenses.py --strict
```

## Daten & Backups
- Alle App-Daten (DBs, Modell-Caches) liegen **außerhalb des Repositories**
  (git-ignoriert); Pfad-Auflösung: `utils/db_path_resolver.py`.
- Optional: tägliches konsistentes Backup via `scripts/db_backup.py`
  (`VACUUM INTO`, 7 Tagesstände).
- **Grenze:** Lokale Backups schützen gegen
  Softwarefehler/Korruption, **nicht** gegen Plattenausfall.
- Der Psycho-Schlüssel wird mitgesichert; ohne ihn ist die verschlüsselte
  DB wertlos.

## Keine Secrets im Repository
- Keine API-Keys, Tokens oder Zertifikate in Code oder Doku.
- Secrets gehören in lokale, git-ignorierte Dateien/Umgebung.
