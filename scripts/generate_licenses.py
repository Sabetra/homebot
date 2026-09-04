"""
License Inventory Generator
===========================

Erzeugt `LICENSES.md` aus den installierten Paket-Metadaten der Produktiv-
Venv. Deterministisch (keine Zeitstempel, stabile Sortierung), damit
`scripts/check_licenses.py` byte-exakt diffen kann.

Lizenz-Quellen (Prioritaet):
    1. PEP 639 `License-Expression`-Metadaten
    2. `License :: OSI Approved :: ...`-Klassifizierer (3-teilig bevorzugt)
    3. Legacy-`License:`-Feld
    4. MANUAL_OVERRIDES (manuell verifiziert, s. Eintrag)
    5. UNKNOWN (wird vom Checker geflaggt)
    Die beste Klassifizierung ueber alle Quellen gewinnt; bei gleicher
    Klassifizierung gilt die hoechste Quelle-Prioritaet.

Klassifizierung (AGPL-3.0-Kompatibilitaets-Perspektive):
    permissive        MIT/Apache/BSD/ISC/Zlib/PSF/CC0/...   -> AGPL-kompatibel
    weak-copyleft     LGPL/MPL                               -> AGPL-kompatibel
    strong-copyleft   GPL/AGPL                               -> AGPL-kompatibel
    needs-review      nicht-standard/proprietar              -> manuelle Pruefung
    unknown           keine Lizenz-Metadaten                 -> manuelle Pruefung

Sicherheit:
    - 100% lokal (kein Netzwerk, keine Telemetrie)
    - nur Python-Stdlib (laeuft unter jedem Python >= 3.9)

Usage:
    python scripts/generate_licenses.py
    python scripts/generate_licenses.py --venv <pfad-zur-venv>
    python scripts/generate_licenses.py --out <pfad>/LICENSES.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _site_packages_of(venv: Path) -> Path | None:
    """Liefert den site-packages-Pfad einer Venv (layout-unabhaengig) oder None."""
    candidates: list[Path] = []
    try:
        candidates.append(
            Path(
                sysconfig.get_path(
                    "purelib", vars={"base": str(venv), "platbase": str(venv)}
                )
            )
        )
    except Exception:  # Schema/Version, die sysconfig in dieser Kombi nicht kennt
        pass
    candidates += [
        venv / "Lib" / "site-packages",
        venv / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def resolve_venv(explicit: Path | None = None) -> Path:
    """Wahlt die zu pruefende Venv (maschinenunabhaengig).

    Funktioniert auch, wenn HOME/USERPROFILE im Aufrufkontext fehlerhaft sind
    (z. B. Git-Bash-Hook mit veraltetem HOME) - es wird nicht blind auf
    Path.home() zugegriffen.

    Reihenfolge:
      1. `explicit` (--venv)
      2. `BOT6_VENV` (Umweltvariable, muss existieren)
      3. Repo-relative Kandidaten: venv_bot_20260802, .venv, venv
         (erster mit vorhandenem site-packages)
      4. laufende Venv (falls ein venv-Interpreter die Skripte ausfuehrt)
      5. Legacy-Default: ~/venv_bot_20260802
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("BOT6_VENV")
    if env:
        env_path = Path(env)
        if env_path.is_dir():
            return env_path
    for name in ("venv_bot_20260802", ".venv", "venv"):
        candidate = REPO_ROOT / name
        if _site_packages_of(candidate) is not None:
            return candidate
    running = Path(sys.prefix)
    if sys.prefix != sys.base_prefix and _site_packages_of(running) is not None:
        return running
    return Path.home() / "venv_bot_20260802"


DEFAULT_VENV = resolve_venv()
DEFAULT_OUT = REPO_ROOT / "LICENSES.md"
DEFAULT_REQUIREMENTS = REPO_ROOT / "requirements.txt"
DEFAULT_DEV_REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

# ---------------------------------------------------------------------------
# Manuelle Overrides fuer Pakete mit unvollstaendigen Metadaten.
# Key: PEP 503 normalisierter Name. Wert: (Lizenz-Label, Klassifizierung, Note).
# Nur wirksam, wenn die Metadaten-Klassifizierung unknown/needs-review ergibt.
# ---------------------------------------------------------------------------
MANUAL_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "streamlit-option-menu": (
        "MIT",
        "permissive",
        "manuell verifiziert 2026-08-30 (PyPI: MIT License-Klassifizierer)",
    ),
    "socksio": (
        "MIT",
        "permissive",
        "manuell verifiziert 2026-08-30 (Projekt deklariert MIT)",
    ),
    "pillow": (
        "PIL (BSD-Style, permissiv)",
        "permissive",
        "manuell verifiziert 2026-08-30 (PIL-Lizenz ist BSD-derivative)",
    ),
}

