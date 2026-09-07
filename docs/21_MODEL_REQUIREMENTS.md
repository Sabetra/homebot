<!-- last-verified: 2026-09-05 -->
# 21 – Model Requirements & Offline Setup

> **Stand:** 2026-09-05 | **Single Source of Truth:** `models/manifest.json`
> **Tool:** `scripts/setup_models.py` (Status / Check / Fetch)
> **Prinzip:** local-first — keine impliziten Runtime-Downloads für kritische Modelle

Homebot läuft vollständig lokal. Es gibt **zwei Modell-Familien**:

1. **LLM** (GGUF, z. B. Gemma 4 12B) — über `llama-cpp-python` geladen.
2. **AUX** (Embeddings, Reranker, NLI, OCR, Docling) — Hugging Face / EasyOCR.

Beide werden lokal gespeichert und pro Start nur geladen. Das **Manifest**
(`models/manifest.json`) ist die deklarative Single Source of Truth:
pinnte Revisions, Größen, Lizenzen, Required/Optional und Cache-Strategie.
Es wird von `scripts/setup_models.py`, dem Konsistenz-Test
(`tests/test_model_manifest_consistency.py`) und dieser Doku konsumiert.

---

## Design-Prinzipien (SOTA 2026)

| Prinzip | Umsetzung |
|---------|-----------|
| **Local-first** | Kein impliziter Download im Produktivpfad. Fehlende Modelle → explizite, i18n-fähige Fehlermeldung statt stiller Degradation. |
| **Deklarativer Manifest** | `models/manifest.json` als SSoT — ein Ort für Namen, Pfade, Revisions, Lizenzen. |
| **Drift-Prävention** | `tests/test_model_manifest_consistency.py` vergleicht Manifest ↔ Code ↔ Doku und scheitert bei Abweichungen. |
| **Pinned Revisions** | Kritische HF-Modelle haben pinnte Commit-SHAs (Reproducibilität). |
| **Explicit Bootstrap** | `scripts/setup_models.py --fetch` lädt fehlende HF-Modelle — nur explizit, nie implizit. |

---

## LLM-Modelle (GGUF)

Runtime: `llama-cpp-python` via `scripts/model_loader.py`.
Default-Ort: `~/.cache/lm-studio/models/lmstudio-community`
(Override: `BOT_MODELS_DIR`). **Jede** GGUF ist ladebar (Registry scannt den
Ordner); Vision wird durch `mmproj*.gguf` im selben Ordner erkannt.

| Modell | Rolle | Größe (ca.) | Status |
|--------|-------|-------------|--------|
| Gemma 4 12B (QAT Q4_0) | Produktion (Default) | 6.5 GB (+ 0.16 GB mmproj) | empfohlen |
| Gemma 4 E4B (Q4_K_M) | Kompakt | 4.97 GB (+ 0.92 GB mmproj) | optional |
| Gemma 4 26B A4B (Q4_K_M) | MoE, hoch | 15.64 GB (+ 1.11 GB mmproj) | optional |
| Gemma 3 12B (Q4_K_M) | Legacy | ~6.5 GB | optional |
| Magistral Small 2509 (Q4_K_M) | Alternativ | ~4.5 GB | optional |

> ⚠️ **Lizenz:** Gemma-Gewichte unterliegen den *Google Gemma Terms of Use*
> (Akzeptanz erforderlich, z. B. über Hugging Face-Login oder LM Studio).
> Modellgewichte sind **kein** Teil dieses Repositories und dürfen nicht
> weiterverteilt werden. LLM-GGUFs werden **nicht** per `--fetch` geladen —
> sie sind Operator-managed (LM Studio / manuell).

---

## AUX-Modelle

