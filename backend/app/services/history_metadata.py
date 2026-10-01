"""History metadata repository with explicit file, provenance, and mirror states."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ports.history import HistoryMetadataError

from .task_store import TaskStore


class HistoryMetadataRepository:
    """Application-facing adapter around the relational history repository."""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def commit_dataset(
        self,
        dataset: Mapping[str, object],
        *,
        raw_responses: Sequence[Mapping[str, object]],
        raw_response_state: str,
        parquet_state: str,
    ) -> dict[str, object]:
        try:
            return self._store.commit_history_dataset(
                dict(dataset),
                raw_responses=(dict(item) for item in raw_responses),
                raw_response_state=raw_response_state,
                parquet_state=parquet_state,
            )
        except Exception as error:
            if isinstance(error, HistoryMetadataError):
                raise
            raise HistoryMetadataError("history metadata transaction failed") from error

    def reconcile(self, datasets: Iterable[Mapping[str, object]]) -> dict[str, object]:
        try:
            return self._store.sync_history_datasets(dict(dataset) for dataset in datasets)
        except Exception as error:
            raise HistoryMetadataError("history metadata reconciliation failed") from error

    def record_archive(self, archive: Mapping[str, object]) -> dict[str, object]:
        try:
            return self._store.sync_history_archive(dict(archive))
        except Exception as error:
            raise HistoryMetadataError("history archive metadata update failed") from error

    def status(self) -> dict[str, object]:
        return self._store.history_metadata_status()
