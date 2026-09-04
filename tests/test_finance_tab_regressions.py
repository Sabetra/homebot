from __future__ import annotations

import pytest

from finance import tab as finance_tab
from finance.db_schema import _to_cents


@pytest.mark.parametrize(
    ("amount", "expected_cents"),
    [
        (1.005, 101),
        (-1.005, -101),
        (2.675, 268),
    ],
)
def test_to_cents_uses_commercial_rounding(amount: float, expected_cents: int) -> None:
    assert _to_cents(amount) == expected_cents


def test_format_eur_has_no_trailing_whitespace() -> None:
    formatted = finance_tab._format_eur(1234.5)

    assert formatted == "1.234,50"
    assert formatted == formatted.strip()


def test_relink_transfers_uses_productive_settlement_window() -> None:
    class _RecordingDB:
        received_max_days: int | None = None

        def relink_all_transfers(self, *, max_days: int) -> int:
            self.received_max_days = max_days
            return 3

    db = _RecordingDB()
    relink = getattr(finance_tab, "_relink_transfers", None)

    assert callable(relink)
    assert relink(db, productive_window_days=45) == 3
    assert db.received_max_days == 45