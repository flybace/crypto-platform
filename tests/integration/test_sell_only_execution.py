from adapters.venues.fake_exchange import FakeExchangeGateway
from application.execution import SellOnlyExecutionService
from domain.trading import Side

from tests.helpers import make_context, make_intent, make_limits


def test_service_wires_precheck_to_fake_exchange_once() -> None:
    gateway = FakeExchangeGateway()
    service = SellOnlyExecutionService(gateway)

    attempt = service.submit(make_intent(), make_limits(), make_context())

    assert attempt.submitted is True
    assert attempt.receipt is not None
    assert len(gateway.submissions) == 1


def test_service_never_calls_gateway_for_buy_in_sell_only() -> None:
    gateway = FakeExchangeGateway()
    service = SellOnlyExecutionService(gateway)

    attempt = service.submit(make_intent(side=Side.BUY), make_limits(), make_context())

    assert attempt.submitted is False
    assert "BUY_NOT_ALLOWED" in attempt.decision.reasons
    assert gateway.submissions == []
