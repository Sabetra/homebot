#!/usr/bin/env python3
"""
GENERISCHES VISUALISIERUNGS-TOOL FÜR DEN BOT
============================================

Ein universelles Tool, mit dem der Bot alle möglichen Diagramme erstellen kann:
- Netzwerk-Diagramme (Knowledge Graphs, Social Networks)
- Zeitachsen (Timelines, Gantt-Charts)
- Hierarchien (Org-Charts, Mind Maps, Tree Structures)
- Prozesse (Flussdiagramme, State Machines)
- Vergleiche (Balken, Radar, Scatter)
- Geografisch (Karten mit Markern)
- Graphviz-Diagramme (Dependency Graphs, State Machines, UML) - 100% lokal, DSGVO-konform
- Custom (Freiform-Sketches)

Der Bot gibt eine JSON/YAML-Beschreibung, das Tool rendert das Diagramm.

🔒 DATENSCHUTZ-HINWEIS:
- Alle Visualisierungen laufen 100% lokal ab.
- Graphviz nutzt das lokale 'dot'-Tool oder Python-Paket - KEINE Datenübertragung an Dritte!
- Mermaid.js wird von CDN geladen, aber im Browser isoliert ausgeführt.
- Internet-Bilder (Wikimedia/Unsplash) werden nur bei expliziter Anfrage geladen.

Beispiel für Graphviz:
{
    "type": "graphviz",
    "title": "Dependency Graph",
    "graph_type": "digraph",
    "format": "png",
    "nodes": [
        {"id": "main", "label": "Main Module"},
        {"id": "utils", "label": "Utils"}
    ],
    "edges": [
        {"source": "main", "target": "utils", "label": "imports"}
    ]
}

Oder mit direktem DOT-Code:
{
    "type": "graphviz",
    "dot_code": "digraph G { A -> B -> C; }"
}
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, Circle, Rectangle, Wedge, FancyArrowPatch
from matplotlib.collections import LineCollection
import numpy as np
from typing import Dict, List, Any, Optional, Tuple, Union
from datetime import timedelta
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
import networkx as nx

logger = logging.getLogger(__name__)


class DiagramType(Enum):
    """Unterstützte Diagramm-Typen"""
    NETWORK = "network"              # Netzwerk-Diagramm (Graph)
    TIMELINE = "timeline"            # Zeitachse
    HIERARCHY = "hierarchy"          # Hierarchie/Tree
    FLOWCHART = "flowchart"          # Flussdiagramm
    MINDMAP = "mindmap"              # Mind Map
    GANTT = "gantt"                  # Gantt-Chart
    COMPARISON = "comparison"        # Vergleichs-Diagramm
    SCATTER = "scatter"              # Scatter-Plot
    HEATMAP = "heatmap"              # Heatmap
    PIE = "pie"                      # Kreisdiagramm
    SANKEY = "sankey"                # Sankey-Diagramm (Flüsse)
    GRAPHVIZ = "graphviz"            # Graphviz-Diagramm (Dependency Graphs, State Machines) - 100% lokal, DSGVO-konform
    CUSTOM = "custom"                # Freiform


@dataclass
class VisualStyle:
    """Visuelle Stil-Konfiguration"""
    node_color: str = "#4A90E2"
    node_size: int = 600
    edge_color: str = "#666666"
    edge_width: float = 2.5
    font_size: int = 22
    font_color: str = "#2C3E50"
    background_color: str = "#FAFBFC"
    title_size: int = 32
    title_color: str = "#1A1A2E"
    use_grid: bool = False
    figsize: Tuple[int, int] = (28, 20)
    dpi: int = 150


def _adaptive_font_size(base_size: int, n_elements: int, min_size: int = 14) -> int:
    """Reduziert Schriftgröße adaptiv bei vielen Elementen."""
    if n_elements <= 8:
        return base_size
    elif n_elements <= 15:
        return max(min_size, base_size - 2)
    elif n_elements <= 25:
        return max(min_size, base_size - 4)
    else:
        return max(min_size, base_size - 6)


def _adaptive_node_size(base_size: int, n_nodes: int) -> int:
    """Reduziert Knotengröße adaptiv bei vielen Knoten."""
    if n_nodes <= 10:
        return base_size
    elif n_nodes <= 20:
        return int(base_size * 0.7)
    else:
        return int(base_size * 0.5)


class GenericVisualizationTool:
    """
    🎨 Generisches Visualisierungs-Tool
    
    Der Bot kann beliebige Diagramme erstellen, indem er eine JSON-Struktur übergibt:
    
    📌 Unterstützte Typen:
    - network: Netzwerk-Diagramme (Knowledge Graphs, Social Networks)
    - timeline: Zeitachsen
    - hierarchy: Hierarchien (Org-Charts, Mind Maps, Tree Structures)
    - flowchart: Flussdiagramme
    - mindmap: Mind Maps
    - gantt: Gantt-Charts
    - comparison: Vergleichsdiagramme (Balken, Radar, gruppierte Balken)
    - scatter: Streudiagramme
    - heatmap: Heatmaps
    - pie: Kreisdiagramme
    - sankey: Sankey-Diagramme (Fluss-Visualisierung)
    - graphviz: Graphviz-Diagramme (Dependency Graphs, State Machines, UML) - 🔒 100% lokal
    - custom: Freiform-Diagramme
    
    Beispiel für Netzwerk:
    {
        "type": "network",
        "title": "Künstler-Netzwerk des 20. Jahrhunderts",
        "nodes": [
            {"id": "picasso", "label": "Pablo Picasso", "color": "#FF6B6B", "size": 500},
            {"id": "braque", "label": "Georges Braque", "color": "#4ECDC4"}
        ],
        "edges": [
            {"source": "picasso", "target": "braque", "label": "Kubismus (1907)"}
        ],
        "style": {
            "figsize": [28, 20],
            "background_color": "#F8F9FA"
        }
    }
    
    Beispiel für Graphviz (Dependency Graph):
    {
        "type": "graphviz",
        "title": "Modul-Abhängigkeiten",
        "graph_type": "digraph",
        "format": "png",
        "nodes": [
            {"id": "main", "label": "Hauptmodul"},
            {"id": "utils", "label": "Hilfsfunktionen"}
        ],
        "edges": [
            {"source": "main", "target": "utils", "label": "importiert"}
        ]
    }
    
    Beispiel für Graphviz mit DOT-Code (für Experten):
    {
        "type": "graphviz",
        "dot_code": "digraph G { A -> B -> C; }"
    }
    
    🔒 DATENSCHUTZ: Alle Visualisierungen laufen 100% lokal. Graphviz nutzt das lokale
    'dot'-Tool oder Python-Paket - KEINE Datenübertragung an Dritte (DSGVO-konform)!
    """
    
    def __init__(self):
        self.supported_types = list(DiagramType)
        logger.info(f"🎨 Generisches Visualisierungs-Tool initialisiert ({len(self.supported_types)} Typen)")
    
    def visualize(self, 
                  description: Union[Dict[str, Any], str],
                  output_path: str = "visualization.png") -> str:
        """
        Erstellt eine Visualisierung basierend auf der Beschreibung.
        
        Args:
            description: JSON-Dict oder JSON-String mit Diagramm-Beschreibung
            output_path: Pfad für das Ausgabe-Bild
            
        Returns:
            Pfad zum erstellten Bild
        """
        
        # Parse description
        if isinstance(description, str):
            try:
                desc = json.loads(description)
            except json.JSONDecodeError as e:
                raise ValueError(f"Ungültige JSON-Beschreibung: {e}")
        else:
            desc = description
        
        # Bestimme Diagramm-Typ
        diagram_type_str = desc.get("type", "network").lower()
        
        try:
            diagram_type = DiagramType(diagram_type_str)
        except ValueError:
            raise ValueError(f"Unbekannter Diagramm-Typ: {diagram_type_str}. "
                           f"Unterstützt: {[t.value for t in DiagramType]}")
        
        # Parse Style
        style_dict = desc.get("style", {})
        if not isinstance(style_dict, dict):
            style_dict = {}
        style = VisualStyle(**style_dict)
        
        # Route zu spezifischer Visualisierungs-Methode
        if diagram_type == DiagramType.NETWORK:
            return self._create_network(desc, style, output_path)
        elif diagram_type == DiagramType.TIMELINE:
            return self._create_timeline(desc, style, output_path)
        elif diagram_type == DiagramType.HIERARCHY:
            return self._create_hierarchy(desc, style, output_path)
        elif diagram_type == DiagramType.FLOWCHART:
            return self._create_flowchart(desc, style, output_path)
        elif diagram_type == DiagramType.MINDMAP:
            return self._create_mindmap(desc, style, output_path)
        elif diagram_type == DiagramType.GANTT:
            return self._create_gantt(desc, style, output_path)
        elif diagram_type == DiagramType.COMPARISON:
            return self._create_comparison(desc, style, output_path)
        elif diagram_type == DiagramType.SCATTER:
            return self._create_scatter(desc, style, output_path)
        elif diagram_type == DiagramType.HEATMAP:
            return self._create_heatmap(desc, style, output_path)
        elif diagram_type == DiagramType.PIE:
            return self._create_pie(desc, style, output_path)
        elif diagram_type == DiagramType.SANKEY:
            return self._create_sankey(desc, style, output_path)
        elif diagram_type == DiagramType.GRAPHVIZ:
            return self._create_graphviz(desc, style, output_path)
        elif diagram_type == DiagramType.CUSTOM:
            return self._create_custom(desc, style, output_path)
        else:
            raise NotImplementedError(f"Diagramm-Typ {diagram_type} noch nicht implementiert")
    
    def _create_network(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Netzwerk-Diagramm (Graph) mit adaptivem Layout"""
        
        # Erstelle NetworkX Graph (DiGraph für gerichtete Kanten mit Pfeilen)
        directed = desc.get("directed", False)
        G = nx.DiGraph() if directed else nx.Graph()
        
        # Parse Nodes
        nodes = desc.get("nodes", [])
        node_colors = {}
        node_sizes = {}
        node_labels = {}
        
        # Adaptive Sizing basierend auf Knotenanzahl
        n_nodes = len(nodes)
        adaptive_size = _adaptive_node_size(style.node_size, n_nodes)
        adaptive_font = _adaptive_font_size(style.font_size, n_nodes)
        
        for node in nodes:
            node_id = node["id"]
            G.add_node(node_id)
            node_labels[node_id] = node.get("label", node_id)
            node_colors[node_id] = node.get("color", style.node_color)
            node_sizes[node_id] = node.get("size", adaptive_size)
        
        # Parse Edges
        edges = desc.get("edges", [])
        edge_labels = {}
        
        for edge in edges:
            source = edge["source"]
            target = edge["target"]
            G.add_edge(source, target)
            if "label" in edge:
                edge_labels[(source, target)] = edge["label"]
        
        # Layout
        layout_type = desc.get("layout", "spring")
        
        # Adaptive k-Parameter für spring_layout basierend auf Knotenanzahl
        k_factor = max(1.5, 3.0 - n_nodes * 0.08) if n_nodes > 5 else 2.5
        
        if layout_type == "spring":
            pos = nx.spring_layout(G, k=k_factor, iterations=80, seed=42)
        elif layout_type == "circular":
            pos = nx.circular_layout(G)
        elif layout_type == "kamada_kawai":
            pos = nx.kamada_kawai_layout(G)
        elif layout_type == "shell":
            pos = nx.shell_layout(G)
        else:
            pos = nx.spring_layout(G, k=k_factor, iterations=80, seed=42)
        
        # Erstelle Figure
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        
        # Zeichne Nodes
        for node in G.nodes():
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=[node],
                node_color=node_colors.get(node, style.node_color),
                node_size=node_sizes.get(node, style.node_size),
                ax=ax
            )
        
        # Zeichne Edges (mit Pfeilen bei gerichteten Graphen)
        if directed:
            nx.draw_networkx_edges(
                G, pos,
                edge_color=style.edge_color,
                width=style.edge_width,
                arrows=True,
                arrowsize=20,
                arrowstyle='-|>',
                connectionstyle='arc3,rad=0.1',
                ax=ax
            )
        else:
            nx.draw_networkx_edges(
                G, pos,
                edge_color=style.edge_color,
                width=style.edge_width,
                ax=ax
            )
        
        # Zeichne Labels
        nx.draw_networkx_labels(
            G, pos,
            labels=node_labels,
            font_size=adaptive_font,
            font_color=style.font_color,
            ax=ax
        )
        
        # Zeichne Edge-Labels
        if edge_labels:
            nx.draw_networkx_edge_labels(
                G, pos,
                edge_labels=edge_labels,
                font_size=max(14, adaptive_font - 4),
                ax=ax
            )
        
        # Titel
        title = desc.get("title", "Netzwerk-Diagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=20)
        
        ax.axis('off')
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Netzwerk-Diagramm erstellt: {output_path}")
        return output_path
    
    def _create_timeline(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt eine SOTA-Zeitachse mit Descriptions, Phasen-Gruppierung und Smart-Spacing."""
        import textwrap
        
        events = desc.get("events", [])
        if not events:
            raise ValueError("Timeline benötigt 'events'-Liste")
        
        # ═══ Parse Events ═══
        event_data = []
        for event in events:
            year = event.get("year", event.get("date", 0))
            # Unterstütze "2021-2022" Bereiche
            if isinstance(year, str) and "-" in year:
                try:
                    year = int(year.split("-")[0])
                except ValueError:
                    year = 0
            event_data.append({
                "year": int(year) if year else 0,
                "label": event.get("label", "Event"),
                "description": event.get("description", ""),
                "color": event.get("color", ""),
                "phase": event.get("phase", ""),
            })
        
        event_data.sort(key=lambda x: x["year"])
        n_events = len(event_data)
        
        # ═══ Adaptive Farben pro Phase/Event ═══
        phase_colors = [
            "#2E86DE", "#10AC84", "#EE5A24", "#6C5CE7",
            "#F39C12", "#E74C3C", "#1ABC9C", "#8E44AD",
        ]
        # Phasen sammeln und Farben zuweisen
        phases_seen: Dict[str, str] = {}
        for i, ev in enumerate(event_data):
            phase = ev["phase"] or f"phase_{ev['year']}"
            if not ev["color"]:
                if phase not in phases_seen:
                    phases_seen[phase] = phase_colors[len(phases_seen) % len(phase_colors)]
                ev["color"] = phases_seen[phase]
        
        # ═══ Adaptive Sizing ═══
        adaptive_font = _adaptive_font_size(style.font_size, n_events, min_size=16)
        desc_font = max(14, adaptive_font - 4)
        
        # Figure -- breiter bei vielen Events
        fig_w = max(style.figsize[0], n_events * 5.0)
        fig_h = max(style.figsize[1], 16)
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        years = [e["year"] for e in event_data]
        year_min, year_max = min(years), max(years)
        year_span = max(1, year_max - year_min)
        margin = max(1, year_span * 0.08)
        
        # ═══ Normalisierte X-Positionen (spread events evenly if clustered) ═══
        # Wenn Events zu nah beieinander liegen, verteile sie gleichmäßig
        x_positions = []
        for i, ev in enumerate(event_data):
            x_positions.append(ev["year"])
        
        # Mindestabstand erzwingen (verhindert Überlappung)
        min_gap = year_span * 0.06 if year_span > 0 else 1
        for i in range(1, len(x_positions)):
            if x_positions[i] - x_positions[i-1] < min_gap:
                x_positions[i] = x_positions[i-1] + min_gap
        
        x_min = x_positions[0] - margin
        x_max = x_positions[-1] + margin
        
        # ═══ Zentrale Zeitachse ═══
        y_axis = 0.50
        ax.plot([x_min, x_max], [y_axis, y_axis],
                color=style.edge_color, linewidth=4.0, solid_capstyle='round', zorder=2)
        
        # ═══ Jahr-Ticks auf der Achse ═══
        for x_pos, ev in zip(x_positions, event_data):
            ax.text(x_pos, y_axis - 0.03, str(ev["year"]),
                    ha='center', va='top', fontsize=max(14, adaptive_font - 2),
                    color=style.edge_color, fontweight='bold', zorder=5)
        
        # ═══ Events zeichnen ═══
        for i, (ev, x_pos) in enumerate(zip(event_data, x_positions)):
            is_above = (i % 2 == 0)
            
            # Marker auf der Achse
            ax.scatter(x_pos, y_axis, s=360, color=ev["color"], zorder=10,
                       edgecolors='white', linewidth=3)
            
            # ─── Label + Description Card ───
            # Abwechselnd oben/unten, mit zunehmendem Offset bei vielen Events
            base_offset = 0.12
            if is_above:
                card_y = y_axis + base_offset + 0.02
                va = 'bottom'
                stem_end = y_axis + base_offset * 0.8
            else:
                card_y = y_axis - base_offset - 0.02
                va = 'top'
                stem_end = y_axis - base_offset * 0.8
            
            # Verbindungslinie (Stiel)
            ax.plot([x_pos, x_pos], [y_axis, stem_end],
                    color=ev["color"], linewidth=2.5, linestyle='-', alpha=0.6, zorder=3)
            
            # Label (Titel des Events)
            wrapped_label = textwrap.fill(ev["label"], width=30)
            ax.text(x_pos, card_y, wrapped_label,
                    ha='center', va=va,
                    fontsize=adaptive_font, fontweight='bold', color=style.font_color,
                    bbox=dict(boxstyle='round,pad=0.6', facecolor=ev["color"],
                              alpha=0.15, edgecolor=ev["color"], linewidth=2),
                    zorder=6)
            
            # Description (unter/über dem Label)
            if ev["description"]:
                wrapped_desc = textwrap.fill(ev["description"], width=36)
                desc_offset = 0.06 * (wrapped_label.count('\n') + 1)
                if is_above:
                    desc_y = card_y + desc_offset + 0.04
                else:
                    desc_y = card_y - desc_offset - 0.04
                
                ax.text(x_pos, desc_y, wrapped_desc,
                        ha='center', va=va,
                        fontsize=desc_font, color='#555555', style='italic',
                        zorder=5)
        
        # ═══ Phasen-Balken (optional, wenn Phasen definiert) ═══
        if phases_seen and len(phases_seen) > 1:
            phase_events: Dict[str, list] = {}
            for ev, x_pos in zip(event_data, x_positions):
                p = ev["phase"] or f"phase_{ev['year']}"
                phase_events.setdefault(p, []).append(x_pos)
            
            legend_y = 0.92
            for j, (phase_name, positions) in enumerate(phase_events.items()):
                if len(positions) >= 2:
                    px_min = min(positions) - min_gap * 0.3
                    px_max = max(positions) + min_gap * 0.3
                    color = phases_seen.get(phase_name, phase_colors[j % len(phase_colors)])
                    ax.axvspan(px_min, px_max, ymin=0.46, ymax=0.54,
                               alpha=0.08, color=color, zorder=1)
        
        # ═══ Titel ═══
        title = desc.get("title", "Zeitachse")
        ax.set_title(title, fontsize=style.title_size + 2, color=style.title_color,
                     pad=25, fontweight='bold')
        
        # ═══ Achsen ═══
        ax.set_ylim(0.15, 0.88)
        ax.set_xlim(x_min, x_max)
        ax.axis('off')
        
        plt.tight_layout(pad=1.5)
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
        plt.close()
        
        logger.info(f"✅ Timeline erstellt: {output_path} ({n_events} Events)")
        return output_path
    
    def _create_hierarchy(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Hierarchie-Diagramm mit echtem Tree-Layout (Buchheim et al.)"""
        
        # Verwende NetworkX DiGraph für Hierarchie
        G = nx.DiGraph()
        
        # === Format 1: nodes-Liste mit id/parent ===
        nodes = desc.get("nodes", [])
        
        if nodes:
            for node in nodes:
                node_id = node["id"]
                G.add_node(node_id, label=node.get("label", node_id),
                           color=node.get("color", style.node_color))
                if "parent" in node:
                    G.add_edge(node["parent"], node_id)
        else:
            # === Format 2: root + children (nested) ===
            root_name = desc.get("root", "Root")
            G.add_node(root_name, label=root_name, color=style.node_color)
            
            def _add_children(parent_id, children_list):
                for child in children_list:
                    if isinstance(child, str):
                        G.add_node(child, label=child, color=style.node_color)
                        G.add_edge(parent_id, child)
                    elif isinstance(child, dict):
                        child_id = child.get("name", child.get("id", str(child)))
                        G.add_node(child_id, label=child_id,
                                   color=child.get("color", style.node_color))
                        G.add_edge(parent_id, child_id)
                        if "children" in child:
                            _add_children(child_id, child["children"])
            
            _add_children(root_name, desc.get("children", []))
        
        # Finde Root(s) -- Knoten ohne eingehende Kanten
        if len(G.nodes()) == 0:
            raise ValueError("Hierarchy hat keine Knoten")
        
        roots = [n for n in G.nodes() if G.in_degree(n) == 0]
        if not roots:
            roots = [list(G.nodes())[0]]
        
        # Echtes hierarchisches Top-Down-Layout
        pos = self._tree_layout(G, roots[0])
        
        n_nodes = len(G.nodes())
        adaptive_font = _adaptive_font_size(style.font_size, n_nodes)
        adaptive_size = _adaptive_node_size(style.node_size, n_nodes)
        
        # Erstelle Figure
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        # Zeichne Kanten (von oben nach unten)
        for u, v in G.edges():
            if u in pos and v in pos:
                ax.annotate('', xy=pos[v], xytext=pos[u],
                           arrowprops=dict(arrowstyle='->', color=style.edge_color,
                                          lw=style.edge_width, connectionstyle='arc3,rad=0'))
        
        # Zeichne Knoten
        node_attrs = nx.get_node_attributes(G, 'label')
        node_colors_attr = nx.get_node_attributes(G, 'color')
        
        for node in G.nodes():
            if node not in pos:
                continue
            x, y = pos[node]
            color = node_colors_attr.get(node, style.node_color)
            label = node_attrs.get(node, node)
            
            # Runder Kasten
            bbox = dict(boxstyle='round,pad=0.4', facecolor=color, edgecolor='white',
                       lw=2, alpha=0.9)
            ax.text(x, y, label, ha='center', va='center',
                   fontsize=adaptive_font, fontweight='bold', color='white',
                   bbox=bbox, zorder=5)
        
        # Titel
        title = desc.get("title", "Hierarchie-Diagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, 
                     pad=20, fontweight='bold')
        
        ax.axis('off')
        ax.margins(0.15)
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close()
        
        logger.info(f"✅ Hierarchie-Diagramm erstellt: {output_path}")
        return output_path
    
    @staticmethod
    def _tree_layout(G: nx.DiGraph, root, width: float = 1.0, vert_gap: float = 0.2) -> Dict:
        """Berechnet ein echtes Top-Down Tree-Layout (kein spring_layout)."""
        pos = {}
        
        def _bfs_assign(node, left, right, depth):
            children = list(G.successors(node))
            pos[node] = ((left + right) / 2, -depth * vert_gap)
            
            if children:
                segment = (right - left) / len(children)
                for i, child in enumerate(children):
                    child_left = left + i * segment
                    child_right = child_left + segment
                    _bfs_assign(child, child_left, child_right, depth + 1)
        
        _bfs_assign(root, 0, width, 0)
        return pos
    
    def _create_flowchart(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Flussdiagramm mit verschiedenen Shapes"""
        
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        
        steps = desc.get("steps", [])
        if not steps:
            raise ValueError("Flowchart benötigt 'steps'-Liste")
        
        # Berechne Positionen (vertikal von oben nach unten)
        n_steps = len(steps)
        y_positions = np.linspace(0.9, 0.1, n_steps)
        x_center = 0.5
        
        prev_y = None
        
        for i, step in enumerate(steps):
            step_type = step.get("type", "process")  # start, process, decision, end
            label = step.get("label", f"Step {i+1}")
            color = step.get("color", style.node_color)
            y = y_positions[i]
            
            # Zeichne Shape basierend auf Typ
            if step_type == "start" or step_type == "end":
                # Abgerundetes Rechteck (Oval)
                shape = FancyBboxPatch(
                    (x_center - 0.12, y - 0.03), 0.24, 0.06,
                    boxstyle="round,pad=0.01,rounding_size=0.02",
                    facecolor=color, edgecolor='black', linewidth=2
                )
            elif step_type == "decision":
                # Raute für Entscheidungen
                diamond = patches.Polygon(
                    [(x_center, y + 0.04), (x_center + 0.1, y), 
                     (x_center, y - 0.04), (x_center - 0.1, y)],
                    facecolor=color, edgecolor='black', linewidth=2
                )
                ax.add_patch(diamond)
                shape = None
            else:
                # Standard-Rechteck für Prozesse
                shape = FancyBboxPatch(
                    (x_center - 0.12, y - 0.03), 0.24, 0.06,
                    boxstyle="square,pad=0.01",
                    facecolor=color, edgecolor='black', linewidth=2
                )
            
            if shape:
                ax.add_patch(shape)
            
            # Label
            ax.text(x_center, y, label, ha='center', va='center',
                   fontsize=style.font_size, fontweight='bold')
            
            # Pfeil zum nächsten Step
            if prev_y is not None:
                ax.annotate('', xy=(x_center, y + 0.035), xytext=(x_center, prev_y - 0.035),
                           arrowprops=dict(arrowstyle='->', color='black', lw=1.5))
            
            prev_y = y
        
        # Titel
        title = desc.get("title", "Flussdiagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=20)
        
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Flussdiagramm erstellt: {output_path}")
        return output_path
    
    def _create_mindmap(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt eine echte radiale Mind Map mit zentralem Thema und Zweigen.
        
        Erwartete JSON-Struktur:
        {
            "type": "mindmap",
            "title": "Mind Map Titel",
            "central": "Hauptthema",
            "branches": [
                {"label": "Zweig 1", "color": "#FF6B6B", "children": [
                    {"label": "Unterpunkt 1.1"},
                    {"label": "Unterpunkt 1.2"}
                ]},
                {"label": "Zweig 2", "color": "#4ECDC4", "children": [...]}
            ]
        }
        
        Fallback: Wenn 'branches' fehlt aber 'nodes' vorhanden → delegiere an hierarchy.
        """
        branches = desc.get("branches", [])
        central = desc.get("central", desc.get("title", "Thema"))
        
        # Fallback auf Hierarchie wenn keine branches-Struktur
        if not branches and desc.get("nodes"):
            return self._create_hierarchy(desc, style, output_path)
        
        if not branches:
            branches = [{"label": "Zweig 1"}, {"label": "Zweig 2"}, {"label": "Zweig 3"}]
        
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        # Farbpalette für Zweige
        default_colors = [
            "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
            "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
            "#F0B27A", "#82E0AA",
        ]
        
        n_branches = len(branches)
        
        # Zentraler Knoten
        central_circle = plt.Circle((0.5, 0.5), 0.08, color="#2C3E50", ec="white", lw=3, zorder=10)
        ax.add_patch(central_circle)
        ax.text(0.5, 0.5, central, ha='center', va='center',
                fontsize=style.font_size + 3, fontweight='bold', color='white',
                wrap=True, zorder=11)
        
        # Zweige radial verteilen
        for i, branch in enumerate(branches):
            angle = (2 * np.pi * i / n_branches) - np.pi / 2  # Start oben
            color = branch.get("color", default_colors[i % len(default_colors)])
            label = branch.get("label", f"Zweig {i+1}")
            children = branch.get("children", [])
            
            # Position des Zweig-Knotens
            r_branch = 0.28
            bx = 0.5 + r_branch * np.cos(angle)
            by = 0.5 + r_branch * np.sin(angle)
            
            # Verbindungslinie zum Zentrum (geschwungen)
            ax.annotate('', xy=(bx, by), xytext=(0.5, 0.5),
                       arrowprops=dict(arrowstyle='-', color=color, lw=3, 
                                       connectionstyle='arc3,rad=0.1'))
            
            # Zweig-Knoten
            branch_circle = plt.Circle((bx, by), 0.05, color=color, ec='white', lw=2, zorder=8)
            ax.add_patch(branch_circle)
            
            # Adaptive Schriftgröße für den Zweig
            branch_font = _adaptive_font_size(style.font_size + 1, n_branches)
            ax.text(bx, by, label, ha='center', va='center',
                    fontsize=branch_font, fontweight='bold', color='white', zorder=9)
            
            # Kinder radial um den Zweig-Knoten
            n_children = len(children)
            for j, child in enumerate(children):
                # Kinder fächern sich im Sektor des Eltern-Zweigs auf
                child_angle_spread = np.pi / max(3, n_branches)
                child_angle = angle - child_angle_spread / 2 + (child_angle_spread * j / max(1, n_children - 1)) if n_children > 1 else angle
                
                r_child = 0.16
                cx = bx + r_child * np.cos(child_angle)
                cy = by + r_child * np.sin(child_angle)
                
                # Clamp to figure bounds
                cx = max(0.05, min(0.95, cx))
                cy = max(0.05, min(0.95, cy))
                
                # Verbindungslinie
                ax.plot([bx, cx], [by, cy], color=color, lw=1.5, alpha=0.6, zorder=5)
                
                # Kindknoten
                child_label = child.get("label", f"Sub {j+1}") if isinstance(child, dict) else str(child)
                child_font = _adaptive_font_size(style.font_size - 1, n_branches * max(1, n_children))
                
                ax.text(cx, cy, child_label, ha='center', va='center',
                        fontsize=child_font, color=style.font_color,
                        bbox=dict(boxstyle='round,pad=0.3', facecolor=color, alpha=0.2, edgecolor=color),
                        zorder=6)
        
        # Titel
        title = desc.get("title", "Mind Map")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=20, fontweight='bold')
        
        ax.set_xlim(-0.05, 1.05)
        ax.set_ylim(-0.05, 1.05)
        ax.set_aspect('equal')
        ax.axis('off')
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close()
        
        logger.info(f"✅ Mind Map erstellt: {output_path} ({n_branches} Zweige)")
        return output_path
    
    def _create_gantt(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Gantt-Chart (Projektplan-Visualisierung)
        
        Erwartete JSON-Struktur:
        {
            "type": "gantt",
            "title": "Projekt-Zeitplan",
            "tasks": [
                {"name": "Task 1", "start": "2024-01-01", "end": "2024-02-15", "color": "#FF6B6B", "progress": 80},
                {"name": "Task 2", "start": "2024-02-01", "end": "2024-03-30", "color": "#4ECDC4", "progress": 50}
            ],
            "milestones": [
                {"name": "Release 1.0", "date": "2024-02-15", "color": "#FFD93D"}
            ]
        }
        """
        from datetime import datetime
        
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        
        tasks = desc.get("tasks", [])
        milestones = desc.get("milestones", [])
        
        if not tasks:
            raise ValueError("Gantt-Chart benötigt 'tasks'-Liste")
        
        # Parse Tasks
        task_data = []
        for task in tasks:
            start_val = task.get("start", 0)
            end_val = task.get("end", None)
            duration_val = task.get("duration", None)
            
            if isinstance(start_val, (int, float)):
                # Numerisches Format: start=Offset in Tagen, duration=Tage
                from datetime import datetime
                base_date = datetime(2024, 1, 1)
                start = base_date + timedelta(days=int(start_val))
                if duration_val is not None:
                    end = start + timedelta(days=int(duration_val))
                elif end_val is not None and isinstance(end_val, (int, float)):
                    end = base_date + timedelta(days=int(end_val))
                else:
                    end = start + timedelta(days=7)
            else:
                # Datumsstring-Format
                try:
                    start = datetime.strptime(str(start_val), "%Y-%m-%d")
                    if end_val:
                        end = datetime.strptime(str(end_val), "%Y-%m-%d")
                    elif duration_val:
                        end = start + timedelta(days=int(duration_val))
                    else:
                        end = start + timedelta(days=7)
                except (ValueError, TypeError):
                    start = datetime.now()
                    end = start + timedelta(days=7)
                
            task_data.append({
                "name": task.get("name", "Task"),
                "start": start,
                "end": end,
                "color": task.get("color", style.node_color),
                "progress": task.get("progress", 0)  # 0-100%
            })
        
        # Sortiere nach Startdatum
        task_data.sort(key=lambda x: x["start"])
        
        # Berechne Datumsgrenzen
        min_date = min(t["start"] for t in task_data)
        max_date = max(t["end"] for t in task_data)
        total_days = (max_date - min_date).days or 1
        
        # Zeichne Tasks als horizontale Balken
        bar_height = 0.6
        y_positions = range(len(task_data))
        
        for i, task in enumerate(task_data):
            y = len(task_data) - 1 - i  # Von oben nach unten
            
            # Berechne Balkenposition
            start_pos = (task["start"] - min_date).days
            duration = (task["end"] - task["start"]).days or 1
            
            # Hintergrund-Balken (100%)
            ax.barh(y, duration, left=start_pos, height=bar_height,
                   color=task["color"], alpha=0.3, edgecolor='gray')
            
            # Fortschritt-Balken
            progress_width = duration * task["progress"] / 100
            ax.barh(y, progress_width, left=start_pos, height=bar_height,
                   color=task["color"], alpha=0.9, edgecolor='black', linewidth=0.5)
            
            # Task-Label
            ax.text(-total_days * 0.02, y, task["name"],
                   ha='right', va='center', fontsize=style.font_size,
                   fontweight='bold')
            
            # Progress-Label
            if task["progress"] > 0:
                ax.text(start_pos + duration / 2, y, f"{task['progress']}%",
                       ha='center', va='center', fontsize=style.font_size - 2,
                       fontweight='bold', color='white')
        
        # Zeichne Milestones als Rauten
        for milestone in milestones:
            try:
                m_date = datetime.strptime(milestone.get("date", "2024-01-15"), "%Y-%m-%d")
            except ValueError:
                continue
                
            x_pos = (m_date - min_date).days
            m_color = milestone.get("color", "#FFD93D")
            
            # Raute zeichnen
            ax.scatter(x_pos, -0.5, marker='D', s=200, color=m_color,
                      edgecolor='black', linewidth=1, zorder=10)
            ax.text(x_pos, -1.0, milestone.get("name", "Milestone"),
                   ha='center', va='top', fontsize=style.font_size - 1,
                   fontweight='bold', color=m_color)
        
        # Achsen konfigurieren
        ax.set_xlim(-total_days * 0.15, total_days * 1.05)
        ax.set_ylim(-1.5, len(task_data) - 0.5 + 0.5)
        
        # X-Achse: Datums-Labels
        n_ticks = min(6, total_days // 30 + 1)
        tick_positions = np.linspace(0, total_days, n_ticks)
        tick_labels = [(min_date + timedelta(days=int(d))).strftime('%Y-%m-%d') 
                       for d in tick_positions]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, rotation=45, ha='right', fontsize=style.font_size - 2)
        
        ax.set_yticks([])
        ax.spines['left'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        # Titel
        title = desc.get("title", "Gantt-Chart")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=20)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Gantt-Chart erstellt: {output_path}")
        return output_path
    
    def _create_comparison(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Vergleichs-Diagramm (Balken, Radar, oder Grouped Bars)
        
        Erwartete JSON-Struktur für Balkendiagramm:
        {
            "type": "comparison",
            "subtype": "bar",  # bar, radar, grouped
            "title": "Vergleich",
            "categories": ["Kategorie A", "Kategorie B", "Kategorie C"],
            "series": [
                {"name": "Serie 1", "values": [10, 20, 15], "color": "#FF6B6B"},
                {"name": "Serie 2", "values": [12, 18, 22], "color": "#4ECDC4"}
            ]
        }
        """
        subtype = desc.get("subtype", "bar").lower()
        
        if subtype == "bar":
            return self._create_bar_comparison(desc, style, output_path)
        elif subtype == "radar":
            return self._create_radar_comparison(desc, style, output_path)
        elif subtype == "grouped":
            return self._create_grouped_bar_comparison(desc, style, output_path)
        else:
            # Default: Bar-Diagramm
            return self._create_bar_comparison(desc, style, output_path)
    
    def _create_bar_comparison(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt einfaches Balkendiagramm"""
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        
        categories = desc.get("categories", [])
        series = desc.get("series", desc.get("datasets", []))
        
        if not series:
            raise ValueError("Comparison benötigt mindestens eine 'series' oder 'datasets'")
        
        if not categories:
            # Auto-generiere Kategorien
            categories = [f"Kat {i+1}" for i in range(len(series[0].get("values", [])))]
        
        x = np.arange(len(categories))
        width = 0.8 / len(series)
        
        for i, s in enumerate(series):
            values = s.get("values", [])
            color = s.get("color", style.node_color)
            name = s.get("name", s.get("label", f"Serie {i+1}"))
            
            offset = (i - len(series) / 2 + 0.5) * width
            bars = ax.bar(x + offset, values, width, label=name, color=color, alpha=0.85)
            
            # Wert-Labels über Balken
            for bar, val in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                       f'{val}', ha='center', va='bottom', fontsize=style.font_size - 2)
        
        ax.set_xticks(x)
        ax.set_xticklabels(categories, fontsize=style.font_size)
        ax.legend(loc='upper right', fontsize=style.font_size - 1)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        title = desc.get("title", "Vergleichs-Diagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=15)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Balken-Vergleich erstellt: {output_path}")
        return output_path
    
    def _create_radar_comparison(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Radar/Spider-Diagramm für Multi-Attribut-Vergleiche"""
        categories = desc.get("categories", [])
        series = desc.get("series", desc.get("datasets", []))
        
        if not series or not categories:
            raise ValueError("Radar-Chart benötigt 'categories' und 'series'")
        
        num_vars = len(categories)
        angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
        angles += angles[:1]  # Schließe den Kreis
        
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi, subplot_kw=dict(polar=True))
        fig.patch.set_facecolor(style.background_color)
        
        for s in series:
            values = s.get("values", [])
            if len(values) != num_vars:
                continue
            values += values[:1]  # Schließe den Kreis
            
            color = s.get("color", style.node_color)
            name = s.get("name", "Serie")
            
            ax.plot(angles, values, 'o-', linewidth=2, color=color, label=name)
            ax.fill(angles, values, alpha=0.25, color=color)
        
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, fontsize=style.font_size)
        ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.0), fontsize=style.font_size - 1)
        
        title = desc.get("title", "Radar-Vergleich")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=20)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Radar-Chart erstellt: {output_path}")
        return output_path
    
    def _create_grouped_bar_comparison(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein gruppiertes Balkendiagramm (horizontal)"""
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        
        categories = desc.get("categories", [])
        series = desc.get("series", desc.get("datasets", []))
        
        if not series:
            raise ValueError("Grouped-Bar benötigt mindestens eine 'series' oder 'datasets'")
        
        y = np.arange(len(categories))
        height = 0.8 / len(series)
        
        for i, s in enumerate(series):
            values = s.get("values", [])
            color = s.get("color", style.node_color)
            name = s.get("name", f"Serie {i+1}")
            
            offset = (i - len(series) / 2 + 0.5) * height
            ax.barh(y + offset, values, height, label=name, color=color, alpha=0.85)
        
        ax.set_yticks(y)
        ax.set_yticklabels(categories, fontsize=style.font_size)
        ax.legend(loc='lower right', fontsize=style.font_size - 1)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        title = desc.get("title", "Gruppierter Vergleich")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=15)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Gruppiertes Balkendiagramm erstellt: {output_path}")
        return output_path
    
    # ═══════════════════════════════════════════════════════════════════
    # NEUE DIAGRAMM-TYPEN (SOTA)
    # ═══════════════════════════════════════════════════════════════════
    
    def _create_scatter(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Scatter-Plot (Streudiagramm).
        
        JSON: {"type":"scatter", "title":"...", "xlabel":"X", "ylabel":"Y",
               "series":[{"name":"S1", "x":[1,2,3], "y":[4,5,6], "color":"#FF6B6B", "size":50}]}
        """
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        series = desc.get("series", [])
        if not series:
            raise ValueError("Scatter benötigt 'series' mit x/y Arrays")
        
        for s in series:
            x = s.get("x", [])
            y = s.get("y", [])
            color = s.get("color", style.node_color)
            name = s.get("name", "Serie")
            size = s.get("size", 60)
            marker = s.get("marker", "o")
            
            ax.scatter(x, y, c=color, s=size, label=name, marker=marker,
                      alpha=0.8, edgecolors='white', linewidth=0.5, zorder=5)
            
            # Trendlinie (optional)
            if s.get("trendline") and len(x) > 1:
                z = np.polyfit(x, y, 1)
                p = np.poly1d(z)
                x_line = np.linspace(min(x), max(x), 100)
                ax.plot(x_line, p(x_line), '--', color=color, alpha=0.5, lw=1.5)
        
        ax.set_xlabel(desc.get("xlabel", "X"), fontsize=style.font_size)
        ax.set_ylabel(desc.get("ylabel", "Y"), fontsize=style.font_size)
        ax.legend(loc='best', fontsize=style.font_size - 1)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(True, alpha=0.3)
        
        title = desc.get("title", "Scatter-Plot")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, 
                     pad=15, fontweight='bold')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
        plt.close()
        
        logger.info(f"✅ Scatter-Plot erstellt: {output_path}")
        return output_path
    
    def _create_heatmap(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt eine Heatmap.
        
        JSON: {"type":"heatmap", "title":"...", "x_labels":["A","B"],
               "y_labels":["X","Y"], "values":[[1,2],[3,4]], "colormap":"YlOrRd",
               "annotate": true}
        """
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        values = np.array(desc.get("values", [[1, 2], [3, 4]]))
        x_labels = desc.get("x_labels", [f"Col {i+1}" for i in range(values.shape[1])])
        y_labels = desc.get("y_labels", [f"Row {i+1}" for i in range(values.shape[0])])
        colormap = desc.get("colormap", "YlOrRd")
        annotate = desc.get("annotate", True)
        
        im = ax.imshow(values, cmap=colormap, aspect='auto', interpolation='nearest')
        
        # Achsen-Labels
        ax.set_xticks(np.arange(len(x_labels)))
        ax.set_yticks(np.arange(len(y_labels)))
        ax.set_xticklabels(x_labels, fontsize=style.font_size, rotation=45, ha='right')
        ax.set_yticklabels(y_labels, fontsize=style.font_size)
        
        # Werte in Zellen schreiben
        if annotate:
            for i in range(len(y_labels)):
                for j in range(len(x_labels)):
                    val = values[i, j]
                    # Kontrastfarbe: weiß auf dunkel, schwarz auf hell
                    text_color = "white" if val > (values.max() + values.min()) / 2 else "black"
                    ax.text(j, i, f"{val:.1f}" if isinstance(val, float) else str(val),
                           ha='center', va='center', color=text_color,
                           fontsize=_adaptive_font_size(style.font_size, len(x_labels) * len(y_labels)))
        
        # Colorbar
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.tick_params(labelsize=style.font_size - 1)
        
        title = desc.get("title", "Heatmap")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, 
                     pad=15, fontweight='bold')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
        plt.close()
        
        logger.info(f"✅ Heatmap erstellt: {output_path}")
        return output_path
    
    def _create_pie(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Kreisdiagramm.
        
        JSON: {"type":"pie", "title":"...", 
               "slices":[{"label":"A", "value":30, "color":"#FF6B6B"},
                         {"label":"B", "value":70, "color":"#4ECDC4"}],
               "donut": false, "explode_index": 0}
        """
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        slices = desc.get("slices", [])
        if not slices:
            raise ValueError("Pie-Chart benötigt 'slices'-Liste")
        
        default_colors = [
            "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
            "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
        ]
        
        labels = [s.get("label", f"Slice {i+1}") for i, s in enumerate(slices)]
        values = [s.get("value", 1) for s in slices]
        colors = [s.get("color", default_colors[i % len(default_colors)]) for i, s in enumerate(slices)]
        
        # Explode (optional)
        explode_idx = desc.get("explode_index", None)
        explode = [0.05 if i == explode_idx else 0 for i in range(len(slices))] if explode_idx is not None else None
        
        # Donut-Variante
        is_donut = desc.get("donut", False)
        
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, colors=colors, explode=explode,
            autopct='%1.1f%%', pctdistance=0.75 if is_donut else 0.6,
            startangle=90, textprops={'fontsize': style.font_size},
            wedgeprops=dict(width=0.5 if is_donut else 1.0, edgecolor='white', linewidth=2)
        )
        
        for autotext in autotexts:
            autotext.set_fontsize(style.font_size - 1)
            autotext.set_fontweight('bold')
        
        if is_donut:
            centre_circle = plt.Circle((0, 0), 0.50, fc=style.background_color)
            ax.add_artist(centre_circle)
        
        ax.set_aspect('equal')
        
        title = desc.get("title", "Kreisdiagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color,
                     pad=20, fontweight='bold')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
        plt.close()
        
        logger.info(f"✅ Pie-Chart erstellt: {output_path}")
        return output_path
    
    def _create_sankey(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Sankey-Diagramm (Fluss-Visualisierung).
        
        JSON: {"type":"sankey", "title":"...",
               "flows":[{"source":"A", "target":"B", "value":10, "color":"#FF6B6B"},
                        {"source":"A", "target":"C", "value":5}],
               "nodes":[{"id":"A", "label":"Start"}, {"id":"B", "label":"Ziel 1"}]}
        """
        from matplotlib.sankey import Sankey
        
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        fig.patch.set_facecolor(style.background_color)
        
        flows_raw = desc.get("flows", [])
        nodes_raw = desc.get("nodes", [])
        
        if not flows_raw:
            raise ValueError("Sankey benötigt 'flows'-Liste")
        
        # Baue ein vereinfachtes Sankey (matplotlib.sankey ist limitiert)
        # Stattdessen: Alluvial-artiges Diagramm mit Bezier-Kurven
        
        # Sammle alle einzigartigen Nodes (links und rechts)
        node_labels = {}
        for n in nodes_raw:
            node_labels[n["id"]] = n.get("label", n["id"])
        
        sources = list(dict.fromkeys(f["source"] for f in flows_raw))
        targets = list(dict.fromkeys(f["target"] for f in flows_raw if f["target"] not in sources))
        
        for s in sources:
            if s not in node_labels:
                node_labels[s] = s
        for t in targets:
            if t not in node_labels:
                node_labels[t] = t
        
        default_colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD"]
        
        # Positioniere Quellen links, Ziele rechts
        n_sources = len(sources)
        n_targets = len(targets)
        
        source_y = {s: (i + 0.5) / n_sources for i, s in enumerate(sources)}
        target_y = {t: (i + 0.5) / n_targets for i, t in enumerate(targets)}
        
        x_source = 0.15
        x_target = 0.85
        
        # Zeichne Flows als Bezier-Kurven
        max_val = max(f.get("value", 1) for f in flows_raw) or 1
        
        for i, flow in enumerate(flows_raw):
            src = flow["source"]
            tgt = flow["target"]
            val = flow.get("value", 1)
            color = flow.get("color", default_colors[i % len(default_colors)])
            
            sy = source_y.get(src, 0.5)
            ty = target_y.get(tgt, 0.5)
            line_width = max(2, val / max_val * 30)
            
            # Bezier-Kurve
            from matplotlib.patches import FancyArrowPatch
            from matplotlib.path import Path
            import matplotlib.patches as mpatches
            
            verts = [(x_source + 0.05, sy), (0.4, sy), (0.6, ty), (x_target - 0.05, ty)]
            codes = [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4]
            path = Path(verts, codes)
            patch = mpatches.PathPatch(path, facecolor='none', edgecolor=color,
                                       lw=line_width, alpha=0.5, capstyle='round')
            ax.add_patch(patch)
        
        # Zeichne Source-Nodes (links)
        for src, y in source_y.items():
            ax.text(x_source, y, node_labels.get(src, src), ha='center', va='center',
                   fontsize=style.font_size + 1, fontweight='bold',
                   bbox=dict(boxstyle='round,pad=0.5', facecolor='#2C3E50', 
                            edgecolor='white', lw=2, alpha=0.9),
                   color='white', zorder=10)
        
        # Zeichne Target-Nodes (rechts)
        for tgt, y in target_y.items():
            ax.text(x_target, y, node_labels.get(tgt, tgt), ha='center', va='center',
                   fontsize=style.font_size + 1, fontweight='bold',
                   bbox=dict(boxstyle='round,pad=0.5', facecolor='#34495E',
                            edgecolor='white', lw=2, alpha=0.9),
                   color='white', zorder=10)
        
        title = desc.get("title", "Sankey-Diagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color,
                     pad=20, fontweight='bold')
        
        ax.set_xlim(0, 1)
        ax.set_ylim(-0.1, 1.1)
        ax.axis('off')
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight',
                    facecolor=fig.get_facecolor())
        plt.close()
        
        logger.info(f"✅ Sankey-Diagramm erstellt: {output_path}")
        return output_path
    
    def _create_graphviz(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Graphviz-Diagramm (Dependency Graphs, State Machines, etc.)
        
        ⚠️ DATENSCHUTZ: 100% lokal, keine Datenübertragung an Dritte (DSGVO-konform)!
        Graphviz rendert lokal mit dem 'dot'-Befehl.
        
        Erwartete JSON-Struktur:
        {
            "type": "graphviz",
            "title": "Dependency Graph",
            "graph_type": "digraph"  # oder "graph" für ungerichtet
            "format": "png",        # png, svg, pdf
            "dot_code": "digraph G { A -> B -> C; }"  # Optional: Direkter DOT-Code
            "nodes": [{"id": "A", "label": "Node A"}, {"id": "B", "label": "Node B"}],
            "edges": [{"source": "A", "target": "B", "label": "depends on"}]
        }
        
        Falls 'dot_code' vorhanden: Wird direkt verwendet (für Experten).
        Falls 'nodes' und 'edges' vorhanden: Wird in DOT-Code umgewandelt.
        """
        import subprocess
        import tempfile
        
        # ═══ DATENSCHUTZ: Prüfe ob Graphviz lokal verfügbar ist ═══
        try:
            # Teste ob 'dot' (Graphviz) im PATH verfügbar ist
            subprocess.run(['dot', '-V'], capture_output=True, check=True, timeout=5)
            graphviz_available = True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            graphviz_available = False
            logger.warning("⚠️ Graphviz (dot) nicht im Systempfad gefunden. Versuche über Python-Paket...")
            try:
                import graphviz as gv
                # Teste mit Python-Paket
                gv.Digraph().render(filename='test', format='png', cleanup=True)
                graphviz_available = True
                logger.info("✅ Graphviz über Python-Paket verfügbar")
            except (ImportError, Exception):
                graphviz_available = False
                logger.error("❌ Graphviz weder als System-Tool noch als Python-Paket verfügbar")
        
        if not graphviz_available:
            raise RuntimeError(
                "Graphviz ist nicht installiert. Bitte installiere es mit: "
                "'pip install graphviz' (inkl. System-Binaries) oder "
                "'conda install graphviz' für eine vollständige Installation. "
                "Graphviz läuft 100% lokal – keine Datenübertragung!"
            )
        
        # ═══ DOT-Code generieren oder verwenden ═══
        dot_code = desc.get("dot_code")
        
        if not dot_code:
            # Baue DOT-Code aus nodes/edges
            graph_type = desc.get("graph_type", "digraph")
            nodes = desc.get("nodes", [])
            edges = desc.get("edges", [])
            
            lines = [f"{graph_type} G {{"]
            
            # Graph-Attribute
            graph_attrs = desc.get("graph_attrs", {})
            if graph_attrs:
                attrs = ", ".join(f'{k}="{v}"' for k, v in graph_attrs.items())
                lines.append(f"  graph [{attrs}];")
            
            # Node Defaults
            node_attrs = desc.get("node_attrs", {})
            if node_attrs:
                attrs = ", ".join(f'{k}="{v}"' for k, v in node_attrs.items())
                lines.append(f"  node [{attrs}];")
            
            # Edge Defaults
            edge_attrs = desc.get("edge_attrs", {})
            if edge_attrs:
                attrs = ", ".join(f'{k}="{v}"' for k, v in edge_attrs.items())
                lines.append(f"  edge [{attrs}];")
            
            # Nodes
            for node in nodes:
                node_id = node.get("id")
                label = node.get("label", node_id)
                attrs = node.get("attrs", {})
                attr_str = ", ".join(f'{k}="{v}"' for k, v in attrs.items())
                if attr_str:
                    lines.append(f'  "{node_id}" [{attr_str}, label="{label}"];')
                else:
                    lines.append(f'  "{node_id}" [label="{label}"];')
            
            # Edges
            for edge in edges:
                source = edge.get("source")
                target = edge.get("target")
                label = edge.get("label", "")
                attrs = edge.get("attrs", {})
                attr_str = ", ".join(f'{k}="{v}"' for k, v in attrs.items())
                if label:
                    attr_str = f'label="{label}", {attr_str}' if attr_str else f'label="{label}"'
                if attr_str:
                    lines.append(f'  "{source}" -> "{target}" [{attr_str}];')
                else:
                    lines.append(f'  "{source}" -> "{target}";')
            
            lines.append("}")
            dot_code = "\n".join(lines)
        
        # ═══ Output-Format ═══
        output_format = desc.get("format", "png").lower()
        if output_format not in ["png", "svg", "pdf", "jpg"]:
            output_format = "png"
        
        # ═══ Graphviz ausführen (100% lokal!) ═══
        try:
            # Schreibe DOT-Code in temporäre Datei
            with tempfile.NamedTemporaryFile(mode='w', suffix='.dot', delete=False, encoding='utf-8') as f:
                f.write(dot_code)
                dot_file = f.name
            
            # Führe Graphviz aus
            try:
                subprocess.run(
                    ['dot', '-T', output_format, dot_file, '-o', output_path],
                    capture_output=True,
                    check=True,
                    timeout=30
                )
                logger.info(f"✅ Graphviz-Diagramm erstellt: {output_path}")
            finally:
                # Temp-Datei bereinigen
                try:
                    os.unlink(dot_file)
                except Exception:
                    pass
            
            return output_path
            
        except subprocess.TimeoutExpired:
            logger.error("❌ Graphviz-Timeout: Rendering dauert zu lange")
            raise RuntimeError("Graphviz-Rendering überschritt Timeout (30s)")
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Graphviz-Fehler: {e.stderr.decode('utf-8', errors='replace')}")
            # Versuche mit Python-Paket
            try:
                import graphviz as gv
                g = gv.Source(dot_code)
                g.format = output_format
                g.render(filename=output_path, cleanup=True)
                logger.info(f"✅ Graphviz-Diagramm (Python-Paket) erstellt: {output_path}")
                return output_path
            except Exception as e2:
                logger.error(f"❌ Graphviz-Python-Paket Fehler: {e2}")
                raise RuntimeError(f"Graphviz konnte das Diagramm nicht rendern: {e.stderr.decode('utf-8', errors='replace')}")
        except Exception as e:
            logger.error(f"❌ Graphviz-Fehler: {e}")
            raise RuntimeError(f"Graphviz-Fehler: {e}")
    
    def _create_custom(self, desc: Dict, style: VisualStyle, output_path: str) -> str:
        """Erstellt ein Custom-Diagramm basierend auf freien Shapes
        
        Erwartete JSON-Struktur:
        {
            "type": "custom",
            "title": "Custom Diagramm",
            "elements": [
                {"shape": "rect", "x": 0.2, "y": 0.5, "width": 0.2, "height": 0.1, "color": "#FF6B6B", "label": "Box 1"},
                {"shape": "circle", "x": 0.5, "y": 0.5, "radius": 0.08, "color": "#4ECDC4", "label": "Kreis"},
                {"shape": "arrow", "start": [0.4, 0.5], "end": [0.42, 0.5], "color": "#333"},
                {"shape": "text", "x": 0.5, "y": 0.9, "text": "Beschreibung", "fontsize": 14}
            ]
        }
        """
        fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
        ax.set_facecolor(style.background_color)
        
        elements = desc.get("elements", [])
        
        for elem in elements:
            shape = elem.get("shape", "rect").lower()
            color = elem.get("color", style.node_color)
            
            if shape == "rect":
                x = elem.get("x", 0.3)
                y = elem.get("y", 0.3)
                w = elem.get("width", 0.2)
                h = elem.get("height", 0.1)
                rect = FancyBboxPatch(
                    (x - w/2, y - h/2), w, h,
                    boxstyle="round,pad=0.01",
                    facecolor=color, edgecolor='black', linewidth=1.5
                )
                ax.add_patch(rect)
                if "label" in elem:
                    ax.text(x, y, elem["label"], ha='center', va='center',
                           fontsize=style.font_size, fontweight='bold')
                           
            elif shape == "circle":
                x = elem.get("x", 0.5)
                y = elem.get("y", 0.5)
                r = elem.get("radius", 0.08)
                circle = Circle((x, y), r, facecolor=color, edgecolor='black', linewidth=1.5)
                ax.add_patch(circle)
                if "label" in elem:
                    ax.text(x, y, elem["label"], ha='center', va='center',
                           fontsize=style.font_size, fontweight='bold')
                           
            elif shape == "arrow":
                start = elem.get("start", [0.3, 0.5])
                end = elem.get("end", [0.7, 0.5])
                ax.annotate('', xy=end, xytext=start,
                           arrowprops=dict(arrowstyle='->', color=color, lw=2))
                           
            elif shape == "text":
                x = elem.get("x", 0.5)
                y = elem.get("y", 0.5)
                text = elem.get("text", "")
                fontsize = elem.get("fontsize", style.font_size)
                ax.text(x, y, text, ha='center', va='center', fontsize=fontsize, color=color)
                
            elif shape == "line":
                start = elem.get("start", [0.2, 0.5])
                end = elem.get("end", [0.8, 0.5])
                ax.plot([start[0], end[0]], [start[1], end[1]], 
                       color=color, linewidth=elem.get("linewidth", 2))
        
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        
        title = desc.get("title", "Custom-Diagramm")
        ax.set_title(title, fontsize=style.title_size, color=style.title_color, pad=20)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=style.dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"✅ Custom-Diagramm erstellt: {output_path}")
        return output_path


