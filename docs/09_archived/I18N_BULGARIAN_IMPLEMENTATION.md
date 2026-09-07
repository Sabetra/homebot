# I18n Bulgarische Sprachunterstützung – Implementierungsdokumentation

## User-Prompt (Original)
> "kannst Du bitte prüfen, was alles geändert werden muss, wenn der Bot und die Gui nicht nur auf Deutsch zur Verfügung stehen sollen, sondern auch auf Bulgarisch."

## Status: ⚠️ TEILWEISE UMGESETZT (Audit+Implementierung 2026-07-12)

Die Kernbausteine (i18n-Modul, Locale-Dateien, Language-Detector-Erweiterung) sind vorhanden,
die End-to-End-Integration ist deutlich vorangeschritten, aber in Teilbereichen noch nicht abgeschlossen.

---

## Zusammenfassung der Änderungen

### 1. Neue Dateien erstellt

| Datei | Zweck |
|-------|--------|
| `i18n/__init__.py` | Package-Init für i18n-Modul |
| `i18n/i18n_manager.py` | Zentrales I18n-Management (Laden, Übersetzen, Sprachwechsel) |
| `i18n/locale_negotiator.py` | Deterministische Locale-Negotiation (Override > Session > Detect > Fallback) |
| `i18n/locales/de.json` | Deutsche Übersetzungen (komplett) |
| `i18n/locales/bg.json` | Bulgarische Übersetzungen (komplett) |
| `i18n/locales/en.json` | Englische Übersetzungen (komplett) |
| `llm_utils/language_detector.py` | Automatische Spracherkennung (Deutsch, Bulgarisch, Englisch) |

### 2._EXISTIERENDE_ Dateien zum Anpassen

Die folgenden Dateien müssen von den Entwicklern manuell angepasst werden, um i18n zu nutzen:

- `enhanced_streamlit_bot.py` – **teilweise umgesetzt** (Sidebar, Tabs, zentrale Fehlermeldungen lokalisiert + Sprachmodus)
- `agent/orchestrator.py` – **teilweise umgesetzt** (Planner-Sprachdirektive nach aktiver Locale)
- `finance/tab.py` – **teilweise umgesetzt** (Haupt-UI + erweiterte Teilflows lokalisiert: Kategorisierung/Budgets/Transfers/Analytics)
- `psychological_session/` – **teilweise umgesetzt** (therapeutischer Prompt mit aktiver Locale-Direktive)
- `kg_dashboard.py` – **teilweise umgesetzt** (Haupt-UI + Statistik/Top-Entities-Detailpfade lokalisiert)

### 3. Architektur-Übersicht

```
i18n/
  __init__.py           # Package init
  i18n_manager.py       # Zentrales Management
  locales/
    de.json             # Deutsch
    bg.json             # Bulgarisch
    en.json             # Englisch

llm_utils/
  language_detector.py  # Automatische Spracherkennung
```

### 4. Nutzung

```python
from i18n import I18nManager

# Initialisierung
i18n = I18nManager()

# Übersetzung
title = i18n.t("gui.title")
i18n.set_language("bg")
title_bg = i18n.t("gui.title")
```

Audit-Hinweis:
- `I18nManager(default_language=...)` ist in der aktuellen Implementierung nicht verfügbar.
- `detect_and_switch(...)` ist in der aktuellen Implementierung nicht vorhanden.

### 5. Bulgarische Sprache – technische Details

- **Sprachcode**: `bg` (ISO 639-1)
- **Zeichensatz**: UTF-8 mit kyrillischen Zeichen
- **Erkennung**: Kombination aus Sprachwörterbuch und kyrillischem Zeichenerkennung
- **System Prompts**: Werden automatisch in die erkannte Sprache übersetzt

### 6. Test-Ergebnis

```
python -c "
from i18n import I18nManager
i18n = I18nManager()
print('DE:', i18n.t('gui.title'))
i18n.set_language('bg')
print('BG:', i18n.t('gui.title'))
"
```

