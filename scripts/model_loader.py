import os
import json
import logging
import threading
import gc
import inspect
import struct
import torch
import time
import llama_cpp
from datetime import datetime
from collections import OrderedDict
from typing import Optional, Callable, Dict, Any, Generator, List
from llama_cpp import Llama, LlamaCache, StoppingCriteriaList
from llama_cpp.llama_chat_format import Llava15ChatHandler

# ── Circuit Breaker (SOTA: Production Resilience, Nygard 2007) ──
# tenacity provides proper exponential backoff with jitter, circuit-breaker
# semantics, and configurable retry predicates -- no hand-rolled workarounds.
try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential_jitter,
        retry_if_exception_type,
        before_sleep_log,
    )
    _TENACITY_AVAILABLE = True
except ImportError:
    _TENACITY_AVAILABLE = False
    # Stubs so Pylance knows names are always defined (never actually called)
    def retry(*a: Any, **kw: Any) -> Any: ...  # type: ignore[no-redef]
    def stop_after_attempt(*a: Any, **kw: Any) -> Any: ...  # type: ignore[no-redef]
    def wait_exponential_jitter(*a: Any, **kw: Any) -> Any: ...  # type: ignore[no-redef]
    def retry_if_exception_type(*a: Any, **kw: Any) -> Any: ...  # type: ignore[no-redef]
    def before_sleep_log(*a: Any, **kw: Any) -> Any: ...  # type: ignore[no-redef]

# Logging einrichten
# WICHTIG: logging.basicConfig() wirkt nur beim ERSTEN Aufruf im Prozess.
# Da enhanced_streamlit_bot.py vorher basicConfig(level=INFO) ohne filename aufruft,
# wird der FileHandler hier NICHT gesetzt. Daher expliziter FileHandler.
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# FileHandler nur hinzufügen, wenn noch keiner existiert (verhindert Duplikate bei Reloads)
if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', '').endswith('model_loader.log') for h in logger.handlers):
    try:
        # Absoluter Logpfad in monitoring/ (CWD-unabhängig): Der frühere
        # relative Pfad erzeugte model_loader.log im jeweiligen Arbeitsver-
        # zeichnis statt im Repo (Befund 2026-08-20, root-cleanup-Commit).
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'monitoring')
        os.makedirs(_log_dir, exist_ok=True)
        _log_path = os.path.join(_log_dir, 'model_loader.log')
        _file_handler = logging.FileHandler(_log_path, mode='a', encoding='utf-8')
        _file_handler.setLevel(logging.INFO)
        _file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(_file_handler)
    except PermissionError:
        # Fallback: nur Console-Logging wenn Datei nicht beschreibbar
        logger.warning("model_loader.log nicht beschreibbar – nur Console-Logging aktiv")


def _is_llm_role(role: object) -> bool:
    """True, wenn der VRAM-Snapshot die LLM-Rolle trägt.

    Single-GPU-Systeme tragen die zusammengesetzte Rolle "LLM+AUX"
    (siehe utils/vram_monitor.py, 2026-09-04) — beide Formen sind die LLM-GPU.
    """
    return role in ("LLM", "LLM+AUX")


# ============================================================================
# CUDA SCHEDULING — Prevent Spin-Wait on Windows WDDM (SOTA)
# ============================================================================
# Shared utility in utils/cuda_init.py — MUST execute before first CUDA context.
# Entry point (enhanced_streamlit_bot.py) imports this first, but we also call
# here as a safety net in case model_loader.py is imported directly.
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from pathlib import Path
from utils.cuda_init import configure_cuda_scheduling
configure_cuda_scheduling()
from utils.model_registry import models_root
from utils import token_scaling  # Token/Context-Skalierung (nie-feilend, s. docs/20)

# ============================================================================
# MODELL-KONFIGURATIONEN - Pfade über Umgebungsvariablen konfigurierbar
# Single Source of Truth: utils/model_registry.models_root()
#   Default: ~/.cache/lm-studio/models/lmstudio-community (Home des Users)
#   Override: BOT_MODELS_DIR
# ============================================================================
_MODELS_ROOT = Path(models_root())
_DEFAULT_MODEL_PATH = str(_MODELS_ROOT / "Magistral-Small-2509-GGUF" / "Magistral-Small-2509-Q4_K_M.gguf")
_DEFAULT_MMPROJ_PATH = str(_MODELS_ROOT / "Magistral-Small-2509-GGUF" / "mmproj-Magistral-Small-2509-F16.gguf")

_GEMMA4_MODEL_PATH = str(_MODELS_ROOT / "gemma-4-E4B-it-GGUF" / "gemma-4-E4B-it-Q4_K_M.gguf")
_GEMMA4_MMPROJ_PATH = str(_MODELS_ROOT / "gemma-4-E4B-it-GGUF" / "mmproj-gemma-4-E4B-it-BF16.gguf")

_GEMMA4_12B_MODEL_PATH = str(_MODELS_ROOT / "gemma-4-12B-it-QAT-GGUF" / "gemma-4-12B-it-QAT-Q4_0.gguf")
_GEMMA4_12B_MMPROJ_PATH = str(_MODELS_ROOT / "gemma-4-12B-it-QAT-GGUF" / "mmproj-gemma-4-12B-it-QAT-BF16.gguf")

_GEMMA3_12B_MODEL_PATH = str(_MODELS_ROOT / "gemma-3-12b-it-GGUF" / "gemma-3-12b-it-Q4_K_M.gguf")
_GEMMA3_12B_MMPROJ_PATH = str(_MODELS_ROOT / "gemma-3-12b-it-GGUF" / "mmproj-model-f16.gguf")

_GEMMA4_26B_MODEL_PATH = str(_MODELS_ROOT / "gemma-4-26B-A4B-it-GGUF" / "gemma-4-26B-A4B-it-Q4_K_M.gguf")
_GEMMA4_26B_MMPROJ_PATH = str(_MODELS_ROOT / "gemma-4-26B-A4B-it-GGUF" / "mmproj-gemma-4-26B-A4B-it-BF16.gguf")

# ── Single Source of Truth: Default context window size ──────────────────────
# 16384 tokens = optimal VRAM balance for RTX 4090 with Magistral 24B Q4_K_M:
#   - KV-cache: ~2.0 GB (vs ~4.0 GB at 32768) → frees ~2 GB VRAM headroom
#   - Sufficient for all use cases: KG extraction (max 4k tokens),
#     RAG chat (typically 6-10k tokens), tool calling (4-8k tokens)
#   - Override via LLM_N_CTX environment variable if needed
LLM_CONTEXT_SIZE: int = int(os.environ.get("LLM_N_CTX", "16384"))
GEMMA4_CONTEXT_SIZE: int = int(os.environ.get("LLM_GEMMA4_N_CTX", "32768"))
# Fallback-Kontextfenster für den OOM-Retry (kleinerer KV-Cache): Wird
# automatisch versucht, wenn der Load mit dem vollen n_ctx scheitert bzw.
# der VRAM-Pre-Check das volle n_ctx nicht durchlässt (2026-08-27).
LLM_CONTEXT_FALLBACK: int = 8192

# ── Model Families: Determines prompt format, stop tokens, tool-call parsing ──
MODEL_FAMILY_MAGISTRAL = "magistral"
MODEL_FAMILY_GEMMA = "gemma"

MODEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "magistral-small": {
        "name": "Magistral Small 2509 (Q4_K_M)",
        "description": "24B Parameter Modell mit Reasoning-Fähigkeiten, Q4_K_M Quantisierung",
        "model_path": os.environ.get("LLM_MODEL_PATH", _DEFAULT_MODEL_PATH),
        "mmproj_path": os.environ.get("LLM_MMPROJ_PATH", _DEFAULT_MMPROJ_PATH),
        "n_ctx": LLM_CONTEXT_SIZE,
        "n_gpu_layers": int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
        "model_family": MODEL_FAMILY_MAGISTRAL,
    },
    "gemma-4-e4b": {
        "name": "Gemma 4 E4B IT (Q4_K_M)",
        "description": "4B Multimodal-Modell mit Vision-Fähigkeiten (Gemma 4), Q4_K_M Quantisierung",
        "model_path": os.environ.get("LLM_GEMMA4_MODEL_PATH", _GEMMA4_MODEL_PATH),
        "mmproj_path": os.environ.get("LLM_GEMMA4_MMPROJ_PATH", _GEMMA4_MMPROJ_PATH),
        "n_ctx": GEMMA4_CONTEXT_SIZE,
        "n_gpu_layers": int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
        "model_family": MODEL_FAMILY_GEMMA,
    },
    "gemma-4-12b-it": {
        "name": "Gemma 4 12B IT QAT (Q4_0)",
        "description": "12B Multimodal-Modell mit Vision-Fähigkeiten (Gemma 4), QAT Q4_0 Quantisierung",
        "model_path": os.environ.get("LLM_GEMMA4_12B_MODEL_PATH", _GEMMA4_12B_MODEL_PATH),
        "mmproj_path": os.environ.get("LLM_GEMMA4_12B_MMPROJ_PATH", _GEMMA4_12B_MMPROJ_PATH),
        "n_ctx": int(os.environ.get("LLM_GEMMA4_12B_N_CTX", str(GEMMA4_CONTEXT_SIZE))),
        "n_gpu_layers": int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
        "model_family": MODEL_FAMILY_GEMMA,
    },
    "gemma-3-12b-it": {
        "name": "Gemma 3 12B IT (Q4_K_M)",
        "description": "12B Multimodal-Modell mit Vision-Fähigkeiten (Gemma 3), Q4_K_M Quantisierung",
        "model_path": os.environ.get("LLM_GEMMA3_12B_MODEL_PATH", _GEMMA3_12B_MODEL_PATH),
        "mmproj_path": os.environ.get("LLM_GEMMA3_12B_MMPROJ_PATH", _GEMMA3_12B_MMPROJ_PATH),
        "n_ctx": int(os.environ.get("LLM_GEMMA3_12B_N_CTX", str(LLM_CONTEXT_SIZE))),
        "n_gpu_layers": int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
        "model_family": MODEL_FAMILY_GEMMA,
    },
    "gemma-4-26b-a4b": {
        "name": "Gemma 4 26B A4B IT (Q4_K_M)",
        "description": "26B Multimodal-MoE-Modell (4B aktiv) mit Vision-Fähigkeiten (Gemma 4), Q4_K_M Quantisierung",
        "model_path": os.environ.get("LLM_GEMMA4_26B_MODEL_PATH", _GEMMA4_26B_MODEL_PATH),
        "mmproj_path": os.environ.get("LLM_GEMMA4_26B_MMPROJ_PATH", _GEMMA4_26B_MMPROJ_PATH),
        "n_ctx": int(os.environ.get("LLM_GEMMA4_26B_N_CTX", str(LLM_CONTEXT_SIZE))),
        "n_gpu_layers": int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
        "model_family": MODEL_FAMILY_GEMMA,
    },
}

# Standard-Modell
DEFAULT_MODEL = "gemma-4-12b-it"