# ====================
# HELPER FUNCTIONS
# ====================

def create_visualization_from_text(text_description: str, 
                                   model_loader=None,
                                   output_path: str = "visualization.png") -> Optional[str]:
    """
    🤖 LLM-gesteuertes Interface
    
    Nimmt eine natürlichsprachige Beschreibung und lässt das LLM
    eine JSON-Struktur generieren, die dann visualisiert wird.
    
    Args:
        text_description: "Erstelle ein Netzwerk von Künstlern..."
        model_loader: LLM für JSON-Generierung
        output_path: Ausgabepfad
        
    Returns:
        Pfad zum erstellten Bild oder None bei Fehler
    """
    
    if model_loader is None:
        logger.warning("Kein model_loader verfügbar - kann keine LLM-gesteuerte Visualisierung erstellen")
        return None
    
    # Prompt für LLM
    system_prompt = """Du bist ein Visualisierungs-Assistent. Konvertiere natürlichsprachige Beschreibungen in JSON-Strukturen für Diagramme.

Unterstützte Typen:
- network: Netzwerk-Diagramme (Knoten + Kanten)
- timeline: Zeitachsen (Events mit Jahren)
- hierarchy: Hierarchien (Parent-Child-Beziehungen)

Antwort NUR mit gültigem JSON, kein zusätzlicher Text!

Beispiel für Network:
{
  "type": "network",
  "title": "Beispiel-Netzwerk",
  "nodes": [
    {"id": "a", "label": "Node A", "color": "#FF6B6B"},
    {"id": "b", "label": "Node B", "color": "#4ECDC4"}
  ],
  "edges": [
    {"source": "a", "target": "b", "label": "Verbindung"}
  ]
}"""
    
    user_prompt = f"Erstelle ein Diagramm: {text_description}"
    
    try:
        # LLM-Call
        response = model_loader.generate_response(
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=2048,
            temperature=0.3
        )
        
        # Parse JSON
        json_desc = json.loads(response)
        
        # Visualisiere
        tool = GenericVisualizationTool()
        return tool.visualize(json_desc, output_path)
        
    except Exception as e:
        logger.error(f"Fehler bei LLM-gesteuerter Visualisierung: {e}")
        return None