| Modell | Rolle | Required | Größe (ca.) | Revision | Lizenz |
|--------|-------|:--------:|-------------|----------|--------|
| `intfloat/multilingual-e5-large` | Embeddings | ✅ | ~1.1 GB | pinned (3d7cfbda) | Apache-2.0 |
| `BAAI/bge-reranker-v2-m3` | Reranker | ✅ | ~2.3 GB | pinned (953dc6f6) | Apache-2.0 |
| `cross-encoder/nli-deberta-v3-base` | NLI-Verifikation | ○ | ~1.1 GB | pinned (6c749ce3) | MIT |
| `cross-encoder/nli-deberta-base` | NLI-Fallback | ○ | ~1.1 GB | pinned (f375a3f8) | MIT |
| `cross-encoder/nli-MiniLM2-L6-H768` | NLI-Fast-Fallback | ○ | ~0.1 GB | pinned (b95119ce) | MIT |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | Reranker-Fallback | ○ | ~0.15 GB | latest | MIT |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker-Fallback | ○ | ~0.13 GB | latest | MIT |
| `cross-encoder/ms-marco-TinyBERT-L-2-v2` | Reranker-Fallback | ○ | ~0.1 GB | latest | MIT |
| `sentence-transformers/all-MiniLM-L6-v2` | Semantic-Cache | ○ | ~0.085 GB | latest | Apache-2.0 |
| EasyOCR (CRAFT + CRNN) | OCR | ○ | ~0.2 GB | bundled | MIT / Apache-2.0 |
| Docling (`smolvlm2-500M-instruct`) | Dokument-Import | ○ | ~1.2 GB | default | Apache-2.0 |

✅ = **Required** (fehlt → Feature degradiert, `--check` exit 1).
○ = Optional (Feature-Fallback oder Operator-Opt-in).

### Optional / Opt-in Embeddings (via `RAG_EMBEDDING_MODEL`)

`intfloat/multilingual-e5-large-instruct` · `BAAI/bge-m3` ·
`BAAI/bge-large-en-v1.5` · `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` ·
`jinaai/jina-embeddings-v2-base-de` · `Alibaba-NLP/gte-multilingual-base`

---

## Cache-Orte & Env-Overrides

| Modell-Familie | Default-Ort | Override |
|----------------|-------------|----------|
| LLM GGUF | `~/.cache/lm-studio/models/lmstudio-community` | `BOT_MODELS_DIR` |
| Embeddings (ST) | `<repo>/models_cache/sentence_transformers` | `SENTENCE_TRANSFORMERS_HOME` |
| Hugging Face Hub | `~/.cache/huggingface` | `HF_HOME` |
| EasyOCR | `~/.EasyOCR` (oder `models_cache/`) | `EASYOCR_MODULE_PATH` |
| Offline-Flag | — | `APP_LOCAL_ONLY`, `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` |

Alle DBs liegen **außerhalb** des Repos (`~/.local/share/homebot_dbs`,
Override `HOMEBOT_DB_ROOT`) — siehe `utils/db_path_resolver.py`.

---

## `scripts/setup_models.py`

Inspectieren, verifizieren und bootstrappen der lokalen Modelle aus
`models/manifest.json`.

| Befehl | Zweck | Exit-Code |
|--------|-------|-----------|
| `python scripts/setup_models.py --status` | Mensch-lesbare Präsenz-Tabelle (kein Netzwerk) | `0` |
| `python scripts/setup_models.py --check` | Wie `--status`; CI-Gate | `0` = alle Required vorhanden · `1` = Required fehlt |
| `python scripts/setup_models.py --fetch` | Fehlende HF-Modelle laden (ehrt Offline-Flags) | `0` = OK · `1` = Fehlschlag/Offline-Refusal · `2` = Manifest-Fehler |

Optionen: `--manifest <pfad>` (alternatives Manifest) · `--only <model-id>`
(nur bei `--fetch`: auf ein Modell beschränken).

```powershell
# Status anzeigen (lokal, ohne Netzwerk):
python scripts/setup_models.py --status

# CI-Gate: exit 1, wenn ein Required-Modell fehlt:
python scripts/setup_models.py --check

# Fehlende AUX-Modelle einmalig beziehen (online):
python scripts/setup_models.py --fetch
```

