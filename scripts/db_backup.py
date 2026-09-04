"""Taegliches Datenbank-Backup fuer die produktiven homebot-Datenbanken.

Sichert alle produktiven SQLite-Datenbanken (Quelle: utils/db_path_resolver,
also das .db_root-Ziel) konsistent nach ~/homebot_backups/db/auto/
(Override: Env HOMEBOT_BACKUP_ROOT) in tagesdatierte Ordner und rotiert alte Staende.

Warum VACUUM INTO statt Dateikopie:
    Eine Dateikopie einer SQLite-DB, in die gerade geschrieben wird, ist
    potenziell korrupt. ``VACUUM INTO`` erzeugt dagegen eine transaktional
    konsistente, kompaktierte Kopie und funktioniert auch bei laufender App
    (Lesetransaktion, WAL-kompatibel).

Aufrufmodell:
    Der Autosave-Watcher (scripts/autosave_watcher.ps1) ruft dieses Skript in
    jedem Zyklus auf; es beendet sich sofort, wenn das heutige Backup bereits
    existiert (Selbst-Gate). Manuell:

        <Projekt-venv>/Scripts/python.exe scripts/db_backup.py
        ... --force      # heutiges Backup verwerfen und neu erstellen

Wiederherstellung (Beispiel):
    1. App stoppen.
    2. Datei aus ~/homebot_backups/db/auto/<datum>/ an den
       produktiven Ort unter dem .db_root-Ziel kopieren.
    3. Bei der Psycho-DB die zugehoerige .key-Datei mitkopieren.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Repo-Root in den Pfad, damit utils/ importierbar ist, egal von wo gestartet.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.db_path_resolver import (  # noqa: E402
    get_agent_rag_path,
    get_chat_history_path,
    get_finance_path,
    get_wellbeing_path,
    get_rag_store_path,
    get_web_policy_path,
)

# Backup-Ziel (portabel): Env-Override > ~/homebot_backups/db/auto
BACKUP_ROOT = Path(os.environ.get("HOMEBOT_BACKUP_ROOT") or (Path.home() / "homebot_backups" / "db" / "auto"))
LOG_FILE = Path(os.environ.get("HOMEBOT_BACKUP_LOG") or (Path.home() / "homebot_backups" / "db_backup.log"))
KEEP_GENERATIONS = 7
MIN_FREE_BYTES = 2 * 1024**3  # 2 GB Sicherheitsreserve auf der Zielplatte


def _log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _backup_sources() -> list[tuple[str, Path]]:
    """(Zielname, Quellpfad) aller produktiven DBs — Quelle ist der Resolver."""
    return [
        ("rag_store.db", get_rag_store_path()),
        ("wellbeing_store.db", get_wellbeing_path()),
        ("chat_history.db", get_chat_history_path()),
        ("finance.db", get_finance_path()),
        ("web_policy.db", get_web_policy_path()),
        ("agent_rag_store.db", get_agent_rag_path()),
    ]


def _key_files() -> list[Path]:
    """Schluesseldateien, ohne die verschluesselte Felder unlesbar waeren."""
    psych = get_wellbeing_path()
    return [psych.with_suffix(psych.suffix + ".key")]


def _vacuum_into(source: Path, target: Path) -> None:
    """Konsistente Kopie via VACUUM INTO (read-only Verbindung)."""
    # mode=ro: das Backup kann die Quelle niemals veraendern.
    conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=60)
    try:
        escaped = str(target).replace("'", "''")
        conn.execute(f"VACUUM INTO '{escaped}'")
    finally:
        conn.close()


def _quick_check(db: Path) -> str:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=60)
    try:
        return str(conn.execute("PRAGMA quick_check;").fetchone()[0])
    finally:
        conn.close()


def _rotate() -> list[str]:
    """Aelteste tagesdatierte Ordner ueber KEEP_GENERATIONS hinaus entfernen."""
    dated = sorted(
        d for d in BACKUP_ROOT.iterdir()
        if d.is_dir() and len(d.name) == 10 and d.name[4] == "-" and d.name[7] == "-"
    )
    removed: list[str] = []
    for d in dated[:-KEEP_GENERATIONS] if len(dated) > KEEP_GENERATIONS else []:
        shutil.rmtree(d)
        removed.append(d.name)
    return removed


def run_backup(force: bool = False) -> int:
    today_dir = BACKUP_ROOT / date.today().isoformat()

    # Selbst-Gate: pro Tag genau ein Backup.
    if today_dir.exists():
        if not force:
            return 0
        shutil.rmtree(today_dir)

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    free = shutil.disk_usage(BACKUP_ROOT).free
    if free < MIN_FREE_BYTES:
        _log(f"FEHLER: nur {free / 1024**3:.1f} GB frei (< 2 GB Reserve) — Backup uebersprungen")
        return 1

    part_dir = BACKUP_ROOT / (today_dir.name + ".part")
    if part_dir.exists():
        shutil.rmtree(part_dir)  # Rest eines frueheren Abbruchs
    part_dir.mkdir(parents=True)

    started = time.monotonic()
    manifest: dict[str, dict[str, object]] = {}
    errors: list[str] = []

    for name, source in _backup_sources():
        if not source.exists():
            # Fehlende Quelle ist ein Fehler, kein stilles Ueberspringen:
            # eine produktive DB, die ploetzlich fehlt, muss auffallen.
            errors.append(f"{name}: Quelle fehlt ({source})")
            continue
        target = part_dir / name
        try:
            t0 = time.monotonic()
            _vacuum_into(source, target)
            check = _quick_check(target)
            manifest[name] = {
                "source": str(source),
                "source_bytes": source.stat().st_size,
                "backup_bytes": target.stat().st_size,
                "quick_check": check,
                "seconds": round(time.monotonic() - t0, 1),
            }
            if check != "ok":
                errors.append(f"{name}: quick_check='{check}'")
        except Exception as exc:  # noqa: BLE001 — jede Fehlerart soll gemeldet werden
            errors.append(f"{name}: {exc}")

    for key in _key_files():
        if key.exists():
            shutil.copy2(key, part_dir / key.name)
            manifest[key.name] = {"source": str(key), "backup_bytes": key.stat().st_size}
        else:
            errors.append(f"Schluesseldatei fehlt: {key}")

    manifest["_meta"] = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 1),
        "errors": errors,
    }
    (part_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if errors:
        # Fehlgeschlagene Backups behalten das .part-Suffix: sie zaehlen nicht
        # als gueltige Generation und der naechste Lauf versucht es erneut.
        _log(f"FEHLER: Backup unvollstaendig ({'; '.join(errors)}) — liegt in {part_dir.name}")
        return 1

    part_dir.rename(today_dir)
    removed = _rotate()
    total_mb = sum(
        int(v.get("backup_bytes", 0)) for v in manifest.values() if isinstance(v, dict)
    ) / 1024**2
    _log(
        f"OK: {len([k for k in manifest if not k.startswith('_')])} Dateien, "
        f"{total_mb:.0f} MB, {manifest['_meta']['duration_seconds']}s"
        + (f", rotiert: {', '.join(removed)}" if removed else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_backup(force="--force" in sys.argv))