# ====================
# DEMO
# ====================

if __name__ == "__main__":
    print("🎨 GENERISCHES VISUALISIERUNGS-TOOL - DEMO")
    print("=" * 60)
    
    tool = GenericVisualizationTool()
    
    # Demo 1: Künstler-Netzwerk
    print("\n1️⃣ Künstler-Netzwerk des 20. Jahrhunderts")
    artist_network = {
        "type": "network",
        "title": "Künstler-Netzwerk: Kubismus & Surrealismus",
        "layout": "spring",
        "nodes": [
            {"id": "picasso", "label": "Pablo Picasso", "color": "#FF6B6B", "size": 800},
            {"id": "braque", "label": "Georges Braque", "color": "#FF6B6B", "size": 600},
            {"id": "gris", "label": "Juan Gris", "color": "#FF6B6B", "size": 400},
            {"id": "dali", "label": "Salvador Dalí", "color": "#4ECDC4", "size": 700},
            {"id": "ernst", "label": "Max Ernst", "color": "#4ECDC4", "size": 500},
            {"id": "miro", "label": "Joan Miró", "color": "#4ECDC4", "size": 600},
            {"id": "klee", "label": "Paul Klee", "color": "#95E1D3", "size": 500},
        ],
        "edges": [
            {"source": "picasso", "target": "braque", "label": "Kubismus (1907)"},
            {"source": "picasso", "target": "gris", "label": "Paris"},
            {"source": "braque", "target": "gris", "label": "Kubismus"},
            {"source": "dali", "target": "ernst", "label": "Surrealismus"},
            {"source": "dali", "target": "miro", "label": "Barcelona"},
            {"source": "ernst", "target": "miro", "label": "Paris (1920er)"},
            {"source": "ernst", "target": "klee", "label": "indirekt"},
        ],
        "style": {
            "figsize": [28, 20],
            "background_color": "#F8F9FA",
            "dpi": 150
        }
    }
    
    try:
        output1 = tool.visualize(artist_network, "artist_network.png")
        print(f"   ✅ Erstellt: {output1}")
    except Exception as e:
        print(f"   ❌ Fehler: {e}")
    
    # Demo 2: Zeitachse
    print("\n2️⃣ Kunstgeschichte-Zeitachse")
    timeline = {
        "type": "timeline",
        "title": "Wichtige Kunstbewegungen des 20. Jahrhunderts",
        "events": [
            {"year": 1907, "label": "Kubismus", "color": "#FF6B6B"},
            {"year": 1916, "label": "Dadaismus", "color": "#4ECDC4"},
            {"year": 1924, "label": "Surrealismus", "color": "#95E1D3"},
            {"year": 1940, "label": "Abstrakter Expressionismus", "color": "#F38181"},
            {"year": 1960, "label": "Pop Art", "color": "#AA96DA"},
        ],
        "style": {
            "figsize": [16, 6],
            "dpi": 150
        }
    }
    
    try:
        output2 = tool.visualize(timeline, "art_timeline.png")
        print(f"   ✅ Erstellt: {output2}")
    except Exception as e:
        print(f"   ❌ Fehler: {e}")
    
    # Demo 3: Hierarchie
    print("\n3️⃣ Bauhaus-Hierarchie")
    hierarchy = {
        "type": "hierarchy",
        "title": "Bauhaus: Organisationsstruktur",
        "nodes": [
            {"id": "gropius", "label": "Walter Gropius\n(Gründer)"},
            {"id": "klee", "label": "Paul Klee\n(Formmeister)", "parent": "gropius"},
            {"id": "kandinsky", "label": "Wassily Kandinsky\n(Formmeister)", "parent": "gropius"},
            {"id": "moholy", "label": "László Moholy-Nagy\n(Werkmeister)", "parent": "gropius"},
            {"id": "albers", "label": "Josef Albers\n(Student → Meister)", "parent": "klee"},
        ],
        "style": {
            "figsize": [12, 8],
            "node_color": "#FFB6B9",
            "dpi": 150
        }
    }
    
    try:
        output3 = tool.visualize(hierarchy, "bauhaus_hierarchy.png")
        print(f"   ✅ Erstellt: {output3}")
    except Exception as e:
        print(f"   ❌ Fehler: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Demo abgeschlossen! Prüfe die erstellten Bilder:")
    print("   - artist_network.png")
    print("   - art_timeline.png")
    print("   - bauhaus_hierarchy.png")
