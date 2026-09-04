# Workdoc: Selektiver AUX-GPU-Modell-Lifecycle

> **Erstellt:** 2026-08-28
> **Abschluss-Ziel:** 2026-08-28
> **Status:** ABGESCHLOSSEN
> **Autor:** Cline (Agent)

---

## Original-Auftrag
Selektives AUX-GPU-Lifecycle-Management hinzufügen: kalte OCR/Docling-Modelle nach Import freigeben, während heiße Query-Path-Modelle resident bleiben, und die Änderung dokumentieren.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Zentrale Freigabe kalter AUX-Modelle (Docling, EasyOCR) | Entladung heißer Query-Path-Modelle (Reranker/NLI/Embeddings) |
| WeakSet-Registries + idempotentes cleanup() (OCR, VisionOCR, Docling) | VRAM-Monitoring-UI-Änderungen |
| Post-PDF-Import-Release-Hook (defensiv) | LLM-Load / split_mode-Logik |
| Latenter `VisionOCRProcessor.cleanup()`-Crash (uninitialisiertes `vision_model`) fixen | Vision-Modell-Entladung (bewusst NICHT — geteilter ModelLoader-Slot; = aktuell geladenes LLM) |
| Verfrühte `is_available`-Gates entfernen (Reranker-Laden nicht dauerhaft blockieren) | Cloud-LLM / API-Integration |
| Doku: funktionen.md §V, 00_CONTEXT_MASTER.md, AGENTS.md | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Kalte Modelle (Docling, EasyOCR) nach Import freigegeben | Smoke-Test `monitoring/_smoke_aux_release.py` | ☑ PASS |
| 2 | Heiße Query-Path-Modelle bleiben resident | Code-Inspektion (`release_cold_aux_models` berührt sie nicht) | ☑ |
| 3 | `cleanup()` idempotent; kein VisionOCR `vision_model`-Crash | Smoke-Test + `py_compile` | ☑ PASS |
| 4 | Lazy-Reload bleibt erreichbar (Instanz bleibt in Registry) | Smoke-Test | ☑ PASS |
| 5 | Release-Fehler blockiert keinen erfolgreichen Import | Defensive `try/except` im Hook | ☑ |
| 6 | Doku aktuell (funktionen.md §V, 00_CONTEXT_MASTER, AGENTS.md) | Re-Read der Sektionen | ☑ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Performance | Risiko | Entscheidung |
|---|--------|-----|--------|-------------|--------|--------------|
| A | Selektives Release (nur kalte Modelle) | Heiße Query-Path-Modelle bleiben resident; VRAM-Headroom für LLM | etwas komplexer | Hoch (keine wiederholten Reranker-Loads) | Niedrig | **Gewählt** |
| B | Blanket-Release (alle AUX-Modelle) | maximaler VRAM-Freigabe; einfach | verdrängt Reranker/NLI/Embeddings → hohe Reload-Latenz im Query-Path | Geringer | Mittel | Abgelehnt |
| C | Kein Release (alles resident) | keine Reload-Latenz | kalte OCR/Docling-Modelle halten VRAM auf der 3060 Ti | Geringer (VRAM-Frust) | Mittel | Abgelehnt |

> **Auswahl:** Option A — Begründung: selektives Release gibt den CUDA-Cache kalter Modelle an das OS zurück (VRAM-Headroom auf der 3060 Ti für den LLM-Query-Path), während heiße Query-Path-Modelle (Reranker/NLI/Embeddings) resident bleiben, um wiederholte Reload-Latenzen zu vermeiden. Lazy-Reload hält die kalten Modelle bei Bedarf verfügbar (kein Feature-Verlust).
## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | OCR/VisionOCR/multimodal-Konstruktoren sind lazy (kein Eager-Load) | `agent/ocr_processor.py`, `agent/vision_ocr_processor.py`, `agent/multimodal_integration.py` |
| 2 | `release_cold_aux_models()` entlädt Docling + alle registrierten OCR-Instanzen, danach `gc.collect()` + `torch.cuda.empty_cache()` | `utils/aux_model_release.py` |
| 3 | Post-Import-Hook im `finally`-Block (reason="pdf_import") | `ui_tabs/chat_tab.py` |
| 4 | `reranker.py`: `_load_failed`-Cache verhindert wiederholte fehlgeschlagene Lazy-Loads | `agent/reranker.py` |
| 5 | Vision-Modell (= aktuell geladenes LLM, Produktion: Gemma 4 12B) lebt im geteilten `ModelLoader`-Slot (Haupt-LLM-Schutz) → NICHT entladen | `agent/multimodal_integration.py` |
| 6 | Vision-Fallback = `DEFAULT_MODEL` (= Gemma 4 12B, multimodal via mmproj), NICHT Magistral; nur wenn aktives LLM keine Vision hat — lädt in dieselbe geteilte Slot (kein 2. großes Modell) | `agent/vision_ocr_processor.py::_ensure_vision_model()`, `scripts/model_loader.py` (DEFAULT_MODEL, `is_multimodal=bool(mmproj)`) |
| 7 | `model_used`-Labels sind rein deskriptiv (`vision-llm` / `easyocr` / `pymupdf4llm` + `-failed`); kein Code parsiert/vergleicht sie; kein `magistral-vision`-String im Code | Code-Scan (os.walk, .py) |
| 8 | Stale „Magistral Vision"-Docstrings in Vision-Pfad behoben (`multimodal_pdf_processor.py`, `unified_rag_store.py`) | `py_compile` EXITCODE=0 |
| 9 | Alle 9 .py-Dateien kompilieren fehlerfrei | `py_compile` EXITCODE=0 |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | Release gibt messbar VRAM auf der 3060 Ti frei | Offen | End-to-End-PDF-Import mit `nvidia-smi` Vorher/Nachher (LM Studio vorher schließen) |

