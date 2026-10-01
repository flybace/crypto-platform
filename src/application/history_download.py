"""Historical download use case shared by the API, CLI and future GACE App."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from domain.candle import Candle, HistoryQuery
from ports.history import HistoryMetadataError, HistoryMetadataWriter, PublicHistoryGateway

from .history_response_archive import HistoryResponseArchive, HistoryResponseArchiveError
from .history_storage import HistoryStorage, StoredDataset


class HistoryDownloadError(RuntimeError):
    """A public history request returned no usable candles."""


class HistoryRawArchiveError(HistoryDownloadError):
    """The download completed but its raw public responses were not archived."""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    query: HistoryQuery
    dataset: StoredDataset
    fetched_rows: int
    raw_archive: dict[str, object] | None = None
    history_metadata: dict[str, object] | None = None


class HistoryDownloadService:
    def __init__(
        self,
        gateways: Mapping[str, PublicHistoryGateway],
        storage: HistoryStorage,
        *,
        raw_archive: HistoryResponseArchive | None = None,
        metadata_writer: HistoryMetadataWriter | None = None,
    ) -> None:
        self._gateways = {str(key).strip().lower(): value for key, value in gateways.items()}
        self.storage = storage
        self._raw_archive = raw_archive
        self.raw_archive = raw_archive
        self._metadata_writer = metadata_writer

    def download(self, query: HistoryQuery) -> DownloadResult:
        gateway = self._gateways.get(query.venue_id)
        if gateway is None:
            raise HistoryDownloadError(f"no public history gateway is configured for {query.venue_id}")
        reset_capture = getattr(gateway, "reset_raw_responses", None)
        if callable(reset_capture):
            reset_capture()
        responses = ()
        try:
            candles = gateway.fetch_candles(query)
        finally:
            drain_capture = getattr(gateway, "drain_raw_responses", None)
            if callable(drain_capture):
                responses = tuple(drain_capture())
        if not candles:
            raise HistoryDownloadError("public history returned no candles in the requested range")
        dataset = self.storage.upsert(query, candles, source=gateway.source)
        raw_result: dict[str, object] | None = None
        if self._raw_archive is not None:
            if not responses:
                raise HistoryRawArchiveError("history gateway returned no responses for raw archiving")
            try:
                raw_result = self._raw_archive.archive(
                    query,
                    responses,
                    dataset_id=dataset.manifest.dataset_id,
                )
            except HistoryResponseArchiveError as error:
                raise HistoryRawArchiveError("raw history response archive failed") from error
        metadata_result: dict[str, object] | None = None
        if self._metadata_writer is not None:
            raw_items = raw_result.get("items", []) if raw_result else []
            try:
                metadata_result = self._metadata_writer.commit_dataset(
                    self.storage.dataset_dict(dataset),
                    raw_responses=tuple(item for item in raw_items if isinstance(item, dict)),
                    raw_response_state="READY" if raw_result else "NOT_CONFIGURED",
                    parquet_state="PENDING",
                )
            except HistoryMetadataError:
                raise
            except Exception as error:
                raise HistoryMetadataError("history metadata commit failed") from error
        return DownloadResult(
            query=query,
            dataset=dataset,
            fetched_rows=len(candles),
            raw_archive=raw_result,
            history_metadata=metadata_result,
        )

    def download_many(self, queries: Sequence[HistoryQuery]) -> tuple[DownloadResult, ...]:
        return tuple(self.download(query) for query in queries)

    def close(self) -> None:
        for gateway in self._gateways.values():
            close = getattr(gateway, "close", None)
            if callable(close):
                close()

    def update_proxies(self, proxies: Mapping[str, str | None]) -> None:
        """Reload public REST proxies without rebuilding the job manager."""
        for venue, gateway in self._gateways.items():
            update = getattr(gateway, "update_proxy", None)
            if callable(update):
                update(proxies.get(venue))

    def update_endpoints(self, history_base_urls: Mapping[str, str]) -> None:
        """Reload public history endpoints without rebuilding the job manager."""
        for venue, gateway in self._gateways.items():
            update = getattr(gateway, "update_base_url", None)
            if callable(update) and venue in history_base_urls:
                update(history_base_urls[venue])

    def update_network(
        self,
        history_base_urls: Mapping[str, str],
        proxies: Mapping[str, str | None],
        api_routes: Mapping[str, Mapping[str, str | None]] | None = None,
    ) -> None:
        """Apply endpoint and proxy changes as one runtime configuration pass."""
        self.update_endpoints(history_base_urls)
        self.update_proxies(proxies)
        if api_routes is not None:
            self.update_api_routes(api_routes)

    def update_api_routes(self, api_routes: Mapping[str, Mapping[str, str | None]]) -> None:
        """Reload public REST paths without rebuilding the job manager."""
        for venue, gateway in self._gateways.items():
            update = getattr(gateway, "update_api_routes", None)
            if callable(update) and venue in api_routes:
                update(api_routes[venue])
