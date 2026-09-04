"""
Token Management Utilities - SOTA Dynamic Token Estimation
===========================================================

Provides production-grade token estimation and dynamic sizing for LLM calls.
Uses a two-tier approach:
  1. Fast heuristic (no external deps) - character/word-based estimation
  2. Optional tiktoken integration for exact counts when available

Key functions:
  - estimate_response_tokens(): Complexity-aware output token budget
  - calculate_dynamic_max_tokens(): Safe context-window-aware limit
  - estimate_prompt_tokens(): Fast prompt token estimation
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional tiktoken integration (exact token counts for OpenAI-style models)
# ---------------------------------------------------------------------------
_tiktoken_encoder: Optional[Any] = None
try:
    import tiktoken  # type: ignore
    _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")  # GPT-4 style
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Heuristic: ~1.3 tokens per word for German, ~1.4 for English
# We use a conservative 1.35 as default multiplier
DEFAULT_TOKENS_PER_WORD = 1.35
DEFAULT_CHARS_PER_TOKEN = 3.8  # ~4 chars/token for mixed languages

# Complexity tiers for response estimation
MIN_OUTPUT_TOKENS = 64        # Absolute minimum (yes/no, short fact)
DEFAULT_OUTPUT_TOKENS = 512   # Standard conversational response
COMPLEX_OUTPUT_TOKENS = 1024  # Multi-paragraph analysis
EXTREME_OUTPUT_TOKENS = 2048  # Deep research, code generation, long reports

# Safety buffers
DEFAULT_SAFETY_BUFFER_RATIO = 0.10  # 10% of context window
MIN_CONTEXT_RESERVATION = 64  # Tokens reserved for EOS + protocol


# ===================================================================
# Core Estimation Functions
# ===================================================================

def estimate_prompt_tokens(text: str, use_tiktoken: bool = True) -> int:
    """
    Estimate token count for a text string.

    Uses tiktoken for exact counts when available, otherwise falls back
    to a character-based heuristic.

    Args:
        text: The text to estimate tokens for.
        use_tiktoken: Prefer tiktoken if True (default). Set to False
                      to force heuristic mode.

    Returns:
        Estimated token count (minimum 1).
    """
    if not text:
        return 0

    if _TIKTOKEN_AVAILABLE and use_tiktoken and _tiktoken_encoder is not None:
        try:
            return len(_tiktoken_encoder.encode(text))
        except Exception:
            pass  # Fallback to heuristic

    # Heuristic: chars / chars_per_token, with word-level refinement
    char_estimate = math.ceil(len(text) / DEFAULT_CHARS_PER_TOKEN)
    word_estimate = len(text.split()) * DEFAULT_TOKENS_PER_WORD

    # Use the more conservative (higher) estimate
    return max(1, int(max(char_estimate, word_estimate)))


def estimate_response_tokens(
    query: str,
    context_tokens: Optional[int] = None,
    complexity_hint: Optional[str] = None,
    model_context_window: int = 32768,
    safety_buffer_ratio: float = DEFAULT_SAFETY_BUFFER_RATIO,
    min_output: int = MIN_OUTPUT_TOKENS,
    max_output: int = EXTREME_OUTPUT_TOKENS,
) -> int:
    """
    Dynamically estimate the required output token budget for a query.

    This is the central function that replaces hardcoded max_tokens=1024.
    It analyses the query to determine complexity and assigns an appropriate
    token budget, respecting the model's context window.

    Args:
        query: The user query to analyse.
        context_tokens: Optional pre-computed token count of the full
                        context (system + history + evidence). When provided,
                        the result is capped to not exceed the remaining
                        context window.
        complexity_hint: Optional override - one of 'simple', 'moderate',
                         'complex', 'extreme'. When None, complexity is
                         inferred from the query.
        model_context_window: Total context window of the model (default 32768).
        safety_buffer_ratio: Fraction of context window kept as buffer.
        min_output: Absolute minimum tokens to allow.
        max_output: Absolute maximum tokens to allow.

    Returns:
        Recommended token budget, clamped to [min_output, max_output] and
        context-window-safe.
    """
    # --- Step 1: Determine base budget from complexity ---
    if complexity_hint:
        base = _complexity_to_tokens(complexity_hint)
    else:
        base = _infer_complexity_and_tokens(query)

    # --- Step 2: Clamp to configured bounds ---
    base = max(min_output, min(base, max_output))

    # --- Step 3: Context-window safety (if context_tokens known) ---
    if context_tokens is not None:
        remaining = model_context_window - context_tokens
        buffer = math.floor(model_context_window * safety_buffer_ratio)
        safe_max = max(min_output, remaining - buffer - MIN_CONTEXT_RESERVATION)
        base = min(base, safe_max)

    return max(min_output, base)


def calculate_dynamic_max_tokens(
    model_context_window: int,
    prompt_tokens: int,
    safety_buffer_ratio: float = DEFAULT_SAFETY_BUFFER_RATIO,
    min_output_tokens: int = MIN_OUTPUT_TOKENS,
) -> int:
    """
    Calculate a safe max_tokens limit for the LLM's completion based on
    the remaining space in the model's context window.

    Args:
        model_context_window: Total tokens supported by the model.
        prompt_tokens: Tokens consumed by system prompt + user inputs.
        safety_buffer_ratio: Fraction of context window to keep as buffer.
        min_output_tokens: Minimum floor for allowed output length.

    Returns:
        Safe maximum tokens for completion.
    """
    remaining = model_context_window - prompt_tokens
    buffer = math.floor(model_context_window * safety_buffer_ratio)
    available = remaining - buffer - MIN_CONTEXT_RESERVATION

    # Ensure we don't exceed the true physical limit
    return int(max(min_output_tokens, min(available, remaining)))


def estimate_structured_output_tokens(
    prompt_tokens: int,
    model_context_window: int = 32768,
    min_output_tokens: int = 256,
    max_output_tokens: int = 4096,
    safety_buffer_ratio: float = DEFAULT_SAFETY_BUFFER_RATIO,
) -> int:
    """
    Estimate a robust completion budget for schema-constrained/JSON outputs.

    This estimator intentionally avoids intent keywords and relies on prompt
    payload size + model context capacity. It prevents chronic under-budgeting
    from fixed constants (for example 512) while still respecting context
    boundaries.

    Args:
        prompt_tokens: Token count of the assembled prompt/messages.
        model_context_window: Total context window of the model.
        min_output_tokens: Hard floor for structured outputs.
        max_output_tokens: Hard cap for structured outputs.
        safety_buffer_ratio: Reserved context fraction for protocol overhead.

    Returns:
        Safe, dynamically scaled completion budget.
    """
    prompt_tokens = max(0, int(prompt_tokens))
    model_context_window = max(1024, int(model_context_window))

    # Scale with prompt size so large structured prompts are not under-budgeted.
    scaled_target = max(
        min_output_tokens,
        int(prompt_tokens * 0.45),
        int(model_context_window * 0.03),
    )
    scaled_target = min(scaled_target, max_output_tokens)

    dynamic_cap = calculate_dynamic_max_tokens(
        model_context_window=model_context_window,
        prompt_tokens=prompt_tokens,
        safety_buffer_ratio=safety_buffer_ratio,
        min_output_tokens=min_output_tokens,
    )

    return int(max(min_output_tokens, min(scaled_target, dynamic_cap)))


# ===================================================================
# Internal Helpers
# ===================================================================

def _complexity_to_tokens(complexity: str) -> int:
    """Map complexity tier to base token budget."""
    mapping = {
        "simple": MIN_OUTPUT_TOKENS,
        "moderate": DEFAULT_OUTPUT_TOKENS,
        "complex": COMPLEX_OUTPUT_TOKENS,
        "extreme": EXTREME_OUTPUT_TOKENS,
    }
    return mapping.get(complexity.lower(), DEFAULT_OUTPUT_TOKENS)


def _infer_complexity_and_tokens(query: str) -> int:
    """
    Infer query complexity and return appropriate token budget.

    Heuristics (any match bumps complexity):
      - Length: >200 chars → moderate, >500 → complex
      - Question count: multiple questions → complex
      - Keywords: "analyse", "compare", "research", "code", "explain" → complex
      - Structured requests: tables, lists, JSON → complex
      - Code patterns: backticks, programming terms → extreme
    """
    if not query:
        return MIN_OUTPUT_TOKENS

    q_lower = query.lower()
    word_count = len(query.split())
    char_count = len(query)

    # --- Base complexity from length ---
    if char_count > 500 or word_count > 80:
        base_complexity = "complex"
    elif char_count > 200 or word_count > 30:
        base_complexity = "moderate"
    else:
        base_complexity = "simple"

    # --- Keyword-based escalation ---
    complex_keywords = [
        "analyse", "analyze", "erkläre", "explain", "compare", "vergleiche",
        "research", "forsch", "zusammenfassung", "summary", "überblick",
        "übersicht", "tabellen", "table", "liste", "list", "schritte",
        "steps", "plan", "planen", "strategie", "strategy",
    ]
    extreme_keywords = [
        "code", "programm", "function", "implement", "implementiere",
        "refactor", "debug", "algorithmus", "algorithm", "architektur",
        "architecture", "design pattern", "system design",
    ]

    kw_complex = sum(1 for kw in complex_keywords if kw in q_lower)
    kw_extreme = sum(1 for kw in extreme_keywords if kw in q_lower)

    if kw_extreme >= 2:
        base_complexity = "extreme"
    elif kw_complex >= 2 or kw_extreme >= 1:
        base_complexity = "complex"

    # --- Multi-question detection ---
    question_patterns = re.findall(r'[?¿\?]|\b(what|how|why|when|where|who|which|was|wie|warum|welche|was)\b', q_lower)
    if len(question_patterns) >= 3:
        base_complexity = "complex"

    # --- Structured output request ---
    structure_patterns = [
        r'\b(json|yaml|csv|markdown|html|xml)\b',
        r'(tabellarisch|in a table|as a list|bullet point|nummeriert)',
        r'(step[- ]?by[- ]?step|Schritt für Schritt|detailliert)',
    ]
    for pat in structure_patterns:
        if re.search(pat, q_lower):
            base_complexity = "complex"
            break

    return _complexity_to_tokens(base_complexity)