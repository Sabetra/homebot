#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
finance_repair.py -- Deterministische PDF<->DB-Reparatur (Finance).

Trockenlauf (dry-run) zuerst, dann --apply mit Backup-Gate. Nutzt die
verifizierte Ground-Truth (finance_truth.json) sowie die bestehenden
Helper aus scripts/finance_pdf_reconcile.py und finance/db_schema.py
(_tx_dedup_hash). KEIN LLM im Datenpfad, keine silent-Fallbacks.

Operationen (in EINER atomaren SQLite-Transaktion):
  - Junk-Statements (ohne Periode) loeschen -> Transaktionen cascadieren.
  - Statement-Salden (opening/closing) auf verifizierte Truth-Werte setzen.
  - Ueberzaehlige DB-Transaktionen (extra_in_db) loeschen.
  - Betrags-/Vorzeichen-Fehler in-place korrigieren (amount_cents + dedup_hash).
  - Fehlende Transaktionen (missing_in_db) einfuegen -- unter UNIQUE(dedup_hash).
  - Echte Duplikate (wiederholte Truth-Hashes) NICHT still verwerfen ->
    als needs_review im Report flaggen (DB kann sie wegen UNIQUE nicht halten);
    betroffene Statements bekommen needs_review=1.

Keine Schema-Migration. Deterministisch: gleicher Input => gleicher Report.
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = r"c:\Users\bot6"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + r"\scripts")

from finance.db_schema import _tx_dedup_hash as dedup_hash  # noqa: E402
import finance_pdf_reconcile as fp  # noqa: E402  (_load_db_statements, _text_tokens)


def _utf8() -> None:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


def resolve_accounts(conn: sqlite3.Connection):
    accs = {r["account_type"]: r["id"] for r in conn.execute("SELECT id, account_type FROM accounts")}
    if "checking" in accs and "credit_card" in accs:
        return accs["checking"], accs["credit_card"]
    rows = list(conn.execute("SELECT id, account_type FROM accounts"))
    if len(rows) == 1:
        return rows[0]["id"], rows[0]["id"]
    raise SystemExit(f"Konten nicht eindeutig (checking/credit_card): {accs}")


def map_nature(kind: str) -> str:
    return "internal_transfer" if (kind or "").lower() in ("uebertrag", "transfer", "internal_transfer") else "ordinary"


def brief(t):
    return {
        "booking_date": t.get("booking_date"),
        "value_date": t.get("value_date"),
        "amount_cents": t.get("amount_cents"),
        "counterparty": (t.get("counterparty") or "")[:60],
        "purpose": (t.get("purpose") or "")[:120],
    }


def match_with_ids(truth_txs, db_txs):
    """Wie fp._match_transactions, liefert aber Truth-/DB-INDIZES (ID-basierte Ops).

    Stufe 1: (booking_date, value_date, amount_cents) exakt -> keep.
    Stufe 2: (booking_date, amount_cents), value_date ignoriert, Text-Overlap als Determinierung -> keep.
    Stufe 3: gleiche Buchungsdaten + Text-Overlap >= 2 -> wrong_amount / sign_flip (update).
    Rest: missing (truth) / extra (db).
    """
    truth_tokens = [fp._text_tokens(t) for t in truth_txs]
    db_tokens = [fp._text_tokens(d) for d in db_txs]
    db_left = list(range(len(db_txs)))
    pairs = []
    mismatches = []
    truth_left = []
    for i, t in enumerate(truth_txs):
        key = (t.get("booking_date"), t.get("value_date"), t.get("amount_cents"))
        hit = next(
            (j for j in db_left
             if (db_txs[j].get("booking_date"), db_txs[j].get("value_date"), db_txs[j].get("amount_cents")) == key),
            None,
        )
        if hit is not None:
            db_left.remove(hit)
            pairs.append((i, hit))
        else:
            truth_left.append(i)
    still_left = []
    for i in truth_left:
        t = truth_txs[i]
        key = (t.get("booking_date"), t.get("amount_cents"))
        cands = [j for j in db_left if (db_txs[j].get("booking_date"), db_txs[j].get("amount_cents")) == key]
        if cands:
            best = max(cands, key=lambda j: len(truth_tokens[i] & db_tokens[j]))
            db_left.remove(best)
            pairs.append((i, best))
        else:
            still_left.append(i)
    truth_left = still_left
    missing = []
    for i in truth_left:
        t = truth_txs[i]
        cands = [j for j in db_left
                 if db_txs[j].get("booking_date") == t.get("booking_date") and len(truth_tokens[i] & db_tokens[j]) >= 2]
        if cands:
            best = max(cands, key=lambda j: len(truth_tokens[i] & db_tokens[j]))
            d = db_txs[best]
            db_left.remove(best)
            typ = "sign_flip" if d.get("amount_cents") == -t.get("amount_cents") else "wrong_amount"
            mismatches.append((i, best, typ))
        else:
            missing.append(i)
    extra = list(db_left)
    return pairs, mismatches, missing, extra


