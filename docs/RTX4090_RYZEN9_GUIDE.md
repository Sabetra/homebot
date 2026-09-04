<!-- last-verified: 2026-08-27 -->

🔥 RTX 4090 + RYZEN 9 5950X PERFORMANCE GUIDE
============================================

Ihre Hardware:
- GPU 1 (LLM): NVIDIA GeForce RTX 4090 (24.0GB) — CUDA-Runtime `cuda:0`, NVML-Index 1
- GPU 2 (AUX): NVIDIA GeForce RTX 3060 Ti (8.0GB) — CUDA-Runtime `cuda:1`, NVML-Index 0
- CPU: 16 Kerne, 32 Threads
- RAM: 63.9GB

Optimale Einstellungen (allgemein, RAG/Embedding-lastige Workloads):
{
  "gpu_batch_size": 1024,
  "max_sequence_length": 8192,
  "use_mixed_precision": true,
  "gpu_memory_fraction": 0.95,
  "enable_cudnn_benchmark": true,
  "tensor_parallel_size": 1,
  "gpu_layers": -1,
  "num_workers": 24,
  "cpu_batch_size": 128,
  "parallel_chunk_processing": true,
  "max_parallel_extractions": 16,
  "cpu_memory_limit_gb": 48,
  "enable_cpu_parallel": true,
  "omp_num_threads": 16,
  "faiss_index": "cpu-hnsw",
  "embedding_batch_size": 512,
  "chunk_size": 2048,
  "overlap_size": 256,
  "enable_memory_mapping": true,
  "prefetch_factor": 8,
  "pin_memory": true
}

Verwendung:
1. Führen Sie diese Optimierungen vor RAG-Operations aus
2. Nutzen Sie die erstellte rag_rtx4090_config.json
3. Importieren Sie rtx4090_rag_patch.py in Ihren Code
4. Überwachen Sie GPU/CPU-Auslastung für Fine-Tuning

Performance-Tipps:
- Nutzen Sie Batch-Größen von 512-1024 für Embeddings
- Aktivieren Sie Mixed Precision (FP16) für GPU
- Verwenden Sie 16-24 parallele Worker für CPU-Tasks
- Setzen Sie Memory-Limits auf 40-48GB für große PDFs
- FAISS-HNSW Vector-Suche läuft auf der CPU (by design — FAISS hat keine HNSW-GPU-Variante)

Wichtiger Hinweis fuer llama.cpp Langkontext-Inferenz (24k/32k):
- Für die LLM-Prefill-Phase keine starren Maximalwerte bei `n_batch`/`n_ubatch`
  erzwingen; zu aggressive Werte (z.B. `n_batch=8192`) können ggml-cuda
  Kernelfehler auslösen.
- Empfohlen: adaptive Grenzen anhand `n_ctx` und VRAM (im Code umgesetzt).
- Nur für kontrollierte Benchmarks per `LLM_N_BATCH` / `LLM_N_UBATCH`
  überschreiben.
- Der konkrete lokale Benchmark-Workflow ist in
  `scripts/benchmark_llm_gpu_tuning.py` implementiert (Docstring beschreibt
  Nutzung; Overrides via ENV `LLM_N_BATCH` / `LLM_N_UBATCH`).

Verifiziertes Single-User-LLM-Profil fuer diesen Rechner:
- `n_batch=3072`
- `n_ubatch=2048`
- `n_threads=12`
- `n_threads_batch=12`
- Ergebnis: schneller als die konservative 2048/2048-Baseline im
  alternierenden Canary-Test, stabil ueber mehrere Wiederholungen.

Hinweis zur Durchgaengigkeit:
- Die obenstehenden LLM-Werte gelten spezifisch fuer llama.cpp-Long-Context-
  Inferenz im produktiven Single-User-Pfad.
- Die JSON-Einstellungen am Anfang dieses Dokuments bleiben als generische
  System-/RAG-Startwerte gueltig und sind nicht als harte LLM-Prefill-Defaults
  zu verstehen.

Build-Integritaet (wichtig fuer ggml-cuda Fehler):
- Der aktive `llama-cpp-python` Build laeuft in `venv_bot_20260802`
  (Produktiv-venv; `venv_mistral_gguf` bleibt nur Rollback).
  Stand 2026-08-27: `llama-cpp-python 0.3.35` (erste Version mit `qwen35`).
  CUDA deckt `ARCHS = ...890...` ab und passt damit zur RTX 4090 (SM 8.9).
- Der Ryzen 9 5950X (Zen 3) unterstuetzt kein AVX-512. Das offizielle
  `0.3.35-cu124`-Windows-Wheel meldete dennoch `AVX512 = 1` und verursachte
  beim `llama_init_from_model` reproduzierbar `0xc000001d` (Illegal Instruction),
  auch CPU-only und bei `n_ctx=512`. Der aktive Build nutzt deshalb das
  versionsgleiche offizielle CPU-Backend ohne AVX-512 zusammen mit dem
  CUDA-Backend des cu124-Wheels. Verifiziert: 26B-QAT-Kontext-Init auf RTX 4090.
