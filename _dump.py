"""Tempoerares Dump-Helper: zeigt Zeilenbereiche einer Datei (Session-Tool)."""
import io
import sys

def main() -> None:
    path = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    lines = io.open(path, encoding="utf-8").read().splitlines()
    out = []
    for i in range(start - 1, min(end, len(lines))):
        out.append(f"{i + 1}|{lines[i]}")
    sys.stdout.write("\n".join(out) + "\n")

if __name__ == "__main__":
    main()
