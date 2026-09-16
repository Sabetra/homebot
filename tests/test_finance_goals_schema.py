"""Finance SOTA Phase 2 (Sparziele / Sinking Funds) — Schema, DAO, Tools, Registrierung.

Validiert die Phase-2-Oberfläche (Doku: docs/03_FINANCE_MODULE.md §19):

* DoD #1: goals + goal_contributions existieren auf jeder FRESH DB
* DoD #2: Neustart auf derselben DB behält Ziel-Daten (Auto-CREATE)
* DoD #3: DAO-Invarianten (1 Buchung = 1 Ziel, CASCADE, Fortschritt, as-of)
* DoD #4: Projektion (Rate-Priorität explicit > planned > history, as-of, kein Future-Leak)
* DoD #5: Kandidaten (Periode > 45 d, stabil, unzugeordnet, Währungstrennung)
* DoD #6: cash_flow_forecast include_goals (Default: AUS)
* DoD #7: Registrierung in Dispatch / Schemas / Profiles / FinanceTools

Alle Referenzdaten sind synthetisch und deterministisch (feste Referenzdaten).
"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance.db_schema import FinanceDB  # noqa: E402
from finance.tools import FinanceTools  # noqa: E402

EUR_IBAN = "DE89370400440532013000"
CH_IBAN = "CH9300762011623852957"
REF = "2026-08-01"  # festes Referenzdatum der Szenarien


@pytest.fixture(autouse=True)
def _stub_finance_embeddings(monkeypatch):
    """Import-Kette abfangen (Embedding-Stack lädt in Tests kein echtes Modell)."""
    stub = types.ModuleType("finance.embeddings")

    class _Stub:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    stub.FinanceEmbeddingClient = _Stub
    stub.FinanceEmbeddingIndex = _Stub
    stub.FinanceEmbeddingService = _Stub
    stub.get_finance_embedding_client = lambda *a, **k: _Stub()
    stub.get_finance_embedding_index = lambda *a, **k: _Stub()
    stub.get_finance_embedding_service = lambda *a, **k: _Stub()
    monkeypatch.setitem(sys.modules, "finance.embeddings", stub)
    yield


def _tx(booking_date: str, amount: float, counterparty: str, currency: str = "EUR") -> Dict[str, Any]:
    return {
        "booking_date": booking_date,
        "amount": amount,
        "currency": currency,
        "counterparty": counterparty,
    }


def _import(
    db: FinanceDB,
    *,
    iban: str,
    currency: str,
    transactions: List[Dict[str, Any]],
    tag: str,
    period_start: str,
    opening_balance: float = 0.0,
) -> int:
    """Synthetische Kontoumsätze importieren (Determinismus: keine Duplikate)."""
    _, account_id, _, inserted, duplicates = db.persist_statement_import(
        bank_name="Synthetic Bank",
        bank_bic=None,
        bank_country_code=None,
        iban=iban,
        account_holder="Test Account",
        currency=currency,
        account_type=None,
        source_pdf_hash=tag,
        source_filename=None,
        period_start=period_start,
        period_end=None,
        opening_balance=opening_balance,
        closing_balance=0.0,
        transactions=transactions,
    )
    assert duplicates == 0, f"{tag}: unerwartete Duplikate"
    assert inserted == len(transactions), f"{tag}: nicht alle Buchungen eingefügt"
    return account_id


def _tx_ids_by_counterparty(db: FinanceDB, counterparty: str) -> List[int]:
    facts = db.list_analysis_facts()
    return [int(f["transaction_id"]) for f in facts if f["counterparty"] == counterparty]


@pytest.fixture
def goals_db(tmp_path) -> FinanceDB:
    return FinanceDB(str(tmp_path / "goals.db"))


@pytest.fixture
def goals_tools(goals_db) -> FinanceTools:
    return FinanceTools(goals_db)


class TestDoD1_Schema:
    """DoD #1: goals + goal_contributions auf jeder frischen DB."""

    def test_fresh_db_has_goal_tables(self, goals_db):
        with goals_db._connect() as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
                )
            }
        assert {"goals", "goal_contributions"} <= names
        assert {"goals_active", "gc_goal", "gc_tx"} <= names

    def test_goal_contributions_contract(self, goals_db):
        with goals_db._connect() as conn:
            unique_columns = [
                [column["name"] for column in conn.execute(
                    "SELECT name FROM pragma_index_info(?)", (index["name"],)
                )]
                for index in conn.execute("PRAGMA index_list(goal_contributions)")
                if index["unique"]
            ]
            foreign_keys = {
                (row["from"], row["table"], row["to"], row["on_delete"])
                for row in conn.execute("PRAGMA foreign_key_list(goal_contributions)")
            }
        assert ["transaction_id"] in unique_columns
        assert ("goal_id", "goals", "id", "CASCADE") in foreign_keys
        assert ("transaction_id", "transactions", "id", "CASCADE") in foreign_keys

    def test_reinitialized_db_keeps_goal_data(self, tmp_path, goals_db):
        """DoD #2: zweiter Start auf derselben DB → Auto-CREATE schlägt nicht fehl, Daten bleiben."""
        path = str(tmp_path / "goals.db")
        db1 = FinanceDB(path)
        _import(
            db1,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-07-01", 250.0, "Sparverein")],
            tag="reinit",
            period_start="2026-07-01",
        )
        gid = db1.upsert_goal(
            name="Auto", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000
        )
        db1.assign_contribution(gid, _tx_ids_by_counterparty(db1, "Sparverein")[0])

        db2 = FinanceDB(path)  # zweiter Start auf derselben Datei
        assert db2.get_goal(gid) is not None
        assert db2.get_goal(gid).monthly_rate_cents == 25000
        progress = db2.goal_progress(gid)
        assert progress["progress_cents"] == 25000
        assert progress["contribution_count"] == 1



