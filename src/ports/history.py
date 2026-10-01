"""Ports for public history gateways and durable history metadata."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from domain.candle import Candle, HistoryQuery


class PublicHistoryGateway(Protocol):
    venue_id: str
    source: str

    def fetch_candles(self, query: HistoryQuery) -> tuple[Candle, ...]:
        """Fetch and normalize all candles in the query's half-open range."""


class HistoryMetadataWriter(Protocol):
    def commit_dataset(
        self,
        dataset: Mapping[str, object],
        *,
        raw_responses: Sequence[Mapping[str, object]],
        raw_response_state: str,
        parquet_state: str,
    ) -> dict[str, object]:
        """Commit one verified dataset and its provenance in one database transaction."""


class HistoryMetadataError(RuntimeError):
    """The verified file could not be committed to the history metadata store."""
