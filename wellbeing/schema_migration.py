"""Phase E (2026-09-01): idempotente De-Klinifizierung des Wellbeing-DB-Schemas.

Renamed legacy clinical table/index names to neutral "wellbeing" names and the
``planned_interventions`` column to ``planned_steps``.  MUST run BEFORE the DDL
(``CREATE TABLE IF NOT EXISTS``) in ``WellbeingDatabase._init_schema()`` so that a
legacy DB's old tables are renamed in place instead of an empty new table being
created beside them (which would strand the data).

Guarantees:
- Tables: in-place ``ALTER TABLE ... RENAME TO`` — metadata-only, no row
  copies.  SQLite 3.26+ (here 3.49.1) carries FK references along
  (``legacy_alter_table=OFF``).
- Indexes: SQLite has NO ``ALTER INDEX ... RENAME`` (only table/column
  renames are supported).  Because a table rename rewrites the index ``sql``
  in ``sqlite_master`` to the new table name, we ``DROP`` the old index and
  re-``CREATE`` it under the new name using its (already-updated) definition.
- Idempotent: every rename is conditional (old present, new absent) and a
  ``_schema_meta`` sentinel marks completion.  A re-run is a no-op.
- Fails loud on conflict (both old and new object present) — never silent.
- Column rename ``session_focuses.planned_interventions -> planned_steps``
  (SQLite 3.25+ ``RENAME COLUMN``), data preserved.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Definitive rename map — derived from the production DB ``sqlite_master`` dump
# (2026-09-01).  Order is explicit per object (not substring), so it is safe
# regardless of prefix overlap.
# ---------------------------------------------------------------------------
# (old_table, new_table)
TABLE_RENAMES: list[tuple[str, str]] = [
    ("psychological_insight_corrections", "wellbeing_insight_corrections"),
    ("psychological_kg_triples", "wellbeing_kg_triples"),
    ("psychological_embeddings", "wellbeing_embeddings"),
    ("psychological_sessions", "wellbeing_sessions"),
    ("psychological_profiles", "wellbeing_profiles"),
    ("psychological_insights", "wellbeing_insights"),
    ("treatment_plans", "care_plans"),
    ("screening_results", "self_check_results"),
    ("alliance_scores", "engagement_scores"),
    ("homework_tasks", "practice_tasks"),
    ("case_formulations_v2", "context_formulations_v2"),
    ("case_formulations", "context_formulations"),
    ("mbc_observations", "checkin_observations"),
    ("stage_assessments", "progress_stages"),
    ("outcome_assessments", "progress_assessments"),
]

# (old_index, new_index)
INDEX_RENAMES: list[tuple[str, str]] = [
    ("idx_treatment_plans_user_status", "idx_care_plans_user_status"),
    ("idx_stage_assessments_user", "idx_progress_stages_user"),
    ("idx_mbc_user_instrument", "idx_checkin_user_instrument"),
    ("idx_case_form_v2_user", "idx_context_form_v2_user"),
    ("idx_alliance_session", "idx_engagement_session"),
    ("idx_screening_session", "idx_selfcheck_session"),
    ("idx_screening_user", "idx_selfcheck_user"),
    ("idx_homework_user", "idx_practice_user"),
    ("idx_outcome_session", "idx_progress_session"),
    ("idx_case_user", "idx_context_form_user"),
]

# (table, old_column, new_column)
COLUMN_RENAMES: list[tuple[str, str, str]] = [
    ("session_focuses", "planned_interventions", "planned_steps"),
]

# Sentinel key in _schema_meta marking a completed migration.
SENTINEL_KEY = "declinicalize_v1"


def _exists(conn: sqlite3.Connection, kind: str, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?", (kind, name)
    ).fetchone()
    return row is not None


def migrate_wellbeing_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rename legacy clinical schema objects to neutral names (idempotent).

    Must be called with an open connection (any pragmas already set) and is
    responsible for its own commit.  Returns a stats dict.  Raises RuntimeError
    on a name conflict (both old and new object present).
    """
    stats: dict[str, Any] = {
        "tables": 0,
        "indexes": 0,
        "columns": 0,
        "already_migrated": False,
    }

    # Ensure FK references are carried along with table renames (SQLite 3.26+
    # default is OFF already; set explicitly for clarity/robustness).
    conn.execute("PRAGMA legacy_alter_table=OFF")

    # Sentinel check (creates _schema_meta if it does not exist yet).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    done = conn.execute(
        "SELECT value FROM _schema_meta WHERE key=?", (SENTINEL_KEY,)
    ).fetchone()
    if done is not None:
        stats["already_migrated"] = True
        logger.debug("Wellbeing schema already de-clinicalised (sentinel present)")
        return stats

    # ── Tables ────────────────────────────────────────────────────────────
    for old, new in TABLE_RENAMES:
        if not _exists(conn, "table", old):
            continue  # fresh DB (already new name) or unrelated → no-op
        if _exists(conn, "table", new):
            raise RuntimeError(
                f"Schema rename conflict: both '{old}' and '{new}' tables exist"
            )
        conn.execute(f'ALTER TABLE "{old}" RENAME TO "{new}"')
        stats["tables"] += 1

    # ── Indexes ───────────────────────────────────────────────────────────
    # SQLite has no ``ALTER INDEX ... RENAME``.  After the table renames above,
    # each clinical index's ``sql`` in sqlite_master already references the NEW
    # table name — only the index name token still needs changing.  We capture
    # the definition, drop the old index, and recreate it under the new name.
    for old, new in INDEX_RENAMES:
        if not _exists(conn, "index", old):
            continue
        if _exists(conn, "index", new):
            raise RuntimeError(
                f"Schema rename conflict: both '{old}' and '{new}' indexes exist"
            )
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (old,)
        ).fetchone()
        if row is None or not row[0]:
            raise RuntimeError(
                f"Index '{old}' has no SQL definition (auto-created index?); "
                "cannot safely rename"
            )
        # Rewrite only the index name (first token after CREATE [UNIQUE] INDEX);
        # the table name is already the post-rename value.  Index names here are
        # unique tokens that do not appear elsewhere in the definition.
        new_sql = row[0].replace(old, new, 1)
        if new_sql == row[0]:
            raise RuntimeError(f"Failed to rename index '{old}' -> '{new}' (token not found)")
        conn.execute(f'DROP INDEX "{old}"')
        conn.execute(new_sql)
        stats["indexes"] += 1

    # ── Columns ───────────────────────────────────────────────────────────
    for table, old_col, new_col in COLUMN_RENAMES:
        if not _exists(conn, "table", table):
            continue
        cols = {
            r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        if old_col not in cols:
            continue  # already renamed (or never had it)
        if new_col in cols:
            raise RuntimeError(
                f"Schema rename conflict: table '{table}' has both "
                f"'{old_col}' and '{new_col}'"
            )
        conn.execute(
            f'ALTER TABLE "{table}" RENAME COLUMN "{old_col}" TO "{new_col}"'
        )
        stats["columns"] += 1

    # ── Sentinel ──────────────────────────────────────────────────────────
    conn.execute(
        "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, 'done')",
        (SENTINEL_KEY,),
    )
    conn.commit()

    if stats["tables"] or stats["indexes"] or stats["columns"]:
        logger.info(
            "Wellbeing schema de-clinicalised: tables=%d indexes=%d columns=%d",
            stats["tables"], stats["indexes"], stats["columns"],
        )
    return stats
