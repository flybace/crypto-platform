from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from adapters.standalone.file_market_archive import FileMarketArchive
from domain.market import PriceLevel
from ports.market_archive import MarketArchiveError
from tests.helpers import NOW, make_snapshot


def test_file_market_archive_appends_and_replays_normalized_states(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path)
    first = make_snapshot(sequence=1, bid_price="100.00")
    second = make_snapshot(sequence=2, bid_price="101.00", ask_price="101.10", age_seconds=0)

    archive.append_snapshot(first)
    archive.append_snapshot(second)

    assert archive.read_snapshots(first.instrument.key) == (first, second)


def test_file_market_archive_filters_by_receive_time_and_limit(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path)
    first = make_snapshot(sequence=1, age_seconds=2)
    second = make_snapshot(sequence=2, bid_price="100.01", age_seconds=1)
    third = make_snapshot(sequence=3, bid_price="100.02", age_seconds=0)
    for snapshot in (first, second, third):
        archive.append_snapshot(snapshot)

    assert archive.read_snapshots(
        first.instrument.key,
        start=NOW - timedelta(seconds=1),
        end=NOW,
        limit=1,
    ) == (second,)


def test_file_market_archive_rejects_corrupt_or_invalid_reads(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path)
    snapshot = make_snapshot()
    archive.append_snapshot(snapshot)
    next(tmp_path.glob("book-*.jsonl")).write_text("{bad\n", encoding="utf-8")

    with pytest.raises(MarketArchiveError, match="invalid"):
        archive.read_snapshots(snapshot.instrument.key)
    with pytest.raises(MarketArchiveError, match="timezone-aware"):
        archive.read_snapshots(snapshot.instrument.key, start=NOW.replace(tzinfo=None))


def test_file_market_archive_rotates_full_segments_and_replays_all_parts(tmp_path: Path) -> None:
    first = make_snapshot(sequence=1, age_seconds=2)
    second = make_snapshot(sequence=2, bid_price="100.01", age_seconds=1)
    probe_root = tmp_path / "probe"
    FileMarketArchive(probe_root).append_snapshot(first)
    record_bytes = next(probe_root.glob("book-*.jsonl")).stat().st_size
    archive_root = tmp_path / "segments"
    archive = FileMarketArchive(archive_root, max_segment_bytes=record_bytes)

    archive.append_snapshot(first)
    archive.append_snapshot(second)

    assert len(tuple(archive_root.glob("book-*.jsonl"))) == 2
    assert archive.read_snapshots(first.instrument.key) == (first, second)


def test_file_market_archive_reads_latest_bounded_window_in_chronological_order(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path)
    snapshots = tuple(
        make_snapshot(sequence=sequence, bid_price=f"100.{sequence:02d}", age_seconds=3 - sequence)
        for sequence in range(1, 4)
    )
    for snapshot in snapshots:
        archive.append_snapshot(snapshot)

    assert archive.read_snapshots(snapshots[0].instrument.key, limit=2, tail=True) == snapshots[1:]


def test_file_market_archive_reverse_reads_record_larger_than_chunk(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path)
    first = make_snapshot(sequence=1, age_seconds=2)
    large = replace(
        first,
        bids=tuple(
            PriceLevel(Decimal("100") - Decimal(index) / Decimal("100"), Decimal("1.123456789012345678"))
            for index in range(5000)
        ),
    )
    second = make_snapshot(sequence=2, bid_price="100.01", age_seconds=1)
    archive.append_snapshot(large)
    archive.append_snapshot(second)

    path = next(tmp_path.glob("book-*.jsonl"))
    assert path.stat().st_size > 64 * 1024
    result = archive.read_snapshots(large.instrument.key, limit=2, tail=True)

    assert tuple(item.sequence for item in result) == (1, 2)
    assert result[0].bids == large.bids


def test_file_market_archive_prunes_only_complete_old_segments(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path, segment_seconds=1)
    old = make_snapshot(sequence=1, age_seconds=10)
    fresh = make_snapshot(sequence=2, bid_price="100.01", age_seconds=1)
    archive.append_snapshot(old)
    archive.append_snapshot(fresh)

    removed = archive.prune(before=NOW - timedelta(seconds=5))

    assert removed == 1
    assert archive.read_snapshots(old.instrument.key) == (fresh,)


def test_file_market_archive_rejects_invalid_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="segment_seconds"):
        FileMarketArchive(tmp_path, segment_seconds=0)
    with pytest.raises(ValueError, match="max_segment_bytes"):
        FileMarketArchive(tmp_path, max_segment_bytes=0)
    with pytest.raises(ValueError, match="retention_seconds"):
        FileMarketArchive(tmp_path, retention_seconds=0)


def test_file_market_archive_recovers_only_an_incomplete_tail(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path)
    first = make_snapshot(sequence=1, age_seconds=2)
    second = make_snapshot(sequence=2, bid_price="100.01", age_seconds=1)
    archive.append_snapshot(first)
    path = next(tmp_path.glob("book-*.jsonl"))
    with path.open("ab") as handle:
        handle.write(b'{"schema":"market-book-state-v1"')

    removed = archive.recover()
    archive.append_snapshot(second)

    assert removed == len(b'{"schema":"market-book-state-v1"')
    assert archive.read_snapshots(first.instrument.key) == (first, second)


def test_file_market_archive_range_query_skips_unrelated_segments(tmp_path: Path) -> None:
    archive = FileMarketArchive(tmp_path, segment_seconds=1)
    old = make_snapshot(sequence=1, age_seconds=10)
    fresh = make_snapshot(sequence=2, bid_price="100.01", age_seconds=1)
    archive.append_snapshot(old)
    archive.append_snapshot(fresh)

    result = archive.read_snapshots(
        old.instrument.key,
        start=fresh.received_timestamp,
        end=NOW,
    )

    assert result == (fresh,)

def test_file_market_archive_status_reports_bounded_governance_metadata(tmp_path: Path) -> None:
    archive = FileMarketArchive(
        tmp_path,
        segment_seconds=60,
        max_segment_bytes=1024 * 1024,
        retention_seconds=3600,
        cleanup_interval_seconds=15,
    )
    snapshot = make_snapshot(sequence=1)
    archive.append_snapshot(snapshot)

    status = archive.status()

    assert status["schema"] == "market-archive-status-v1"
    assert status["root_exists"] is True
    assert status["segment_count"] == 1
    assert status["total_bytes"] > 0
    assert status["segment_seconds"] == 60
    assert status["max_segment_bytes"] == 1024 * 1024
    assert status["retention_seconds"] == 3600
    assert status["cleanup_interval_seconds"] == 15
    assert status["oldest_segment_at"] is not None
    assert status["latest_segment_at"] == status["oldest_segment_at"]