class TestDoD3_DaoInvariants:
    """DoD #3: 1 Buchung = 1 Ziel, CASCADE, Fortschritt ohne Doppelzählen."""

    def test_upsert_goal_roundtrip(self, goals_db):
        gid = goals_db.upsert_goal(
            name="Urlaub",
            iban=EUR_IBAN,
            target_cents=500000,
            monthly_rate_cents=50000,
            target_date="2027-06-01",
            status="active",
        )
        goal = goals_db.get_goal(gid)
        assert goal.name == "Urlaub"
        assert goal.iban == EUR_IBAN
        assert goal.target_cents == 500000
        assert goal.monthly_rate_cents == 50000
        assert goal.target_date == "2027-06-01"
        assert goal.currency == "EUR"
        assert goal.status == "active"

    @pytest.mark.parametrize(
        "iban,currency,expected",
        [(EUR_IBAN, "CHF", "CHF"), (CH_IBAN, "", "CHF"), ("AT611904300234573201", "", "EUR")],
    )
    def test_goal_currency_default_and_override(self, goals_db, iban, currency, expected):
        goal_id = goals_db.upsert_goal(
            name="Currency", iban=iban, target_cents=10000, currency=currency
        )
        assert goals_db.get_goal(goal_id).currency == expected

    def test_upsert_goal_validation(self, goals_db):
        db = goals_db
        with pytest.raises(ValueError):
            db.upsert_goal(name="", iban=EUR_IBAN, target_cents=100)
        with pytest.raises(ValueError):
            db.upsert_goal(name="X", iban=EUR_IBAN, target_cents=0)
        with pytest.raises(ValueError):
            db.upsert_goal(name="X", iban=EUR_IBAN, target_cents=100, monthly_rate_cents=-1)
        with pytest.raises(ValueError):
            db.upsert_goal(name="X", iban=EUR_IBAN, target_cents=100, status="bogus")
        with pytest.raises(ValueError):
            db.upsert_goal(name="X", iban=EUR_IBAN, target_cents=100, target_date="01.06.2027")
        with pytest.raises(ValueError):
            db.upsert_goal(name="X", iban=EUR_IBAN, target_cents=100, currency="EUROS")

    def test_upsert_same_key_updates_in_place(self, goals_db):
        g1 = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=100000)
        g2 = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=200000, status="paused")
        assert g1 == g2
        goal = goals_db.get_goal(g1)
        assert goal.target_cents == 200000
        assert goal.status == "paused"

    def test_one_transaction_one_goal(self, goals_db):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-01", 100.0, "Sparverein A"),
                _tx("2026-07-02", 100.0, "Sparverein B"),
            ],
            tag="one2one",
            period_start="2026-07-01",
        )
        g1 = goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=1000)
        g2 = goals_db.upsert_goal(name="B", iban=EUR_IBAN, target_cents=1000)
        tx_a = _tx_ids_by_counterparty(goals_db, "Sparverein A")[0]
        goals_db.assign_contribution(g1, tx_a)
        with pytest.raises(ValueError, match="already assigned"):
            goals_db.assign_contribution(g2, tx_a)

    def test_assign_unknown_goal_or_tx(self, goals_db):
        with pytest.raises(ValueError):
            goals_db.assign_contribution(99999, 1)
        gid = goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=100)
        with pytest.raises(ValueError):
            goals_db.assign_contribution(gid, 99999)

    def test_cascade_goal_delete(self, goals_db):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-07-01", 50.0, "Sparverein")],
            tag="cascade-goal",
            period_start="2026-07-01",
        )
        gid = goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=1000)
        goals_db.assign_contribution(gid, _tx_ids_by_counterparty(goals_db, "Sparverein")[0])
        assert len(goals_db.list_contributions(gid)) == 1
        goals_db.delete_goal(gid)
        assert goals_db.get_goal(gid) is None
        with goals_db._connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM goal_contributions").fetchone()[0]
        assert remaining == 0

    def test_cascade_transaction_delete(self, goals_db):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-07-01", 50.0, "Sparverein")],
            tag="cascade-tx",
            period_start="2026-07-01",
        )
        gid = goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=1000)
        tx_id = _tx_ids_by_counterparty(goals_db, "Sparverein")[0]
        goals_db.assign_contribution(gid, tx_id)
        assert goals_db.delete_transaction(tx_id) is True
        with goals_db._connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM goal_contributions").fetchone()[0]
        assert remaining == 0

    def test_progress_signed_and_counted_once(self, goals_db):
        """Positive Einlage + negative Auszahlung → Netto-Fortschritt, jeder Beitrag zählt 1×."""
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-06-01", 150.0, "Sparverein"),
                _tx("2026-07-01", -50.0, "Ziel-Auszahlung"),
            ],
            tag="signed",
            period_start="2026-06-01",
        )
        gid = goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=10000)
        for counterparty in ("Sparverein", "Ziel-Auszahlung"):
            goals_db.assign_contribution(gid, _tx_ids_by_counterparty(goals_db, counterparty)[0])
        progress = goals_db.goal_progress(gid)
        assert progress["progress_cents"] == 10000  # 15000 - 5000
        assert progress["contribution_count"] == 2
        assert progress["remaining_cents"] == 0
        assert progress["progress_pct"] == 100.0

    def test_progress_asof_excludes_future(self, goals_db):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-01", 100.0, "Sparverein"),
                _tx("2026-08-15", 200.0, "Sparverein"),
            ],
            tag="asof",
            period_start="2026-07-01",
        )
        gid = goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=10000)
        for tx_id in _tx_ids_by_counterparty(goals_db, "Sparverein"):
            goals_db.assign_contribution(gid, tx_id)
        asof_july = goals_db.goal_progress_asof(gid, "2026-07-31")
        assert asof_july["saved_cents"] == 10000
        assert asof_july["contribution_count"] == 1
        asof_sep = goals_db.goal_progress_asof(gid, "2026-09-01")
        assert asof_sep["saved_cents"] == 30000
        assert asof_sep["contribution_count"] == 2


