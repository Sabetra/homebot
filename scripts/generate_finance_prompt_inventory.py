"""Generator fuer ``docs/finance_prompt_inventory.md``.

Single source of truth = der Code. Diesem Skript werden die Finance-Module
importiert; alle System-Prompts und Tool-Schemas werden introspiziert und
das Inventory-Dokument deterministisch geschrieben.

Verwendungen
============

::

    # Doc neu schreiben (z.B. nach einer Prompt-Aenderung)
    python -m scripts.generate_finance_prompt_inventory --write

    # CI-Modus: pruefen ob das On-Disk-Doc mit dem Code uebereinstimmt
    python -m scripts.generate_finance_prompt_inventory --check

Zusaetzlich laeuft eine Encoding-Lint ueber alle extrahierten Prompt-Texte,
die historisch wiederkehrende bytes-stripped-Umlaut-Artefakte
(``fr`` statt ``fuer``, ``Mobilitt`` statt ``Mobilitaet`` usw.) als Fehler
meldet. Diese Liste ist klein und gezielt: jeder Token darin existiert in
korrektem Deutsch NICHT, d.h. ein Treffer ist immer ein Bug.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Repository root auf den Importpfad setzen, damit das Skript auch ohne
# installiertes Paket lauffaehig ist.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# pylint: disable=wrong-import-position
from agent import tool_schemas  # noqa: E402
from finance import categorizer as cat_mod  # noqa: E402
from finance import chat as chat_mod  # noqa: E402
from finance import extractor as ext_mod  # noqa: E402
from finance import query_planner as planner_mod  # noqa: E402
from finance import query_reflector as reflector_mod  # noqa: E402


DOC_PATH = _REPO_ROOT / "docs" / "finance_prompt_inventory.md"


# Bytes-stripped-Umlaut-Artefakte. Jeder dieser Tokens ist in korrektem Deutsch
# nicht existent, ein Treffer im Prompt ist daher zuverlaessig ein Bug.
_ENCODING_SMELL_TOKENS: Tuple[str, ...] = (
    " fr ",          # "fuer" / "für" mit gestripptem Umlaut
    " ber ",         # "ueber" / "über"
    "Mobilitt",      # Mobilitaet/Mobilität
    "persnliche",    # persoenliche/persönliche
    "Empfngern",     # Empfaengern/Empfängern
    "berweisungen",  # Ueberweisungen/Überweisungen
    "zurck",         # zurueck/zurück
    "knnen",         # koennen/können
    "mglich",        # moeglich/möglich
    "einschtzen",    # einschaetzen/einschätzen
    "Frderung",      # Foerderung/Förderung
    "Gesprch",       # Gespraech/Gespräch
    "rckwirkend",    # rueckwirkend/rückwirkend
    "Auszgen",       # Auszuegen/Auszügen
)


def _placeholders_for(builder_name: str) -> Dict[str, object]:
    """Sentinel-Argumente, mit denen die ``_build_prompt``-Methoden gerendert
    werden, damit das Template-Skelett im Doc sichtbar wird.
    """
    if builder_name == "planner":
        return {
            "question": "{question}",
            "schema_context": "{schema_context}",
            "available_tools": [],
            "reference_date": _dt.date(2026, 5, 14),
        }
    if builder_name == "reflector":
        return {
            "question": "{question}",
            "schema_context": "{schema_context}",
            "tool_trace": "{tool_trace}",
            "recent_tool_outputs": "{recent_tool_outputs}",
            "tool_names": "{tool_names}",
            "conversation_context": [],
        }
    raise ValueError(f"Unknown builder {builder_name!r}")


def _collect_prompts() -> List[Tuple[str, str, str, str]]:
    """Liefert eine Liste ``(section_id, title, source_file, body)``.

    Single point fuer Drift-Detection: jedes Prompt-Stueck wird hier zentral
    erfasst und sowohl in der Doc-Section 9 verbatim ausgegeben als auch von
    der Encoding-Lint gescannt.
    """
    planner_prompt = planner_mod.FinanceQueryPlanner._build_prompt(
        **_placeholders_for("planner"),  # type: ignore[arg-type]
    )
    reflector_prompt = reflector_mod.FinanceQueryReflector._build_prompt(
        **_placeholders_for("reflector"),  # type: ignore[arg-type]
    )
    return [
        (
            "9.1",
            "_FINANCE_SYSTEM_PROMPT",
            "finance/chat.py",
            chat_mod._FINANCE_SYSTEM_PROMPT,
        ),
        (
            "9.2",
            "Final synthesis system prompt in _final_synthesis(...)",
            "finance/chat.py",
            chat_mod._FINANCE_FINAL_SYNTHESIS_PROMPT,
        ),
        (
            "9.3",
            "Planner prompt template from FinanceQueryPlanner._build_prompt(...)",
            "finance/query_planner.py",
            planner_prompt,
        ),
        (
            "9.4",
            "Reflector prompt template from FinanceQueryReflector._build_prompt(...)",
            "finance/query_reflector.py",
            reflector_prompt,
        ),
        (
            "9.5",
            "_HEADER_SYSTEM_PROMPT (statement header extraction)",
            "finance/extractor.py",
            ext_mod._HEADER_SYSTEM_PROMPT,
        ),
        (
            "9.6",
            "_TX_SYSTEM_PROMPT (transaction extraction)",
            "finance/extractor.py",
            ext_mod._TX_SYSTEM_PROMPT,
        ),
        (
            "9.7",
            "_CATEGORIZER_SYSTEM_PROMPT (categorizer)",
            "finance/categorizer.py",
            cat_mod._CATEGORIZER_SYSTEM_PROMPT,
        ),
    ]


def _encoding_lint(prompts: List[Tuple[str, str, str, str]]) -> List[str]:
    """Findet bytes-stripped-Umlaut-Tokens in den extrahierten Prompt-Texten.

    Verwendet Unicode-Wortgrenzen (``(?<!\\w)`` / ``(?!\\w)``), damit z.B.
    ``berweisungen`` nicht innerhalb des korrekten Wortes ``Ueberweisungen``
    oder ``Ueberweisungen`` matcht (re.UNICODE ist in Python 3 default).
    """
    findings: List[str] = []
    patterns = [
        (token, re.compile(rf"(?<!\w){re.escape(token.strip())}(?!\w)", re.UNICODE))
        for token in _ENCODING_SMELL_TOKENS
    ]
    for section_id, title, source, body in prompts:
        for token, pat in patterns:
            if pat.search(body):
                findings.append(
                    f"  - section {section_id} ({source}, {title}): contains {token!r}"
                )
    return findings


def _finance_chat_routed_tools_block() -> str:
    routed = sorted(chat_mod._FINANCE_CHAT_ROUTED_TOOLS)
    return "\n".join(f"- {name}" for name in routed)


def _finance_tool_catalog_block() -> str:
    schemas = tool_schemas.get_finance_tool_schemas(include_code_executor=False)
    lines: List[str] = []
    for schema in sorted(schemas, key=lambda s: s["function"]["name"]):
        fn = schema["function"]
        name = fn["name"]
        desc = (fn.get("description") or "").strip().splitlines()
        first_line = desc[0] if desc else ""
        lines.append(f"- `{name}` — {first_line}")
    return "\n".join(lines)


def _reflector_actions_block() -> str:
    """Extrahiert die Action-Literal-Werte aus dem Reflector-Schema."""
    from typing import get_args, get_type_hints

    decision_cls = reflector_mod.FinanceContinuationDecision  # type: ignore[attr-defined]
    hints = get_type_hints(decision_cls)
    action_type = hints.get("action")
    if action_type is None:
        return "(action type not resolvable)"
    actions = get_args(action_type)
    return "\n".join(f"- `{a}`" for a in actions)


def _render() -> str:
    prompts = _collect_prompts()
    today = _dt.date.today().isoformat()
    routed_block = _finance_chat_routed_tools_block()
    catalog_block = _finance_tool_catalog_block()
    reflector_actions = _reflector_actions_block()

    parts: List[str] = []
    parts.append("# Finance Tab Prompt Inventory")
    parts.append("")
    parts.append(f"Last generated: {today}")
    parts.append("")
    parts.append(
        "> **Auto-generated** by `scripts/generate_finance_prompt_inventory.py`. "
        "Do NOT edit by hand. Re-run the generator after changing any finance "
        "prompt or tool schema (`python -m scripts.generate_finance_prompt_inventory --write`)."
    )
    parts.append("")
    parts.append(
        "This document lists all background prompts and prompt-like control "
        "texts that flow into the LLM in the Finance tab pipeline. The "
        "content below is extracted directly from the source modules; the "
        "generator and the `finance.chat` module also enforce a fail-fast "
        "consistency check between the prompt and the tool schemas."
    )
    parts.append("")

    parts.append("## 1) Finance Chat Runtime")
    parts.append("")
    parts.append("### 1.1 Base system prompt for FinanceChatEngine")
    parts.append("- File: `finance/chat.py`")
    parts.append("- Symbol: `_FINANCE_SYSTEM_PROMPT`")
    parts.append("- Applied in: `FinanceChatEngine.respond(...)` as the first system message.")
    parts.append("")
    parts.append("### 1.2 Final synthesis system prompt")
    parts.append("- File: `finance/chat.py`")
    parts.append("- Symbol: `_FINANCE_FINAL_SYNTHESIS_PROMPT`")
    parts.append("- Applied in: `FinanceChatEngine._final_synthesis(...)` (mode=`none`).")
    parts.append("")
    parts.append("### 1.3 Planner prompt")
    parts.append("- File: `finance/query_planner.py`")
    parts.append("- Symbol: `FinanceQueryPlanner._build_prompt(...)`")
    parts.append("- Applied in: `FinanceQueryPlanner.plan(...)`.")
    parts.append("")
    parts.append("### 1.4 Reflection gate prompt")
    parts.append("- File: `finance/query_reflector.py`")
    parts.append("- Symbol: `FinanceQueryReflector._build_prompt(...)`")
    parts.append("- Applied in: `FinanceQueryReflector.decide(...)`.")
    parts.append("- Supported continuation actions (from `FinanceReflectionDecision.action` literal):")
    parts.append("")
    parts.append(reflector_actions)
    parts.append("")

    parts.append("## 2) Chat-routed finance tools (single source of truth)")
    parts.append("")
    parts.append(
        "These tool names are explicitly enumerated in "
        "`finance.chat._FINANCE_CHAT_ROUTED_TOOLS` and must appear in "
        "`_FINANCE_SYSTEM_PROMPT`. The module-load validator "
        "`_validate_finance_prompt_coverage()` fails fast on any drift."
    )
    parts.append("")
    parts.append(routed_block)
    parts.append("")
    parts.append("## 3) Full finance tool catalog exposed to the LLM")
    parts.append("")
    parts.append(
        "All `finance_*` schemas returned by "
        "`agent.tool_schemas.get_finance_tool_schemas()`. The non-routed "
        "tools (admin/setup/listing) are reachable via their schema "
        "descriptions, without explicit prompt rules."
    )
    parts.append("")
    parts.append(catalog_block)
    parts.append("")

    parts.append("## 4) Schema context payload")
    parts.append("")
    parts.append("- File: `finance/db_schema.py`")
    parts.append("- Symbol: `FinanceDB.get_schema_context()` / `_build_schema_context_payload(...)`")
    parts.append("- Interpolated into planner and reflector prompts as `schema_context`.")
    parts.append("")

    parts.append("## 5) LLM runtime template layer")
    parts.append("")
    parts.append("- File: `scripts/model_loader.py`")
    parts.append(
        "- The model loader renders `tokenizer.chat_template` from the GGUF "
        "metadata; if the template does not support a `system` role, system "
        "content is merged into the first user turn."
    )
    parts.append("")

    parts.append("## 6) Entry point")
    parts.append("")
    parts.append("- File: `finance/tab.py`")
    parts.append("- Symbol: `_render_chat_tab()`")
    parts.append(
        "- Constructs `FinanceChatEngine(..., allow_python=True)` and passes "
        "user/assistant history into `engine.respond(...)`."
    )
    parts.append("")

    parts.append("## 7) Additional finance prompts outside the chat loop")
    parts.append("")
    parts.append("- `finance/extractor.py::_HEADER_SYSTEM_PROMPT` — statement header extraction")
    parts.append("- `finance/extractor.py::_TX_SYSTEM_PROMPT` — transaction extraction")
    parts.append("- `finance/categorizer.py::_CATEGORIZER_SYSTEM_PROMPT` — categorizer")
    parts.append("")

    parts.append("## 8) Request path map (Finance chat)")
    parts.append("")
    parts.append("1. `finance/tab.py` -> `FinanceChatEngine.respond(...)`")
    parts.append("2. `finance/chat.py` prepends `_FINANCE_SYSTEM_PROMPT` as system message")
    parts.append("3. `FinanceQueryPlanner._build_prompt(...)` produces the first plan")
    parts.append("4. Engine dispatches finance tool(s) and tool outputs are appended")
    parts.append("5. `FinanceQueryReflector._build_prompt(...)` decides continue vs done")
    parts.append("6. `_final_synthesis` adds `_FINANCE_FINAL_SYNTHESIS_PROMPT` and forces a non-tool answer")
    parts.append("7. `scripts/model_loader.py` renders the message stack through the GGUF chat template")
    parts.append("")

    parts.append("## 9) Verbatim prompt texts")
    parts.append("")
    parts.append(
        "All bodies below are extracted from the source modules at "
        "generation time. Dynamic prompts show interpolation placeholders in "
        "`{curly_braces}`."
    )
    parts.append("")
    for section_id, title, source, body in prompts:
        parts.append(f"### {section_id} {title}")
        parts.append(f"Source: `{source}`")
        parts.append("")
        parts.append("```text")
        parts.append(body.rstrip("\n"))
        parts.append("```")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="Schreibe das Inventory-Doc.")
    group.add_argument(
        "--check",
        action="store_true",
        help="Pruefe ob das On-Disk-Doc mit dem Code uebereinstimmt; nonzero exit bei Drift.",
    )
    args = parser.parse_args()

    prompts = _collect_prompts()
    encoding_findings = _encoding_lint(prompts)
    if encoding_findings:
        print("FAIL: encoding smell in finance prompts:", file=sys.stderr)
        for line in encoding_findings:
            print(line, file=sys.stderr)
        return 2

    rendered = _render()

    if args.write:
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {DOC_PATH} ({len(rendered)} chars)")
        return 0

    # --check
    if not DOC_PATH.exists():
        print(f"FAIL: {DOC_PATH} does not exist; run --write first", file=sys.stderr)
        return 1
    actual = DOC_PATH.read_text(encoding="utf-8")
    if actual != rendered:
        # Print a short diff for the operator.
        import difflib

        diff = list(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                rendered.splitlines(keepends=True),
                fromfile=str(DOC_PATH),
                tofile="generated-from-code",
                n=3,
            )
        )
        sys.stderr.write("FAIL: finance prompt inventory doc is out of sync with code.\n")
        sys.stderr.writelines(diff[:200])
        return 1
    print(f"OK {DOC_PATH} is in sync with code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
