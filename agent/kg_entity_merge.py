"""
Shared KG-Entity-Merge-Helper (★ SOTA)
=======================================

Schema-agnostischer Helper, der zwei korrektheits-kritische Schritte beim
Mergen zweier Entitäten in einem Triple-Store atomar durchführt:

  1. **Triple-Hash-Recompute** — sobald `subject` oder `object` einer Zeile
     geändert wird, ist der gespeicherte `triple_hash` veraltet. Ohne
     Recompute überleben SPO-Duplikate die Merge-Operation, weil der UNIQUE
     Index auf `triple_hash` zwei Zeilen mit identischer (s,p,o), aber
     unterschiedlichen Hashes nicht erkennt.

  2. **Bayesian Noisy-OR Collapse bei Hash-Kollision** — wenn der neu
     berechnete Hash bereits existiert (es gab schon eine Zeile mit der
     kanonischen SPO-Form), werden die zwei Zeilen kollabiert:
        confidence_neu  = 1 - (1 - c1) * (1 - c2)   [Cap 0.99]
        mention_count   = mc1 + mc2
        metadata        = (Verbleib-Zeile bleibt, Gegenstück gelöscht)
     Damit ist das Verhalten konsistent mit der Insert-Pipeline, die bei
     Re-Extraktion derselben Aussage ebenfalls Noisy-OR anwendet.

Der Helper unterstützt zwei Schemen über PRAGMA-Reflection:
  • `agent/rag_store` (Document-KG):    PK=`triple_id`, Hash `s|p|o`
  • `wellbeing` (Psycho-KG): PK=`id`,        Hash `s_p_o`

Beide rufen denselben Helper mit ihrer jeweiligen `hash_fn` auf.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Set

logger = logging.getLogger(__name__)


def _detect_schema(conn: Any) -> Dict[str, Any]:
    """Reflect über `triples`-Tabelle: PK-Spalte + optional vorhandene Cols."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(triples)")
    rows = cur.fetchall()
    cols: Set[str] = set()
    pk_col: str = ""
    for row in rows:
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        name = row[1] if not isinstance(row, dict) else row["name"]
        is_pk = row[5] if not isinstance(row, dict) else row["pk"]
        cols.add(name)
        if is_pk and not pk_col:
            pk_col = name
    if not pk_col:
        # Fallback to common conventions
        for cand in ("triple_id", "id"):
            if cand in cols:
                pk_col = cand
                break
    if not pk_col:
        raise RuntimeError(
            "kg_entity_merge: Konnte keine Primary-Key-Spalte in `triples` ermitteln "
            f"(verfügbare Spalten: {sorted(cols)})"
        )
    return {
        "pk_col": pk_col,
        "has_confidence": "confidence" in cols,
        "has_mention_count": "mention_count" in cols,
        "has_updated_at": "updated_at" in cols,
        "has_metadata": "metadata" in cols,
    }


