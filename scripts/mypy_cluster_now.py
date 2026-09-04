"""Cluster mypy errors from mypy_full_output.txt."""
import re
import collections
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
raw = open(os.path.join(project_root, "mypy_full_output.txt"), "rb").read()
# UTF-16 LE with BOM from PowerShell redirect
if raw.startswith(b"\xff\xfe"):
    lines = raw.decode("utf-16-le").splitlines()
elif raw.startswith(b"\xfe\xff"):
    lines = raw.decode("utf-16-be").splitlines()
else:
    lines = raw.decode("utf-8").splitlines()

codes = []
files_map = {}

# Only match lines that contain "error:" and extract the trailing [code]
for line in lines:
    if "error:" not in line:
        continue
    m = re.search(r"\[([a-z]+)\]\s*$", line)
    if m:
        code = m.group(1)
        codes.append(code)
        fm = re.match(r"([^:]+):", line)
        f = fm.group(1).split("\\")[-1] if fm else "<unknown>"
        if code not in files_map:
            files_map[code] = {}
        files_map[code][f] = files_map[code].get(f, 0) + 1

counter = collections.Counter(codes)
print(f"Total errors: {len(codes)}")
print(f"Unique codes: {len(counter)}")
print()
print("=" * 65)
for code, count in counter.most_common():
    print(f"  {code:30s} {count:>5}")
print("=" * 65)

for code, count in counter.most_common(10):
    print(f"\n[{code}] ({count} errors) — top files:")
    top = sorted(files_map[code].items(), key=lambda x: -x[1])[:5]
    for f, c in top:
        print(f"  {f:45s} {c:>3}")

# Overall file ranking
print("\n" + "=" * 65)
print("TOP FILES BY ERROR COUNT:")
print("=" * 65)
all_files = collections.Counter()
for fm in files_map.values():
    all_files.update(fm)
for f, c in all_files.most_common(25):
    print(f"  {f:45s} {c:>3}")
