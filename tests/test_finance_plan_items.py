"""AP1 Finance-Plan-Items (FORECAST_UX, 2026-09-16).

Validiert die DAO-Ebene fuer Plan-Items (``finance/db_schema.py``):

* Schema: ``forecast_plan_items`` + ``forecast_plan_journal`` existieren
* CRUD: create/get/list/update/status/delete mit Fail-Fast-Validierung
* Optimistic Locking: ``PlanRevisionConflict`` bei abweichender Revision
* Idempotenz: ``client_token`` verhindert Duplikate
* Journal: created/updated/deleted/restored mit before-/after-Snapshots
* Undo: Gegenrevisionen (created -> Loeschung, updated/deleted -> Restore)
* Isolation: Bankdaten (Konten, Ziele, Buchungen) bleiben unangetastet

Außerdem: Forecast-Extension AP1 (``finance/tools.py``):
``include_manual_plan`` + ``manual_start_balance`` auf
``cash_flow_forecast`` (deterministisch, byte-kompatible Defaults,
kein Future-Leak, Multi-Currency ohne Waehrungsmischen).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance.db_schema import (  # noqa: E402
    FinanceDB,
    PlanItem,
    PlanRevisionConflict,
)
from finance.tools import FinanceTools  # noqa: E402

EUR_IBAN = "DE89370400440532013000"
OTHER_IBAN = "DE42500105175487360033"


@pytest.fixture(autouse=True)
def _stub_finance_embeddings(monkeypatch):
    """Import-Kette abfangen (embedding-Stack braucht kein echter Modell-Laden)."""
    stub = types.ModuleType("finance.embeddings")

    class _StubEmbeddingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubFinanceEmbeddingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubFinanceEmbeddingService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubFinanceEmbeddingIndex:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    stub.FinanceEmbeddingClient = _StubEmbeddingClient
    stub.FinanceEmbeddingIndex = _StubFinanceEmbeddingIndex
    stub.FinanceEmbeddingService = _StubFinanceEmbeddingService
    stub.get_finance_embedding_client = lambda *a, **k: _StubEmbeddingClient()
    stub.get_finance_embedding_index = lambda *a, **k: _StubFinanceEmbeddingIndex()
    stub.get_finance_embedding_service = lambda *a, **k: _StubFinanceEmbeddingService()
    monkeypatch.setitem(sys.modules, "finance.embeddings", stub)
    yield


@pytest.fixture
def db(tmp_path) -> FinanceDB:
    """DB mit zwei Konten (Filter-Tests)."""
    db = FinanceDB(str(tmp_path / "plan_items.db"))
    bank_id = db.upsert_bank("Example Bank", bic="EXBKDEFF", country_code="DE")
    db.upsert_account(bank_id, EUR_IBAN, account_holder="Example Holder", currency="EUR")
    db.upsert_account(bank_id, OTHER_IBAN, account_holder="Second Holder", currency="EUR")
    return db


def _create(db: FinanceDB, **overrides: Any) -> PlanItem:
    base: Dict[str, Any] = dict(
        iban=EUR_IBAN,
        kind="expense",
        amount_cents=4999,
        due_date="2026-10-15",
        currency="EUR",
        title="Miete Oktober",
        source_type="manual",
    )
    base.update(overrides)
    return db.create_plan_item(**base)


class TestSchema:
    def test_tables_exist(self, db: FinanceDB) -> None:
        with db._connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                ).fetchall()
            }
        assert "forecast_plan_items" in names
        assert "forecast_plan_journal" in names
        assert "fpi_iban_due" in names
        assert "fpi_status" in names
        assert "fpj_item" in names

    def test_journal_survives_deletion(self, db: FinanceDB) -> None:
        """Journal ist bewusst ohne FK: Eintraege ueberleben die Loeschung."""
        item = _create(db)
        db.delete_plan_item(item.id)
        with db._connect() as conn:
            fk = conn.execute(
                "PRAGMA foreign_key_list(forecast_plan_journal)"
            ).fetchall()
        assert fk == []


class TestCreate:
    def test_roundtrip(self, db: FinanceDB) -> None:
        item = _create(db, title="  Miete Oktober  ", iban="de89370400440532013000 ")
        assert item.id > 0
        assert item.iban == EUR_IBAN  # normalisiert (Uppercase, whitespace-frei)
        assert item.kind == "expense"
        assert item.amount_cents == 4999
        assert item.due_date == "2026-10-15"
        assert item.currency == "EUR"
        assert item.title == "Miete Oktober"  # whitespace-kollabiert
        assert item.status == "active"
        assert item.source_type == "manual"
        assert item.revision == 1
        assert item.category_id is None
        assert item.client_token is None
        assert item.notes is None

    def test_get_plan_item_roundtrip(self, db: FinanceDB) -> None:
        item = _create(db)
        assert db.get_plan_item(item.id) == item

    def test_get_unknown_raises(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError):
            db.get_plan_item(9999)

    def test_unknown_iban_raises(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="unknown account"):
            _create(db, iban="DE00000000000000000000")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"kind": "savings"},
            {"amount_cents": 0},
            {"amount_cents": -100},
            {"amount_cents": True},
            {"due_date": "15.10.2026"},
            {"currency": "EU"},
            {"status": "pending"},
            {"source_type": "magic"},
            {"category_id": 12345},
        ],
    )
    def test_invalid_fields_raise(
        self, db: FinanceDB, overrides: Dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError):
            _create(db, **overrides)


class TestIdempotency:
    def test_same_client_token_returns_existing(self, db: FinanceDB) -> None:
        first = _create(db, client_token="ui-token-1")
        second = db.create_plan_item(
            iban=EUR_IBAN,
            kind="expense",
            amount_cents=9999,  # bewusste Abweichung: darf NICHT ueberschreiben
            due_date="2026-11-15",
            client_token="ui-token-1",
        )
        assert second.id == first.id
        assert second.amount_cents == 4999
        assert second.due_date == "2026-10-15"
        assert len(db.list_plan_items()) == 1

    def test_blank_token_is_no_token(self, db: FinanceDB) -> None:
        first = _create(db, client_token="  ")
        second = _create(db, title="Anderes")
        assert first.id != second.id
        assert len(db.list_plan_items()) == 2


class TestUpdate:
    def test_update_increments_revision(self, db: FinanceDB) -> None:
        item = _create(db)
        updated = db.update_plan_item(
            item.id,
            item.revision,
            amount_cents=5999,
            due_date="2026-11-15",
        )
        assert updated.revision == 2
        assert updated.amount_cents == 5999
        assert updated.due_date == "2026-11-15"
        assert updated.title == "Miete Oktober"  # unveraendert
        assert updated.kind == "expense"

    def test_update_writes_journal(self, db: FinanceDB) -> None:
        item = _create(db)
        db.update_plan_item(item.id, item.revision, amount_cents=7777)
        entries = db.get_plan_journal(item.id)
        assert [e.action for e in entries] == ["updated", "created"]
        before = json.loads(entries[0].before_json)
        after = json.loads(entries[0].after_json)
        assert before["amount_cents"] == 4999
        assert after["amount_cents"] == 7777
        assert entries[0].revision == 2

    def test_revision_conflict_raises(self, db: FinanceDB) -> None:
        item = _create(db)
        db.update_plan_item(item.id, item.revision, amount_cents=5999)
        with pytest.raises(PlanRevisionConflict) as exc:
            db.update_plan_item(item.id, 1, amount_cents=6999)
        assert isinstance(exc.value, ValueError)  # API-Kompatibilitaet
        assert exc.value.actual_revision == 2
        assert db.get_plan_item(item.id).amount_cents == 5999  # unveraendert

    def test_update_no_fields_raises(self, db: FinanceDB) -> None:
        item = _create(db)
        with pytest.raises(ValueError, match="no fields"):
            db.update_plan_item(item.id, item.revision)

    def test_update_unknown_iban_raises(self, db: FinanceDB) -> None:
        item = _create(db)
        with pytest.raises(ValueError, match="unknown account"):
            db.update_plan_item(
                item.id, item.revision, iban="DE00999999999999999999"
            )

    def test_set_status(self, db: FinanceDB) -> None:
        item = _create(db)
        paused = db.set_plan_item_status(item.id, "paused", item.revision)
        assert paused.status == "paused"
        assert paused.revision == 2
        rejected = db.set_plan_item_status(item.id, "rejected", 2)
        assert rejected.status == "rejected"
        with pytest.raises(ValueError):
            db.set_plan_item_status(item.id, "pending", 3)


class TestDeleteAndUndo:
    def test_delete_writes_journal(self, db: FinanceDB) -> None:
        item = _create(db)
        db.delete_plan_item(item.id)
        with pytest.raises(ValueError):
            db.get_plan_item(item.id)
        entries = db.get_plan_journal(item.id)
        assert entries[0].action == "deleted"
        before = json.loads(entries[0].before_json)
        assert before["id"] == item.id
        assert before["title"] == "Miete Oktober"

    def test_undo_update_restores_before(self, db: FinanceDB) -> None:
        item = _create(db)
        db.update_plan_item(item.id, 1, amount_cents=99999, title="Geaendert")
        restored = db.undo_plan_change(item.id)
        assert restored.id == item.id
        assert restored.amount_cents == 4999
        assert restored.title == "Miete Oktober"
        assert restored.revision == 3
        assert [e.action for e in db.get_plan_journal(item.id)] == [
            "restored",
            "updated",
            "created",
        ]

    def test_undo_delete_restores_same_id(self, db: FinanceDB) -> None:
        item = _create(db)
        db.delete_plan_item(item.id)
        with pytest.raises(ValueError):
            db.get_plan_item(item.id)
        restored = db.undo_plan_change(item.id)
        assert restored.id == item.id
        assert restored.title == "Miete Oktober"
        assert restored.amount_cents == 4999
        assert len(db.list_plan_items()) == 1  # exakt einmal da

    def test_undo_created_deletes_item(self, db: FinanceDB) -> None:
        item = _create(db)
        removed = db.undo_plan_change(item.id)
        assert removed.id == item.id
        with pytest.raises(ValueError):
            db.get_plan_item(item.id)
        assert [e.action for e in db.get_plan_journal(item.id)] == [
            "deleted",
            "created",
        ]

    def test_undo_without_journal_raises(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="no journal entry"):
            db.undo_plan_change(424242)


class TestList:
    def test_filters(self, db: FinanceDB) -> None:
        a = _create(db, title="A", due_date="2026-10-01", amount_cents=100)
        b = _create(
            db, title="B", due_date="2026-11-01", kind="income", amount_cents=200
        )
        c = _create(
            db,
            title="C",
            iban=OTHER_IBAN,
            due_date="2026-12-01",
            source_type="detected",
            amount_cents=300,
        )
        db.set_plan_item_status(c.id, "paused", c.revision)

        assert [i.id for i in db.list_plan_items()] == [a.id, b.id, c.id]
        assert [i.id for i in db.list_plan_items(status="paused")] == [c.id]
        assert [i.id for i in db.list_plan_items(kind="income")] == [b.id]
        assert [i.id for i in db.list_plan_items(iban=OTHER_IBAN)] == [c.id]
        assert [i.id for i in db.list_plan_items(source_type="detected")] == [c.id]
        window = db.list_plan_items(due_from="2026-11-01", due_to="2026-11-30")
        assert [i.id for i in window] == [b.id]

    def test_invalid_filter_raises(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError):
            db.list_plan_items(status="bogus")
        with pytest.raises(ValueError):
            db.list_plan_items(kind="bogus")


class TestIsolation:
    def test_bank_data_untouched(self, db: FinanceDB) -> None:
        """Plan-Item-CRUD + Undo beruehren Bankdaten nicht (Kern-Ausfallvermeidung)."""

        def _accounts() -> List[Dict[str, Any]]:
            with db._connect() as conn:
                return [
                    dict(r)
                    for r in conn.execute("SELECT * FROM accounts ORDER BY id")
                ]

        accounts_before = _accounts()
        goal_id = db.upsert_goal(
            name="Urlaub",
            iban=EUR_IBAN,
            target_cents=100000,
            currency="EUR",
            target_date="2026-12-01",
        )
        goal_before = db.get_goal(goal_id)
        with db._connect() as conn:
            tx_before = conn.execute(
                "SELECT COUNT(*) AS n FROM transactions"
            ).fetchone()["n"]

        # Vollstaendiger CRUD-Zyklus inkl. Undo, Endzustand: geloescht
        item = _create(db, client_token="iso-1")
        db.set_plan_item_status(item.id, "paused", 1)
        db.undo_plan_change(item.id)      # updated  -> restored
        db.delete_plan_item(item.id)
        db.undo_plan_change(item.id)      # deleted  -> restored
        db.delete_plan_item(item.id)      # Endzustand: weg
        assert db.list_plan_items() == []

        assert _accounts() == accounts_before
        assert db.get_goal(goal_id) == goal_before
        with db._connect() as conn:
            tx_after = conn.execute(
                "SELECT COUNT(*) AS n FROM transactions"
            ).fetchone()["n"]
        assert tx_after == tx_before


# ---------------------------------------------------------------------------
# Forecast-Extension AP1 (DoD#2/#3): include_manual_plan + manual_start_balance
# ---------------------------------------------------------------------------

REF = "2026-09-16"  # Mittwoch; Restmonat 2026-09-16..2026-09-30


def _forecast_months(end_year: int, end_month: int, count: int) -> List[tuple]:
    """``count`` Kalendermonate bis (und inkl.) (end_year, end_month), aufsteigend."""
    months: List[tuple] = []
    year, month = end_year, end_month
    for _ in range(count):
        months.append((year, month))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(months))


def _seed_forecast_history(db: FinanceDB, *, iban: str, currency: str, tag: str) -> None:
    """12 Monate Historie (2025-09..2026-08): Gehalt, Miete, variabel, Netflix."""
    transactions: List[Dict[str, Any]] = []
    for year, month in _forecast_months(2026, 8, 12):
        ym = f"{year:04d}-{month:02d}"
        transactions.append(
            {
                "booking_date": f"{ym}-01",
                "amount": 5000.0,
                "currency": currency,
                "counterparty": "Example Employer",
            }
        )
        transactions.append(
            {
                "booking_date": f"{ym}-03",
                "amount": -1200.0,
                "currency": currency,
                "counterparty": "Example Landlord",
            }
        )
        transactions.append(
            {
                "booking_date": f"{ym}-10",
                "amount": -(300.0 + 50.0 * month),
                "currency": currency,
                "counterparty": "Example Grocer",
            }
        )
        transactions.append(
            {
                "booking_date": f"{ym}-12",
                "amount": -13.99,
                "currency": currency,
                "counterparty": "Netflix",
            }
        )
    db.persist_statement_import(
        bank_name="Synthetic Bank",
        bank_bic=None,
        bank_country_code=None,
        iban=iban,
        account_holder="Test Account",
        currency=currency,
        account_type=None,
        source_pdf_hash=tag,
        source_filename=None,
        period_start="2025-09-01",
        period_end=None,
        opening_balance=10000.0,
        closing_balance=0.0,
        transactions=transactions,
    )


@pytest.fixture
def forecast_db(tmp_path) -> FinanceDB:
    """DB mit 12 Monaten Historie auf dem EUR-Konto."""
    db = FinanceDB(str(tmp_path / "forecast_plan.db"))
    _seed_forecast_history(db, iban=EUR_IBAN, currency="EUR", tag="forecast-plan-history")
    return db


@pytest.fixture
def forecast_tools(forecast_db: FinanceDB) -> FinanceTools:
    return FinanceTools(db=forecast_db)


class TestCashFlowForecastManualPlan:
    """Forecast AP1 auf ``cash_flow_forecast``.

    Konventionen: Determinismus, kein Future-Leak (D4), byte-kompatible
    Defaults (ohne Opt-ins keine neuen Keys), Plan-Items bleiben isoliert
    (keine Buchungen/Goals), Multi-Currency mischt keine Waehrungen (D6).
    """

    def _call(self, tools: FinanceTools, **params: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "reference_date": REF,
            "iban": EUR_IBAN,
            "forecast_months": 6,
        }
        base.update(params)
        return tools.cash_flow_forecast(base)

    def test_defaults_stay_byte_compatible(self, forecast_tools: FinanceTools) -> None:
        result = self._call(forecast_tools)
        assert result["success"] is True
        assert "plan" not in result
        assert "rest_month" not in result
        assert result["balance"] is not None
        assert "start_balance_source" not in result["balance"]
        for entry in result["results"]:
            for month in entry["months"]:
                for key in (
                    "manual_plan",
                    "net_with_plan",
                    "balance_with_plan",
                    "balance_low_with_plan",
                    "balance_high_with_plan",
                ):
                    assert key not in month

    def test_rest_month_and_month_plan_keys(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        _create(forecast_db, title="Miete Rest", amount_cents=50000, due_date="2026-09-25")
        _create(
            forecast_db,
            title="Zulage",
            kind="income",
            amount_cents=150000,
            due_date="2026-10-10",
        )
        _create(forecast_db, title="Horizont", amount_cents=200000, due_date="2027-06-15")

        result = self._call(forecast_tools, include_manual_plan=True)
        assert result["success"] is True

        rest = result["rest_month"]
        assert rest["period_start"] == "2026-09-16"
        assert rest["period_end"] == "2026-09-30"
        assert rest["days_remaining"] == 15
        assert rest["manual_plan"] == -500.0
        assert rest["net_with_plan"] == -500.0

        start = result["balance"]["start_balance"]
        assert rest["balance_with_plan"] == round(start - 500.0, 2)

        plan = result["plan"]
        assert plan["count"] == 2
        assert plan["beyond_horizon"] == 1
        assert [item["due_date"] for item in plan["items"]] == [
            "2026-09-25",
            "2026-10-10",
        ]

        months = result["results"][0]["months"]
        assert months[0]["month"] == "2026-10"
        assert months[0]["manual_plan"] == 1500.0
        assert months[0]["net_with_plan"] == round(months[0]["net"] + 1500.0, 2)
        assert months[0]["balance_with_plan"] == round(months[0]["balance"] + 1500.0, 2)
        assert months[1]["manual_plan"] == 0.0
        assert months[1]["net_with_plan"] == months[1]["net"]
        # Kette: kumulierter Plan-Offset bleibt pro Monat exakt
        assert months[2]["balance_with_plan"] == round(months[2]["balance"] + 1500.0, 2)
        assert months[5]["balance_with_plan"] == round(months[5]["balance"] + 1500.0, 2)
        assert any(note.startswith("Manual-Plan:") for note in result["notes"])

    def test_manual_start_balance_marker_and_chain(
        self, forecast_tools: FinanceTools
    ) -> None:
        result = self._call(forecast_tools, manual_start_balance=2500.0)
        assert result["success"] is True
        assert result["balance"]["start_balance"] == 2500.0
        assert result["balance"]["start_balance_source"] == "manual"
        # D2: wirkt auch ohne include_manual_plan
        assert "plan" not in result
        months = result["results"][0]["months"]
        assert months[0]["balance"] == round(2500.0 + months[0]["net"], 2)

    def test_manual_start_balance_with_rest_month(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        _create(forecast_db, title="Rest-Ausgabe", amount_cents=10000, due_date="2026-09-20")
        result = self._call(
            forecast_tools, include_manual_plan=True, manual_start_balance=1000.0
        )
        rest = result["rest_month"]
        assert rest["manual_plan"] == -100.0
        assert rest["balance_with_plan"] == 900.0
        months = result["results"][0]["months"]
        assert months[0]["balance"] == round(1000.0 + months[0]["net"], 2)

    def test_invalid_manual_start_balance(self, forecast_tools: FinanceTools) -> None:
        for bad in ("abc", float("nan")):
            result = self._call(forecast_tools, manual_start_balance=bad)
            assert result["success"] is False
            assert result["error_class"] == "invalid_param"

    def test_items_before_reference_ignored(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        past = _create(forecast_db, title="Vergangenheit", amount_cents=99999, due_date="2026-09-01")
        future = _create(forecast_db, title="Zukunft", amount_cents=10000, due_date="2026-10-05")
        result = self._call(forecast_tools, include_manual_plan=True)
        assert result["plan"]["count"] == 1
        assert result["plan"]["items"][0]["id"] == future.id
        assert result["rest_month"]["manual_plan"] == 0.0
        # Item bleibt in der DAO lesbar (nur die Prognose ignoriert es, D4)
        assert [item.id for item in forecast_db.list_plan_items(status="active")] == [
            past.id,
            future.id,
        ]

    def test_only_active_items_count(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        active = _create(forecast_db, title="A", amount_cents=10000, due_date="2026-10-05")
        paused = _create(forecast_db, title="P", amount_cents=20000, due_date="2026-10-06")
        forecast_db.set_plan_item_status(paused.id, "paused", paused.revision)
        done = _create(forecast_db, title="D", amount_cents=30000, due_date="2026-10-07")
        forecast_db.set_plan_item_status(done.id, "done", done.revision)

        result = self._call(forecast_tools, include_manual_plan=True)
        assert result["plan"]["count"] == 1
        assert result["plan"]["items"][0]["id"] == active.id
        months = result["results"][0]["months"]
        assert months[0]["manual_plan"] == -100.0

    def test_multi_currency_no_mixing(self, tmp_path) -> None:
        db = FinanceDB(str(tmp_path / "multi_forecast.db"))
        _seed_forecast_history(db, iban=EUR_IBAN, currency="EUR", tag="multi-eur")
        _seed_forecast_history(db, iban=OTHER_IBAN, currency="CHF", tag="multi-chf")
        db.create_plan_item(
            iban=EUR_IBAN, kind="expense", amount_cents=10000,
            due_date="2026-09-25", currency="EUR", title="EUR-Rest",
        )
        db.create_plan_item(
            iban=OTHER_IBAN, kind="income", amount_cents=20000,
            due_date="2026-09-26", currency="CHF", title="CHF-Rest",
        )
        db.create_plan_item(
            iban=EUR_IBAN, kind="expense", amount_cents=11100,
            due_date="2026-10-05", currency="EUR", title="EUR-Monat",
        )
        db.create_plan_item(
            iban=OTHER_IBAN, kind="income", amount_cents=22200,
            due_date="2026-10-06", currency="CHF", title="CHF-Monat",
        )
        tools = FinanceTools(db=db)

        result = tools.cash_flow_forecast(
            {"reference_date": REF, "forecast_months": 2, "include_manual_plan": True}
        )
        assert result["success"] is True
        assert result["currencies"] == ["CHF", "EUR"]

        rest = result["rest_month"]
        assert rest["period_start"] == "2026-09-16"
        assert rest["period_end"] == "2026-09-30"
        assert rest["days_remaining"] == 15
        # D6: kein gemischter Betrag im Restmonat
        assert "manual_plan" not in rest
        assert "net_with_plan" not in rest
        assert "balance_with_plan" not in rest
        assert result["balance"] is None

        plan = result["plan"]
        assert plan["count"] == 4
        assert plan["beyond_horizon"] == 0
        assert [item["due_date"] for item in plan["items"]] == [
            "2026-09-25",
            "2026-09-26",
            "2026-10-05",
            "2026-10-06",
        ]

        by_currency = {entry["currency"]: entry for entry in result["results"]}
        assert by_currency["EUR"]["months"][0]["manual_plan"] == -111.0
        assert by_currency["CHF"]["months"][0]["manual_plan"] == 222.0

    def test_goals_and_plan_together(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        forecast_db.upsert_goal(
            name="Urlaub",
            iban=EUR_IBAN,
            target_cents=100000,
            currency="EUR",
            target_date="2027-06-01",
            monthly_rate_cents=15000,
        )
        _create(forecast_db, title="Plan-Ausgabe", amount_cents=7500, due_date="2026-10-15")

        result = self._call(forecast_tools, include_goals=True, include_manual_plan=True)
        assert result["success"] is True
        assert result["goals"] is not None
        assert result["goals"]["count"] == 1
        assert result["plan"]["count"] == 1
        months = result["results"][0]["months"]
        assert months[0]["goals_draw"] > 0
        assert months[0]["manual_plan"] == -75.0
        assert months[0]["net_with_goals"] == round(
            months[0]["net"] - months[0]["goals_draw"], 2
        )
        assert months[0]["net_with_plan"] == round(months[0]["net"] - 75.0, 2)
        # Die Overlays bleiben unabhaengig (separate Keys, keine Kopplung)
        assert months[0]["balance_with_plan"] == round(months[0]["balance"] - 75.0, 2)
        assert months[0]["balance_with_goals"] == round(
            months[0]["balance"] - months[0]["goals_draw"], 2
        )

    def test_deterministic(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        _create(forecast_db, title="A", amount_cents=10000, due_date="2026-10-05")
        first = self._call(
            forecast_tools, include_manual_plan=True, manual_start_balance=1234.5
        )
        second = self._call(
            forecast_tools, include_manual_plan=True, manual_start_balance=1234.5
        )
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_forecast_call_is_read_only(
        self, forecast_db: FinanceDB, forecast_tools: FinanceTools
    ) -> None:
        with forecast_db._connect() as conn:
            before = {
                "tx": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
                "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            }
        _create(forecast_db, title="A", amount_cents=5000, due_date="2026-10-05")
        result = self._call(
            forecast_tools, include_manual_plan=True, manual_start_balance=999.0
        )
        assert result["success"] is True
        with forecast_db._connect() as conn:
            after = {
                "tx": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
                "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            }
        assert after == before






