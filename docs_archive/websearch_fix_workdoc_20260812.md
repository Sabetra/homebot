# Workdoc: WebSearchPlanner max_tokens-Compatibility

> **Erstellt:** 2026-08-12 12:00
> **Status:** ABGESCHLOSSEN
> **Autor:** Bot
> **Reviewer:** User

---

## Original-Auftrag

Fehler im Log:

INFO:agent.orchestrator:SOTA-Pipeline: WebSearchPlanner/Reflector nicht verfuegbar: WebSearchPlanner.__init__() got an unexpected keyword argument 'max_tokens'

Ziel: Die nachgewiesene Ursache beheben, ohne das Produktiv-LLM-Profil oder GPU-Parameter zu verändern. Dabei die AGENTS.md/00_CONTEXT_MASTER.md-Regeln beachten, Backups anlegen, das kleinste konsistente Fix umsetzen und die relevante Regression verifizieren.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Fix des Konstruktor-Konflikts beim WebSearchPlanner/Reflector | GPU-Parameter-Optimierung oder Benchmark-Auftrag |
| Kleine Regression zum max_tokens-Contract | große Architekturänderungen am SOTA-Pipeline |
| Verifikation unter der validierten Produktiv-Umgebung | Deployment oder Infrastrukturänderung |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Ursache ist im Code belegt | Codepfad + Konstruktor-Signatur geprüft | ✅ |
| 2 | Fix ist minimal und konsistent | Einziger Parametriker + Nutzung in _generate | ✅ |
| 3 | Relevante Regression ist nachweisbar | pytest in venv_mistral_gguf | ✅ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | Konstruktor akzeptiert max_tokens und nutzt es | kompatibel zum Orchestrator-Contract | zusätzlicher Attribut-Contract | 5/5 | 5/5 | 5/5 | 1/5 | ✅ gewählt |
| B | Orchestrator ruft ohne max_tokens | weniger Codeänderung | bricht den bestehenden Vertrag und Umgebungskonventionen | 2/5 | 2/5 | 4/5 | 3/5 | verworfen |

> **Auswahl:** Option A — Begründung: Der Codepfad in [agent/orchestrator.py](agent/orchestrator.py#L577-L593) initialisiert die WebSearch-Objekte mit einem `max_tokens`-Argument; die Klasse in [agent/web_search_planner.py](agent/web_search_planner.py#L96-L195) akzeptierte das Attribut bisher nicht. Die konsistente Korrektur ist die Signatur zu erweitern, nicht den Aufruf zu entfernen.

## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|
| 1 | Orchestrator initialisiert Planner und Reflector mit max_tokens | [agent/orchestrator.py](agent/orchestrator.py#L577-L593) |
| 2 | WebSearchPlanner.__init__ akzeptierte bislang kein max_tokens | [agent/web_search_planner.py](agent/web_search_planner.py#L96-L139) |
| 3 | Productive venv contains llama_cpp | Shell-Verifikation: `<PROJEKT_ROOT>\venv_mistral_gguf\Scripts\python.exe` imports llama_cpp |
| 4 | AGENTS.md mandates the working venv and forbids GPU-param increases without benchmark task | [AGENTS.md](AGENTS.md#L6-L34) |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | Die Warnung verschwindet, sobald die Konstruktor-Signatur konsistent ist | bestätigt | Initialisierung ohne TypeError und pytest-Regressionslauf |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | [agent/web_search_planner.py](agent/web_search_planner.py) | `max_tokens` in beiden Konstruktoren akzeptiert, gespeichert und in `_generate()` verwendet | ✅ Regressionstest passes |
| 2 | [tests/test_meta_capability_gate.py](tests/test_meta_capability_gate.py) | Regressions-Test für `max_tokens`-Compatibility ergänzt | ✅ passes |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `<PROJEKT_ROOT>\venv_mistral_gguf\Scripts\python.exe -m pytest tests/test_meta_capability_gate.py -q -rA -s` | 4 passed in 106.41s | 2026-08-12 |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|
| 1 | Die System-Umgebung kann außerhalb des venv trotzdem mit fehlender Abhängigkeit laufen | mittel | bei lokalen Verifikationen immer das Projekt-venv verwenden |
