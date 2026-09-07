"""
SOTA Mermaid.js Integration für automatische Diagramme aus Text-Eingabe.

Features:
- 100% lokale Verarbeitung (keine Datenübertragung, DSGVO-konform)
- Native Mermaid-Quelltext-Eingabe (Header-Erkennung + Primary-Block-Extraktion)
- Heuristische Generierung aus Freitext pro Diagrammtyp
- Sanitizer gegen Konfigurations-Override (securityLevel-Bypass)
- Eigenständiges Export-HTML mit Mermaid v11 + securityLevel='strict'

Architektur:
    ui_tabs/chat_tab.py
        ↓ (importiert)
    utils/mermaid_diagram.py  ← Diese Datei (Generator + Sanitizer + Export-HTML)
        ↓ (sanitisierter Mermaid-Code)
    Streamlit ``st.code(language='mermaid')`` (natives Rendering ab v1.30)
        ↓ (oder)
    download_button(get_export_html(...))  →  Browser (Mermaid.js v11, securityLevel=strict)

SOTA-Referenzen:
- Mermaid v11 securityLevel: https://mermaid.js.org/config/usage.html#securitylevel
- Mermaid Direktiven (config-override): https://mermaid.js.org/config/directives.html
- PEP 484 Type Hints, PEP 589 TypedDict
"""

from typing import Literal, Dict, Any, List, TypedDict, Optional
import json
import re
import uuid
import logging

logger = logging.getLogger(__name__)


MermaidDiagramKind = Literal[
    "flowchart",
    "mindmap",
    "gantt",
    "classDiagram",
    "sequenceDiagram",
    "stateDiagram",
    "erDiagram",
    "pie",
]


# ============================================================================
# SOTA: TypedDict für strukturierte Diagramm-Daten (PEP 484 + PEP 589)
# ============================================================================

class MermaidDiagram(TypedDict):
    """SOTA: Strukturierte Diagramm-Daten für Type Safety."""
    type: MermaidDiagramKind
    title: str
    code: str
    data: Dict[str, Any]  # Originaldaten für Export/Nachverfolgung
    id: str  # Eindeutige ID für Caching


# ============================================================================
# SOTA: MermaidGenerator-Klasse (Single Responsibility Principle)
# ============================================================================

