"""Standalone public-market worker entry point.

The worker is deliberately disabled unless both an explicit enable flag and
an instrument allowlist are supplied.  It has no private credentials and no
order execution path.
"""

import asyncio
from contextlib import suppress
import logging
import os
import signal
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from adapters.venues.binance_public_rest import BinanceSpotPublicRestGateway
from adapters.venues.binance_spot import BinanceSpotPublicAdapter
from adapters.venues.bybit_public_rest import BybitSpotPublicRestGateway
from adapters.venues.bybit_spot import BybitSpotPublicAdapter
from adapters.venues.httpx_transport import HttpxJsonTransport
from adapters.venues.okx_public_rest import OkxSpotPublicRestGateway
from adapters.venues.okx_spot import OkxSpotPublicAdapter
from adapters.venues.websockets_transport import WebsocketsJsonConnector
from adapters.standalone.file_market_archive import FileMarketArchive
from adapters.standalone.file_market_state import FileMarketStateStore
from backend.app.services.public_network_settings import (
    PublicNetworkSettingsStore,
    PublicNetworkSnapshot,
    endpoint_defaults_from_env,
    proxy_defaults_from_env,
)
from application.market_collector import PublicMarketCollector
from application.market_data import MarketDataService
from application.order_book import SequencePolicy
from domain.market import OrderBookSnapshot
from domain.market_status import MarketConnectionState, MarketStatus


LOGGER = logging.getLogger("crypto.market_worker")


@dataclass(frozen=True, slots=True)
class WorkerInstrument:
    venue_id: str
    native_symbol: str


