"""Dynamische Modell-Registry für den LM-Studio-Community-Ordner.

Scannt den Modell-Ordner (rekursiv) und liefert eine LIVE-Liste aller
ladbaren GGUF-Modelle. Neue Modell-Ordner erscheinen sofort, gelöschte
verschwinden — ohne Neustart und ohne Cache.

Vision-Fähigkeit wird durch die Präsenz einer ``mmproj``-Datei (Projector)
im selben Ordner erkannt; mehrere Quantisierungen im selben Ordner erzeugen
je einen eigenen Eintrag.

Framework-agnostisch (kein Streamlit/LLM-Import) → direkt unit-testbar.

Konfiguration:
    BOT_MODELS_DIR (optional) — Override des Modell-Ordners. Default:
    ``~/.cache/lm-studio/models/lmstudio-community`` (Home des aktuellen
    Users). Legacy-Pfade (z. B. ein älterer Windows-Home-Pfad) werden nur
    berücksichtigt, sofern sie existieren.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

# Single Source of Truth für den Modell-Ordner (LM Studio):
# portable Default-Auflösung im Home-Verzeichnis des aktuellen Users.
def _default_models_dir() -> Path:
    return Path.home() / ".cache" / "lm-studio" / "models" / "lmstudio-community"

DEFAULT_MODELS_DIR = str(_default_models_dir())


def models_root() -> Path:
    """Aktiver Modell-Ordner (Env-Override ``BOT_MODELS_DIR`` > portable Default)."""
    env = os.environ.get("BOT_MODELS_DIR")
    if env:
        return Path(env)
    return Path(DEFAULT_MODELS_DIR)

# GGUF-Shards (geteilte Dateien) sind keine ladbaren Einzelmodelle,
# z. B. "model-00001-of-00002.gguf".
_SHARD_RE = re.compile(r"-of-\d+\.gguf$", re.IGNORECASE)


def _is_mmproj(filename: str) -> bool:
    """True für Vision-Projector-Dateien (mmproj*.gguf)."""
    low = filename.lower()
    return low.startswith("mmproj") and low.endswith(".gguf")


def _is_main_gguf(filename: str) -> bool:
    """True für ladbare Haupt-GGUF-Dateien (keine mmproj, keine Shards)."""
    low = filename.lower()
    if not low.endswith(".gguf"):
        return False
    if low.startswith("mmproj"):
        return False
    if _SHARD_RE.search(low):
        return False
    return True


def _slug(text: str) -> str:
    """Stabiler, eindeutiger Slug aus Ordnerrelativ-Stem (klein, alphanum)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass(frozen=True)
class ModelInfo:
    """Metadaten eines ladbaren GGUF-Modells (aus dem Live-Scan)."""

    model_id: str                      # stabiles eindeutiges ID (Ordner/Stem)
    display_name: str                  # Datei-Stem, z. B. "gemma-4-12B-it-QAT-Q4_0"
    model_path: str                    # absoluter Pfad zur GGUF-Datei
    mmproj_path: Optional[str]         # Pfad zur Vision-Projector-Datei (oder None)
    size_gb: float                     # Dateigröße in GB
    is_vision: bool                    # True, wenn mmproj im selben Ordner existiert
    folder_rel: str                    # Ordner relativ zur Wurzel ("." = Wurzel)
    modified: str                      # ISO-Zeitstempel der letzten Änderung



def scan_models(root: Optional[Union[str, "os.PathLike"]] = None) -> List[ModelInfo]:
    """Scannt den Modell-Ordner LIVE und liefert alle ladbaren Modelle.

    Regeln:
      - Rekursiv: jede Unterstruktur wird berücksichtigt (LM-Studio-Layout
        ``<famile>/<modell>/<datei>.gguf`` sowie beliebige Nestings).
      - Vision = ``mmproj*.gguf`` im selben Ordner (größte Datei gewinnt).
      - Mehrere Haupt-GGUFs im Ordner (Quantisierungen) → je ein Eintrag.
      - Ordnern ohne Haupt-GGUF (nur mmproj, nur Shards) → ignoriert.
      - Fehlender Wurzelordner → leere Liste (kein Fehler; die UI zeigt
        eine Warnung mit dem Pfad).

    Kein Cache: Jeder Aufruf spiegelt den aktuellen Ordner-Stand wider,
    damit neue/gelöschte Modell-Ordner sofort sichtbar sind.
    """
    root_path = Path(root) if root is not None else models_root()
    if not root_path.is_dir():
        return []

    results: List[ModelInfo] = []
    try:
        dir_iter = [root_path, *[p for p in root_path.rglob("*") if p.is_dir()]]
    except OSError:
        return []

    for dir_path in sorted(dir_iter):
        try:
            files = [f.name for f in dir_path.iterdir() if f.is_file()]
        except OSError:
            continue

        main_names = sorted((f for f in files if _is_main_gguf(f)), key=str.lower)
        if not main_names:
            continue

        mmproj_name: Optional[str] = None
        mmproj_names = [f for f in files if _is_mmproj(f)]
        if mmproj_names:
            try:
                # Größte Projector-Datei gewinnt (z. B. F16/BF16 vor Q8)
                mmproj_name = max(
                    mmproj_names,
                    key=lambda n: dir_path.joinpath(n).stat().st_size,
                )
            except OSError:
                mmproj_name = mmproj_names[0]

        folder_rel = dir_path.relative_to(root_path).as_posix()
        for main_name in main_names:
            model_file = dir_path / main_name
            try:
                st = model_file.stat()
            except OSError:
                continue
            stem = Path(main_name).stem
            results.append(
                ModelInfo(
                    model_id=_slug(f"{folder_rel}/{stem}"),
                    display_name=stem,
                    model_path=str(model_file),
                    mmproj_path=str(dir_path / mmproj_name) if mmproj_name else None,
                    size_gb=round(st.st_size / (1024 ** 3), 3),
                    is_vision=mmproj_name is not None,
                    folder_rel=folder_rel,
                    modified=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                )
            )

    # Sortierung: Ordner (A-Z), dann Größe (absteigend), dann ID
    results.sort(key=lambda m: (m.folder_rel, -m.size_gb, m.model_id))
    return results


def find_model_by_path(
    path: Union[str, "os.PathLike"], root: Optional[Path] = None
) -> Optional[ModelInfo]:
    """Findet die ``ModelInfo`` zu einem GGUF-Dateipfad (oder None).

    Args:
        path: Pfad zur Haupt-GGUF-Datei.
        root: Optionales Registry-Root (Default: ``models_root()``).
    """
    try:
        target = str(Path(path).resolve())
    except OSError:
        return None
    for info in scan_models(root):
        try:
            if str(Path(info.model_path).resolve()) == target:
                return info
        except OSError:
            continue
    return None


if __name__ == "__main__":
    # Diagnose-CLI:  python -m utils.model_registry
    found = scan_models()
    print(f"Modelle unter {models_root()}: {len(found)}")
    for m in found:
        vision = "👁 Vision" if m.is_vision else "📝 Text"
        print(f"  {m.display_name:<48} {m.size_gb:8.2f} GB  {vision}  ({m.folder_rel})")
    if not found:
        print("  (leer — Ordner fehlt oder enthält keine ladbaren GGUF-Modelle)")