> **`--fetch` verhält sich:**
> - **Offline** (`APP_LOCAL_ONLY`/`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`):
>   verweigert den Download (`REFUSED`, exit 1).
> - **Niemals** LLM-GGUFs oder EasyOCR-Bundled-Modelle — diese sind
>   Operator-managed und werden als `skipped` gemeldet.
> - Pinnte Revisions werden exakt geladen (Reproducibilität).

---

## Fehlende Modelle — Operator-Guide

| Symptom | Ursache | Lösung |
|---------|---------|--------|
| `RAG-Embeddings nicht verfügbar` | `multilingual-e5-large` fehlt | `python scripts/setup_models.py --fetch` (oder manuell via `huggingface_hub.snapshot_download`) |
| `Reranker nicht aktiv` (RAG-Qualität sinkt) | `BAAI/bge-reranker-v2-m3` fehlt | `python scripts/setup_models.py --fetch` |
| `NLI-Verifikation deaktiviert` | `nli-deberta-v3-base` fehlt | `python scripts/setup_models.py --fetch` (oder Feature bleibt deaktiviert — kein Crash) |
| `OCR nicht verfügbar` | EasyOCR-Modelle fehlen | `pip install easyocr` (EasyOCR lädt bei erstem OCR-Call) |
| `Docling-Import langsam/fällt zurück` | Docling-Modelle fehlen | `python scripts/setup_models.py --fetch` (sonst lädt Docling aus `docling-project/docling-models`) |

> **Wichtig:** Seit 2026-09-05 fallen fehlende Required-Modelle **nicht mehr
> still** — der Reranker und die Embeddings melden eine explizite,
> i18n-fähige Fehlermeldung mit genau diesem Hinweis.

---

## Lizenz- & Compliance-Hinweis

- **Gemma-Gewichte:** *Google Gemma Terms of Use* (Akzeptanz erforderlich).
  **Kein** Teil dieses Repositories; nicht weiterverteilen.
- **AUX-Modelle:** überwiegend Apache-2.0 / MIT (per HF-Modellkarte).
- **Projekt-Lizenz:** AGPL-3.0 — siehe [19_LICENSES_AND_COMPLIANCE.md](19_LICENSES_AND_COMPLIANCE.md).
- **Lizenz-Gate:** Nach jeder Dependency-/Modell-Änderung:
  `python scripts/generate_licenses.py` + `python scripts/check_licenses.py --strict`.

---

## Modell hinzufügen / aktualisieren

1. `models/manifest.json` anpassen (die **SSoT**).
2. `python scripts/setup_models.py --status` verifizieren.
3. `tests/test_model_manifest_consistency.py` ausführen (drift-free).
4. `python scripts/generate_licenses.py` + `python scripts/check_licenses.py --strict`.
5. Diese Doku + `README.md` bei Bedarf aktualisieren.

> **Niemals** Modell-Namen, Pfade oder Cache-Strategien in Code, Doku und
> Manifest *unabhängig* voneinander ändern — der Konsistenz-Test scheitert,
> wenn sie divergieren.

---

## Verwandte Dokumente

| Doku | Bezug |
|------|-------|
| [00_CONTEXT_MASTER.md](00_CONTEXT_MASTER.md) | Master-Kontext (immer zuerst) |
| [19_LICENSES_AND_COMPLIANCE.md](19_LICENSES_AND_COMPLIANCE.md) | Lizenzen & Compliance |
| [RTX4090_RYZEN9_GUIDE.md](RTX4090_RYZEN9_GUIDE.md) | GPU-/LLM-Parameter |
| [../models_cache/README.md](../models_cache/README.md) | Cache-Verzeichnis (AUX) |
| [../funktionen.md](../funktionen.md) | Kompendium großer Funktionen |