# Kein schliessendes Wortgrenzen-\b: Kurz-Tokens kommen auch als "Apache2.0"
# oder "0BSD" vor. Falsch-Positiv-Risiko ist minimal (leading \b + lange Tokens).
PERMISSIVE_RE = re.compile(
    r"\b(mit|apache|0?bsd|isc|zlib|psf|software foundation|cc0|unlicense|cc-by|0cl"
    r"|public[- ]domain|w3c|ecl|artistic|simplified|ofl|font license)",
    re.IGNORECASE,
)
WEAK_COPYLEFT_RE = re.compile(r"\b(lgpl|mpl|mozilla)", re.IGNORECASE)
STRONG_COPYLEFT_RE = re.compile(r"\b(agpl|gpl)", re.IGNORECASE)

CLASS_LABELS = {
    "permissive": "permissiv ✓",
    "weak-copyleft": "Weak-Copyleft (LGPL/MPL) ✓",
    "strong-copyleft": "Strong-Copyleft (GPL/AGPL) ✓",
    "needs-review": "⚠️ PRÜFEN",
    "unknown": "❓ UNKNOWN",
}


@dataclass
class PackageInfo:
    """Ein installiertes Paket mit Lizenz-Metadaten und Scope."""

    name: str
    version: str
    license_label: str
    classification: str
    scope: str = "runtime-transitive"  # runtime-direct | runtime-transitive | dev-only
    note: str = ""
    requires: tuple[str, ...] = ()  # normalisierte Requires-Dist-Ziele (Scope-Closure)


