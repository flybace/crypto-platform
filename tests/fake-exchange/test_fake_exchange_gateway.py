import pytest

from adapters.venues.fake_exchange import FakeExchangeGateway
from domain.trading import Side

from tests.helpers import make_intent


def test_fake_exchange_returns_receipt_for_approved_sell() -> None:
    gateway = FakeExchangeGateway()

    receipt = gateway.submit(make_intent())

    assert receipt.status.value == "ACCEPTED"
    assert receipt.venue_order_id == "fake-000001"
    assert len(gateway.submissions) == 1


def test_fake_exchange_rejects_buy_even_if_called_directly() -> None:
    gateway = FakeExchangeGateway()

    with pytest.raises(ValueError, match="SELL intents only"):
        gateway.submit(make_intent(side=Side.BUY))

    assert gateway.submissions == []
