import io

def lines_of(path):
    return io.open(path, encoding="utf-8").read().splitlines()

def find(path, *pats):
    lines = lines_of(path)
    hits = []
    for i, l in enumerate(lines):
        if any(p in l for p in pats):
            hits.append(i + 1)
    return hits, len(lines)

print("== tool_schemas finance names ==")
print(find("agent/tool_schemas.py", '"name": "finance_'))
print("== tool_profiles finance ==")
print(find("agent/tool_profiles.py", "finance_"))
print("== agent_toolkit dispatch ==")
hits, n = find("agent_toolkit.py", '"finance_')
print(hits)
print("== agent_toolkit defs ==")
lines = lines_of("agent_toolkit.py")
print([i+1 for i,l in enumerate(lines) if l.strip().startswith("def _finance_")])
print("== tools.py finance tool defs ==")
lines = lines_of("finance/tools.py")
print([i+1 for i,l in enumerate(lines) if l.strip().startswith("def ") and "series" in l or (l.strip().startswith("def ") and ("suppress" in l or "forecast" in l))])
print("== tools.py series-related defs ==")
print([i+1 for i,l in enumerate(lines) if l.strip().startswith("def ") and ("series" in l.lower() or "candidate" in l.lower())])
print("== chat.py finance refs ==")
print(find("finance/chat.py", "finance_"))
print("== query_reflector finance ==")
print(find("finance/query_reflector.py", "retry_"))
print("== tab.py forecast render ==")
print(find("finance/tab.py", "_render_forecast", "_tr(\"finance_ui.forecast"))
print("== db_schema forecast_suppressions ==")
print(find("finance/db_schema.py", "forecast_suppression", "forecast_plan_items"))
print("== i18n locale files ==")
import os
for d in ("i18n/locales", "i18n"):
    if os.path.isdir(d):
        print(d, os.listdir(d))
