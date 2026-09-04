#!/usr/bin/env python3
"""Analyze mypy full-scope output and cluster errors by code."""
import re
from collections import Counter, defaultdict

with open('mypy_full_scope_raw.txt', 'r', encoding='utf-16', errors='replace') as f:
    lines = f.readlines()

errors = Counter()
files_set = set()
files_by_code = defaultdict(set)
notes = 0

for line in lines:
    stripped = line.rstrip()
    # Skip note lines (they are informational, not errors)
    if stripped.startswith(' ') or 'note:' in stripped:
        notes += 1
        continue
    # Match error lines like: file.py:42: error: message  [code]
    em = re.search(r'\[(\w[\w-]*)\]\s*$', stripped)
    if em:
        code = em.group(1)
        errors[code] += 1
        fm = re.match(r'([^\s:]+):', line)
        if fm:
            files_set.add(fm.group(1))
            files_by_code[code].add(fm.group(1))

print(f'Total errors: {sum(errors.values())}')
print(f'Total notes: {notes}')
print(f'Files with errors: {len(files_set)}')
print()
print('=== Error Code Distribution (sorted by count desc) ===')
for code, count in errors.most_common():
    nfiles = len(files_by_code[code])
    print(f'  {code:20s} {count:4d}  ({nfiles} files)')
print()
print('=== Top 20 Offending Files ===')
file_counts = Counter()
for line in lines:
    stripped = line.rstrip()
    if stripped.startswith(' ') or 'note:' in stripped:
        continue
    em = re.search(r'\]\s*\[(\w[\w-]*)\]\s*$', stripped)
    if em:
        fm = re.match(r'([^\s:]+):', line)
        if fm:
            file_counts[fm.group(1)] += 1
for fpath, cnt in file_counts.most_common(20):
    print(f'  {fpath}: {cnt} errors')