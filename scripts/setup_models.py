#!/usr/bin/env python3
"""
Homebot model bootstrap / status / check tool.
==============================================

``models/manifest.json`` is the Single Source of Truth for all local model
requirements (LLM GGUFs + auxiliary RAG/verification models). This module is
the operator-facing CLI that *inspects* (``--status``), *verifies*
(``--check``) and *bootstraps* (``--fetch``) those models.

SOTA design principles (2026)
-----------------------------
* **Local-first / offline-respecting.** ``--status`` and ``--check`` never
  touch the network. ``--fetch`` honours ``APP_LOCAL_ONLY``,
  ``HF_HUB_OFFLINE`` and ``TRANSFORMERS_OFFLINE`` and *refuses* to download in
  strict-offline mode (fail-fast, no silent fallback).
* **Declarative.** Every model requirement lives in ``models/manifest.json``;
  this file only *interprets* that manifest. No model name is hard-coded here,
  which is what lets ``tests/test_model_manifest_consistency.py`` prevent drift.
* **Testable.** All presence-detection logic is pure and takes *injectable*
  cache roots, so tests can use temp directories with zero network access.

Usage
-----
    python scripts/setup_models.py --status
    python scripts/setup_models.py --check
    python scripts/setup_models.py --fetch [--only <model-id>]

Exit codes
----------
    0   success / all *required* models present (``--check``)
    1   one or more *required* models missing (``--check``), or ``--fetch``
        refused (offline) / failed
    2   manifest missing / invalid / unexpected schema
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# scripts/ -> repository root (CWD-independent, mirrors scripts/model_loader.py).
REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_RELPATH = Path("models") / "manifest.json"

# Offline flags, consistent with utils/runtime_policy.py / utils/embedding_singleton.py.
_OFFLINE_FLAGS = ("APP_LOCAL_ONLY", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
_TRUTHY = {"1", "true", "yes", "on"}


class ManifestError(ValueError):
    """Raised when models/manifest.json is missing, unparseable or malformed."""


class FetchRefusedError(RuntimeError):
    """Raised when ``--fetch`` is attempted under strict-offline policy."""


def manifest_path(explicit: Optional[str] = None) -> Path:
    """Resolve the manifest path (explicit override, else repo-relative)."""
    if explicit:
        return Path(explicit)
    return REPO_ROOT / MANIFEST_RELPATH


def load_manifest(path: Optional[str] = None) -> Dict[str, Any]:
    """Load and schema-validate the model manifest.

    Raises:
        ManifestError: if the file is missing, not valid JSON, or lacks the
            expected top-level structure (``version``, ``llm`` and ``aux``).
    """
    p = manifest_path(path)
    if not p.is_file():
        raise ManifestError(f"model manifest not found at {p}")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"model manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("model manifest root must be a JSON object")
    for key in ("version", "llm", "aux"):
        if key not in data:
            raise ManifestError(f"model manifest is missing required key: {key!r}")
    for section in ("llm", "aux"):
        models = data.get(section, {}).get("models")
        if not isinstance(models, list) or not models:
            raise ManifestError(f"model manifest section {section!r} must list at least one model")
    return data


@dataclass(frozen=True)
class CacheRoots:
    """Resolved on-disk cache roots (injectable for tests)."""

    lm_studio_root: Path
    st_home: Path
    hf_home: Path
    easyocr_dir: Path
    repo_root: Path

    @classmethod
    def from_env(cls, repo_root: Path, environ: Optional[Dict[str, str]] = None) -> "CacheRoots":
        """Build roots from environment overrides with local-first defaults.

        Defaults mirror the production loaders:
          * LLM GGUFs  -> ``BOT_MODELS_DIR``  (else ``~/.cache/lm-studio/models/lmstudio-community``)
          * embeddings -> ``SENTENCE_TRANSFORMERS_HOME`` (else ``<repo>/models_cache/sentence_transformers``)
          * HF hub     -> ``HF_HOME`` (else ``~/.cache/huggingface``)
          * EasyOCR    -> ``EASYOCR_MODULE_PATH`` (else ``~/.EasyOCR``)
        """
        env = os.environ if environ is None else environ
        home = Path.home()
        return cls(
            lm_studio_root=Path(env.get("BOT_MODELS_DIR") or (home / ".cache" / "lm-studio" / "models" / "lmstudio-community")),
            st_home=Path(env.get("SENTENCE_TRANSFORMERS_HOME") or (repo_root / "models_cache" / "sentence_transformers")),
            hf_home=Path(env.get("HF_HOME") or (home / ".cache" / "huggingface")),
            easyocr_dir=Path(env.get("EASYOCR_MODULE_PATH") or (home / ".EasyOCR")),
            repo_root=Path(repo_root),
        )


def _split_repo(repo_id: str) -> "tuple[str, str]":
    org, name = repo_id.split("/", 1)
    return org, name


def resolve_candidates(entry: Dict[str, Any], roots: CacheRoots) -> List[Path]:
    """Return the on-disk location(s) where a manifest entry would live.

    The first existing candidate is reported as the model's resolved path.
    Pure function: no I/O beyond ``Path`` construction (existence is checked
    by the caller so tests can assert on the candidates directly).
    """
    strategy = entry.get("cache_strategy")
    repo_id = entry.get("hf_repo")

    if strategy in ("hf_hub", "docling"):
        if not repo_id:
            return []
        org, name = _split_repo(repo_id)
        return [roots.hf_home / "hub" / f"models--{org}--{name}"]

    if strategy == "st_home":
        if not repo_id:
            return []
        org, name = _split_repo(repo_id)
        # sentence-transformers 5.x stores models in the huggingface_hub layout
        # (models--<org>--<name>) under the cache_folder / SENTENCE_TRANSFORMERS_HOME;
        # also accept the legacy <org>_<name> layout and the default HF hub cache,
        # so presence detection is robust to any of the supported cache configs.
        return [
            roots.st_home / f"models--{org}--{name}",
            roots.st_home / f"{org}_{name}",
            roots.hf_home / "hub" / f"models--{org}--{name}",
        ]

    if strategy == "easyocr":
        return [
            roots.easyocr_dir / "model",
            roots.easyocr_dir,
            roots.repo_root / "models_cache",
        ]

    # LLM GGUF entries carry an explicit folder/file (LM Studio layout).
    if entry.get("folder") and entry.get("file"):
        return [roots.lm_studio_root / entry["folder"] / entry["file"]]

    return []


def check_entry(entry: Dict[str, Any], roots: CacheRoots) -> Dict[str, Any]:
    """Presence + metadata for a single manifest entry (pure)."""
    candidates = resolve_candidates(entry, roots)
    present = any(c.exists() for c in candidates)
    resolved = next((str(c) for c in candidates if c.exists()), str(candidates[0]) if candidates else "")
    return {
        "id": entry.get("id", "<missing-id>"),
        "role": entry.get("role") or entry.get("format") or "",
        "required": bool(entry.get("required", False)),
        "present": present,
        "path": resolved,
        "license": entry.get("license"),
        "size_gb": entry.get("size_gb_measured"),
    }


def all_entries(manifest: Dict[str, Any]) -> "List[tuple[str, Dict[str, Any]]]":
    """Yield ``(section, entry)`` for every LLM + AUX model in the manifest."""
    out: List[tuple[str, Dict[str, Any]]] = []
    for section in ("llm", "aux"):
        for entry in manifest.get(section, {}).get("models", []):
            out.append((section, entry))
    return out


def collect_status(manifest: Dict[str, Any], roots: CacheRoots) -> List[Dict[str, Any]]:
    """Status row for every model in the manifest (LLM + AUX), ordered."""
    rows = []
    for section, entry in all_entries(manifest):
        row = check_entry(entry, roots)
        row["section"] = section
        rows.append(row)
    return rows


def is_offline(environ: Optional[Dict[str, str]] = None) -> bool:
    """True when any strict-offline flag is set (see ``_OFFLINE_FLAGS``)."""
    env = os.environ if environ is None else environ
    return any((env.get(flag) or "").strip().lower() in _TRUTHY for flag in _OFFLINE_FLAGS)


def fetchable_missing(
    manifest: Dict[str, Any], roots: CacheRoots, only: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Entries that (a) have a Hugging Face repo, (b) are missing locally, and
    (c) match ``only`` when provided. Pure (no network)."""
    result = []
    for _section, entry in all_entries(manifest):
        if only is not None and entry.get("id") != only:
            continue
        if not entry.get("hf_repo"):
            continue
        if check_entry(entry, roots)["present"]:
            continue
        result.append(entry)
    return result


