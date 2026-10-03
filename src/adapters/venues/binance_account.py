"""Binance read-only account gateway.

Implements the ReadOnlyAccountGateway port against Binance private
read-only endpoints. No order mutation capability exists in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from domain.account import AccountSnapshot
from domain.trading import Balance

from .binance_signed import BinanceSignedRestClient


class BinanceReadOnlyAccountGateway:
    """Fetch-only Binance account adapter."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = "https://api.binance.com",
        timeout_seconds: float = 10.0,
        trust_env: bool = True,
    ) -> None:
        self._client = BinanceSignedRestClient(
            base_url,
            api_key,
            api_secret,
            timeout_seconds=timeout_seconds,
            trust_env=trust_env,
        )

    def fetch_account(self, account_id: str) -> AccountSnapshot:
        cleaned = str(account_id or "").strip()
        if not cleaned:
            raise ValueError("account_id must not be empty")
        payload = self._client.get_account()
        balances: list[Balance] = []
        for entry in payload.get("balances", []):
            if not isinstance(entry, dict):
                continue
            asset = str(entry.get("asset", "")).strip().upper()
            if not asset:
                continue
            try:
                free = Decimal(str(entry.get("free", "0")))
                locked = Decimal(str(entry.get("locked", "0")))
            except Exception:
                continue
            total = free + locked
            if total <= 0:
                continue
            balances.append(Balance(asset=asset, available=free, total=total))
        order_ids: list[str] = []
        for order in self._client.get_open_orders():
            order_id = str(order.get("orderId", "")).strip()
            if order_id:
                order_ids.append(order_id)
        return AccountSnapshot(
            account_id=cleaned,
            venue_id="binance",
            balances=tuple(balances),
            fetched_at=datetime.now(UTC),
            open_order_ids=tuple(order_ids),
        )

    def close(self) -> None:
        self._client.close()