Audit-Hinweis:
- Der zuvor dokumentierte Test mit `default_language` und `detect_and_switch` ist im Ist-Stand nicht ausführbar.

### 7. Nächste Schritte für Entwickler

1. Restlokalisierung in `finance/tab.py` (tiefe Teilflows, DataFrame-/Form-Spaltenlabels)
2. Restlokalisierung in `kg_dashboard.py` (Top-Entities/Detailbereiche)
3. Psychologie-UI-/Servicetexte außerhalb des Response-Generators auf `i18n.t(...)` umstellen
4. Optional: zentrale Prompt-Registry als Single Source of Truth einführen

---

## SOTA-Bewertung der Implementierung (Audit 2026-07-11)

| Kriterium | Bewertung | Begründung |
|-----------|-----------|------------|
| Vollständigkeit | ⭐⭐⭐ | Kernmodule vorhanden, produktive Integration in Hauptpfade fehlt |
| Wartbarkeit | ⭐⭐⭐⭐⭐ | Gute JSON-Basis, aber API-/Dokudrift und fehlende zentrale Prompt-Registry |
| Performance | ⭐⭐⭐⭐⭐ | Leichtgewichtig, geringe Laufzeitkosten für String-Lookups |
| Spracherkennung | ⭐⭐⭐⭐ | BG im Detector ergänzt, aber noch keine stabile Locale-Negotiation im End-to-End-Flow |
| Durchgängigkeit | ⭐⭐ | In GUI/Orchestrierung/Psychologie/Finance/KG noch nicht systematisch verdrahtet |
| Testbarkeit | ⭐⭐⭐ | Basis testbar, aber es fehlen belastbare Integrations- und Regressionstests |

## Audit-Feststellungen (Code-Realität)

- i18n-Dateien sind vorhanden (`i18n/`, `i18n/locales/*.json`) und um `locale_negotiator.py` erweitert.
- Implementiert:
  - `enhanced_streamlit_bot.py`: Sprachmodus (Auto/DE/BG/EN), Session-Locale-Negotiation, lokalisierte Sidebar/Tabs/Kernfehlertexte.
  - `agent/orchestrator.py`: Planner-Systemprompt bekommt aktive Sprachdirektive.
  - `agent/response_builder.py`: Summarizer/Verifier-Systemprompts bekommen aktive Sprachdirektive.
  - `psychological_session/handlers/response_generator.py`: therapeutische Prompts bekommen aktive Sprachdirektive.
  - `kg_dashboard.py`: Typwarnungen (Optional/Edge-Daten) ursächlich bereinigt; keine offenen Diagnostikfehler in der Datei.
- Noch offen:
  - `finance/tab.py` (Resttexte in tiefen Teilflows, DataFrame-/Form-Spaltenlabels)
  - `kg_dashboard.py` (Resttexte in nicht-kritischen Randbereichen)
  - Weitere psychologische UI-/Service-Texte außerhalb des Response-Generators

---

## Dateien-Index

### Erstellt
- [x] `i18n/__init__.py`
- [x] `i18n/i18n_manager.py`
- [x] `i18n/locale_negotiator.py`
- [x] `i18n/locales/de.json`
- [x] `i18n/locales/bg.json`
- [x] `i18n/locales/en.json`
- [x] `llm_utils/language_detector.py`

### Zum Anpassen (manuell)
- [x] `enhanced_streamlit_bot.py` (Phase 1 Integration)
- [x] `agent/orchestrator.py` (Prompt-Language-Direktive)
- [x] `finance/tab.py` (Phase 1 Kernlokalisierung)
- [ ] `psychological_session/` Module (restliche UI-/Service-Texte offen)
- [x] `kg_dashboard.py` (Phase 1 Kernlokalisierung)

---

_Dokument erstellt am 11.07.2026, aktualisiert am 12.07.2026 (Implementierungsfortschritt)._ 