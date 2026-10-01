"""Venue adapters; each connector owns its exchange-specific rules."""

from .fake_exchange import FakeExchangeGateway
from .fake_account import FakeReadOnlyAccountGateway
from .binance_spot import BinanceSpotPublicAdapter
from .fake_market_data import FakeMarketDataGateway
from .okx_spot import OkxSpotPublicAdapter
from .binance_public_rest import BinanceSpotPublicRestGateway
from .okx_public_rest import OkxSpotPublicRestGateway
from .binance_history import BinanceSpotHistoryGateway
from .okx_history import OkxSpotHistoryGateway
from .bybit_history import BybitSpotHistoryGateway
from .bybit_public_rest import BybitSpotPublicRestGateway
from .bybit_spot import BybitSpotPublicAdapter
from .websockets_transport import WebSocketTransportError, WebsocketsJsonConnector

__all__ = [
    "BinanceSpotPublicAdapter",
    "FakeExchangeGateway",
    "FakeReadOnlyAccountGateway",
    "FakeMarketDataGateway",
    "OkxSpotPublicAdapter",
    "BinanceSpotPublicRestGateway",
    "OkxSpotPublicRestGateway",
    "BinanceSpotHistoryGateway",
    "OkxSpotHistoryGateway",
    "BybitSpotHistoryGateway",
    "BybitSpotPublicRestGateway",
    "BybitSpotPublicAdapter",
    "WebSocketTransportError",
    "WebsocketsJsonConnector",
]
