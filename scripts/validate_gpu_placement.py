#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
validate_gpu_placement.py — GPU-Runtime-Validierung (Single-/Dual-GPU)
=======================================================================

Validiert die GPU-Platzierung LLM / AUX (Single-GPU: beide auf derselben GPU):

  1. Placement      utils.gpu_devices.get_placement() — Rollen, CUDA-/NVML-Indizes
  2. VRAM-Snapshots utils.vram_monitor.get_all_gpu_snapshots() — GPUs mit Rollen
  3. GPU-Reranker   agent.reranker — ONNX CUDAExecutionProvider auf der AUX-GPU
                    (+ optional --bench: 200 Passagen x 3 Runs)

Voraussetzung fuer saubere Messung: LM Studio geschlossen (haelt VRAM auf beiden GPUs).

Nutzung:
    python scripts/validate_gpu_placement.py            # alle Checks
    python scripts/validate_gpu_placement.py --bench    # + Reranker-Benchmark
    python scripts/validate_gpu_placement.py -v         # Logging DEBUG

Exit-Codes: 0 = alle Checks PASS, 1 = mindestens ein FAIL.

Single-GPU-Policy (2026-09-04): Genau eine GPU ist gültig; LLM+AUX auf derselben
GPU ist gültig mit Warnung (erhöhter VRAM-Druck). Harte Fehler: keine nutzbare
GPU, oder eine env-Override (BOT_LLM_CUDA_DEVICE/BOT_AUX_CUDA_DEVICE) auf ein
unsichtbares Device — die Bibliotheken würden sonst still auf die
Auto-Platzierung zurückfallen.

Hinweis: CUDA- und NVML-Index sind auf diesem System vertauscht (UUID-Mapping
in utils/gpu_devices.py). Alle Rollen/Indizes kommen von get_placement().
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys
import time

# Repo-Root auf sys.path (Skript laeuft aus scripts/)
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:  # Robustes Konsolen-Output unabhaengig vom Codepage
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

logger = logging.getLogger("validate_gpu_placement")

_BAR = "=" * 74


def section(title: str) -> None:
    print()
    print(_BAR)
    print(f"  {title}")
    print(_BAR)


def _gpu_field(gpu: object, attr: str, default: str) -> str:
    """Defensive Feld-Zugriffe auf GPUInfo (Dataclass)."""
    val = getattr(gpu, attr, default)
    return str(val)


def check_placement() -> bool:
    """1/3: Placement-Quelle pruefen (Rollen, Indizes, valide Device-Overrides).

    Single-GPU ist gültig: LLM und AUX teilen dann dieselbe GPU (Warnung,
    erhöhter VRAM-Druck). Harte Fehler: keine nutzbare GPU, oder eine
    explizite env-Override (BOT_LLM_CUDA_DEVICE / BOT_AUX_CUDA_DEVICE) auf
    ein unsichtbares Device — die Bibliotheken würden sonst still auf die
    Auto-Platzierung zurueckfallen.
    """
    section("1/3 GPU-PLACEMENT (utils.gpu_devices)")
    from utils.gpu_devices import detect_gpus, get_placement

    p = get_placement()
    gpus = detect_gpus()

    ok = True

    usable = [g for g in gpus if float(getattr(g, "vram_gb", 0) or 0) > 0]
    if not usable:
        print("  !! Keine nutzbare GPU (Detection-Fallback aktiv, VRAM=0)")
        ok = False

    if p.single_gpu:
        print(
            f"  i  Single-GPU: LLM und AUX teilen cuda:{p.llm_cuda} "
            f"({_gpu_field(p.llm, 'name', '?')}, {float(getattr(p.llm, 'vram_gb', 0) or 0):.0f} GB) "
            f"— erwartet: erhöhter VRAM-Druck, AUX-Modelle entladen bei Bedarf"
        )
    elif p.llm_cuda == p.aux_cuda:
        print(
            f"  !! LLM und AUX auf derselben CUDA-GPU (cuda:{p.llm_cuda}) — "
            f"AUX-Modelle reduzieren den verfügbaren LLM-VRAM"
        )

    # Explizite env-Overrides: nicht-numerische oder unsichtbare Devices sind
    # harte Fehler — sonst greift still die Auto-Platzierung (silent fallback).
    visible = {g.cuda_index for g in gpus}
    for env in ("BOT_LLM_CUDA_DEVICE", "BOT_AUX_CUDA_DEVICE"):
        raw = os.environ.get(env)
        if raw is None or not str(raw).strip():
            continue
        try:
            val = int(str(raw).strip())
        except ValueError:
            print(f"  !! {env}={raw!r} ist kein Integer — Override ignoriert, Auto-Platzierung aktiv")
            ok = False
            continue
        if val not in visible:
            print(
                f"  !! {env}={val} nicht sichtbar (sichtbar: {sorted(visible)}) — "
                f"explizite Anforderung unerfüllbar, Auto-Fallback aktiv"
            )
            ok = False

    for idx, gpu in enumerate(gpus):
        roles = []
        if idx == p.llm_cuda:
            roles.append("LLM")
        if idx == p.aux_cuda:
            roles.append("AUX")
        role = "+".join(roles) if roles else "-"
        nvmls = []
        if idx == p.llm_cuda:
            nvmls.append(str(p.llm_nvml))
        if idx == p.aux_cuda:
            nvmls.append(str(p.aux_nvml))
        nvml = "+".join(nvmls) if nvmls else "?"
        print(
            f"  cuda:{idx}  NVML-{nvml}  "
            f"{_gpu_field(gpu, 'name', '?'):<30} "
            f"{float(getattr(gpu, 'vram_gb', 0) or 0):.0f} GB  [{role}]"
        )

    llm_vram = float(getattr(gpus[p.llm_cuda], "vram_gb", 0) or 0) if len(gpus) > p.llm_cuda else 0.0
    aux_vram = float(getattr(gpus[p.aux_cuda], "vram_gb", 0) or 0) if len(gpus) > p.aux_cuda else 0.0
    if ok and p.llm_cuda != p.aux_cuda and llm_vram and aux_vram and llm_vram < aux_vram:
        print(f"  !! Warnung: LLM-GPU ({llm_vram:.0f} GB) kleiner als AUX-GPU ({aux_vram:.0f} GB)")

    print(f"  Summary: {p}")
    return ok