class TestDoD4_GoalTools:
    """DoD #4 (Teil 1): CRUD-Tools + Beitrag-Zuweisung über die Tool-Schicht."""

    def test_upsert_tool_validation_and_money_parsing(self, goals_tools):
        t = goals_tools
        assert t.upsert_goal({"name": "X"})["success"] is False
        assert t.upsert_goal({"name": "X", "iban": EUR_IBAN})["success"] is False
        assert t.upsert_goal({"name": "X", "iban": EUR_IBAN, "target_amount": -5})["success"] is False
        bad = t.upsert_goal({"name": "X", "iban": EUR_IBAN, "target_amount": 100, "status": "bogus"})
        assert bad["success"] is False and bad["error_class"] == "invalid_param"
        res = t.upsert_goal(
            {"name": "Auto", "iban": EUR_IBAN, "target_amount": "1.000,50", "monthly_rate": 250}
        )
        assert res["success"] is True
        assert res["goal"]["target_amount"] == 1000.5
        assert res["goal"]["monthly_rate"] == 250.0
        assert res["goal"]["currency"] == "EUR"

    def test_list_goals_empty(self, goals_tools):
        res = goals_tools.list_goals({})
        assert res == {"success": True, "count": 0, "goals": []}

    def test_list_goals_includes_progress_and_projection(self, goals_db, goals_tools):
        gid = goals_db.upsert_goal(
            name="Auto", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000
        )
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-07-01", 100.0, "Sparverein")],
            tag="list",
            period_start="2026-07-01",
        )
        goals_db.assign_contribution(gid, _tx_ids_by_counterparty(goals_db, "Sparverein")[0])
        res = goals_tools.list_goals({})
        assert res["success"] is True and res["count"] == 1
        entry = res["goals"][0]
        assert entry["goal"]["goal_id"] == gid
        assert entry["progress"]["progress_cents"] == 10000
        assert entry["projection"]["months_to_target"] == 4  # ceil((100000-10000)/25000)
        assert goals_tools.list_goals({"status": "active", "iban": EUR_IBAN})["count"] == 1
        assert goals_tools.list_goals({"status": "bogus"})["success"] is False

    def test_get_goal_not_found(self, goals_tools):
        res = goals_tools.get_goal({"goal_id": 99999})
        assert res["success"] is False and res["error_class"] == "goal_not_found"

    def test_set_goal_status(self, goals_db, goals_tools):
        gid = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=1000)
        res = goals_tools.set_goal_status({"goal_id": gid, "status": "achieved"})
        assert res["success"] is True and res["status"] == "achieved"
        assert goals_db.get_goal(gid).status == "achieved"
        bad = goals_tools.set_goal_status({"goal_id": gid, "status": "bogus"})
        assert bad["success"] is False and bad["error_class"] == "invalid_param"

    def test_delete_goal(self, goals_db, goals_tools):
        gid = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=1000)
        res = goals_tools.delete_goal({"goal_id": gid})
        assert res["success"] is True and res["removed_contributions"] == 0
        assert goals_tools.get_goal({"goal_id": gid})["error_class"] == "goal_not_found"
        assert goals_tools.delete_goal({"goal_id": 424242})["success"] is False

    def test_assign_and_unassign_roundtrip(self, goals_db, goals_tools):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-07-01", 120.0, "Sparverein")],
            tag="assign",
            period_start="2026-07-01",
        )
        gid = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=100000)
        tx_id = _tx_ids_by_counterparty(goals_db, "Sparverein")[0]
        res = goals_tools.assign_goal_contribution({"goal_id": gid, "transaction_id": tx_id})
        assert res["success"] is True
        assert res["progress"]["progress_cents"] == 12000
        conflict = goals_tools.assign_goal_contribution({"goal_id": gid, "transaction_id": tx_id})
        assert conflict["success"] is False and conflict["error_class"] == "conflict"

        listed = goals_tools.list_goal_contributions({"goal_id": gid})
        assert listed["count"] == 1
        contribution_id = listed["contributions"][0]["contribution_id"]
        assert listed["contributions"][0]["transaction"]["counterparty"] == "Sparverein"

        un = goals_tools.unassign_goal_contribution({"contribution_id": contribution_id})
        assert un["success"] is True
        assert goals_db.goal_progress(gid)["progress_cents"] == 0

        # Pair-Auflösung (goal_id + transaction_id)
        assert goals_tools.assign_goal_contribution({"goal_id": gid, "transaction_id": tx_id})["success"]
        pair = goals_tools.unassign_goal_contribution({"goal_id": gid, "transaction_id": tx_id})
        assert pair["success"] is True
        assert goals_tools.unassign_goal_contribution({})["error_class"] == "missing_param"


