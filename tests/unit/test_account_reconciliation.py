"""Tests for account ledger store and reconciliation scheduler."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.app.services.account_ledger_store import AccountLedgerStore
from backend.app.services.account_reconciliation_scheduler import AccountReconciliationScheduler


@pytest.fixture()
def store(tmp_path):
    db = AccountLedgerStore(f"sqlite:///{tmp_path}/test-ledger.db")
    yield db
    db.close()


class FakeBalance:
    def __init__(self, asset, available, total):
        self.asset = asset
        self.available = Decimal(str(available))
        self.total = Decimal(str(total))


class FakeSnapshot:
    def __init__(self, balances, state="FRESH", order_ids=()):
        self.balances = tuple(FakeBalance(a, av, t) for a, av, t in balances)
        self.state = state
        self.open_order_ids = tuple(order_ids)
        self.fetched_at = datetime.now(UTC)


class FakeGateway:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)

    def fetch_account(self, account_id):
        return self._snapshots.pop(0)


class TestAccountLedgerStore:
    def test_pause_defaults_to_not_paused(self, store):
        assert store.get_pause()["paused"] is False

    def test_set_and_clear_pause(self, store):
        paused = store.set_paused("test reason")
        assert paused["paused"] is True
        assert paused["reason"] == "test reason"
        cleared = store.clear_pause("tester")
        assert cleared["paused"] is False
        assert cleared["cleared_by"] == "tester"

    def test_snapshot_roundtrip(self, store):
        store.save_snapshot(
            "default", "binance", datetime.now(UTC),
            [{"asset": "BTC", "available": "0.5", "total": "0.5"}],
            ["123"], "FRESH",
        )
        latest = store.latest_snapshot("default", "binance")
        assert latest is not None
        assert latest["balances"][0]["asset"] == "BTC"
        assert store.list_snapshots("default", "binance")[0]["balance_count"] == 1

    def test_ledger_entries(self, store):
        store.append_entry("default", "binance", "BTC", "0.1", "snapshot_delta", datetime.now(UTC))
        entries = store.list_entries("default", "binance")
        assert len(entries) == 1
        assert entries[0]["asset"] == "BTC"
        assert entries[0]["delta"] == "0.1"

    def test_reconciliation_runs(self, store):
        store.save_reconciliation_run(
            "default", "binance", datetime.now(UTC), True, [], "RECONCILED"
        )
        runs = store.list_reconciliation_runs("default", "binance")
        assert len(runs) == 1
        assert runs[0]["balanced"] is True


class TestReconciliationScheduler:
    def test_skipped_without_gateway(self, store):
        sched = AccountReconciliationScheduler(ledger_store=store, gateway=None)
        assert sched.run_once()["status"] == "skipped"

    def test_happy_path_records_snapshot(self, store):
        gateway = FakeGateway([FakeSnapshot([("BTC", "0.5", "0.5")])])
        sched = AccountReconciliationScheduler(ledger_store=store, gateway=gateway)
        result = sched.run_once()
        assert result["status"] == "completed"
        assert result["balanced"] is True
        assert store.latest_snapshot("default", "binance") is not None
        assert store.get_pause()["paused"] is False

    def test_delta_creates_ledger_entry(self, store):
        gateway = FakeGateway([
            FakeSnapshot([("BTC", "0.5", "0.5")]),
            FakeSnapshot([("BTC", "0.6", "0.6")]),
        ])
        sched = AccountReconciliationScheduler(ledger_store=store, gateway=gateway)
        sched.run_once()
        result = sched.run_once()
        assert result["new_ledger_entries"] == 1
        assert store.list_entries("default", "binance")[0]["delta"] == "0.1"

    def test_large_drop_triggers_pause(self, store):
        gateway = FakeGateway([
            FakeSnapshot([("BTC", "1.0", "1.0")]),
            FakeSnapshot([("BTC", "0.4", "0.4")]),  # 60% drop
        ])
        sched = AccountReconciliationScheduler(ledger_store=store, gateway=gateway)
        sched.run_once()
        result = sched.run_once()
        assert result["status"] == "anomaly"
        assert store.get_pause()["paused"] is True

    def test_fetch_error_triggers_pause(self, store):
        class BadGateway:
            def fetch_account(self, account_id):
                raise RuntimeError("connection failed")
        sched = AccountReconciliationScheduler(ledger_store=store, gateway=BadGateway())
        result = sched.run_once()
        assert result["status"] == "failed"
        assert store.get_pause()["paused"] is True

    def test_rejects_bad_interval(self, store):
        with pytest.raises(ValueError):
            AccountReconciliationScheduler(ledger_store=store, gateway=None, interval_seconds=0)
