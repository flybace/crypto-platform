import pytest

from domain.instrument_registry import InstrumentRegistry

from tests.helpers import make_instrument


def test_instrument_registry_is_venue_scoped_and_stable() -> None:
    binance = make_instrument("binance")
    okx = make_instrument("okx")
    registry = InstrumentRegistry((okx, binance))

    assert registry.get("BINANCE:SPOT:BTC/USDT") == binance
    assert registry.for_venue("OKX") == (okx,)
    assert registry.all() == (binance, okx)


def test_instrument_registry_rejects_duplicate_keys() -> None:
    instrument = make_instrument()
    registry = InstrumentRegistry((instrument,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(instrument)
