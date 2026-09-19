"""Scan tab.py for the bug: German opening quote + placeholder + ASCII quote.

Correct:  „{name}”  (U+201E ... U+201C)
Buggy:    „{name}"  (U+201E ... U+0022)  -> terminates the Python string
"""
import io
import re

path = r"c:\Users\bot6\finance\tab.py"
with io.open(path, "r", encoding="utf-8") as fh:
    lines = fh.readlines()

# German opening quote, then up to 20 chars, then an ASCII quote that is NOT
# the final terminator of the line (i.e. more content + closing quote follows).
pat = re.compile("\u201E[^\"\u201E]{0,20}\"\s*\S.*\"")
bad = []
for idx, line in enumerate(lines, 1):
    if pat.search(line):
        bad.append((idx, line.rstrip("\r\n")))

for idx, line in bad:
    print(idx, line)
print("total:", len(bad))

