<!-- last-verified: 2026-08-20 -->
# 04 – Internationalization (i18n) Guide

> **Stand:** 2026-07-13 | **Consolidation Release 1.0**
> **Quelldokumente:** i18n/i18n_manager.py, i18n/locales/*.json
> (I18N-Bulgarische Analyse-Dokumente wurden am 2026-07-13 als reine Arbeitsdokumente entfernt)

---

## 1. i18n Architecture

### 1.1 Core Components

| Component | File | Purpose |
|-----------|------|---------|
| I18nManager | `i18n/i18n_manager.py` | Translation lookup, fallback chain |
| DE Locale | `i18n/locales/de.json` | German translations |
| EN Locale | `i18n/locales/en.json` | English translations |
| BG Locale | `i18n/locales/bg.json` | Bulgarian translations |
| Language Detector | `llm_utils/language_detector.py` | Auto-detect user language |

### 1.2 Translation Flow
```
User Input
    |
    v
[Language Detector] --> Detect language (DE/EN/BG)
    |
    v
[I18nManager] --> Load locale file
    |
    v
[Translation Lookup] --> Return translated string
    |
    v
[Fallback Chain] --> DE → EN → key string
```

---

## 2. Supported Languages

| Code | Language | Status | Completeness |
|------|----------|--------|--------------|
| de | German | ✅ Production | ~95% |
| en | English | ✅ Production | ~100% |
| bg | Bulgarian | ✅ Production | ~90% |

---

## 3. Usage

### 3.1 Basic Usage
```python
from i18n import I18nManager

i18n = I18nManager(default_locale="de")
message = i18n.translate("greeting.welcome", locale="de")
```

### 3.2 With Parameters
```python
message = i18n.translate("greeting.hello_name", params={"name": "Max"}, locale="de")
```

### 3.3 Fallback Behavior
1. Try requested locale
2. Fallback to default locale (de)
3. Fallback to English
4. Return translation key

---

## 4. Adding New Translations

### 4.1 Add Key to English
Edit `i18n/locales/en.json`:
```json
{
  "module.feature.description": "This is a new feature"
}
```

### 4.2 Add Key to Other Locales
Repeat for `de.json` and `bg.json`.

### 4.3 Add New Locale
1. Create `i18n/locales/{code}.json`
2. Copy structure from `en.json`
3. Translate all values
4. Register in I18nManager

---

## 5. Configuration

| Setting | Purpose | Default |
|---------|---------|---------|
| `DEFAULT_LOCALE` | Fallback language | "de" |
| `DETECT_LANGUAGE` | Auto-detect user lang | true |

---

*Für i18n Änderungen, dieses Dokument aktualisieren.*