class TestDoD4_Projection:
    """DoD #4 (Teil 2): Projektion — Rate-Priorität, as-of, kein Future-Leak."""

    @staticmethod
    def _seed(db: FinanceDB, *, with_rate: bool, contributions) -> int:
        gid = db.upsert_goal(
            name="Auto",
            iban=EUR_IBAN,
            target_cents=100000,
            monthly_rate_cents=25000 if with_rate else None,
        )
        for i, (day, amount) in enumerate(contributions):
            _import(
                db,
                iban=EUR_IBAN,
                currency="EUR",
                transactions=[_tx(day, amount, f"Sparverein {i}")],
                tag=f"proj-{day}",
                period_start=day,
            )
            db.assign_contribution(gid, _tx_ids_by_counterparty(db, f"Sparverein {i}")[0])
        return gid

    def test_rate_priority_explicit_over_planned(self, goals_db, goals_tools):
        gid = goals_db.upsert_goal(
            name="Auto", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000
        )
        res = goals_tools.project_goal({"goal_id": gid, "reference_date": REF, "rate": 500})
        proj = res["projection"]
        assert proj["method"] == "explicit" and proj["rate"] == 500.0
        assert proj["achieved"] is False
        assert proj["months_left_at_rate"] == 2  # ceil(1000 / 500)
        assert proj["achieved_month"] == "2026-10"
        assert proj["series"][0] == {"month": "2026-09", "balance": 500.0}
        assert proj["series"][1] == {"month": "2026-10", "balance": 1000.0}

    def test_rate_priority_planned_over_history(self, goals_db, goals_tools):
        gid = self._seed(goals_db, with_rate=True, contributions=[("2026-07-01", 200.0)])
        res = goals_tools.project_goal({"goal_id": gid, "reference_date": REF})
        assert res["projection"]["method"] == "planned"
        assert res["projection"]["rate"] == 250.0

    def test_rate_priority_history_fallback(self, goals_db, goals_tools):
        gid = self._seed(
            goals_db,
            with_rate=False,
            contributions=[
                ("2026-05-01", 200.0),
                ("2026-06-01", 200.0),
                ("2026-07-01", 200.0),
            ],
        )
        res = goals_tools.project_goal({"goal_id": gid, "reference_date": REF})
        proj = res["projection"]
        assert proj["method"] == "history"
        assert proj["rate"] == 200.0
        assert res["progress"]["saved_cents"] == 60000
        assert proj["months_left_at_rate"] == 2  # ceil(40000 / 20000)

    @pytest.mark.parametrize("with_rate", [True, False])
    def test_projection_no_future_leak(self, goals_db, goals_tools, with_rate):
        gid = self._seed(
            goals_db,
            with_rate=with_rate,
            contributions=[("2026-07-01", 100.0), ("2026-08-20", 500.0)],
        )
        early = goals_tools.project_goal({"goal_id": gid, "reference_date": "2026-08-01"})
        late = goals_tools.project_goal({"goal_id": gid, "reference_date": "2026-09-01"})
        assert early["progress"]["saved_cents"] == 10000
        assert late["progress"]["saved_cents"] == 60000
        assert early["projection"]["rate"] == (250.0 if with_rate else 100.0)
        assert late["projection"]["rate"] == (250.0 if with_rate else 300.0)

    def test_projection_achieved_and_overdue(self, goals_db, goals_tools):
        gid = self._seed(goals_db, with_rate=False, contributions=[("2026-07-01", 1000.0)])
        res = goals_tools.project_goal({"goal_id": gid, "reference_date": REF})
        assert res["projection"]["achieved"] is True
        assert res["projection"]["months_left_at_rate"] == 0
        assert res["projection"]["achieved_month"] == "2026-08"

        # target_date in der Vergangenheit → overdue
        goals_db.upsert_goal(
            name="Auto", iban=EUR_IBAN, target_cents=50000, target_date="2026-07-01"
        )
        res2 = goals_tools.project_goal({"goal_id": gid, "reference_date": REF})
        assert res2["projection"]["overdue"] is True
        assert res2["projection"]["months_until_target_date"] < 0

    def test_project_goal_missing_params(self, goals_db, goals_tools):
        gid = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=1000)
        assert goals_tools.project_goal({})["error_class"] == "missing_param"
        bad = goals_tools.project_goal({"goal_id": gid, "horizon_months": 61})
        assert bad["success"] is False and bad["error_class"] == "invalid_param"


