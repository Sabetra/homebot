<!-- last-verified: 2026-09-04 -->
# Standard-Prompt für Agenten-Aufgaben

> Zuletzt aktualisiert: 2026-07-31
> Grund: Reactive Search Pattern in Schritt 10 eingebettet (SOTA Websearch-Awareness)

---

Arbeite auf Grundlage der Repository-Anweisungen in AGENTS.md.

Projektkontext:
- Windows 11, 64 GB RAM, RTX 4090 mit 24 GB VRAM
- Produktiv-LLM: Gemma4 12B GGUF über llama-cpp-python
- Virtuelle Umgebung (validierte Produktivumgebung):
  <PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1
  (venv_mistral_gguf ist nur die Rollback-Umgebung)
- Verifizierte GPU-Parameter dürfen ohne Benchmark-Auftrag nicht erhöht werden.

Vorgehen:
1. Lies zuerst docs/00_CONTEXT_MASTER.md.
2. Lies danach nur die Dokumentation und den Code, die für die konkrete
   Aufgabe erforderlich sind. Der Code ist die maßgebliche Wahrheit.
3. Formuliere vor der ersten Änderung:
   - eine konkrete, falsifizierbare Hypothese,
   - den kontrollierenden Codepfad,
   - einen günstigen Test, der die Hypothese widerlegen kann.
4. Erstelle ein temporäres Workdoc basierend auf docs/templates/WORKDOC_TEMPLATE.md.
   Es enthält mindestens:
   - den vollständigen ursprünglichen Auftrag,
   - Scope und Nicht-Scope,
   - Definition of Done (falsifizierbare Abnahmekriterien),
   - Alternativen & Entscheidung (mindestens 2 Optionen, 5-Kriterien-Bewertung),
   - Abhängigkeiten & Stakeholder (betroffene Module/Personen),
   - verifizierte Fakten mit Datei-/Symbolbelegen,
   - offene Hypothesen klar getrennt von bestätigten Befunden,
   - Risiko & Impact-Matrix (Wahrscheinlichkeit × Auswirkung × Minderung),
   - Sicherheits- & PII-Implikationen (bei Berührung relevanter Bereiche),
   - Rollback-Strategie,
   - Änderungen und Testergebnisse.
   Kopiere keine ganzen Quelldateien oder Dokumentationen hinein.
   Nutze und aktualisiere es regelmässig, um den Fokus nicht zu verlieren und Redundanzen in der Arbeit zu vermeiden.
   Nicht-anwendbare Sektionen entfernen (nicht auskommentieren).
5. Erstelle vor Änderungen Backups gemäß AGENTS.md unter backups/.
   Verändere keine fremden oder taskfremden Änderungen.
6. Behebe die nachgewiesene Ursache mit der kleinsten konsistenten Änderung.
   Keine stillen Fallbacks, keine pauschalen Exception-Handler und keine
   Architekturwechsel ohne belegten Bedarf.
7. Führe unmittelbar nach der ersten Änderung den kleinsten relevanten Test
   aus. Erweitere die Tests entsprechend Risiko und Blast Radius.
8. Behaupte keinen Fehler ohne mindestens einen dieser Belege:
   - reproduzierender Test oder reproduzierbarer Lauf,
   - konkreter unerreichbarer/falscher Codepfad,
   - verletzter Vertrag, Typ oder dokumentierte API-Semantik.
   Prüfe aktiv, was den Befund widerlegen würde.
9. Behebe auch taskfremde Fehler, Warnungen oder Deadcode-Funde.
   Dokumentiere sie separat mit Schweregrad und Beleg.
 10. Nutze Webrecherche (MCP-Tools: websearch → fetch_content), wenn die
     Entscheidung von aktueller externer Information abhängt.

     REACTIVE SEARCH PATTERN (SOTA):
     a) Erste Suche: websearch("breiter Query") → grobe Übersicht
     b) Ergebnis in <thinking> bewerten:
        - Mindestens 3 relevante Ergebnisse?
        - Verschiedene Quellen (nicht alle derselben Domain)?
        - Deckt den benötigten Aspekt ab?
     c) Bei unzureichenden Ergebnissen (max 2 Reformulierungen):
        - Query spezifischer machen, Synonyme, DE/EN wechseln
        - Erneute Suche mit verbessertem Query
     d) fetch_content(url) für die 1–3 vielversprechendsten Quellen
     e) Bei Wissenslücken: gezielte Follow-up-Suche mit spezifischen Keywords

     Typische Einsatzszenarien:
     - Unsichere API-Calls: websearch("pydantic v2 field_validator") → fetch_content(docs.pydantic.dev) → Code
     - Unbekannte Fehler: websearch("exact error message") → StackOverflow/GitHub Issues
     - SOTA-Entscheidungen: websearch → arxiv.org/paperswithcode.com → fetch_content
     - Neue Library-Features: offizielle Doku via fetch_content lesen
     Bevorzuge Primärquellen und dokumentiere Quelle, Datum und Konsequenz.
     Bei "Domain not in allowlist"-Fehler: Nur offizielle Framework-Doku oder
     etablierte Quellen zur Allowlist in server.py hinzufügen (nie Wildcards).
11. Bewerte nur echte, relevante Lösungsvarianten. Nutze dafür 1–7 Punkte in:
    Korrektheit, Robustheit, Wartbarkeit, Performance und Migrationsrisiko.
    Begründe jede Bewertung mit überprüfbaren Fakten.
12. Gib keine ausführliche interne Gedankenkette aus. Liefere stattdessen
    Hypothese, geprüfte Alternativen, Evidenz und Entscheidung kompakt.
13. Aktualisiere bestehende Doku, wenn sich ein dauerhafter Vertrag,
    Workflow oder Architekturpunkt geändert hat. Ergänze funktionen.md
    bei großen oder komplexen Funktionen und vermeide Duplikate.
14. Räume ausschließlich selbst erzeugte temporäre Artefakte auf. Lösche oder
    verschiebe bestehenden Code und bestehende Dokumente nur nach
    Referenzprüfung und bei eindeutigem Bezug zum Auftrag.
15. Vergleiche vor Abschluss Workdoc, Git-Diff, Implementierung und Tests.
    Entferne das Workdoc nach vollständigem Abschluss oder archiviere es nur,
    wenn es einen dauerhaften Auditwert besitzt.

Subsystem-Navigation (bei Bedarf gezielt lesen):
- Orchestrator/SOTA-Pipeline: zuerst funktionen.md + docs/01_ARCHITECTURE_DEEP_DIVE.md
- Finance-Änderungen: zuerst docs/03_FINANCE_MODULE.md
- Wellbeing-Modul-Änderungen: zuerst docs/08_WELLBEING_MODULE_OPTIMIZATION.md
- i18n-Änderungen: zuerst docs/04_I18N_GUIDE.md
- GPU/LLM-Parameter: zuerst docs/RTX4090_RYZEN9_GUIDE.md

Doku-Frische: Jede Doku trägt <!-- last-verified: YYYY-MM-DD --> im Header.
Bei Abweichung >30 Tagen: als "möglicherweise veraltet" flaggen und vor
Verwendung den aktuellen Code prüfen.

Abschlussbericht:
- bestätigte Ursache und Belege,
- geänderte Dateien,
- ausgeführte Tests mit Ergebnis,
- nicht bearbeitete Risiken,
- tabellarisch: sichtbare Änderung und zusätzlicher Nutzen für den User.