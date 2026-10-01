from decimal import Decimal

from application.reconciliation import ReconciliationService
from domain.account import AccountSnapshot, AccountState
from domain.trading import Balance

from tests.helpers import NOW


def _external(usdt: str = "100") -> AccountSnapshot:
    return AccountSnapshot(
        account_id="account-1",
        venue_id="binance",
        balances=(Balance("USDT", Decimal(usdt), Decimal(usdt)),),
        fetched_at=NOW,
    )


def test_matching_external_snapshot_is_reconciled() -> None:
    result = ReconciliationService().reconcile(
        "account-1",
        "BINANCE",
        {"USDT": Decimal("100")},
        _external(),
        NOW,
    )

    assert result.balanced is True
    assert result.state is AccountState.RECONCILED
    assert result.differences == ()


def test_difference_requires_reconciliation_and_reports_delta() -> None:
    result = ReconciliationService().reconcile(
        "account-1",
        "binance",
        {"USDT": Decimal("80")},
        _external(),
        NOW,
    )

    assert result.balanced is False
    assert result.state is AccountState.RECONCILIATION_REQUIRED
    assert result.differences[0].asset == "USDT"
    assert result.differences[0].delta == Decimal("20")
