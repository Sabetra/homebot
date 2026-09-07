<!-- last-verified: 2026-07-25 -->
# Chat-Streaming-Architektur

> **Stand:** 2026-07-25 | **Status:** Implementiert und verifiziert
> **Scope:** Normaler Streamlit-Chat, Routen SIMPLE, PLAN_EXECUTE, REACT, VISION und CACHE

## 1. Zielzustand

Der normale Chat verwendet einen typisierten, request-lokalen Event-Stream. Die UI zeigt
Text und Fortschritt bereits waehrend eines Runs, persistiert eine Assistant-Antwort aber
erst nach einem erfolgreichen `RunCompleted`. Abbruch und Fehler erzeugen eigene terminale
Events und hinterlassen keine partielle Assistant-Antwort in Chat-History oder SQLite.

```text
Streamlit chat_tab
  -> get_ai_response_events()
    -> AgentChatbotLogic.stream_chat_events()
      -> worker thread + queue
        -> route selection
        -> model/agent execution
        -> typed ChatEvent sequence
      <- TextDelta / progress / terminal event
  -> st.write_stream(TextDelta only)
  -> persist ChatRunResult only after RunCompleted
```

## 2. Event-Vertrag

`agent/streaming_events.py` ist die zentrale Definition. Alle Events sind immutable
Pydantic-v2-Modelle (`extra="forbid"`) und tragen `run_id`, `session_id`, monotone
`sequence`, UTC-`timestamp` und optional die Route.

| Gruppe | Events | Zweck |
|--------|--------|-------|
| Lifecycle | `RunStarted`, `RunCompleted`, `RunCancelled`, `RunFailed` | Genau ein terminaler Ausgang pro Run |
| Routing | `RouteSelected` | Sichtbare Route ohne String-Protokoll zwischen Schichten |
| Fortschritt | `StepStarted`, `StepFinished`, `ToolStarted`, `ToolFinished` | Strukturierte Agent-/Tool-Aktivitaet |
| Antwort | `TextStarted`, `TextDelta`, `TextFinished` | Ausschliesslich darstellbarer Assistant-Text |
| Metadaten | `SourcesUpdated`, `UsageUpdated` | Quellen und Laufzeitmetriken |

`ChatRunResult` ist das kanonische Abschlussobjekt. Sein `text` muss dem aus allen
`TextDelta`-Events sichtbaren Text entsprechen. Die UI prueft diese Invariante vor der
Persistierung explizit.

Grafische Ergebnisse werden nicht aus Antworttexten oder Marker-Strings rekonstruiert.
`ChatRunResult.graphics` transportiert validierte `GraphicArtifact`-Objekte mit genau
einem Payload: lokaler Dateipfad oder Base64-Bilddaten. Diagrammtyp, Renderer, MIME-Typ
und Caption bleiben bis zur Streamlit-History und SQLite-Metadaten erhalten. Reine,
erfolgreiche Grafiktool-Laeufe (`create_diagram`, `canvas`, `code_executor`) schliessen
deterministisch ab, ohne danach eine sachfremde RAG-/Web-Quellensuche zu starten.

Nutzerprogramme besitzen einen separaten, expliziten Vertrag: `code_executor` erhaelt
`deliver_to_user=true` nur bei angeforderten, wiederverwendbaren Skripten, Spielen oder
Apps. Erst nach erfolgreicher Sandbox-Ausfuehrung wird die letzte, gegebenenfalls per
Auto-Fix korrigierte Codeversion als saubere `.py`-Datei gespeichert. Interner Rechen-
und Verifikationscode bleibt ephemer. `ChatRunResult.files` transportiert typisierte
`FileArtifact`-Metadaten bis zum Streamlit-Download und in die SQLite-History; fehlgeschlagener
oder sicherheitsblockierter Code wird nie als fertiges Programm angeboten.