def compute_operations(truth, conn):
    acc_bank, acc_cc = resolve_accounts(conn)
    junk, db_by_period = fp._load_db_statements(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    report = {
        "mode": "computed",
        "generated_at": now,
        "account_map": {"bank": acc_bank, "credit_card": acc_cc},
        "delete_statements": [],
        "update_statements": [],
        "delete_transactions": [],
        "update_transactions": [],
        "insert_transactions": [],
        "needs_review": [],
        "counts": {},
    }

    hash_owner = {}  # dedup_hash -> wer besetzt es (Kollisionsdetektion)
    pending_inserts = []
    n_kept = 0

    # Phase 1: Junk-Statements (ohne Periode)
    for j in junk:
        report["delete_statements"].append({
            "id": j["id"], "account_id": j["account_id"],
            "source_filename": j["source_filename"],
            "transaction_count": j["transaction_count"],
            "reason": "no_period (Teil-Auszug/Junk)",
        })

    # Phase 2: gematchte Statements
    for s in truth["statements"]:
        acc = acc_bank if s["kind"] == "bank" else acc_cc
        rows = db_by_period.get((acc, s["period_start"], s["period_end"]), [])
        if not rows:
            report["needs_review"].append({
                "kind": "missing_statement", "account_id": acc,
                "period_start": s["period_start"], "period_end": s["period_end"],
                "truth_tx_count": len(s["transactions"]),
                "note": "Statement fehlt in DB -- manuell pruefen",
            })
            continue
        if len(rows) > 1:
            keep = min(rows, key=lambda r: r["id"])
            for r in rows[1:]:
                report["delete_statements"].append({
                    "id": r["id"], "reason": "duplicate_statement", "keep_id": keep["id"],
                })
            rows = [keep]
        row = rows[0]
        stmt_id = row["id"]
        db_txs = [dict(r) for r in conn.execute(
            "SELECT * FROM transactions WHERE statement_id=? ORDER BY id", (stmt_id,))]
        truth_txs = s["transactions"]
        pairs, mismatches, missing, extra = match_with_ids(truth_txs, db_txs)

        # keep (exakt) -> Hash besetzt
        for (_ti, di) in pairs:
            h = db_txs[di]["dedup_hash"]
            hash_owner.setdefault(h, f"kept tx id={db_txs[di]['id']}")
            n_kept += 1

        # mismatches -> update (neuer Hash)
        for (ti, di, typ) in mismatches:
            t = truth_txs[ti]; d = db_txs[di]
            new_amt = t["amount_cents"]
            new_h = dedup_hash(acc, t["booking_date"], new_amt, t.get("counterparty"), t.get("purpose"))
            if new_h in hash_owner:
                report["needs_review"].append({
                    "kind": "update_hash_collision", "db_id": d["id"],
                    "statement_id": stmt_id, "new_amount_cents": new_amt,
                    "dedup_hash": new_h, "conflict": hash_owner[new_h],
                    "note": "Update wuerde UNIQUE(dedup_hash) verletzen -- NICHT angewendet",
                })
                continue
            hash_owner[new_h] = f"updated tx id={d['id']}"
            report["update_transactions"].append({
                "db_id": d["id"], "statement_id": stmt_id,
                "old_amount_cents": d["amount_cents"], "new_amount_cents": new_amt,
                "type": typ, "old_hash": d["dedup_hash"], "new_hash": new_h,
                "booking_date": t["booking_date"], "truth": brief(t),
            })

        # extra -> delete
        for di in extra:
            d = db_txs[di]
            report["delete_transactions"].append({
                "db_id": d["id"], "statement_id": stmt_id,
                "booking_date": d["booking_date"], "amount_cents": d["amount_cents"],
                "counterparty": (d["counterparty"] or "")[:60], "purpose": (d["purpose"] or "")[:120],
                "freed_hash": d["dedup_hash"],
            })

        # missing -> pending insert
        for ti in missing:
            t = truth_txs[ti]
            h = dedup_hash(acc, t["booking_date"], t["amount_cents"], t.get("counterparty"), t.get("purpose"))
            pending_inserts.append({"hash": h, "t": t, "acc": acc, "stmt_id": stmt_id,
                                    "period_start": s["period_start"], "period_end": s["period_end"]})

        # Salden -> Truth
        changed = (row["opening_balance_cents"] != s["opening_balance_cents"]) or \
                  (row["closing_balance_cents"] != s["closing_balance_cents"])
        report["update_statements"].append({
            "id": stmt_id, "kind": s["kind"], "period_start": s["period_start"],
            "old_opening": row["opening_balance_cents"], "new_opening": s["opening_balance_cents"],
            "old_closing": row["closing_balance_cents"], "new_closing": s["closing_balance_cents"],
            "changed": bool(changed),
        })

    # Phase 3: pending inserts (deterministische Reihenfolge)
    stmt_needs_review = set()
    for p in pending_inserts:
        h = p["hash"]; t = p["t"]
        if h in hash_owner:
            report["needs_review"].append({
                "kind": "duplicate_hash", "account_id": p["acc"], "period_start": p["period_start"],
                "booking_date": t["booking_date"], "amount_cents": t["amount_cents"],
                "counterparty": (t.get("counterparty") or "")[:60], "purpose": (t.get("purpose") or "")[:120],
                "dedup_hash": h, "conflict": hash_owner[h],
                "note": "Echtes Duplikat (wiederholte Buchung) -- UNIQUE(dedup_hash); keep-first, hier NICHT eingefuegt",
            })
            stmt_needs_review.add(p["stmt_id"])
            continue
        hash_owner[h] = f"inserted tx (stmt {p['stmt_id']})"
        report["insert_transactions"].append({
            "statement_id": p["stmt_id"], "account_id": p["acc"],
            "booking_date": t["booking_date"], "value_date": t.get("value_date"),
            "amount_cents": t["amount_cents"], "currency": t.get("currency") or "CHF",
            "counterparty": t.get("counterparty"), "purpose": t.get("purpose"),
            "raw_text": t.get("raw_text"), "transaction_nature": map_nature(t.get("kind")),
            "dedup_hash": h, "created_at": now, "truth": brief(t),
        })

    for u in report["update_statements"]:
        u["set_needs_review"] = 1 if u["id"] in stmt_needs_review else 0

    report["counts"] = {
        "delete_statements": len(report["delete_statements"]),
        "update_statements_total": len(report["update_statements"]),
        "update_statements_changed": sum(1 for u in report["update_statements"] if u["changed"]),
        "statements_flagged_needs_review": len(stmt_needs_review),
        "delete_transactions": len(report["delete_transactions"]),
        "update_transactions": len(report["update_transactions"]),
        "insert_transactions": len(report["insert_transactions"]),
        "needs_review": len(report["needs_review"]),
        "kept_transactions": n_kept,
        "distinct_final_hashes": len(hash_owner),
    }
    return report, acc_bank, acc_cc


def validate_chain(truth):
    """Balance-Chain je Account auf der TRUTH (soll sauber sein)."""
    out = {}
    for kind in ("bank", "creditcard"):
        stmts = [s for s in truth["statements"] if s["kind"] == kind]
        stmts.sort(key=lambda s: (s["period_start"] or "", s["period_end"] or ""))
        breaks = []
        for a, b in zip(stmts, stmts[1:]):
            if (a["closing_balance_cents"] is not None and b["opening_balance_cents"] is not None
                    and a["closing_balance_cents"] != b["opening_balance_cents"]):
                breaks.append({
                    "after_period_end": a["period_end"], "before_period_start": b["period_start"],
                    "closing": a["closing_balance_cents"], "opening": b["opening_balance_cents"],
                    "diff": b["opening_balance_cents"] - a["closing_balance_cents"],
                })
        out[kind] = {"statements": len(stmts), "chain_breaks": breaks}
    return out


def validate_preconditions(report, conn):
    """Vor APPLY: Zielpfade muessen noch existieren (Drift-Schutz)."""
    errs = []
    for d in report["delete_statements"]:
        n = conn.execute("SELECT count(*) FROM statements WHERE id=?", (d["id"],)).fetchone()[0]
        if n != 1:
            errs.append(f"delete_statement id={d['id']} fehlt")
    for u in report["update_statements"]:
        n = conn.execute("SELECT count(*) FROM statements WHERE id=?", (u["id"],)).fetchone()[0]
        if n != 1:
            errs.append(f"update_statement id={u['id']} fehlt")
    for d in report["delete_transactions"]:
        n = conn.execute("SELECT count(*) FROM transactions WHERE id=?", (d["db_id"],)).fetchone()[0]
        if n != 1:
            errs.append(f"delete_tx id={d['db_id']} fehlt")
    for u in report["update_transactions"]:
        r = conn.execute("SELECT amount_cents, dedup_hash FROM transactions WHERE id=?", (u["db_id"],)).fetchone()
        if r is None:
            errs.append(f"update_tx id={u['db_id']} fehlt")
        elif r["amount_cents"] != u["old_amount_cents"] or r["dedup_hash"] != u["old_hash"]:
            errs.append(f"update_tx id={u['db_id']} hat sich geaendert (Drift)")
    for ins in report["insert_transactions"]:
        n = conn.execute("SELECT count(*) FROM transactions WHERE dedup_hash=?", (ins["dedup_hash"],)).fetchone()[0]
        if n != 0:
            errs.append(f"insert hash={ins['dedup_hash'][:12]} existiert bereits (Drift)")
    return errs


def apply_operations(report, conn):
    con = conn
    con.execute("BEGIN IMMEDIATE")
    for d in report["delete_statements"]:
        con.execute("DELETE FROM statements WHERE id=?", (d["id"],))
    for u in report["update_statements"]:
        con.execute(
            "UPDATE statements SET opening_balance_cents=?, closing_balance_cents=?, needs_review=? WHERE id=?",
            (u["new_opening"], u["new_closing"], u.get("set_needs_review", 0), u["id"]),
        )
    for d in report["delete_transactions"]:
        con.execute("DELETE FROM transactions WHERE id=?", (d["db_id"],))
    for u in report["update_transactions"]:
        con.execute("UPDATE transactions SET amount_cents=?, dedup_hash=? WHERE id=?",
                    (u["new_amount_cents"], u["new_hash"], u["db_id"]))
    for ins in report["insert_transactions"]:
        con.execute(
            "INSERT INTO transactions (statement_id, account_id, booking_date, value_date, amount_cents,"
            " currency, counterparty, purpose, transaction_nature, raw_text, dedup_hash, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ins["statement_id"], ins["account_id"], ins["booking_date"], ins["value_date"],
             ins["amount_cents"], ins["currency"], ins["counterparty"], ins["purpose"],
             ins["transaction_nature"], ins["raw_text"], ins["dedup_hash"], ins["created_at"]),
        )
    con.execute("COMMIT")


