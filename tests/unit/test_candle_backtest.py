from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
                volume=Decimal("1"),
                quote_volume=Decimal(close),
                trade_count=1,
            )
        )
    return tuple(rows)


def test_buy_and_hold_executes_on_next_open_and_closes_at_end() -> None:
    result = CandleBacktestEngine().run(
        make_candles(["100", "110", "120"]),
        config=CandleBacktestConfig(strategy_id="buy_and_hold", initial_quote=1000, fee_bps=0, slippage_bps=0),
        run_id="run-1",
        dataset_id="dataset-1",
    )

    assert result.orders == 2
    assert result.round_trips == 1
    assert result.final_equity == Decimal("1090.909090909090909090909091")
    assert result.total_return_pct > 0
    assert result.trade_log[0]["timestamp"].startswith("2026-01-02")


def test_sma_cross_requires_enough_history_before_emitting() -> None:
    result = CandleBacktestEngine().run(
        make_candles(["100", "101", "102", "103", "104", "105"]),
        config=CandleBacktestConfig(strategy_id="sma_cross", fast_window=2, slow_window=3, fee_bps=0, slippage_bps=0),
        run_id="run-2",
        dataset_id="dataset-2",
    )

    assert result.candle_count == 6
    assert result.orders <= 2


def test_parameterized_breakout_and_volume_strategies_use_current_bar_only() -> None:
    candles = make_candles(["100", "101", "102", "104", "103", "105", "106"])
    breakout = CandleBacktestConfig(
        strategy_id="trend_breakout",
        fee_bps=0,
        slippage_bps=0,
        parameters={"window": 2, "min_return": "0"},
    )
    assert CandleBacktestEngine.signal_at(candles, 1, breakout) is None
    assert CandleBacktestEngine.signal_at(candles, 2, breakout) == "BUY"

    volume = CandleBacktestConfig(
        strategy_id="volume_momentum",
        fee_bps=0,
        slippage_bps=0,
        parameters={"window": 2, "min_return": "0.01", "min_volume_ratio": "1.2"},
    )
    assert CandleBacktestEngine.signal_at(candles, 1, volume) is None


def test_macd_optimized_series_matches_reference_calculation() -> None:
    candles = make_candles([str(100 + ((index * 7) % 19) - index // 8) for index in range(90)])
    fast, slow, signal_period = 8, 17, 5

    def legacy_histogram(index: int) -> Decimal:
        closes = [row.close for row in candles[: index + 1]]
        macd_values = []
        for position in range(slow - 1, len(closes)):
            fast_ema = CandleBacktestEngine._ema(closes[: position + 1], fast)
            slow_ema = CandleBacktestEngine._ema(closes[: position + 1], slow)
            macd_values.append(fast_ema - slow_ema)
        if len(macd_values) < signal_period:
            return Decimal("0")
        return macd_values[-1] - CandleBacktestEngine._ema(macd_values, signal_period)

    for index in range(len(candles)):
        assert CandleBacktestEngine._macd_hist(candles, index, fast, slow, signal_period) == legacy_histogram(index)

    result = CandleBacktestEngine().run(
        candles,
        config=CandleBacktestConfig(
            strategy_id="macd_reversal",
            fee_bps=0,
            slippage_bps=0,
            parameters={"fast": fast, "slow": slow, "signal": signal_period},
        ),
        run_id="macd-run",
        dataset_id="macd-dataset",
    )
    assert result.candle_count == len(candles)
