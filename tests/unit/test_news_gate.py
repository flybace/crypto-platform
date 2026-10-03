"""Unit tests for the replay news gate (no future leak, entries blocked)."""

from datetime import UTC, datetime, timedelta

from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine
from domain.candle import Candle, CandleInterval
from domain.market import MarketType


def make_candles(closes: list[str]) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for index, close in enumerate(closes):
        opened = start + timedelta(days=index)
        rows.append(
            Candle(
                venue_id="binance",
                market_type=MarketType.SPOT,
                instrument_key="binance:spot:BTC/USDT",
                native_symbol="BTCUSDT",
                interval=CandleInterval.DAY,
                open_time=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=close,
                high=close,
                low=close,
                close=close,
                volume="1",
                quote_volume=close,
                trade_count=1,
            )
        )
    return tuple(rows)


# Rise -> dip -> rise: momentum(lookback=3, threshold=0) opens on the way up,
# exits on the dip, and re-opens on the second rise.
CLOSES = [str(100 + i) for i in range(12)] + ["108", "106", "104"] + [str(104 + i) for i in range(12)]


def momentum_config(**overrides) -> CandleBacktestConfig:
    return CandleBacktestConfig(
        strategy_id="momentum", fee_bps=0, slippage_bps=0,
        parameters={"lookback": 3, "threshold": "0"}, **overrides,
    )


def risk_event(published_at: datetime) -> dict[str, object]:
    return {
        "published_at": published_at.isoformat(),
        "symbols": ["BTC/USDT"],
        "sentiment": "risk",
        "risk_level": "high",
        "title": "Major exchange hacked",
    }


def run(config, candles, events=()):
    return CandleBacktestEngine().run(
        candles, config=config, run_id="gate-test", dataset_id="ds", news_events=events,
    )


def test_news_gate_blocks_entries() -> None:
    candles = make_candles(CLOSES)
    plain = run(momentum_config(), candles)
    assert plain.orders > 0
    gated = run(
        momentum_config(news_gate=True, news_block_hours=24 * 30),
        candles,
        [risk_event(candles[0].open_time)],
    )
    assert gated.news_blocked_entries > 0
    assert gated.orders < plain.orders


def test_news_gate_does_not_block_exits() -> None:
    candles = make_candles(CLOSES)
    # Risk event starts right after the first entry; the dip exit must still fire.
    gated = run(
        momentum_config(news_gate=True, news_block_hours=24 * 30),
        candles,
        [risk_event(candles[4].open_time)],
    )
    assert gated.news_blocked_entries > 0
    assert gated.round_trips >= 1  # the losing position was still exited


def test_news_gate_ignores_future_events() -> None:
    candles = make_candles(CLOSES)
    plain = run(momentum_config(), candles)
    gated = run(
        momentum_config(news_gate=True, news_block_hours=24 * 30),
        candles,
        [risk_event(candles[-1].close_time + timedelta(days=30))],
    )
    assert gated.news_blocked_entries == 0
    assert gated.orders == plain.orders


def test_news_gate_ignores_non_risk_events() -> None:
    candles = make_candles(CLOSES)
    plain = run(momentum_config(), candles)
    positive = {
        "published_at": candles[0].open_time.isoformat(),
        "symbols": ["BTC/USDT"],
        "sentiment": "positive",
        "risk_level": "low",
        "title": "ETF inflows hit record",
    }
    gated = run(
        momentum_config(news_gate=True, news_block_hours=24 * 30), candles, [positive],
    )
    assert gated.news_blocked_entries == 0
    assert gated.orders == plain.orders


def test_news_gate_window_expires() -> None:
    candles = make_candles(CLOSES)
    # Block only the first 24h; entries after the window must go through.
    gated = run(
        momentum_config(news_gate=True, news_block_hours=24),
        candles,
        [risk_event(candles[0].open_time)],
    )
    assert gated.orders > 0
