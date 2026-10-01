"""Historical cross-venue spread research over verified candle datasets."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from application.history_storage import HistoryStorage, HistoryStorageError, StoredDataset


class SpreadResearchError(ValueError):
    """A historical spread request cannot be evaluated safely."""


class HistoricalSpreadResearch:
    """Compare synchronized candle closes without presenting them as orders."""

    INTERVALS = frozenset({"1d", "1h", "5m"})

    def __init__(self, storage: HistoryStorage) -> None:
        self._storage = storage

    def run(
        self,
        *,
        symbol: str,
        buy_venue_id: str,
        sell_venue_id: str,
        interval: str,
        fee_bps: Decimal = Decimal("10"),
        slippage_bps: Decimal = Decimal("5"),
        min_net_spread_bps: Decimal = Decimal("0"),
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        canonical = self._symbol(symbol)
        buy_venue = self._venue(buy_venue_id)
        sell_venue = self._venue(sell_venue_id)
        if buy_venue == sell_venue:
            raise SpreadResearchError("buy and sell venues must be different")
        normalized_interval = str(interval).strip().lower()
        if normalized_interval not in self.INTERVALS:
            raise SpreadResearchError("interval must be one of 1d, 1h, 5m")
        fee = self._non_negative(fee_bps, "fee_bps")
        slippage = self._non_negative(slippage_bps, "slippage_bps")
        threshold = self._non_negative(min_net_spread_bps, "min_net_spread_bps")
        bounded_limit = max(1, min(int(limit), 200))
        lower = self._bound(start_at, "start_at")
        upper = self._bound(end_at, "end_at")
        if lower is not None and upper is not None and upper <= lower:
            raise SpreadResearchError("end_at must be after start_at")

        buy_dataset = self._dataset(buy_venue, canonical, normalized_interval)
        sell_dataset = self._dataset(sell_venue, canonical, normalized_interval)
        try:
            buy_page = self._storage.read_all(buy_dataset, start_at=lower, end_at=upper)
            sell_page = self._storage.read_all(sell_dataset, start_at=lower, end_at=upper)
        except (HistoryStorageError, ValueError) as error:
            raise SpreadResearchError("verified history is unavailable") from error

        buy_by_time = {candle.open_time: candle for candle in buy_page.items}
        sell_by_time = {candle.open_time: candle for candle in sell_page.items}
        common_times = sorted(set(buy_by_time).intersection(sell_by_time))
        cost_pct = (fee + slippage) * Decimal("2") / Decimal("100")
        observations: list[dict[str, object]] = []
        skipped_price_count = 0
        for timestamp in common_times:
            buy_candle = buy_by_time[timestamp]
            sell_candle = sell_by_time[timestamp]
            if buy_candle.close <= 0 or sell_candle.close <= 0:
                skipped_price_count += 1
                continue
            gross_pct = (sell_candle.close / buy_candle.close - Decimal("1")) * Decimal("100")
            net_pct = gross_pct - cost_pct
            observations.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "buy_close": str(buy_candle.close),
                    "sell_close": str(sell_candle.close),
                    "gross_spread_pct": str(gross_pct),
                    "net_spread_pct": str(net_pct),
                    "net_spread_bps": str(net_pct * Decimal("100")),
                }
            )
        if not observations:
            raise SpreadResearchError("the selected venues have no aligned positive-price candles")

        net_values = [Decimal(str(item["net_spread_pct"])) for item in observations]
        gross_values = [Decimal(str(item["gross_spread_pct"])) for item in observations]
        threshold_pct = threshold / Decimal("100")
        opportunity_count = sum(1 for value in net_values if value >= threshold_pct)
        alignment_denominator = min(len(buy_page.items), len(sell_page.items))
        return {
            "kind": "historical_spread_research",
            "status": "completed",
            "research_only": True,
            "symbol": canonical,
            "interval": normalized_interval,
            "buy_venue_id": buy_venue,
            "sell_venue_id": sell_venue,
            "buy_dataset": self._storage.dataset_dict(buy_dataset),
            "sell_dataset": self._storage.dataset_dict(sell_dataset),
            "requested_start_at": None if lower is None else lower.isoformat(),
            "requested_end_at": None if upper is None else upper.isoformat(),
            "aligned_candle_count": len(observations),
            "alignment_ratio_pct": self._ratio(len(common_times), alignment_denominator),
            "skipped_price_count": skipped_price_count,
            "start_at": observations[0]["timestamp"],
            "end_at": observations[-1]["timestamp"],
            "positive_count": sum(1 for value in net_values if value > 0),
            "opportunity_count": opportunity_count,
            "opportunity_ratio_pct": self._ratio(opportunity_count, len(observations)),
            "average_gross_spread_pct": str(self._average(gross_values)),
            "average_net_spread_pct": str(self._average(net_values)),
            "max_net_spread_pct": str(max(net_values)),
            "min_net_spread_pct": str(min(net_values)),
            "cost_model": {
                "fee_bps_per_leg": str(fee),
                "slippage_bps_per_leg": str(slippage),
                "total_cost_bps": str((fee + slippage) * Decimal("2")),
                "min_net_spread_bps": str(threshold),
                "transfer_cost_included": False,
                "inventory_cost_included": False,
            },
            "top_observations": sorted(
                observations,
                key=lambda item: Decimal(str(item["net_spread_pct"])),
                reverse=True,
            )[:bounded_limit],
            "recent_observations": observations[-bounded_limit:],
            "note": "按同一开盘时间对齐两市场收盘价；不包含 L2 买卖盘、成交深度、延迟、库存、转账和订单腿风险，不能直接作为下单信号。",
        }

    def _dataset(self, venue_id: str, symbol: str, interval: str) -> StoredDataset:
        try:
            dataset = self._storage.find_dataset(
                venue_id=venue_id,
                instrument_key=f"{venue_id}:spot:{symbol}",
                interval=interval,
            )
        except HistoryStorageError as error:
            raise SpreadResearchError("verified history is unavailable") from error
        if dataset is None:
            raise SpreadResearchError(f"verified history dataset was not found: {venue_id}/{symbol}/{interval}")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            raise SpreadResearchError(f"history quality gate did not pass: {venue_id}/{symbol}/{interval}")
        return dataset

    @staticmethod
    def _symbol(value: str) -> str:
        raw = str(value).strip().upper().replace("-", "/")
        parts = tuple(part.strip() for part in raw.split("/") if part.strip())
        if len(parts) != 2 or parts[0] == parts[1] or any(not part.isalnum() for part in parts):
            raise SpreadResearchError("symbol must be like BTC/USDT")
        return f"{parts[0]}/{parts[1]}"

    @staticmethod
    def _venue(value: str) -> str:
        venue = str(value).strip().lower()
        if venue not in {"binance", "okx", "bybit"}:
            raise SpreadResearchError("venue must be binance, okx, or bybit")
        return venue

    @staticmethod
    def _non_negative(value: Decimal, name: str) -> Decimal:
        try:
            parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise SpreadResearchError(f"{name} must be a decimal value") from error
        if not parsed.is_finite() or parsed < 0:
            raise SpreadResearchError(f"{name} must be finite and non-negative")
        return parsed

    @staticmethod
    def _bound(value: datetime | None, name: str) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise SpreadResearchError(f"{name} must include timezone information")
        return value.astimezone(UTC)

    @staticmethod
    def _average(values: list[Decimal]) -> Decimal:
        return sum(values, Decimal("0")) / Decimal(len(values))

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> str:
        if denominator <= 0:
            return "0.00"
        return str((Decimal(numerator) / Decimal(denominator) * Decimal("100")).quantize(Decimal("0.01")))
