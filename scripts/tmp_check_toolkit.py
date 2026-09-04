"""Temp-Check: Backup-Ordner durchsuchen + funktionen.md P1-Status (wird gelöscht)."""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 1) Alle .py-Dateien im Backup-Ordner mit Groessen
backup_root = Path.home() / "bot6_backups"
print("== bot6_backups: .py-Dateien > 50 kB ==")
found = False
if backup_root.is_dir():
    for p in sorted(backup_root.rglob("*.py")):
        if p.stat().st_size > 50000:
            found = True
            print(f"  {p}  ({p.stat().st_size:,} B)")
    # Auch explizit alle agent_toolkit.py-Kopien
    print("== bot6_backups: ALLE agent_toolkit.py-Kopien ==")
    for p in sorted(backup_root.rglob("agent_toolkit.py")):
        print(f"  {p}  ({p.stat().st_size:,} B)")
        text = p.read_text(encoding="utf-8", errors="replace")
        print(f"      lines={len(text.splitlines())}, FileReaderToolkit={text.count('FileReaderToolkit')}, "
              f"AgentToolkit={text.count('class AgentToolkit')}, _path_sandbox={text.count('_path_sandbox')}, "
              f"offset={text.count('offset')}")
if not found:
    print("  (keine großen .py gefunden)")

# 2) funktionen.md: P1-Status
funk = _REPO_ROOT / "funktionen.md"
text = funk.read_text(encoding="utf-8", errors="replace")
print("\n== funktionen.md: P1/offset-Erwahnungen ==")
for i, line in enumerate(text.splitlines(), 1):
    if "P1" in line or "offset" in line.lower() or "limit" in line.lower():
        print(f"  {i}: {line.strip()[:150]}")

# 3) workdoc: aktueller P1-Abschnitt
wd = _REPO_ROOT / "docs" / "WORKDOC_FILESYSTEM_CONTEXT_SAFETY_20260824.md"
wt = wd.read_text(encoding="utf-8", errors="replace")
print("\n== workdoc: P1-Section-Start ==")
idx = wt.find("## P1:")
if idx != -1:
    print(wt[idx:idx + 600])
else:
    print("  (keine '## P1:'-Section gefunden)")
print(f"\nworkdoc total: {len(wt.splitlines())} lines")