def parse_instrument_allowlist(value: str) -> tuple[WorkerInstrument, ...]:
    """Parse ``venue:native_symbol`` entries without accepting empty values."""
    entries: list[WorkerInstrument] = []
    for raw_entry in str(value).split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        venue_id, separator, native_symbol = entry.partition(":")
        venue_id = venue_id.strip().lower()
        native_symbol = native_symbol.strip().upper()
        if not separator or not venue_id or not native_symbol:
            raise ValueError("CRYPTO_MARKET_INSTRUMENTS must use venue:native_symbol entries")
        if venue_id not in {"binance", "okx", "bybit"}:
            raise ValueError(f"unsupported public market venue: {venue_id}")
        entries.append(WorkerInstrument(venue_id, native_symbol))
    if len({(item.venue_id, item.native_symbol) for item in entries}) != len(entries):
        raise ValueError("CRYPTO_MARKET_INSTRUMENTS contains duplicates")
    return tuple(entries)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_float(name: str, default: float, *, allow_zero: bool = False) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} must be positive")
    return value


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)
    value = Decimal(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _network_settings_path() -> Path:
    configured = os.getenv("CRYPTO_PUBLIC_NETWORK_SETTINGS_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path
    history_path = Path(os.getenv("CRYPTO_HISTORY_DATA_PATH", "/runtime/history").strip() or "/runtime/history")
    return history_path / ".runtime" / "public-network-settings.json"


def _network_settings_store() -> PublicNetworkSettingsStore:
    return PublicNetworkSettingsStore(
        _network_settings_path(),
        defaults=proxy_defaults_from_env(os.getenv),
        endpoint_defaults=endpoint_defaults_from_env(os.getenv),
    )


def _logging_config() -> None:
    logging.basicConfig(
        level=os.getenv("CRYPTO_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def run_worker() -> None:
    if not _env_bool("CRYPTO_MARKET_WORKER_ENABLED"):
        LOGGER.info("public market worker disabled")
        return

    specifications = parse_instrument_allowlist(os.getenv("CRYPTO_MARKET_INSTRUMENTS", ""))
    if not specifications:
        raise RuntimeError("CRYPTO_MARKET_INSTRUMENTS is required when the market worker is enabled")

    service = MarketDataService(max_age_seconds=_env_int("CRYPTO_MARKET_MAX_AGE_SECONDS", 2))
    depth_limit = _env_int("CRYPTO_MARKET_DEPTH_LIMIT", 100)
    timeout_seconds = _env_decimal("CRYPTO_MARKET_REST_TIMEOUT_SECONDS", "5")
    ws_speed_ms = _env_int("CRYPTO_MARKET_WS_SPEED_MS", 100)
    trust_env = _env_bool("CRYPTO_PUBLIC_TRUST_ENV", default=False)
    retry_initial_seconds = _env_float("CRYPTO_MARKET_RETRY_INITIAL_SECONDS", 1.0)
    retry_max_seconds = _env_float("CRYPTO_MARKET_RETRY_MAX_SECONDS", 30.0)
    network_poll_seconds = _env_float("CRYPTO_PUBLIC_NETWORK_SETTINGS_POLL_SECONDS", 2.0)
    if retry_max_seconds < retry_initial_seconds:
        raise ValueError("CRYPTO_MARKET_RETRY_MAX_SECONDS must be greater than or equal to the initial delay")
    network_settings = _network_settings_store()
    state_store = FileMarketStateStore(
        Path(os.getenv("CRYPTO_MARKET_STATE_PATH", "/runtime/market"))
    )
    archive_store = None
    if _env_bool("CRYPTO_MARKET_ARCHIVE_ENABLED", default=False):
        archive_store = FileMarketArchive(
            Path(os.getenv("CRYPTO_MARKET_ARCHIVE_PATH", "/runtime/market/archive")),
            segment_seconds=_env_int("CRYPTO_MARKET_ARCHIVE_SEGMENT_SECONDS", 3600),
            max_segment_bytes=_env_int("CRYPTO_MARKET_ARCHIVE_SEGMENT_BYTES", 64 * 1024 * 1024),
            retention_seconds=_env_int("CRYPTO_MARKET_ARCHIVE_RETENTION_SECONDS", 7 * 24 * 3600),
            cleanup_interval_seconds=_env_float(
                "CRYPTO_MARKET_ARCHIVE_CLEANUP_INTERVAL_SECONDS",
                60.0,
            ),
        )
        recovered_bytes = archive_store.recover()
        if recovered_bytes:
            LOGGER.warning("recovered incomplete market archive tail bytes=%s", recovered_bytes)
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    LOGGER.info(
        "public market worker starting venues=%s instruments=%s",
        ",".join(sorted({item.venue_id for item in specifications})),
        len(specifications),
    )
    await asyncio.gather(
        *(
            _run_instrument(
                specification,
                service,
                stop_event,
                depth_limit=depth_limit,
                timeout_seconds=float(timeout_seconds),
                ws_speed_ms=ws_speed_ms,
                state_store=state_store,
                archive_store=archive_store,
                trust_env=trust_env,
                retry_initial_seconds=retry_initial_seconds,
                retry_max_seconds=retry_max_seconds,
                network_settings=network_settings,
                network_poll_seconds=network_poll_seconds,
            )
            for specification in specifications
        )
    )
    LOGGER.info("public market worker stopped")


async def _run_instrument(
    specification: WorkerInstrument,
    service: MarketDataService,
    stop_event: asyncio.Event,
    *,
    depth_limit: int,
    timeout_seconds: float,
    ws_speed_ms: int,
    state_store: FileMarketStateStore,
    archive_store: FileMarketArchive | None,
    trust_env: bool,
    retry_initial_seconds: float,
    retry_max_seconds: float,
    network_settings: PublicNetworkSettingsStore,
    network_poll_seconds: float,
) -> None:
    delay = retry_initial_seconds
    while not stop_event.is_set():
        transport: HttpxJsonTransport | None = None
        try:
            configuration = network_settings.current()
            venue_proxies = configuration.proxies[specification.venue_id]
            venue_endpoints = configuration.endpoints[specification.venue_id]
            venue_api_routes = configuration.api_routes[specification.venue_id]
            websocket_connector = WebsocketsJsonConnector(proxy=venue_proxies["ws_proxy"] or None)
            collector, transport = await _build_collector(
                specification,
                service,
                websocket_connector,
                depth_limit=depth_limit,
                timeout_seconds=timeout_seconds,
                ws_speed_ms=ws_speed_ms,
                state_store=state_store,
                archive_store=archive_store,
                http_proxy=venue_proxies["http_proxy"] or None,
                trust_env=trust_env,
                retry_initial_seconds=retry_initial_seconds,
                retry_max_seconds=retry_max_seconds,
                public_rest_base_url=venue_endpoints["public_rest_base_url"],
                public_ws_base_url=venue_endpoints["public_ws_base_url"],
                api_routes=venue_api_routes,
            )
            delay = retry_initial_seconds
            reloaded = await _run_collector_with_reload(
                collector,
                configuration,
                network_settings,
                stop_event,
                poll_seconds=network_poll_seconds,
            )
            if stop_event.is_set():
                return
            if reloaded:
                LOGGER.info(
                    "public market network settings changed; reconnecting venue=%s instrument=%s",
                    specification.venue_id,
                    specification.native_symbol,
                )
                continue
            LOGGER.warning(
                "public market collector stopped unexpectedly venue=%s instrument=%s",
                specification.venue_id,
                specification.native_symbol,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning(
                "public market collector unavailable venue=%s instrument=%s error_type=%s retry_in=%.1fs",
                specification.venue_id,
                specification.native_symbol,
                type(error).__name__,
                delay,
            )
            _persist_worker_failure(service, state_store, specification.venue_id)
        finally:
            if transport is not None:
                transport.close()
        if stop_event.is_set():
            return
        await _wait_for_stop(stop_event, delay)
        delay = min(retry_max_seconds, max(retry_initial_seconds, delay * 2))


async def _run_collector_with_reload(
    collector: PublicMarketCollector,
    configuration: PublicNetworkSnapshot,
    network_settings: PublicNetworkSettingsStore,
    stop_event: asyncio.Event,
    *,
    poll_seconds: float,
) -> bool:
    """Stop the current connection when the UI changes the proxy file."""
    connection_stop = asyncio.Event()
    collector_task = asyncio.create_task(collector.run(connection_stop))
    watcher_task = asyncio.create_task(
        _watch_network_settings(
            configuration,
            network_settings,
            connection_stop,
            stop_event,
            poll_seconds=poll_seconds,
        )
    )
    try:
        done, _ = await asyncio.wait(
            {collector_task, watcher_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if watcher_task in done:
            changed = watcher_task.result() == "changed"
            if not collector_task.done():
                collector_task.cancel()
                with suppress(asyncio.CancelledError):
                    await collector_task
            else:
                await collector_task
            return changed
        await collector_task
        return False
    finally:
        if not watcher_task.done():
            watcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await watcher_task


async def _watch_network_settings(
    configuration: PublicNetworkSnapshot,
    network_settings: PublicNetworkSettingsStore,
    connection_stop: asyncio.Event,
    stop_event: asyncio.Event,
    *,
    poll_seconds: float,
) -> str:
    while not connection_stop.is_set():
        if stop_event.is_set():
            connection_stop.set()
            return "stopped"
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            connection_stop.set()
            return "stopped"
        if network_settings.current().fingerprint() != configuration.fingerprint():
            connection_stop.set()
            return "changed"
    return "stopped"


async def _build_collector(
    specification: WorkerInstrument,
    service: MarketDataService,
    websocket_connector: WebsocketsJsonConnector,
    *,
    depth_limit: int,
    timeout_seconds: float,
    ws_speed_ms: int,
    state_store: FileMarketStateStore,
    archive_store: FileMarketArchive | None = None,
    http_proxy: str | None = None,
    trust_env: bool = False,
    retry_initial_seconds: float = 1.0,
    retry_max_seconds: float = 30.0,
    public_rest_base_url: str,
    public_ws_base_url: str,
    api_routes: dict[str, str] | None = None,
) -> tuple[PublicMarketCollector, HttpxJsonTransport]:
    subscription = None
    if specification.venue_id == "binance":
        adapter = BinanceSpotPublicAdapter()
        transport = HttpxJsonTransport(
            public_rest_base_url,
            timeout_seconds=timeout_seconds,
            trust_env=trust_env,
            proxy=http_proxy,
        )
        gateway = BinanceSpotPublicRestGateway(transport, depth_limit=depth_limit, api_routes=api_routes)
        try:
            instrument = await asyncio.to_thread(gateway.fetch_instrument, specification.native_symbol)
        except Exception:
            transport.close()
            raise
        stream_url = adapter.depth_stream_url(instrument, ws_speed_ms, base_url=public_ws_base_url)
        parser = adapter.normalize_depth_event
        sequence_policy = SequencePolicy.RANGE
    elif specification.venue_id == "okx":
        adapter = OkxSpotPublicAdapter()
        transport = HttpxJsonTransport(
            public_rest_base_url,
            timeout_seconds=timeout_seconds,
            trust_env=trust_env,
            proxy=http_proxy,
        )
        gateway = OkxSpotPublicRestGateway(transport, depth_limit=depth_limit, api_routes=api_routes)
        try:
            instrument = await asyncio.to_thread(gateway.fetch_instrument, specification.native_symbol)
        except Exception:
            transport.close()
            raise
        stream_url = public_ws_base_url
        parser = adapter.normalize_books_event
        sequence_policy = SequencePolicy.PREVIOUS_ID
        subscription = adapter.books_subscription(instrument)
    else:
        adapter = BybitSpotPublicAdapter()
        transport = HttpxJsonTransport(
            public_rest_base_url,
            timeout_seconds=timeout_seconds,
            trust_env=trust_env,
            proxy=http_proxy,
        )
        bybit_depth = _env_int("CRYPTO_MARKET_BYBIT_DEPTH_LIMIT", 50)
        gateway = BybitSpotPublicRestGateway(transport, depth_limit=bybit_depth, api_routes=api_routes)
        try:
            instrument = await asyncio.to_thread(gateway.fetch_instrument, specification.native_symbol)
        except Exception:
            transport.close()
            raise
        stream_url = public_ws_base_url
        parser = adapter.normalize_order_book_event
        sequence_policy = SequencePolicy.RANGE
        subscription = adapter.orderbook_subscription(instrument, bybit_depth)

    collector = PublicMarketCollector(
        service,
        gateway,
        websocket_connector,
        instrument=instrument,
        stream_url=stream_url,
        parser=parser,
        subscription=subscription,
        sequence_policy=sequence_policy,
        max_age_seconds=_env_int("CRYPTO_MARKET_MAX_AGE_SECONDS", 2),
        bootstrap_stream_before_snapshot=specification.venue_id == "binance",
        failure_retry_initial_seconds=retry_initial_seconds,
        failure_retry_max_seconds=retry_max_seconds,
        on_snapshot=lambda snapshot: _persist_snapshot(state_store, snapshot, archive_store),
        on_status=state_store.write_status,
    )
    return collector, transport


def _persist_worker_failure(
    service: MarketDataService,
    state_store: FileMarketStateStore,
    venue_id: str,
) -> None:
    status = service.mark_disconnected(venue_id, "COLLECTOR_UNAVAILABLE")
    try:
        state_store.write_status(status)
    except Exception:
        LOGGER.exception("unable to persist public market failure venue=%s", venue_id)


async def _wait_for_stop(stop_event: asyncio.Event, delay_seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
    except asyncio.TimeoutError:
        pass


def _persist_snapshot(
    state_store: FileMarketStateStore,
    snapshot: OrderBookSnapshot,
    archive_store: FileMarketArchive | None = None,
) -> None:
    """Persist normalized state and log bounded metadata only."""
    state_store.write_snapshot(snapshot)
    if archive_store is not None:
        archive_store.append_snapshot(snapshot)
    state_store.write_status(
        MarketStatus(
            venue_id=snapshot.instrument.venue_id,
            state=MarketConnectionState.CONNECTED,
            last_received_at=snapshot.received_timestamp,
            last_sequence=snapshot.sequence,
        )
    )
    LOGGER.debug(
        "accepted market snapshot venue=%s instrument=%s sequence=%s bid=%s ask=%s",
        snapshot.instrument.venue_id,
        snapshot.instrument.canonical_symbol,
        snapshot.sequence,
        None if snapshot.best_bid is None else snapshot.best_bid.price,
        None if snapshot.best_ask is None else snapshot.best_ask.price,
    )


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue


def main() -> None:
    _logging_config()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
