"""
Code-Level Reachability Analysis (SOTA 2026)
============================================

Dritte Säule der "Prioritization Trinity" (CISA KEV + EPSS + Reachability):
Eine Schwachstelle ist nur ein reales Risiko, wenn der betroffene Code aus
der Anwendung heraus tatsächlich erreichbar ist.

Local-only, AST-basiert (keine Code-Ausführung):
  1. Sammelt die top-level importierten Module des Repositories
     (statische Analyse aller .py-Dateien).
  2. Mappt installierte Distributionen auf ihre top-level Module
     (importlib.metadata.packages_distributions(), Python 3.10+;
     löst z.B. pillow -> PIL, beautifulsoup4 -> bs4 korrekt auf).
  3. Eine Distribution ist "reachable", wenn
     a) eines ihrer top-level Module im Repo-Code importiert wird, ODER
     b) sie (transitiv) in den deklarierten Abhängigkeiten eines direkt
        importierten Distribution liegt (Abhängigkeits-Closure über
        importlib.metadata.requires — z.B. urllib3 via requests).

Warum Closure (2026-09-06): Naive Direkt-Import-Analyse würde transitiv
erreichbare Pakete (urllib3 via requests, click via streamlit) fälschlich
herabstufen — bei einem Security-Tool eine gefährliche Fehlzuversicht.
Die Closure ist die Standard-Approximation für Runtime-Reachability und
bleibt vollständig lokal (nur lokale Metadaten, kein pip, kein Netzwerk).

Semantik (is_reachable):
  True   -> im Reachability-Closure (Import oder deklarierte Abhängigkeit)
  False  -> installiert, aber weder importiert noch in der Closure
             (Tier wird in compute_risk um 1 gesenkt)
  None   -> unbestimmbar (Distribution nicht installiert / Analyse
             nicht verfügbar) -> KEINE Tier-Änderung (kein False-Negative)

Einschränkungen (bewusste Trade-offs):
  - Statisch: hinter try/except oder importlib.import_module() verborgene
    Importe werden nicht gesehen.
  - Deklarative (Metadata-) Abhängigkeiten, nicht der reale
    Runtime-Importgraph — deklarative Abhängigkeiten zählen als reachable
    (vorsorglich, Richtung False-Negative für Downgrades).

Privacy:
  - Kein Netzwerk, keine Telemetrie, kein Code verlässt die Maschine.
  - Nur lokale Datei-Lesungen (AST-Parsing) + lokale Metadaten.

Best-effort:
  - Analyse-Fehler -> available=False, is_reachable() -> None.
"""

from __future__ import annotations

import ast
import logging
import re
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

# Verzeichnisse, die keine App-Code-Quellen sind (Venvs, Caches, Doku,
# Einmal-Skripte, Archive). Zusätzlich: alles, dessen Name mit "venv"
# beginnt (venv_bot_*, .venv, ...).
# Design-Entscheidung 2026-09-06: temp_scripts/ und dead_code_archive/
# sind Throwaway-/Archiv-Code und tauchen NICHT in der Reachability auf
# (verhindert z.B. auch SyntaxWarnings aus Legacy-Skripten wie
# temp_scripts/patch_db.py im Scanner-Output).
DEFAULT_EXCLUDE_DIRS: frozenset = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", "site-packages", "dist", "build",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "backups", "docs", "docs_archive",
    "temp_scripts", "dead_code_archive",
})

# Namens-Normierung nach PEP 503 (für Distribution-Vergleiche)
_NORMALIZE_RE = re.compile(r"[-_.]+")

# Requirement-Name aus einer Requirement-String extrahieren
# ("urllib3>=1.21", "pkg[extra] ; marker", "pkg (>=2)" -> Name)
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize_name(name: str) -> str:
    """PyPI-kompatible Namens-Normierung (klein, -/_/. äquivalent)."""
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def _dist_requires(dist_name: str) -> List[str]:
    """
    Deklarierte Abhängigkeitsnamen (Distribution-Names) einer Distribution.

    Best-effort: unbekannt/fehlerhaft -> leere Liste (kein Exception).
    Verwendet importlib.metadata.requires() (lokal, kein pip, kein Netzwerk).
    """
    try:
        dist = metadata.distribution(dist_name)
        reqs = dist.requires or []
    except Exception:  # noqa: BLE001 - best-effort, Closure bleibt nutzbar
        return []
    names: List[str] = []
    for req in reqs:
        m = _REQ_NAME_RE.match(str(req))
        if m:
            names.append(m.group(1))
    return names


def extract_imports(path: Path) -> Set[str]:
    """
    Extrahiert statisch die top-level-Import-Namen aus einer Python-Datei.

    Erfasst `import X` und absolute `from X import ...` (level == 0).
    Relative Imports (level > 0) und Parse-Fehler werden ignoriert
    (best-effort: leere Menge, kein Exception-Weiterreichen).
    """
    try:
        # utf-8-sig: entfernt ggf. vorhandenes BOM (U+FEFF, Windows-Artefakt),
        # sonst wuerfe ast.parse SyntaxError und die Imports gingen verloren.
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError) as e:
        logger.debug(f"extract_imports: {path} übersprungen: {e}")
        return set()

    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


