"""Tempoerares Dump-Helper: zeigt Zeilenbereiche einer Datei (Session-Tool)."""
import io
import sys

def main() -> None:
    path = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    out_path = sys.argv[4] if len(sys.argv) > 4 else None
    lines = io.open(path, encoding="utf-8").read().splitlines()
    out = []
    for i in range(start - 1, min(end, len(lines))):
        out.append(f"{i + 1}|{lines[i]}")
    text = "\n".join(out) + "\n"
    if out_path:
        with io.open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)

if __name__ == "__main__":
    main()