def merge_entity_in_triples(
    conn: Any,
    hash_fn: Callable[[str, str, str], str],
    canonical_text: str,
    old_text: str,
    *,
    schema: Dict[str, Any] | None = None,
) -> Dict[str, int]:
    """
    Rewrite alle `triples`-Zeilen, die `old_text` als subject oder object
    haben, auf `canonical_text` — mit Hash-Recompute und Kollisions-Collapse.

    Args:
        conn:           sqlite3.Connection (Caller managt commit/transaction).
        hash_fn:        (s, p, o) → triple_hash. Muss konsistent zur DB sein.
        canonical_text: Ziel-Entity-Text (Verbleib).
        old_text:       Quell-Entity-Text (wird ersetzt).
        schema:         Optional: vorab reflektiertes Schema (Performance).

    Returns:
        {'rewritten': N, 'collapsed': M}
          rewritten:  Zeilen, die in-place updated wurden (kein Konflikt).
          collapsed:  Zeilen, die mit existierender kanonischer Zeile per
                      Noisy-OR fusioniert + gelöscht wurden.
    """
    if canonical_text == old_text:
        return {"rewritten": 0, "collapsed": 0}

    if schema is None:
        schema = _detect_schema(conn)

    pk = schema["pk_col"]
    has_conf = schema["has_confidence"]
    has_mc = schema["has_mention_count"]
    has_updated = schema["has_updated_at"]

    cur = conn.cursor()

    # Selektor-Liste dynamisch — None für nicht vorhandene Spalten
    sel_conf = "confidence" if has_conf else "NULL"
    sel_mc = "mention_count" if has_mc else "NULL"

    cur.execute(
        f"SELECT {pk}, subject, predicate, object, {sel_conf}, {sel_mc}, triple_hash "
        f"FROM triples WHERE subject = ? OR object = ?",
        (old_text, old_text),
    )
    affected = cur.fetchall()

    rewritten = 0
    collapsed = 0

    for row in affected:
        # Both tuple- and Row-style cursors
        if isinstance(row, dict):
            row_pk = row[pk]
            s, p, o = row["subject"], row["predicate"], row["object"]
            row_conf = row.get(sel_conf) if has_conf else None
            row_mc = row.get(sel_mc) if has_mc else None
        else:
            row_pk, s, p, o, row_conf, row_mc, _old_hash = row

        new_s = canonical_text if s == old_text else s
        new_o = canonical_text if o == old_text else o

        # No-op safety (shouldn't happen given the WHERE clause, but defensive)
        if new_s == s and new_o == o:
            continue

        new_hash = hash_fn(new_s, p, new_o)

        # Check for collision with an *existing* canonical row
        cur.execute(
            f"SELECT {pk}, {sel_conf}, {sel_mc} "
            f"FROM triples WHERE triple_hash = ? AND {pk} != ?",
            (new_hash, row_pk),
        )
        coll = cur.fetchone()

        if coll is None:
            # No collision — safe in-place rewrite
            sets = ["subject = ?", "object = ?", "triple_hash = ?"]
            params: list = [new_s, new_o, new_hash]
            if has_updated:
                sets.append("updated_at = CURRENT_TIMESTAMP")
            cur.execute(
                f"UPDATE triples SET {', '.join(sets)} WHERE {pk} = ?",
                params + [row_pk],
            )
            rewritten += 1
        else:
            # Collision — collapse into existing canonical row via Noisy-OR
            if isinstance(coll, dict):
                keep_pk = coll[pk]
                keep_conf = coll.get(sel_conf)
                keep_mc = coll.get(sel_mc)
            else:
                keep_pk, keep_conf, keep_mc = coll

            sets: list = []
            params = []

            if has_conf:
                old_conf = float(keep_conf if keep_conf is not None else 0.5)
                add_conf = float(row_conf if row_conf is not None else 0.5)
                # Noisy-OR; cap at 0.99 (never assert absolute certainty);
                # never lower the surviving confidence.
                merged = 1.0 - (1.0 - old_conf) * (1.0 - add_conf)
                merged = min(0.99, max(old_conf, merged))
                sets.append("confidence = ?")
                params.append(merged)

            if has_mc:
                merged_mc = int(keep_mc or 1) + int(row_mc or 1)
                sets.append("mention_count = ?")
                params.append(merged_mc)

            if has_updated:
                sets.append("updated_at = CURRENT_TIMESTAMP")

            if sets:
                cur.execute(
                    f"UPDATE triples SET {', '.join(sets)} WHERE {pk} = ?",
                    params + [keep_pk],
                )

            cur.execute(f"DELETE FROM triples WHERE {pk} = ?", (row_pk,))
            collapsed += 1

    if rewritten or collapsed:
        logger.debug(
            "[KG-Merge] '%s' → '%s': %d rewritten, %d collapsed (Noisy-OR)",
            old_text, canonical_text, rewritten, collapsed,
        )

    return {"rewritten": rewritten, "collapsed": collapsed}


