# Workdoc: [Kurztitel]

> **Erstellt:** YYYY-MM-DD HH:MM
> **Abschluss-Ziel:** YYYY-MM-DD (falls anwendbar)
> **Status:** OFFEN | IN_ARBEIT | ABGESCHLOSSEN
> **Autor:** [Name/Bot]
> **Reviewer:** [Name, falls zutreffend]

---

## Original-Auftrag
<!-- Vollständiger, unveränderter Auftragstext. Nicht kürzen, nicht paraphrasieren. -->

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
|          |                |

## Definition of Done
<!-- Falsifizierbare Abnahmekriterien. Jedes Kriterium muss über Test, Inspektion oder Benchmark überprüfbar sein. -->

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 |           |             | ☐      |

## Alternativen & Entscheidung
<!-- Mindestens 2 plausible Optionen bewerten. Quelle: Google Design Doc + AWS RFC-Pattern. -->

| # | Option | Pro | Contra | Korrektheit | Robustheit | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|--------|--------------|
| A |        |     |        |             |            |             |        |              |
| B |        |     |        |             |            |             |        |              |

> **Auswahl:** [Option X] — Begründung: [konkret, belegbar]

## Abhängigkeiten & Stakeholder
<!-- Welche Module, Personen oder externe Dienste sind betroffen? Quelle: Google Design Doc. -->

| # | Abhängigkeit / Stakeholder | Art | Impact | Status |
|---|---------------------------|-----|--------|--------|
| 1 |                           |     |        |        |

## Verifizierte Fakten

| # | Fakt | Beleg (Datei:Zeile / Symbol) |
|---|------|------------------------------|

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|

## Offene Fragen
<!-- Klare Zuordnung an Owner mit Deadline. Quelle: Meta WDD. -->

| # | Frage | Owner | Deadline | Status |
|---|-------|-------|----------|--------|

## Risiko & Impact-Matrix
<!-- Wahrscheinlichkeit × Auswirkung. Quelle: Google Design Doc + Linux Kernel RFC. -->

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme | Status |
|---|--------|-------------------|------------|-------------------|--------|

## Sicherheits- & PII-Implikationen
<!-- Bei Berührung von pii_protection/, Finance-Daten oder User-Inhalten ausfüllen. -->

| # | Aspekt | Implikation | Gegenmaßnahme |
|---|--------|-------------|---------------|

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|

## Rollback-Strategie
<!-- Wie wird bei Fehlschlag zurückgesetzt? Quelle: AWS RFC. -->

| Schritt | Aktion | Befehl / Referenz |
|---------|--------|-------------------|
| 1        |        |                   |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|

## Testergebnisse
<!-- Zusammenfassung aller relevanten Testläufe. -->

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|

## Token-Budget & Kosten (optional, bei LLM-intensiven Tasks)

| Komponente | Geschätzte Tokens | Budget | Status |
|------------|-------------------|--------|--------|

---

> **Regeln:**
> - Keine ganzen Quelldateien oder Dokumentationen kopieren.
> - Hypothesen klar von bestätigten Befunden trennen.
> - Bei Abschluss: Workdoc löschen oder bei Auditwert nach `docs_archive/` verschieben.
> - Sektionen, die nicht anwendbar sind, können entfernt werden (nicht auskommentieren).