class TestDoD5_Candidates:
    """DoD #5: Ziel-Kandidaten — Periode > 45 d, stabil, unzugeordnet, Währungstrennung."""

    def test_candidates_default_reference_date(self, goals_tools, monkeypatch):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 8, 1)

        monkeypatch.setattr("finance.tools.date", FixedDate)
        res = goals_tools.suggest_goal_candidates({})
        assert res["success"] is True
        assert res["reference_date"] == REF

    def test_candidate_period_and_currency(self, goals_db, goals_tools):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-01-01", -300.0, "Auto Club"),
                _tx("2026-04-01", -300.0, "Auto Club"),
                _tx("2026-07-01", -300.0, "Auto Club"),
                _tx("2026-08-05", -900.0, "Auto Club"),
                _tx("2026-05-01", -100.0, "Monatlich"),
                _tx("2026-06-01", -100.0, "Monatlich"),
                _tx("2026-07-01", -100.0, "Monatlich"),
                _tx("2026-07-15", -900.0, "Einmalig"),
                _tx("2026-01-01", -100.0, "Instabil"),
                _tx("2026-04-01", -900.0, "Instabil"),
                _tx("2026-07-01", -300.0, "Instabil"),
                _tx("2026-01-01", 300.0, "Einzahlung"),
                _tx("2026-04-01", 300.0, "Einzahlung"),
                _tx("2026-07-01", 300.0, "Einzahlung"),
            ],
            tag="cand-eur",
            period_start="2026-01-01",
        )
        res = goals_tools.suggest_goal_candidates({"reference_date": REF})
        assert res["success"] is True
        assert [c["counterparty"] for c in res["candidates"]] == ["Auto Club"]
        cand = res["candidates"][0]
        assert cand["currency"] == "EUR"
        assert cand["average_amount"] == 300.0
        assert cand["average_period_days"] == 90.5
        assert cand["monthly_equivalent"] == round(300.0 * 30.44 / 90.5, 2)
        assert cand["occurrences"] == 3

    def test_candidates_multi_currency(self, goals_db, goals_tools):
        for iban, cur, tag in ((EUR_IBAN, "EUR", "cand-eur"), (CH_IBAN, "CHF", "cand-chf")):
            _import(
                goals_db,
                iban=iban,
                currency=cur,
                transactions=[
                    _tx("2026-01-10", -250.0, "Kfz Versicherung", cur),
                    _tx("2026-04-10", -250.0, "Kfz Versicherung", cur),
                    _tx("2026-07-10", -250.0, "Kfz Versicherung", cur),
                ],
                tag=tag,
                period_start="2026-01-10",
            )
        res = goals_tools.suggest_goal_candidates({"reference_date": REF})
        currencies = sorted(c["currency"] for c in res["candidates"])
        assert currencies == ["CHF", "EUR"]
        assert res["total_monthly_equivalent"] is None
        for iban, currency in ((EUR_IBAN, "EUR"), (CH_IBAN, "CHF")):
            filtered = goals_tools.suggest_goal_candidates({"reference_date": REF, "iban": iban})
            assert [cand["currency"] for cand in filtered["candidates"]] == [currency]

    def test_assigned_transactions_excluded(self, goals_db, goals_tools):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-01-01", -300.0, "Auto Club"),
                _tx("2026-04-01", -300.0, "Auto Club"),
                _tx("2026-07-01", -300.0, "Auto Club"),
            ],
            tag="cand-assigned",
            period_start="2026-01-01",
        )
        assert goals_tools.suggest_goal_candidates({"reference_date": REF})["count"] == 1
        gid = goals_db.upsert_goal(name="Auto", iban=EUR_IBAN, target_cents=10000)
        for tx_id in _tx_ids_by_counterparty(goals_db, "Auto Club"):
            goals_db.assign_contribution(gid, tx_id)
        res = goals_tools.suggest_goal_candidates({"reference_date": REF})
        assert res["success"] is True and res["candidates"] == []

    @pytest.mark.parametrize("min_occurrences", [1, "invalid"])
    def test_candidate_occurrence_bounds(self, goals_tools, min_occurrences):
        assert goals_tools.suggest_goal_candidates(
            {"reference_date": REF, "min_occurrences": min_occurrences}
        )["error_class"] == "invalid_param"

    def test_candidate_invalid_reference_date(self, goals_tools):
        assert goals_tools.suggest_goal_candidates(
            {"reference_date": "2026-02-30"}
        )["error_class"] == "invalid_reference_date"


