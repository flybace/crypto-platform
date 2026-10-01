"""Read-only account boundary; no order mutation methods are exposed."""

from typing import Protocol

from domain.account import AccountSnapshot


class ReadOnlyAccountGateway(Protocol):
    def fetch_account(self, account_id: str) -> AccountSnapshot:
        """Read balances and open-order identifiers only."""