def _fetch_cache_dir(entry: Dict[str, Any], roots: CacheRoots) -> Path:
    """Where ``snapshot_download`` should stage a model, per its strategy."""
    if entry.get("cache_strategy") == "st_home":
        return roots.st_home
    return roots.hf_home / "hub"


def run_fetch(
    manifest: Dict[str, Any],
    roots: CacheRoots,
    only: Optional[str] = None,
    offline: Optional[bool] = None,
    snapshot_downloader: Optional[Any] = None,
) -> int:
    """Bootstrap missing Hugging Face models. Returns a process exit code.

    * Refuses (raises :class:`FetchRefusedError`) under strict-offline policy.
    * Never downloads LLM GGUFs (gated / LM-Studio-managed) or EasyOCR bundled
      weights -- those are operator-managed and reported as skipped.
    * ``snapshot_downloader`` is injectable for tests (defaults to
      ``huggingface_hub.snapshot_download``).
    """
    if offline is None:
        offline = is_offline()
    if offline:
        raise FetchRefusedError(
            "Offline policy active (APP_LOCAL_ONLY / HF_HUB_OFFLINE / "
            "TRANSFORMERS_OFFLINE). Refusing network download: pre-populate "
            "the models manually, or clear the offline flags and retry."
        )

    targets = fetchable_missing(manifest, roots, only=only)
    if not targets:
        print("Nothing to fetch: all fetchable models are present (or none are fetchable).")
        return 0

    if snapshot_downloader is None:
        try:
            from huggingface_hub import snapshot_download as snapshot_downloader  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise FetchRefusedError(f"huggingface_hub is required for --fetch: {exc}") from exc

    failures: List[tuple[str, str]] = []
    for entry in targets:
        repo = entry["hf_repo"]
        revision = entry.get("revision") if entry.get("revision_status") == "pinned" else None
        cache_dir = _fetch_cache_dir(entry, roots)
        try:
            snapshot_downloader(
                repo_id=repo,
                cache_dir=str(cache_dir),
                revision=revision,
                local_files_only=False,
            )
            print(f"[OK]   fetched {repo} -> {cache_dir}")
        except Exception as exc:  # noqa: BLE001 - report per-model, keep going
            failures.append((repo, str(exc)))
            print(f"[FAIL] {repo}: {exc}")

    if failures:
        print(f"{len(failures)} model(s) could not be fetched.")
        return 1
    return 0