class TestDoD6_ForecastIntegration:
    """DoD #6: cash_flow_forecast include_goals (Default: AUS)."""

    def test_default_excludes_goals(self, goals_db, goals_tools):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
            ],
            tag="fc-default",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        goals_db.upsert_goal(
            name="Auto", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000
        )
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 3, "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        assert res["success"] is True
        assert "goals" not in res or res.get("goals") is None
        assert res["results"][0]["currency"] == "EUR"
        assert len(res["results"][0]["months"]) == 3
        for month in res["results"][0]["months"]:
            assert "net_with_goals" not in month
            assert month["net"] == -500.0

    def test_include_goals_adjusts_months(self, goals_db, goals_tools):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
            ],
            tag="fc-goals",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        goals_db.upsert_goal(
            name="Auto", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000
        )
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 3, "include_goals": True,
             "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        assert res["success"] is True
        assert res["goals"]["count"] == 1
        assert res["goals"]["monthly_draw_by_currency"] == {"EUR": 250.0}
        assert res["results"][0]["currency"] == "EUR"
        assert len(res["results"][0]["months"]) == 3
        for step, month in enumerate(res["results"][0]["months"], start=1):
            assert month["goals_draw"] == 250.0
            assert month["net_with_goals"] == round(month["net"] - 250.0, 2)
            assert month["balance_with_goals"] == round(month["balance"] - 250.0 * step, 2)

    def test_include_goals_paused_and_achieved_ignored(self, goals_db, goals_tools):
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-08-15", -100.0, "Miete")],
            tag="fc-paused",
            period_start="2026-08-01",
            opening_balance=1000.0,
        )
        goals_db.upsert_goal(
            name="Paused", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000,
            status="paused",
        )
        achieved = goals_db.upsert_goal(
            name="Done", iban=EUR_IBAN, target_cents=1000, monthly_rate_cents=25000,
            status="achieved",
        )
        goals_db.upsert_goal(
            name="Active", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=25000
        )
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 2, "include_goals": True,
             "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        assert res["success"] is True
        assert res["goals"]["count"] == 1
        assert res["goals"]["monthly_draw_by_currency"] == {"EUR": 250.0}
        assert achieved is not None

    def test_goals_draw_caps_at_remaining_amount(self, goals_db, goals_tools):
        """DoD#6: Draws werden an den verbleibenden Zielbetrag capped."""
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
            ],
            tag="fc-cap",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        goals_db.upsert_goal(name="Car", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=40000)
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 5, "include_goals": True, "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        months = res["results"][0]["months"]
        assert [m["goals_draw"] for m in months] == [400.0, 400.0, 200.0, 0.0, 0.0]
        assert sum(m["goals_draw"] for m in months) == 1000.0  # exakt der Restbetrag
        assert res["goals"]["goals"][0]["last_draw_month"] == 3
        assert res["goals"]["monthly_draw_by_currency"] == {"EUR": 400.0}
        for step, month in enumerate(months, start=1):
            assert month["balance_with_goals"] == round(
                month["balance"] - sum(m["goals_draw"] for m in months[:step]), 2
            )

    def test_goals_draw_stops_at_target_date(self, goals_db, goals_tools):
        """DoD#6: Nach dem Zielmonat keine weiteren Draws mehr."""
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
            ],
            tag="fc-date",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        # 4 Monate wuerden zum Fuellen reichen (1000/250); Zielmonat Nov 2026 => 3 Schritte
        goals_db.upsert_goal(
            name="Car", iban=EUR_IBAN, target_cents=100000,
            monthly_rate_cents=25000, target_date="2026-11-30")
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 5, "include_goals": True, "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        assert [m["goals_draw"] for m in res["results"][0]["months"]] == [250.0, 250.0, 250.0, 0.0, 0.0]
        assert res["goals"]["goals"][0]["last_draw_month"] == 3

    def test_goals_draw_none_when_target_date_passed(self, goals_db, goals_tools):
        """DoD#6: Bereits ueberlaufenes target_date => keine Draws mehr."""
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
            ],
            tag="fc-past",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        goals_db.upsert_goal(
            name="Car", iban=EUR_IBAN, target_cents=100000,
            monthly_rate_cents=25000, target_date="2026-08-15")
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 3, "include_goals": True, "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        assert res["goals"]["count"] == 1
        assert res["goals"]["goals"][0]["last_draw_month"] == 0
        assert res["goals"]["monthly_draw_by_currency"] == {}
        for month in res["results"][0]["months"]:
            assert "goals_draw" not in month

    def test_goals_draw_multi_goal_same_currency(self, goals_db, goals_tools):
        """DoD#6: Mehrere Ziele gleicher Waehrung: Summe der capped Schedules."""
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
            ],
            tag="fc-multi",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        goals_db.upsert_goal(name="A", iban=EUR_IBAN, target_cents=60000, monthly_rate_cents=20000)
        goals_db.upsert_goal(name="B", iban=EUR_IBAN, target_cents=10000, monthly_rate_cents=5000)
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 5, "include_goals": True, "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        assert [m["goals_draw"] for m in res["results"][0]["months"]] == [250.0, 250.0, 200.0, 0.0, 0.0]
        assert res["goals"]["monthly_draw_by_currency"] == {"EUR": 250.0}

    def test_goals_draw_with_existing_savings(self, goals_db, goals_tools):
        """DoD#6: Zugeordnete Buchungen kuerzen den Restbetrag (as-of-Fortschritt)."""
        _import(
            goals_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[
                _tx("2026-07-15", -500.0, "Miete"),
                _tx("2026-08-15", -500.0, "Miete"),
                _tx("2026-08-20", 200.0, "Sparplan Auto"),
            ],
            tag="fc-saved",
            period_start="2026-07-01",
            opening_balance=10000.0,
        )
        goal_id = goals_db.upsert_goal(
            name="Car", iban=EUR_IBAN, target_cents=100000, monthly_rate_cents=40000)
        goals_db.assign_contribution(goal_id, _tx_ids_by_counterparty(goals_db, "Sparplan Auto")[0])
        res = goals_tools.cash_flow_forecast(
            {"forecast_months": 5, "include_goals": True, "reference_date": "2026-08-31", "iban": EUR_IBAN}
        )
        goal_view = res["goals"]["goals"][0]
        assert goal_view["saved"] == 200.0
        assert goal_view["remaining"] == 800.0
        assert [m["goals_draw"] for m in res["results"][0]["months"]] == [400.0, 400.0, 0.0, 0.0, 0.0]


