"""Atomic JSON files for the development runtime's shared market state.

This is a small cross-process bridge used before PostgreSQL/Redis are added.
It stores normalized objects only; raw exchange payloads and credentials never
enter the files.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

from domain.market import Instrument, MarketType, OrderBookSnapshot, PriceLevel
from domain.market_status import MarketConnectionState, MarketStatus
from ports.market_state import MarketStateStoreError


_VENUE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class FileMarketStateStore:
    """Persist one current state file per venue and instrument."""

    def __init__(self, root: str | os.PathLike[str], *, create: bool = True) -> None:
        self._root = Path(root)
        if create:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise MarketStateStoreError("market state directory is unavailable") from error

    def write_snapshot(self, snapshot: OrderBookSnapshot) -> None:
        payload = {
            "schema_version": 1,
            "instrument": _instrument_payload(snapshot.instrument),
            "bids": [_level_payload(level) for level in snapshot.bids],
            "asks": [_level_payload(level) for level in snapshot.asks],
            "exchange_timestamp": snapshot.exchange_timestamp.isoformat(),
            "received_timestamp": snapshot.received_timestamp.isoformat(),
            "sequence": snapshot.sequence,
        }
        self._atomic_write(self._snapshot_path(snapshot.instrument), payload)

    def write_status(self, status: MarketStatus) -> None:
        path = self._status_path(status.venue_id)
        current = self._read_status_for_merge(status.venue_id, path)
        if current is not None and not _status_update_is_current(status, current):
            return
        self._atomic_write(path, _status_payload(status))

    def read_status(self, venue_id: str) -> MarketStatus | None:
        path = self._status_path(venue_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return _status_from_payload(payload)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MarketStateStoreError("market status file is invalid") from error

    def read_snapshot(self, instrument_key: str) -> OrderBookSnapshot | None:
        normalized_key = str(instrument_key).strip()
        if not normalized_key or any(character.isspace() for character in normalized_key):
            raise MarketStateStoreError("market snapshot instrument key is invalid")
        path = self._root / f"snapshot-{quote(normalized_key, safe='')}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = _snapshot_from_payload(payload)
            if snapshot.instrument.key != normalized_key:
                raise ValueError("market snapshot instrument key does not match its path")
            return snapshot
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MarketStateStoreError("market snapshot file is invalid") from error

    def _status_path(self, venue_id: str) -> Path:
        normalized = str(venue_id).strip().lower()
        if not _VENUE_PATTERN.fullmatch(normalized):
            raise MarketStateStoreError("market status venue is invalid")
        return self._root / f"status-{normalized}.json"

    def _snapshot_path(self, instrument: Instrument) -> Path:
        filename = quote(instrument.key, safe="") + ".json"
        return self._root / f"snapshot-{filename}"

    def _read_status_for_merge(self, venue_id: str, path: Path) -> MarketStatus | None:
        if not path.exists():
            return None
        try:
            return self.read_status(venue_id)
        except MarketStateStoreError:
            # A new valid status can repair a corrupted runtime cache.
            return None

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._root,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except OSError as error:
            if "temporary_path" in locals():
                temporary_path.unlink(missing_ok=True)
            raise MarketStateStoreError("market state file is unavailable") from error


def _instrument_payload(instrument: Instrument) -> dict[str, str]:
    return {
        "venue_id": instrument.venue_id,
        "market_type": instrument.market_type.value,
        "base_asset": instrument.base_asset,
        "quote_asset": instrument.quote_asset,
        "native_symbol": instrument.native_symbol,
        "price_tick": str(instrument.price_tick),
        "quantity_step": str(instrument.quantity_step),
        "min_quantity": str(instrument.min_quantity),
        "min_notional": str(instrument.min_notional),
    }


def _instrument_from_payload(payload: Any) -> Instrument:
    if not isinstance(payload, dict):
        raise ValueError("instrument payload must be an object")
    return Instrument(
        venue_id=str(payload["venue_id"]),
        market_type=MarketType(str(payload["market_type"])),
        base_asset=str(payload["base_asset"]),
        quote_asset=str(payload["quote_asset"]),
        native_symbol=str(payload["native_symbol"]),
        price_tick=Decimal(str(payload["price_tick"])),
        quantity_step=Decimal(str(payload["quantity_step"])),
        min_quantity=Decimal(str(payload["min_quantity"])),
        min_notional=Decimal(str(payload["min_notional"])),
    )


def _level_payload(level: PriceLevel) -> dict[str, str]:
    return {"price": str(level.price), "quantity": str(level.quantity)}


def _level_from_payload(payload: Any) -> PriceLevel:
    if not isinstance(payload, dict):
        raise ValueError("price level payload must be an object")
    return PriceLevel(Decimal(str(payload["price"])), Decimal(str(payload["quantity"])))


def _snapshot_from_payload(payload: Any) -> OrderBookSnapshot:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported market snapshot schema")
    return OrderBookSnapshot(
        instrument=_instrument_from_payload(payload["instrument"]),
        bids=tuple(_level_from_payload(item) for item in payload["bids"]),
        asks=tuple(_level_from_payload(item) for item in payload["asks"]),
        exchange_timestamp=datetime.fromisoformat(str(payload["exchange_timestamp"])),
        received_timestamp=datetime.fromisoformat(str(payload["received_timestamp"])),
        sequence=int(payload["sequence"]),
    )


def _status_payload(status: MarketStatus) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "venue_id": status.venue_id,
        "state": status.state.value,
        "last_received_at": None if status.last_received_at is None else status.last_received_at.isoformat(),
        "last_sequence": status.last_sequence,
        "reason": status.reason,
    }


def _status_from_payload(payload: Any) -> MarketStatus:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported market status schema")
    received = payload.get("last_received_at")
    last_received_at = None if received is None else datetime.fromisoformat(str(received))
    sequence = payload.get("last_sequence")
    return MarketStatus(
        venue_id=str(payload["venue_id"]),
        state=MarketConnectionState(str(payload["state"])),
        last_received_at=last_received_at,
        last_sequence=None if sequence is None else int(sequence),
        reason=None if payload.get("reason") is None else str(payload["reason"]),
    )


def _status_update_is_current(incoming: MarketStatus, current: MarketStatus) -> bool:
    """Keep the status associated with the newest instrument snapshot."""
    if incoming.last_received_at is None:
        return current.last_received_at is None
    if current.last_received_at is None:
        return True
    return incoming.last_received_at >= current.last_received_at
