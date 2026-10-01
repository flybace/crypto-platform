import json
from datetime import UTC, datetime, timedelta

import pytest

from application.history_response_archive import HistoryResponseArchive, HistoryResponseArchiveError
from domain.candle import CandleInterval, HistoryQuery
from domain.market import MarketType
from ports.rest import PublicJsonResponse


def _query() -> HistoryQuery:
    return HistoryQuery(
        venue_id="binance",
        market_type=MarketType.SPOT,
        instrument_key="binance:spot:BTC/USDT",
        native_symbol="BTCUSDT",
        interval=CandleInterval.DAY,
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _response() -> PublicJsonResponse:
    return PublicJsonResponse(
        path="/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1d", "limit": "1000"},
        payload=[[1767225600000, "100", "101", "99", "100.5", "2", 1767311999999, "201", 20]],
        headers={"content-type": "application/json", "authorization": "must-not-be-archived"},
        received_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def test_raw_history_archive_is_contract_bound_and_idempotent(tmp_path) -> None:
    archive = HistoryResponseArchive(tmp_path / "history")
    first = archive.archive(_query(), (_response(),), dataset_id="dataset-1")
    second = archive.archive(_query(), (_response(),), dataset_id="dataset-1")

    assert first["status"] == "COMPLETED"
    assert first["written_count"] == 1
    assert second["existing_count"] == 1
    path = tmp_path / "history" / str(first["items"][0]["archive_key"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["contract_version"] == "history-raw-response-v1"
    assert payload["dataset_id"] == "dataset-1"
    assert payload["payload_sha256"] == first["items"][0]["payload_sha256"]
    assert "authorization" not in json.dumps(payload).lower()
    assert archive.status()["response_count"] == 1


def test_raw_history_archive_rejects_identity_conflicts_and_path_escape(tmp_path) -> None:
    archive = HistoryResponseArchive(tmp_path / "history")
    first = archive.archive(_query(), (_response(),), dataset_id="dataset-1")
    path = tmp_path / "history" / str(first["items"][0]["archive_key"])
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(HistoryResponseArchiveError, match="identity conflict"):
        archive.archive(_query(), (_response(),), dataset_id="dataset-1")
    with pytest.raises(HistoryResponseArchiveError):
        archive.archive_path("../outside.json")


def test_raw_history_archive_status_degrades_for_invalid_contract(tmp_path) -> None:
    archive = HistoryResponseArchive(tmp_path / "history")
    invalid_path = tmp_path / "history" / "_raw" / "broken.json"
    invalid_path.parent.mkdir(parents=True)
    invalid_path.write_text('{"contract_version":"unknown"}', encoding="utf-8")

    status = archive.status()

    assert status["status"] == "DEGRADED"
    assert status["response_count"] == 1
    assert status["invalid_count"] == 1
    assert status["items"] == []
