import re
import io
import os

pat = re.compile(
    r"FinanceTools|finance_tools\.|\.subscription_audit\(|\.upcoming_bills\(|"
    r"TOOL_HANDLERS|tool_dispatch|def execute_tool|FINANCE_TOOL_MAP"
)
for root, dirs, files in os.walk("."):
    dirs[:] = [
        d
        for d in dirs
        if d
        not in (
            ".git",
            "venv_bot_20260802",
            "venv_mistral_gguf",
            "node_modules",
            "__pycache__",
            "docs",
            "docs_archive",
        )
    ]
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(root, f)
        for n, l in enumerate(io.open(p, encoding="utf-8")):
            if pat.search(l):
                print(p + ":" + str(n + 1) + ": " + l.rstrip()[:130])