- Bei Source-Rebuilds von `llama.cpp` muss die CUDA-Architektur explizit zur
  Ziel-GPU passen und AVX-512 explizit deaktiviert werden:
  `-DGGML_CUDA=ON -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF`
  `-DGGML_AVX512_VNNI=OFF -DCMAKE_CUDA_ARCHITECTURES=89`.
- Rollback: `monitoring/backup_llamacpp.ps1` (0.3.20-Dist-Info-Backup).

---

## Dual-GPU-Rollen (2026-08-25)

| Rolle | GPU | Wo laeuft es |
|-------|-----|--------------|
| LLM | RTX 4090 (24 GB) | Gemma4 12B via llama-cpp-python (Voll-Offload, `n_gpu_layers=-1`) |
| AUX | RTX 3060 Ti (8 GB) | Cross-Encoder-Reranker (ONNX-GPU), Embeddings (SentenceTransformer), NLI (onnx), OCR (EasyOCR/Torch), Docling (Torch) |

- **Single Source of Truth:** `utils/gpu_devices.py` (`get_placement()`). Konsumenten nutzen
  `aux_cuda` (ONNX `device_id`), `aux_device_string` (`"cuda:1"`) bzw. CPU-Fallback —
  keine hartkodierten Indizes.
- **CUDA-Runtime-Index != NVML-Index** (auf diesem System vertauscht, per GPU-UUID gemappt):
  `nvidia-smi` zeigt NVML 0 = 3060 Ti, NVML 1 = 4090.
- **ONNX-GPU-Reranking** braucht `onnxruntime-gpu` im venv (venv_bot_20260802); ohne GPU-EP
  faellt der Reranker auf CPU zurueck (funktioniert, langsamer).
- **Monitoring:** `utils/vram_monitor.py::get_all_gpu_snapshots()` und der Performance-Tab zeigen
  beide GPUs mit Rollenbeschriftung (LLM/AUX); pynvml primar, `nvidia-smi --query-gpu`-CLI
  als Fallback. Erwartetes `nvidia-smi`-Bild bei Leerlauf nach App-Start:
  GPU0 (NVML 0, 3060 Ti) mit AUX-Modellen, GPU1 (NVML 1, 4090) mit dem LLM.
- **LLM-Profil (4090)** bleibt unveraendert: `n_batch=3072`, `n_ubatch=2048`, threads=12.
- **split_mode=NONE ist Pflicht beim LLM-Load:** Der llama-cpp-python-Default
  `LLAMA_SPLIT_MODE_LAYER` verteilt Layer/Compute-Buffer auf BEIDE GPUs;
  `main_gpu` allein verhindert das nicht. Im App-Prozess kollidierte der Split
  mit den AUX-Modellen (ONNX-Reranker, 4 GB) auf der 3060 Ti — Folge:
  `sched_reserve: compute buffer allocation failed` → "Failed to create
  llama_context" (nur im App-Prozess, standalone lud dasselbe Modell).
  Fix in `scripts/model_loader.py`: `split_mode=llama_cpp.LLAMA_SPLIT_MODE_NONE`.
  Verifiziert mit Qwen3.8-27B-Q4_K_M + BF16-MMPROJ, n_ctx=16384, n_batch=3072,
  bei 5 GB belegter AUX-GPU.
- **Validierung:** `python -m utils.gpu_devices` + `python scripts/validate_gpu_placement.py`.
- **WICHTIG — LM Studio ist das Agent-Backend (nicht fremder VRAM-Verbraucher):**
  `LM Studio.exe` + `llama-server.exe` betreiben das lokale LLM des Cline-Agents und teilen
  sich die VRAM beider GPUs. Niemals per `Stop-Process`/Kill beenden — das trennt den
  laufenden Agent ab. Fuer eine saubere Runtime-Validierung der App (LLM-Load auf der 4090):
  Agent-Session vorher beenden und LM Studio in der GUI schliessen.
- **FAISS-HNSW laeuft auf der CPU — Design, kein Bug:** Das venv haelt `faiss-cpu`
  (1.14.3), und FAISS liefert fuer den hier eingesetzten HNSW-Index-Typ keine
  GPU-Variante. Index-Build und -Suche sind CPU-basiert; die Embedding-Generation
  laeuft separat auf der AUX-GPU (3060 Ti). Erklaerende Logs:
  `agent/faiss_index_manager.py`, `agent/unified_rag_store.py`.
- **VRAM-Telemetrie beim LLM-Load:** `scripts/model_loader.py` vergleicht vor
  `Llama(...)` den freien VRAM mit einer Schaetzung aus Modell, GGUF-KV-Metadaten
  und Compute-Puffer. Eine Unterschreitung erzeugt eine Warnung, blockiert den
  realen Ladeversuch aber nicht; llama.cpp entscheidet ueber die tatsaechliche
  Speicherverteilung und meldet einen echten Kapazitaetsfehler selbst.
- **Overrides:** `BOT_LLM_CUDA_DEVICE` / `BOT_AUX_CUDA_DEVICE` (Integer = CUDA-Runtime-Index).