def normalize_name(name: str) -> str:
    """PEP 503: Normalisierung von Paket-Namen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _strip_dist_version(base: str) -> str:
    """'streamlit-1.60.0' -> 'streamlit' (Version aus Dist-Verzeichnis-Namen).

    Nur Fallback, wenn das Name:-Feld der Metadaten fehlt; die Version ist
    das erste '-'/_'-getrennte Segment, das mit einer Ziffer beginnt.
    """
    m = re.match(r"^(.+?)[-_]v?\d", base)
    return m.group(1) if m else base


# Metadaten-Werte ohne Aussagekraft -> "unknown" (nicht needs-review),
# damit MANUAL_OVERRIDES greifen koennen.
UNKNOWN_VALUES = {"unknown", "none", "unspecified", "undisclosed", "other"}


def classify_branch(token: str) -> str:
    """Klassifiziert einen einzelnen Lizenz-Branch (keine OR-Expression)."""
    t = token.lower().strip()
    if t in UNKNOWN_VALUES:
        return "unknown"
    if "classpath" in t:  # GPL + Classpath-Exception wirkt permissiv (OSI-nahe)
        return "permissive"
    if STRONG_COPYLEFT_RE.search(t):
        return "strong-copyleft"
    if WEAK_COPYLEFT_RE.search(t):
        return "weak-copyleft"
    if PERMISSIVE_RE.search(t):
        return "permissive"
    return "needs-review"


def classify_license(raw: str) -> str:
    """Klassifiziert eine Lizenz-String (auch PEP 639 OR-Expressions).

    OR-Semantik: Ein permissiver Zweig reicht (Nutzer darf diesen waehlen).
    """
    s = (raw or "").strip()
    if not s:
        return "unknown"
    branches = [b for b in re.split(r"\s+or\s+", s, flags=re.IGNORECASE) if b.strip()]
    if not branches:
        branches = [s]
    results = [classify_branch(b) for b in branches]
    for level in ("permissive", "weak-copyleft", "strong-copyleft", "needs-review"):
        if level in results:
            return level
    return "unknown"


def parse_requirements(path: Path) -> set[str]:
    """Liest die direktesten Abhaengigkeiten aus einer requirements-Datei."""
    names: set[str] = set()
    if not path.is_file():
        return names
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!@\[\];\s]", line, 1)[0].strip()
        if name:
            names.add(normalize_name(name))
    return names


def _pick_license_classifier(classifiers: list[str]) -> str:
    """Waehlt den aussagekraeftigsten Lizenz-Klassifizierer.

    Bevorzugt 'License :: OSI Approved :: <Name>' (3 Teile) vor 2-teiligen
    Eintraegen wie 'License :: DFSG approved' (keine echte Lizenzbezeichnung).
    """
    fallback = ""
    for c in classifiers:
        parts = [p.strip() for p in c.split("::")]
        if len(parts) >= 3:
            return parts[-1]
        if len(parts) == 2:
            fallback = parts[-1]
    return fallback


def _parse_metadata_file(meta_file: Path) -> dict:
    """Parst Name/Version/Lizenz-/Abhaengigkeits-Felder aus METADATA/PKG-INFO."""
    info = {
        "name": "",
        "version": "",
        "license_expr": "",
        "license": "",
        "classifiers": [],
        "requires": [],
    }
    # Skalar-Felder (Name/Version/Lizenz): ERSTE Vorkommt gewinnt.
    # Kern-Metadaten stehen nach PEP 345/566 am Datei-Anfang; spaetere
    # Duplikate sind Korruptions-Artefakte (Beispiele: distro enthaelt
    # 'Name: Antergos Linux', pip-licenses enthaelt 'Name: setuptools,
    # Version: 38.5.0' als angehaengte Fremd-Zeilen).
    for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Name:") and not info["name"]:
            info["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Version:") and not info["version"]:
            info["version"] = line.split(":", 1)[1].strip()
        elif line.startswith("License-Expression:") and not info["license_expr"]:
            info["license_expr"] = line.split(":", 1)[1].strip()
        elif line.startswith("License:") and not info["license"]:
            info["license"] = line.split(":", 1)[1].strip()
        elif line.startswith("Classifier:") and "License ::" in line:
            info["classifiers"].append(line.split(":", 1)[1].strip())
        elif line.startswith("Requires-Dist:"):
            req = line.split(":", 1)[1].strip()
            # Optionale Extras (docs/test/dev) sind keine echten Abhaengigkeiten
            # und duerfen den Runtime-Closure nicht verunreinigen.
            if "extra ==" in req or "extra==" in req:
                continue
            name = re.split(r"[<>=!@\[\];\s]", req, 1)[0].strip()
            if name:
                info["requires"].append(normalize_name(name))
    return info


def collect_packages(venv: Path) -> list[PackageInfo]:
    """Sammelt alle installierten Dist-Metadaten aus der Venv (deterministisch)."""
    site_dir = _site_packages_of(venv)
    if site_dir is None:
        raise SystemExit(f"site-packages nicht gefunden: {venv}")

    empty_info = {
        "name": "",
        "version": "",
        "license_expr": "",
        "license": "",
        "classifiers": [],
        "requires": [],
    }
    found: dict[str, PackageInfo] = {}
    for entry in sorted(site_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.endswith(".dist-info"):
            base = entry.name[: -len(".dist-info")]
            meta_file = entry / "METADATA"
        elif entry.name.endswith(".egg-info"):
            base = entry.name[: -len(".egg-info")]
            meta_file = entry / "PKG-INFO"
        else:
            continue
        info = (
            _parse_metadata_file(meta_file)
            if meta_file.is_file()
            else dict(empty_info)
        )

        # Paket-Name: Dist-Verzeichnis-Name minus Versionssuffix ist die
        # Basis (z. B. 'streamlit-1.60.0' -> 'streamlit'). Das Name:-Feld der
        # Metadaten wird nur uebernommen, wenn es dazu passt — manche
        # METADATA-Dateien tragen spaeter noch fremde 'Name:'-Zeilen (z. B.
        # 'distro' enthaelt 'Antergos Linux').
        base_name = _strip_dist_version(base)
        meta_name = _strip_dist_version(info["name"]) if info["name"] else ""
        pkg_name = (
            info["name"]
            if meta_name
            and normalize_name(meta_name) == normalize_name(base_name)
            else base_name
        )
        norm = normalize_name(pkg_name)

        # Lizenz-Quellen (Prioritaet s. Modul-Docstring); die beste
        # Klassifizierung gewinnt, bei Gleichstand die hoechste Quelle.
        candidates: list[tuple[str, str]] = []
        if info["license_expr"].strip():
            candidates.append((info["license_expr"].strip(), "(PEP 639)"))
        classifier = _pick_license_classifier(info["classifiers"])
        if classifier:
            candidates.append((classifier, "(OSI-Klassifizierer)"))
        if info["license"].strip():
            candidates.append((info["license"].strip(), "(Metadaten-Feld)"))

        license_label = "UNKNOWN"
        classification = "unknown"
        for level in (
            "permissive",
            "weak-copyleft",
            "strong-copyleft",
            "needs-review",
            "unknown",
        ):
            for text, suffix in candidates:
                if classify_license(text) == level:
                    license_label = f"{text} {suffix}"
                    classification = level
                    break
            else:
                continue
            break

        note = ""
        if classification in ("unknown", "needs-review") and norm in MANUAL_OVERRIDES:
            lic, cls, note = MANUAL_OVERRIDES[norm]
            classification = cls
            license_label = f"{lic} (manuelles Override)"

        pkg = PackageInfo(
            name=pkg_name,
            version=info["version"] or "0.0.0",
            license_label=license_label,
            classification=classification,
            note=note,
            requires=tuple(sorted(set(info["requires"]))),
        )
        # Deduplication: dist-info/egg-info mit Version gewinnen
        if norm not in found or (not found[norm].version and pkg.version):
            found[norm] = pkg

    return sorted(found.values(), key=lambda p: (normalize_name(p.name), p.name))


def _closure(roots: set[str], graph: dict[str, set[str]]) -> set[str]:
    """Alle (transitiv) erreichbaren Pakete ueber Requires-Dist-Kanten."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, ()))
    return seen