class CodeReachability:
    """
    Statische Erreichbarkeits-Analyse eines Repositories.

    Nutzung:
        ra = CodeReachability(repo_root=Path("/pfad/zum/repo"))
        ra.build()                       # idempotent, einmalig
        ra.is_reachable("pillow")        # True | False | None

    Privacy/Best-effort: keine Netzwerk-Calls; Fehler führen zu
    available=False (und is_reachable() -> None), nie zu Exceptions.
    """

    def __init__(
        self,
        repo_root: Path,
        exclude_dirs: Optional[Iterable[str]] = None,
    ):
        self.repo_root = Path(repo_root)
        self.exclude_dirs = (
            frozenset(exclude_dirs) if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
        )
        self.available = False
        self.files_scanned = 0
        self.imported_modules: frozenset = frozenset()
        self._dist_map: Dict[str, List[str]] = {}  # normierte Distribution -> Module
        self._reachable: Optional[Set[str]] = None  # Closure (normierte Dists)
    # -- Analyse ------------------------------------------------------------

    def build(self) -> bool:
        """
        Führt die Analyse durch (idempotent).

        Steps:
          1. Statische Import-Sammlung (AST, alle App-Code-Dateien).
          2. Distribution-Map (importlib.metadata.packages_distributions).
          3. Reachability-Closure: direkt importierte Distributionen +
             ihre deklarierten Abhängigkeiten (transitiv, BFS) —
             deckt z.B. urllib3 via requests ab.

        Returns:
            True, wenn die Analyse ausgeführt ist (available=True).
        """
        if self.available:
            return True

        imported: Set[str] = set()
        files = 0
        if self.repo_root.is_dir():
            for path in sorted(self.repo_root.rglob("*.py")):
                try:
                    parents = set(path.relative_to(self.repo_root).parts[:-1])
                except ValueError:
                    parents = set()
                if parents & self.exclude_dirs or any(
                    p.lower().startswith("venv") for p in parents
                ):
                    continue
                imported |= extract_imports(path)
                files += 1

        self.imported_modules = frozenset(imported)
        self.files_scanned = files
        self._dist_map = self._build_dist_map()
        self._reachable = self._build_reachable_closure()
        self.available = True
        logger.debug(
            f"Reachability: {files} Dateien, {len(imported)} importierte Module, "
            f"{len(self._dist_map)} Distributionen gemappt, "
            f"{len(self._reachable)} reachable"
        )
        return True

    def _build_reachable_closure(self) -> Set[str]:
        """
        Reachability-Closure (normierte Distributionen):
        alle direkt importierten Distributionen + ihre deklarierten
        Abhängigkeiten, transitiv (BFS). Nur installierte Distributionen
        (aus _dist_map) sind Kandidaten.
        """
        direct = [
            dist
            for dist, modules in self._dist_map.items()
            if any(module in self.imported_modules for module in modules)
        ]
        seen: Set[str] = set()
        queue = list(direct)
        while queue:
            dist = queue.pop()
            if dist in seen:
                continue
            seen.add(dist)
            for dep in _dist_requires(dist):
                dep_norm = _normalize_name(dep)
                if dep_norm in self._dist_map and dep_norm not in seen:
                    queue.append(dep_norm)
        return seen

    def _build_dist_map(self) -> Dict[str, List[str]]:
        """
        Mappt normierte Distributionen auf ihre top-level Module.

        Nutzt importlib.metadata.packages_distributions() (installierte
        Distributionen des laufenden Interpreters). Fehlt eine Distribution
        hier, ist ihre Erreichbarkeit unbestimmbar (None) - keine Annahme.
        """
        try:
            packages_distributions = metadata.packages_distributions()
        except Exception as e:  # noqa: BLE001 - best-effort, Analyse bleibt nutzbar
            logger.debug(f"packages_distributions nicht verfügbar: {e}")
            return {}

        inverted: Dict[str, List[str]] = {}
        for module, dists in packages_distributions.items():
            for dist in dists:
                inverted.setdefault(_normalize_name(dist), []).append(module)
        return inverted

    # -- Lookup -------------------------------------------------------------

    def is_reachable(self, distribution_name: str) -> Optional[bool]:
        """
        Erreichbarkeit einer Distribution (PyPI-Name) prüfen.

        Returns:
            True   -> im Reachability-Closure (direkt importiert oder
                      deklarierte Abhängigkeit eines importierten Packages)
            False  -> installiert, aber weder importiert noch in der Closure
            None   -> unbestimmbar (nicht installiert / Analyse fehlt)
        """
        if not self.available or not distribution_name or self._reachable is None:
            return None
        key = _normalize_name(distribution_name)
        if key not in self._dist_map:
            return None
        return key in self._reachable

    # -- Statistik ----------------------------------------------------------

    @property
    def stats(self) -> Dict[str, object]:
        """Kurzstatistik (für Reports/Tests)."""
        return {
            "available": self.available,
            "files_scanned": self.files_scanned,
            "imported_modules": len(self.imported_modules),
            "mapped_distributions": len(self._dist_map),
            "reachable_distributions": len(self._reachable or ()),
        }