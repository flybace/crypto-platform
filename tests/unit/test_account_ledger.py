from decimal import Decimal

import pytest

from domain.account import LedgerEntry, LedgerProjection
from tests.helpers import NOW


def test_ledger_projection_rebuilds_balances_from_entries() -> None:
    projection = LedgerProjection({"USDT": "10"})
    projection.apply(LedgerEntry("entry-1", "account-1", "binance", "USDT", Decimal("5"), "DEPOSIT", NOW))
    projection.apply(LedgerEntry("entry-2", "account-1", "binance", "USDT", Decimal("-2"), "FEE", NOW))

    assert projection.available("usdt") == Decimal("13")
    assert projection.snapshot() == {"USDT": Decimal("13")}


def test_ledger_projection_rejects_negative_result() -> None:
    projection = LedgerProjection()

    with pytest.raises(ValueError, match="would become negative"):
        projection.apply(LedgerEntry("entry-1", "account-1", "binance", "BTC", Decimal("-1"), "SELL", NOW))