`ChatEventConsumer` demultiplext schichtuebergreifende Events ausschliesslich ueber
den stabilen Pydantic-Discriminator `event.type`. Python-Klassenidentitaet ist bewusst
kein Teil des Protokolls: Nach einem Streamlit-Hot-Reload koennen Producer und UI noch
verschiedene Klassenobjekte derselben Eventdefinition halten. Der Consumer normalisiert
deshalb auch ein Completion-Resultat aus einer neu geladenen Modellklasse, protokolliert
die beobachteten Eventtypen und verwirft mehrere terminale Events als Protokollfehler.

## 3. Verhalten je Route

| Route | Laufzeitverhalten | Sicherheitsentscheidung |
|-------|-------------------|-------------------------|
| SIMPLE | Native llama.cpp-Deltas via `generate_response_stream()` | Inkrementeller Filter entfernt private Denkmarker, Rollenfortsetzungen und `[FOLLOW_UP]`-Metadaten auch ueber Chunk-Grenzen |
| PLAN_EXECUTE | Strukturierte Schritt-/Tool-Fortschritte; kanonischer Finaltext nach Abschluss | Keine ungeprueften Zwischentexte als Antwort |
| REACT | Fortschrittsereignisse; Ausgabe erst nach Citations, Verification und PII-Redaction | Drafts und private Reasoning-Inhalte bleiben intern |
| VISION | Route und Fortschritt als Events; finaler Text nach Vision-Verarbeitung | Kein text-only Generator fuer multimodale Eingaben |
| CACHE | Gecachter kanonischer Text wird ueber den gleichen Event-Vertrag geliefert | Gleiche Completion-/Persistenzregeln wie Live-Runs |

### Sichtbare Prozess-Timeline

Die Chat-UI bewahrt waehrend eines Runs die letzten acht sicheren Prozessschritte auf.
Abgeschlossene Schritte erhalten einen Haken, der aktive Schritt einen Warteindikator.
PLAN_EXECUTE meldet unter anderem Analysevorbereitung, Reasoning-Phase, Planung,
Planergebnis mit Toolanzahl, Werkzeug-/Quellenverarbeitung, Antwortsynthese und
Qualitaetssicherung. Die alternative Optimized-Research-Engine meldet Reasoning-Phase,
Quellensuche, Quellenbewertung, Antwortsynthese und Ausgabe-Finalisierung.

Dabei werden keine privaten Chain-of-Thought-Inhalte ausgegeben. Sichtbar sind nur
prozessuale Metadaten wie Phase, Route, Komplexitaetsklasse, Tokenbudget, Anzahl der
geplanten Tools und aggregierte Quellenqualitaet. SIMPLE streamt echte Modell-Deltas.
Agent- und Research-Antworttexte erscheinen dagegen erst nach ihren Safety- und
Qualitaets-Gates; die Timeline liefert waehrend dieser Zeit den Laufzeitfortschritt.

## 4. Cancellation und Atomizitaet

- `StreamingContext` besitzt pro Request Sequenzierung, Timing und `threading.Event` fuer Cancellation.
- `ActiveRunRegistry` isoliert aktive Runs pro UI-Session; ein neuer Run derselben Session bricht den vorherigen kooperativ ab.
- `ModelLoader.generate_response_stream()` prueft Cancellation zwischen Deltas und schliesst den llama.cpp-Iterator im `finally`-Pfad.
- Der CUDA-Lock bleibt ueber den gesamten Iterator-Lebenszyklus gehalten.
- `AgentChatbotLogic.stream_chat_events()` snapshotet die interne History und rollt sie bei Abbruch oder Fehler zurueck.
- `ui_tabs/chat_tab.py` schreibt Assistant-History und SQLite nur nach `RunCompleted`.
- Fehlertexte werden als `RunFailed` transportiert und nie als vermeintliche Assistant-Antwort gestreamt.
- `[FOLLOW_UP]`-Bloecke, auch abgeschnittene ohne Schlusstag, werden nicht gerendert oder persistiert. Ihre Inhalte werden separat als klickbare naechste Nutzerfragen uebergeben.
- Der zentrale Follow-up-Promptvertrag verlangt Nutzerperspektive: keine Fragen des Assistenten an den Nutzer und keine Angebote wie "Soll ich ...?".

