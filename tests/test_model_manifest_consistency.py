"""Prevent drift between models/manifest.json (SSoT) and model names in code.

Regression this test guards against: ``agent/config_manager.py`` carried a stale
default ``cross_encoder_model = "BAAI/bge-reranker-v2-m5"`` (a model that does
not exist) while the active canonical reranker is ``BAAI/bge-reranker-v2-m3``.
Such drift produces *silent* fallbacks (a missing required model degrades RAG
quality without an operator-visible error).

The test pins, against the manifest:
  * the canonical reranker (``agent.reranker.RERANKER_MODEL_NAME``),
  * the reranker fallback chain (``agent.reranker._CROSS_ENCODER_MODELS``),
  * the config default (``agent.config_manager`` ``cross_encoder_model``),
  * the NLI model(s) used by ``agent.verification_manager``,
and scans the model-loading code for any Hugging Face repo that is *not*
declared in the manifest (silent-missing-model drift).

The checks read source with regex (no heavy runtime imports), so the test stays
fast and dependency-free.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.setup_models import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Hugging Face organisations used by Homebot models (org anchor keeps the scan
# from matching unrelated "a/b" path strings).
_MODEL_ORGS = (
    "BAAI",
    "intfloat",
    "cross-encoder",
    "sentence-transformers",
    "docling-project",
    "Alibaba-NLP",
    "jinaai",
)
_HF_REPO_RE = re.compile(r"\b(?:%s)(?:/[A-Za-z0-9_.\-]+)+" % "|".join(_MODEL_ORGS))

# Model-loading code where a hard-coded name would cause silent drift.
_MODEL_CODE_FILES = (
    "agent/reranker.py",
    "agent/cross_encoder_reranker.py",
    "agent/verification_manager.py",
    "agent/config_manager.py",
    "utils/embedding_singleton.py",
)


def _manifest_repos() -> set[str]:
    m = load_manifest(str(REPO_ROOT / "models" / "manifest.json"))
    repos = set()
    for section in ("llm", "aux"):
        for entry in m[section]["models"]:
            if entry.get("hf_repo"):
                repos.add(entry["hf_repo"])
    for alt in m.get("aux", {}).get("known_alternatives", []):
        if alt.get("hf_repo"):
            repos.add(alt["hf_repo"])
    return repos


def _source(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _covered(code_repo: str, repos: set[str]) -> bool:
    """True if a code-referenced repo is declared in the manifest, tolerating
    family references / variant suffixes (e.g. ``cross-encoder/ms-marco`` in a
    comment vs ``cross-encoder/ms-marco-MiniLM-L-6-v2`` in the manifest)."""
    if code_repo in repos:
        return True
    return any(
        r == code_repo or r.startswith(code_repo + "-") or r.startswith(code_repo + "/")
        for r in repos
    )


# --- precise canonical pins -------------------------------------------------

def test_canonical_reranker_is_v2m3_and_in_manifest():
    block = re.search(r"_CROSS_ENCODER_MODELS\s*=\s*\[(.*?)\]", _source("agent/reranker.py"), re.DOTALL)
    assert block, "agent/reranker.py must define _CROSS_ENCODER_MODELS"
    names = re.findall(r'"([^"]+)"', block.group(1))
    assert names, "_CROSS_ENCODER_MODELS must list at least one model"
    canonical = names[0]
    assert canonical == "BAAI/bge-reranker-v2-m3", (
        f"canonical reranker is {canonical!r}; expected BAAI/bge-reranker-v2-m3"
    )
    assert canonical in _manifest_repos(), (
        f"canonical reranker {canonical!r} not declared in models/manifest.json"
    )


def test_reranker_fallback_chain_in_manifest():
    block = re.search(r"_CROSS_ENCODER_MODELS\s*=\s*\[(.*?)\]", _source("agent/reranker.py"), re.DOTALL)
    assert block, "agent/reranker.py must define _CROSS_ENCODER_MODELS"
    names = re.findall(r'"([^"]+)"', block.group(1))
    repos = _manifest_repos()
    missing = [n for n in names if n not in repos]
    assert not missing, f"reranker fallback-chain members not in manifest: {missing}"


def test_config_manager_reranker_in_manifest():
    match = re.search(r'cross_encoder_model:\s*str\s*=\s*"([^"]+)"', _source("agent/config_manager.py"))
    assert match, "agent/config_manager.py must define a cross_encoder_model default"
    assert match.group(1) in _manifest_repos(), (
        f"config_manager.cross_encoder_model {match.group(1)!r} drifted from the manifest "
        "(fix the default or add the model to models/manifest.json)"
    )


def test_nli_model_in_manifest():
    names = set(re.findall(r'"(cross-encoder/nli-[A-Za-z0-9\-]+)"', _source("agent/verification_manager.py")))
    assert names, "agent/verification_manager.py must reference an NLI model"
    repos = _manifest_repos()
    missing = [n for n in names if n not in repos]
    assert not missing, f"NLI model(s) not declared in manifest: {missing}"


# --- broad scan: no orphan HF repo in model-loading code ---------------------

def test_no_orphan_hf_repo_in_model_code():
    repos = _manifest_repos()
    orphans: dict[str, set[str]] = {}
    for relpath in _MODEL_CODE_FILES:
        text = _source(relpath)
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue  # skip comments (family references, SOTA notes)
            for match in _HF_REPO_RE.findall(line):
                if not _covered(match, repos):
                    orphans.setdefault(relpath, set()).add(match)
    assert not orphans, (
        "model-loading code references Hugging Face repo(s) not in "
        f"models/manifest.json: { {k: sorted(v) for k, v in orphans.items()} }"
    )