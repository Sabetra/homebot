"""
OpenAI-Compatible Tool Schemas for Native Function Calling
===========================================================

Definiert alle Agent-Tools im OpenAI Function Calling Format, damit
Magistral Small's eingebettetes [AVAILABLE_TOOLS]/[TOOL_CALLS] Template
sie automatisch korrekt formatiert.

Diese Schemas werden an create_chat_completion(tools=...) übergeben.

SOTA Pattern: Native Function Calling statt Regex-basiertes [TOOL:...] Parsing.
"""

from __future__ import annotations
from typing import Any, Dict, List


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Gibt alle Tool-Definitionen im OpenAI Function Calling Format zurück.
    
    Format pro Tool:
    {
        "type": "function",
        "function": {
            "name": "tool_name",
            "description": "...",
            "parameters": { JSON Schema }
        }
    }
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Sucht aktuelle Informationen im Internet via DuckDuckGo. "
                    "WANN VERWENDEN: Aktuelle Nachrichten, Fakten die sich ändern "
                    "können (Preise, Termine, Statistiken), Ereignisse, oder wenn "
                    "lokale RAG-Daten nicht ausreichen. "
                    "QUERY-TIPPS: Verwende präzise Schlüsselwörter statt natürlicher "
                    "Fragen. RICHTIG: 'Tesla Model 3 Preis 2026'. "
                    "FALSCH: 'Was kostet ein Tesla Model 3?'. "
                    "Nutze Englisch für internationale/technische Themen. "
                    "Bei komplexen Recherchen: Mehrere Aufrufe mit verschiedenen "
                    "Queries für umfassendere Ergebnisse."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Suchbegriff -- präzise Schlüsselwörter verwenden, "
                                "NICHT natürliche Fragen. "
                                "Beispiel: 'Python asyncio tutorial 2025' statt "
                                "'Wie funktioniert asyncio in Python?'"
                            )
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "Anzahl gewünschter Ergebnisse (1-10)",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "rag_search",
                "description": (
                    "Durchsucht die lokale Wissensdatenbank (FAISS-Index + SQLite) "
                    "nach relevanten Dokumenten und gespeicherten Inhalten. "
                    "WANN VERWENDEN: ZUERST prüfen bei Fragen zu hochgeladenen "
                    "Dokumenten, PDFs, früheren Recherchen oder gespeichertem Wissen. "
                    "Auch für Kontext aus früheren Gesprächen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Semantische Suchanfrage -- natürliche Formulierungen "
                                "funktionieren am besten (Embedding-basiert, nicht "
                                "Keyword-basiert). Beispiel: 'Vorteile von erneuerbaren "
                                "Energien' statt nur 'erneuerbare Energien'"
                            )
                        },
                        "k": {
                            "type": "integer",
                            "description": "Anzahl der Top-Ergebnisse",
                            "default": 10
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": (
                    "Berechnet einfache mathematische Ausdrücke. Unterstützt: "
                    "Grundrechenarten, sin/cos/tan, sqrt, log, abs, pi, e, "
                    "Potenzen (**). NUR für direkt auswertbare Ausdrücke in "
                    "einer Zeile. Für mehrstufige Berechnungen, Datenanalysen, "
                    "Gleichungssysteme oder iterative Probleme → code_executor verwenden."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Mathematischer Ausdruck (z.B. 'sqrt(144) + 2**10')"
                        }
                    },
                    "required": ["expression"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "code_executor",
                "description": (
                    "Führt Python-Code in einer sicheren Sandbox aus. "
                    "WANN VERWENDEN: Berechnungen mit mehreren Schritten, "
                    "Datenanalysen, Datumsberechnungen, String-Verarbeitung/Regex, "
                    "Statistik, Simulationen, Visualisierungen, Datenkonvertierung, "
                    "oder wenn du ein Ergebnis mit Code VERIFIZIEREN kannst. "
                    "INTERAKTIVE PROGRAMME: Spiele (pygame/arcade), GUI-Apps "
                    "(tkinter/PyQt), Web-Dashboards (flask/gradio/dash) werden "
                    "automatisch als eigenständiger Prozess gestartet (detached mode). "
                    "Du kannst auch detached=true setzen, um ein Programm explizit "
                    "im Hintergrund zu starten. "
                    "Vorinstallierte Pakete: numpy, pandas, matplotlib, scipy, seaborn, "
                    "scikit-learn, plotly, sympy, math, statistics, datetime, json, re, csv, io. "
                    "Auto-installierbar: pygame, arcade, flask, gradio, dash, kivy u.v.m. "
                    "Features: Persistente Sessions (Variablen bleiben erhalten), "
                    "automatische Plot-Erfassung, Datei-Output, "
                    "automatische Fehlerkorrektur (bis 3 Versuche), "
                    "Detached-Modus für interaktive Programme. "
                    "KEIN Netzwerk-Zugriff (außer im Detached-Modus für lokale Server)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Python-Code zum Ausführen. Immer mit print() "
                                "das Ergebnis ausgeben. Plots: plt.show() oder "
                                "fig.show() am Ende. Für Spiele/GUIs: vollständigen "
                                "Code schreiben -- wird automatisch als eigener Prozess gestartet."
                            )
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in Sekunden (default: 30, max: 120). Wird im Detached-Modus ignoriert.",
                            "default": 30
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Optionale Session-ID für persistente Variablen über mehrere Aufrufe hinweg. Gleiche ID = gleicher Namespace."
                        },
                        "detached": {
                            "type": "boolean",
                            "description": (
                                "Wenn true, wird der Code als eigenständiger Hintergrund-Prozess "
                                "gestartet (eigenes Fenster, kein Timeout). Wird automatisch "
                                "aktiviert bei pygame, tkinter, flask etc. "
                                "Für interaktive Programme, Spiele und GUI-Apps."
                            ),
                            "default": False
                        },
                        "deliver_to_user": {
                            "type": "boolean",
                            "description": (
                                "Auf true setzen, wenn der Nutzer ein dauerhaft nutzbares "
                                "Python-Programm oder Skript angefordert hat. Der erfolgreich "
                                "getestete finale Code wird dann als Download-Artefakt gespeichert. "
                                "Für interne Berechnungs- und Verifikationsskripte false lassen."
                            ),
                            "default": False
                        },
                        "artifact_name": {
                            "type": "string",
                            "description": (
                                "Dateiname für das Nutzerprogramm, z.B. tetris.py. "
                                "Nur zusammen mit deliver_to_user=true verwenden."
                            )
                        }
                    },
                    "required": ["code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "file_reader",
                "description": (
                    "Liest den Inhalt einer Datei vom lokalen System. "
                    "Unterstützt Text-Dateien (txt, csv, json, py, md, etc.). "
                    "Liest standardmäßig die ersten 2.000 Zeilen. "
                    "Nutze offset/limit, um gezielt weiterzulesen: Das Ergebnis "
                    "enthält total_lines, start_line, end_line, has_more_lines und "
                    "next_offset — mit offset=next_offset geht es direkt weiter. "
                    "Sehr lange Zeilen werden zusätzlich auf 50.000 Zeichen "
                    "begrenzt (was_truncated=true + suggested_action)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absoluter oder relativer Pfad zur Datei"
                        },
                        "offset": {
                            "type": "integer",
                            "description": (
                                "Startzeile (1-basiert, Standard 1). "
                                "Zur Weiterschreibung: Wert aus next_offset des letzten Ergebnisses."
                            ),
                            "default": 1,
                            "minimum": 1
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Anzahl der Zeilen, die gelesen werden sollen "
                                "(Standard 2000, Minimum 1)."
                            ),
                            "default": 2000,
                            "minimum": 1
                        },
                        "encoding": {
                            "type": "string",
                            "description": "Zeichenkodierung",
                            "default": "utf-8"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "file_writer",
                "description": (
                    "Schreibt Inhalte in eine Datei auf dem lokalen System."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Pfad zur Zieldatei"
                        },
                        "content": {
                            "type": "string",
                            "description": "Zu schreibender Inhalt"
                        },
                        "encoding": {
                            "type": "string",
                            "description": "Zeichenkodierung",
                            "default": "utf-8"
                        }
                    },
                    "required": ["file_path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "create_diagram",
                "description": (
                    "Erstellt professionelle PNG-Diagramme. IMMER verwenden wenn der User "
                    "nach Visualisierung, Diagramm, Graph, Chart, Mind-Map oder grafischer "
                    "Darstellung fragt. Unterstützte Typen: network, timeline, hierarchy, "
                    "flowchart, mindmap, gantt, comparison, scatter, heatmap, sankey, pie, graphviz, custom."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "object",
                            "description": (
                                "JSON-Objekt mit Diagramm-Beschreibung. PFLICHTFELD: 'type' und 'title'. "
                                "TYPEN UND IHRE FELDER:\n"
                                "1) network: {\"type\":\"network\", \"title\":\"...\", \"directed\":false, "
                                "\"nodes\":[{\"id\":\"a\", \"label\":\"A\", \"color\":\"#FF6B6B\", \"size\":500}], "
                                "\"edges\":[{\"source\":\"a\", \"target\":\"b\", \"label\":\"Verbindung\"}], "
                                "\"layout\":\"spring|circular|kamada_kawai|shell\"}\n"
                                "2) timeline: {\"type\":\"timeline\", \"title\":\"...\", "
                                "\"events\":[{\"year\":2024, \"label\":\"Titel des Events\", "
                                "\"description\":\"Was passiert ist (kurz)\", \"phase\":\"Phasenname\", "
                                "\"color\":\"#4ECDC4\"}]} "
                                "WICHTIG bei timeline: Jedes Event MUSS 'description' haben! "
                                "'phase' gruppiert Events farblich. Mindestens 3-5 Events erstellen.\n"
                                "3) hierarchy: {\"type\":\"hierarchy\", \"title\":\"...\", "
                                "\"nodes\":[{\"id\":\"root\", \"label\":\"Root\"}, {\"id\":\"child\", \"label\":\"Child\", \"parent\":\"root\"}]}\n"
                                "4) flowchart: {\"type\":\"flowchart\", \"title\":\"...\", "
                                "\"steps\":[{\"type\":\"start\", \"label\":\"Start\"}, {\"type\":\"process\", \"label\":\"Step 1\"}, "
                                "{\"type\":\"decision\", \"label\":\"OK?\"}, {\"type\":\"end\", \"label\":\"Ende\"}]}\n"
                                "5) mindmap: {\"type\":\"mindmap\", \"title\":\"...\", \"central\":\"Hauptthema\", "
                                "\"branches\":[{\"label\":\"Zweig 1\", \"children\":[{\"label\":\"Unterpunkt\"}]}]}\n"
                                "6) gantt: {\"type\":\"gantt\", \"title\":\"...\", "
                                "\"tasks\":[{\"name\":\"Task\", \"start\":\"2024-01-01\", \"end\":\"2024-02-15\", \"progress\":80}]}\n"
                                "7) comparison: {\"type\":\"comparison\", \"subtype\":\"bar|radar|grouped\", \"title\":\"...\", "
                                "\"categories\":[\"A\",\"B\"], \"series\":[{\"name\":\"S1\", \"values\":[10,20], \"color\":\"#FF6B6B\"}]}\n"
                                "8) scatter: {\"type\":\"scatter\", \"title\":\"...\", \"xlabel\":\"X\", \"ylabel\":\"Y\", "
                                "\"series\":[{\"name\":\"S1\", \"x\":[1,2,3], \"y\":[4,5,6], \"color\":\"#FF6B6B\"}]}\n"
                                "9) heatmap: {\"type\":\"heatmap\", \"title\":\"...\", \"x_labels\":[\"A\",\"B\"], "
                                "\"y_labels\":[\"X\",\"Y\"], \"values\":[[1,2],[3,4]], \"colormap\":\"YlOrRd\"}\n"
                                "10) pie: {\"type\":\"pie\", \"title\":\"...\", "
                                "\"slices\":[{\"label\":\"A\", \"value\":30}, {\"label\":\"B\", \"value\":70}]}\n"
                                "11) graphviz: {\"type\":\"graphviz\", \"title\":\"...\", "
                                "\"graph_type\":\"digraph\", \"nodes\":[{\"id\":\"A\", \"label\":\"Modul A\"}], "
                                "\"edges\":[{\"source\":\"A\", \"target\":\"B\", \"label\":\"depends_on\"}], "
                                "\"format\":\"png\"} ODER direkt: {\"type\":\"graphviz\", \"dot_code\":\"digraph G { A -> B; }\"}\n"
                                "OPTIONAL: \"style\":{\"figsize\":[28,20], \"dpi\":150, \"background_color\":\"#F8F9FA\", \"font_size\":22}"
                            )
                        },
                        "output_filename": {
                            "type": "string",
                            "description": "Dateiname für PNG-Ausgabe",
                            "default": "diagram.png"
                        }
                    },
                    "required": ["description"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "canvas",
                "description": (
                    "Alias fuer create_diagram. Erzeugt konzeptuelle Visualisierungen "
                    "(inkl. Graphviz-Dependency/UML/State-Diagramme) als PNG-Datei. "
                    "Verwende dieselben Parameter wie create_diagram."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "object",
                            "description": (
                                "JSON-Objekt mit Diagramm-Beschreibung. PFLICHTFELD: 'type' und 'title'. "
                                "Unterstützte Typen: network, timeline, hierarchy, flowchart, mindmap, gantt, "
                                "comparison, scatter, heatmap, sankey, pie, graphviz, custom. "
                                "Graphviz-Beispiel: {\"type\":\"graphviz\", \"dot_code\":\"digraph G { A -> B; }\"}."
                            )
                        },
                        "output_filename": {
                            "type": "string",
                            "description": "Dateiname für PNG-Ausgabe",
                            "default": "diagram.png"
                        }
                    },
                    "required": ["description"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "pdf_extract",
                "description": (
                    "Extrahiert Text aus einer lokalen PDF-Datei (auch passwort-/"
                    "berechtigungs-geschützt, mit OCR-Fallback für gescannte PDFs). "
                    "WANN VERWENDEN: Wenn der User nach Inhalt einer konkreten PDF "
                    "fragt, die nicht im RAG-Index ist, oder ein PDF-Dokument "
                    "on-demand zusammengefasst werden soll. Für bereits indizierte "
                    "Dokumente → rag_search verwenden."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Pfad zur PDF-Datei (innerhalb des Workspace-Sandbox)."
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximale Anzahl extrahierter Zeichen (Truncation-Limit).",
                            "default": 50000
                        },
                        "use_ocr": {
                            "type": "boolean",
                            "description": "OCR-Fallback aktivieren für gescannte PDFs.",
                            "default": True
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "finance_list_accounts",
                "description": (
                    "Listet alle in der Finanz-DB hinterlegten Bankkonten samt "
                    "Bank-Name, IBAN, Inhaber und Whrung. WANN VERWENDEN: Wenn "
                    "der User wissen will, welche Konten/Banken bekannt sind, "
                    "oder vor einer gezielten Konto-Abfrage."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_get_schema_context",
                "description": (
                    "Liefert strukturierte Metadaten zur Finance-DB (Tabellen, Spalten, "
                    "Beziehungen, semantische Hinweise, schema_hash). WANN VERWENDEN: "
                    "Vor SQL-Abfragen oder komplexen Finance-Analysen, um die aktuelle "
                    "DB-Struktur deterministisch zu kennen statt sie zu raten."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_tables": {
                            "type": "integer",
                            "default": 30,
                            "description": "Maximale Anzahl Tabellen im Ergebnis.",
                        },
                        "include_relationships": {
                            "type": "boolean",
                            "default": True,
                            "description": "Foreign-Key-Beziehungen mit ausgeben.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_sql_query",
                "description": (
                    "Fuehrt eine read-only SQL-Abfrage direkt auf der Finanz-DB aus "
                    "(nur SELECT/CTE/PRAGMA table_info). WANN VERWENDEN: Fuer ad-hoc "
                    "Analysen, joins oder dedizierte Auswertungen, die mit den "
                    "vorgefertigten Finance-Tools nicht exakt abbildbar sind. "
                    "Verwende bevorzugt parameterisierte Abfragen ueber query_params."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "Read-only SQL (SELECT/WITH/PRAGMA table_info).",
                        },
                        "query_params": {
                            "type": "array",
                            "description": "Optionale Parameterliste fuer Platzhalter in der SQL-Abfrage.",
                            "items": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "integer"},
                                    {"type": "boolean"},
                                    {"type": "null"}
                                ]
                            },
                        },
                        "limit": {
                            "type": "integer",
                            "default": 100,
                            "description": "Maximale Zeilenzahl (hart gecappt).",
                        },
                    },
                    "required": ["sql"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_search_transactions",
                "description": (
                    "Generische Hybrid-Suche (lexikalisch + semantisch) ueber "
                    "Finanz-Buchungen. Nutzt Suchtext ueber Gegenseite, "
                    "Verwendungszweck, Kategorie und semantische Aehnlichkeit, "
                    "liefert Relevanz-Scores und robuste Treffer fuer freie "
                    "Cluster wie Reise/Event/Ort/Thema ('Parisreise', 'Hotel', "
                    "'Hochzeit'). WANN VERWENDEN: Wenn keine exakte Kategorie "
                    "oder Gegenseite vorgegeben ist und erst relevante Buchungen "
                    "identifiziert werden muessen. Fuer deterministische "
                    "Nachberechnung von Dedup/Summen ggf. code_executor verwenden."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_text": {
                            "type": "string",
                            "description": "Freier Suchtext (z.B. 'Parisreise Hotel')."
                        },
                        "iban": {"type": "string", "description": "IBAN des Kontos (optional)"},
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "limit": {"type": "integer", "default": 500},
                        "include_transfers": {
                            "type": "boolean",
                            "description": "Interne Transfers einbeziehen (default false)",
                        },
                    },
                    "required": ["query_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_query_transactions",
                "description": (
                    "Liefert Buchungen aus der Finanz-DB nach optionalen Filtern "
                    "(Konto-IBAN, Datums-Range, Gegenseite/Verwendungszweck-Substring, "
                    "Kategorie). Der Suchbegriff wird gegen Gegenseite ODER "
                    "Verwendungszweck ODER Kategorie gematcht. WANN VERWENDEN: "
                    "Konkrete Fragen wie 'Welche berweisungen an X im Mrz?', "
                    "'Alle Lastschriften ber 100 EUR', aber auch freie Cluster "
                    "wie Reise/Event/Ort/Thema ('Paris', 'Hotel', 'Hochzeit'), "
                    "wenn mehrere Buchungen erst identifiziert und danach "
                    "summiert werden mssen. Bevorzuge fuer freie Cluster jedoch "
                    "finance_search_transactions. Fr einfache Standard-Aggregate "
                    "(Summe, Schnitt) lieber finance_aggregate; fr freie "
                    "Sammelmengen erst finance_query_transactions, dann ggf. "
                    "code_executor zum deterministischen Deduplizieren/Summieren."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "iban": {"type": "string", "description": "IBAN des Kontos (optional)"},
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "counterparty_like": {
                            "type": "string",
                            "description": "Substring-Filter auf Gegenseite oder Verwendungszweck",
                        },
                        "category": {"type": "string", "description": "Kategorie-Name (optional)"},
                        "limit": {"type": "integer", "default": 100},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_aggregate",
                "description": (
                    "Aggregiert Einnahmen/Ausgaben/Net-Saldo aus der Finanz-DB "
                    "nach Monat, Konto, Gegenseite oder Kategorie. WANN VERWENDEN: "
                    "Auswertungen wie 'Wie viel habe ich im Q1 ausgegeben?', "
                    "'Top-5 Empfnger nach Volumen', 'Monatliche Sparquote'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "group_by": {
                            "type": "string",
                            "enum": ["month", "account", "counterparty", "category"],
                        },
                        "iban": {"type": "string"},
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                    },
                    "required": ["group_by"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_sum_counterparty_costs",
                "description": (
                    "Summiert Kosten (Ausgaben), Gutschriften und Netto fuer eine "
                    "bestimmte Gegenseite/Keyword im Zeitraum. Nutzt LIKE-Filter auf "
                    "Gegenseite und Verwendungszweck. WANN VERWENDEN: Fragen wie "
                    "'Wie viel habe ich seit Januar bei Apple ausgegeben?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "counterparty": {
                            "type": "string",
                            "description": "Gegenseite/Marke/Keyword, z.B. 'Apple'. Wird als LIKE-Substring-Filter angewandt.",
                        },
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                        "include_transfers": {
                            "type": "boolean",
                            "description": "Interne Transfers einbeziehen (default false)",
                        },
                    },
                    "required": ["counterparty"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_top_counterparty_expenses",
                "description": (
                    "Liefert Top-Gegenseiten nach hoechsten Ausgaben im Zeitraum "
                    "(nur negative Buchungen, absteigend sortiert). WANN VERWENDEN: "
                    "Fragen wie 'Wofuer habe ich am meisten ausgegeben?' oder "
                    "'Top 5 Empfaenger seit Januar'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                        "limit": {"type": "integer", "default": 5},
                        "include_transfers": {
                            "type": "boolean",
                            "description": "Interne Transfers einbeziehen (default false)",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_balance_at",
                "description": (
                    "Liefert den kumulierten Kontostand eines Kontos zum Stichtag. "
                    "Verwendet zuletzt bekannten Anfangssaldo + Buchungen seitdem. "
                    "WANN VERWENDEN: 'Wie hoch war mein Kontostand am 31.12.?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "iban": {"type": "string", "description": "IBAN des Kontos"},
                        "as_of_date": {
                            "type": "string",
                            "description": "Stichtag im Format YYYY-MM-DD",
                        },
                    },
                    "required": ["iban", "as_of_date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_list_categories",
                "description": (
                    "Liefert alle definierten Kategorien (Name, Typ, Farbe). "
                    "WANN VERWENDEN: vor 'finance_assign_category', um zu prüfen, "
                    "ob es bereits eine passende Kategorie gibt."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_assign_category",
                "description": (
                    "Weist einer einzelnen Buchung eine Kategorie zu. Optional "
                    "kann eine Counterparty-Regel angelegt werden, die alle "
                    "zukünftigen UND bisherigen Buchungen derselben Counterparty "
                    "automatisch in diese Kategorie steckt — nachhaltig, kein "
                    "wiederholtes Zuweisen nötig. WANN VERWENDEN: User sagt "
                    "'Diese REWE-Buchung ist Lebensmittel, gilt allgemein'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "transaction_id": {"type": "integer"},
                        "category": {"type": "string", "description": "z.B. 'Lebensmittel'"},
                        "kind": {
                            "type": "string",
                            "enum": ["expense", "income", "transfer"],
                            "description": "Default 'expense'",
                        },
                        "create_rule": {
                            "type": "boolean",
                            "description": "Wenn true, wird Counterparty als Auto-Regel persistiert",
                        },
                    },
                    "required": ["transaction_id", "category"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_suggest_categories",
                "description": (
                    "Lässt das LLM Vorschläge für unkategorisierte Buchungen "
                    "erstellen (strukturierter Output via GBNF). Optional werden "
                    "die Vorschläge sofort übernommen UND für stabile "
                    "Counterparties Auto-Regeln angelegt. WANN VERWENDEN: User "
                    "sagt 'Kategorisier meine offenen Buchungen automatisch'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "iban": {"type": "string"},
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "limit": {"type": "integer", "description": "max 100, default 25"},
                        "apply": {
                            "type": "boolean",
                            "description": "Wenn true, werden Vorschläge sofort persistiert",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_list_rules",
                "description": (
                    "Listet alle Counterparty-Auto-Kategorisierungs-Regeln. "
                    "WANN VERWENDEN: 'Welche Auto-Regeln sind aktiv?'."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_apply_rules",
                "description": (
                    "Wendet alle Counterparty-Regeln auf bestehende Buchungen an. "
                    "Default nur unkategorisierte; mit only_uncategorized=false "
                    "auch Re-Apply auf 'llm'/'rule'-Zuweisungen (User-Zuweisungen "
                    "bleiben immer unangetastet). WANN VERWENDEN: nach Anlage "
                    "neuer Regeln rückwirkenden Backfill auslösen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "iban": {"type": "string"},
                        "only_uncategorized": {"type": "boolean"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_set_budget",
                "description": (
                    "Setzt ein monatliches Budget für eine Kategorie (signed: "
                    "expenses negativ, z.B. -500.00 für 500€ Limit). WANN "
                    "VERWENDEN: User sagt 'Budget für Lebensmittel im März = 400€'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "month": {"type": "string", "description": "YYYY-MM"},
                        "amount": {
                            "type": "number",
                            "description": "Signed: negativ für expenses, positiv für income",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["expense", "income", "transfer"],
                        },
                    },
                    "required": ["category", "month", "amount"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_budget_status",
                "description": (
                    "Soll/Ist-Vergleich aller Kategorien für einen Monat. "
                    "Liefert pro Kategorie budget, actual, remaining. WANN "
                    "VERWENDEN: 'Wie steht mein Budget im April?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "month": {"type": "string", "description": "YYYY-MM"}
                    },
                    "required": ["month"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_monthly_report",
                "description": (
                    "Vollständiger Monatsbericht: Income/Expense/Net, "
                    "Kategorienverteilung, Top-10-Counterparties, Budget-Status. "
                    "WANN VERWENDEN: User sagt 'Monatsauswertung März' oder "
                    "'Wie war mein letzter Monat?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "month": {"type": "string", "description": "YYYY-MM"},
                        "iban": {
                            "type": "string",
                            "description": "Optional: nur ein Konto",
                        },
                    },
                    "required": ["month"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_list_transfer_candidates",
                "description": (
                    "Findet noch unverlinkte Transfer-Kandidaten zwischen den "
                    "Konten (z.B. Kreditkartenabrechnung gegen Sammelbelastung "
                    "auf dem Girokonto). Rein numerisches Matching: |Betrag| "
                    "identisch + Vorzeichen entgegengesetzt + Datum innerhalb "
                    "Fenster + verschiedene Konten. WANN: bevor User-Confirm "
                    "ber finance_link_transfer gemacht wird; auch zur "
                    "Diagnose, wenn Doppelzhlungen vermutet werden."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_days": {
                            "type": "integer",
                            "description": "Toleranzfenster in Tagen (default 5)",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_link_transfer",
                "description": (
                    "Verknpft zwei Buchungen als einen Transfer (z.B. CC-"
                    "Sammelbelastung  CC-Zahlungseingang). Beide Buchungen "
                    "werden danach in Cashflow-Aggregationen ausgeschlossen, "
                    "verhindert Doppelzhlung. Vorzeichen + Konten werden "
                    "validiert."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "outgoing_tx_id": {
                            "type": "integer",
                            "description": "Tx-ID der negativen Buchung",
                        },
                        "incoming_tx_id": {
                            "type": "integer",
                            "description": "Tx-ID der positiven Buchung",
                        },
                    },
                    "required": ["outgoing_tx_id", "incoming_tx_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_unlink_transfer",
                "description": "Lst eine Transfer-Verknpfung wieder auf.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "link_id": {"type": "integer"},
                    },
                    "required": ["link_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_list_statements_with_incomplete_balances",
                "description": (
                    "Listet alle Kontoauszaege, bei denen Eroeffnungs- oder "
                    "Schlusssaldo NULL oder 0 ist. Tritt auf, wenn der "
                    "Auszug mehrseitig war und der alte Extraktor nur den "
                    "Anfang gelesen hat. Liefert statement_id und "
                    "source_filename fuer die anschliessende Reparatur "
                    "via finance_repair_statement_header."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_check_statement_import_completeness",
                "description": (
                    "Bewertet fuer ein importiertes Statement die Datenvollstaendigkeit "
                    "fuer Ausgleichs-/Transferlogik (insb. Kreditkarte gegen Gegenkonto). "
                    "Liefert status/severity sowie Zaehlwerte zu Gegenkonto-Statements, "
                    "Gegenkonto-Transaktionen und Same-Amount-Kandidaten."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "statement_id": {
                            "type": "integer",
                            "description": "ID des importierten Statements",
                        },
                        "settlement_window_days": {
                            "type": "integer",
                            "description": "Ausgleichsfenster in Tagen (default 45)",
                        },
                        "statement_lookback_days": {
                            "type": "integer",
                            "description": "Rueckblick fuer Gegenkonto-Statements in Tagen (default 15)",
                        },
                    },
                    "required": ["statement_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_repair_statement_header",
                "description": (
                    "Re-extrahiert Kopfdaten (Eroeffnungs-/Schlusssaldo, "
                    "Periode) fuer ein bestimmtes Statement aus der "
                    "Original-PDF und aktualisiert die DB-Zeile. Buchungen "
                    "werden nicht veraendert. Benoetigt statement_id (aus "
                    "finance_list_statements_with_incomplete_balances) und "
                    "den absoluten Pfad zur PDF-Datei."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "statement_id": {
                            "type": "integer",
                            "description": "ID des Statements (aus statements-Tabelle)",
                        },
                        "pdf_path": {
                            "type": "string",
                            "description": "Absoluter Pfad zur Original-PDF-Datei",
                        },
                    },
                    "required": ["statement_id", "pdf_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_relink_transfers",
                "description": (
                    "Fuehrt Transfer-Auto-Erkennung fuer ALLE unverlinkten "
                    "Buchungen erneut durch. Sinnvoll nach nachtraeglichem "
                    "Import eines weiteren Kontos (z.B. Kreditkarte), damit "
                    "bestehende Girokonto-Belastungen rueckwirkend mit dem "
                    "Kreditkarten-Eingang verknuepft werden."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_days": {
                            "type": "integer",
                            "description": "Toleranzfenster in Tagen (default 5)",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_list_transfer_links",
                "description": (
                    "Listet alle aktiven Transfer-Verknpfungen mit Beleg-IDs, "
                    "Betrag, Datum, IBANs."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_detect_statement_settlement_gaps",
                "description": (
                    "Diagnostiziert offene Kreditkarten-Statement-Ausgleichsfaelle "
                    "strukturell (kein Keyword-Matching): keine Kandidaten, "
                    "Kandidaten ausserhalb Fenster, mehrdeutige oder eindeutige "
                    "Kandidaten. WANN: wenn Ausgleichsbuchungen fehlen oder "
                    "Doppelzaehlungen geprueft werden sollen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_days_after_statement": {
                            "type": "integer",
                            "description": "Produktives Matching-Fenster in Tagen (default 45)",
                        },
                        "extended_search_days": {
                            "type": "integer",
                            "description": "Erweitertes Diagnosefenster in Tagen (default 180)",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_sum_category_costs",
                "description": (
                    "Summiert Ausgaben/Refunds fuer eine oder mehrere Kategorien "
                    "deterministisch direkt aus der DB. Loest Kategorienamen robust "
                    "auf (Singular/Plural, leichte Schreibvarianten). "
                    "WANN VERWENDEN: 'Wie viel habe ich fuer Lebensmittel ausgegeben?', "
                    "'Kosten in den Kategorien Reise und Hotel'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categories": {
                            "type": "string",
                            "description": "Komma-getrennte Kategorienamen, z.B. 'Lebensmittel,Drogerie'",
                        },
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                        "include_transfers": {
                            "type": "boolean",
                            "description": "Interne Transfers einbeziehen (default false)",
                        },
                    },
                    "required": ["categories"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_cost_structure_analysis",
                "description": (
                    "Analysiert Fixkosten vs. variable Kosten, Top-Kostentreiber "
                    "und monatliche Ausgabenentwicklung. "
                    "WANN VERWENDEN: 'Was sind meine groessten Kostentreiber?', "
                    "'Wie entwickeln sich meine Ausgaben?', Fixkosten-Analyse."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_recurring_expense_analysis",
                "description": (
                    "Deterministische Erkennung wiederkehrender Ausgaben auf Basis "
                    "von Buchungsfrequenz, Monatsabdeckung und Betragsstabilitaet. "
                    "WANN VERWENDEN: 'Welche Kosten laufen monatlich?', "
                    "'Welche Abos habe ich?', 'regelmaessige Belastungen'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                        "min_occurrences": {
                            "type": "integer",
                            "description": "Mindestanzahl Buchungen fuer 'wiederkehrend' (default 2)",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_forecast",
                "description": (
                    "Prognose der naechsten N Ausgabenmonate via rollendem "
                    "Durchschnitt der letzten M Monate. "
                    "WANN VERWENDEN: 'Was werde ich naechsten Monat ausgeben?', "
                    "'Ausgabenprognose fuer Q3', Forecast-Fragen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lookback_months": {
                            "type": "integer",
                            "description": "Monate als Basis fuer Durchschnitt (default 6)",
                        },
                        "forecast_months": {
                            "type": "integer",
                            "description": "Anzahl Prognosemonate (default 3)",
                        },
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_anomaly_detection",
                "description": (
                    "Erkennt Ausreisser-Monate in Ausgaben via Z-Score-Analyse. "
                    "WANN VERWENDEN: 'Gab es ungewoehnliche Ausgaben?', "
                    "'Welcher Monat war teurer als normal?', Anomalie-Fragen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                        "z_threshold": {
                            "type": "number",
                            "description": "Z-Score-Schwellenwert fuer Ausreisser (default 2.0)",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_budget_vs_actual_analysis",
                "description": (
                    "Budget-vs-Ist-Vergleich ueber mehrere Monate mit Abweichung "
                    "pro Kategorie und Gesamtbilanz. "
                    "WANN VERWENDEN: 'Wie habe ich mein Budget eingehalten?', "
                    "'Budget-Auswertung Q1', Soll-Ist ueber mehrere Monate."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_month": {"type": "string", "description": "Startmonat YYYY-MM"},
                        "end_month": {"type": "string", "description": "Endmonat YYYY-MM"},
                    },
                    "required": ["start_month", "end_month"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_savings_potential_analysis",
                "description": (
                    "Priorisierte Sparpotenzial-Analyse: hoechste Ausgabenkategorien "
                    "mit konservativer Einspar-Schaetzung. "
                    "WANN VERWENDEN: 'Wo kann ich sparen?', 'Einsparpotenziale', "
                    "'priorisierte Sparoptionen'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finance_expense_trend_break_detection",
                "description": (
                    "Erkennt strukturelle Trendbrueche in monatlichen Ausgaben durch "
                    "Mittelwertvergleich zweier Zeithaelften. "
                    "WANN VERWENDEN: 'Sind meine Ausgaben gestiegen?', "
                    "'Trendbruch in Ausgaben', strukturelle Veraenderungen."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "end_date": {"type": "string", "description": "ISO YYYY-MM-DD (optional)"},
                        "iban": {"type": "string", "description": "Konto-IBAN (optional)"},
                    },
                    "required": [],
                },
            },
        },
        # ------------------------------------------------------------------
        # SOTA Filesystem-Connector (2026) — list_directory + search_files
        # ------------------------------------------------------------------
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": (
                    "Listet Dateien und Ordner in einem Verzeichnis auf. "
                    "Sicher durch Pfad-Sandbox: Symlinks werden verworfen, "
                    "binäre Dateien werden markiert, maximale Tiefe wird "
                    "eingehalten. "
                    "WANN VERWENDEN: User fragt nach Dateistruktur, "
                    "Inhalt eines Ordners, oder möchte Dateien durchsuchen. "
                    "Beispiel: 'Was ist in meinem Projektordner?' oder "
                    "'Zeige mir die Python-Dateien'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Verzeichnispfad (absolut oder relativ zum "
                                "Workspace). Beispiel: './agent' oder "
                                "'C:\\Dokumente\\agent'"
                            ),
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximale Verschachtelungstiefe (1-5, default 2)",
                            "default": 2,
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "Sucht Datei-Inhalte im Dateisystem (Default: Content-Suche "
                    "via ripgrep) oder Dateinamen (content_search=false, "
                    "Python-Regex). Content-Suche: binär-sicher, Ergebnis-Cap "
                    "(max_results, default 50), Timeout (default 10s, bei "
                    "Überschreitung Partial-Ergebnis), Dateigrößenlimit 20MB, "
                    "respektiert .gitignore und Hidden-Dateien (PII-Schutz: "
                    "z.B. .env wird nicht ungefragt durchsucht). "
                    "WANN VERWENDEN: User sucht nach Code/Text-Inhalten "
                    "(z.B. 'Suche nach def main im Projekt') oder nach "
                    "bestimmten Dateinamen (content_search=false). "
                    "Beispiel: pattern='pytest', glob='*.py' findet alle "
                    "Python-Dateien, die 'pytest' enthalten."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root_path": {
                            "type": "string",
                            "description": (
                                "Startverzeichnis für die Suche (absolut oder "
                                "relativ zum Workspace)"
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Suchmuster — Content-Modus: Rust-Regex "
                                "(lineare, ReDoS-sichere Semantik) oder "
                                "Literal bei fixed_string=true. "
                                "Name-Modus: Python-Regex gegen Dateinamen. "
                                "Beispiele: 'def main', 'pytest', "
                                "'test_.*_test\\.py' (Namen)"
                            ),
                        },
                        "content_search": {
                            "type": "boolean",
                            "description": (
                                "true (Default): Datei-Inhalte durchsuchen "
                                "(ripgrep-Backend). "
                                "false: nur Dateinamen matchen (Python-Walker)"
                            ),
                            "default": True,
                        },
                        "case_insensitive": {
                            "type": "boolean",
                            "description": (
                                "Groß-/Kleinschreibung ignorieren "
                                "(Content-Modus, rg -i). Default: true"
                            ),
                            "default": True,
                        },
                        "fixed_string": {
                            "type": "boolean",
                            "description": (
                                "Pattern als Literal statt Regex behandeln "
                                "(rg -F) — z.B. 'a.b.c' mit Punkten. "
                                "Nur Content-Modus. Default: false"
                            ),
                            "default": False,
                        },
                        "glob": {
                            "type": "string",
                            "description": (
                                "Glob-Filter für Dateinamen, z.B. '*.py' oder "
                                "'src/**/*.ts'. Nur Content-Modus."
                            ),
                        },
                        "hidden": {
                            "type": "boolean",
                            "description": (
                                "Dotfiles/Hidden-Verzeichnisse zusätzlich "
                                "durchsuchen (rg --hidden; "
                                ".gitignore bleibt respektiert). "
                                "Default: false (PII-Schutz)"
                            ),
                            "default": False,
                        },
                        "context": {
                            "type": "integer",
                            "description": (
                                "Zeilen-Kontext vor/nach Treffer "
                                "(rg -C, 0-10). Default: 0"
                            ),
                            "default": 0,
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                "Maximale Trefferzahl (1-500); bei Cap wird "
                                "truncated=true gesetzt. Default: 50"
                            ),
                            "default": 50,
                        },
                        "timeout": {
                            "type": "number",
                            "description": (
                                "Zeitlimit in Sekunden (max. 60); bei Timeout "
                                "Partial-Ergebnis (timed_out=true). "
                                "Default: 10"
                            ),
                            "default": 10,
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": (
                                "Maximale Suchtiefe (1-10), nur Name-Modus. "
                                "Default: 5"
                            ),
                            "default": 5,
                        },
                    },
                    "required": ["root_path", "pattern"],
                },
            },
        },
    ]


def get_tool_names() -> List[str]:
    """Gibt die Namen aller verfügbaren Tools zurück."""
    return [t["function"]["name"] for t in get_tool_schemas()]


def get_finance_tool_schemas(*, include_code_executor: bool = False) -> List[Dict[str, Any]]:
    """Gibt nur die `finance_*`-Tool-Schemas zurueck.

    Wird vom Finanz-Chat (siehe ``finance/chat.py``) genutzt, der dem LLM
    bewusst nur den Finance-Werkzeugkasten exponiert -- kein web_search,
    kein RAG, kein code_executor. Single Source of Truth bleibt
    ``get_tool_schemas()``; dieser Filter ist die einzige zulaessige
    Selektion (kein Pattern-Hack ausserhalb dieses Moduls).
    """
    finance_tools = [
        s for s in get_tool_schemas()
        if s["function"]["name"].startswith("finance_")
    ]
    if include_code_executor:
        code_schema = get_tool_schema_by_name("code_executor")
        if code_schema is not None:
            finance_tools.append(code_schema)
    return finance_tools


def get_tool_schema_by_name(name: str) -> Dict[str, Any] | None:
    """Gibt das Schema eines einzelnen Tools zurück."""
    for t in get_tool_schemas():
        if t["function"]["name"] == name:
            return t
    return None


def get_toolkit_format_schemas() -> Dict[str, Dict[str, Any]]:
    """Konvertiert OpenAI-Schemas ins Toolkit-Format (description + parameters).

    Wird von ``AgentToolkit._initialize_tools()`` verwendet, damit es nur
    EINE kanonische Schema-Quelle gibt.  Toolkit-spezifische Extras
    (image_info, session_manager) werden dort separat hinzugefügt.

    Returns:
        Dict mit ``{tool_name: {"description": ..., "parameters": ...}}``
    """
    result: Dict[str, Dict[str, Any]] = {}
    for schema in get_tool_schemas():
        func = schema["function"]
        name = func["name"]
        result[name] = {
            "description": func["description"],
            "parameters": func["parameters"],
        }
    return result