def print_status(manifest: Dict[str, Any], roots: CacheRoots) -> None:
    """Human-readable per-model presence table (LLM + AUX)."""
    rows = collect_status(manifest, roots)
    header = f"{'MODEL':32} {'SECTION':5} {'ROLE':10} {'REQ':4} {'PRESENT':8} SIZE_GB   PATH"
    print(header)
    print("-" * len(header))
    for r in rows:
        size = f"{r['size_gb']:.2f}" if isinstance(r["size_gb"], (int, float)) else "-"
        pres = "YES" if r["present"] else "no"
        req = "yes" if r["required"] else "no"
        print(f"{r['id']:32} {r['section']:5} {r['role']:10} {req:4} {pres:8} {size:7}   {r['path']}")
    missing_required = [r for r in rows if r["required"] and not r["present"]]
    if missing_required:
        print("\nWARNING: Required model(s) missing: " + ", ".join(r["id"] for r in missing_required))
    else:
        print("\nOK: All required models present.")


def run_check(manifest: Dict[str, Any], roots: CacheRoots) -> int:
    """Print status and return 0 (all required present) or 1 (any missing)."""
    print_status(manifest, roots)
    missing_required = [r for r in collect_status(manifest, roots) if r["required"] and not r["present"]]
    return 1 if missing_required else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup_models.py",
        description="Inspect, verify and bootstrap Homebot's local models from models/manifest.json.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="Show per-model presence (no network).")
    mode.add_argument("--check", action="store_true", help="Like --status; exit 1 if any required model is missing.")
    mode.add_argument("--fetch", action="store_true", help="Download missing Hugging Face models (honours offline flags).")
    parser.add_argument("--manifest", default=None, help="Path to an alternate manifest (default: models/manifest.json).")
    parser.add_argument("--only", default=None, help="For --fetch: restrict to a single model id.")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    roots = CacheRoots.from_env(REPO_ROOT)

    if args.status:
        print_status(manifest, roots)
        return 0
    if args.check:
        return run_check(manifest, roots)
    if args.fetch:
        try:
            return run_fetch(manifest, roots, only=args.only)
        except FetchRefusedError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
    return 2  # pragma: no cover - mutually exclusive group guarantees a mode


if __name__ == "__main__":
    sys.exit(main())