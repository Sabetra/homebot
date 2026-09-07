#!/usr/bin/env python3
"""KG-Qualit鋞s-Audit: Entity-Count, Relation-Dichte, Coverage.

Minimalinvasiv: liest nur die SQLite-DB, l鋎t KEIN LLM.
Output: JSON-Report + Console-Zusammenfassung.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KG_DB_PATH = PROJECT_ROOT / "data" / "wellbeing_store.db"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(str(KG_DB_PATH))


def audit() -> dict:
    result: dict = {"ok": False, "error": None}

    if not KG_DB_PATH.exists():
        result["error"] = f"KG-DB nicht gefunden: {KG_DB_PATH}"
        return result

    try:
        conn = _conn()
        cursor = conn.cursor()

        # Tabellen entdecken
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        result["tables"] = tables

        # Versuche: nodes / edges / entities / relations (h鋟fige Namenskonventionen)
        node_tables = [t for t in tables if t.lower() in ("nodes", "node", "entities", "entity", "kg_nodes")]
        edge_tables = [t for t in tables if t.lower() in ("edges", "edge", "relations", "relation", "kg_edges")]

        result["node_tables_found"] = node_tables
        result["edge_tables_found"] = edge_tables

        entity_count = 0
        relation_count = 0
        entity_types: Counter = Counter()
        relation_types: Counter = Counter()

        # Nodes z鋒len
        for tbl in node_tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM [{tbl}];")
                cnt = cursor.fetchone()[0]
                entity_count += cnt
                result[f"count_{tbl}"] = cnt

                # Spalten-Info
                cursor.execute(f"PRAGMA table_info([{tbl}]);")
                columns = [row[1] for row in cursor.fetchall()]
                result[f"columns_{tbl}"] = columns

                # Typ-Verteilung (falls Spalte 'type', 'label', 'category' existiert)
                for type_col in ("type", "label", "category", "kind", "node_type"):
                    if type_col in columns:
                        cursor.execute(
                            f"SELECT [{type_col}], COUNT(*) FROM [{tbl}] GROUP BY [{type_col}] ORDER BY COUNT(*) DESC LIMIT 20;"
                        )
                        entity_types.update({str(r[0]): r[1] for r in cursor.fetchall()})
                        break
            except Exception as exc:
                result[f"error_{tbl}"] = str(exc)

        # Edges z鋒len
        for tbl in edge_tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM [{tbl}];")
                cnt = cursor.fetchone()[0]
                relation_count += cnt
                result[f"count_{tbl}"] = cnt

                cursor.execute(f"PRAGMA table_info([{tbl}]);")
                columns = [row[1] for row in cursor.fetchall()]
                result[f"columns_{tbl}"] = columns

                for type_col in ("type", "label", "relation_type", "edge_type", "relationship"):
                    if type_col in columns:
                        cursor.execute(
                            f"SELECT [{type_col}], COUNT(*) FROM [{tbl}] GROUP BY [{type_col}] ORDER BY COUNT(*) DESC LIMIT 20;"
                        )
                        relation_types.update({str(r[0]): r[1] for r in cursor.fetchall()})
                        break
            except Exception as exc:
                result[f"error_{tbl}"] = str(exc)

        # Falls keine spezialisierten Tabellen: alle Tabellen durchsuchen
        if entity_count == 0 and relation_count == 0:
            result["fallback_scan"] = True
            for tbl in tables:
                if tbl.startswith("sqlite_"):
                    continue
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM [{tbl}];")
                    cnt = cursor.fetchone()[0]
                    result[f"count_{tbl}"] = cnt
                    if cnt > 0:
                        cursor.execute(f"PRAGMA table_info([{tbl}]);")
                        columns = [row[1] for row in cursor.fetchall()]
                        result[f"columns_{tbl}"] = columns
                        # Sample
                        cursor.execute(f"SELECT * FROM [{tbl}] LIMIT 3;")
                        result[f"sample_{tbl}"] = [list(r) for r in cursor.fetchall()]
                except Exception as exc:
                    result[f"error_{tbl}"] = str(exc)

        conn.close()

        # Metriken berechnen
        result["entity_count"] = entity_count
        result["relation_count"] = relation_count
        result["entity_type_distribution"] = dict(entity_types) if entity_types else {}
        result["relation_type_distribution"] = dict(relation_types) if relation_types else {}

        # Relation-Dichte (edges / (nodes * (nodes-1) / 2) f黵 ungerichtet)
        if entity_count > 1:
            max_edges = entity_count * (entity_count - 1) // 2
            density = relation_count / max_edges if max_edges > 0 else 0.0
        else:
            density = 0.0
        result["relation_density"] = round(density, 6)

        # Community-Detection-Tauglichkeit
        # Thresholds: >500 Entities, >0.001 Dichte, >5 Relation-Typen
        cd_ready = (
            entity_count >= 500
            and density >= 0.001
            and len(relation_types) >= 5
        )
        result["community_detection_ready"] = cd_ready
        result["cd_checks"] = {
            "entities_gte_500": entity_count >= 500,
            "density_gte_0.001": density >= 0.001,
            "relation_types_gte_5": len(relation_types) >= 5,
        }

        result["ok"] = True

    except Exception as exc:
        result["error"] = str(exc)
        import traceback
        result["traceback"] = traceback.format_exc()

    return result


def main() -> int:
    r = audit()

    output_dir = PROJECT_ROOT / "monitoring" / "kg_quality"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "kg_audit_latest.json"
    out_path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    # Console-Zusammenfassung
    print("=" * 60)
    print("  KG-Qualit鋞s-Audit")
    print("=" * 60)

    if not r.get("ok"):
        print(f"  FEHLER: {r.get('error', 'Unbekannt')}")
        return 1

    print(f"  Tabellen:            {r.get('tables', [])}")
    print(f"  Entity-Count:        {r.get('entity_count', 0)}")
    print(f"  Relation-Count:      {r.get('relation_count', 0)}")
    print(f"  Relation-Dichte:     {r.get('relation_density', 0):.6f}")
    print(f"  Entity-Typen:        {len(r.get('entity_type_distribution', {}))}")
    print(f"  Relation-Typen:      {len(r.get('relation_type_distribution', {}))}")
    print()
    print("  Community-Detection-Tauglichkeit:")
    cd = r.get("cd_checks", {})
    print(f"    Entities >= 500:    {'JA' if cd.get('entities_gte_500') else 'NEIN'}")
    print(f"    Dichte >= 0.001:   {'JA' if cd.get('density_gte_0.001') else 'NEIN'}")
    print(f"    Rel-Typen >= 5:    {'JA' if cd.get('relation_types_gte_5') else 'NEIN'}")
    print(f"  --> CD ready:        {'JA' if r.get('community_detection_ready') else 'NEIN'}")
    print()
    print(f"  Vollst鋘diger Report: {out_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())