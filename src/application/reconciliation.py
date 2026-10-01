"""Compare local projections with an external read-only account snapshot."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from domain.account import AccountState, AccountSnapshot, BalanceDifference, ReconciliationResult


class ReconciliationService:
    def reconcile(
        self,
        account_id: str,
        venue_id: str,
        local_balances: Mapping[str, Decimal | int | str],
        external: AccountSnapshot,
        checked_at: datetime,
    ) -> ReconciliationResult:
        if external.account_id != account_id or external.venue_id != str(venue_id).strip().lower():
            raise ValueError("external account identity does not match reconciliation target")
        local = {str(asset).strip().upper(): Decimal(str(amount)) for asset, amount in local_balances.items()}
        external_balances = {balance.asset: balance.available for balance in external.balances}
        assets = sorted(set(local) | set(external_balances))
        differences = tuple(
            BalanceDifference(asset, local.get(asset, Decimal("0")), external_balances.get(asset, Decimal("0")))
            for asset in assets
            if local.get(asset, Decimal("0")) != external_balances.get(asset, Decimal("0"))
        )
        balanced = not differences and external.state is not AccountState.ERROR
        return ReconciliationResult(
            account_id=account_id,
            venue_id=str(venue_id).strip().lower(),
            balanced=balanced,
            differences=differences,
            checked_at=checked_at,
            state=AccountState.RECONCILED if balanced else AccountState.RECONCILIATION_REQUIRED,
        )