def print_summary(report):
    c = report["counts"]
    print("")
    print("=== REPAIR SUMMARY ===")
    print(f"  Junk-Statements loeschen:         {c['delete_statements']}")
    print(f"  Statement-Salden setzen:          {c['update_statements_total']} ({c['update_statements_changed']} geaendert)")
    print(f"  Statements needs_review-Flag:     {c['statements_flagged_needs_review']}")
    print(f"  Tx loeschen (extra):              {c['delete_transactions']}")
    print(f"  Tx korrigieren (Betrag/Vorzeichen): {c['update_transactions']}")
    print(f"  Tx einfuegen (missing):           {c['insert_transactions']}")
    print(f"  needs_review (Duplikate etc.):    {c['needs_review']}")
    print(f"  Tx behalten (exakt):              {c['kept_transactions']}")
    print(f"  Distinkte finale Hashes:          {c['distinct_final_hashes']}")
    cv = report.get("chain_validation", {})
    for kind, v in cv.items():
        print(f"  Truth-Chain {kind}: {v['statements']} Statements, {len(v['chain_breaks'])} Brueche")


def main():
    _utf8()
    ap = argparse.ArgumentParser(description="Deterministische PDF<->DB-Reparatur (Finance)")
    ap.add_argument("--truth", required=True)
    ap.add_argument("--db", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Nur Report, KEINE DB-Schreibzugriffe")
    g.add_argument("--apply", action="store_true", help="Reparatur atomar anwenden")
    ap.add_argument("--yes-i-have-a-backup", action="store_true", help="Sicherheits-Gate fuer --apply")
    ap.add_argument("--out", default=None, help="Pfad fuer den Repair-Report (JSON)")
    args = ap.parse_args()

    if args.apply and not args.yes_i_have_a_backup:
        raise SystemExit("ABGELEHNT: --apply erfordert --yes-i-have-a-backup")

    truth = json.load(open(args.truth, encoding="utf-8"))
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    report, acc_bank, acc_cc = compute_operations(truth, conn)
    report["chain_validation"] = validate_chain(truth)
    print_summary(report)

    default_out = ROOT + r"\database\finance_repair_report.json"
    out = args.out or default_out

    if args.dry_run:
        try:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"\nDRY-RUN: Report geschrieben -> {out}")
        except Exception as e:
            print(f"\nDRY-RUN: (Report-Write nach {out} fehlgeschlagen: {e})")
        print("DRY-RUN: KEINE DB-Schreibzugriffe ausgefuehrt.")
        return 0

    # APPLY
    errs = validate_preconditions(report, conn)
    if errs:
        print("\nABBRUCH (Voraussetzungen verletzt / Drift erkannt):")
        for e in errs[:20]:
            print("  -", e)
        if len(errs) > 20:
            print(f"  ... {len(errs) - 20} weitere")
        return 2
    try:
        apply_operations(report, conn)
    except Exception as e:
        print(f"\nAPPLY FEHLGESCHLAGEN (zurueckgerollt): {e}")
        return 3
    print("\nAPPLY: Reparatur in einer Transaktion committed.")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"APPLY: Report -> {out}")
    except Exception as e:
        print(f"APPLY: (Report-Write fehlgeschlagen: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