def recompute_and_dedupe_triple_hashes(
    conn: Any,
    hash_fn: Callable[[str, str, str], str],
) -> Dict[str, int]:
    """
    One-time / idempotent migration: re-derive `triple_hash` for every row
    from its current (subject, predicate, object) and collapse any rows that
    end up sharing a hash via Bayesian Noisy-OR (confidence) + sum
    (mention_count). Then refresh the UNIQUE index on `triple_hash`.

    Why this exists
    ---------------
    Earlier merge code paths rewrote `subject`/`object` without recomputing
    `triple_hash`. As a result existing databases contain:
      • Rows whose stored hash no longer matches their SPO (stale hash)
      • SPO duplicates that the UNIQUE index never blocked because their
        stale hashes differ

    Both are pure data corruption from past code paths and must be healed
    structurally — not papered over by query-time deduplication. After this
    migration, the UNIQUE constraint on `triple_hash` is meaningful again
    and every future merge runs through `merge_entity_in_triples`, which
    keeps the invariant.

    Args:
        conn:    sqlite3.Connection (caller manages transaction/commit).
        hash_fn: same hash function the rest of the pipeline uses.

    Returns:
        {'rows_scanned', 'hashes_rewritten', 'collapsed'}
    """
    schema = _detect_schema(conn)
    pk = schema["pk_col"]
    has_conf = schema["has_confidence"]
    has_mc = schema["has_mention_count"]
    has_updated = schema["has_updated_at"]

    sel_conf = "confidence" if has_conf else "NULL"
    sel_mc = "mention_count" if has_mc else "NULL"

    cur = conn.cursor()
    cur.execute(
        f"SELECT {pk}, subject, predicate, object, triple_hash, {sel_conf}, {sel_mc} "
        f"FROM triples"
    )
    rows = cur.fetchall()

    # First pass: bucket rows by their *correct* hash so we can decide who
    # survives a collision before mutating anything.
    buckets: Dict[str, list] = {}
    for row in rows:
        if isinstance(row, dict):
            r_pk = row[pk]
            s, p, o = row["subject"], row["predicate"], row["object"]
            stored_hash = row["triple_hash"]
            r_conf = row.get(sel_conf) if has_conf else None
            r_mc = row.get(sel_mc) if has_mc else None
        else:
            r_pk, s, p, o, stored_hash, r_conf, r_mc = row
        if s is None or p is None or o is None:
            continue
        new_hash = hash_fn(s, p, o)
        buckets.setdefault(new_hash, []).append(
            (r_pk, stored_hash, r_conf, r_mc)
        )

    rows_scanned = sum(len(v) for v in buckets.values())
    hashes_rewritten = 0
    collapsed = 0

    # If there is a UNIQUE index on triple_hash we must drop it before
    # rewriting hashes, otherwise an intermediate state would violate it.
    # We re-create it at the end.
    cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='triples' AND name LIKE '%hash%'"
    )
    existing_hash_indices = [r[0] for r in cur.fetchall()]
    unique_index_name = None
    for idx_name in existing_hash_indices:
        cur.execute(f"PRAGMA index_info({idx_name!r})")
        cols_in_idx = [r[2] for r in cur.fetchall()]
        if cols_in_idx == ["triple_hash"]:
            cur.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND name=?",
                (idx_name,),
            )
            sql_row = cur.fetchone()
            if sql_row and sql_row[0] and "UNIQUE" in sql_row[0].upper():
                unique_index_name = idx_name
                cur.execute(f"DROP INDEX {idx_name}")
                break

    for new_hash, members in buckets.items():
        if len(members) == 1:
            r_pk, stored_hash, _, _ = members[0]
            if stored_hash != new_hash:
                cur.execute(
                    f"UPDATE triples SET triple_hash = ? WHERE {pk} = ?",
                    (new_hash, r_pk),
                )
                hashes_rewritten += 1
            continue

        # Collision — pick survivor (highest confidence, then lowest pk for
        # determinism), Noisy-OR the rest into it, delete the rest.
        members_sorted = sorted(
            members,
            key=lambda m: (
                -(float(m[2]) if (has_conf and m[2] is not None) else 0.5),
                m[0],
            ),
        )
        keeper = members_sorted[0]
        keep_pk, keep_hash, keep_conf, keep_mc = keeper

        merged_conf = float(keep_conf) if (has_conf and keep_conf is not None) else 0.5
        merged_mc = int(keep_mc) if (has_mc and keep_mc is not None) else 1

        for other in members_sorted[1:]:
            o_pk, _, o_conf, o_mc = other
            if has_conf:
                add = float(o_conf) if o_conf is not None else 0.5
                merged_conf = 1.0 - (1.0 - merged_conf) * (1.0 - add)
            if has_mc:
                merged_mc += int(o_mc) if o_mc is not None else 1
            cur.execute(f"DELETE FROM triples WHERE {pk} = ?", (o_pk,))
            collapsed += 1

        sets = ["triple_hash = ?"]
        params: list = [new_hash]
        if has_conf:
            sets.append("confidence = ?")
            params.append(min(0.99, merged_conf))
        if has_mc:
            sets.append("mention_count = ?")
            params.append(merged_mc)
        if has_updated:
            sets.append("updated_at = CURRENT_TIMESTAMP")
        cur.execute(
            f"UPDATE triples SET {', '.join(sets)} WHERE {pk} = ?",
            params + [keep_pk],
        )
        if keep_hash != new_hash:
            hashes_rewritten += 1

    # Recreate UNIQUE index — now safe because all duplicates are collapsed.
    if unique_index_name:
        cur.execute(
            f"CREATE UNIQUE INDEX {unique_index_name} ON triples(triple_hash)"
        )

    if hashes_rewritten or collapsed:
        logger.info(
            "[KG-Migrate] triple_hash resync: scanned=%d, rewritten=%d, collapsed=%d (Noisy-OR)",
            rows_scanned, hashes_rewritten, collapsed,
        )

    return {
        "rows_scanned": rows_scanned,
        "hashes_rewritten": hashes_rewritten,
        "collapsed": collapsed,
    }


__all__ = ["merge_entity_in_triples", "recompute_and_dedupe_triple_hashes"]