def assign_scopes(
    pkgs: list[PackageInfo], direct: set[str], dev: set[str]
) -> None:
    """Weist Scope zu: direkter Runtime > Runtime-Closure > dev-only.

    Dev-only = erreichbar aus den Dev-Abhaengigkeiten, aber NICHT aus den
    Runtime-Abhaengigkeiten. Installierte Residuen (in keiner Closure)
    werden konservativ als Runtime-transitive eingeordnet (Uebermeldung
    ist compliance-sicher; Untererfassung nicht).
    """
    graph = {normalize_name(p.name): set(p.requires) for p in pkgs}
    runtime_reach = direct | _closure(direct, graph)
    dev_reach = dev | _closure(dev, graph)
    for p in pkgs:
        n = normalize_name(p.name)
        if n in direct:
            p.scope = "runtime-direct"
        elif n in runtime_reach:
            p.scope = "runtime-transitive"
        elif n in dev_reach:
            p.scope = "dev-only"
        else:
            p.scope = "runtime-transitive"  # konservativ: Venv-Residuum


def render_table(pkgs: list[PackageInfo]) -> str:
    """Markdown-Tabelle fuer eine Paketgruppe (nach Name sortiert)."""
    lines = [
        "| Paket | Version | Lizenz | AGPL-3.0-Klasse |",
        "|-------|---------|--------|-----------------|",
    ]
    for p in sorted(pkgs, key=lambda x: (normalize_name(x.name), x.name)):
        note = f" — {p.note}" if p.note else ""
        lines.append(
            f"| {p.name} | {p.version} | {p.license_label} | {CLASS_LABELS[p.classification]}{note} |"
        )
    return "\n".join(lines)


HEADER = """# Third-Party-Lizenzen

> **AUTO-GENERIERT — NICHT MANUELL EDITIEREN.**
>
> - Generieren: `python scripts/generate_licenses.py`
> - Frische- & Policy-Check: `python scripts/check_licenses.py --strict`
>
> Dieses Projekt ist unter **AGPL-3.0** lizenziert (siehe [LICENSE](LICENSE)).
> Alle Runtime-Abhaengigkeiten unten sind AGPL-kompatibel (permissiv,
> Weak-Copyleft oder Strong-Copyleft). Dev-only-Abhaengigkeiten sind
> Build-/Test-Tools und werden nie mit dem Projekt verteilt.
"""

