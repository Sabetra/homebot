"""SOTA-Tests für utils.mermaid_diagram + chat_history_db FK-Härtung.

Deckt:
- Header-Erkennung & Primary-Block-Extraktion (Native-Mermaid-Eingabe)
- Sanitizer: Init-Direktiven & Frontmatter werden entfernt, Styling bleibt
- False-Positive-Schutz: Labels mit "click" im Text bleiben erhalten
- Fallback-Skeletons für alle deklarierten Diagramm-Typen
- _ensure_session_exists: FK-sichere Persistenz von Mermaid-Diagrammen
"""
from __future__ import annotations

import os
import tempfile

import pytest

from utils.mermaid_diagram import MermaidGenerator


# ---------------------------------------------------------------------------
# Native-Mermaid-Header-Erkennung
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "src, expected_type",
    [
        ("flowchart TD\nA-->B", "flowchart"),
        ("classDiagram\n  class Foo", "classDiagram"),
        ("sequenceDiagram\n  Alice->>Bob: Hi", "sequenceDiagram"),
        ("stateDiagram-v2\n  [*] --> Idle", "stateDiagram"),
        ("erDiagram\n  A ||--o{ B : has", "erDiagram"),
        ("mindmap\n  root", "mindmap"),
        ("pie\n  \"A\" : 1", "pie"),
        ("gantt\n  title T", "gantt"),
    ],
)
def test_header_detection_canonical(src: str, expected_type: str) -> None:
    diag = MermaidGenerator.from_text(src, diagram_type="flowchart", title="t")
    assert diag["type"] == expected_type
    # Originalquelle muss erhalten bleiben (keine step_X-Wrapping):
    assert "step_0" not in diag["code"]
    assert diag["data"]["source_mode"] == "raw_mermaid"


def test_primary_block_isolated_when_multiple_diagrams_pasted() -> None:
    src = (
        "flowchart TD\n"
        "    A --> B\n"
        "\n"
        "flowchart LR\n"
        "    X --> Y\n"
    )
    diag = MermaidGenerator.from_text(src, diagram_type="flowchart", title="t")
    assert diag["code"].startswith("flowchart TD")
    # Zweiter Block darf nicht mehr enthalten sein:
    assert "flowchart LR" not in diag["code"]
    assert "X --> Y" not in diag["code"]


# ---------------------------------------------------------------------------
# Sanitizer: nur echte Sicherheits-Threats blockiert
# ---------------------------------------------------------------------------

def test_sanitizer_strips_init_directive() -> None:
    src = (
        '%%{init: { "securityLevel": "loose" } }%%\n'
        "flowchart TD\n"
        "  A --> B"
    )
    cleaned = MermaidGenerator.sanitize_mermaid_code(src)
    assert "init" not in cleaned.lower()
    assert "securityLevel" not in cleaned
    assert "flowchart TD" in cleaned


def test_sanitizer_strips_initialize_directive() -> None:
    src = '%%{initialize: { "theme": "dark" } }%%\nflowchart TD\nA-->B'
    cleaned = MermaidGenerator.sanitize_mermaid_code(src)
    assert "initialize" not in cleaned.lower()
    assert "A-->B" in cleaned


def test_sanitizer_strips_frontmatter_block() -> None:
    src = (
        "---\n"
        "config:\n"
        "  securityLevel: loose\n"
        "---\n"
        "flowchart TD\n"
        "A --> B"
    )
    cleaned = MermaidGenerator.sanitize_mermaid_code(src)
    assert "securityLevel" not in cleaned
    assert "config:" not in cleaned
    assert "flowchart TD" in cleaned
    assert "A --> B" in cleaned


def test_sanitizer_strips_click_handler() -> None:
    src = (
        "flowchart TD\n"
        "  A --> B\n"
        '  click A "javascript:alert(1)"\n'
    )
    cleaned = MermaidGenerator.sanitize_mermaid_code(src)
    assert "javascript:" not in cleaned
    for line in cleaned.splitlines():
        assert not line.strip().lower().startswith("click ")


def test_sanitizer_preserves_legitimate_styling() -> None:
    """classDef / linkStyle / class sind reine CSS-Styling-Features ohne JS."""
    src = (
        "flowchart TD\n"
        "  A --> B\n"
        "  classDef important fill:#f96,stroke:#333\n"
        "  class A important\n"
        "  linkStyle 0 stroke:#0f0,stroke-width:2px\n"
    )
    cleaned = MermaidGenerator.sanitize_mermaid_code(src)
    assert "classDef important" in cleaned
    assert "class A important" in cleaned
    assert "linkStyle 0" in cleaned


def test_sanitizer_no_false_positive_on_label_containing_click() -> None:
    """Labels mit 'click' als Wortteil dürfen NICHT entfernt werden."""
    src = 'flowchart TD\n  A["User clicks the button"] --> B'
    cleaned = MermaidGenerator.sanitize_mermaid_code(src)
    assert 'A["User clicks the button"]' in cleaned


# ---------------------------------------------------------------------------
# Fallback-Skeletons
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "diagram_type, header_marker",
    [
        ("flowchart", "flowchart TD"),
        ("mindmap", "mindmap"),
        ("gantt", "gantt"),
        ("classDiagram", "classDiagram"),
        ("sequenceDiagram", "sequenceDiagram"),
        ("stateDiagram", "stateDiagram-v2"),
        ("erDiagram", "erDiagram"),
        ("pie", "pie"),
    ],
)
def test_fallback_skeleton_for_each_type(diagram_type: str, header_marker: str) -> None:
    diag = MermaidGenerator.from_text("freitext ohne header", diagram_type=diagram_type, title="t")
    assert diag["type"] == diagram_type
    assert header_marker in diag["code"]


def test_render_html_uses_parser_and_safe_json_payload() -> None:
    source = 'flowchart TD\nA["${alert(1)}` </script>"] --> B'
    html = MermaidGenerator.get_render_html(source)

    assert "await mermaid.parse(code)" in html
    assert "data-render-status" not in html
    assert "target.dataset.renderStatus = 'error'" in html
    assert "`${alert(1)}`" not in html
    assert "<\\/script>" in html


def test_export_html_uses_safe_json_payload() -> None:
    html = MermaidGenerator.get_export_html('flowchart TD\nA["${alert(1)}` </script>"] --> B')

    assert "`${alert(1)}`" not in html
    assert "<\\/script>" in html


# ---------------------------------------------------------------------------
# Persistenz-Layer FK-Härtung
# ---------------------------------------------------------------------------

def test_save_mermaid_diagram_creates_session_when_missing() -> None:
    """`_ensure_session_exists` darf FK-Verletzungen verhindern."""
    from database.chat_history_db import ChatHistoryDB

    # Windows: WAL-Sidecar-Dateien können nach close() kurz noch gelockt sein,
    # daher ignore_cleanup_errors=True (Python 3.10+).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "fk_test.db")
        db = ChatHistoryDB(db_path=db_path)
        try:
            diag_id = db.save_mermaid_diagram(
                session_id="brand_new_session_never_seen_before",
                diagram_type="flowchart",
                title="FK-Test",
                mermaid_code="flowchart TD\nA-->B",
                metadata={"source": "unit_test"},
            )
            assert diag_id  # Persistenz war erfolgreich
            diagrams = db.get_session_diagrams("brand_new_session_never_seen_before")
            assert len(diagrams) == 1
            assert diagrams[0]["mermaid_code"].startswith("flowchart TD")
        finally:
            db.close()
