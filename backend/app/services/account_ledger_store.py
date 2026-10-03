"""Persistent account ledger: snapshots, entries, reconciliation runs, safety pause.

All account state changes are durable in the relational store. The safety
pause is a singleton row: when set, risk expansion must be blocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    Index,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    desc,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccountLedgerStore:
    """Relational store for account snapshots, ledger entries, reconciliation, pause."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        self._engine: Engine = create_engine(
            database_url, poolclass=QueuePool, pool_size=5, max_overflow=10
        )
        self._metadata = MetaData()

        self._snapshots = Table(
            "account_snapshots",
            self._metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("account_id", String(128), nullable=False),
            Column("venue_id", String(64), nullable=False),
            Column("fetched_at", DateTime(timezone=True), nullable=False),
            Column("balances_json", Text, nullable=False, default="[]"),
            Column("open_order_ids_json", Text, nullable=False, default="[]"),
            Column("state", String(32), nullable=False, default="FRESH"),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Index("ix_account_snapshots_account", "account_id", "venue_id"),
        )

        self._entries = Table(
            "account_ledger_entries",
            self._metadata,
            Column("entry_id", String(64), primary_key=True),
            Column("account_id", String(128), nullable=False),
            Column("venue_id", String(64), nullable=False),
            Column("asset", String(32), nullable=False),
            Column("delta", String(64), nullable=False),
            Column("event_type", String(64), nullable=False),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
            Column("external_ref", String(128), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Index("ix_ledger_entries_account", "account_id", "venue_id", "occurred_at"),
        )

        self._recon_runs = Table(
            "account_reconciliation_runs",
            self._metadata,
            Column("run_id", String(64), primary_key=True),
            Column("account_id", String(128), nullable=False),
            Column("venue_id", String(64), nullable=False),
            Column("checked_at", DateTime(timezone=True), nullable=False),
            Column("balanced", Boolean, nullable=False),
            Column("differences_json", Text, nullable=False, default="[]"),
            Column("state", String(32), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Index("ix_recon_runs_account", "account_id", "venue_id", "checked_at"),
        )

        self._pause = Table(
            "account_safety_pause",
            self._metadata,
            Column("id", Integer, primary_key=True),
            Column("paused", Boolean, nullable=False, default=False),
            Column("reason", Text, nullable=False, default=""),
            Column("triggered_at", DateTime(timezone=True), nullable=True),
            Column("cleared_at", DateTime(timezone=True), nullable=True),
            Column("cleared_by", String(128), nullable=True),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )

        self._metadata.create_all(self._engine)
        # Ensure singleton pause row exists
        with self._engine.begin() as conn:
            existing = conn.execute(select(self._pause.c.id)).first()
            if existing is None:
                conn.execute(
                    insert(self._pause).values(
                        id=1, paused=False, reason="", updated_at=_utcnow()
                    )
                )

    # -- snapshots ---------------------------------------------------------

    def save_snapshot(
        self,
        account_id: str,
        venue_id: str,
        fetched_at: datetime,
        balances: list[dict[str, str]],
        open_order_ids: list[str],
        state: str,
    ) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(self._snapshots).values(
                    account_id=account_id,
                    venue_id=venue_id,
                    fetched_at=fetched_at,
                    balances_json=json.dumps(balances),
                    open_order_ids_json=json.dumps(open_order_ids),
                    state=state,
                    created_at=_utcnow(),
                )
            )
            return int(result.inserted_primary_key[0])

    def latest_snapshot(self, account_id: str, venue_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(self._snapshots)
                .where(
                    self._snapshots.c.account_id == account_id,
                    self._snapshots.c.venue_id == venue_id,
                )
                .order_by(desc(self._snapshots.c.id))
                .limit(1)
            ).mappings().first()
            if row is None:
                return None
            return {
                "balances": json.loads(row["balances_json"]),
                "open_order_ids": json.loads(row["open_order_ids_json"]),
                "fetched_at": row["fetched_at"].isoformat(),
                "state": row["state"],
            }

    def list_snapshots(
        self, account_id: str, venue_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self._snapshots)
                .where(
                    self._snapshots.c.account_id == account_id,
                    self._snapshots.c.venue_id == venue_id,
                )
                .order_by(desc(self._snapshots.c.id))
                .limit(max(1, min(limit, 100)))
            ).mappings().all()
            return [
                {
                    "id": r["id"],
                    "fetched_at": r["fetched_at"].isoformat(),
                    "balance_count": len(json.loads(r["balances_json"])),
                    "open_order_count": len(json.loads(r["open_order_ids_json"])),
                    "state": r["state"],
                }
                for r in rows
            ]

    # -- ledger entries -----------------------------------------------------

    def append_entry(
        self,
        account_id: str,
        venue_id: str,
        asset: str,
        delta: str,
        event_type: str,
        occurred_at: datetime,
        external_ref: str | None = None,
    ) -> str:
        entry_id = uuid4().hex
        with self._engine.begin() as conn:
            conn.execute(
                insert(self._entries).values(
                    entry_id=entry_id,
                    account_id=account_id,
                    venue_id=venue_id,
                    asset=asset,
                    delta=str(delta),
                    event_type=event_type,
                    occurred_at=occurred_at,
                    external_ref=external_ref,
                    created_at=_utcnow(),
                )
            )
        return entry_id

    def list_entries(
        self, account_id: str, venue_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self._entries)
                .where(
                    self._entries.c.account_id == account_id,
                    self._entries.c.venue_id == venue_id,
                )
                .order_by(desc(self._entries.c.occurred_at))
                .limit(max(1, min(limit, 200)))
            ).mappings().all()
            return [
                {
                    "entry_id": r["entry_id"],
                    "asset": r["asset"],
                    "delta": r["delta"],
                    "event_type": r["event_type"],
                    "occurred_at": r["occurred_at"].isoformat(),
                    "external_ref": r["external_ref"],
                }
                for r in rows
            ]

    # -- reconciliation runs -------------------------------------------------

    def save_reconciliation_run(
        self,
        account_id: str,
        venue_id: str,
        checked_at: datetime,
        balanced: bool,
        differences: list[dict[str, str]],
        state: str,
    ) -> str:
        run_id = uuid4().hex
        with self._engine.begin() as conn:
            conn.execute(
                insert(self._recon_runs).values(
                    run_id=run_id,
                    account_id=account_id,
                    venue_id=venue_id,
                    checked_at=checked_at,
                    balanced=bool(balanced),
                    differences_json=json.dumps(differences),
                    state=state,
                    created_at=_utcnow(),
                )
            )
        return run_id

    def list_reconciliation_runs(
        self, account_id: str, venue_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(self._recon_runs)
                .where(
                    self._recon_runs.c.account_id == account_id,
                    self._recon_runs.c.venue_id == venue_id,
                )
                .order_by(desc(self._recon_runs.c.checked_at))
                .limit(max(1, min(limit, 100)))
            ).mappings().all()
            return [
                {
                    "run_id": r["run_id"],
                    "checked_at": r["checked_at"].isoformat(),
                    "balanced": bool(r["balanced"]),
                    "differences": json.loads(r["differences_json"]),
                    "state": r["state"],
                }
                for r in rows
            ]

    # -- safety pause --------------------------------------------------------

    def get_pause(self) -> dict[str, Any]:
        with self._engine.connect() as conn:
            row = conn.execute(select(self._pause).where(self._pause.c.id == 1)).mappings().first()
            assert row is not None
            return {
                "paused": bool(row["paused"]),
                "reason": row["reason"],
                "triggered_at": row["triggered_at"].isoformat() if row["triggered_at"] else None,
                "cleared_at": row["cleared_at"].isoformat() if row["cleared_at"] else None,
                "cleared_by": row["cleared_by"],
            }

    def set_paused(self, reason: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            conn.execute(
                update(self._pause)
                .where(self._pause.c.id == 1)
                .values(
                    paused=True,
                    reason=str(reason),
                    triggered_at=_utcnow(),
                    cleared_at=None,
                    cleared_by=None,
                    updated_at=_utcnow(),
                )
            )
        return self.get_pause()

    def clear_pause(self, cleared_by: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            conn.execute(
                update(self._pause)
                .where(self._pause.c.id == 1)
                .values(
                    paused=False,
                    cleared_at=_utcnow(),
                    cleared_by=str(cleared_by),
                    updated_at=_utcnow(),
                )
            )
        return self.get_pause()

    def close(self) -> None:
        self._engine.dispose()
