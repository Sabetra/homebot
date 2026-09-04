"""
CUDA Scheduling Configuration (SOTA)
======================================

Prevents CUDA spin-wait on Windows WDDM by setting BlockingSync scheduling.

Root Cause: On Windows WDDM, CUDA defaults to spin-wait scheduling
(cudaDeviceScheduleAuto → Spin on WDDM). This causes:
  - 100% GPU utilization reported even while GPU is idle (busy-polling)
  - 1+ CPU core wasted in tight polling loop
  - False GPU metrics making performance analysis impossible

Fix: Set BlockingSync scheduling BEFORE any CUDA context is created.
The CPU thread performs an OS-level wait (interrupt-driven wakeup, ~10-50µs
latency) instead of spin-polling. Negligible for LLM inference (~10-50ms/token).

MUST be imported before any CUDA context creation:
  - Before torch.cuda.get_device_name() / get_device_properties()
  - Before Llama() constructor (ggml-cuda init)
  - Before SentenceTransformer(device="cuda")
  - Before torch.cuda.mem_get_info()

Usage (at the very top of entry point, after os import):
    from utils.cuda_init import configure_cuda_scheduling
    configure_cuda_scheduling()

Author: SOTA Bot Pipeline
Date: 2026-03-28
"""

import os
import logging

logger = logging.getLogger(__name__)

_cuda_scheduling_configured = False


def configure_cuda_scheduling() -> None:
    """Set CUDA device scheduling to BlockingSync (eliminates spin-wait).

    Safe to call multiple times — no-op after first successful call.
    Only relevant on Windows (WDDM driver model).
    """
    global _cuda_scheduling_configured
    if _cuda_scheduling_configured:
        return

    if os.name != 'nt':
        _cuda_scheduling_configured = True
        return

    try:
        import torch
        import ctypes

        if not (hasattr(torch, 'cuda') and torch.backends.cuda.is_built()):
            logger.info("[CUDA-SCHED] PyTorch CUDA not built — scheduling fix skipped")
            _cuda_scheduling_configured = True
            return

        # Locate cudart64_XX.dll from PyTorch's bundled CUDA runtime.
        torch_lib_dir = os.path.join(os.path.dirname(torch.__file__), 'lib')
        cudart_path = None
        for fname in sorted(os.listdir(torch_lib_dir)):
            if fname.startswith('cudart64') and fname.endswith('.dll'):
                cudart_path = os.path.join(torch_lib_dir, fname)
                break

        if not cudart_path:
            logger.info(
                "[CUDA-SCHED] No cudart DLL found in torch/lib — "
                "scheduling fix skipped (torch may be CPU-only)"
            )
            _cuda_scheduling_configured = True
            return

        cudart = ctypes.WinDLL(cudart_path)

        # cudaDeviceScheduleBlockingSync = 0x04
        # Ref: CUDA Runtime API — cudaSetDeviceFlags()
        _BLOCKING_SYNC = 0x04
        result = cudart.cudaSetDeviceFlags(_BLOCKING_SYNC)

        if result == 0:  # cudaSuccess
            logger.info(
                "[CUDA-SCHED] ✅ BlockingSync scheduling activated — "
                "spin-wait eliminated, ~1 CPU core freed"
            )
        elif result == 35:  # cudaErrorSetOnActiveProcess
            logger.warning(
                "[CUDA-SCHED] ⚠️ CUDA context already active — cannot change "
                "scheduling flags. Ensure no CUDA API was called before this point."
            )
        else:
            logger.warning(
                f"[CUDA-SCHED] cudaSetDeviceFlags returned error code {result}"
            )
    except Exception as exc:
        logger.info(f"[CUDA-SCHED] Scheduling fix skipped: {exc}")
    finally:
        _cuda_scheduling_configured = True
