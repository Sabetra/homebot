# Workdoc: Web-Provenance im Psychotab

> **Erstellt:** 2026-08-04
> **Abschluss-Ziel:** 2026-08-04
> **Status:** ABGESCHLOSSEN
> **Autor:** GitHub Copilot

## Original-Auftrag

Der Psychotab hat gerade munter Webadressen erfunden und behauptet, er habe eine Onlinerecherche gemacht, obwohl es nicht stimmt. Erfolgen hier nicht dieselben Mechanismen gegen Halluzinationen wie im normalen Chattab?

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Request-lokaler Web-Provenance-Vertrag im Psychotab | Umbau auf vollständigen Agent-/REACT-Modus |
| URL-Allowlist aus tatsächlichen Webresultaten | Medizinische Bewertung der zitierten Inhalte |
| Rechercheclaim-Erkennung, Regeneration und Fail-Closed | Generelles Websearch-Engine-Redesign |
| DE/EN/BG Claim-Erkennung | Änderungen am normalen Chatstreaming |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Ohne Webresultate werden Recherchebehauptungen erkannt | Unit-Test | ✅ |
| 2 | Ohne Webresultate werden externe URLs erkannt | Unit-Test | ✅ |
| 3 | Mit Webresultaten sind nur exakt gelieferte URLs zulässig | Unit-Test | ✅ |
| 4 | Psychchat regeneriert einen ungedeckten Draft genau einmal | Integrationstest | ✅ |
| 5 | Zweiter ungedeckter Draft wird nicht gespeichert/ausgegeben | Integrationstest | ✅ |
| 6 | Psych-/Chatregression bleibt grün | Pytest | ✅ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A | Nur Prompt ergänzen | Klein | Modell kann Regel erneut verletzen | 3 | 2 | 7 | 4 | Verworfen |
| B | URLs nachträglich entfernen | Einfach | Behauptungen/Inhalt bleiben unbelegt | 3 | 3 | 7 | 4 | Verworfen |
| C | Prompt + Allowlist + Postvalidation + eine Regeneration | Provenance end-to-end | Maximal ein zusätzlicher LLM-Call bei Verstoß | 7 | 7 | 6 | 6 | Umsetzen |
| D | Psychotab durch normalen Agent-Chat ersetzen | Volle Agent-Gates | Vermischt Session-/Safety-Architektur | 5 | 5 | 2 | 2 | Verworfen |

> **Auswahl:** Option C. Die Stelle mit tatsächlicher Web-Evidence (`psychological_chat`) besitzt den Vertrag und kann Ausgabe gegen request-lokale Quellen prüfen.

## Abhängigkeiten & Stakeholder

| # | Abhängigkeit | Art | Impact | Status |
|---|--------------|-----|--------|--------|
| 1 | `AgentChatbotLogic.psychological_chat()` | Owner von RAG/Web und Generation | Guard-Integration | offen |
| 2 | `THERAPEUTIC_SYSTEM_PROMPT_BASE` | Single Source Prompt | statische Invariante | offen |
| 3 | `ChatInputHandler` | Persistiert Finaltext | erhält nur geprüften Text | unverändert |
| 4 | Normaler Chat | Vergleichspfad | keine Codeänderung | verifiziert |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | Psychotab nutzt separaten `psychological_chat()` ohne Agent-Modus | Methodendokumentation/Runtimepfad |
| 2 | FACTUAL darf Webfallback ausführen; MIXED/PERSONAL nicht | `web_fallback_allowed` |
| 3 | `web_results` existiert nur lokal in `psychological_chat()` | Generierungspfad |
| 4 | Web-URLs werden in den Prompt injiziert, aber Output-URLs nicht dagegen geprüft | `web_context`/Generation |
| 5 | Ohne Webresultate gibt es keine Quellenmetadaten; freie LLM-Generation läuft dennoch | Generation-Pfad |
| 6 | ChatInputHandler prüft nur nicht-leeren String | `_validate_response()` |

## Hypothese und Falsifizierung

**Hypothese:** Fehlende request-lokale Provenance-Validierung erlaubt dem Modell sowohl erfundene Rechercheclaims als auch beliebige URLs. Ein Guard am `psychological_chat()`-Owner kann diese anhand der tatsächlichen `web_results` vollständig unterscheiden.

**Falsifizierung:** Guard-Tests müssen unbelegte Claims/URLs ablehnen, erlaubte URLs akzeptieren und eine URL aus einem nicht gelieferten Host selbst bei vorhandener Websuche ablehnen. Integration muss genau eine Regeneration und danach Fail-Closed zeigen.

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|-------------------|------------|-------------------|--------|
| 1 | Nutzer nennt selbst eine URL | mittel | mittel | Guard bewertet Assistant-Ausgabe gegen aktuelle Tool-Evidence; keine Übernahme als Quelle | offen |
| 2 | Medizinische Notfall-URL wird blockiert | niedrig | hoch | Deterministischer Krisenpfad liegt außerhalb freier Psychchat-Generation | verifiziert |
| 3 | False Positive bei Wort „online“ | mittel | niedrig | enge Rechercheclaim-Muster statt Einzelwort | offen |
| 4 | Zweiter Draft verletzt Vertrag | niedrig | hoch | deterministischer Fail-Closed-Text ohne URL/Quellenclaim | offen |

## Sicherheits- & PII-Implikationen

Keine neuen Netzwerkaufrufe. Die Allowlist enthält nur URLs aus dem aktuellen lokalen Toolresultat und wird nicht persistiert. Krisenressourcen bleiben im separaten deterministischen Safety-Pfad.

## Rollback-Strategie

Gezielte Backups unter `backups/psych_web_provenance_20260804/`.

## Änderungen und Testergebnisse

- Neu: `psychological_session/response_provenance.py` mit URL-Extraktion, Claim-Erkennung, dynamischem Promptvertrag und Exactly-Once-Regeneration.
- `AgentChatbotLogic.psychological_chat()` bildet die Allowlist ausschließlich aus `web_results` des aktuellen Requests und prüft den Draft vor Postprocessing/Rückgabe.
- `THERAPEUTIC_SYSTEM_PROMPT_BASE` dokumentiert die unveränderliche Quellenregel auch für Fallback-Systemprompts.
- Fokussierter Red-Test: Importfehler wegen fehlendem Guard; nach Umsetzung `11 passed`.
- Relevante Psychregression nach finaler Änderung: `63 passed`.
- VS-Code-Diagnostik: keine Fehler in allen betroffenen Python-Dateien.
- `py_compile` konnte eine bestehende gesperrte `.pyc` nicht ersetzen (`WinError 5`); keine Codeursache, Pytest-Import und VS-Code-Diagnostik sind erfolgreich.
