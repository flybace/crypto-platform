"""Read-only account snapshots and append-only ledger projection."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .market import _aware, _decimal
from .trading import Balance


class AccountState(StrEnum):
    FRESH = "FRESH"
    RECONCILED = "RECONCILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: str
    venue_id: str
    balances: tuple[Balance, ...]
    fetched_at: datetime
    open_order_ids: tuple[str, ...] = ()
    state: AccountState = AccountState.FRESH

    def __post_init__(self) -> None:
        account_id = str(self.account_id).strip()
        venue_id = str(self.venue_id).strip().lower()
        if not account_id or not venue_id:
            raise ValueError("account_id and venue_id must not be empty")
        _aware(self.fetched_at, "fetched_at")
        state = self.state if isinstance(self.state, AccountState) else AccountState(self.state)
        balances = tuple(self.balances)
        assets = [balance.asset for balance in balances]
        if len(assets) != len(set(assets)):
            raise ValueError("account snapshot cannot contain duplicate assets")
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "venue_id", venue_id)
        object.__setattr__(self, "balances", balances)
        object.__setattr__(self, "open_order_ids", tuple(self.open_order_ids))
        object.__setattr__(self, "state", state)

    def available(self, asset: str) -> Decimal:
        key = str(asset).strip().upper()
        for balance in self.balances:
            if balance.asset == key:
                return balance.available
        return Decimal("0")


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: str
    account_id: str
    venue_id: str
    asset: str
    delta: Decimal
    event_type: str
    occurred_at: datetime
    external_ref: str | None = None

    def __post_init__(self) -> None:
        fields = {
            "entry_id": str(self.entry_id).strip(),
            "account_id": str(self.account_id).strip(),
            "venue_id": str(self.venue_id).strip().lower(),
            "asset": str(self.asset).strip().upper(),
            "event_type": str(self.event_type).strip(),
        }
        if any(not value for value in fields.values()):
            raise ValueError("ledger identity fields must not be empty")
        _aware(self.occurred_at, "occurred_at")
        object.__setattr__(self, "delta", _decimal(self.delta, "delta"))
        for field, value in fields.items():
            object.__setattr__(self, field, value)


class LedgerProjection:
    """A rebuildable balance projection; entries remain the source of truth."""

    def __init__(self, balances: Mapping[str, Decimal | int | str] | None = None) -> None:
        self._balances = {
            str(asset).strip().upper(): _decimal(amount, f"balance[{asset}]")
            for asset, amount in (balances or {}).items()
        }

    def apply(self, entry: LedgerEntry) -> None:
        key = entry.asset
        next_value = self._balances.get(key, Decimal("0")) + entry.delta
        if next_value < 0:
            raise ValueError(f"ledger projection would become negative: {key}")
        self._balances[key] = next_value

    def available(self, asset: str) -> Decimal:
        return self._balances.get(str(asset).strip().upper(), Decimal("0"))

    def snapshot(self) -> dict[str, Decimal]:
        return dict(self._balances)


@dataclass(frozen=True, slots=True)
class BalanceDifference:
    asset: str
    local: Decimal
    external: Decimal

    @property
    def delta(self) -> Decimal:
        return self.external - self.local


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    account_id: str
    venue_id: str
    balanced: bool
    differences: tuple[BalanceDifference, ...]
    checked_at: datetime
    state: AccountState

    def __post_init__(self) -> None:
        _aware(self.checked_at, "checked_at")
        object.__setattr__(self, "differences", tuple(self.differences))