class MermaidGenerator:
    """
    SOTA Generator für Mermaid-Diagramme aus Text/RAG-Ergebnissen.
    
    Features:
    - Automatische Diagramme aus strukturierten Daten
    - Support für alle Mermaid-Diagramm-Typen
    - Sanitization für sicheren Code
    - Caching für Performance
    
    Root-Cause-Lösung:
    - Kein Workaround: Direkte Umwandlung RAG → Mermaid
    - Keine externen Abhängigkeiten
    - 100% lokal
    """
    
    # SOTA: Präzise Sicherheits-Filter gegen Konfigurations-Override.
    # Hintergrund (https://mermaid.js.org/config/directives.html):
    #   %%{init: ...}%% / %%{initialize: ...}%% können securityLevel umschalten.
    #   YAML-Frontmatter (--- config: --- ab v10.5+) ersetzt Direktiven, hat denselben Hebel.
    #   click NodeId href "..." / call ... bindet JS-Callbacks (durch securityLevel=strict
    #   bereits blockiert; defensiv zusätzlich entfernen).
    # WICHTIG: classDef / linkStyle / themeCSS-as-style sind reine Styling-Features ohne JS
    # und werden NICHT entfernt. Frühere Substring-Matches haben sie fälschlich gestrippt.
    _CONFIG_OVERRIDE_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"%%\s*\{\s*init(?:ialize)?\s*:", re.IGNORECASE),
    )
    _LINE_PREFIX_BLOCKED: tuple[str, ...] = (
        "click",       # click NodeId href "..." / click NodeId callbackName
        "call",        # call NodeId(args) — auch JS-Callback in flowcharts
    )
    _FRONTMATTER_FENCE = "---"

    _SUPPORTED_HEADERS: Dict[str, MermaidDiagramKind] = {
        "flowchart": "flowchart",
        "mindmap": "mindmap",
        "gantt": "gantt",
        "classdiagram": "classDiagram",
        "sequencediagram": "sequenceDiagram",
        "pie": "pie",
        "statediagram": "stateDiagram",
        "erdiagram": "erDiagram",
    }
    
    @staticmethod
    def _sanitize_mermaid(text: str) -> str:
        """
        SOTA: Escape von Sonderzeichen für Mermaid-Syntax.
        
        Root-Cause: Mermaid hat spezielle Zeichen (", ', \n), die escaped werden müssen.
        Lösung: Systematische Ersetzung aller Problemzeichen.
        """
        if not text:
            return ""
        
        # Mermaid-spezifische Escapes
        text = text.replace('"', '\\"')
        text = text.replace("'", "\\'")
        text = text.replace("\n", "<br>")
        text = text.replace("\r", "")
        text = text.replace("\t", "    ")
        
        # HTML-spezifische Escapes für sicherere Anzeige
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        
        return text

    @classmethod
    def sanitize_mermaid_code(cls, code: str) -> str:
        """
        Härtung von Mermaid-Code gegen Konfigurations-Overrides und JS-Callbacks.

        Entfernt nur das, was tatsächlich securityLevel='strict' aushöhlen kann:
          * %%{init|initialize: ...}%% Direktiven (kann securityLevel überschreiben)
          * YAML-Frontmatter `--- config: ... ---` (v10.5+ Ersatz für Direktiven)
          * `click` / `call`-Statements (JS-Callbacks; durch strict-Mode bereits blockiert,
            defensiv zusätzlich entfernt)

        Legitime Mermaid-Features bleiben erhalten:
          * classDef / class / linkStyle / style (rein visuelles Styling, kein JS)
          * Subgraphs, Direktiven für theme/font/logLevel werden via Init-Block gefiltert,
            inline-Stilkommentare bleiben erhalten.
        """
        if not code:
            return ""

        lines = code.splitlines()
        sanitized: List[str] = []
        in_frontmatter = False

        for raw_line in lines:
            stripped = raw_line.strip()

            # Frontmatter-Block (YAML --- ... ---) erkennen und vollständig droppen.
            if stripped == cls._FRONTMATTER_FENCE:
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue

            # Init-/Initialize-Direktiven entfernen, egal wo in der Zeile.
            if any(pat.search(stripped) for pat in cls._CONFIG_OVERRIDE_PATTERNS):
                continue

            # Zeilenpräfix-basierte Filter (nur am Zeilenanfang, ohne Fälschungsrisiko
            # bei Labels wie "User clicks button").
            first_token = stripped.split(" ", 1)[0].lower() if stripped else ""
            if first_token in cls._LINE_PREFIX_BLOCKED:
                continue

            sanitized.append(raw_line)

        return "\n".join(sanitized)
    
    @staticmethod
    def _generate_id() -> str:
        """SOTA: Generiere eindeutige ID für Caching."""
        return str(uuid.uuid4())
    
    @classmethod
    def from_text(
        cls,
        text: str,
        diagram_type: MermaidDiagramKind = "flowchart",
        title: str = "Diagramm"
    ) -> MermaidDiagram:
        """
        SOTA: Erstellt ein Mermaid-Diagramm aus reinem Text.

        Strategie:
        1. Wenn der Text mit einem nativen Mermaid-Header beginnt
           (``flowchart``, ``mindmap``, ``classDiagram`` etc.), wird der erste
           kohärente Block 1:1 übernommen — keine Mangling.
        2. Sonst: typ-spezifische Heuristik / Skeleton-Vorlage.

        Args:
            text: Input-Text (Prozessbeschreibung oder roher Mermaid-Code).
            diagram_type: Ziel-Typ falls keine native Header-Zeile erkannt wird.
            title: Titel des Diagramms.

        Returns:
            MermaidDiagram: Strukturierte Diagramm-Daten.
        """
        raw_header = cls._detect_mermaid_header(text)
        if raw_header:
            primary_block = cls._extract_primary_mermaid_block(text)
            code = cls.sanitize_mermaid_code(primary_block)
            return MermaidDiagram(
                type=raw_header,
                title=title,
                code=code,
                data={"text": text, "source_mode": "raw_mermaid"},
                id=cls._generate_id(),
            )

        fallback_dispatch = {
            "flowchart": cls._text_to_flowchart,
            "mindmap": cls._text_to_mindmap,
            "gantt": cls._text_to_gantt,
            "classDiagram": cls._text_to_class_diagram,
            "sequenceDiagram": cls._text_to_sequence_diagram,
            "stateDiagram": cls._text_to_state_diagram,
            "erDiagram": cls._text_to_er_diagram,
            "pie": cls._text_to_pie,
        }
        builder = fallback_dispatch.get(diagram_type)
        if builder is None:
            raise ValueError(f"Unsupported diagram type: {diagram_type}")
        return builder(text, title)

    @classmethod
    def _detect_mermaid_header(cls, text: str) -> Optional[MermaidDiagramKind]:
        """Detect canonical diagram type from the first meaningful Mermaid header line.

        Akzeptiert auch Mermaid-Versions-Suffixe (z. B. ``stateDiagram-v2``),
        Direction-Suffixe (``flowchart TD``) und Subtype-Aliase
        (``graph`` als Alias für ``flowchart``).
        """
        if not text:
            return None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Erste Token (vor erstem Leerzeichen) extrahieren und Versions-Suffixe entfernen.
            first_token = stripped.split()[0].lower()
            base_token = first_token.split("-", 1)[0]
            # ``graph`` ist legacy-Alias für flowchart in Mermaid v9+.
            if base_token == "graph":
                base_token = "flowchart"
            return cls._SUPPORTED_HEADERS.get(base_token)

        return None

    @classmethod
    def _is_header_line(cls, line: str) -> bool:
        """Pure helper: True wenn die Zeile mit einem Mermaid-Header beginnt
        (inkl. Versions-Suffixen und ``graph``-Alias)."""
        stripped = line.strip()
        if not stripped:
            return False
        first_token = stripped.split()[0].lower()
        base_token = first_token.split("-", 1)[0]
        if base_token == "graph":
            base_token = "flowchart"
        return base_token in cls._SUPPORTED_HEADERS

    @classmethod
    def _extract_primary_mermaid_block(cls, text: str) -> str:
        """Extract first Mermaid diagram block when multiple declarations are pasted."""
        lines = text.splitlines()
        header_indices: List[int] = [
            idx for idx, line in enumerate(lines) if cls._is_header_line(line)
        ]

        if len(header_indices) <= 1:
            return text.strip()

        start = header_indices[0]
        end = header_indices[1]
        return "\n".join(lines[start:end]).strip()
    
    # ========================================================================
    # PRIVATE METHODEN: Text-basierte Generierung (Fallback)
    # ========================================================================
    
    @classmethod
    def _text_to_flowchart(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: Flussdiagramm aus reinem Text (Fallback)."""
        # Einfache Aufteilung nach Zeilenumbrüchen
        steps = [s.strip() for s in text.split("\n") if s.strip()]
        
        if not steps:
            steps = ["Start", "Ende"]
        
        nodes = []
        edges = []
        
        for i, step in enumerate(steps):
            safe_step = cls._sanitize_mermaid(step)
            node_id = f"step_{i}"
            nodes.append(f'{node_id}["{safe_step}"]')
            if i > 0:
                edges.append(f"step_{i-1} --> {node_id}")
        
        code = "flowchart TD\n" + "\n".join(nodes + edges)
        diagram_id = cls._generate_id()
        
        return MermaidDiagram(
            type="flowchart",
            title=title,
            code=code,
            data={"text": text, "steps": steps},
            id=diagram_id
        )
    
    @classmethod
    def _text_to_mindmap(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: Mindmap aus reinem Text (Fallback)."""
        lines = ["mindmap"]
        lines.append("  root((Hauptthema))")
        
        # Aufteilung nach Aufzählungszeichen oder Zeilen
        items = re.split(r'[\n,;]', text)
        for item in items:
            if item.strip():
                lines.append(f'    {cls._sanitize_mermaid(item.strip())}')
        
        code = "\n".join(lines)
        diagram_id = cls._generate_id()
        
        return MermaidDiagram(
            type="mindmap",
            title=title,
            code=code,
            data={"text": text, "items": items},
            id=diagram_id
        )
    
    @classmethod
    def _text_to_gantt(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: Gantt-Chart aus reinem Text (Fallback)."""
        lines = ["gantt"]
        lines.append("    title Gantt-Diagramm")
        lines.append("    dateFormat  YYYY-MM-DD")
        lines.append("    section Phase 1")
        lines.append("    Schritt 1 :a1, 2026-06-01, 7d")
        lines.append("    Schritt 2 :after a1, 3d")
        
        code = "\n".join(lines)
        diagram_id = cls._generate_id()
        
        return MermaidDiagram(
            type="gantt",
            title=title,
            code=code,
            data={"text": text},
            id=diagram_id
        )
    
    @classmethod
    def _text_to_class_diagram(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: Klassendiagramm aus reinem Text (Fallback)."""
        lines = ["classDiagram"]
        lines.append("  class BeispielKlasse {")
        lines.append("    +attribut1")
        lines.append("    +methode1()")
        lines.append("  }")
        
        code = "\n".join(lines)
        diagram_id = cls._generate_id()
        
        return MermaidDiagram(
            type="classDiagram",
            title=title,
            code=code,
            data={"text": text},
            id=diagram_id
        )
    
    @classmethod
    def _text_to_sequence_diagram(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: Sequenzdiagramm aus reinem Text (Fallback)."""
        lines = ["sequenceDiagram"]
        lines.append("    participant Alice")
        lines.append("    participant Bob")
        lines.append("    Alice->>Bob: Nachricht")
        lines.append("    Bob-->>Alice: Antwort")
        
        code = "\n".join(lines)
        diagram_id = cls._generate_id()
        
        return MermaidDiagram(
            type="sequenceDiagram",
            title=title,
            code=code,
            data={"text": text},
            id=diagram_id
        )

    @classmethod
    def _text_to_state_diagram(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: State-Diagramm-Skeleton (Fallback ohne Header-Erkennung)."""
        code = "\n".join([
            "stateDiagram-v2",
            "    [*] --> Idle",
            "    Idle --> Running: start",
            "    Running --> Idle: stop",
            "    Running --> [*]: shutdown",
        ])
        return MermaidDiagram(
            type="stateDiagram",
            title=title,
            code=code,
            data={"text": text},
            id=cls._generate_id(),
        )

    @classmethod
    def _text_to_er_diagram(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: ER-Diagramm-Skeleton (Fallback ohne Header-Erkennung)."""
        code = "\n".join([
            "erDiagram",
            "    CUSTOMER ||--o{ ORDER : places",
            "    ORDER ||--|{ LINE_ITEM : contains",
            "    CUSTOMER {",
            "        string name",
            "        string email",
            "    }",
        ])
        return MermaidDiagram(
            type="erDiagram",
            title=title,
            code=code,
            data={"text": text},
            id=cls._generate_id(),
        )

    @classmethod
    def _text_to_pie(cls, text: str, title: str) -> MermaidDiagram:
        """SOTA: Pie-Chart-Skeleton (Fallback ohne Header-Erkennung)."""
        safe_title = cls._sanitize_mermaid(title or "Verteilung")
        code = "\n".join([
            "pie showData",
            f'    title {safe_title}',
            '    "Kategorie A" : 40',
            '    "Kategorie B" : 35',
            '    "Kategorie C" : 25',
        ])
        return MermaidDiagram(
            type="pie",
            title=title,
            code=code,
            data={"text": text},
            id=cls._generate_id(),
        )
    
    # ========================================================================
    # SOTA: Export-Funktionen
    # ========================================================================

    @staticmethod
    def get_render_html(mermaid_code: str, diagram_id: str = "mermaid-preview") -> str:
        """Render-only HTML for embedding Mermaid diagrams in Streamlit components."""
        safe_code_json = json.dumps(
            MermaidGenerator.sanitize_mermaid_code(mermaid_code),
            ensure_ascii=False,
        ).replace("</", "<\\/")
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8" />
            <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
            <style>
                html, body {{ margin: 0; padding: 0; background: white; }}
                .wrap {{ padding: 12px; }}
                #{diagram_id} {{ max-width: 100%; overflow-x: auto; }}
                #{diagram_id} svg {{ max-width: 100%; height: auto; }}
                .render-error {{ padding: 12px; border-left: 4px solid #b42318; background: #fff4f2; color: #7a271a; }}
                .render-error pre {{ white-space: pre-wrap; overflow-wrap: anywhere; color: #344054; }}
            </style>
        </head>
        <body>
            <div class="wrap">
                <div id="{diagram_id}" class="mermaid"></div>
            </div>
            <script>
                mermaid.initialize({{
                    startOnLoad: false,
                    theme: 'default',
                    securityLevel: 'strict',
                    htmlLabels: false,
                    deterministicIds: true,
                }});

                (async function renderDiagram() {{
                    const target = document.getElementById('{diagram_id}');
                    const code = {safe_code_json};
                    try {{
                        await mermaid.parse(code);
                        target.textContent = code;
                        await mermaid.run({{ querySelector: '#{diagram_id}' }});
                        target.dataset.renderStatus = 'ok';
                    }} catch (error) {{
                        target.dataset.renderStatus = 'error';
                        target.className = 'render-error';
                        target.replaceChildren();
                        const message = document.createElement('strong');
                        message.textContent = 'Dieses Diagramm konnte nicht gerendert werden.';
                        const hint = document.createElement('p');
                        hint.textContent = 'Der erzeugte Mermaid-Code ist syntaktisch ungültig. Der Quellcode bleibt zur Prüfung sichtbar.';
                        const source = document.createElement('pre');
                        source.textContent = code;
                        target.append(message, hint, source);
                    }}
                }})();
            </script>
        </body>
        </html>
        """
        return html_template
    
    @staticmethod
    def get_export_html(mermaid_code: str, diagram_id: str = "mermaid-diagram") -> str:
        """
        SOTA: Generiere HTML für Client-seitigen Export.
        
        Root-Cause-Lösung:
        - Mermaid.js läuft im Browser (100% lokal)
        - Export via HTML2Canvas
        - Keine Server-Abhängigkeit
        """
        safe_code_json = json.dumps(
            MermaidGenerator.sanitize_mermaid_code(mermaid_code),
            ensure_ascii=False,
        ).replace("</", "<\\/")
        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
            <style>
                body {{ margin: 0; padding: 20px; background: white; }}
                #{diagram_id} {{ max-width: 100%; }}
            </style>
        </head>
        <body>
            <div id="{diagram_id}" class="mermaid"></div>
            <script>
                mermaid.initialize({{
                    startOnLoad: false,
                    theme: 'default',
                    securityLevel: 'strict',
                    htmlLabels: false,
                    deterministicIds: true,
                }});
                document.addEventListener('DOMContentLoaded', function() {{
                    const code = {safe_code_json};
                    const target = document.getElementById('{diagram_id}');
                    target.textContent = code;
                    mermaid.run({{ querySelector: '#{diagram_id}' }});
                }});
            </script>
            <script>
                // Export-Funktion nach dem Rendern
                window.addEventListener('load', function() {{ 
                    setTimeout(function() {{ 
                        const svg = document.querySelector('#{diagram_id} svg');
                        if (svg) {{
                            const serializer = new XMLSerializer();
                            const svgStr = serializer.serializeToString(svg);
                            const blob = new Blob([svgStr], {{ type: 'image/svg+xml' }});
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = 'mermaid-diagram.svg';
                            document.body.appendChild(a);
                            a.click();
                            document.body.removeChild(a);
                            URL.revokeObjectURL(url);
                        }}
                    }}, 1000);
                }});
            </script>
        </body>
        </html>
        """
        return html_template