def check_vram() -> bool:
    """2/3: VRAM-Monitor sieht die Placement-GPU(s) mit Rollen.

    Single-GPU: Ein Snapshot mit Rolle LLM/AUX/LLM+AUX auf der geteilten
    GPU ist gültig. Dual-GPU: zwei Snapshots mit den Rollen 'LLM' und 'AUX'.
    """
    section("2/3 VRAM-MONITOR (utils.vram_monitor.get_all_gpu_snapshots)")
    from utils.gpu_devices import get_placement
    from utils.vram_monitor import get_all_gpu_snapshots

    snaps = get_all_gpu_snapshots()
    if not snaps:
        print("  !! Keine GPU-Snapshots (pynvml FEHLT und nvidia-smi-CLI nicht erreichbar?)")
        return False

    ok = True
    for s in sorted(snaps, key=lambda x: int(x.get("nvml_index", 0))):
        nvml = s.get("nvml_index", "?")
        cuda = s.get("cuda_index", "?")
        role = s.get("role", "?")
        name = s.get("name", "?")
        used = float(s.get("used_gb", 0.0) or 0.0)
        total = float(s.get("total_gb", 0.0) or 0.0)
        util = float(s.get("utilization_pct", 0.0) or 0.0)
        src = s.get("source", "?")
        print(
            f"  NVML-{nvml}  cuda:{cuda}  [{role:<3}] {str(name):<30} "
            f"{used:6.2f}/{total:6.2f} GB  {util:5.1f}%  (src={src})"
        )

    roles = {s.get("role") for s in snaps}
    if get_placement().single_gpu:
        if not (roles & {"LLM", "AUX", "LLM+AUX"}):
            print(f"  !! Geteilte GPU ohne LLM/AUX-Rolle (gefunden: {sorted(r for r in roles if r)})")
            ok = False
    else:
        if len(snaps) < 2:
            print("  !! Erwartet 2 GPU-Snapshots")
            ok = False
        if "LLM" not in roles or "AUX" not in roles:
            print(f"  !! Rollen 'LLM'/'AUX' nicht beide vorhanden (gefunden: {sorted(r for r in roles if r)})")
            ok = False
    return ok


def _sample_passages(n: int) -> list:
    """Testpassagen: erste zwei relevant, Rest Foehn."""
    base = [
        "Dual-GPU-Platzierung: Das LLM laeuft auf der RTX 4090, die AUX-Modelle "
        "auf der RTX 3060 Ti; CUDA- und NVML-Index sind vertauscht und werden "
        "per UUID in utils/gpu_devices.py aufgelost.",
        "Die GPU-Platzierung trennt LLM und Hilfsmodelle (Reranker, Embeddings, "
        "OCR, Docling) auf separate GPUs und nutzt get_placement() als Single "
        "Source of Truth fuer alle Device-Strings.",
    ]
    filler = [
        "Einkaufen: Milch, Eier, Brot und Butter nicht vergessen.",
        "Rezept: Apfelkuchen mit Vanillesosse und Zimt.",
        "Wetterbericht: Am Wochenende ist es ueberwiegend bewoelkt.",
        "Fußball: Der Spielplan fuer die naechste Woche ist veroeffentlicht.",
        "Reise: Die Zugverbindung nach Muenchen hat 15 Minuten Verspaetung.",
        "Garten: Die Tomaten brauchen jetzt alle zwei Tage gegossen zu werden.",
    ]
    out = []
    for i in range(n):
        if i < len(base):
            out.append({"text": base[i]})
        else:
            out.append({"text": filler[i % len(filler)]})
    return out


