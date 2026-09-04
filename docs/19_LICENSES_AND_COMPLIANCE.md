<!-- last-verified: 2026-08-30 -->

# 19 – Lizenzen & Compliance

> **Zweck:** Single Source of Truth für die Lizenzierung dieses Projekts, die
> Third-Party-Lizenz-Inventarisierung und den Compliance-Workflow.
> **Copyright-Inhaber:** Michaël Artebas · **Projekt-Lizenz:** AGPL-3.0

---

## 1. Projekt-Lizenz: AGPL-3.0

- Volltext: [`LICENSE`](../LICENSE) (GNU Affero General Public License v3.0)
- Copyright: Michaël Artebas
- **Warum AGPL-3.0?** Das Projekt enthält und kombiniert bereits AGPL-/GPL-
  komponenten (u. a. PyMuPDF, pymupdf4llm). AGPL-3.0 ist die kompatible,
  strong-copyleft-Oberlizenz: Die Kombination bleibt erlaubt und das Ergebnis
  bleibt AGPL-3.0. Ein permissives Label (MIT/Apache) wäre hier rechtlich
  unkorrekt.
- **Reichweite:** Deckt den Code dieses Repositories ab. **Nicht** abgedeckt:
  Modell-Gewichte (siehe §5) und Betriebssystem-/Hardware-Komponenten.

### AGPL-Praktika (Kurzform)
- Der Source Code bleibt unter AGPL-3.0 verfügbar.
- Bei network-basielter Bereitstellung (z. B. Hosten eines Bot-Endpoints für
  Dritte) gelten die Affero-Klauseln (§13): Quelltextverfügbarkeit.
- Reine lokale Nutzung (dieses Projekt) ist davon unberührt.

---

## 2. Third-Party-Inventar: `LICENSES.md`

- Generiert: [`scripts/generate_licenses.py`](../scripts/generate_licenses.py)
  (100 % lokal, Python-Stdlib, deterministisch)
- Quelle: Paket-Metadaten (`*.dist-info/METADATA`) der Produktiv-Venv
  `venv_bot_20260802` + `requirements.txt` / `requirements-dev.txt`
- **Kein Netzwerk, keine Telemetrie** – läuft offline unter jedem Python ≥ 3.9.

### Struktur (Stand 2026-08-30)
| Abschnitt | Anzahl | Bedeutung |
|-----------|--------|-----------|
| Runtime – direkt | 52 | in `requirements.txt` |
| Runtime – transitiv | 161 | aus Runtime-Paketen erreichbar |
| Dev-only | 48 | nur in `requirements-dev.txt`, nie verteilt |
| UNKNOWN / needs-review | 0 | Policy: keine offenen Punkte |

### Lizenz-Quellen (Priorität)
1. PEP 639 `License-Expression`
2. `License :: OSI Approved :: …`-Klassifizierer (3-teilig bevorzugt)
3. Legacy-`License:`-Feld
4. `MANUAL_OVERRIDES` (manuell verifiziert, siehe Generator)
5. `UNKNOWN` (wird vom Checker geflaggt)

Die beste Klassifizierung über alle Quellen gewinnt.

---

## 3. Klassifizierung & AGPL-Kompatibilität

| Klasse | Beispiele | AGPL-kompatibel |
|--------|-----------|-----------------|
| permissive | MIT, Apache-2.0, BSD, ISC, Zlib, PSF, CC0 | ✓ |
| weak-copyleft | LGPL, MPL | ✓ |
| strong-copyleft | GPL-2/3, AGPL-3 | ✓ |
| needs-review | nicht-standard / proprietär | ✗ manuelle Prüfung |
| unknown | keine Lizenz-Metadaten | ✗ manuelle Prüfung |

AGPL-3.0 ist strong-copyleft: Kombination mit permissiven, LGPL-, MPL- oder
GPL-Komponenten ist erlaubt (Ergebnis bleibt AGPL-3.0). Das Umgekehrte
(AGPL-Code in ein permissives Projekt) würde dieses „infizieren" – daher ist
dieses Projekt AGPL-3.0.

---

## 4. Compliance-Workflow

```
1. Abhängigkeit installieren/entfernen
2. python scripts/generate_licenses.py          → LICENSES.md neu
3. python scripts/check_licenses.py --strict    → Policy-Gate
4. git commit                                   → Pre-Commit-Hook prüft erneut
```

### Tooling
| Werkzeug | Zweck |
|----------|-------|
| `scripts/generate_licenses.py` | Erzeugt `LICENSES.md` (deterministisch) |
| `scripts/check_licenses.py` | Frische-Check + Policy-Check; `--strict` für CI/Hook |
| `tests/test_licenses_md_up_to_date.py` | pytest-Gate (Frische + keine unknown-Runtime-Lizenzen) |
| `.githooks/pre-commit` | Fail-fast Lizenz-Gate vor dem Release-Gate |

### Manuelle Overrides
Pakete mit unvollständigen Metadaten werden in `MANUAL_OVERRIDES` im Generator
verifiziert (Stand 2026-08-30): `streamlit-option-menu` (MIT), `socksio` (MIT),
`pillow` (PIL, BSD-Style). Nur wirksam, wenn die Metadaten sonst
unknown/needs-review ergeben.

### Unabhängiger Cross-Check: `pip-licenses`
`pip-licenses` (dev-only, `requirements-dev.txt`) dient als **zweite,
unabhängige Quelle**. Konsolidierung (2026-08-30): Einziger UNKNOWN war
`streamlit-option-menu` – bereits per Override als MIT erfasst. Alle übrigen
Lizenzen permissiv oder AGPL-kompatibler Copyleft. Keine Abweichung.

---

## 5. Modell-Gewichte (bewusst ausgenommen)

Modell-Gewichte sind **kein Bestandteil der Repository-Lizenzierung** und
liegen nicht im Repo:

| Modell | Bedingungen | Quelle |
|--------|-------------|--------|
| Gemma 4 12B | Google **Gemma Terms of Use** | vom Nutzer selbst zu beziehen |
| Reranker/Embeddings | je nach Modell (häufig Apache-2.0/MIT) | Hugging Face |
| EasyOCR-Bundled (CRAFT/CRNN) | MIT / Apache-2.0 (laut EasyOCR-Doku) | via easyocr |

> **Compliance-Hinweis:** Die AGPL-3.0 dieses Repositories deckt fremde
> Modell-Gewichte mit eigenen Bedingungen **nicht** ab. Gemma-Gewichte dürfen
> aus diesem Repository nicht weiterverteilt werden; Nutzer beziehen sie
> unter Googles Gemma Terms of Use selbst.

---

## 6. Historie
| Datum | Ereignis |
|-------|----------|
| 2026-08-30 | AGPL-3.0 eingeführt (Copyright Michaël Artebas); Generator, Checker, pytest-Gate, Pre-Commit-Hook, `LICENSES.md` (52/161/48, 0 offen); pip-licenses-Cross-Check; Doku 19 + Root-Dokus (LICENSE, SUPPORT, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY) |
