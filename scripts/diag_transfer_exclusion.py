"""Temporaere Diagnose: Transfer-Exklusion in der Cashflow-Prognose."""
import sys
import tempfile
from datetime import date
from typing import Any, Dict, List

sys.path.insert(0, ".")
from finance.db_schema import FinanceDB  # noqa: E402
from finance.tools import FinanceTools  # noqa: E402

CH_IBAN = "CH9300762011623852957"
EUR_IBAN = "DE89370400440532013000"
REF = "2026-08-28"


def _tx(booking_date: str, amount: float, counterparty: str, currency: str = "CHF") -> Dict[str, Any]:
    return {
        "booking_date": booking_date,
        "amount": amount,
        "currency": currency,
        "counterparty": counterparty,
    }


def _import(
    db: FinanceDB, *, iban: str, currency: str, transactions: List[Dict[str, Any]], tag: str, period_start: str,
    opening_balance: float = 0.0,
) -> int:
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
    assert duplicates == 0 and inserted == len(transactions), (tag, inserted, duplicates)
    return account_id


def main() -> None:
    d = tempfile.mkdtemp()
    db = FinanceDB(d + "\\diag.db")
    tools = FinanceTools(db=db)

    txs: List[Dict[str, Any]] = []
    for year, month in [(2025, m) for m in range(9, 13)] + [(2026, m) for m in range(1, 9)]:
        ym = f"{year:04d}-{month:02d}"
        txs.append(_tx(f"{ym}-01", 5000.0, "Example Employer"))
        txs.append(_tx(f"{ym}-03", -1200.0, "Example Landlord"))
        txs.append(_tx(f"{ym}-10", -(300.0 + 50.0 * month), "Example Grocer"))
        txs.append(_tx(f"{ym}-12", -13.99, "Netflix"))
    _import(db, iban=CH_IBAN, currency="CHF", transactions=txs, tag="seed", period_start="2025-09-01", opening_balance=10000.0)
    print("seed facts:", len(db.list_analysis_facts()))

    params = {"forecast_months": 6, "lookback_months": 12, "confidence_level": 0.9, "reference_date": REF}

    def report(label: str) -> None:
        res = tools.cash_flow_forecast(params)
        if not res.get("success"):
            print(f"== {label}: ERROR {res}")
            return
        r0 = res["results"][0]
        m0 = r0["months"][0]
        fit = {k: v for k, v in r0.items() if k != "months"}
        print(f"== {label}: ccy={r0['currency']}")
        print(f"   fit-meta: {fit}")
        print(f"   sep: {m0}")
        balances = res.get("balances")
        if balances:
            print(f"   balances: {balances[0] if isinstance(balances, list) else balances}")

    report("BEFORE")

    aid = _import(db, iban=CH_IBAN, currency="CHF", transactions=[_tx("2026-08-05", -500.0, "Example Savings")], tag="t-out", period_start="2026-08-01")
    ea = _import(db, iban=EUR_IBAN, currency="EUR", transactions=[_tx("2026-08-20", 500.0, "Example Savings", "EUR")], tag="t-in", period_start="2026-08-01")
    report("UNLINKED")

    facts_u = db.list_analysis_facts()
    out500 = [f for f in facts_u if f["amount_cents"] == -50000]
    in500 = [f for f in facts_u if f["amount_cents"] == 50000 and f["currency"] == "EUR"]
    print(f"   unlinked facts: total={len(facts_u)} -500chf={len(out500)} +500eur={len(in500)}")

    out = db.query_transactions(account_id=aid, counterparty_like="savings")
    inn = db.query_transactions(account_id=ea, counterparty_like="savings")
    print("   out tx:", [(t.id, t.booking_date, t.amount_cents, t.transaction_nature) for t in out])
    print("   in  tx:", [(t.id, t.booking_date, t.amount_cents, t.transaction_nature) for t in inn])

    link_id = db.link_transfer(outgoing_tx_id=out[0].id, incoming_tx_id=inn[0].id)
    print("   link_id:", link_id, " links:", db.list_transfer_links())

    conn = db._connect()
    rows = conn.execute(
        "SELECT id, counterparty, amount_cents, currency, transaction_nature, account_id FROM transactions "
        "WHERE lower(counterparty) LIKE '%savings%'"
    ).fetchall()
    print("   db rows:", [(r["id"], r["amount_cents"], r["currency"], r["transaction_nature"]) for r in rows])
    print("   transfer_links raw:", conn.execute("SELECT * FROM transfer_links").fetchall())

    facts_l = db.list_analysis_facts()
    out500l = [f for f in facts_l if f["amount_cents"] == -50000]
    in500l = [f for f in facts_l if f["amount_cents"] == 50000 and f["currency"] == "EUR"]
    print(f"   linked facts: total={len(facts_l)} -500chf={len(out500l)} +500eur={len(in500l)}")
    report("LINKED")


if __name__ == "__main__":
    main()