def check_reranker(bench: bool) -> bool:
    """3/3: Reranker laedt, nutzt ONNX auf GPU (CUDAExecutionProvider) und reranked."""
    section("3/3 GPU-RERANKER (agent.reranker)")
    ok = True

    # onnxruntime + CUDA-EP pruefen
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
        cuda_ep = "CUDAExecutionProvider" in providers
        print(f"  onnxruntime {ort.__version__}  providers={providers}")
        if not cuda_ep:
            print("  !! CUDAExecutionProvider nicht verfuegbar — Reranker faellt auf CPU zurueck.")
            print("     Fix: pip install onnxruntime-gpu   (venv_bot_20260802)")
            ok = False
    except ImportError as exc:
        print(f"  !! onnxruntime nicht importierbar: {exc}")
        return False

    # Reranker laden
    try:
        from agent.reranker import get_reranker

        rr = get_reranker()
        rr._ensure_loaded()
    except Exception as exc:  # noqa: BLE001 — Validierung darf nie crashen
        print(f"  !! Reranker-Load fehlgeschlagen: {exc}")
        return False

    on_gpu = bool(getattr(rr, "_onnx_on_gpu", False))
    has_onnx = getattr(rr, "_onnx_session", None) is not None
    print(
        f"  Reranker: is_available={getattr(rr, 'is_available', '?')}  "
        f"onnx_session={'ja' if has_onnx else 'nein'}  on_gpu={on_gpu}"
    )
    if on_gpu and has_onnx:
        try:
            print(f"  active providers: {rr._onnx_session.get_providers()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (active provider-Abfrage fehlgeschlagen: {exc})")

    # Funktionstest: 2 relevante + 4 Foehn-Passagen
    query = "Wie ist die GPU-Platzierung von LLM und AUX-Modellen?"
    passages = _sample_passages(6)
    t0 = time.perf_counter()
    ranked = rr.rerank(query, passages, top_k=2)
    dt = (time.perf_counter() - t0) * 1000.0
    top1 = ranked[0] if ranked else {}
    score = top1.get("rerank_score")
    if isinstance(score, float):
        print(f"  Test-Rerank: {dt:.0f} ms  Top1 score={score:.3f}")
    else:
        print(f"  Test-Rerank: {dt:.0f} ms  Top1 score=n/a")
    print(f"    Top1: {str(top1.get('text', ''))[:80]!r}")
    if len(ranked) == 2 and isinstance(ranked[1].get("rerank_score"), float):
        print(f"    Top2: {str(ranked[1].get('text', ''))[:80]!r} "
              f"(score={float(ranked[1]['rerank_score']):.3f})")

    # Benchmark
    if bench:
        n_pass = 200
        runs = 3
        big = _sample_passages(n_pass)
        rr.rerank(query, big[:20])  # Warm-up
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            rr.rerank(query, big, top_k=5)
            times.append((time.perf_counter() - t0) * 1000.0)
        best = min(times)
        avg = sum(times) / len(times)
        print(f"  Benchmark: {n_pass} Passagen x {runs} Runs  best={best:.0f} ms  avg={avg:.0f} ms  "
              f"(~{best / n_pass * 100:.1f} ms/100, ~{n_pass / (best / 1000.0):.0f} Passagen/s)")

    if not on_gpu:
        print("  ERGEBNIS: Reranker funktioniert, aber NICHT auf GPU (CPU-Fallback aktiv).")
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="GPU-Platzierung validieren (Single-/Dual-GPU, LLM + AUX-Rollen).",
    )
    parser.add_argument("--bench", action="store_true",
                        help="zusätzliches Reranker-Benchmark (200 Passagen x 3 Runs)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logging DEBUG")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    print("GPU-Placement-Validierung — homebot (Single-/Dual-GPU, LLM + AUX-Rollen)")
    print(f"  Python: {sys.executable}")

    results = [
        ("placement", check_placement()),
        ("vram-monitor", check_vram()),
        ("gpu-reranker", check_reranker(bench=args.bench)),
    ]

    section("ERGEBNIS")
    worst = 0
    for name, passed in results:
        worst = max(worst, 0 if passed else 1)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print()
    if worst:
        print("  Hinweise: LM Studio schließen (hält VRAM auf beiden GPUs)?")
        print("            onnxruntime-gpu im venv installiert?  ->  pip install onnxruntime-gpu")
        print("  Doku: docs/RTX4090_RYZEN9_GUIDE.md (Dual-GPU-Rollen) / AGENTS.md (Dual-GPU-Platzierung)")
    else:
        print("  Alle Checks bestanden: LLM + AUX platziert, Reranker auf GPU.")
    return worst


if __name__ == "__main__":
    sys.exit(main())