# Krisen-False-Positive Workdoc — ABGESCHLOSSEN (2026-07-31)

## Ursprünglicher Auftrag
Wiederholte, unangebrachte Krisenhinweise in psychologischen Chatsessions beheben.
User beschreibt Beziehungskonflikte und Kindheitserfahrungen → System zeigt 4× Krisen-Banner obwohl keine Krise vorliegt.

## Scope
- Krisen-Trigger-Logik: Nur `acute` löst deterministisches Krisen-Banner aus.
- `elevated`: Probing-Zweig (LLM fragt nach, eskaliert nicht).
- Fail-safe bei Klassifikator-Ausfall: `LOW` statt `ELEVATED`.
- Last-Resort Fallback in `detect_crisis`: `False` statt `True`.

## Nicht-Scope
- Emotional Analyzer thresholds (nicht geändert).
- i18n Krisen-Strings (nur referenziert).
- Risk-Prompt-Inhalte (bereits in P1 korrigiert).

## Verifizierte Fakten
| # | Fakt | Beleg |
|---|------|-------|
| 1 | `session_manager_adapter` mappte `acute` **und** `elevated` auf `is_crisis=True` | Zeile 205-208 (vor Fix) |
| 2 | `risk_classifier` fail-safe bei `parsed is None` → `ELEVATED` | Zeile 104-115 (vor Fix) |
| 3 | `message_handler.detect_crisis` last-resort → `return True` | Zeile 261 (vor Fix) |
| 4 | `chat_input_handler` hatte keinen `elevated`-Probing-Zweig | Zeile 138-146 (vor Fix) |

## Änderungen
| Datei | Änderung | Zeilen |
|-------|----------|--------|
| `psychological_session/adapters/session_manager_adapter.py` | `is_crisis` nur bei `acute`; `elevated` → `is_crisis=False` | 205-212 |
| `psychological_session/handlers/chat_input_handler.py` | Probing-Zweig für `elevated` eingefügt | 138-151 |
| `psychological_support/treatment/risk_classifier.py` | Fail-safe `ELEVATED` → `LOW` | 104-118 |
| `psychological_session/handlers/message_handler.py` | Last-resort `return True` → `return False` | 257-261 |

## Tests
| Test | Ergebnis |
|------|----------|
| `test_crisis_prompt_threshold.py` (9 tests) | ✅ 9/9 passed |
| `test_psychological_db_dedup.py` (2 tests) | ✅ 2/2 passed |
| `test_psychological_db_maybe_decrypt.py` (10 tests) | ✅ 10/10 passed |
| `test_role_alternation_psych_chat.py` (8 tests) | ✅ 8/8 passed |
| **Gesamt** | **✅ 27/27 passed** |

## Status
✅ ABGESCHLOSSEN — Bereit zur Archivierung.