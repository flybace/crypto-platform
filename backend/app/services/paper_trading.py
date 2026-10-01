"""In-memory paper account facade backed by the real L2 paper broker."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from application.history_storage import HistoryStorage
from application.candle_backtest import CandleBacktestConfig, CandleBacktestEngine
from application.task_lifecycle import TaskLedger, TaskLifecycle
from application.paper import PaperBroker
from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel
from domain.paper import PaperAccount
from domain.trading import OrderIntent, OrderType, Side
from adapters.standalone.state_store import JsonStateError, JsonStateStore, StateStore


class PaperTradingService:
    VENUE_IDS = ("binance", "okx", "bybit")
    DEFAULT_BALANCES = {"USDT": "10000", "BTC": "0.1", "ETH": "1", "BNB": "5"}

    def __init__(
        self,
        storage: HistoryStorage,
        *,
        fee_bps: Decimal = Decimal("10"),
        state_path: str | None = None,
        state_store: StateStore | None = None,
        task_store: TaskLedger | None = None,
    ) -> None:
        self._storage = storage
        self._fee_bps = Decimal(str(fee_bps))
        self._state = state_store or (JsonStateStore(state_path) if state_path else None)
        self._task_store = task_store
        self._lock = RLock()
        self._accounts: dict[str, PaperAccount] = {}
        self._brokers: dict[str, PaperBroker] = {}
        self._records: list[dict[str, object]] = []
        self._strategy_runs: list[dict[str, object]] = []
        self._request_ids: dict[str, dict[str, object]] = {}
        self._state_mtime_ns = 0
        for venue_id in self.VENUE_IDS:
            self._ensure_account(venue_id)
        self._load()

    def summary(self) -> dict[str, object]:
        with self._lock:
            self._refresh_external_locked()
            prices = self._latest_prices()
            account_items = [self._account_summary(venue_id, prices) for venue_id in sorted(self._accounts)]
            primary = next((item for item in account_items if item["venue_id"] == "binance"), account_items[0])
            aggregate_balances: dict[str, Decimal] = {}
            aggregate_positions: dict[str, dict[str, Decimal]] = {}
            aggregate_equity = Decimal("0")
            for item in account_items:
                aggregate_equity += Decimal(str(item["equity_quote"]))
                for asset, raw_amount in item["balances"].items():
                    aggregate_balances[asset] = aggregate_balances.get(asset, Decimal("0")) + Decimal(str(raw_amount))
                for position in item["positions"]:
                    asset = str(position["asset"])
                    current = aggregate_positions.setdefault(asset, {"quantity": Decimal("0"), "value_quote": Decimal("0")})
                    current["quantity"] += Decimal(str(position["quantity"]))
                    current["value_quote"] += Decimal(str(position["value_quote"]))
            return {
                # Keep the original top-level fields scoped to the primary
                # Binance paper account; consumers can opt into the explicit
                # multi-venue fields without double-counting balances.
                "account_id": primary["account_id"],
                "primary_venue": "binance",
                "quote_asset": "USDT",
                "balances": primary["balances"],
                "positions": primary["positions"],
                "equity_quote": primary["equity_quote"],
                "accounts": account_items,
                "aggregate_balances": {asset: str(amount) for asset, amount in sorted(aggregate_balances.items())},
                "aggregate_positions": [
                    {
                        "asset": asset,
                        "quantity": str(values["quantity"]),
                        "mark_price": None,
                        "value_quote": str(values["value_quote"]),
                    }
                    for asset, values in sorted(aggregate_positions.items())
                ],
                "aggregate_equity_quote": str(aggregate_equity),
                "venue_count": len(account_items),
                "fee_bps": str(self._fee_bps),
                "order_count": len(self._records),
                "strategy_run_count": len(self._strategy_runs),
                "execution_mode": "PAPER",
                "real_execution_mode": "DISABLED",
            }

    def orders(self, limit: int = 50) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            return [deepcopy(item) for item in self._records[-max(1, min(int(limit), 200)) :][::-1]]

    def strategy_runs(self, limit: int = 30) -> list[dict[str, object]]:
        with self._lock:
            self._refresh_external_locked()
            return [deepcopy(item) for item in self._strategy_runs[-max(1, min(int(limit), 100)) :][::-1]]

    def run_strategy(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
        config: CandleBacktestConfig,
        run_id: str | None = None,
        record_task: bool = True,
    ) -> dict[str, object]:
        venue = self._venue(venue_id)
        normalized_symbol = self._symbol(symbol)
        dataset = self._storage.find_dataset(
            venue_id=venue,
            instrument_key=f"{venue}:spot:{normalized_symbol}",
            interval=str(interval).strip().lower(),
        )
        if dataset is None:
            raise ValueError("verified history dataset was not found for paper strategy")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            raise ValueError("history dataset quality gate did not pass")
        page = self._storage.read_all(dataset)
        if not page.items:
            raise ValueError("paper strategy history is empty")
        result = CandleBacktestEngine().run(
            page.items,
            config=config,
            run_id=str(run_id or f"paper-replay-{uuid4().hex}"),
            dataset_id=dataset.manifest.dataset_id,
        ).as_dict()
        now = datetime.now(UTC).isoformat()
        record = {
            **result,
            "status": "completed",
            "mode": "PAPER_REPLAY",
            "venue_id": venue,
            "account_id": self._ensure_account(venue).account_id,
            "symbol": normalized_symbol,
            "dataset": self._storage.dataset_dict(dataset),
            "created_at": now,
            "updated_at": now,
            "parameters": {
                **(dict(result.get("parameters", {})) if isinstance(result.get("parameters"), dict) else {}),
                "strategy_parameters": dict(config.parameters),
            },
        }
        with self._lock:
            self._refresh_external_locked()
            self._strategy_runs.append(record)
            self._persist()
        if record_task:
            TaskLifecycle(
                self._task_store,
                f"paper-strategy:{record['run_id']}",
                kind="paper_strategy",
                title="模拟策略回放",
                payload={"run_id": record["run_id"], "dataset_id": dataset.manifest.dataset_id},
            ).completed(
                result={
                    "run_id": record["run_id"],
                    "dataset_id": dataset.manifest.dataset_id,
                    "orders": record.get("orders", 0),
                    "total_return_pct": record.get("total_return_pct"),
                }
            )
        return deepcopy(record)

    def reset(
        self,
        balances: dict[str, str | int | float | Decimal] | None = None,
        *,
        venue_id: str | None = None,
    ) -> dict[str, object]:
        next_balances = balances or self.DEFAULT_BALANCES
        with self._lock:
            self._refresh_external_locked()
            if venue_id is None:
                self._accounts.clear()
                self._brokers.clear()
                for reset_venue in self.VENUE_IDS:
                    self._ensure_account(reset_venue, next_balances)
                self._records.clear()
                self._strategy_runs.clear()
                self._request_ids.clear()
            else:
                venue = self._venue(venue_id)
                self._accounts.pop(venue, None)
                self._brokers.pop(venue, None)
                self._ensure_account(venue, next_balances)
                self._records = [item for item in self._records if str(item.get("venue_id")) != venue]
                self._strategy_runs = [item for item in self._strategy_runs if str(item.get("venue_id")) != venue]
                self._request_ids = {
                    str(item["request_id"]): item
                    for item in self._records
                    if str(item.get("request_id", "")).strip()
                }
            self._persist()
        return self.summary()

    def submit(
        self,
        *,
        venue_id: str,
        symbol: str,
        interval: str,
        side: str,
        quantity: Decimal,
        limit_price: Decimal | None,
        request_id: str | None,
    ) -> dict[str, object]:
        request_key = str(request_id or uuid4().hex).strip()
        venue = self._venue(venue_id)
        normalized_symbol = self._symbol(symbol)
        normalized_side = Side(str(side).strip().upper())
        order_type = OrderType.LIMIT if limit_price is not None else OrderType.LIMIT_IOC
        dataset = self._storage.find_dataset(
            venue_id=venue,
            instrument_key=f"{venue}:spot:{normalized_symbol}",
            interval=str(interval).strip().lower(),
        )
        if dataset is None:
            raise ValueError("verified history dataset was not found for paper order")
        if dataset.manifest.gap_count or dataset.manifest.duplicate_count:
            raise ValueError("history dataset quality gate did not pass")
        page = self._storage.read_page(dataset, limit=1, tail=True)
        if not page.items:
            raise ValueError("paper order market data is empty")
        candle = page.items[-1]
        instrument = Instrument(
            venue_id=venue,
            market_type=MarketType.SPOT,
            base_asset=normalized_symbol.split("/")[0],
            quote_asset=normalized_symbol.split("/")[1],
            native_symbol=self._native_symbol(venue, normalized_symbol),
            price_tick=Decimal("0.00000001"),
            quantity_step=Decimal("0.000001"),
            min_quantity=Decimal("0.000001"),
            min_notional=Decimal("5"),
        )
        reference = candle.close
        spread = max(reference * Decimal("0.0005"), instrument.price_tick)
        bid = reference - spread
        ask = reference + spread
        depth = max(candle.volume, quantity, instrument.min_quantity)
        now = datetime.now(UTC)
        market = OrderBookSnapshot(
            instrument=instrument,
            bids=(PriceLevel(bid, depth),),
            asks=(PriceLevel(ask, depth),),
            exchange_timestamp=candle.close_time,
            received_timestamp=now,
            sequence=max(1, int(candle.open_time.timestamp())),
        )
        effective_price = limit_price
        if effective_price is None:
            effective_price = ask * Decimal("1.001") if normalized_side is Side.BUY else bid * Decimal("0.999")
        with self._lock:
            self._refresh_external_locked()
            existing = self._request_ids.get(request_key)
            if existing is not None:
                return deepcopy(existing)
            account = self._ensure_account(venue)
            intent = OrderIntent(
                request_id=request_key,
                account_id=account.account_id,
                venue_id=venue,
                instrument=instrument,
                side=normalized_side,
                order_type=order_type,
                quantity=quantity,
                limit_price=effective_price if order_type is not OrderType.MARKET else None,
                strategy_id="paper-manual",
                strategy_version="1.0.0",
                authorization_id="paper-session",
                generated_at=now,
                reference_price=reference,
                max_slippage_bps=Decimal("50"),
            )
            order = self._brokers[venue].submit(intent, market, now)
            record = self._record(order, dataset.manifest.dataset_id, request_key, candle.close, account=account)
            self._records.append(record)
            self._request_ids[request_key] = record
            self._persist()
            return deepcopy(record)

    def _record(self, order, dataset_id: str, request_id: str, mark_price: Decimal, *, account: PaperAccount) -> dict[str, object]:
        filled_value = sum((fill.price * fill.quantity for fill in order.fills), Decimal("0"))
        filled_quantity = order.filled_quantity
        fees = sum((fill.fee_amount for fill in order.fills), Decimal("0"))
        return {
            "order_id": order.order_id,
            "request_id": request_id,
            "account_id": account.account_id,
            "dataset_id": dataset_id,
            "venue_id": order.intent.venue_id,
            "symbol": order.intent.instrument.canonical_symbol,
            "side": order.intent.side.value,
            "order_type": order.intent.order_type.value,
            "status": order.status.value,
            "quantity": str(order.intent.quantity),
            "filled_quantity": str(filled_quantity),
            "remaining_quantity": str(order.remaining_quantity),
            "average_price": None if filled_quantity == 0 else str(filled_value / filled_quantity),
            "fee_quote": str(fees),
            "mark_price": str(mark_price),
            "reason": order.reason,
            "created_at": datetime.now(UTC).isoformat(),
            "balances": {asset: str(amount) for asset, amount in sorted(account.snapshot().items())},
        }

    def _latest_prices(self) -> dict[tuple[str, str], Decimal]:
        prices: dict[tuple[str, str], Decimal] = {}
        for dataset in self._storage.list_datasets():
            if dataset.manifest.interval != "1h" or dataset.manifest.gap_count or dataset.manifest.duplicate_count:
                continue
            symbol = dataset.manifest.instrument_key.rsplit(":", 1)[-1]
            base = symbol.split("/")[0]
            key = (dataset.manifest.venue_id, base)
            if key in prices:
                continue
            page = self._storage.read_page(dataset, limit=1, tail=True)
            if page.items:
                prices[key] = page.items[-1].close
        return prices

    def _load(self) -> None:
        if self._state is None:
            return
        try:
            payload = self._state.load({"version": 1, "balances": self.DEFAULT_BALANCES, "records": [], "strategy_runs": [], "order_counter": 0})
        except JsonStateError as error:
            raise RuntimeError("paper trading state is unreadable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("paper trading state must be an object")
        balances = payload.get("balances", self.DEFAULT_BALANCES)
        records = payload.get("records", [])
        strategy_runs = payload.get("strategy_runs", [])
        accounts = payload.get("accounts")
        if not isinstance(balances, dict) or not isinstance(records, list) or not isinstance(strategy_runs, list):
            raise RuntimeError("paper trading state has invalid fields")
        self._accounts.clear()
        self._brokers.clear()
        if isinstance(accounts, dict):
            for venue_id, raw_account in accounts.items():
                venue = self._venue(venue_id)
                if not isinstance(raw_account, dict) or not isinstance(raw_account.get("balances"), dict):
                    continue
                try:
                    order_counter = int(raw_account.get("order_counter", 0))
                except (TypeError, ValueError) as error:
                    raise RuntimeError("paper trading state has an invalid order counter") from error
                self._ensure_account(
                    venue,
                    raw_account["balances"],
                    order_counter=max(order_counter, self._record_counter(records, venue)),
                )
        else:
            order_counter = payload.get("order_counter", 0)
            try:
                parsed_counter = max(int(order_counter), self._record_counter(records))
            except (TypeError, ValueError) as error:
                raise RuntimeError("paper trading state has an invalid order counter") from error
            self._ensure_account("binance", balances, order_counter=parsed_counter)
        if not self._accounts:
            self._ensure_account("binance")
        for venue_id in self.VENUE_IDS:
            self._ensure_account(venue_id)
        self._records = [deepcopy(item) for item in records if isinstance(item, dict) and str(item.get("order_id", "")).strip()]
        self._strategy_runs = [deepcopy(item) for item in strategy_runs if isinstance(item, dict) and str(item.get("run_id", "")).strip()]
        self._request_ids = {
            str(item["request_id"]): item
            for item in self._records
            if str(item.get("request_id", "")).strip()
        }
        try:
            state_path = getattr(self._state, "path", None)
            self._state_mtime_ns = state_path.stat().st_mtime_ns if state_path is not None else 0
        except OSError:
            self._state_mtime_ns = 0

    def _persist(self) -> None:
        if self._state is not None:
            try:
                accounts = {
                    venue: {
                        "account_id": account.account_id,
                        "balances": {asset: str(amount) for asset, amount in sorted(account.snapshot().items())},
                        "order_counter": self._brokers[venue].order_counter,
                    }
                    for venue, account in sorted(self._accounts.items())
                }
                self._state.save(
                    {
                        "version": 1,
                        "accounts": accounts,
                        "balances": accounts.get("binance", {}).get("balances", self.DEFAULT_BALANCES),
                        "order_counter": accounts.get("binance", {}).get("order_counter", 0),
                        "records": self._records,
                        "strategy_runs": self._strategy_runs[-100:],
                    }
                )
                try:
                    state_path = getattr(self._state, "path", None)
                    self._state_mtime_ns = state_path.stat().st_mtime_ns if state_path is not None else 0
                except OSError:
                    self._state_mtime_ns = 0
            except JsonStateError as error:
                raise RuntimeError("paper trading state cannot be saved") from error

    def _refresh_external_locked(self) -> None:
        if self._state is None:
            return
        if not hasattr(self._state, "path"):
            self._load()
            return
        try:
            current_mtime = self._state.path.stat().st_mtime_ns
        except OSError:
            return
        if current_mtime and current_mtime != self._state_mtime_ns:
            self._load()

    @staticmethod
    def _record_counter(records: list[object], venue_id: str | None = None) -> int:
        highest = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_order_id = str(record.get("order_id", ""))
            record_venue = str(record.get("venue_id", "")).strip().lower()
            if venue_id is not None and record_venue != venue_id:
                continue
            prefix = "paper-" if record_venue in {"", "binance"} else f"paper-{record_venue}-"
            if not raw_order_id.startswith(prefix):
                continue
            suffix = raw_order_id[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return highest

    def _ensure_account(
        self,
        venue_id: str,
        balances: dict[str, str | int | float | Decimal] | None = None,
        *,
        order_counter: int = 0,
    ) -> PaperAccount:
        venue = self._venue(venue_id)
        account = self._accounts.get(venue)
        if account is not None:
            return account
        account = PaperAccount(f"paper-{venue}", balances or self.DEFAULT_BALANCES)
        self._accounts[venue] = account
        order_id_prefix = "paper-" if venue == "binance" else f"paper-{venue}-"
        self._brokers[venue] = PaperBroker(
            account,
            fee_bps=self._fee_bps,
            order_counter=order_counter,
            order_id_prefix=order_id_prefix,
        )
        return account

    def _account_summary(self, venue_id: str, prices: dict[tuple[str, str], Decimal]) -> dict[str, object]:
        account = self._accounts[venue_id]
        balances = account.snapshot()
        positions = []
        equity = balances.get("USDT", Decimal("0"))
        for asset, amount in balances.items():
            if asset == "USDT" or amount <= 0:
                continue
            price = prices.get((venue_id, asset))
            value = amount * price if price is not None else Decimal("0")
            equity += value
            positions.append({
                "asset": asset,
                "quantity": str(amount),
                "mark_price": None if price is None else str(price),
                "value_quote": str(value),
            })
        return {
            "venue_id": venue_id,
            "account_id": account.account_id,
            "quote_asset": "USDT",
            "balances": {asset: str(amount) for asset, amount in sorted(balances.items())},
            "positions": positions,
            "equity_quote": str(equity),
            "order_count": sum(1 for item in self._records if str(item.get("venue_id")) == venue_id),
            "strategy_run_count": sum(1 for item in self._strategy_runs if str(item.get("venue_id")) == venue_id),
        }

    @classmethod
    def _venue(cls, value: str) -> str:
        venue = str(value).strip().lower()
        if venue not in cls.VENUE_IDS:
            raise ValueError("venue_id must be binance, okx, or bybit")
        return venue

    @staticmethod
    def _native_symbol(venue: str, symbol: str) -> str:
        base, quote = symbol.split("/")
        return f"{base}-{quote}" if venue == "okx" else f"{base}{quote}"

    @staticmethod
    def _symbol(value: str) -> str:
        normalized = str(value).strip().upper().replace("-", "/")
        if "/" not in normalized:
            for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "BNB"):
                if normalized.endswith(quote) and len(normalized) > len(quote):
                    normalized = f"{normalized[:-len(quote)]}/{quote}"
                    break
        if normalized.count("/") != 1 or any(not part for part in normalized.split("/")):
            raise ValueError("symbol must be like BTC/USDT")
        return normalized
