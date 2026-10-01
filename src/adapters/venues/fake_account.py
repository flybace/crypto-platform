"""Deterministic read-only account gateway for reconciliation tests."""

from domain.account import AccountSnapshot


class FakeReadOnlyAccountGateway:
    def __init__(self, snapshots: tuple[AccountSnapshot, ...] = ()) -> None:
        self._snapshots = {snapshot.account_id: snapshot for snapshot in snapshots}

    def fetch_account(self, account_id: str) -> AccountSnapshot:
        try:
            return self._snapshots[str(account_id).strip()]
        except KeyError as error:
            raise KeyError(f"unknown fake account: {account_id}") from error
