#!/usr/bin/env python3
"""Live mypy cluster analysis on current output."""
import re
from collections import Counter, defaultdict

errors = Counter()
notes = 0
file_counts = Counter()
files_by_code = defaultdict(set)

with open('mypy_live_scope_errors.txt', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for line in lines:
    stripped = line.rstrip()
    if 'note:' in stripped or stripped.startswith(' '):
        notes += 1
        continue
    em = re.search(r'\[(\w[\w-]*)\]\s*$', stripped)
    if em:
        code = em.group(1)
        errors[code] += 1
        fm = re.match(r'([^\s:]+):', line)
        if fm:
            file_counts[fm.group(1)] += 1
            files_by_code[code].add(fm.group(1))

print(f'Gesamtfehler: {sum(errors.values())}')
print(f'Notes: {notes}')
print(f'Betroffene Dateien: {len(file_counts)}')
print()
print('=== Error-Code Verteilung (desc) ===')
for code, count in errors.most_common():
    nfiles = len(files_by_code[code])
    print(f'  {code:20s} {count:4d}  ({nfiles} Dateien)')
print()
print('=== Top-15 Dateien ===')
for fpath, cnt in file_counts.most_common(15):
    print(f'  {fpath}: {cnt} Fehler')

# Compare with old baseline
print()
print('=== Vergleich zur alten Baseline (mypy_full_scope_raw.txt: 496 Fehler) ===')
reduction = 496 - sum(errors.values())
pct = reduction / 496 * 100
print(f'  Reduziert um: {reduction} Fehler ({pct:.1f}%)')
print(f'  Noch offen: {sum(errors.values())} Fehler')