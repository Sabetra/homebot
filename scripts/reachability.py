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
  3. Eine Distribution ist "reachable", wenn eines ihrer top-level
     Module irgendwo im Repository importiert wird.

Semantik (is_reachable):
  True   -> Modul wird im Repo-Code importiert (volle Priorität)
  False  -> kein Import gefunden (Tier wird in compute_risk um 1 gesenkt)
  None   -> unbestimmbar (Distribution nicht installiert / Analyse
             nicht verfügbar) -> KEINE Tier-Änderung (kein False-Negative)

Privacy:
  - Kein Netzwerk, keine Telemetrie, kein Code verlässt die Maschine.
  - Nur lokale Datei-Lesungen (AST-Parsing).

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

# Verzeichnisse, die keine App-Code-Quellen sind (Venvs, Caches, Doku).
# Zusätzlich: alles, dessen Name mit "venv" beginnt (venv_bot_*, .venv, ...).
DEFAULT_EXCLUDE_DIRS: frozenset = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", "site-packages", "dist", "build",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "backups", "docs", "docs_archive",
})


def _normalize_name(name: str) -> str:
    """PyPI-kompatible Namens-Normierung (klein, -/_/. äquivalent)."""
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def extract_imports(path: Path) -> Set[str]:
    """
    Extrahiert statisch die top-level-Import-Namen aus einer Python-Datei.

    Erfasst `import X` und absolute `from X import ...` (level == 0).
    Relative Imports (level > 0) und Parse-Fehler werden ignoriert
    (best-effort: leere Menge, kein Exception-Weiterreichen).
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
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
    # -- Analyse ------------------------------------------------------------

    def build(self) -> bool:
        """
        Führt die Analyse durch (idempotent).

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
        self.available = True
        logger.debug(
            f"Reachability: {files} Dateien, {len(imported)} importierte Module, "
            f"{len(self._dist_map)} Distributionen gemappt"
        )
        return True

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
            True   -> wird im Repo-Code importiert
            False  -> Distribution bekannt, aber kein Import gefunden
            None   -> unbestimmbar (nicht installiert / Analyse fehlt)
        """
        if not self.available or not distribution_name:
            return None
        modules = self._dist_map.get(_normalize_name(distribution_name))
        if not modules:
            return None
        return any(module in self.imported_modules for module in modules)

    # -- Statistik ----------------------------------------------------------

    @property
    def stats(self) -> Dict[str, object]:
        """Kurzstatistik (für Reports/Tests)."""
        return {
            "available": self.available,
            "files_scanned": self.files_scanned,
            "imported_modules": len(self.imported_modules),
            "mapped_distributions": len(self._dist_map),
        }