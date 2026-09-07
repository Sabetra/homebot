# Workdoc: User-geführter Psychotab-Fokus und Safety-Episoden

> **Erstellt:** 2026-08-04
> **Abschluss-Ziel:** 2026-08-04
> **Status:** ABGESCHLOSSEN
> **Autor:** GitHub Copilot

## Original-Auftrag

setze es bestmöglich um

Kontext des Auftrags: Der User muss jederzeit über ein aktuell belastendes Thema sprechen können, ohne durch Therapieziele oder einen zu früh ausgelösten Krisenmodus wiederholt zu einem anderen Thema gedrängt zu werden.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Sessiongebundene Safety-Episode mit einmaligem Elevated-Check | Abschwächung des deterministischen Acute-Safety-Pfads |
| Frische Akutprüfung pro User-Turn | Klinische Diagnose oder medizinische Beratung |
| User-bestätigter/pausierbarer Zielfokus | Cloud-Dienste oder neue Modelle |
| Turnbezogenes Ziel-Relevanz-Gate | Vollständiger Neuaufbau des Treatment-Domains |
| Entfernung doppelter Zielinjektion | Finance-/KG-Änderungen |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Elevated erzeugt pro Episode höchstens einen Safety-Check | Sequenztest | ✅ |
| 2 | Neue Acute-Evidenz unterdrückt kein Cooldown | Sequenztest | ✅ |
| 3 | Niedriges Risiko beendet/beruhigt eine offene Episode | Sequenztest | ✅ |
| 4 | Frühere Risikostufe allein darf keine Eskalation begründen | Prompt-/Classifier-Test | ✅ |
| 5 | Aktuelles User-Thema hat Vorrang vor gespeicherten Zielen | Formatter-Test | ✅ |
| 6 | Nicht relevante Ziele/Interventionen werden turnbezogen weggelassen | Formatter-Test | ✅ |
| 7 | Bestätigter Fokus kann pausiert und gewechselt werden | Repository-/UI-Test | ✅ |
| 8 | Aktive Ziele erscheinen nicht doppelt im Prompt | Formatter-Test | ✅ |
| 9 | DE/EN/BG UI-Texte sind vollständig | JSON-/i18n-Test | ✅ |

## Alternativen & Entscheidung

Skala 1 (schwach) bis 7 (stark); Risiko 7 bedeutet geringes Migrationsrisiko.

| Option | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko | Entscheidung |
|--------|------------:|-----------:|------------:|------------:|-----------------:|--------------|
| Nur Prompt abschwächen | 3 | 2 | 5 | 7 | 7 | Verworfen: kein Zustandsvertrag |
| Globaler Cooldown ohne Episode | 4 | 4 | 5 | 7 | 6 | Verworfen: kann neue akute Evidenz verdecken |
| Persistente Safety-Episode plus user-geführter Fokus | 7 | 7 | 6 | 6 | 6 | Umsetzen |
| Vollständige Workflow-State-Machine-Neuentwicklung | 6 | 6 | 3 | 4 | 2 | Zu großer Scope |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | Nur `acute` setzt `AddMessageResult.is_crisis` | `SessionManagerAdapter.AddMessageResult.ok()` |
| 2 | Jedes `elevated` hängt erneut dieselbe Probe an | `ChatInputHandler.handle_psychological_chat_input()` |
| 3 | RiskClassifier erhält den vorherigen Level als Promptkontext | `TreatmentManager.process_turn()` / `RiskClassifier.assess()` |
| 4 | FocusPlanner persistiert Primärziel/Interventionen ohne Userbestätigung | `FocusPlanner.plan()` / `TreatmentRepository.upsert_focus()` |
| 5 | Ziele erscheinen als Treatment-Plan und separate Goal-Liste | `ContextFormatter.format_context_for_llm()` |
| 6 | Systemprompt verlangt bereits, zuerst das aktuelle Anliegen zu beantworten | `THERAPEUTIC_SYSTEM_PROMPT_BASE` |

## Hypothese und günstiger Test

**Hypothese:** Eine sessiongebundene Episode mit `probe_sent` unterdrückt wiederholte Elevated-Probes, ohne Acute-Routing zu beeinflussen; ein user-bestätigter Fokus plus lexikalisches Relevanz-Gate verhindert Ziel-Rücklenkung bei Themenwechsel.

**Falsifizierung:** Sequenz `elevated -> elevated -> acute` muss genau eine Probe und anschließend Acute liefern; ein themenfremder Turn darf weder Fokusinterventionen noch doppelte aktive Ziele im Prompt enthalten.

## Risiko & Impact-Matrix

| Risiko | Wahrscheinlichkeit | Auswirkung | Minderung |
|--------|-------------------|------------|-----------|
| Cooldown verdeckt neue Gefahr | Niedrig | Kritisch | Acute wird auf jedem Turn frisch geprüft und nie unterdrückt |
| Lexikalisches Gate übersieht semantischen Bezug | Mittel | Niedrig | Bestätigter Fokus bleibt als knapper Hintergrund; keine Zwangsrücklenkung |
| UI-State und DB-State driften | Niedrig | Mittel | Repository ist Owner; UI ruft nur typisierte Mutationen auf |
| Historische Daten fehlen neuen Spalten | Mittel | Mittel | Additive idempotente Migration |

## Sicherheits- & PII-Implikationen

- Safety-Episoden speichern nur Status, Turnnummern und Zeitpunkte, keine zusätzlichen Nachrichtentexte.
- Akute aktuelle Evidenz hat immer Vorrang vor Cooldown oder Fokus.
- Ziele bleiben lokal im bestehenden Treatment-Repository.

## Rollback-Strategie

Timestamped Backups unter `backups/psych_user_led_focus_<timestamp>/`; additive Tabellen/Spalten sind rückwärtskompatibel und können bei Code-Rollback bestehen bleiben.

## Änderungen und Testergebnisse

- Persistente `safety_episodes` mit Turnfenster und Aktionen `normal`, `probe`, `acute` eingeführt.
- Focus-Modi `suggested`, `confirmed`, `paused`, `dismissed` samt Repository-/Manager-Mutationen und Streamlit-Steuerung ergänzt.
- Formatter auf User-Bestätigung, aktuelle Turn-Relevanz, einmalige Zielinjektion und episodischen Risk-Kontext begrenzt.
- Risk-Prompt auf Evidenz der aktuellen Nachricht festgelegt; unsichere Klassifikationen werden deterministisch abgestuft.
- Chat-, Sync- und Async-Handler verwenden denselben lokalisierten Safety-Vertrag.
- `tests/test_context_formatter_and_insight_extractor.py`: 10 passed.
- `tests/test_crisis_prompt_threshold.py`: 16 passed.
- `tests/test_psychological_crisis_i18n.py`: 11 passed; alle Locale-JSON-Dateien valide.
- Breite Psychoregression (`test_psych*.py` plus Kontext/Safety): 71 passed.
