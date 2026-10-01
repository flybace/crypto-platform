from application.market_data import MarketDataService
from domain.market_status import MarketConnectionState
from web.market_status import MarketStatusView

from tests.helpers import NOW, make_snapshot


def test_market_status_view_returns_serializable_state() -> None:
    service = MarketDataService()
    service.ingest(make_snapshot(), NOW)

    response = MarketStatusView(service).get("BINANCE")

    assert response["venue_id"] == "binance"
    assert response["state"] == MarketConnectionState.CONNECTED.value
    assert response["last_sequence"] == 1