## 5. Nutzwert

| Vorher | Jetzt | Nutzen fuer Anwender |
|--------|-------|----------------------|
| Mehrere Sekunden ohne Rueckmeldung | Textdeltas oder lokalisierter Routenschritt erscheinen waehrend der Verarbeitung | Geringere wahrgenommene Wartezeit und sichtbarer Systemzustand |
| Antwort erschien komplett am Ende | SIMPLE-Antwort baut sich waehrend der Inferenz auf | Lesen kann vor Ende der Generierung beginnen |
| Kein kontrollierter Abbruch | Stop-Button bricht den aktiven Session-Run kooperativ ab | Lange oder versehentliche Runs lassen sich beenden |
| Partielle Ausgabe konnte semantisch unklar sein | Persistenz nur nach kanonischem Completion-Event | Chatverlauf enthaelt keine abgebrochenen oder fehlgeschlagenen Antworten |
| Agent-Drafts haetten Gates umgehen koennen | REACT-Ausgabe erst nach Citation-, Verification- und PII-Gates | Streaming senkt nicht das bestehende Sicherheitsniveau |
| Freie Callback-Strings zwischen Schichten | Diskriminierte, validierte Events | Stabilere UI-Integration und gezieltere Tests |
| Hot-Reload konnte gueltige Terminalevents per `isinstance` verfehlen | Consumer wertet den serialisierbaren `type`-Discriminator aus | Kein falsches "Antwortgenerierung endete unerwartet" nach Code-Reload |
| Agent-Runs zeigten nur den jeweils letzten Status | Bis zu acht abgeschlossene und aktive Phasen bleiben als Timeline sichtbar | Nutzer erkennen Route, Planung, Toolausfuehrung, Synthese und Pruefung waehrend langer Runs |

## 6. SOTA-Bewertung (1-7)

| Kategorie | Bewertung | Begruendung |
|-----------|-----------|------------|
| Event- und Vertragsarchitektur | 7/7 | Typisiert, immutable, geordnet, request-lokal und mit eindeutigen terminalen Events |
| Wahrgenommene Latenz | 6/7 | Native Deltas fuer SIMPLE und Fortschritt fuer Agent-Routen; Vision und sichere Agent-Finalisierung bleiben bewusst gepuffert |
| Safety und Datenkonsistenz | 7/7 | Kein Raw-CoT, Post-Gate-REACT, kanonische Textidentitaet und completion-only Persistenz |
| Cancellation | 6/7 | Durchgehend bis zum Modelliterator; laufende blockierende Drittanbieter-/Tool-Calls koennen nur an kooperativen Grenzen stoppen |
| Observability und Tests | 6/7 | Route, Schritte, Quellen, TTFT und Dauer sind modelliert; vollstaendige Token-Nutzungswerte sind noch nicht fuer alle Routen verfuegbar |
| **Gesamt** | **6.4/7** | SOTA-nahe lokale Streaming-Architektur ohne Safety-Abkuerzung |

## 7. Verifikation

Abgedeckte Invarianten:

- Event-Reihenfolge, Pydantic-Discriminator und genau ein terminales Event
- Erkennung und Normalisierung eines `run_completed` aus fremder/reloadeter Klassenidentitaet
- Ablehnung doppelter terminaler Events und Diagnose der beobachteten Eventtypen
- Session-Isolation und Cancellation ohne `RunCompleted`
- Schliessen des Modelliterators bei Abbruch und Fehler
- Filterung privater Denkmarker und `USER:`-Fortsetzungen ueber Chunk-Grenzen
- Trennung vollstaendiger und abgeschnittener `[FOLLOW_UP]`-Bloecke vom sichtbaren Text
- Promptvertrag fuer klickbare Folgefragen aus Nutzerperspektive
- REACT-Callback erhaelt nur den redigierten kanonischen Finaltext
- Identitaet von sichtbarem, internem und persistiertem Assistant-Text
- Rollback interner History bei Cancellation
- Schluesselparitaet der Streaming-Texte in DE, EN und BG
- Dauerhafte UI-Timeline aus geordneten `StepStarted`/`StepFinished`-Events
- Sichere Checkpoints in Standard-PLAN_EXECUTE und Optimized Research ohne Raw-CoT
- Datei- und Base64-Grafiken vom Toolresultat bis zum kanonischen Completion-Resultat
- Erfolgreich getestete Nutzerprogramme als persistente Downloads; kein Artefakt bei Fehlern
- Mermaid-Browserparse fuer gueltige SVGs und kontrollierter Quellcode-Fallback bei Syntaxfehlern

Hardware-Canary am 2026-07-25: lokales `gemma-3-12b-it` GGUF auf RTX 4090,
3 native Chunks, kanonischer Text `STREAM_OK`, Runtime-Kontext 16384. Zusaetzlich
schlossen zwei aufeinanderfolgende echte Browser-Runs im frisch gestarteten Streamlit-
Prozess mit je genau einem Assistant-Turn ab; kein `stream_incomplete`, kein doppelter
Text und korrekte History-Persistenz. Aktuelle Vollsuite inklusive Grafikvertrag:
189 Tests bestanden.

Timeline-Canary am 2026-07-25 im frischen Streamlit-Prozess mit Gemma 3 12B:
PLAN_EXECUTE zeigte nacheinander Routing, Recherche/Synthese, Komplexitaet und
Tokenbudget, Analysevorbereitung, Reasoning-Phase, Planung, Planergebnis mit zwei
Tools sowie die aktive Werkzeug-/Quellenphase. Aktuelle Vollsuite: 189 Tests bestanden.

## 8. Relevante Dateien

| Datei | Verantwortung |
|-------|---------------|
| `agent/streaming_events.py` | Eventmodelle, kanonisches Resultat, discriminator-basierter Consumer, Context und Active-Run-Registry |
| `agent/streaming_text_filter.py` | Chunk-grenzenfeste Filterung nicht sichtbarer Modellteile |
| `scripts/model_loader.py` | Native llama.cpp-Iteration, Stop-Merge, Cancellation und Cleanup |
| `chatbot_logic.py` | SIMPLE-Streaming und History-Identitaet |
| `agent_chatbot_logic.py` | Producer-Thread, Queue, Routing, Terminalsemantik und Rollback |
| `agent/orchestrator.py` | Normalisierung von Grafik- und Dateiartefakten mit deterministischem Early-Completion |
| `agent/react_agent.py` | Post-Gate-Ausgabe sowie Trennung interner Codeausfuehrung von Nutzerprogrammen |
| `enhanced_streamlit_bot.py` | Locale-Aufloesung und UI-Event-Bridge |
| `ui_tabs/chat_tab.py` | Event-, Mermaid- und Bild-Rendering, Stop und completion-only Persistenz |
| `utils/mermaid_diagram.py` | Sanitization, parser-geprueftes Browser-Rendering und sicherer Export |

## 9. Bewusste Grenzen

- Agent- und Vision-Drafts werden nicht fuer einen kuenstlichen Typewriter-Effekt vor ihren Safety-Gates angezeigt.
- Cancellation ist kooperativ; ein einzelner nicht unterbrechbarer Tool-Aufruf endet vor dem naechsten Checkpoint.
- `UsageUpdated` liefert derzeit verlaesslich TTFT und die Completion-Metriken liefern Dauer; Tokenzaehler und Tokens/s sind noch nicht auf allen Routen befuellt.
- Der synchrone Kompatibilitaetspfad `get_ai_response_modern()` bleibt fuer andere Call-Sites erhalten; der normale Chat nutzt die Event-Bridge.