"""
Tool Profiles (SOTA Declarative, 2026-08)
==========================================

Declarative Tool-Availability pro Tab/Mode — inspiriert vom Masters of AI Harness
YAML-Frontmatter-Pattern.

Jeder Tab-Mode hat ein eigenes ToolProfile, das steuert:
- Welche Tools verfügbar sind (Allowlist)
- Dateisystem-Root und Read/Write-Permissions
- Größenlimits und Depth-Limits

Design:
- Single Source of Truth für Tool-Verfügbarkeit
- Erweiterbar (neue Profile einfach hinzufügen)
- Fallback auf 'main_chat' bei unbekanntem Mode
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# Default-Workspace-Root
_DEFAULT_ROOT = str(Path(__file__).resolve().parent.parent)


# ────────────────────────────────────────────────────────────────────
# Finance-Tool-Partition (Single Source of Truth, 2026-08-24)
# ────────────────────────────────────────────────────────────────────
# FINANCE_CORE: read-only Finance-Tools für den ReAct-Chat
#   (Finance-Intent-Override in react_agent._tool_schemas_for_state).
#   Bewusst OHNE FINANCE_WRITE_TOOLS -- Schreiboperationen bleiben in
#   der dedizierten Finance-Pipeline (kein capability loss: Pipeline
#   bleibt voll verfügbar, ReAct bekommt nur die Lese-Tools).
#   `finance_sql_query` ist der bewusste Escape-Hat (volle DB-Lesbarkeit).
FINANCE_CORE: List[str] = [
    "finance_search_transactions",
    "finance_query_transactions",
    "finance_list_accounts",
    "finance_list_categories",
    "finance_balance_at",
    "finance_sum_category_costs",
    "finance_sum_counterparty_costs",
    "finance_top_counterparty_expenses",
    "finance_budget_status",
    "finance_monthly_report",
    "finance_expense_forecast",
    "finance_get_schema_context",
    "finance_sql_query",
]

# Schreib-/Verwaltungs-Tools: NUR über die dedizierte Finance-Pipeline.
# Nie Teil eines ReAct-Tool-Pools (Progressive Disclosure, 2026-08-24).
FINANCE_WRITE_TOOLS: List[str] = [
    "finance_apply_rules",
    "finance_assign_category",
    "finance_link_transfer",
    "finance_relink_transfers",
    "finance_repair_statement_header",
    "finance_set_budget",
    "finance_suggest_categories",
    "finance_unlink_transfer",
]

# Spezialisierte Analyse-Tools (read-only) -- gehören zum Finance-Tab-Pool,
# werden aber im ReAct-Chat nur via Finance-Intent + Retriever aktiv.
FINANCE_ANALYTICS: List[str] = [
    "finance_aggregate",
    "finance_budget_vs_actual_analysis",
    "finance_check_statement_import_completeness",
    "finance_cost_structure_analysis",
    "finance_detect_statement_settlement_gaps",
    "finance_expense_anomaly_detection",
    "finance_expense_trend_break_detection",
    "finance_list_rules",
    "finance_list_statements_with_incomplete_balances",
    "finance_list_transfer_candidates",
    "finance_list_transfer_links",
    "finance_recurring_expense_analysis",
    "finance_savings_potential_analysis",
]

FINANCE_READ_TOOLS: List[str] = FINANCE_CORE + FINANCE_ANALYTICS
FINANCE_ALL: List[str] = FINANCE_CORE + FINANCE_WRITE_TOOLS + FINANCE_ANALYTICS


@dataclass(frozen=True)
class ToolProfile:
    """Declaratives Tool-Profil für einen Tab/Mode."""

    # Erlaubte Tool-Namen (müssen mit tool_schemas.py übereinstimmen)
    allowed_tools: List[str] = field(default_factory=list)

    # Dateisystem-Konfiguration
    fs_root: Optional[str] = None
    fs_read: bool = False
    fs_write: bool = False

    # Limits
    max_file_size_mb: float = 2.0
    max_search_depth: int = 5
    max_search_results: int = 200
    max_read_chars: int = 50_000

    # Beschreibung (für Logging/Dashboard)
    description: str = ""

    def __post_init__(self) -> None:
        # fs_root normalisieren
        root = self.fs_root
        if root is not None:
            object.__setattr__(self, "fs_root", os.path.expanduser(root))


# ---------------------------------------------------------------------------
# SOTA: Declarative Tool Profiles (wie Masters of AI Harness Frontmatter)
# ---------------------------------------------------------------------------

TOOL_PROFILES: Dict[str, ToolProfile] = {
    "main_chat": ToolProfile(
        allowed_tools=[
            "web_search",
            "rag_search",
            "file_reader",
            "file_writer",
            "pdf_extract",
            "list_directory",
            "search_files",
            "code_executor",
            "calculator",
            "canvas",
        ],
        fs_root=_DEFAULT_ROOT,
        fs_read=True,
        fs_write=True,
        max_file_size_mb=2.0,
        max_search_depth=5,
        description="Vollständiger Tool-Zugriff für Hauptchat",
    ),

    "finance_tab": ToolProfile(
        # 2026-08-24 (Progressive Disclosure): Finance-Read-Tools (CORE +
        # ANALYTICS) gehören zum Finance-Tab-Pool. Schreib-Tools bleiben
        # bewusst ausgeschlossen (dedizierte Finance-Pipeline, fs_write=False).
        allowed_tools=[
            "rag_search",
            "file_reader",
            "calculator",
        ] + FINANCE_READ_TOOLS,
        fs_root=_DEFAULT_ROOT,
        fs_read=True,
        fs_write=False,  # Read-only für Finance!
        max_file_size_mb=5.0,  # Größer für CSV/Excel
        max_search_depth=3,
        description="Eingeschränkter Zugriff für Finance-Tab (Read-only FS)",
    ),

    "wellbeing_tab": ToolProfile(
        allowed_tools=[
            "rag_search",
        ],
        fs_root=None,
        fs_read=False,  # KEIN FS-Zugriff!
        fs_write=False,
        description="Privacy-First: Nur RAG, kein Dateisystem-Zugriff",
    ),

    "settings_tab": ToolProfile(
        allowed_tools=[
            "file_reader",
        ],
        fs_root=_DEFAULT_ROOT,
        fs_read=True,
        fs_write=False,
        max_file_size_mb=1.0,
        description="Settings: Nur Konfig-Dateien lesen",
    ),
}


def get_profile(mode: str) -> ToolProfile:
    """Holt das ToolProfile für einen Mode (Fallback: main_chat)."""
    return TOOL_PROFILES.get(mode, TOOL_PROFILES["main_chat"])


def is_tool_allowed(tool_name: str, mode: str) -> bool:
    """Prüft ob ein Tool im gegebenen Mode erlaubt ist."""
    profile = get_profile(mode)
    return tool_name in profile.allowed_tools


def get_available_tool_schemas(mode: str) -> List[str]:
    """Gibt die erlaubten Tool-Namen für einen Mode zurück."""
    return list(get_profile(mode).allowed_tools)


def has_fs_read(mode: str) -> bool:
    """Prüft ob FS-Lesen im Mode erlaubt ist."""
    return get_profile(mode).fs_read


def has_fs_write(mode: str) -> bool:
    """Prüft ob FS-Schreiben im Mode erlaubt ist."""
    return get_profile(mode).fs_write


def filter_tool_schemas(schemas: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    """Filtert eine Liste von Tool-Schemas nach dem erlaubten Profil.

    Args:
        schemas: Liste von OpenAI-Tool-Schemas (wie von tool_schemas.get_tool_schemas()).
        mode: Tab-Mode (z.B. 'main_chat', 'finance_tab', 'wellbeing_tab').

    Returns:
        Gefilterte Schema-Liste.
    """
    allowed = set(get_profile(mode).allowed_tools)
    return [s for s in schemas if s["function"]["name"] in allowed]


__all__ = [
    "ToolProfile",
    "TOOL_PROFILES",
    "FINANCE_CORE",
    "FINANCE_WRITE_TOOLS",
    "FINANCE_ANALYTICS",
    "FINANCE_READ_TOOLS",
    "FINANCE_ALL",
    "get_profile",
    "is_tool_allowed",
    "get_available_tool_schemas",
    "has_fs_read",
    "has_fs_write",
    "filter_tool_schemas",
]