GOAL_TOOL_NAMES = (
    "finance_upsert_goal",
    "finance_list_goals",
    "finance_get_goal",
    "finance_set_goal_status",
    "finance_delete_goal",
    "finance_assign_goal_contribution",
    "finance_unassign_goal_contribution",
    "finance_list_goal_contributions",
    "finance_project_goal",
    "finance_suggest_goal_candidates",
)

GOAL_WRITE_TOOLS = (
    "finance_upsert_goal",
    "finance_set_goal_status",
    "finance_delete_goal",
    "finance_assign_goal_contribution",
    "finance_unassign_goal_contribution",
)


class TestDoD7_Registration:
    """DoD #7: Registrierung in FinanceTools, Dispatch, Schemas, Profiles."""

    def test_finance_tools_exposes_all_goal_methods(self):
        for name in GOAL_TOOL_NAMES:
            method = name.removeprefix("finance_")
            assert callable(getattr(FinanceTools, method)), f"FinanceTools.{method} fehlt"

    def test_dispatch_and_wrappers_registered(self):
        from agent_toolkit import AgentToolkit

        src = Path("agent_toolkit.py").read_text(encoding="utf-8")
        for name in GOAL_TOOL_NAMES:
            wrapper = f"_{name}"
            assert f'"{name}": self.{wrapper},' in src, f"Dispatch-Eintrag fehlt: {name}"
            assert callable(getattr(AgentToolkit, wrapper)), f"AgentToolkit.{wrapper} fehlt"

    def test_schemas_cover_all_goal_tools(self):
        from agent.tool_schemas import get_tool_schemas

        schemas = {schema["function"]["name"]: schema for schema in get_tool_schemas()}
        for name in GOAL_TOOL_NAMES:
            assert name in schemas, f"Tool-Schema fehlt: {name}"
            schema = schemas[name]
            assert schema["function"]["name"] == name
            assert schema["function"]["parameters"]["type"] == "object"

    def test_profiles_partition_goal_tools(self):
        from agent.tool_profiles import (
            FINANCE_ALL,
            FINANCE_READ_TOOLS,
            FINANCE_WRITE_TOOLS,
            get_available_tool_schemas,
        )

        read = set(FINANCE_READ_TOOLS)
        write = set(FINANCE_WRITE_TOOLS)
        expected_read = set(GOAL_TOOL_NAMES) - set(GOAL_WRITE_TOOLS)
        assert expected_read <= read, f"Read-Profile unvollständig: {expected_read - read}"
        assert set(GOAL_WRITE_TOOLS) <= write, f"Write-Profile unvollständig: {set(GOAL_WRITE_TOOLS) - write}"
        assert not (read & write)

        assert set(GOAL_TOOL_NAMES) <= set(FINANCE_ALL)
        available = set(get_available_tool_schemas("finance_tab"))
        assert expected_read <= available
        assert not (available & write), "finance_tab darf keine Write-Tools enthalten"
