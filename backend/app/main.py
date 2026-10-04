"""FastAPI composition root for the standalone crypto application."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from adapters.standalone.file_market_state import FileMarketStateStore  # noqa: E402
from adapters.standalone.file_market_archive import FileMarketArchive  # noqa: E402
from application.market_data import MarketDataService  # noqa: E402
from application.pool_catalog import PoolCatalog  # noqa: E402
from application.spread_research import HistoricalSpreadResearch  # noqa: E402
from application.strategy_incubator import StrategyIncubatorService  # noqa: E402
from application.history_archive import ParquetHistoryArchive  # noqa: E402
from .api.auth import router as auth_router  # noqa: E402
from .api.advice import router as advice_router  # noqa: E402
from .api.assistant import router as assistant_router  # noqa: E402
from .api.backtests import router as backtests_router  # noqa: E402
from .api.capabilities import router as capabilities_router  # noqa: E402
from .api.market import router as market_router  # noqa: E402
from .api.news import router as news_router  # noqa: E402
from .api.history import router as history_router  # noqa: E402
from .api.incubators import router as incubators_router  # noqa: E402
from .api.notifications import router as notifications_router  # noqa: E402
from .api.paper import router as paper_router  # noqa: E402
from .api.pools import router as pools_router  # noqa: E402
from .api.risk import router as risk_router  # noqa: E402
from .api.research import router as research_router  # noqa: E402
from .api.screening import router as screening_router  # noqa: E402
from .api.strategies import router as strategies_router  # noqa: E402
from .api.system import router as system_router  # noqa: E402
from .api.settings import router as settings_router  # noqa: E402
from .api.tasks import router as tasks_router  # noqa: E402
from .api.tradeplan import router as tradeplan_router  # noqa: E402
from .api.runtime import router as runtime_router  # noqa: E402
from .api.strategy_matrices import router as strategy_matrices_router  # noqa: E402
from .api.strategy_packages import router as strategy_packages_router  # noqa: E402
from .api.account import router as account_router  # noqa: E402
from .api.strategy_funnel import router as strategy_funnel_router  # noqa: E402
from .api.market_regime import router as market_regime_router  # noqa: E402
from .auth.service import AuthService  # noqa: E402
from .services.advice import AdviceService  # noqa: E402
from .services.assistant import AssistantService  # noqa: E402
from .services.history_service import build_history_service  # noqa: E402
from .services.history_jobs import HistoryJobManager  # noqa: E402
from .services.history_metadata import HistoryMetadataRepository  # noqa: E402
from .services.history_scheduler_state import HistorySchedulerState  # noqa: E402
from .services.history_sync import HistorySyncService  # noqa: E402
from .services.instrument_catalog import InstrumentCatalogService  # noqa: E402
from .services.public_tickers import PublicTickerService  # noqa: E402
from .services.notifications import NotificationService  # noqa: E402
from .services.opportunity_log import OpportunityLogService  # noqa: E402
from .services.news import NewsService  # noqa: E402
from .services.backtest_runs import BacktestRunManager  # noqa: E402
from .services.strategy_presets import StrategyPresetService  # noqa: E402
from .services.tune_history import TuneHistoryService  # noqa: E402
from .services.domain_state import build_domain_state, build_runtime_state  # noqa: E402
from .services.paper_trading import PaperTradingService  # noqa: E402
from .services.paper_automation import PaperAutomationService  # noqa: E402
from .services.paper_follow import PaperFollowService  # noqa: E402
from .services.paper_live import PaperLiveService  # noqa: E402
from .services.risk_policy import RiskPolicyService  # noqa: E402
from .services.research_runs import ResearchRunService  # noqa: E402
from .services.strategy_registry import StrategyRegistry  # noqa: E402
from .services.strategy_matrix import StrategyMatrixService  # noqa: E402
from .services.strategy_packages import StrategyPackageService  # noqa: E402
from .services.task_store import TaskStore  # noqa: E402
from .services.task_dispatch_consistency import TaskDispatchConsistency  # noqa: E402
from .services.redis_runtime import RedisRuntime  # noqa: E402
from .services.task_queue import RedisTaskQueue, TaskQueueError  # noqa: E402
from .services.task_dispatcher import TaskDispatcher  # noqa: E402
from .services.task_quota import TaskQuota  # noqa: E402
from .services.task_result_archive import TaskResultArchive  # noqa: E402
from .services.task_log_archive import TaskLogArchive  # noqa: E402
from .services.task_log_lifecycle import (  # noqa: E402
    TaskLogArchiveLifecycle,
    resolve_task_log_lifecycle_path,
)
from .services.public_network_settings import (  # noqa: E402
    PublicNetworkSettingsStore,
    endpoint_defaults_from_settings,
    proxy_defaults_from_settings,
    resolve_public_network_settings_path,
    settings_with_public_network,
)
from application.screening import ScreeningService  # noqa: E402
from .settings import Settings  # noqa: E402
from web.market_status import MarketStatusView  # noqa: E402
from ports.market_archive import MarketArchive  # noqa: E402


def create_app(
    settings: Settings | None = None,
    market_data: MarketDataService | None = None,
    history_service=None,
    history_jobs: HistoryJobManager | None = None,
    instrument_catalog: InstrumentCatalogService | None = None,
    ticker_service: PublicTickerService | None = None,
    market_archive: MarketArchive | None = None,
) -> FastAPI:
    base_settings = settings or Settings.from_env()
    network_settings = PublicNetworkSettingsStore(
        resolve_public_network_settings_path(base_settings, PROJECT_ROOT),
        defaults=proxy_defaults_from_settings(base_settings),
        endpoint_defaults=endpoint_defaults_from_settings(base_settings),
    )
    network_snapshot = network_settings.current()
    runtime_settings = settings_with_public_network(base_settings, network_snapshot)
    service = market_data or MarketDataService()
    state_path = os.getenv("CRYPTO_MARKET_STATE_PATH")
    state_store = None if not state_path else FileMarketStateStore(state_path, create=False)
    runtime_market_archive = market_archive
    if runtime_market_archive is None and os.getenv("CRYPTO_MARKET_ARCHIVE_ENABLED", "false").strip().lower() == "true":
        runtime_market_archive = FileMarketArchive(
            os.getenv("CRYPTO_MARKET_ARCHIVE_PATH", "/runtime/market/archive"),
            create=False,
        )
    task_store = TaskStore(
        runtime_settings.database_url,
        required=runtime_settings.persistence_required,
    )
    history_metadata = HistoryMetadataRepository(task_store)
    runtime_history_service = history_service or build_history_service(
        runtime_settings,
        data_root=_history_root(runtime_settings),
        metadata_writer=history_metadata,
        api_routes=network_snapshot.api_routes,
    )
    runtime_history_archive = ParquetHistoryArchive(runtime_history_service.storage.root)
    redis_runtime = RedisRuntime(
        runtime_settings.redis_url,
        required=runtime_settings.persistence_required,
    )
    task_queue = None
    if runtime_settings.task_queue_mode == "redis":
        try:
            task_queue = RedisTaskQueue(runtime_settings.redis_url)
        except TaskQueueError:
            if runtime_settings.persistence_required:
                raise
    runtime_history_jobs = history_jobs or HistoryJobManager(
        runtime_history_service,
        task_store=task_store,
        task_queue=task_queue,
        archive=runtime_history_archive,
    )
    runtime_state_root = Path(runtime_history_service.storage.root) / ".runtime"
    task_store.event_archive = TaskLogArchive(runtime_state_root / "task-logs")
    task_store.reconcile_event_archives()
    task_log_lifecycle = TaskLogArchiveLifecycle(
        task_store.event_archive,
        retention_days=runtime_settings.task_log_retention_days,
    )
    task_log_backup_path = resolve_task_log_lifecycle_path(
        runtime_settings.task_log_backup_path,
        PROJECT_ROOT,
    )
    task_log_restore_path = resolve_task_log_lifecycle_path(
        runtime_settings.task_log_restore_path,
        PROJECT_ROOT,
    )
    task_result_archive = TaskResultArchive(runtime_state_root / "task-results")
    domain_state = lambda name: build_domain_state(task_store, runtime_state_root, name)
    runtime_instrument_catalog = instrument_catalog or InstrumentCatalogService(
        base_urls={
            "binance": runtime_settings.binance_public_rest_base_url,
            "okx": runtime_settings.okx_public_rest_base_url,
            "bybit": runtime_settings.bybit_public_rest_base_url,
        },
        proxies={
            "binance": runtime_settings.binance_public_http_proxy,
            "okx": runtime_settings.okx_public_http_proxy,
            "bybit": runtime_settings.bybit_public_http_proxy,
        },
        timeout_seconds=runtime_settings.history_timeout_seconds,
        trust_env=runtime_settings.public_trust_env,
        state_path=runtime_state_root / "instrument-catalog.json",
        api_routes=network_snapshot.api_routes,
    )
    runtime_ticker_service = ticker_service or PublicTickerService(
        base_urls={
            "binance": runtime_settings.binance_public_rest_base_url,
            "okx": runtime_settings.okx_public_rest_base_url,
            "bybit": runtime_settings.bybit_public_rest_base_url,
        },
        proxies={
            "binance": runtime_settings.binance_public_http_proxy,
            "okx": runtime_settings.okx_public_http_proxy,
            "bybit": runtime_settings.bybit_public_http_proxy,
        },
        timeout_seconds=runtime_settings.history_timeout_seconds,
        trust_env=runtime_settings.public_trust_env,
        api_routes=network_snapshot.api_routes,
    )
    runtime_backtest_runs = BacktestRunManager(
        runtime_history_service.storage,
        state_store=domain_state("backtest-runs.json"),
        task_store=task_store,
    )
    runtime_tune_history = TuneHistoryService(
        state_path=runtime_state_root / "tune-history.json",
    )
    runtime_strategy_presets = StrategyPresetService(
        state_path=runtime_state_root / "strategy-presets.json",
    )
    strategy_registry = StrategyRegistry(state_store=domain_state("strategies.json"))
    strategy_packages = StrategyPackageService(state_store=domain_state("strategy-packages.json"))
    strategy_matrices = StrategyMatrixService(
        runtime_history_service.storage,
        strategy_registry,
        state_store=domain_state("strategy-matrices.json"),
        task_store=task_store,
    )
    pool_catalog = PoolCatalog(
        runtime_history_service.storage,
        state_store=domain_state("pools.json"),
    )
    screening_service = ScreeningService(
        runtime_history_service.storage,
        pool_catalog,
        state_store=domain_state("screening-runs.json"),
        task_store=task_store,
    )
    research_runs = ResearchRunService(
        runtime_history_service.storage,
        pool_catalog,
        screening_service,
        state_store=domain_state("research-runs.json"),
        task_store=task_store,
    )
    strategy_incubator = StrategyIncubatorService(
        screening_service,
        state_store=domain_state("strategy-incubators.json"),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        paper_live_task = asyncio.create_task(_paper_live_engine())
        try:
            account_recon_scheduler.start()
            yield
        finally:
            paper_live_task.cancel()
            try:
                await paper_live_task
            except asyncio.CancelledError:
                pass
            try:
                close_tickers = getattr(runtime_ticker_service, "close", None)
                if callable(close_tickers):
                    close_tickers()
                close_catalog = getattr(runtime_instrument_catalog, "close", None)
                if callable(close_catalog):
                    close_catalog()
                close_account = getattr(app.state.account_gateway, "close", None)
                if callable(close_account):
                    close_account()
                account_recon_scheduler.stop()
                account_ledger_store.close()
                runtime_history_jobs.close()
            finally:
                task_store.close()

    app = FastAPI(
        title=runtime_settings.app_name,
        version=runtime_settings.version,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.public_network_settings = network_settings
    app.state.auth_service = AuthService(runtime_settings)
    app.state.market_status_view = MarketStatusView(service, state_store)
    app.state.market_state_store = state_store
    app.state.market_archive = runtime_market_archive
    app.state.history_storage = runtime_history_service.storage
    app.state.history_service = runtime_history_service
    app.state.history_archive = runtime_history_archive
    app.state.history_response_archive = runtime_history_service.raw_archive
    app.state.history_metadata = history_metadata
    app.state.spread_research = HistoricalSpreadResearch(runtime_history_service.storage)
    app.state.history_jobs = runtime_history_jobs
    app.state.history_scheduler_state = HistorySchedulerState(
        runtime_state_root / "history-scheduler.json",
        interval_seconds=runtime_settings.scheduler_interval_seconds,
        state_store=build_runtime_state(task_store, runtime_state_root, "history-scheduler.json"),
    )
    app.state.task_store = task_store
    app.state.task_log_archive = task_store.event_archive
    app.state.task_log_lifecycle = task_log_lifecycle
    app.state.task_log_backup_path = task_log_backup_path
    app.state.task_log_restore_path = task_log_restore_path
    app.state.task_result_archive = task_result_archive
    app.state.redis_runtime = redis_runtime
    app.state.task_queue = task_queue
    app.state.task_dispatch_consistency = (
        TaskDispatchConsistency(task_store, task_queue) if task_queue is not None else None
    )
    app.state.task_dispatcher = TaskDispatcher(
        task_store,
        task_queue,
        mode=runtime_settings.task_queue_mode,
        max_attempts=runtime_settings.task_max_attempts,
        quota=TaskQuota.from_settings(runtime_settings),
        history_replayer=runtime_history_jobs.replay_dead_letter,
    )
    app.state.history_sync = HistorySyncService(
        runtime_history_service.storage,
        state_path=runtime_state_root / "history-sync.json",
        state_store=build_runtime_state(task_store, runtime_state_root, "history-sync.json"),
        default_lookback_days=runtime_settings.history_sync_lookback_days,
    )
    app.state.instrument_catalog = runtime_instrument_catalog
    app.state.public_tickers = runtime_ticker_service
    # Read-only account foundation (M5): secret provider + Binance gateway.
    # Gateway is only created when credentials are configured; otherwise the
    # account API reports unconfigured instead of failing silently.
    # FileSecretProvider: keys can be entered via UI (stored 0600, survives
    # resets), env vars still take precedence. Gateway is hot-swappable —
    # saving credentials via API rebuilds it without a restart.
    from adapters.standalone.file_secret_provider import FileSecretProvider

    secrets_path = Path(runtime_history_service.storage.root) / ".secrets" / "exchange.json"
    secret_provider = FileSecretProvider(secrets_path)
    app.state.secret_provider = secret_provider

    def _build_account_gateway():
        if not secret_provider.is_configured("binance"):
            return None
        from adapters.venues.binance_account import BinanceReadOnlyAccountGateway

        return BinanceReadOnlyAccountGateway(
            secret_provider.get_api_key("binance") or "",
            secret_provider.get_api_secret("binance") or "",
            base_url=runtime_settings.binance_public_rest_base_url,
            trust_env=runtime_settings.public_trust_env,
        )

    app.state.account_gateway = _build_account_gateway()
    app.state.rebuild_account_gateway = _build_account_gateway
    from .services.account_ledger_store import AccountLedgerStore
    from .services.account_reconciliation_scheduler import AccountReconciliationScheduler

    account_ledger_store = AccountLedgerStore(runtime_settings.database_url or "sqlite:///data/account-ledger.db")
    app.state.account_ledger_store = account_ledger_store
    account_recon_scheduler = AccountReconciliationScheduler(
        ledger_store=account_ledger_store,
        gateway=app.state.account_gateway,
        interval_seconds=300,
        gateway_provider=lambda: app.state.account_gateway,
    )
    app.state.account_recon_scheduler = account_recon_scheduler
    from .services.trading_settings import TradingSettingsStore

    app.state.trading_settings_store = TradingSettingsStore(
        Path(runtime_settings.history_data_path).parent / "trading-settings.json"
    )
    app.state.backtest_runs = runtime_backtest_runs
    app.state.tune_history = runtime_tune_history
    app.state.strategy_presets = runtime_strategy_presets
    app.state.strategy_registry = strategy_registry
    app.state.strategy_packages = strategy_packages
    app.state.strategy_matrices = strategy_matrices
    app.state.pool_catalog = pool_catalog
    app.state.screening_service = screening_service
    app.state.research_runs = research_runs
    app.state.strategy_incubator = strategy_incubator
    app.state.advice_service = AdviceService(
        runtime_history_service.storage,
        screening_service,
        state_store=domain_state("advice.json"),
    )
    app.state.news_service = NewsService(
        runtime_history_service.storage,
        state_store=domain_state("news.json"),
    )
    app.state.notifications = NotificationService(state_store=domain_state("notifications.json"))
    app.state.opportunity_log = OpportunityLogService(
        state_store=domain_state("opportunities.json"),
        notifications=app.state.notifications,
    )
    app.state.assistant = AssistantService(state_store=domain_state("assistant.json"))
    paper_trading = PaperTradingService(
        runtime_history_service.storage,
        state_store=domain_state("paper-trading.json"),
        task_store=task_store,
    )
    app.state.paper_trading = paper_trading
    app.state.paper_automation = PaperAutomationService(
        paper_trading,
        strategy_registry,
        state_store=domain_state("paper-automation.json"),
    )
    app.state.paper_follow = PaperFollowService(
        state_path=runtime_state_root / "paper-follow.json",
    )
    from .services.paper_live import PaperLiveManager

    def _paper_factory(instance_id: str) -> PaperTradingService:
        """每个策略实例独立模拟账户。"""
        return PaperTradingService(
            runtime_history_service.storage,
            state_store=domain_state(f"paper-trading-{instance_id}.json"),
            task_store=task_store,
        )

    # 市场状态服务：评估市场强弱，联动仓位；危机/数据不可用时禁开仓
    from .services.market_regime import MarketRegimeService

    market_regime = MarketRegimeService(
        storage=runtime_history_service.storage,
        state_store=domain_state("market-regime.json"),
    )
    app.state.market_regime = market_regime
    paper_live_manager = PaperLiveManager(
        strategies=strategy_registry,
        storage=runtime_history_service.storage,
        state_store=domain_state("paper-live-manager.json"),
        paper_factory=_paper_factory,
        regime_service=market_regime,
        # 实例运行时状态（entry_price / day_start_equity / last_candle_time…）
        # 必须落盘：云机随时重置，只活在内存里等于风控失忆。
        # SqlStateStore 没有 .path，manager 无法自行派生实例路径，故显式传入。
        instance_state_store_factory=lambda instance_id: domain_state(
            f"paper-live-{instance_id}.json"
        ),
    )
    # 迁移旧单实例：首次运行时把 paper-live.json 导入为默认实例
    if not paper_live_manager.list_instances():
        try:
            legacy = domain_state("paper-live.json")
            payload = legacy.load({"version": 1, "live": {}})
            live_config = payload.get("live", {}) if isinstance(payload, dict) else {}
            if live_config:
                paper_live_manager.create_instance(live_config)
                paper_live_logger = logging.getLogger("crypto.paper_live")
                paper_live_logger.info("migrated legacy paper-live.json to manager")
        except Exception:
            pass
    app.state.paper_live_manager = paper_live_manager
    # 策略准入漏斗：评级 + 阶段验证，模拟盘需要 A 级准入
    from .services.strategy_funnel import StrategyFunnelService

    app.state.strategy_funnel = StrategyFunnelService(
        state_store=domain_state("strategy-funnel.json"),
    )
    # 兼容旧 API：app.state.paper_live 指向 manager（API 已更新为多实例）
    paper_live_logger = logging.getLogger("crypto.paper_live")

    async def _paper_live_engine() -> None:
        """策略引擎：所有已启动的策略实例独立运行，每 60 秒各检查一次。

        不依赖历史同步调度器——策略有自己的运行循环：
        启动策略 → 每轮读最新已收盘 K 线 → 有信号就自动下单（模拟）。
        tick 本身幂等（每根 K 线最多一笔），频繁检查安全。
        """
        while True:
            try:
                outcomes = await asyncio.to_thread(paper_live_manager.tick_all)
                for outcome in outcomes:
                    status = outcome.get("status") if isinstance(outcome, dict) else "?"
                    if status not in {"disabled", "no_new_candle"}:
                        paper_live_logger.info("paper live tick: %s", outcome)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # 引擎永不崩溃，报错只记日志
                paper_live_logger.warning("paper live tick failed: %s", error)
            await asyncio.sleep(60)
    app.state.risk_policy = RiskPolicyService(state_store=domain_state("risk-policy.json"))
    app.state.domain_state = task_store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": runtime_settings.version,
            "execution_mode": runtime_settings.execution_mode,
            "auth": "enabled",
        }

    app.include_router(auth_router)
    app.include_router(advice_router)
    app.include_router(assistant_router)
    app.include_router(capabilities_router)
    app.include_router(market_router)
    app.include_router(news_router)
    app.include_router(history_router)
    app.include_router(incubators_router)
    app.include_router(notifications_router)
    app.include_router(backtests_router)
    app.include_router(strategies_router)
    app.include_router(strategy_packages_router)
    app.include_router(strategy_matrices_router)
    app.include_router(system_router)
    app.include_router(settings_router)
    app.include_router(pools_router)
    app.include_router(screening_router)
    app.include_router(paper_router)
    app.include_router(strategy_funnel_router)
    app.include_router(market_regime_router)
    app.include_router(risk_router)
    app.include_router(research_router)
    app.include_router(tasks_router)
    app.include_router(tradeplan_router)
    app.include_router(runtime_router)
    app.include_router(account_router)

    return app


def _history_root(settings: Settings) -> Path:
    path = Path(settings.history_data_path)
    return path if path.is_absolute() else PROJECT_ROOT / path