MODEL_WEIGHTS_SECTION = """
## 4. Modell-Gewichte (nicht Teil dieses Repositories)

Modell-Gewichte werden **nicht** mit diesem Repository ausgeliefert. Sie werden
beim ersten Start heruntergeladen (gitignored: `models_cache/`, LM-Studio-
Modellordner) und bleiben auf dem Rechner des Nutzers.

| Modell | Lizenz | Ort | Hinweis |
|--------|--------|-----|---------|
| Gemma 4 12B (Haupt-LLM) | Google Gemma Terms of Use | LM Studio (lokal) | Nutzer laedt selbst und akzeptiert die Gemma-ToU; nicht mit diesem Repo redistribuierbar |
| intfloat/multilingual-e5-large (Embeddings) | Apache-2.0 | `models_cache/` | SentenceTransformer-Embedding-Modell |
| Qwen 3.5 (optionales LLM, GGUF) | Apache-2.0 (je nach Modell-Karte) | LM Studio / lokal | Unterstuetzt seit llama-cpp-python 0.3.35 |
| EasyOCR-Bundled-Modelle (CRAFT-Detection, CRNN-Recognition) | MIT / Apache-2.0 (laut EasyOCR-Doku) | `models_cache/` | ueber easyocr 1.7.2 geladen |

> **Compliance-Hinweis:** Die AGPL-3.0 dieses Repositories deckt fremde
> Modell-Gewichte mit eigenen Bedingungen nicht ab. Gemma-Gewichte duerken aus
> diesem Repository nicht weiterverteilt werden; Nutzer beziehen sie unter
> Googles Gemma Terms of Use selbst.
"""

LEGEND_SECTION = """
## 5. Legende & AGPL-3.0-Kompatibilitaet

| Klasse | Bedeutung | AGPL-3.0-kompatibel |
|--------|-----------|---------------------|
| permissiv | MIT, Apache-2.0, BSD, ISC, Zlib, PSF, CC0, ... | ✓ |
| Weak-Copyleft | LGPL, MPL | ✓ |
| Strong-Copyleft | GPL-2/3, AGPL-3 (u. a. PyMuPDF, pymupdf4llm) | ✓ |
| prüfen | nicht-standard / proprietäre Bedingungen | ✗ manuelle Prüfung |
| unknown | keine Lizenz-Metadaten gefunden | ✗ manuelle Prüfung |

AGPL-3.0 ist eine Strong-Copyleft-Lizenz: Die Kombination mit permissiven,
LGPL-, MPL- oder GPL-Komponenten ist erlaubt (Ergebnis bleibt AGPL-3.0).
Das Umgekehrte (AGPL-Code in ein permissives Projekt) würde dieses „infizieren"
— daher ist dieses Projekt AGPL-3.0 und nicht MIT.

## 6. Workflow

1. Abhaengigkeit in der Venv installieren/entfernen.
2. `python scripts/generate_licenses.py` → `LICENSES.md` wird aktualisiert.
3. `python scripts/check_licenses.py --strict` → Gate (auch im Pre-Commit-Hook).
"""


def build_markdown(pkgs: list[PackageInfo]) -> str:
    """Erzeugt den kompletten LICENSES.md-Inhalt (deterministisch)."""
    direct = [p for p in pkgs if p.scope == "runtime-direct"]
    transitive = [p for p in pkgs if p.scope == "runtime-transitive"]
    dev = [p for p in pkgs if p.scope == "dev-only"]
    parts = [
        HEADER,
        f"## 1. Runtime-Abhaengigkeiten — direkt ({len(direct)})\n\n"
        + (render_table(direct) if direct else "_keine_"),
        f"## 2. Runtime-Abhaengigkeiten — transitiv ({len(transitive)})\n\n"
        + (render_table(transitive) if transitive else "_keine_"),
        f"## 3. Dev-only-Abhaengigkeiten ({len(dev)}) — nie verteilt\n\n"
        + (render_table(dev) if dev else "_keine_"),
        MODEL_WEIGHTS_SECTION,
        LEGEND_SECTION,
    ]
    return "\n".join(parts) + "\n"


def generate(
    venv: Path = DEFAULT_VENV,
    requirements: Path = DEFAULT_REQUIREMENTS,
    dev_requirements: Path = DEFAULT_DEV_REQUIREMENTS,
) -> str:
    """Sammelt Metadaten und erzeugt den LICENSES.md-Text."""
    pkgs = collect_packages(venv)
    direct = parse_requirements(requirements)
    dev = parse_requirements(dev_requirements)
    assign_scopes(pkgs, direct, dev)
    return build_markdown(pkgs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Erzeugt LICENSES.md aus den Venv-Paket-Metadaten."
    )
    parser.add_argument("--venv", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument(
        "--dev-requirements", type=Path, default=DEFAULT_DEV_REQUIREMENTS
    )
    args = parser.parse_args(argv)
    text = generate(
        resolve_venv(args.venv), args.requirements, args.dev_requirements
    )
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"LICENSES.md geschrieben: {args.out} ({len(text.encode('utf-8'))} Bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())


