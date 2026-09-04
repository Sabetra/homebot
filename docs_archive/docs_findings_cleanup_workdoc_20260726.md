# Dokumentations-Findings - Arbeitsdokument

Status: Validierung
Datum: 2026-07-26

## Originalauftrag

> Das andere LLM arbeitet mit `<PROJEKT_ROOT>\finance_analysis_workdoc_20260726.md` aktuell noch eine Aufgabe ab. Korrigiere bitte alle Findings.

## Scope

- Verifizierte Drift in aktiven Markdown-Dokumenten gegen aktuellen Code korrigieren.
- Tote Links, falsche Symbole, unbelegte Konfiguration und widerspruechliche Statusangaben bereinigen.
- Das aktive Finance-Workdoc und parallel geaenderten Finance-/Psycho-Code nicht veraendern.

## Fakten und Hypothese

- Code ist Source of Truth.
- `README.md` verweist auf drei nicht vorhandene aktive Dateien.
- Aktuelle Finance-APIs: `FinanceQueryPlanner.plan()`, `GrammarCompiler.compile_for_schema()`, `FinanceQueryReflector.decide()`.
- Die dokumentierten Variablen `FINANCE_CACHE_ENABLED`, `FINANCE_REFLECTOR_MAX_RETRIES`, `RAG_CHANGE_DETECTOR_ENABLED`, `RAG_DOCLING_PARALLEL_WORKERS`, `RAG_MULTIMODAL_ENABLED`, `RAG_STRIXKAT_ENABLED` und `RAG_AUTO_ROLLBACK_THRESHOLD` sind im aktiven Code nicht implementiert.
- Community-Detection-Code und Tests existieren; Dokument 14 beschreibt noch einen alten Arbeitszustand.
- Hypothese: Die Findings sind Dokumentationsdrift und lassen sich ohne Produktivcode-Aenderung beheben.

## Plan und Validierung

1. Tote README-Verweise auf existierende kanonische Dokumente umstellen; Linktest.
2. Dokuindex und Master-Kontext um den verifizierten KG-Status ergaenzen.
3. Architektur-, Finance-, Developer- und Context-Doku gegen aktuelle APIs/Konfiguration korrigieren.
4. Roadmap als belegten Ist-/Backlog-Status statt Technologie-Behauptungen formulieren.
5. Veraltete Eintraege in `funktionen.md` durch aktuelle kontrollierende Pfade ersetzen.
6. Markdown-Links, Symbole und Env-Namen automatisiert pruefen; fokussierte KG-Tests ausfuehren.

## Fortschritt

- [x] Parallele Aenderungen und aktives Finance-Workdoc abgegrenzt.
- [x] Zehn potenziell betroffene Dokumente gesichert.
- [x] README, Index, Master-Kontext, Architektur, Roadmap, Developer Guide, Context Engineering, Community-Status und `funktionen.md` korrigiert.
- [x] `docs/03_FINANCE_MODULE.md` nicht ueberschrieben: Die Datei wurde waehrend dieser Arbeit durch den parallelen Finance-Agenten substanziell geaendert. Der aktuelle Inhalt enthaelt weiterhin alte Planner-/Grammar-/Reflector-Beschreibungen und muss nach Abschluss dieses Agents gegen die dann finale Finance-Implementierung konsolidiert werden.
- [x] Neun bearbeitete aktive Markdown-Dateien: alle lokalen Links loesen auf.
- [x] Audit-Driftcheck: keine alten Orchestrator-/Finance-Symbole, Phantom-RAG-Variablen, toten README-Ziele oder ersetzten Roadmap-Claims in den bearbeiteten Dateien.
- [x] `tests/test_community_detection.py`: 7 Tests bestanden.
- [x] Statische Editor-Diagnostik: keine Fehler in den bearbeiteten aktiven Dokumenten.
- [ ] Abschluss nach Finance-Agent: `docs/03_FINANCE_MODULE.md` konsolidieren, erneut validieren und dieses Workdoc loeschen.
