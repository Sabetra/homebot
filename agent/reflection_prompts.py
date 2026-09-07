"""
Reflection Prompts für Adaptive Planning
=========================================

Prompts für LLM-basierte Reflection in verschiedenen Phasen.

Author: Implementation 2025-10-10
"""

# ==================== REFLECTION 1: DATA QUALITY ====================

REFLECTION_1_SYSTEM = """Du bist ein intelligenter Reflection-Agent für Daten-Qualitätsbewertung.

Deine Aufgabe:
1. Bewerte die Qualität der bisher gesammelten Daten
2. Entscheide ob mehr Daten-Tools nötig sind (web_search, rag_search)
3. Gib JSON-Output zurück

Wichtig: Fokussiere nur auf DATEN-QUALITÄT, nicht auf Verarbeitung (calculator, diagram)!"""

REFLECTION_1_USER_TEMPLATE = """BENUTZER-QUERY:
"{query}"

BISHERIGE TOOL-ERGEBNISSE (Iteration {iteration}):
{results_summary}

AUFGABE - DATEN-QUALITÄT BEWERTEN:

1. VOLLSTÄNDIGKEIT (0.0-1.0):
   - < 3 Ergebnisse = unvollständig (0.3)
   - 3-5 Ergebnisse = ausreichend (0.6)
   - 5+ Ergebnisse = gut (0.9)

2. FEHLERRATE:
   - Keine Fehler = perfekt (1.0)
   - 1 Fehler = akzeptabel (0.7)
   - 2+ Fehler = problematisch (0.3)

3. RELEVANZ:
   - Sind die Ergebnisse relevant für die Query?

4. ENTSCHEIDUNG:
   a) confidence_done: Wie sicher bin ich, dass DATEN-SAMMLUNG abgeschlossen ist? (0.0-1.0)
      - >0.85: Sehr sicher, genug Daten
      - 0.5-0.85: Unsicher
      - <0.5: Mehr Daten nötig
   
   b) confidence_more_tools: Würden weitere DATEN-TOOLS helfen? (0.0-1.0)
      - >0.7: Ja, definitiv!
      - 0.4-0.7: Vielleicht
      - <0.4: Nein, Daten reichen

5. TOOL-VORSCHLÄGE (falls confidence_more_tools > 0.4):
   - web_search: Andere Keywords? Spezifischere Suche?
   - rag_search: Lokale Daten relevant?

ANTWORT-FORMAT (NUR JSON, kein zusätzlicher Text!):
{{
    "confidence_done": 0.7,
    "confidence_more_tools": 0.6,
    "reasoning": "Kurze Begründung hier...",
    "data_quality": {{
        "completeness": 0.8,
        "error_rate": 0.0,
        "relevance": 0.9
    }},
    "suggested_tools": [
        {{
            "tool": "web_search",
            "reason": "Spezifischere Keywords für bessere Ergebnisse",
            "params": {{"query": "alternative search term", "num_results": 5}}
        }}
    ]
}}

Antworte NUR mit JSON:"""


# ==================== REFLECTION 2: TOOL COMPLETENESS ====================

REFLECTION_2_SYSTEM = """Du bist ein intelligenter Reflection-Agent für Tool-Vervollständigung.

Deine Aufgabe:
1. Analysiere was mit vorhandenen DATEN gemacht werden kann
2. Erkenne ob ergänzende Tools nötig sind (calculator, canvas/create_diagram, file_writer)
3. Gib JSON-Output zurück

Wichtig: KEINE Daten-Tools mehr (web_search, rag_search)! Fokus auf VERARBEITUNG!"""