def _align_gpu_batch(value: int) -> int:
    """Align batch sizes to stable ggml-friendly steps."""
    if value <= 256:
        return 256
    return max(256, (value // 256) * 256)


def select_gpu_tuning_profile(
    *,
    model_path: str,
    n_ctx: int,
    gpu_memory_gb: float,
    logical_cores: int,
    batch_override: Optional[int] = None,
    ubatch_override: Optional[int] = None,
    threads_override: Optional[int] = None,
    threads_batch_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Select the inference tuning profile for llama.cpp GPU runs.

    The policy is intentionally conservative enough to avoid CUDA kernel
    failures on long-context prefill, while still pushing high throughput on
    24GB-class GPUs.
    """
    # SOTA 2026 hardening for llama.cpp CUDA stability on consumer GPUs:
    # avoid aggressive prefill batches that are known to trigger
    # ggml_cuda_compute_forward/MUL_MAT_ID failures on some kernels.
    base_batch = 3072
    safe_upper_batch = 3072
    if batch_override is not None:
        optimal_batch = _align_gpu_batch(max(256, int(batch_override)))
        batch_source = "override"
        context_cap = optimal_batch
        vram_cap = optimal_batch
    else:
        # Validated default profile baseline: keep prefill batch at 3072
        # consistently on 24GB-class setups unless VRAM caps require lower.
        context_cap = 3072

        if gpu_memory_gb < 12:
            vram_cap = 1024
        elif gpu_memory_gb < 20:
            vram_cap = 2048
        else:
            vram_cap = 3072

        optimal_batch = _align_gpu_batch(
            min(base_batch, context_cap, vram_cap, safe_upper_batch)
        )
        batch_source = "adaptive"

    if ubatch_override is not None:
        optimal_ubatch = _align_gpu_batch(max(256, int(ubatch_override)))
        optimal_ubatch = min(optimal_ubatch, optimal_batch)
        ubatch_source = "override"
    else:
        optimal_ubatch = min(2048, optimal_batch)
        ubatch_source = "adaptive"

    if threads_override is not None:
        optimal_threads = max(1, int(threads_override))
        threads_source = "override"
    else:
        if n_ctx >= 24576 and gpu_memory_gb >= 20:
            optimal_threads = min(12, max(4, logical_cores // 2))
        else:
            optimal_threads = max(4, logical_cores // 4)
        threads_source = "adaptive"

    if threads_batch_override is not None:
        optimal_threads_batch = max(1, int(threads_batch_override))
        threads_batch_source = "override"
    else:
        # Empirically validated on this hardware: matching prefill threads to
        # decode threads is faster and avoids CPU oversubscription.
        optimal_threads_batch = optimal_threads
        threads_batch_source = "adaptive"

    optimal_tokens = 2048 if gpu_memory_gb >= 20 else 1024

    return {
        "optimal_batch": optimal_batch,
        "optimal_ubatch": optimal_ubatch,
        "optimal_threads": optimal_threads,
        "optimal_threads_batch": optimal_threads_batch,
        "optimal_tokens": optimal_tokens,
        "base_batch": base_batch,
        "context_cap": context_cap,
        "vram_cap": vram_cap,
        "batch_source": batch_source,
        "ubatch_source": ubatch_source,
        "threads_source": threads_source,
        "threads_batch_source": threads_batch_source,
    }

# ── Vision Chat Handler (GGUF-Native-Template basiert) ──────────────────────
# Statt eines hardcoded family-spezifischen CHAT_FORMAT-Strings nutzt der
# Vision-Pfad — exakt wie der Text-Pfad — das ``tokenizer.chat_template`` aus
# den GGUF-Metadaten als Single Source of Truth. Multimodale Content-Items
# (image_url + text) werden vor dem Rendern zu reinem String-Content verflacht;
# die Image-URLs bleiben dabei erhalten, sodass Llava15ChatHandler sie nach
# dem Render strukturell durch den mtmd-Media-Marker ersetzen kann.
def _flatten_multimodal_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Wandelt multimodale Content-Listen in reinen String-Content um.

    Jedes ``image_url``-Item wird als URL-String, jedes ``text``-Item als
    Text-Stück übernommen; alle Stücke werden mit ``\\n`` verbunden. Messages
    mit String-Content bleiben unverändert.

    Llava15ChatHandler scannt den gerenderten Prompt nach den ursprünglichen
    URLs und ersetzt sie durch den mtmd-Media-Marker — daher müssen die URLs
    1:1 im gerenderten Output erscheinen.
    """
    flat: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            flat.append(msg)
            continue
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "image_url":
                url = item.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url", "")
                if url:
                    parts.append(str(url))
            elif t == "text":
                text = item.get("text", "")
                if text:
                    parts.append(str(text))
        merged = dict(msg)
        merged["content"] = "\n".join(parts)
        flat.append(merged)
    return flat


class _NativeTemplateVisionChatHandler(Llava15ChatHandler):
    """Llava15ChatHandler-Variante mit GGUF-Native-Chat-Template.

    Setzt ``CHAT_FORMAT`` lazy auf das native ``tokenizer.chat_template``
    aus den GGUF-Metadaten und delegiert sämtliche multimodale Mechanik
    (CLIP/mtmd-Tokenisierung, Bitmap-Erstellung, Generation-Loop) an die
    Basisklasse. Vor dem Aufruf der Basisklasse:

    1. Original-Image-URLs werden via ``Llava15ChatHandler.get_image_urls``
       extrahiert und gecached.
    2. Multimodaler Content wird zu reinen Strings verflacht.
    3. Falls die System-Rolle vom Template nicht akzeptiert wird (Capability-
       Probe via Loader), werden System-Inhalte deterministisch in den ersten
       User-Turn gemergt.
    4. ``self.get_image_urls`` wird temporär auf eine Closure gepatcht, die
       die gecachten URLs zurückgibt — die geflatteten Messages haben keine
       list-content mehr, ohne Patch würde der Renderer keine Bilder finden.
    """

    def __init__(
        self,
        clip_model_path: str,
        *,
        loader: 'ModelLoader',
        verbose: bool = False,
    ) -> None:
        super().__init__(clip_model_path=clip_model_path, verbose=verbose)
        self._loader = loader

    def __call__(self, *, llama, messages, **kwargs):  # type: ignore[override]
        # CHAT_FORMAT lazy aus GGUF setzen (einmalig pro Handler-Instanz).
        if self.CHAT_FORMAT is Llava15ChatHandler.CHAT_FORMAT:
            native_template = llama.metadata.get("tokenizer.chat_template", "")
            if not native_template:
                raise RuntimeError(
                    "Vision-Pfad: GGUF enthält kein tokenizer.chat_template — "
                    "kein deterministisches Vision-Format ableitbar."
                )
            self.CHAT_FORMAT = native_template

        # Capability-Cache des Loaders sicherstellen (System-Rolle?).
        self._loader._ensure_template_capabilities()

        original_urls = Llava15ChatHandler.get_image_urls(messages)
        flat_messages = _flatten_multimodal_messages(messages)
        if self._loader._supports_system_role is False:
            flat_messages = ModelLoader._merge_system_into_first_user(flat_messages)

        # Instance-Override: Base-__call__ ruft self.get_image_urls(messages),
        # die geflatteten Messages enthalten aber keine list-content mehr.
        # Closure liefert die zuvor extrahierten Original-URLs.
        self.get_image_urls = (  # type: ignore[assignment]
            lambda _msgs, _u=original_urls: list(_u)
        )
        try:
            return super().__call__(
                llama=llama,
                messages=flat_messages,  # type: ignore[arg-type]
                **kwargs,
            )
        finally:
            try:
                del self.get_image_urls
            except AttributeError:
                pass


# ── GGUF-Metadaten & VRAM-Schätzung (Telemetrie, 2026-08-27) ─────────
# Die frühere VRAM-Schätzung nutzte einen flachen Puffer (model_size + 2.0 GB):
# (a) unterschätzte er den KV-Cache großer Modelle (z.B. 27B/30B bei
#     n_ctx=16384: 4–8 GB statt 2 GB) und ließ Loads durch, die erst in
#     Llama(...) mit "Failed to create llama_context" scheiterten;
# (b) sie konnte die tatsächliche Speicherverteilung von llama.cpp nicht
#     vorhersagen. Jetzt wird der KV-Cache für eine informative Warnung
#     aus den GGUF-Metadaten (n_layer, KV-Köpfe, Head-Dim) berechnet:
#     KV-Bytes/Token (f16, K+V) = 2 × n_layer × n_head_kv × head_dim × 2.
# Die GGUF-Datei bleibt Single Source of Truth (keine Modell-Tabellen).
# GGUF-Wertetypen gem. offiziellem gguf.h (ggml-org/ggml, include/gguf.h):
#   0=UINT8 1=INT8 2=UINT16 3=INT16 4=UINT32 5=INT32 6=FLOAT32
#   7=BOOL 8=STRING 9=ARRAY 10=UINT64 11=INT64 12=FLOAT64
# BOOL/STRING/ARRAY werden in _read_value separat behandelt;
# ARRAY: Elementtyp (int32) + u64-Count + Elemente (gguf.h Punkt 3a).
_GGUF_SCALAR_FORMATS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h",
    4: "<I", 5: "<i", 6: "<f",
    10: "<Q", 11: "<q", 12: "<d",
}
_GGUF_TYPE_BOOL = 7
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9


def _llama_cpp_version() -> str:
    """Version der installierten llama-cpp-python (für Fehlermeldungen)."""
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("llama-cpp-python")
    except Exception:
        return getattr(llama_cpp, "__version__", "unbekannt")


def _read_gguf_metadata(model_path: str) -> Optional[Dict[str, Any]]:
    """Liest die GGUF-Metadaten (Header + KV-Paare, ohne Tensoren) aus.

    Gibt None zurück, wenn die Datei keinen validen GGUF-Header enthält —
    der Aufrufer fällt dann auf die konservative Fallback-Schätzung zurück.
    Arrays werden nur zum Byte-Sync gelesen und nicht gespeichert.
    """
    try:
        with open(model_path, "rb") as fh:
            head = fh.read(24)
            if len(head) < 24 or head[:4] != b"GGUF":
                return None
            # Layout (GGUF v1, version==3):
            #   magic(4) | version u32 @4 | tensor_count u64 @8 | kv_count u64 @16
            (version,) = struct.unpack_from("<I", head, 4)
            if version != 3:
                return None  # Nur GGUF v1 (version==3) wird hier geparst; v2/v3 anderes Layout
            (kv_count,) = struct.unpack_from("<Q", head, 16)
            if kv_count > 100_000:
                return None

            def _read_value(value_type: int) -> Any:
                if value_type == _GGUF_TYPE_BOOL:  # 7: 1 Byte (int8)
                    b = fh.read(1)
                    if len(b) < 1:
                        raise EOFError
                    return b[0] != 0
                if value_type == _GGUF_TYPE_STRING:  # 8: u64-Laenge + UTF-8-Bytes
                    n_b = fh.read(8)
                    if len(n_b) < 8:
                        raise EOFError
                    (n,) = struct.unpack("<Q", n_b)
                    if n > 100_000_000:
                        raise ValueError("GGUF-String zu lang")
                    raw = fh.read(n)
                    if len(raw) < n:
                        raise EOFError
                    return raw.decode("utf-8", errors="replace")
                if value_type in _GGUF_SCALAR_FORMATS:
                    fmt = _GGUF_SCALAR_FORMATS[value_type]
                    size = struct.calcsize(fmt)
                    raw = fh.read(size)
                    if len(raw) < size:
                        raise EOFError
                    return struct.unpack(fmt, raw)[0]
                if value_type == _GGUF_TYPE_ARRAY:  # 9: Elementtyp(int32) + u64-Count + Elemente
                    et_b = fh.read(4)
                    if len(et_b) < 4:
                        raise EOFError
                    (elem_type,) = struct.unpack("<I", et_b)
                    n_b = fh.read(8)
                    if len(n_b) < 8:
                        raise EOFError
                    (count,) = struct.unpack("<Q", n_b)
                    if count > 10_000_000:
                        raise ValueError("GGUF-Array zu gross")
                    for _i in range(count):
                        _read_value(elem_type)  # nur Byte-Sync, kein Speichern
                    return None
                raise ValueError(f"unbekannter GGUF-Wertetyp {value_type}")

            meta: Dict[str, Any] = {}
            for _i in range(kv_count):
                klen_b = fh.read(8)
                if len(klen_b) < 8:
                    break
                (klen,) = struct.unpack("<Q", klen_b)
                if klen > 1024:
                    break
                key = fh.read(klen).decode("utf-8", errors="replace")
                t_b = fh.read(4)
                if len(t_b) < 4:
                    break
                (value_type,) = struct.unpack("<I", t_b)
                try:
                    value = _read_value(value_type)
                except (EOFError, ValueError, struct.error):
                    break  # Partial-Metadaten reichen für die Schätzung
                if value is not None:
                    meta[key] = value
            return meta or None
    except OSError:
        return None




def _extract_gguf_shape(meta: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Extrahiert n_layer / n_head_kv / head_dim aus GGUF-Metadaten.

    Unterstützt die gängigen Key-Konventionen (llama.*, gemma.*, qwen*,
    mistral.*, ...); bei fehlenden Werten None (→ Fallback-Schätzung).
    """
    arch = meta.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        return None

    def _num(*keys: str) -> Optional[int]:
        for key in keys:
            value = meta.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return None

    # Key-Konventionen: moderne Namen (block_count, attention.head_count_kv,
    # attention.head_size — z.B. qwen35/gemma4) zuerst, dann Legacy-Formen
    # (n_layer, n_head_kv — z.B. llama2).
    n_layer = _num(f"{arch}.block_count", f"{arch}.n_layer", f"{arch}.n_layers")
    n_head = _num(f"{arch}.attention.head_count", f"{arch}.n_head")
    n_head_kv = _num(
        f"{arch}.attention.head_count_kv",
        f"{arch}.n_head_kv",
        f"{arch}.attention.head_count_k",
    ) or n_head
    head_dim = _num(f"{arch}.attention.head_size", f"{arch}.head_size")
    if head_dim is None:
        n_embd = _num(f"{arch}.embedding_length", f"{arch}.n_embd")
        if n_embd is not None and n_head:
            head_dim = n_embd // n_head
    if not (n_layer and n_head_kv and head_dim):
        return None
    return {"n_layer": n_layer, "n_head_kv": n_head_kv, "head_dim": head_dim}


_VRAM_COMPUTE_BUFFER_GB: float = 1.5   # Compute-Buffers, CUDA-Overhead
_VRAM_FLAT_BUFFER_GB: float = 2.0      # Fallback ohne GGUF-Metadaten (alt: +2.0 GB)


def estimate_vram_gb(model_path: str, model_size_gb: float, n_ctx: int) -> Dict[str, Any]:
    """Schätzt den VRAM-Bedarf für einen Llama-Load.

    required = Modellgröße + KV-Cache(n_ctx) + Compute-Puffer.
    KV-Cache/Token (f16, K+V) = 2 × n_layer × n_head_kv × head_dim × 2 Bytes
    (konservativ: Sliding-Window-Sparpotenzial wird NICHT angerechnet).

    Returns:
        {"required_gb": float, "kv_gb": float, "shape": Optional[dict]}
    """
    shape: Optional[Dict[str, int]] = None
    try:
        meta = _read_gguf_metadata(model_path)
        if meta is not None:
            shape = _extract_gguf_shape(meta)
    except Exception:
        shape = None
    if shape is None:
        return {
            "required_gb": model_size_gb + _VRAM_FLAT_BUFFER_GB,
            "kv_gb": 0.0,
            "shape": None,
        }
    kv_bytes_per_token = 2 * shape["n_layer"] * shape["n_head_kv"] * shape["head_dim"] * 2
    kv_gb = kv_bytes_per_token * max(0, int(n_ctx)) / (1024**3)
    return {
        "required_gb": model_size_gb + kv_gb + _VRAM_COMPUTE_BUFFER_GB,
        "kv_gb": kv_gb,
        "shape": shape,
    }


# ── Load-Fehler-Klassifikation (2026-08-27) ─────────────────────────────────
# Unterscheidet "Architektur vom Build nicht unterstützt" (Build-Upgrade nötig,
# Neustart/Model-Switch nützen NICHT) von "Kapazitätsfehler" (VRAM/KV-Cache —
# Retry mit reduziertem Kontext oder externer VRAM-Freigabe hilfreich). So meldet
# der Loader die präzise Root Cause statt eines generischen Fehlers.
_LOAD_FAILURE_ARCHITECTURE_MARKERS = (
    "unknown model architecture",
    "unknown architecture",
    "unsupported architecture",
    "unsupported model",
    "unrecognized architecture",
    "ggml: unknown",
    "llama_model_loader: unknown",
)
_LOAD_FAILURE_CAPACITY_MARKERS = (
    "out of memory",
    "cannot allocate",
    "insufficient memory",
    "failed to create llama_context",
    "failed to reserve memory",
    "failed to alloc",
    "alloc failed",
    "no space left",
    "kv cache",
    "n_ctx",
    "context size",
)
_LOAD_FAILURE_CPU_ISA_MARKERS = (
    "0xc000001d",
    "illegal instruction",
)

# ── KV-Quantisierungs-Retry: Fehlertyp-Erkennung ───────────────────────────
# llama.cpp meldet die Ablehnung eines KV-Cache-Typs (type_k/type_v) je nach
# Build-Version unterschiedlich (llama-context.cpp / llama-kv-cache.cpp),
# z. B. "unknown kv cache type", "unsupported kv cache type" oder
# "flash attention is not supported for kv cache type". Der Marker-Satz
# deckt diese bekannten Meldungen ab.
# WICHTIG: Der Satz muss eng bleiben — nur Fehler, die eindeutig den
# KV-Cache-Typ betreffen, dürfen den f16-Retry auslösen. Alle anderen Fehler
# (OOM, Architektur, CUDA, Test-Sentinels) müssen unverändert propagieren,
# damit _classify_load_failure() die echte Root Cause meldt (kein
# Silent-Fallback, s. AGENTS.md).
_KV_TYPE_REJECTION_MARKERS = (
    "kv cache",
    "kv-cache",
    "cache type",
    "type_k",
    "type_v",
    "flash attention",
    "ggml_type",
    "unknown type",
    "unsupported type",
)


def _looks_like_kv_type_rejection(exc: BaseException) -> bool:
    """Liefert True, wenn die Exception eindeutig die Ablehnung eines
    KV-Cache-Typs (type_k/type_v) meldet — dann ist ein Retry mit dem
    llama.cpp-Default (f16) sinnvoll. Alle anderen Fehler werden
    unverändert weitergeworfen."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _KV_TYPE_REJECTION_MARKERS)


def _classify_load_failure(exc: BaseException) -> str:
    """Klassifiziert einen Llama-Load-Fehler nach Root Cause.

    Args:
        exc: Ausgegebene Exception der Llama-Konstruktion.

    Returns:
        "architecture" — Build unterstützt die Modell-Architektur nicht
            (z.B. Qwen3.5 auf altem llama.cpp): kein Retry möglich,
            llama-cpp-python muss neu gebaut werden.
        "capacity" — VRAM-/Kontext-/KV-Cache-Kapazität reicht nicht:
            Retry mit reduziertem n_ctx oder VRAM freigeben.
        "cpu_isa" — Native Binary nutzt eine von der CPU nicht unterstützte
            Instruktion; Build-Flags bzw. Wheel müssen korrigiert werden.
        "unknown" — alles andere.
    """
    msg = str(exc).lower()
    if any(marker in msg for marker in _LOAD_FAILURE_ARCHITECTURE_MARKERS):
        return "architecture"
    if any(marker in msg for marker in _LOAD_FAILURE_CAPACITY_MARKERS):
        return "capacity"
    if any(marker in msg for marker in _LOAD_FAILURE_CPU_ISA_MARKERS):
        return "cpu_isa"
    return "unknown"


def _detect_model_family(model_id_or_path: str) -> str:
    """Erkennt die Model-Family aus Model-ID oder Dateipfad.
    
    Prüft zuerst MODEL_CONFIGS, dann heuristische Pfad-Analyse.
    
    Returns:
        MODEL_FAMILY_MAGISTRAL oder MODEL_FAMILY_GEMMA
    """
    # 1. Exakter Match in MODEL_CONFIGS
    if model_id_or_path in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_id_or_path].get("model_family", MODEL_FAMILY_MAGISTRAL)
    
    # 2. Heuristische Erkennung aus Pfad/Name
    lower = model_id_or_path.lower()
    if "gemma" in lower:
        return MODEL_FAMILY_GEMMA
    if "magistral" in lower or "mistral" in lower:
        return MODEL_FAMILY_MAGISTRAL
    
    # 3. Fallback
    return MODEL_FAMILY_MAGISTRAL


def get_available_models() -> Dict[str, str]:
    """Gibt ein Dictionary mit Modell-ID -> Anzeigename zurück"""
    return {key: config["name"] for key, config in MODEL_CONFIGS.items()}

def get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Gibt die Konfiguration für ein Modell zurück"""
    return MODEL_CONFIGS.get(model_id)


# CUDA Lock für Thread-Sicherheit
cuda_lock = threading.RLock()

def cuda_safe(func):
    """Decorator für CUDA-Thread-Sicherheit"""
    def wrapper(*args, **kwargs):
        with cuda_lock:
            return func(*args, **kwargs)
    return wrapper


class ModelLoader:
    """
    Thread-safe Singleton ModelLoader für LLM-Verwaltung.
    
    Garantiert nur eine Instanz mit vollständiger Thread-Sicherheit:
    - RLock für Singleton-Erstellung (verhindert Race Conditions)
    - Atomare Initialisierung (alle Attribute unter Lock)
    - Separate Locks für Load-Operationen
    """
    _instance: Optional['ModelLoader'] = None
    _instance_lock: threading.RLock = threading.RLock()
    
    # Instance-Attribute (Type Hints für Pylance)
    llm: Optional[Llama]
    chat_handler: Optional[Llava15ChatHandler]
    model_path: Optional[str]
    mmproj_path: Optional[str]
    is_multimodal: bool
    progress_callback: Optional[Callable]
    is_loading: bool
    # ID des Threads, der gerade lädt (Race-Fix 2026-08-27: unload_model()
    # wartet nur auf Fremdladungen, nicht auf das eigene is_loading-Flag)
    _loading_thread: Optional[int]
    _initialized: bool
    current_model_id: Optional[str]
    _llm_call_count: int
    # ── Model-agnostic: dynamische Special-Tokens & Capabilities ──
    _model_family: str
    _bos_token: str
    _eos_token: str
    _stop_sequences: list
    _cached_n_ctx: Optional[int]
    # Capability-Flag (None = noch nicht geprobt, True/False = Probe-Ergebnis)
    _supports_system_role: Optional[bool]
    _supports_cache_prompt_arg: Optional[bool]

    def __new__(cls) -> 'ModelLoader':
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance.llm = None
                instance.chat_handler = None
                instance.model_path = None
                instance.mmproj_path = None
                instance.is_multimodal = False
                instance.progress_callback = None
                instance.is_loading = False
                instance._loading_thread = None
                instance._initialized = True
                instance.current_model_id = None
                instance._llm_call_count = 0
                # ── Model-agnostic defaults (werden bei load() aus GGUF überschrieben) ──
                instance._model_family = MODEL_FAMILY_GEMMA
                instance._bos_token = "<bos>"
                instance._eos_token = "<eos>"
                instance._stop_sequences = []
                instance._supports_system_role = None
                instance._supports_cache_prompt_arg = None
                # ★ GPU-OPT: Cached n_ctx — n_ctx() is constant after model load,
                # but each call goes through cuda_lock + C FFI (~0.1ms).
                # During KG extraction with 50+ LLM calls, that's 4 calls/inference
                # × 50 chunks = 200 lock-protected C calls → ~20ms total.
                instance._cached_n_ctx = None
                # Token/Context-Skalierung: letzter propose()-Vorschlag (None vor Load)
                instance.token_scaling_proposal = None
                # ★ PERF: Bounded LRU-Cache für count_tokens() — tokenize() ist
                # deterministisch pro geladenem Modell, aber teure C-FFI-Calls
                # unter cuda_lock. Budget-Checks/Kontext-Builds zählen dieselben
                # Texte oft doppelt. Cache wird bei Modell-Load/Unload/Swap
                # invalidiert (_invalidate_token_cache, s. load_model/unload_model).
                instance._token_cache = OrderedDict()  # text -> int
                instance._token_cache_max = 1024       # max Einträge
                instance._token_cache_max_len = 32768  # nur Texte <= 32 KB cachen
                instance._token_cache_lock = threading.Lock()
                cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        """Init-Methode ist ein No-Op - alle Initialisierung in __new__"""
        pass

    def _supports_cache_prompt(self) -> bool:
        """Detect whether llama_cpp create_completion supports cache_prompt."""
        if self._supports_cache_prompt_arg is not None:
            return self._supports_cache_prompt_arg

        try:
            fn = getattr(self.llm, "create_completion", None)
            if fn is None:
                self._supports_cache_prompt_arg = False
                return False
            sig = inspect.signature(fn)
            self._supports_cache_prompt_arg = "cache_prompt" in sig.parameters
        except Exception:
            self._supports_cache_prompt_arg = False
        return self._supports_cache_prompt_arg

    def get_current_model_id(self) -> Optional[str]:
        """Gibt die ID des aktuell geladenen Modells zurück"""
        return self.current_model_id
    
    def get_current_model_name(self) -> str:
        """Gibt den Namen des aktuell geladenen Modells zurück"""
        model_id = self.get_current_model_id()
        if model_id and model_id in MODEL_CONFIGS:
            name = MODEL_CONFIGS[model_id]["name"]
            # Ensure string return
            return str(name) if name is not None else "Kein Modell geladen"
        if model_id and str(model_id).startswith("custom:"):
            # Dynamisches Modell (LM-Studio-Registry): Dateiname ohne Extension
            return str(model_id)[len("custom:"):].rsplit(".", 1)[0]
        return "Kein Modell geladen"
    
    def load_model_by_config(
        self,
        model_id: str,
        token_scaling_overrides: Optional["token_scaling.TokenScalingOverrides"] = None,
    ) -> bool:
        """
        Lädt ein Modell anhand seiner Konfigurations-ID.
        
        Args:
            model_id: ID des Modells aus MODEL_CONFIGS
            
        Returns:
            True wenn erfolgreich, False bei Fehler
        """
        config = get_model_config(model_id)
        if not config:
            self._log_progress(f"[ERROR] Unbekannte Modell-ID: {model_id}")
            self._log_progress(f"[INFO] Verfügbare Modelle: {list(MODEL_CONFIGS.keys())}")
            return False
        
        self._log_progress(f"[INFO] Lade Modell: {config['name']}")
        self._log_progress(f"[INFO] {config.get('description', '')}")
        
        # Prüfe ob bereits ein anderes Modell geladen ist
        if self.is_model_loaded() and self.current_model_id != model_id:
            self._log_progress(f"[INFO] Entlade vorheriges Modell: {self.get_current_model_name()}")
            self.unload_model()
        
        success = self.load_model(
            model_path=config["model_path"],
            mmproj_path=config.get("mmproj_path"),
            n_gpu_layers=config.get("n_gpu_layers", -1),
            n_ctx=config.get("n_ctx", LLM_CONTEXT_SIZE),
            token_scaling_overrides=token_scaling_overrides,
        )
        
        if success:
            self.current_model_id = model_id
            self._model_family = config.get("model_family", _detect_model_family(model_id))
            self._resolve_special_tokens()
            self._log_progress(f"[SUCCESS] Modell '{config['name']}' erfolgreich geladen!")
            self._log_progress(f"[INFO] Model-Family: {self._model_family}, BOS: {self._bos_token!r}, EOS: {self._eos_token!r}")
            self._log_progress(f"[INFO] Stop-Sequenzen: {self._stop_sequences}")
        else:
            self.current_model_id = None
            
        return bool(success)

    def load_model_by_path(
        self,
        model_path: str,
        mmproj_path: Optional[str] = None,
        n_ctx: Optional[int] = None,
        token_scaling_overrides: Optional["token_scaling.TokenScalingOverrides"] = None,
    ) -> bool:
        """Lädt ein Modell aus einem beliebigen GGUF-Pfad (dynamische Registry).

        Ergänzt ``load_model_by_config`` für Modelle ohne statische
        ``MODEL_CONFIGS``-Eintragung (z. B. alle Modelle im LM-Studio-
        Community-Ordner, siehe ``utils/model_registry.py``).

        Args:
            model_path: Absoluter Pfad zur GGUF-Datei.
            mmproj_path: Optionaler Pfad zur mmproj-Datei (Vision-Fähigkeit).
            n_ctx: Optionale Kontextgröße (Default: LLM_CONTEXT_SIZE).

        Returns:
            True wenn erfolgreich, False bei Fehler
        """
        if not model_path or not os.path.isfile(model_path):
            self._log_progress(f"[ERROR] Modell-Datei nicht gefunden: {model_path}")
            return False

        model_id = f"custom:{os.path.basename(model_path)}"

        # Prüfe ob bereits ein anderes Modell geladen ist
        if self.is_model_loaded() and self.current_model_id != model_id:
            self._log_progress(f"[INFO] Entlade vorheriges Modell: {self.get_current_model_name()}")
            self.unload_model()

        success = self.load_model(
            model_path=model_path,
            mmproj_path=mmproj_path,
            n_gpu_layers=int(os.environ.get("LLM_N_GPU_LAYERS", "-1")),
            n_ctx=n_ctx if n_ctx is not None else LLM_CONTEXT_SIZE,
            token_scaling_overrides=token_scaling_overrides,
        )

        if success:
            self.current_model_id = model_id
            # Model-Family heuristisch aus dem Pfad erkennen (z. B. "gemma" im
            # Ordernamen → Gemma-Chat-Format); die GGUF-Metadaten bleiben die
            # Single Source of Truth für Special-Tokens.
            self._model_family = _detect_model_family(model_path)
            self._resolve_special_tokens()
            self._log_progress(f"[SUCCESS] Modell aus Pfad geladen: {os.path.basename(model_path)}")
            self._log_progress(f"[INFO] Model-Family: {self._model_family}, BOS: {self._bos_token!r}, EOS: {self._eos_token!r}")
        else:
            self.current_model_id = None

        return bool(success)

    def is_model_loaded(self) -> bool:
        """Prüft ob ein Modell geladen ist"""
        result = self.llm is not None
        # Explicit bool cast for type safety
        return bool(result)

    def _resolve_special_tokens(self) -> None:
        """Liest BOS/EOS/EOT-Token und Stop-Sequenzen dynamisch aus den GGUF-Metadaten.

        SOTA-Prinzip: Die GGUF-Datei ist die Single Source of Truth. Es werden
        ausschließlich die im Modell deklarierten Special-Tokens verwendet -- keine
        family-spezifischen Hardcodes. Damit ist das Verfahren modell-agnostisch
        und überlebt Vokabular-Änderungen zwischen Modell-Versionen.

        Setzt:
        - self._bos_token: z.B. "<s>" (Magistral) oder "<bos>" (Gemma)
        - self._eos_token: z.B. "</s>" (Magistral) oder "<eos>" (Gemma)
        - self._stop_sequences: alle relevanten End-Marker aus den GGUF-Metadaten
          (eos_token_id und, falls vorhanden, eot_token_id für Chat-Turn-Ende).
        - self._jinja_template / self._supports_system_role: Cache invalidieren
          (wird beim nächsten Render neu geprobt).
        """
        # Caches invalidieren -- neues Modell, neue Capabilities.
        self._jinja_template = None
        self._supports_system_role = None

        # Stop-Sequenzen werden ausschließlich aus GGUF-Metadaten aufgebaut.
        stop_sequences: list = []

        if not self.llm:
            self._stop_sequences = stop_sequences
            return

        try:
            metadata = self.llm.metadata  # type: ignore[union-attr]

            def _detokenize_id(token_id: int) -> Optional[str]:
                with cuda_lock:
                    raw = self.llm.detokenize([token_id], special=True)  # type: ignore[union-attr]
                text = raw.decode("utf-8", errors="replace")
                return text or None

            # BOS-Token (für Jinja-Template-Render)
            bos_id = metadata.get("tokenizer.ggml.bos_token_id")
            if bos_id is not None:
                try:
                    self._bos_token = _detokenize_id(int(bos_id)) or self._bos_token
                    logger.info(f"[TOKENS] BOS aus GGUF: {self._bos_token!r} (id={int(bos_id)})")
                except Exception as e:
                    logger.warning(f"[TOKENS] BOS-Detokenize fehlgeschlagen: {e}")

            # EOS-Token (Sequenz-Ende)
            eos_id = metadata.get("tokenizer.ggml.eos_token_id")
            if eos_id is not None:
                try:
                    eos_text = _detokenize_id(int(eos_id))
                    if eos_text:
                        self._eos_token = eos_text
                        stop_sequences.append(eos_text)
                        logger.info(f"[TOKENS] EOS aus GGUF: {eos_text!r} (id={int(eos_id)})")
                except Exception as e:
                    logger.warning(f"[TOKENS] EOS-Detokenize fehlgeschlagen: {e}")

            # EOT-Token (Chat-Turn-Ende, z.B. Gemma <end_of_turn>, Llama-3 <|eot_id|>).
            # Nur in Chat-Tunes vorhanden; falls absent, reicht EOS allein.
            eot_id = metadata.get("tokenizer.ggml.eot_token_id")
            if eot_id is not None:
                try:
                    eot_text = _detokenize_id(int(eot_id))
                    if eot_text and eot_text not in stop_sequences:
                        stop_sequences.append(eot_text)
                        logger.info(f"[TOKENS] EOT aus GGUF: {eot_text!r} (id={int(eot_id)})")
                except Exception as e:
                    logger.warning(f"[TOKENS] EOT-Detokenize fehlgeschlagen: {e}")

            self._stop_sequences = stop_sequences

            logger.info(
                f"[TOKENS] Resolved: family={self._model_family}, "
                f"bos={self._bos_token!r}, eos={self._eos_token!r}, "
                f"stop={self._stop_sequences}"
            )

        except Exception as e:
            logger.warning(f"[TOKENS] Konnte Special-Tokens nicht aus GGUF lesen: {e}")
            # Bei nicht-lesbaren Metadaten: leere Stop-Liste -- llama.cpp stoppt
            # weiterhin am internen EOS-Token-ID-Check (token_eos()).
            self._stop_sequences = []

    @property
    def model_family(self) -> str:
        """Gibt die Model-Family des aktuell geladenen Modells zurück."""
        return self._model_family

    def set_progress_callback(self, callback: Callable[[str], None]):
        """Setzt eine Callback-Funktion für Progress-Updates"""
        self.progress_callback = callback

    def _log_progress(self, message: str):
        """Loggt Progress und ruft Callback auf"""
        logger.info(message)
        if self.progress_callback:
            try:
                self.progress_callback(message)
            except Exception as e:
                logger.error(f"Fehler beim Progress-Callback: {e}")

    @cuda_safe
    def unload_model(self):
        """Explizites Entladen des Modells mit verbesserter Speicherfreigabe.

        Race-Fix (2026-08-27): Die Warteschleife wartet nur noch, wenn eine
        ANDERE Thread das Modell lädt. Wird unload_model() vom Lade-Thread
        selbst aufgerufen (Cleanup nach Load-Fehler), wird sofort weiterge-
        arbeitet — vorher blockte sich der Cleanup-Pfad 30 s am eigenen
        is_loading-Flag (Self-Deadlock) und räumte gar nicht auf.
        """
        # FIX: Warte auf laufendes Laden mit Timeout
        max_wait = 30  # Sekunden
        _foreign_load_in_progress = (
            self.is_loading
            and threading.get_ident() != getattr(self, "_loading_thread", None)
        )
        waited = 0
        while _foreign_load_in_progress and waited < max_wait:
            self._log_progress(f"[WARNING] Modell wird gerade geladen - warte... ({waited}s/{max_wait}s)")
            time.sleep(1)
            waited += 1
            _foreign_load_in_progress = (
                self.is_loading
                and threading.get_ident() != getattr(self, "_loading_thread", None)
            )

        if _foreign_load_in_progress:
            self._log_progress("[ERROR] Timeout beim Warten auf Modell-Laden - breche ab")
            return
        
        try:
            self._log_progress("[INFO] Entlade aktuelles Modell...")
            
            # Chat Handler entladen
            if self.chat_handler is not None:
                try:
                    self._log_progress("[INFO] Entlade Chat-Handler...")
                    del self.chat_handler
                    self.chat_handler = None
                    self._log_progress("[SUCCESS] Chat-Handler entladen")
                except Exception as e:
                    self._log_progress(f"[WARNING] Fehler beim Chat-Handler entladen: {e}")
            
            # Hauptmodell entladen  
            if self.llm is not None:
                try:
                    self._log_progress("[INFO] Entlade Llama-Modell...")
                    del self.llm
                    self.llm = None
                    self._log_progress("[SUCCESS] Llama-Modell entladen")
                except Exception as e:
                    self._log_progress(f"[WARNING] Fehler beim Modell entladen: {e}")
            
            # Attribute zurücksetzen
            self.model_path = None
            self.mmproj_path = None
            self.is_multimodal = False
            self.current_model_id = None
            # ── Model-agnostic State zurücksetzen ──
            self._model_family = MODEL_FAMILY_MAGISTRAL
            self._bos_token = "<s>"
            self._eos_token = "</s>"
            self._stop_sequences = []
            self._cached_n_ctx = None  # ★ GPU-OPT: Invalidate cached n_ctx
            self.token_scaling_proposal = None  # Token-Skalierung: Vorschlag ungültig nach Entladung
            self._grammar_cache.clear()  # ★ GPU-OPT: Invalidate grammar cache
            self._invalidate_token_cache()  # ★ PERF: Tokenizer entladen → Cache ungültig
            self._jinja_template = None  # Template-Cache invalidieren
            self._supports_system_role = None  # Capability-Flag invalidieren
            self._supports_cache_prompt_arg = None
            
            # Aggressive Garbage Collection
            self._log_progress("[INFO] Erzwinge Speicherfreigabe...")
            gc.collect()
            
            # CUDA Cache leeren
            try:
                if hasattr(torch, 'cuda') and torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    self._log_progress("[INFO] CUDA Cache geleert")
            except Exception as cuda_err:
                self._log_progress(f"[WARNING] CUDA Cache konnte nicht geleert werden: {cuda_err}")
            
            self._log_progress("[SUCCESS] Modell vollständig entladen")
            
        except Exception as e:
            self._log_progress(f"[ERROR] Fehler beim Modell entladen: {e}")
            logger.exception("Fehler beim Modell entladen")

    @cuda_safe
    def load_model(
        self,
        model_path: str,
        mmproj_path: Optional[str] = None,
        n_gpu_layers: int = -1,
        n_ctx: int = LLM_CONTEXT_SIZE,
        token_scaling_overrides: Optional["token_scaling.TokenScalingOverrides"] = None,
    ) -> bool:
        """Lädt ein LLM-Modell mit optimierten Einstellungen für RTX4090"""
        if self.is_loading:
            self._log_progress("[WARNING] Modell wird bereits geladen!")
            return False
        
        self.is_loading = True
        self._loading_thread = threading.get_ident()

        try:
            self._log_progress("[INFO] Modell-Dateien werden überprüft...")
            
            if not os.path.exists(model_path):
                self._log_progress(f"[ERROR] Modelldatei nicht gefunden: {model_path}")
                return False

            # Dateigröße prüfen
            model_size = os.path.getsize(model_path) / (1024**3)
            self._log_progress(f"[INFO] Modelldatei: {os.path.basename(model_path)} ({model_size:.2f} GB)")

            self.model_path = model_path
            self.mmproj_path = mmproj_path
            self.is_multimodal = bool(mmproj_path)

            if self.is_multimodal:
                self._log_progress("[INFO] Multimodales Modell erkannt - lade MMPROJ...")
                if not (mmproj_path and os.path.exists(mmproj_path)):
                    self._log_progress(f"[ERROR] MMPROJ-Datei nicht gefunden: {mmproj_path}")
                    return False
                
                try:
                    self._log_progress("[INFO] Initialisiere Chat-Handler (mtmd-Backend, GGUF-Native-Template)...")
                    self.chat_handler = _NativeTemplateVisionChatHandler(
                        clip_model_path=mmproj_path,
                        loader=self,
                    )
                    self._log_progress("[SUCCESS] Chat-Handler erfolgreich initialisiert")
                except Exception as e:
                    self._log_progress(f"[ERROR] Fehler beim Laden des Projektors: {e}")
                    logger.exception("Chat-Handler Fehler")
                    return False
            else:
                self._log_progress("[INFO] Reines Textmodell erkannt")
                self.chat_handler = None

            # GPU-Info prüfen (explizite LLM-GPU via utils.gpu_devices)
            use_gpu = False
            gpu_memory = 0.0
            main_gpu = 0
            try:
                if hasattr(torch, 'cuda') and torch.cuda.is_available():
                    from utils.gpu_devices import get_placement
                    _placement = get_placement()
                    main_gpu = _placement.llm_cuda
                    _llm_gpu = _placement.llm
                    gpu_name = _llm_gpu.name
                    gpu_memory = _llm_gpu.vram_gb
                    self._log_progress(
                        f"[INFO] LLM-GPU: {gpu_name} ({gpu_memory:.1f} GB) auf cuda:{main_gpu}"
                    )
                    use_gpu = True
                    # VRAM-Telemetrie vor dem Load. Die Schätzung ist bewusst nur
                    # informativ: llama.cpp kann Speicher anders verteilen/offloaden,
                    # daher entscheidet erst der reale Kontext-Init über die Kapazität.
                    try:
                        from utils.vram_monitor import get_all_gpu_snapshots

                        _llm_snap = next(
                            (
                                s
                                for s in get_all_gpu_snapshots()
                                if _is_llm_role(s.get("role"))
                            ),
                            None,
                        )
                        if _llm_snap is not None:
                            _free_gb = float(_llm_snap.get("free_gb") or 0.0)
                            # Kapazitätsbewusste Schätzung (2026-08-27): Modellgröße +
                            # KV-Cache(n_ctx) + Compute-Buffers — statt flachem +2.0 GB,
                            # der den KV-Cache großer Modelle bei hohem n_ctx
                            # massiv unterschätzte (27B/30B @ n_ctx=16384: 4–8 GB).
                            # KV-Formel aus GGUF-Metadaten (Single Source of Truth);
                            # Fallback ohne Metadaten bleibt konservativ +2.0 GB.
                            _vram_est = estimate_vram_gb(model_path, model_size, n_ctx)
                            _required_gb = float(_vram_est["required_gb"])
                            self._log_progress(
                                f"[INFO] VRAM-Check LLM-GPU ({_llm_snap.get('name')}): "
                                f"{_free_gb:.1f} GB frei | Modell {model_size:.2f} GB "
                                f"(benötigt ~{_required_gb:.1f} GB inkl. KV-Cache/Buffers)"
                            )
                            if _free_gb < _required_gb:
                                self._log_progress(
                                    f"[WARNING] Geschätzter VRAM-Bedarf übersteigt den freien VRAM: "
                                    f"{_free_gb:.1f} GB frei < ~{_required_gb:.1f} GB benötigt. "
                                    f"Der Ladeversuch wird fortgesetzt; bei einem Kapazitätsfehler "
                                    f"andere GPU-Prozesse schließen oder n_ctx (aktuell {n_ctx}) reduzieren."
                                )
                    except Exception as _vram_check_err:
                        self._log_progress(
                            f"[WARNING] VRAM-Pre-Check fehlgeschlagen: {_vram_check_err} "
                            f"— Ladeversuch wird fortgesetzt"
                        )
                else:
                    self._log_progress("[INFO] Keine GPU verfügbar - CPU-Modus")
                    n_gpu_layers = 0
            except Exception as cuda_err:
                self._log_progress(f"[WARNING] CUDA-Prüfung fehlgeschlagen: {cuda_err}")
                n_gpu_layers = 0

            # ── Token/Context-Skalierung: hardware-bewusster Auto-Vorschlag ──
            # n_ctx wird auf den VRAM-Sweet-Spot gekappt (Vorschlag ≤ requested);
            # KV-Quant, Output-/Thinking-Budget und Reasoning-Effort fließen in
            # die Generierungs-Pfade (utils/token_scaling.py, docs/20).
            # Der OOM-Fallback unten bleibt das Sicherheitsnetz.
            # Nie-feilend: bei Fehlern unverändertes requested n_ctx.
            ts_proposal = None
            try:
                ts_proposal = token_scaling.propose(
                    model_path=model_path,
                    requested_n_ctx=n_ctx,
                    mmproj_path=self.mmproj_path,
                    explicit=token_scaling_overrides,
                )
                self._log_progress(
                    f"[TOKEN-SCALING] n_ctx={ts_proposal.n_ctx} | KV={ts_proposal.kv_quant} | "
                    f"output={ts_proposal.output_budget} | thinking={ts_proposal.thinking_budget} | "
                    f"effort={ts_proposal.reasoning_effort}"
                )
                for _note in ts_proposal.notes:
                    self._log_progress(f"[TOKEN-SCALING] Hinweis: {_note}")
                if ts_proposal.n_ctx < n_ctx:
                    self._log_progress(
                        f"[TOKEN-SCALING] n_ctx gekappt: {n_ctx} → {ts_proposal.n_ctx} (VRAM-Sweet-Spot)"
                    )
                    n_ctx = ts_proposal.n_ctx
            except Exception as ts_err:
                ts_proposal = None
                token_scaling.set_current_proposal(None)
                self._log_progress(
                    f"[WARNING] Token-Skalierungs-Vorschlag fehlgeschlagen: {ts_err} "
                    f"— unverändertes n_ctx={n_ctx}"
                )
            self.token_scaling_proposal = ts_proposal

            # FIX: Korrekte GPU-Layer Logik (-1 = alle auf GPU)
            if n_gpu_layers != 0 and use_gpu:
                # GPU-Modus: RTX4090 Optimierungen
                # With n_gpu_layers=-1 + flash_attn + offload_kqv, CPU work during
                # inference is minimal (sampling, token mgmt — all single-threaded).
                # Excessive n_threads creates idle worker threads in ggml's thread pool
                # that consume CPU via spin-locks between tasks.
                # Optimal: physical_cores / 2 ≈ os.cpu_count() / 4 (assumes SMT).
                # This leaves cores free for concurrent embedding, Docling, Streamlit.
                _n_logical = os.cpu_count() or 16
                
                env_batch = os.environ.get("LLM_N_BATCH")
                env_ubatch = os.environ.get("LLM_N_UBATCH")
                env_threads = os.environ.get("LLM_N_THREADS")
                env_threads_batch = os.environ.get("LLM_N_THREADS_BATCH")
                tuning = select_gpu_tuning_profile(
                    model_path=model_path,
                    n_ctx=n_ctx,
                    gpu_memory_gb=gpu_memory,
                    logical_cores=_n_logical,
                    batch_override=int(env_batch) if env_batch else None,
                    ubatch_override=int(env_ubatch) if env_ubatch else None,
                    threads_override=int(env_threads) if env_threads else None,
                    threads_batch_override=int(env_threads_batch) if env_threads_batch else None,
                )
                optimal_batch = int(tuning["optimal_batch"])
                optimal_ubatch = int(tuning["optimal_ubatch"])
                optimal_threads = int(tuning["optimal_threads"])
                optimal_threads_batch = int(tuning["optimal_threads_batch"])
                optimal_tokens = int(tuning["optimal_tokens"])
                if tuning["batch_source"] == "override":
                    self._log_progress(f"[GPU-OPT] LLM_N_BATCH override aktiv: n_batch={optimal_batch}")
                else:
                    self._log_progress(
                        "[GPU-OPT] Adaptive Prefill-Batching aktiv: "
                        f"base={tuning['base_batch']}, context_cap={tuning['context_cap']}, "
                        f"vram_cap={tuning['vram_cap']} -> n_batch={optimal_batch}"
                    )
                if tuning.get("threads_source") == "override":
                    self._log_progress(f"[GPU-OPT] LLM_N_THREADS override aktiv: n_threads={optimal_threads}")
                if tuning.get("threads_batch_source") == "override":
                    self._log_progress(
                        f"[GPU-OPT] LLM_N_THREADS_BATCH override aktiv: n_threads_batch={optimal_threads_batch}"
                    )
                self._log_progress(
                    "[GPU] RTX4090 GPU-Modus: "
                    f"batch={optimal_batch}, threads={optimal_threads}, threads_batch={optimal_threads_batch}"
                )
            else:
                # CPU-Modus: Ryzen9 5950X Optimierungen
                # 75% logische Kerne = physisch + halbe HT → Sweet-Spot für Matmul
                _n_logical = os.cpu_count() or 16
                optimal_batch = 1024
                optimal_ubatch = 512
                optimal_threads = max(4, int(_n_logical * 0.75))  # 24 on 32-logical
                optimal_threads_batch = optimal_threads
                optimal_tokens = 1024
                self._log_progress(
                    "[CPU] CPU-Modus: "
                    f"batch={optimal_batch}, threads={optimal_threads}, threads_batch={optimal_threads_batch}"
                )

            self._log_progress(f"[INFO] Kontextfenster (n_ctx): {n_ctx}")
            self._log_progress("[INFO] Lade Hauptmodell... (Das kann einige Minuten dauern!)")
            start_time = time.time()
            
            try:
                # ── KV-Quantisierung (Token-Skalierung) ─────────────────────
                # type_k/type_v sind ggml_type-Werte (GGML_TYPE_F16=1,
                # GGML_TYPE_Q8_0=8) — NICHT die Nummerierung von llama_ftype
                # (Details: utils/token_scaling.py, docs/20).
                # flash_attn=True (unten) ist Voraussetzung für KV-Quants.
                _kv_pair = (
                    token_scaling.kv_type_pair(ts_proposal.kv_quant)
                    if ts_proposal is not None
                    else None
                )
                _base_kwargs: Dict[str, Any] = dict(
                    model_path=model_path,
                    chat_handler=self.chat_handler,
                    chat_format=None,
                    n_gpu_layers=n_gpu_layers,
                    main_gpu=main_gpu,
                    # Verhindert Compute-Buffer auf der mit AUX-Modellen belegten zweiten GPU.
                    split_mode=llama_cpp.LLAMA_SPLIT_MODE_NONE,
                    n_ctx=n_ctx,
                    verbose=False,
                    use_mlock=False,
                    use_mmap=True,
                    n_threads=optimal_threads,
                    n_threads_batch=optimal_threads_batch,
                    n_batch=optimal_batch,
                    n_ubatch=optimal_ubatch,
                    last_n_tokens_size=optimal_tokens,
                    logits_all=False,
                    embedding=False,
                    offload_kqv=True,
                    flash_attn=True,
                )
                if _kv_pair is not None:
                    _base_kwargs["type_k"] = _kv_pair[0]
                    _base_kwargs["type_v"] = _kv_pair[1]
                try:
                    self.llm = Llama(**_base_kwargs)
                except Exception as _kv_err:
                    # Sicherheitsnetz: Diese llama.cpp-Build-Version lehnt den
                    # KV-Typ ab (z. B. flash_attn/Quants-Konflikt) → Retry OHNE
                    # type_k/type_v (llama.cpp-Default = f16), nie app-crashen.
                    # NUR bei eindeutig KV-Typ-bezogenen Fehlern; alle anderen
                    # (OOM, Architektur, CUDA, Test-Sentinels) propagieren
                    # unverändert — sonst würde die echte Root Cause verschluckt.
                    if _kv_pair is None:
                        raise
                    if not _looks_like_kv_type_rejection(_kv_err):
                        raise
                    self._log_progress(
                        f"[TOKEN-SCALING] KV-Typ ({_kv_pair[0]}/{_kv_pair[1]}) abgelehnt: "
                        f"{_kv_err} → Retry mit f16-Default"
                    )
                    _base_kwargs.pop("type_k", None)
                    _base_kwargs.pop("type_v", None)
                    self.llm = Llama(**_base_kwargs)
                    if ts_proposal is not None:
                        # TokenScalingProposal ist frozen und notes ist ein Tuple —
                        # Mutation (`.append`) ist unmöglich und würde einen
                        # AttributeError auslösen, der im äußeren except den Load
                        # schlägt, OBWOHL der f16-Retry erfolgreich war.
                        # Stattdessen: neues Proposal-Objekt + Registry-Update.
                        # (Regressionstest: test_kv_type_rejection_triggers_f16_retry)
                        import dataclasses as _dc

                        _kv_note = (
                            "KV-Quant wurde beim Laden abgelehnt (llama.cpp-Build); "
                            "läuft mit f16-KV-Cache."
                        )
                        ts_proposal = _dc.replace(
                            ts_proposal, notes=tuple(ts_proposal.notes) + (_kv_note,)
                        )
                        self.token_scaling_proposal = ts_proposal
                        try:
                            token_scaling.set_current_proposal(ts_proposal)
                        except Exception:
                            # Registry-Update ist optional für diesen Pfad.
                            pass

                load_time = time.time() - start_time
                self._log_progress(f"[SUCCESS] Modell geladen in {load_time:.1f} Sekunden!")
                self._invalidate_token_cache()  # ★ PERF: Neuer Tokenizer → Cache alte Modell-Tokens ungültig

                # LlamaRAMCache: DISABLED by default (LLM_CACHE_BYTES=0).
                #
                # Root-cause rationale:
                #   LlamaRAMCache.save_state() stores self.scores[:n_tokens, :] —
                #   a numpy slice of shape (n_tokens, n_vocab). For Gemma-family
                #   models n_vocab = 262 144. A 4 000-token synthesis prompt
                #   produces a (4000, 262144) float32 array = 4.2 GiB. On the
                #   NEXT call load_state() executes state.scores.copy(), which
                #   must allocate that 4.2 GiB contiguously → MemoryError.
                #
                #   The prefix-reuse benefit (O(N_chunks * delta_tokens) instead
                #   of O(N_chunks * prompt_tokens)) requires exact token-level
                #   prefix matches across calls. Finance synthesis prompts contain
                #   per-call tool outputs; they never share exact prefixes. The
                #   cache therefore provides zero speedup while causing OOM.
                #
                #   Set env LLM_CACHE_BYTES to a positive integer to opt-in,
                #   e.g. for workloads with large, shared, static system prompts.
                cache_bytes = int(os.environ.get("LLM_CACHE_BYTES", "0"))
                if cache_bytes > 0:
                    try:
                        with cuda_lock:
                            self.llm.set_cache(LlamaCache(capacity_bytes=cache_bytes))  # type: ignore[union-attr]
                        self._log_progress(
                            f"[GPU-OPT] LlamaRAMCache aktiviert (capacity={cache_bytes / (1024**3):.1f} GiB)"
                        )
                    except Exception as cache_err:
                        # Strukturelle Fehlkonfiguration -> hart loggen, nicht maskieren.
                        self._log_progress(f"[ERROR] LlamaCache init failed: {cache_err}")
                        raise

                # Vision-CHAT_FORMAT: Wird vom _NativeTemplateVisionChatHandler
                # lazy aus den GGUF-Metadaten gesetzt (siehe __call__).
                # Hier kein expliziter Patch nötig — der Handler nutzt das
                # tokenizer.chat_template als Single Source of Truth.

                # ★ GPU-OPT: Cache n_ctx once at load time — avoid repeated
                # cuda_lock + C FFI calls during inference pipeline.
                try:
                    with cuda_lock:
                        self._cached_n_ctx = self.llm.n_ctx()  # type: ignore[union-attr]
                    self._log_progress(f"[GPU-OPT] n_ctx cached: {self._cached_n_ctx}")
                except Exception:
                    self._cached_n_ctx = n_ctx  # Fallback to requested value
                
                # ── SOTA: VRAM status after LLM load ──────────────────────
                try:
                    from utils.vram_monitor import get_vram_monitor
                    vram = get_vram_monitor()
                    vram.set_runtime_profile(
                        model_family=self._model_family,
                        n_ctx=self._cached_n_ctx,
                        workload="model_loader",
                    )
                    vram.log_status("LLM loaded")
                    vram.check_and_alert()
                except Exception as monitor_exc:
                    # VRAM monitor is optional -- log via torch if available
                    self._log_progress(f"[DEBUG] VRAM monitor unavailable: {monitor_exc}")
                    try:
                        vram_alloc = torch.cuda.memory_allocated() / (1024**3)
                        vram_reserved = torch.cuda.memory_reserved() / (1024**3)
                        self._log_progress(
                            f"[VRAM] Nach LLM-Load: "
                            f"allocated={vram_alloc:.2f} GB, "
                            f"reserved={vram_reserved:.2f} GB"
                        )
                    except Exception as exc:
                        self._log_progress(f"[DEBUG] VRAM fallback monitor unavailable: {exc}")
                
                # Setze current_model_id basierend auf dem Modell-Pfad
                # Versuche aus Pfad zu extrahieren (z.B. "Magistral-Small-2509-Q4_K_M.gguf" → "magistral-small-2509")
                try:
                    model_filename = os.path.basename(model_path)
                    # Entferne Dateierweiterung und Quantisierung
                    model_name = model_filename.replace('.gguf', '').lower()
                    # Entferne Quantisierung wie Q4_K_M, Q5_K_M, Q8_0 etc.
                    import re
                    model_name = re.sub(r'-q\d+(_[a-z0-9]+)*$', '', model_name)
                    self.current_model_id = model_name
                    self._log_progress(f"[INFO] Model-ID gesetzt auf: {model_name}")
                except Exception as name_error:
                    # Fallback: Verwende den gesamten Dateinamen
                    self.current_model_id = os.path.basename(model_path)
                    self._log_progress(f"[WARNING] Model-ID Fallback: {self.current_model_id}")
                
                # ── Model-Family aus Pfad erkennen (falls load_model direkt aufgerufen) ──
                # load_model_by_config() setzt _model_family VOR load_model(),
                # aber bei direktem Aufruf muss es hier erkannt werden.
                if self._model_family == MODEL_FAMILY_MAGISTRAL:
                    # Nur überschreiben wenn noch auf Default → direkt-Aufruf
                    detected = _detect_model_family(model_path)
                    if detected != MODEL_FAMILY_MAGISTRAL:
                        self._model_family = detected
                        self._log_progress(f"[INFO] Model-Family aus Pfad erkannt: {detected}")
                
                # ── Special Tokens auflösen (BOS/EOS/Stop aus GGUF) ──
                self._resolve_special_tokens()
                
                # Test-Inferenz
                self._log_progress("[INFO] Teste Modell...")
                try:
                    llm = self.llm
                    if llm is None:
                        raise RuntimeError("LLM instance missing after successful load")
                    test_response = llm.create_chat_completion(
                        messages=[{"role": "user", "content": "Hi"}],  # type: ignore
                        max_tokens=10
                    )
                    if isinstance(test_response, dict) and "choices" in test_response:
                        self._log_progress("[SUCCESS] Modell-Test erfolgreich!")
                except MemoryError as mem_error:
                    self._log_progress(f"[ERROR] Nicht genügend Speicher: {mem_error}")
                    self.unload_model()
                    return False
                except Exception as test_error:
                    self._log_progress(f"[WARNING] Test fehlgeschlagen: {test_error}")
                
                return True
                
            except Exception as e:
                # Root-Cause-Klassifikation (2026-08-27): "architecture" (Build
                # unterstützt die Architektur nicht — Retry nützt NICHT) vs.
                # "capacity" (VRAM/KV-Cache — Retry mit reduziertem n_ctx sinnvoll)
                # und "cpu_isa" (inkompatible Native-Binary).
                failure_class = _classify_load_failure(e)
                self._log_progress(f"[ERROR] Kritischer Fehler beim Modell-Laden: {e}")
                if failure_class == "architecture":
                    self._log_progress(
                        f"[ERROR] Ursache: Modell-Architektur wird vom installierten "
                        f"llama-cpp-python-Build (v{_llama_cpp_version()}) nicht unterstützt "
                        f"(z.B. Qwen3.5 auf altem llama.cpp). Model-Switch/Retry nützen NICHT — "
                        f"llama-cpp-python mit neuerem llama.cpp neu bauen "
                        f"(CUDA-Build-Pfad: requirements.txt)."
                    )
                elif failure_class == "capacity":
                    self._log_progress(
                        f"[ERROR] Ursache: VRAM-/Kontext-Kapazität reicht nicht "
                        f"(Modell + KV-Cache für n_ctx={n_ctx}). Retry mit reduziertem "
                        f"n_ctx oder nach VRAM-Freigabe (z.B. LM Studio schließen) möglich."
                    )
                elif failure_class == "cpu_isa":
                    self._log_progress(
                        "[ERROR] Ursache: Die llama.cpp-Native-Binary verwendet eine "
                        "von dieser CPU nicht unterstützte Instruktion. Auf Ryzen 5000 "
                        "muss der Build AVX512 deaktivieren (GGML_AVX512=OFF); VRAM-"
                        "Freigabe oder ein kleineres n_ctx beheben diesen Fehler nicht."
                    )
                logger.exception("Modell-Laden fehlgeschlagen")
                # Cleanup - nutze unload_model() statt separate Funktion
                try:
                    self.unload_model()
                except Exception as cleanup_exc:
                    self._log_progress(f"[ERROR] Model cleanup after load failure failed: {cleanup_exc}")
                return False
                
        finally:
            self.is_loading = False
            self._loading_thread = None

    # ── Circuit Breaker / Resilient LLM Call Wrapper (SOTA) ──────────
    # Wraps all LLM inference calls with exponential-backoff retry.
    # Root causes addressed:
    #   1. CUDA OOM on concurrent requests → retry after GC
    #   2. Transient llama.cpp decode errors → retry with jitter
    #   3. Thread contention on cuda_lock → wait + retry
    # NOT addressed by catch-and-ignore: if LLM is truly broken, we
    # raise after max attempts so the caller sees the real error.

    def _resilient_llm_call(self, fn, *args, **kwargs):
        """Execute an LLM inference call with circuit-breaker retry.
        
        Thread-safe: acquires cuda_lock to prevent concurrent llama.cpp
        calls which cause access violations (null pointer dereference).
        
        Uses tenacity exponential backoff with jitter (max 3 attempts).
        On transient CUDA/decode errors, clears cache and retries.
        On persistent errors, raises the original exception.
        
        Args:
            fn: Callable (e.g., self.llm.create_completion)
            *args, **kwargs: Forwarded to fn
            
        Returns:
            Whatever fn returns
        """
        if _TENACITY_AVAILABLE:
            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=0.5, max=4.0, jitter=0.5),
                retry=retry_if_exception_type((RuntimeError, OSError, MemoryError)),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            def _call():
                # ── CRITICAL: Serialize all LLM inference calls ──────────
                # llama.cpp is NOT thread-safe for concurrent inference.
                # Without this lock, background threads (RAG-PERSIST,
                # LLM-KG-extraction) can call the LLM simultaneously with
                # the main agent loop → access violation (0x0000000000000000).
                with cuda_lock:
                    try:
                        return fn(*args, **kwargs)
                    except (RuntimeError, MemoryError) as e:
                        # CUDA OOM or decode error -- clear cache before retry
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                        gc.collect()
                        logger.warning(f"[CIRCUIT-BREAKER] Transient LLM error, retrying: {e}")
                        raise
            
            return _call()
        else:
            # No tenacity -- single attempt with lock
            with cuda_lock:
                return fn(*args, **kwargs)

    def generate_response(
        self,
        prompt: str = "",
        image_path: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        messages: Optional[list] = None,
        # Qualitäts-Parameter für bessere Antworten
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        min_p: float = 0.05,  # SOTA: Minimum-P Sampling -- filtert Tokens < min_p * max_prob
        stop: Optional[list] = None,  # Zusätzliche Stop-Sequenzen (z.B. für JSON-Ende)
        strip_think_blocks: bool = True,  # THINK-Blöcke entfernen (False für Planner!)
        grammar: Any = None,  # SOTA: pre-compiled LlamaGrammar object for constrained decoding
    ) -> str:
        """Generiert eine Antwort vom LLM mit Qualitäts-Optimierungen.

        Args:
            grammar: Optional pre-compiled ``LlamaGrammar`` object
                (e.g. via :meth:`_get_or_compile_grammar` or
                ``LlamaGrammar.from_json_schema(...)``). When set, the
                output is *guaranteed* to conform to the grammar — this
                is the SOTA path for structured/JSON output and the
                only sound fix for mode-collapse loops on degenerate
                inputs that no ``max_tokens`` value can resolve.
        """
        self._llm_call_count += 1
        
        if not self.llm:
            return "Fehler: Modell ist nicht geladen."

        # Bild-Validierung
        valid_image_path = self._validate_image_path(image_path)
        
        # Multimodale Verarbeitung
        if self.is_multimodal and valid_image_path:
            return self._process_multimodal(valid_image_path, prompt, messages, max_tokens, temperature, top_p, top_k, repeat_penalty)
        
        # Text-only Verarbeitung
        return self._process_text_only(prompt, messages, max_tokens, temperature, top_p, top_k, repeat_penalty, stop=stop, min_p=min_p, strip_think_blocks=strip_think_blocks, grammar=grammar)

    def generate_with_tools(
        self,
        messages: list,
        tools: list,
        tool_choice: str = "auto",
        max_tokens: int = 2048,
        temperature: float = 0.3,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        min_p: float = 0.05,  # SOTA: Minimum-P Sampling
    ) -> dict:
        """Generiert eine Antwort mit nativem Function Calling (SOTA Agent-Pattern).
        
        Umgeht den Llava15ChatHandler und nutzt das Magistral-eigene
        Jinja2-Template aus den GGUF-Metadaten direkt. Rendert den Prompt
        mit ``[AVAILABLE_TOOLS]``, ``[TOOL_CALLS]``, ``[ARGS]`` Tokens und
        ruft ``create_completion()`` auf (statt ``create_chat_completion``).
        
        Args:
            messages: Chat-Messages (system, user, assistant, tool)
            tools: OpenAI-kompatible Tool-Definitionen
            tool_choice: "auto" | "none" | "required"
            max_tokens: Maximale Token-Anzahl
            temperature: Sampling-Temperatur
            
        Returns:
            dict: {content, tool_calls, finish_reason, raw}
        """
        self._llm_call_count += 1
        
        if not self.llm:
            return {"content": "Fehler: Modell ist nicht geladen.", "tool_calls": None, "finish_reason": "error", "raw": {}}
        
        # ── Graduated Retry Strategy (SOTA: Root-Cause Fix) ──────────────
        # Root cause of empty LLM responses:
        #   1. Low temperature + GBNF grammar → zero-probability completions
        #   2. Context overflow → prompt_tokens ≈ n_ctx → no room for output
        #   3. Malformed prompt → decoder stalls
        # Fix: escalating temperature + grammar relaxation across attempts.
        # Attempt 1: normal (grammar ON, temp as given)
        # Attempt 2: higher temp, grammar ON
        # Attempt 3: higher temp, grammar OFF (free-text fallback)
        RETRY_CONFIGS = [
            {"temp_delta": 0.0,  "use_grammar": True},
            {"temp_delta": 0.15, "use_grammar": True},
            {"temp_delta": 0.3,  "use_grammar": False},
        ]

        cache_can_be_disabled = False
        previous_cache = None
        cache_was_disabled = False

        try:
            # Older llama_cpp bindings don't support cache_prompt in create_completion.
            # In that case, disable LlamaCache for this tool-call run to avoid
            # expensive load_state score-matrix restores and RAM spikes.
            if not self._supports_cache_prompt():
                set_cache_fn = getattr(self.llm, "set_cache", None)
                if callable(set_cache_fn):
                    cache_can_be_disabled = True
                    previous_cache = getattr(self.llm, "cache", None)
                    if previous_cache is not None:
                        try:
                            with cuda_lock:
                                set_cache_fn(None)
                            cache_was_disabled = True
                            logger.info("[LLM-CACHE] Temporarily disabled cache for tool call completion")
                        except Exception:
                            logger.debug("[LLM-CACHE] Failed to disable cache for tool call", exc_info=True)

            # strftime_now Platzhalter ersetzen (alle Message-Felder bewahren)
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            processed_messages = []
            for msg in messages:
                processed_msg = dict(msg)
                content = processed_msg.get("content", "")
                if isinstance(content, str):
                    processed_msg["content"] = content.replace(
                        "{{ strftime_now('%Y-%m-%d %H:%M:%S') }}", current_time
                    )
                processed_messages.append(processed_msg)
            
            # ── Jinja2-Template aus GGUF-Metadaten rendern (model-agnostic) ──
            effective_tools = tools if tool_choice != "none" else None
            prompt_text = self._render_chat_template(
                processed_messages, effective_tools
            )
            
            # Tokenize mit special=True (für Special-Tokens des jeweiligen Modells)
            # add_bos=False: Template enthält bereits {{ bos_token }},
            # ohne dies würde der Tokenizer ein zweites BOS-Token hinzufügen.
            # ── CRITICAL: cuda_lock protects ALL llama.cpp object access ──
            # tokenize() shares internal C++ state with create_completion;
            # concurrent access causes access-violation (0x48 offset from NULL).
            with cuda_lock:
                tokens = self.llm.tokenize(  # type: ignore[union-attr]
                    prompt_text.encode("utf-8"), add_bos=False, special=True
                )
            # ★ GPU-OPT: Use cached n_ctx instead of lock-protected C call
            n_ctx = self._cached_n_ctx or LLM_CONTEXT_SIZE
            
            # ── Context Overflow Pre-Check (Root Cause #2) ──
            if len(tokens) > n_ctx - 64:
                logger.warning(
                    f"[CONTEXT-OVERFLOW] prompt_tokens={len(tokens)} ≈ n_ctx={n_ctx} "
                    f"-- kürze auf n_ctx-256 um Platz für Output zu schaffen"
                )
                tokens = tokens[: n_ctx - 256]
            
            # ── GBNF Grammar für garantiert valides JSON (SOTA) ──
            # ROOT-CAUSE FIX (was hier vorher stand): Grammar wurde komplett
            # deaktiviert, sobald Tool-Ergebnisse (role="tool") in den Messages
            # standen -- in der Annahme, danach folge immer nur noch eine freie
            # finale Antwort. Das ist in einem ReAct-Loop falsch: nach einem
            # fehlgeschlagenen Tool-Call (Replan, Retry) MUSS das LLM oft einen
            # weiteren, strukturierten Tool-Call absetzen können. Ohne Grammar
            # generierte es dafür Freitext, der dann per fragilem Regex-Fallback
            # (`_extract_single_tool_call`) zu unvollständigen Tool-Calls (fehlende
            # Pflicht-Args) "rekonstruiert" wurde.
            # Die Tool-Call-Grammar erlaubt bereits `free-text | tool-call |
            # mixed-output` (siehe agent/grammars.py) -- sie erzwingt also KEINEN
            # Tool-Call, sondern garantiert nur: WENN einer emittiert wird, ist er
            # syntaktisch valide inkl. aller Pflicht-Argumente. Daher bleibt sie
            # in jeder Iteration aktiv, nicht nur wenn noch keine Tool-Ergebnisse
            # vorliegen.
            grammar_obj = None
            if tool_choice != "none":
                try:
                    from agent.grammars import get_tool_call_grammar
                    tool_names = [t["function"]["name"] for t in tools]
                    grammar_obj = get_tool_call_grammar(
                        tool_names, model_family=self._model_family
                    )
                    if grammar_obj:
                        logger.debug(
                            f"[GBNF] Tool-Call Grammar aktiv für: {tool_names} "
                            f"(family={self._model_family})"
                        )
                except Exception as grammar_err:
                    logger.debug(f"[GBNF] Grammar nicht verfügbar, Fallback auf Free-Text: {grammar_err}")

            # ── Graduated Retry Loop ──
            last_text = ""
            last_finish_reason = "stop"
            last_completion = {}
            for attempt, cfg in enumerate(RETRY_CONFIGS):
                attempt_temp = min(temperature + cfg["temp_delta"], 1.0)
                
                completion_kwargs = dict(
                    prompt=tokens,
                    max_tokens=max_tokens,
                    temperature=attempt_temp,
                    top_p=top_p,
                    top_k=top_k,
                    min_p=min_p,  # SOTA: Minimum-P Sampling
                    repeat_penalty=repeat_penalty,
                    stop=self._stop_sequences,
                )
                if self._supports_cache_prompt():
                    # Tool-Calling erzeugt pro Iteration stark variierende Prompts.
                    # Cache-Replay (load_state) bietet hier kaum Hit-Rate, kann aber
                    # bei langen Prompts grosse score-Matrizen allokieren.
                    completion_kwargs["cache_prompt"] = False
                if cfg["use_grammar"] and grammar_obj is not None:
                    completion_kwargs["grammar"] = grammar_obj  # type: ignore[assignment]
                
                completion: Dict[str, Any] = self._resilient_llm_call(  # type: ignore[assignment]
                    self.llm.create_completion,  # type: ignore[union-attr]
                    **completion_kwargs,
                )
                
                text = (completion["choices"][0].get("text") or "").strip()  # type: ignore[index]
                finish_reason = completion["choices"][0].get("finish_reason", "stop")  # type: ignore[index]
                last_text = text
                last_finish_reason = finish_reason
                last_completion = completion
                
                # Erfolg: Non-empty response
                if text:
                    if attempt > 0:
                        logger.info(
                            f"[RETRY] Erfolg bei Attempt {attempt+1} "
                            f"(temp={attempt_temp:.2f}, grammar={'ON' if cfg['use_grammar'] else 'OFF'})"
                        )
                    break
                
                logger.warning(
                    f"[RETRY] Leere LLM-Antwort bei Attempt {attempt+1}/{len(RETRY_CONFIGS)} "
                    f"(temp={attempt_temp:.2f}, grammar={'ON' if cfg['use_grammar'] else 'OFF'}) "
                    f"-- {'nächster Versuch' if attempt < len(RETRY_CONFIGS)-1 else 'aufgeben'}"
                )
            
            text = last_text
            finish_reason = last_finish_reason
            completion = last_completion
            
            # ── Tool-Calls aus der Rohausgabe parsen (model-agnostic) ──
            # Dispatcher: Gemma native → <|tool_call>call:func..., 
            # Magistral → func_name{json}, Generic → [{"name":...}]
            # mit Cross-Fallback für Robustheit.
            tool_calls = self.recover_tool_calls(text, tools)
            
            if tool_calls:
                # Extract pre-tool-call content (explanatory text before first tool call)
                # Gemma 4: <|tool_call> marker, Magistral: function name
                first_tool_name = tool_calls[0]["function"]["name"]
                # Suche nach dem frühesten Tool-Call-Marker
                markers = [first_tool_name, "<|tool_call>", "[TOOL_CALLS]"]
                first_idx = len(text)
                for marker in markers:
                    idx = text.find(marker)
                    if 0 <= idx < first_idx:
                        first_idx = idx
                pre_content = text[:first_idx].strip() if first_idx > 0 else None
                
                return {
                    "content": pre_content,
                    "tool_calls": tool_calls,
                    "finish_reason": "tool_calls",
                    "raw": completion,
                }
            
            return {
                "content": text,
                "tool_calls": None,
                "finish_reason": finish_reason,
                "raw": completion,
            }
            
        except Exception as e:
            logger.error(f"generate_with_tools fehlgeschlagen: {e}", exc_info=True)
            return {"content": f"[ERROR] Tool-Call fehlgeschlagen: {e}", "tool_calls": None, "finish_reason": "error", "raw": {}}
        finally:
            if cache_can_be_disabled and cache_was_disabled and previous_cache is not None:
                try:
                    with cuda_lock:
                        self.llm.set_cache(previous_cache)  # type: ignore[union-attr]
                    logger.debug("[LLM-CACHE] Restored previous cache after tool call completion")
                except Exception:
                    logger.debug("[LLM-CACHE] Failed to restore previous cache", exc_info=True)

    # ── Model-agnostic Jinja2 Template Rendering ─────────────────────

    _jinja_template = None  # Lazy-Init Cache (invalidated on model switch)

    def _ensure_template_capabilities(self) -> None:
        """Stellt sicher, dass Jinja2-Template + System-Role-Capability initialisiert sind.

        Idempotent: Erster Aufruf lädt das Template aus den GGUF-Metadaten
        und probt die System-Rolle; folgende Aufrufe sind no-ops. Wird vom
        Text-Pfad (``_render_chat_template``) und vom Vision-Pfad
        (``_NativeTemplateVisionChatHandler.__call__``) verwendet, damit
        beide auf demselben Capability-Cache aufsetzen.
        """
        if self._jinja_template is None:
            from jinja2.sandbox import ImmutableSandboxedEnvironment
            import jinja2

            template_str = self.llm.metadata.get(  # type: ignore[union-attr]
                "tokenizer.chat_template", ""
            )
            if not template_str:
                raise RuntimeError("Kein Chat-Template in GGUF-Metadaten gefunden!")

            # Undefined (nicht StrictUndefined!): manche Templates greifen
            # auf optionale Keys wie message['tool_calls'] zu. Mit StrictUndefined
            # crasht das sofort, mit Undefined wird es falsy → Skip.
            env = ImmutableSandboxedEnvironment(undefined=jinja2.Undefined)
            # raise_exception wird von Templates für nicht unterstützte Rollen
            # genutzt -- wir lassen es propagieren und werten es bei der Probe aus.
            env.globals["raise_exception"] = lambda msg: (_ for _ in ()).throw(
                ValueError(msg)
            )
            self._jinja_template = env.from_string(template_str)
            logger.info(
                f"✅ Jinja2 Chat-Template aus GGUF geladen "
                f"(family={self._model_family})"
            )

        if self._supports_system_role is None:
            self._supports_system_role = self._probe_system_role_support()

    def _render_chat_template(
        self,
        messages: list,
        tools: Optional[list],
        enable_thinking: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
        thinking_budget: Optional[int] = None,
    ) -> str:
        """Rendert das Jinja2 Chat-Template aus den GGUF-Metadaten.

        Model-agnostisch: Funktioniert mit Magistral ([INST]/[/INST]),
        Gemma (<start_of_turn>/<end_of_turn>) und jedem anderen Modell,
        dessen GGUF ein ``tokenizer.chat_template`` enthält.

        ``enable_thinking``: Reasoning-Templates (z.B. Qwen3.x) prüfen diese
        Variable; ``False`` erzeugt einen vorbefüllten leeren Think-Block und
        das Modell antwortet direkt. ``None`` lässt die Variable undefined
        (Template-Default, bei Qwen: Thinking an, reasoning_effort=xhigh).
        Templates ohne diese Variable ignorieren sie.

        ``reasoning_effort``: Closed-Set pro Architektur (z.B. xhigh/medium/
        low). ``"off"`` wird NICHT ans Template gepasst -- Thinking-Aus läuft
        über ``enable_thinking=False``.

        ``thinking_budget``: Obergrenze der Thinking-Tokens (Template-
        Variable, z.B. Qwen3.x); nur Werte > 0 werden gepasst.

        SOTA (2026-09-04): Ruft der Caller alle drei Parameter ohne
        expliziten Wert (alle None), übernimmt der Renderer den AKTIVEN
        Token-Scaling-Vorschlag (``utils.token_scaling.current_proposal``):
        Budget + Effort für Reasoning-Modelle, bzw. ``enable_thinking=False``
        für Nicht-Reasoning-Modelle. So erreicht die hardware-bewusste
        Planung automatisch den Generierungs-Pfad. Explizite Caller-Werte
        gewinnen immer (z.B. ``_process_text_only`` bei Utility-Budgets).

        Capability-basiert: Manche Templates (z.B. Gemma) verbieten die
        ``system``-Rolle via ``raise_exception``. Statt dies per Family-Switch
        hart zu kodieren, wird das Template am Load einmal mit einer
        Probe-Message geprüft. Liefert die Probe einen Fehler, werden in
        nachfolgenden Renders alle ``system``-Inhalte deterministisch in den
        ersten ``user``-Turn vorne angefügt -- das ist der von den jeweiligen
        Model-Cards (z.B. Google Gemma) dokumentierte kanonische Weg, kein
        Workaround.

        BOS/EOS-Token werden dynamisch aus ``_resolve_special_tokens()``
        bezogen, nicht hardcodiert.
        """
        self._ensure_template_capabilities()
        assert self._jinja_template is not None  # for type-checker

        normalized_messages = self._normalize_messages_for_chat_template(messages)
        effective_messages = (
            normalized_messages
            if self._supports_system_role
            else self._merge_system_into_first_user(normalized_messages)
        )

        # ── Thinking/Reasoning (SOTA, 2026-09-04) ─────────────────────────
        # Explizite Caller-Werte gewinnen IMMER (z.B. _process_text_only
        # schaltet Thinking bei Utility-Budgets aus, generate_with_grammar
        # erzwingt Thinking-Aus für den Grammar-Pfad). Ruft der Caller
        # alles ohne expliziten Wert, übernimmt der aktive Token-Scaling-
        # Vorschlag Budget + Effort -- die hardware-bewusste Planung erreicht
        # so automatisch den Generierungs-Pfad.
        if (
            enable_thinking is None
            and reasoning_effort is None
            and thinking_budget is None
        ):
            proposal = token_scaling.current_proposal()
            if proposal is not None:
                if (
                    proposal.reasoning_effort == "off"
                    or proposal.thinking_budget <= 0
                ):
                    enable_thinking = False
                else:
                    thinking_budget = int(proposal.thinking_budget)
                    if (
                        proposal.reasoning_effort
                        and proposal.reasoning_effort != "off"
                    ):
                        reasoning_effort = proposal.reasoning_effort

        render_kwargs: Dict[str, Any] = {}
        if enable_thinking is not None:
            render_kwargs["enable_thinking"] = enable_thinking
        # Reasoning-Effort (z.B. Qwen3.x): geschlossene Menge pro Architektur
        # (xhigh/medium/low; "off" wird NICHT ans Template gepasst, sondern
        # über enable_thinking=False gesteuert). Templates ohne
        # reasoning_effort-Variablen ignorieren die Kontext-Variable (Jinja)
        # → harmlos.
        if reasoning_effort is not None and reasoning_effort != "off":
            render_kwargs["reasoning_effort"] = reasoning_effort
        # Thinking-Budget (Template-Variable, z.B. Qwen3.x): nur > 0 passend,
        # sonst Template-Default. Templates ohne Variable: harmlos.
        if thinking_budget is not None and int(thinking_budget) > 0:
            render_kwargs["thinking_budget"] = int(thinking_budget)

        return self._jinja_template.render(
            messages=effective_messages,
            tools=tools,
            add_generation_prompt=True,
            bos_token=self._bos_token,
            eos_token=self._eos_token,
            **render_kwargs,
        )

    def _probe_system_role_support(self) -> bool:
        """Prüft am geladenen Template, ob die ``system``-Rolle akzeptiert wird.

        Rendert das Template einmal mit ``[{system}, {user}]``. Wirft das
        Template ``raise_exception`` (üblich z.B. bei Gemma), liefert die
        Probe ``False``. Das Ergebnis wird in ``_supports_system_role``
        gecacht und in nachfolgenden Renders nur noch ausgewertet.
        """
        if self._jinja_template is None:
            return True
        probe = [
            {"role": "system", "content": "probe"},
            {"role": "user", "content": "probe"},
        ]
        try:
            self._jinja_template.render(
                messages=probe,
                tools=None,
                add_generation_prompt=True,
                bos_token=self._bos_token,
                eos_token=self._eos_token,
            )
            logger.info(
                f"[TEMPLATE-PROBE] system-Rolle wird unterstützt "
                f"(family={self._model_family})"
            )
            return True
        except Exception as e:
            logger.info(
                f"[TEMPLATE-PROBE] system-Rolle wird vom Template abgelehnt "
                f"(family={self._model_family}): {e} -- "
                f"system-Inhalte werden in den ersten user-Turn gemergt."
            )
            return False

    @staticmethod
    def _merge_system_into_first_user(messages: list) -> list:
        """Konsolidiert ``system``-Messages deterministisch in den ersten User-Turn.

        Bewahrt Reihenfolge und Inhalt: alle ``system``-Contents werden in
        ihrer ursprünglichen Reihenfolge konkateniert und dem Content der
        ersten ``user``-Message vorangestellt (durch Doppelte Newlines getrennt).
        Existiert keine User-Message, wird ein neuer User-Turn vorne angefügt.
        Andere Rollen (assistant, tool) bleiben unverändert.
        """
        system_chunks: list = []
        rest: list = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    system_chunks.append(content)
            else:
                rest.append(msg)

        if not system_chunks:
            return rest

        merged_system = "\n\n".join(system_chunks)
        for i, msg in enumerate(rest):
            if msg.get("role") == "user":
                merged = dict(msg)
                user_content = merged.get("content", "")
                if isinstance(user_content, str):
                    merged["content"] = f"{merged_system}\n\n{user_content}".strip()
                else:
                    # Multimodal-Content (list of parts): system als erstes Text-Part voranstellen
                    parts = list(user_content) if isinstance(user_content, list) else []
                    merged["content"] = [{"type": "text", "text": merged_system}, *parts]
                rest[i] = merged
                return rest

        # Keine user-Message → neue erzeugen
        return [{"role": "user", "content": merged_system}, *rest]

    @staticmethod
    def _normalize_messages_for_chat_template(messages: list) -> list:
        """Normalisiert Chat-Messages auf template-kompatible Turn-Sequenzen.

        Root cause der GGUF-Template-Fehler war, dass heterogene Aufrufer rohe
        Message-Stacks direkt bis zum Renderer durchreichen konnten. Viele
        Instruct-Templates (Mistral/Gemma u.a.) verlangen nach der optionalen
        ``system``-Message strikt alternierende ``user``/``assistant``-Turns.

        Diese Normalisierung sitzt bewusst in der Owning-Abstraktion des
        Template-Renders statt in einzelnen Call-Sites:

        - leere/ungueltige Messages werden entfernt
        - mehrere ``system``-Nachrichten am Anfang werden beibehalten
        - aufeinanderfolgende ``user``- oder ``assistant``-Turns werden
          semantisch zusammengefuehrt statt spaeter das Template crashen zu
          lassen
                - Tool-/Tool-Call-Rollen werden in eine template-sichere
                    user/assistant-Sequenz ueberfuehrt, weil der nativen GGUF-
                    Renderer sonst mit "Conversation roles must alternate ..."
                    abbricht
        """
        if not messages:
            return []

        def _coerce_content(content: Any) -> Optional[Any]:
            if isinstance(content, str):
                stripped = content.strip()
                return stripped or None
            if isinstance(content, list):
                parts = [part for part in content if part]
                return parts or None
            if content is None:
                return None
            text = str(content).strip()
            return text or None

        def _merge_content(existing: Any, new_value: Any) -> Any:
            if isinstance(existing, str) and isinstance(new_value, str):
                return f"{existing}\n\n{new_value}" if existing else new_value
            if isinstance(existing, list) and isinstance(new_value, list):
                return [*existing, *new_value]
            if isinstance(existing, list):
                return [*existing, {"type": "text", "text": str(new_value)}]
            if isinstance(new_value, list):
                merged = []
                if isinstance(existing, str) and existing:
                    merged.append({"type": "text", "text": existing})
                merged.extend(new_value)
                return merged
            return f"{existing}\n\n{new_value}" if existing else new_value

        normalized: list = []
        conversation_roles = {"user", "assistant"}
        passthrough_roles = {"tool", "tool_results", "assistant_tool_call", "tool_response"}

        def _tool_prefix(role_name: str) -> str:
            if role_name == "tool_results":
                return "Tool-Ergebnis"
            if role_name == "assistant_tool_call":
                return "Tool-Aufruf"
            if role_name == "tool_response":
                return "Tool-Antwort"
            return "Tool"

        def _fold_passthrough_message(role_name: str, content_value: Any) -> Dict[str, Any]:
            folded_content = content_value
            if isinstance(content_value, str):
                folded_content = f"[{_tool_prefix(role_name)}]\n{content_value}".strip()
            elif isinstance(content_value, list):
                folded_content = [
                    {"type": "text", "text": f"[{_tool_prefix(role_name)}]"},
                    *content_value,
                ]
            else:
                folded_content = f"[{_tool_prefix(role_name)}]\n{content_value}"
            return {"role": "assistant", "content": folded_content}

        for raw_message in messages:
            if not isinstance(raw_message, dict):
                continue

            role = str(raw_message.get("role") or "").strip()
            if not role:
                continue

            content = _coerce_content(raw_message.get("content"))
            if content is None and role not in passthrough_roles:
                continue

            message = dict(raw_message)
            if content is not None:
                message["content"] = content

            if role == "system":
                if normalized and normalized[-1].get("role") != "system":
                    # Non-leading system turns must be folded into the preceding
                    # conversation turn to preserve strict user/assistant alternation.
                    # The content is prepended with a system prefix so semantic
                    # meaning is retained.
                    system_text = message.get("content", "")
                    folded = {"type": "text", "text": f"[System] {system_text}"}
                    normalized[-1] = {
                        **normalized[-1],
                        "content": _merge_content(normalized[-1].get("content"), folded),
                    }
                else:
                    normalized.append(message)
                continue

            if role in passthrough_roles:
                folded_message = _fold_passthrough_message(role, message.get("content"))
                if normalized and normalized[-1].get("role") == "assistant":
                    normalized[-1] = {
                        **normalized[-1],
                        "content": _merge_content(
                            normalized[-1].get("content"), folded_message.get("content")
                        ),
                    }
                else:
                    normalized.append(folded_message)
                continue

            if role not in conversation_roles:
                # Unknown roles cannot appear in the chat template - fold them
                # into the preceding conversation turn to preserve strict
                # user/assistant alternation required by all model templates.
                folded_text = f"[{role}] {message.get('content', '')}" if message.get('content') else f"[{role}]"
                folded_entry = {"type": "text", "text": folded_text}
                if normalized:
                    normalized[-1] = {
                        **normalized[-1],
                        "content": _merge_content(normalized[-1].get("content"), folded_entry),
                    }
                else:
                    # If nothing to fold into, treat as assistant turn
                    normalized.append({"role": "assistant", "content": folded_text})
                continue

            has_conversation_turn = any(
                existing.get("role") in conversation_roles for existing in normalized
            )
            if role == "assistant" and not has_conversation_turn:
                logger.debug(
                    "[CHAT-TEMPLATE] Dropping orphan leading assistant turn before first user"
                )
                continue

            # Determine the last non-system conversation role to correctly detect
            # consecutive same-role turns. System messages in the middle are already
            # folded away, but we still need to skip them when looking for the
            # previous role to avoid false positives.
            previous_role = None
            for candidate in reversed(normalized):
                cr = candidate.get("role")
                if cr in conversation_roles:
                    previous_role = cr
                    break

            if previous_role == role:
                # Merge into the last conversation turn with the same role,
                # skipping any intervening non-conversation roles.
                for i in range(len(normalized) - 1, -1, -1):
                    if normalized[i].get("role") == role:
                        normalized[i] = {
                            **normalized[i],
                            "content": _merge_content(
                                normalized[i].get("content"), message.get("content")
                            ),
                        }
                        break
                continue

            normalized.append(message)

        return normalized


    @staticmethod
    def _parse_magistral_tool_calls(
        text: str, tools: list
    ) -> Optional[list]:
        """Parst Magistral Tool-Call-Format aus der Rohausgabe.
        
        Magistral generiert ``[TOOL_CALLS]func[ARGS]{"k":"v"}`` wobei
        die Special-Tokens vom Decoder gestrippt werden. Ergebnis-Format
        im Text: ``func_name{"key":"val"}`` (ggf. mehrere hintereinander).
        
        **ROBUST**: Findet Tool-Calls überall im Text, auch wenn der LLM
        erklärenden Text voranstellt (z.B. "Ich suche jetzt...web_search{...}").
        
        Returns:
            Liste von Tool-Call-Dicts im OpenAI-Format oder None.
        """
        if not text:
            return None
        
        known_tools = {t["function"]["name"] for t in tools}
        tool_calls: list = []
        call_id = 0
        
        # Scan the entire text for tool calls at any position
        search_text = text.strip()
        pos = 0
        
        while pos < len(search_text):
            # Find the earliest tool name occurrence from current position
            earliest_match = None  # (position, tool_name)
            
            for tool_name in known_tools:
                idx = search_text.find(tool_name, pos)
                if idx != -1:
                    if earliest_match is None or idx < earliest_match[0]:
                        earliest_match = (idx, tool_name)
            
            if earliest_match is None:
                break  # No more tool names found
            
            match_pos, tool_name = earliest_match
            after_name_pos = match_pos + len(tool_name)
            
            # Check if followed by JSON object (possibly with whitespace)
            after_name = search_text[after_name_pos:].lstrip()
            whitespace_skipped = len(search_text[after_name_pos:]) - len(after_name)
            
            if after_name.startswith("{"):
                # Extract JSON object via brace-matching
                depth = 0
                end_idx = 0
                in_string = False
                escape_next = False
                
                for i, c in enumerate(after_name):
                    if escape_next:
                        escape_next = False
                        continue
                    if c == '\\' and in_string:
                        escape_next = True
                        continue
                    if c == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
                
                if end_idx > 0:
                    args_json = after_name[:end_idx]
                    
                    # Validate it's actually parseable JSON
                    try:
                        json.loads(args_json)
                    except (json.JSONDecodeError, ValueError):
                        # Not valid JSON -- skip this occurrence
                        pos = match_pos + len(tool_name)
                        continue
                    
                    tool_calls.append({
                        "id": f"call_{call_id}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": args_json,
                        },
                    })
                    call_id += 1
                    
                    # Continue scanning after this tool call
                    pos = after_name_pos + whitespace_skipped + end_idx
                    continue
            
            elif after_name.startswith("["):
                # ── ROBUST: Handle tool_name["key": "val"] bracket syntax ──
                # LLMs sometimes generate Python dict-indexing syntax instead of JSON.
                # Example: rag_search["query": "test"] → convert to {"query": "test"}
                _json_mod = json  # alias for local use
                bracket_depth = 0
                bracket_end = 0
                b_in_string = False
                b_escape = False
                
                for i, c in enumerate(after_name):
                    if b_escape:
                        b_escape = False
                        continue
                    if c == '\\' and b_in_string:
                        b_escape = True
                        continue
                    if c == '"' and not b_escape:
                        b_in_string = not b_in_string
                        continue
                    if b_in_string:
                        continue
                    if c == '[':
                        bracket_depth += 1
                    elif c == ']':
                        bracket_depth -= 1
                    if bracket_depth == 0:
                        bracket_end = i + 1
                        break
                
                if bracket_end > 1:
                    inner = after_name[1:bracket_end - 1].strip()
                    # Only convert if it looks like key-value pairs (contains ":")
                    # not array indexing like [0] or ["key"]
                    if inner and '":' in inner:
                        args_json_candidate = "{" + inner + "}"
                        try:
                            _json_mod.loads(args_json_candidate)
                            tool_calls.append({
                                "id": f"call_{call_id}",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_json_candidate,
                                },
                            })
                            call_id += 1
                            logger.info(
                                f"[PARSE] Bracket-Syntax korrigiert: "
                                f"{tool_name}[...] → {tool_name}{{...}}"
                            )
                            pos = after_name_pos + whitespace_skipped + bracket_end
                            continue
                        except (ValueError, _json_mod.JSONDecodeError):
                            pass  # Not convertible -- fall through
            
            # Tool name found but no valid JSON follows -- skip past it
            pos = match_pos + len(tool_name)
        
        return tool_calls if tool_calls else None

    @staticmethod
    def _parse_gemma4_native_tool_calls(
        text: str, tools: list
    ) -> Optional[list]:
        """Parst Gemma 4 native Tool-Calls aus der GGUF-Template-Ausgabe.
        
        Gemma 4 generiert Tool-Calls in einem eigenen Proto-Text-Format
        (kein JSON!), das von dem GGUF-Template vorgegeben wird:
        
            ``<|tool_call>call:func_name\\nkey:<|"|>value<|"|>\\nkey2:<|"|>value2<|"|>}<tool_call|>``
        
        Mehrere Tool-Calls: Mehrere ``<|tool_call>...<tool_call|>`` Blöcke.
        
        Die ``<|"|>``-Marker sind String-Literal-Quotes im Gemma-4-Vokabular.
        Numerische Werte erscheinen ohne Quotes: ``key:42``.
        
        Returns:
            Liste von Tool-Call-Dicts im OpenAI-Format oder None.
        """
        if not text:
            return None
        
        import re as _re
        known_tools = {t["function"]["name"] for t in tools}
        tool_calls: list = []
        call_id = 0
        
        # Finde alle <|tool_call>...<tool_call|> Blöcke
        pattern = r'<\|tool_call>(.*?)<tool_call\|>'
        for match in _re.finditer(pattern, text, flags=_re.DOTALL):
            block = match.group(1).strip()
            
            # Extrahiere Funktionsname: "call:FUNC_NAME\n..."
            call_match = _re.match(r'call:(\w+)\s*\n?(.*)', block, flags=_re.DOTALL)
            if not call_match:
                continue
            
            func_name = call_match.group(1)
            if func_name not in known_tools:
                continue
            
            params_block = call_match.group(2).strip()
            if params_block.endswith('}'):
                params_block = params_block[:-1].strip()

            def _clean_value(raw: str) -> str:
                val = (raw or "").strip()
                val = val.replace('<|"|>', '').strip()
                val = val.rstrip(',').rstrip(')').rstrip('}').strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                return val.strip()

            arguments: dict = {}
            key_matches = list(_re.finditer(r'(\w+)\s*:', params_block))
            for idx, key_match in enumerate(key_matches):
                key = key_match.group(1)
                val_start = key_match.end()
                val_end = (
                    key_matches[idx + 1].start()
                    if idx + 1 < len(key_matches)
                    else len(params_block)
                )
                raw_val = _clean_value(params_block[val_start:val_end])
                if raw_val == "":
                    continue

                if raw_val.lower() in ('true', 'false'):
                    arguments[key] = raw_val.lower() == 'true'
                    continue
                try:
                    arguments[key] = int(raw_val)
                    continue
                except ValueError:
                    pass
                try:
                    arguments[key] = float(raw_val)
                    continue
                except ValueError:
                    pass
                arguments[key] = raw_val
            
            tool_calls.append({
                "id": f"call_{call_id}",
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": json.dumps(arguments),
                },
            })
            call_id += 1
            logger.info(
                f"[PARSE] Gemma 4 native Tool-Call: {func_name}({arguments})"
            )
        
        return tool_calls if tool_calls else None

    @staticmethod
    def _parse_generic_json_tool_calls(
        text: str, tools: list
    ) -> Optional[list]:
        """Parst JSON-basierte Tool-Calls (Gemma, GPT, generisches Format).
        
        Gemma 4 generiert Tool-Calls als JSON-Array oder -Objekt:
          - ``[{"name":"func","arguments":{"k":"v"}}]``
          - ``{"name":"func","arguments":{"k":"v"}}``
        
        Funktioniert auch wenn der LLM Text vor/nach dem JSON generiert.
        
        Returns:
            Liste von Tool-Call-Dicts im OpenAI-Format oder None.
        """
        if not text:
            return None
        
        known_tools = {t["function"]["name"] for t in tools}
        
        # Versuche zuerst das gesamte Ergebnis als JSON zu parsen
        clean = text.strip()
        
        # Finde JSON-Array oder -Objekt im Text (auch mit umgebendem Text)
        parsed_items = []
        
        # Strategie 1: Ganzer Text ist JSON
        for candidate in [clean]:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, list):
                    parsed_items = obj
                elif isinstance(obj, dict):
                    parsed_items = [obj]
                break
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Strategie 2: Finde JSON-Array [...] oder -Objekt {...} im Text
        if not parsed_items:
            import re as _re
            # Finde äußerstes [...] oder {...}
            for pattern in [r'\[[\s\S]*\]', r'\{[\s\S]*\}']:
                match = _re.search(pattern, clean)
                if match:
                    try:
                        obj = json.loads(match.group())
                        if isinstance(obj, list):
                            parsed_items = obj
                        elif isinstance(obj, dict):
                            parsed_items = [obj]
                        break
                    except (json.JSONDecodeError, ValueError):
                        continue
        
        if not parsed_items:
            return None
        
        # Konvertiere in OpenAI-Format
        tool_calls: list = []
        call_id = 0
        for item in parsed_items:
            if not isinstance(item, dict):
                continue
            # Unterstütze "name"+"arguments" und "function"+"arguments"
            fname = item.get("name") or (item.get("function", {}) or {}).get("name")
            args = item.get("arguments") or item.get("parameters") or {}
            
            if not fname or fname not in known_tools:
                continue
            
            args_str = json.dumps(args) if isinstance(args, dict) else str(args)
            tool_calls.append({
                "id": f"call_{call_id}",
                "type": "function",
                "function": {
                    "name": fname,
                    "arguments": args_str,
                },
            })
            call_id += 1
        
        return tool_calls if tool_calls else None

    def _parse_tool_calls(self, text: str, tools: list) -> Optional[list]:
        """Model-agnostischer Tool-Call-Parser (Dispatcher).
        
        Routet basierend auf ``_model_family`` zum passenden Parser:
        - Magistral: ``func_name{"key":"val"}`` (Special-Token-Format)
        - Gemma 4 nativ: ``<|tool_call>call:func\\nkey:<|"|>val<|"|>}<tool_call|>``
        - Gemma/Generic: JSON-Array/Objekt ``[{"name":"func","arguments":{}}]``
        
        Cascade mit Cross-Fallback pro Family für maximale Robustheit.
        """
        if self._model_family == MODEL_FAMILY_MAGISTRAL:
            result = self._parse_magistral_tool_calls(text, tools)
            if result:
                return result
            # Fallback: Vielleicht hat das Modell JSON-Format generiert
            return self._parse_generic_json_tool_calls(text, tools)
        else:
            # Gemma: Natives Proto-Format zuerst (höchste Präzision)
            result = self._parse_gemma4_native_tool_calls(text, tools)
            if result:
                return result
            # Fallback 1: JSON-Format
            result = self._parse_generic_json_tool_calls(text, tools)
            if result:
                return result
            # Fallback 2: Magistral-Style (func_name{args})
            return self._parse_magistral_tool_calls(text, tools)

    def recover_tool_calls(self, text: str, tools: list) -> Optional[list]:
        """Robuste, parser-zentrierte Recovery fuer Tool-Calls.

        Der Ablauf ist strikt strukturell und modellagnostisch:
        1) Primaerer Family-Parser (``_parse_tool_calls``)
        2) Normalisierung typischer Wrapper-Syntax (z.B. ``tool[arg:1]``)
        3) Minimal-Extraktion eines einzelnen Calls als letzter Ausweg
        """
        parsed = self._parse_tool_calls(text, tools)
        if parsed:
            return parsed

        normalized = self._normalize_tool_call_text(text, tools)
        if normalized != (text or ""):
            parsed = self._parse_tool_calls(normalized, tools)
            if parsed:
                return parsed

        return self._extract_single_tool_call(text, tools)

    @staticmethod
    def _normalize_tool_call_text(text: str, tools: list) -> str:
        """Normalisiert leichte Formatdefekte vor dem Parser-Lauf."""
        if not text:
            return ""

        import re as _re

        known_tools = {
            str(t.get("function", {}).get("name") or "")
            for t in (tools or [])
            if isinstance(t, dict)
        }
        known_tools.discard("")
        if not known_tools:
            return text

        # Pattern: "tool_name[ key:value, ... ]" -> "tool_name{ key:value, ... }"
        tool_group = "|".join(_re.escape(tn) for tn in sorted(known_tools, key=len, reverse=True))
        m = _re.search(rf"\b({tool_group})\s*\[(.+)$", text.strip(), flags=_re.DOTALL)
        if not m:
            return text

        name = m.group(1)
        rest = (m.group(2) or "").strip().rstrip().rstrip("]) ").strip()
        if rest and not rest.startswith("{"):
            rest = "{" + rest
        if rest and not rest.endswith("}"):
            rest = rest + "}"
        return f"{name}{rest}"

    @staticmethod
    def _extract_single_tool_call(text: str, tools: list) -> Optional[list]:
        """Letzter struktureller Ausweg: extrahiere einen einzelnen Tool-Call."""
        if not text:
            return None

        import re as _re

        known_tools = {
            str(t.get("function", {}).get("name") or "")
            for t in (tools or [])
            if isinstance(t, dict)
        }
        known_tools.discard("")
        if not known_tools:
            return None

        tool_group = "|".join(_re.escape(tn) for tn in sorted(known_tools, key=len, reverse=True))
        m = _re.search(rf"\b({tool_group})\s*[\[(]\s*(.+)$", text.strip(), flags=_re.DOTALL)
        if not m:
            return None

        tool_name = m.group(1)
        raw_args = (m.group(2) or "").strip()
        if not raw_args:
            args = {}
        else:
            candidate = raw_args.rstrip().rstrip("]) ").strip()
            candidate = _re.sub(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", r'"\1":', candidate)
            if not candidate.lstrip().startswith("{"):
                candidate = "{" + candidate
            if not candidate.rstrip().endswith("}"):
                candidate = candidate + "}"
            try:
                args = json.loads(candidate)
                if not isinstance(args, dict):
                    args = {}
            except (json.JSONDecodeError, ValueError):
                args = {}

        # ROOT-CAUSE FIX: Ohne Pflichtparameter ist der Call nicht ausführbar
        # (z.B. web_search ohne "query") -- ein Dummy-Call mit leeren Args
        # führt nur zu einem garantierten Tool-Fehlschlag. Lieber gar keinen
        # Call zurückgeben, damit der Agent regulär (LLM-Textantwort/Replan)
        # weiterlaufen kann statt einen sinnlosen Call auszuführen.
        schema = next(
            (t for t in (tools or [])
             if isinstance(t, dict) and t.get("function", {}).get("name") == tool_name),
            None,
        )
        required = (schema or {}).get("function", {}).get("parameters", {}).get("required", [])
        missing = [p for p in required if not str(args.get(p, "")).strip()]
        if missing:
            return None

        return [{
            "id": "recovered_call_0",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            },
        }]

    def _validate_image_path(self, image_path: Optional[str]) -> Optional[str]:
        """Validiert einen Bildpfad"""
        if image_path is None:
            return None
        image_path_str = str(image_path).strip()
        if not image_path_str or image_path_str == "None":
            return None
        if os.path.exists(image_path_str):
            return image_path_str
        return None
    
    def _process_multimodal(self, image_path: str, prompt: str, messages: Optional[list], 
                            max_tokens: int, temperature: float, top_p: float, top_k: int, repeat_penalty: float) -> str:
        """Verarbeitet multimodale Anfragen (Bild + Text)"""
        try:
            import urllib.parse
            import urllib.request
            
            normalized_path = os.path.abspath(image_path)
            file_url = urllib.parse.urljoin('file:', urllib.request.pathname2url(normalized_path))
            
            # User-Content extrahieren
            user_content = prompt or "Beschreibe dieses Bild."
            if messages:
                for message in messages:
                    if message.get("role") == "user":
                        content = message.get("content", "")
                        if isinstance(content, str) and content:
                            user_content = content
                            break
                        elif isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    user_content = item.get("text", user_content)
                                    break
                            break
            
            # Multimodale Message erstellen
            content = [
                {"type": "image_url", "image_url": {"url": file_url}},
                {"type": "text", "text": user_content}
            ]
            
            response = self._resilient_llm_call(
                self.llm.create_chat_completion,  # type: ignore[union-attr]
                messages=[{"role": "user", "content": content}],  # type: ignore
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repeat_penalty=repeat_penalty
            )
            
            if isinstance(response, dict) and "choices" in response:
                choice = response["choices"][0]
                # Try standard message format first
                if "message" in choice and isinstance(choice["message"], dict):
                    content = choice["message"].get("content")
                    if content:
                        return str(content).strip()
                # Fallback: check if choice itself has content (non-standard format)
                # Note: This is for compatibility with non-standard response formats
                if isinstance(choice, dict) and "text" in choice:
                    text_content = choice.get("text")  # Use .get() instead of direct access
                    if text_content:
                        return str(text_content).strip()
            return str(response).strip()
            
        except Exception as e:
            error_msg = str(e)
            if "tokenize" in error_msg.lower():
                return f"Tokenization-Fehler beim Bild '{os.path.basename(image_path)}'. Bitte anderes Bild/Format versuchen."
            return f"Fehler bei der Bildanalyse: {error_msg}"
    
    @staticmethod
    def _strip_reasoning_markup(text: str, strip_think_blocks: bool = True) -> str:
        """Entfernt modell-spezifische Reasoning-Markup aus LLM-Output.

        strip_think_blocks=True: Reasoning-INHALT wird entfernt (Chat-Antworten).
        strip_think_blocks=False: nur die Tags werden entfernt, Inhalt bleibt
        (Planner wertet den Think-Inhalt aus).
        """
        import re as _re

        if strip_think_blocks:
            # Magistral: [THINK]...[/THINK]
            text = _re.sub(
                r'\[THINK\].*?\[/THINK\]\s*', '', text,
                flags=_re.DOTALL | _re.IGNORECASE
            ).strip()
            # Gemma 4: <|channel>thought\n...<channel|>
            text = _re.sub(
                r'<\|channel>thought\n.*?<channel\|>\s*', '', text,
                flags=_re.DOTALL
            ).strip()
            # Gemma/Generic: <think>...</think>
            text = _re.sub(
                r'<think>.*?</think>\s*', '', text,
                flags=_re.DOTALL | _re.IGNORECASE
            ).strip()
            # Qwen3.x: Template befüllt <think> bereits im Prompt vor — der
            # Output enthält nur das schließende Tag; alles davor ist Reasoning.
            if _re.search(r'</think>', text, _re.IGNORECASE) and not _re.search(
                r'<think>', text, _re.IGNORECASE
            ):
                text = _re.split(
                    r'</think>\s*', text, maxsplit=1, flags=_re.IGNORECASE
                )[-1].strip()
        else:
            text = _re.sub(r'\[/?THINK\]', '', text, flags=_re.IGNORECASE).strip()
            text = _re.sub(r'<\|channel>thought\n?|<channel\|>', '', text).strip()
            text = _re.sub(r'</?think>', '', text, flags=_re.IGNORECASE).strip()
        return text

    def _process_text_only(self, prompt: str, messages: Optional[list], max_tokens: int, 
                           temperature: float, top_p: float, top_k: int, repeat_penalty: float,
                           stop: Optional[list] = None, min_p: float = 0.05,
                           strip_think_blocks: bool = True,
                           grammar: Any = None) -> str:
        """Verarbeitet Text-only Anfragen mit korrektem Chat-Template (model-agnostic).
        
        Nutzt _render_chat_template() + create_completion() um den
        Llava15ChatHandler zu umgehen, der ein statisches CHAT_FORMAT anwendet.
        Das GGUF-eingebettete Jinja2-Template wird direkt gerendert und passt
        sich automatisch an die Model-Family an (Magistral, Gemma etc.).
        """
        try:
            if messages:
                # Template-Variablen in Message-Content ersetzen
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                chat_messages = []
                for message in messages:
                    role = message.get("role", "")
                    content = message.get("content", "")
                    content = content.replace("{{ strftime_now('%Y-%m-%d %H:%M:%S') }}", current_time)
                    chat_messages.append({"role": role, "content": content})
            else:
                chat_messages = [{"role": "user", "content": prompt}]
            
            # ── Chat-Template aus GGUF-Metadaten rendern (model-agnostic) ──
            # Umgeht Llava15ChatHandler → korrekte modell-spezifische Formatierung,
            # kein falsches Vicuna USER:/ASSISTANT: Wrapping, kein Double-BOS.
            # Reasoning-Budget: Bei kleinen Antwortbudgets würde ein Reasoning-
            # Modell (Qwen3.x, Default xhigh) das gesamte max_tokens mit <think>
            # verbrauchen → finish_reason=length und leere Antwort nach dem
            # Think-Stripping. Utility-Aufrufe (strip_think_blocks=True) mit
            # Budget < 512 deaktivieren Thinking daher über das Template.
            enable_thinking = None
            if strip_think_blocks and max_tokens < 512:
                enable_thinking = False
            prompt_text = self._render_chat_template(
                chat_messages, tools=None, enable_thinking=enable_thinking
            )
            
            # Tokenize: add_bos=False weil Template bereits {{ bos_token }} enthält
            # ── CRITICAL: cuda_lock protects ALL llama.cpp object access ──
            with cuda_lock:
                tokens = self.llm.tokenize(  # type: ignore[union-attr]
                    prompt_text.encode("utf-8"), add_bos=False, special=True
                )
            # ★ GPU-OPT: Use cached n_ctx instead of lock-protected C call
            n_ctx = self._cached_n_ctx or LLM_CONTEXT_SIZE
            
            # ── Context Overflow Pre-Check ──
            if len(tokens) > n_ctx - 64:
                logger.warning(
                    f"[CONTEXT-OVERFLOW] _process_text_only: prompt_tokens={len(tokens)} "
                    f"≈ n_ctx={n_ctx} -- kürze auf n_ctx-256"
                )
                tokens = tokens[: n_ctx - 256]
            
            # max_tokens an verfügbaren Platz anpassen
            available_tokens = n_ctx - len(tokens)
            effective_max_tokens = min(max_tokens, max(available_tokens - 32, 128))
            if effective_max_tokens < max_tokens:
                logger.warning(
                    f"[CONTEXT] max_tokens {max_tokens} → {effective_max_tokens} "
                    f"(prompt={len(tokens)}, n_ctx={n_ctx}, available={available_tokens})"
                )
            else:
                logger.info(
                    f"[CONTEXT] max_tokens={effective_max_tokens} "
                    f"(prompt={len(tokens)}, n_ctx={n_ctx}, available={available_tokens})"
                )
            
            # create_completion statt create_chat_completion
            # → umgeht Llava15ChatHandler, nutzt modell-spezifisches Template
            # Stop-Sequenzen: model-agnostic aus _resolve_special_tokens
            stop_sequences = list(self._stop_sequences)
            if stop:
                stop_sequences.extend(stop)
            
            response = self._resilient_llm_call(
                self.llm.create_completion,  # type: ignore[union-attr]
                prompt=tokens,
                max_tokens=effective_max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                min_p=min_p,  # SOTA: Minimum-P Sampling
                repeat_penalty=repeat_penalty,
                stop=stop_sequences,
                grammar=grammar,  # SOTA: constrained decoding when caller passes one
            )
            
            # Response parsen (Completion Format: choices[].text)
            if isinstance(response, dict) and "choices" in response:
                finish_reason = response["choices"][0].get("finish_reason", "unknown")
                completion_tokens = response.get("usage", {}).get("completion_tokens", "?")
                logger.info(
                    f"[LLM-COMPLETION] finish_reason={finish_reason}, "
                    f"completion_tokens={completion_tokens}, "
                    f"max_tokens={effective_max_tokens}"
                )
                if finish_reason == "length":
                    logger.warning(
                        f"[TRUNCATION] LLM output was truncated by max_tokens! "
                        f"completion_tokens={completion_tokens}, effective_max_tokens={effective_max_tokens}"
                    )
                text = (response["choices"][0].get("text") or "").strip()
                if text:
                    # ═══ SAFETY NET: Strip think blocks from responses ═══
                    # AUSNAHME: Planner braucht THINK-Inhalt (strip_think_blocks=False)
                    text = self._strip_reasoning_markup(text, strip_think_blocks)
                    if text:
                        return text
            # Empty completion (e.g. truncated by max_tokens before any
            # token was emitted). Surface this as an *empty* response so
            # callers retry / raise on real signal — never leak the raw
            # completion dict's repr (which historically caused
            # downstream JSON parsers to fail mysteriously on
            # ``{'id': '...', 'logprobs': None, ...}``).
            return ""
            
        except OSError as os_error:
            if "access violation" in str(os_error).lower():
                logger.error(f"Memory access violation: {os_error}")
                self.unload_model()
                return "[ERROR] Modell-Speicherfehler. Bitte Modell neu laden."
            return f"[ERROR] System-Fehler: {os_error}"
        except Exception as e:
            logger.error(f"Response-Generierung fehlgeschlagen: {e}")
            if "decode" in str(e).lower() or "access violation" in str(e).lower():
                self.unload_model()
                return "[ERROR] Modell-Fehler. Bitte Modell neu laden."
            return f"Fehler: {e}"

    # ── GBNF Grammar Cache ───────────────────────────────────────────────
    # ROOT-CAUSE FIX for GPU pipeline stalls: LlamaGrammar.from_string()
    # is a C++ parsing/compilation pass that takes ~1-5ms per call.
    # During KG extraction, the SAME grammar string is used for every
    # chunk (30-50 chunks), wasting 30-250ms on repeated compilations.
    # Cache: grammar_str hash → compiled LlamaGrammar object.
    _grammar_cache: Dict[int, Any] = {}  # class-level, shared across calls
    
    def _get_or_compile_grammar(self, grammar_str: str):
        """Return a cached LlamaGrammar object for the given GBNF string.
        
        Compiles the grammar ONCE and caches it by string hash.
        Subsequent calls with the same grammar_str return instantly.
        
        Returns:
            LlamaGrammar object, or None if compilation fails.
        """
        cache_key = hash(grammar_str)
        if cache_key in self._grammar_cache:
            return self._grammar_cache[cache_key]
        
        try:
            from llama_cpp import LlamaGrammar
            grammar_obj = LlamaGrammar.from_string(grammar_str)
            self._grammar_cache[cache_key] = grammar_obj
            logger.debug(f"[GRAMMAR-CACHE] Compiled and cached new grammar (hash={cache_key})")
            return grammar_obj
        except Exception as ge:
            logger.debug(f"[GRAMMAR] Kompilierung fehlgeschlagen: {ge}")
            return None
    
    def generate_with_grammar(
        self,
        messages: list,
        grammar_str: str,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        """Generiert mit GBNF-Grammar-Constraint (garantiert valide Syntax).
        
        Nutzt create_chat_completion mit grammar-Enforcement.
        Ideal für JSON-Outputs (Reflection, CRAG, Compression-Routing).
        
        ★ GPU-OPTIMIERUNG: Grammar wird gecached (compile once, reuse N times).
        Bei KG-Extraktion spart das ~1-5ms × N Chunks = 30-250ms pro Dokument.
        
        Args:
            messages: Chat-Messages
            grammar_str: GBNF Grammar String
            max_tokens: Token-Limit
            temperature: Sampling-Temperatur (niedrig für strukturierte Outputs)
            
        Returns:
            Raw string output (garantiert grammar-konform)
        """
        self._llm_call_count += 1
        
        if not self.llm:
            return ""
        
        try:
            # ★ GPU-OPT: Cached grammar compilation (statt from_string pro Call)
            grammar_obj = self._get_or_compile_grammar(grammar_str)
            
            # ── Magistral Template rendern (umgeht Llava15ChatHandler) ──
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            chat_messages = []
            for msg in messages:
                processed = dict(msg)
                c = processed.get("content", "")
                if isinstance(c, str):
                    processed["content"] = c.replace(
                        "{{ strftime_now('%Y-%m-%d %H:%M:%S') }}", current_time
                    )
                chat_messages.append(processed)
            
            # Grammar-Pfad: Thinking AUS -- ein Thinking-Modell koennte sonst
            # spontan einen Think-Block starten und das erste erlaubte Token
            # der GBNF (z.B. "{") verletzen. Der (geschlossene) Think-Block
            # als Prompt-Praefix ist nur Text, kein Samplungstoken, und
            # verletzt die GBNF nicht. Templates ohne Thinking-Variable
            # ignorieren das Flag (harmlos).
            prompt_text = self._render_chat_template(
                chat_messages, tools=None, enable_thinking=False
            )
            # ── CRITICAL: cuda_lock protects ALL llama.cpp object access ──
            with cuda_lock:
                tokens = self.llm.tokenize(  # type: ignore[union-attr]
                    prompt_text.encode("utf-8"), add_bos=False, special=True
                )
            # ★ GPU-OPT: Use cached n_ctx instead of lock-protected C call
            n_ctx = self._cached_n_ctx or LLM_CONTEXT_SIZE
            
            # Context overflow check
            if len(tokens) > n_ctx - 64:
                logger.warning(
                    f"[CONTEXT-OVERFLOW] generate_with_grammar: "
                    f"prompt_tokens={len(tokens)} ≈ n_ctx={n_ctx}"
                )
                tokens = tokens[: n_ctx - 256]
            
            available = n_ctx - len(tokens)
            effective_max = min(max_tokens, max(available - 32, 128))
            
            completion_kwargs: dict = dict(
                prompt=tokens,
                max_tokens=effective_max,
                temperature=temperature,
                stop=self._stop_sequences,
            )
            if grammar_obj is not None:
                completion_kwargs["grammar"] = grammar_obj
            
            response = self._resilient_llm_call(
                self.llm.create_completion,  # type: ignore[union-attr]
                **completion_kwargs,
            )
            
            if isinstance(response, dict) and "choices" in response:
                text = (response["choices"][0].get("text") or "").strip()
                if text:
                    return text
            return ""
            
        except Exception as e:
            logger.error(f"generate_with_grammar fehlgeschlagen: {e}")
            return ""

    def generate_response_stream(
        self,
        messages: list,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 40,
        repeat_penalty: float = 1.1,
        min_p: float = 0.05,
        stop: Optional[list] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Generator[str, None, None]:
        """Stream text chunks while owning the llama.cpp iterator lifecycle.
        
        Nutzt _render_chat_template() + create_completion(stream=True)
        statt create_chat_completion, um den Llava15ChatHandler zu umgehen.
        """
        self._llm_call_count += 1
        
        if not self.llm:
            raise RuntimeError("Modell ist nicht geladen")
        
        try:
            # ── Magistral Template rendern ──
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            chat_messages = []
            for msg in messages:
                processed = dict(msg)
                c = processed.get("content", "")
                if isinstance(c, str):
                    processed["content"] = c.replace(
                        "{{ strftime_now('%Y-%m-%d %H:%M:%S') }}", current_time
                    )
                chat_messages.append(processed)
            
            prompt_text = self._render_chat_template(chat_messages, tools=None)
            
            # ── CRITICAL: cuda_lock for tokenize+streaming ──────────
            # llama.cpp is NOT thread-safe for ANY concurrent object access.
            # Lock must be held from tokenize() through the last streamed
            # token to prevent access-violation crashes (0x48 null-offset).
            with cuda_lock:
                # Tokenize (add_bos=False -- Template enthält bereits BOS-Token)
                tokens = self.llm.tokenize(  # type: ignore[union-attr]
                    prompt_text.encode("utf-8"), add_bos=False, special=True
                )
                
                # Context overflow check
                # ★ GPU-OPT: Use cached n_ctx instead of C FFI call
                n_ctx = self._cached_n_ctx or LLM_CONTEXT_SIZE
                if len(tokens) > n_ctx - 64:
                    logger.warning(
                        f"[CONTEXT-OVERFLOW] stream: prompt_tokens={len(tokens)} "
                        f"≈ n_ctx={n_ctx}"
                    )
                    tokens = tokens[: n_ctx - 256]
                
                available = n_ctx - len(tokens)
                effective_max = min(max_tokens, max(available - 32, 128))
                
                # create_completion mit stream=True (umgeht Llava15ChatHandler)
                stop_sequences = list(self._stop_sequences)
                if stop:
                    stop_sequences.extend(item for item in stop if item not in stop_sequences)
                stopping_criteria = None
                if is_cancelled is not None:
                    stopping_criteria = StoppingCriteriaList(
                        [lambda _input_ids, _logits: is_cancelled()]
                    )

                stream = self.llm.create_completion(  # type: ignore[union-attr]
                    prompt=tokens,
                    max_tokens=effective_max,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    repeat_penalty=repeat_penalty,
                    min_p=min_p,
                    stop=stop_sequences,
                    stopping_criteria=stopping_criteria,
                    stream=True,
                )

                try:
                    for chunk in stream:
                        if is_cancelled is not None and is_cancelled():
                            break
                        if isinstance(chunk, dict) and "choices" in chunk:
                            text = chunk["choices"][0].get("text", "")
                            if text:
                                yield text
                finally:
                    close_stream = getattr(stream, "close", None)
                    if callable(close_stream):
                        close_stream()
                        
        except Exception as e:
            logger.error(f"Streaming fehlgeschlagen: {e}")
            raise

    def _invalidate_token_cache(self) -> None:
        """Invalidiert den count_tokens()-Cache (Modell-Load/Unload/Swap).

        Der Tokenizer ist an das geladene Modell gebunden: ändert sich das
        Modell (oder wird es entladen), sind alle gecachten Zählungen ungültig.
        """
        with self._token_cache_lock:
            self._token_cache.clear()

    def count_tokens(self, text: str) -> int:
        """Zählt Token-Anzahl für gegebenen Text (echter Tokenizer, kein Heuristik).

        ★ PERF: Bounded LRU-Cache (deterministisch pro geladenem Modell).
        Cache wird bei Modell-Load/Unload/Swap via `_invalidate_token_cache()`
        geleert. Die teure `tokenize()`-C-FFI-Aufrufe unter `cuda_lock` bleibt
        unverändert; nur der Cache-Zugriff wird zusätzlich gesichert.
        """
        if not self.llm:
            return max(1, len(text) // 4)  # Fallback wenn kein Modell geladen
        with self._token_cache_lock:
            cached = self._token_cache.get(text)
            if cached is not None:
                self._token_cache.move_to_end(text)  # LRU-Aktualisierung
                return cached
        try:
            with cuda_lock:
                tokens = self.llm.tokenize(text.encode('utf-8'))
        except Exception as e:
            logger.error(f"Token-Zählung fehlgeschlagen: {e}")
            return max(1, len(text) // 4)
        count = len(tokens)
        if len(text) <= self._token_cache_max_len:
            with self._token_cache_lock:
                self._token_cache[text] = count
                while len(self._token_cache) > self._token_cache_max:
                    self._token_cache.popitem(last=False)  # älteste Einträge raus
        return count
    
    def count_messages_tokens(self, messages: list) -> int:
        """Zählt Tokens für eine Liste von Chat-Messages."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                total += self.count_tokens(content)
            # Overhead pro Message (role, formatting) ~ 4 Tokens
            total += 4
        return total
    
    def get_max_context_tokens(self) -> int:
        """Gibt die maximale Kontext-Größe zurück"""
        # ★ GPU-OPT: Use cached value when available
        if self._cached_n_ctx is not None:
            return self._cached_n_ctx
        if not self.llm:
            return 0
        try:
            with cuda_lock:
                return self.llm.n_ctx()
        except Exception:
            return 8192


def get_model_loader() -> ModelLoader:
    """Gibt die Singleton-Instanz des ModelLoaders zurück"""
    return ModelLoader()
