from decimal import Decimal

from adapters.venues.fake_account import FakeReadOnlyAccountGateway
from domain.account import AccountSnapshot
from domain.trading import Balance

from tests.helpers import NOW


def test_fake_account_gateway_exposes_only_read_snapshot() -> None:
    snapshot = AccountSnapshot(
        account_id="account-1",
        venue_id="binance",
        balances=(Balance("BTC", Decimal("0.5"), Decimal("0.5")),),
        fetched_at=NOW,
    )
    gateway = FakeReadOnlyAccountGateway((snapshot,))

    result = gateway.fetch_account("account-1")

    assert result == snapshot
    assert result.available("BTC") == Decimal("0.5")
    assert not hasattr(gateway, "submit")