## Offene Fragen

| # | Frage | Owner | Status |
|---|-------|-------|--------|
| 1 | `monitoring/_smoke_aux_release.py` behalten, nach `tests/` migrieren oder entfernen? | User | offen |
| 2 | Live-VRAM-Vorher/Nachher-Check durchführen (LM Studio schließen)? | User | offen |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `utils/aux_model_release.py` (neu) | `release_cold_aux_models()` + zentrale Freigabe | py_compile OK · Smoke-Test PASS |
| 2 | `agent/ocr_processor.py` | WeakSet-Registry `_active_ocr_processors` + idempotentes `cleanup()` | py_compile OK · Smoke-Test PASS |
| 3 | `agent/vision_ocr_processor.py` | WeakSet-Registry `_active_vision_ocr_processors` + `cleanup()` (vision_model-Crash-Fix) | py_compile OK · Smoke-Test PASS |
| 4 | `utils/docling_processor.py` | idempotentes `cleanup()`, `get_instance()` ohne Eager-Load | py_compile OK |
| 5 | `ui_tabs/chat_tab.py` | Post-Import-Hook im `finally`-Block | py_compile OK |
| 6 | `agent/reranker.py` | `_load_failed`-Cache | py_compile OK |
| 7 | `agent/rag_pipeline.py` | verfrühte `is_available`-Gates entfernt | py_compile OK |
| 8 | `agent/rag_store/core/search.py` | verfrühte `is_available`-Gates entfernt | py_compile OK |
| 9 | `monitoring/_smoke_aux_release.py` (neu) | temporärer Smoke-Test | PASS |
| 10 | `funktionen.md` | §V + Header/`last-verified` | Re-Read OK |
| 11 | `docs/00_CONTEXT_MASTER.md` | GPU-Sektion-Bullet + Changelog + `last-verified` | Re-Read OK |
| 12 | `AGENTS.md` | Dual-GPU-Bullet (AUX-Lifecycle) | Re-Read OK |
| 13 | `agent/multimodal_pdf_processor.py` | Stale „Magistral Vision"-Docstrings → „Vision-LLM" (Zeile 10, 161) | py_compile OK |
| 14 | `agent/unified_rag_store.py` | Stale „Magistral Vision"-Docstrings → „Vision-LLM" (Zeile 2758, 2979) | py_compile OK |
| 15 | `agent/vision_ocr_processor.py` | Fallback-Doku/Kommentare: `DEFAULT_MODEL` (Gemma 4 12B) statt Magistral; `model_used`-Labels deskriptiv | py_compile OK · Smoke-Test PASS |

## Rollback-Strategie

| Schritt | Aktion | Referenz |
|---------|--------|----------|
| 1 | Doku-Stände aus Backups zurückkopieren | `%USERPROFILE%ot6_backups\funktionen.md.20260828_153200.bak`, `AGENTS.md.20260828_153200.bak`, `00_CONTEXT_MASTER.md.20260828_153200.bak` |
| 2 | Code-Änderungen über Git zurückrollen | `git -C <PROJEKT_ROOT> log --oneline -- <Datei>`; `git -C <PROJEKT_ROOT> checkout <sha> -- <Datei>` |
| 3 | Smoke-Test entfernen (falls neu) | `Remove-Item monitoring/_smoke_aux_release.py` |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `py_compile` (9 .py-Dateien) | EXITCODE=0 | 2026-08-28 |
| 2 | `monitoring/_smoke_aux_release.py` | SMOKE_TEST_OK | 2026-08-28 |
| 3 | Reranker/Docling/Quality | 34/34 PASS | 2026-08-28 |
| 4 | Quality/Dashboard/Release-gate | 15/15 PASS | 2026-08-28 |
| 5 | Gesamte relevante Test-Suite | 49/49 PASS | 2026-08-28 |

## Offene Risiken

| # | Risiko | Schweregrad | Maßnahme |
|---|--------|-------------|----------|
| 1 | Bei sehr häufigen PDF-Importen dominiert die Lazy-Reload-Latenz (EasyOCR ~1–3 s, Docling ~5–15 s) | gering | Frequenz beobachten; ggf. Release-Intervall anpassen |
| 2 | Live-VRAM-Freigabe noch nicht numerisch belegt (LM Studio hält VRAM) | mittel | End-to-End-Check mit LM Studio geschlossen durchführen |