REFLECTION_2_USER_TEMPLATE = """BENUTZER-QUERY:
"{query}"

DATEN-INSIGHTS (nach Daten-Sammlung):
{data_insights}

TOOLS BEREITS VERWENDET:
{tools_used}

AUFGABE - TOOL-VERVOLLSTÄNDIGUNG:

1. ANALYSIERE was ich MIT den Daten machen kann:
   - Berechnungen nötig? → calculator
    - Visualisierung hilfreich? → canvas (oder create_diagram)
   - Dateien schreiben? → file_writer

2. MATCHING-REGELN:
   - Zahlen + Vergleichs-Query → calculator
    - Zeitdaten → canvas (Timeline)
    - Vergleich + Zahlen → canvas (Comparison Chart)
    - Viele Entitäten/Beziehungen → canvas (Network)
   - "berechne", "rechne" in Query → calculator
    - "visualisiere", "zeige als" in Query → canvas

3. ENTSCHEIDUNG:
   a) confidence_done: Wie sicher bin ich, dass TOOL-SET komplett ist? (0.0-1.0)
      - >0.85: Sehr sicher, komplett
      - 0.5-0.85: Unsicher
      - <0.5: Mehr Tools nötig
   
   b) confidence_more_tools: Würden ERGÄNZENDE TOOLS helfen? (0.0-1.0)
      - >0.7: Ja, definitiv!
      - 0.4-0.7: Vielleicht
      - <0.4: Nein, Tools ausreichend

4. WICHTIG:
   - Wenn Tool bereits verwendet → NICHT nochmal vorschlagen!
   - KEINE Daten-Tools (web_search, rag_search)!

ANTWORT-FORMAT (NUR JSON, kein zusätzlicher Text!):
{{
    "confidence_done": 0.7,
    "confidence_more_tools": 0.6,
    "reasoning": "Kurze Begründung hier...",
    "tool_analysis": {{
        "needs_calculation": true,
        "needs_visualization": true,
        "needs_file_ops": false
    }},
    "suggested_tools": [
        {{
            "tool": "calculator",
            "reason": "Verhältnis berechnen für Vergleich",
            "params": {{"expression": "20000000000 / 8000000000"}}
        }},
        {{
            "tool": "canvas",
            "reason": "Vergleichs-Chart für bessere Übersicht",
            "params": {{"description": {{"type": "comparison", "title": "Stryker vs SBB", "categories": ["Stryker", "SBB"], "series": [{{"name": "Wert", "values": [20, 8]}}]}}}}
        }}
    ]
}}

Antworte NUR mit JSON:"""


# ==================== HELPER: Build Results Summary ====================

def build_results_summary(current_results) -> str:
    """
    Erstellt eine Zusammenfassung der Tool-Results für Reflection-Prompt.
    
    Args:
        current_results: Liste von ToolResult-Objekten
        
    Returns:
        Formatierter String für Prompt
    """
    if not current_results:
        return "Keine Ergebnisse bisher"
    
    summary_lines = []
    for r in current_results:
        tool_name = r.tool
        success = r.success
        result_count = len(r.results or [])
        error = r.error or "None"
        
        status = "✓" if success else "✗"
        summary_lines.append(
            f"- {tool_name}: {status} ({result_count} results, error: {error})"
        )
    
    return "\n".join(summary_lines)


def build_data_insights(current_results) -> str:
    """
    Erstellt Daten-Insights für Reflection 2.
    
    Args:
        current_results: Liste von ToolResult-Objekten
        
    Returns:
        Formatierter String mit Insights
    """
    import re
    
    has_numbers = False
    has_temporal = False
    has_entities = False
    result_count = 0
    
    for r in current_results:
        if r.results:
            result_count += len(r.results)
            results_str = str(r.results)
            
            # Prüfe auf Zahlen
            if re.search(r'\d+', results_str):
                has_numbers = True
            
            # Prüfe auf zeitliche Daten
            if re.search(r'(20\d{2}|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|Q[1-4])', results_str):
                has_temporal = True
            
            # Prüfe auf viele Entitäten (grobe Heuristik)
            if len(results_str.split()) > 100:
                has_entities = True
    
    insights = f"""- Ergebnis-Count: {result_count}
- Zahlen vorhanden: {'Ja' if has_numbers else 'Nein'}
- Zeitdaten vorhanden: {'Ja' if has_temporal else 'Nein'}
- Viele Entitäten: {'Ja' if has_entities else 'Nein'}"""
    
    return insights
