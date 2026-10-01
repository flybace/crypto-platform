"""Environment-backed settings for the standalone backend."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float, *, allow_zero: bool = False) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise RuntimeError("history timeout and pause settings must be numbers") from error
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise RuntimeError("history timeout must be positive and history pause must be non-negative")
    return parsed


def _as_positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError("CRYPTO_HISTORY_MAX_PAGES must be an integer") from error
    if parsed <= 0:
        raise RuntimeError("CRYPTO_HISTORY_MAX_PAGES must be positive")
    return parsed


def _as_bounded_positive_int(value: str | None, default: int, *, maximum: int, name: str) -> int:
    parsed = _as_positive_int(value, default)
    if parsed > maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return parsed


def _as_bounded_days(value: str | None, default: int) -> int:
    parsed = _as_positive_int(value, default)
    if parsed > 3650:
        raise RuntimeError("CRYPTO_HISTORY_SYNC_LOOKBACK_DAYS must be between 1 and 3650")
    return parsed


def _as_origins(value: str | None) -> tuple[str, ...]:
    if not value:
        return ("http://localhost:4191", "http://127.0.0.1:4191")
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with fail-closed credential requirements."""

    app_name: str
    version: str
    admin_username: str
    admin_password: str
    session_secret: str
    token_ttl_seconds: int
    execution_mode: str
    allowed_origins: tuple[str, ...]
    dev_mode: bool
    history_data_path: str = "data/history"
    history_timeout_seconds: float = 10.0
    history_page_pause_seconds: float = 0.05
    history_max_pages: int = 2000
    history_sync_lookback_days: int = 365
    history_trust_env: bool = False
    binance_history_base_url: str = "https://data-api.binance.vision"
    okx_history_base_url: str = "https://www.okx.com"
    # Bybit applies geographic access policy at the CDN edge. Keep the
    # regional endpoint configurable and use the reachable public endpoint by
    # default for this development network.
    bybit_history_base_url: str = "https://api.bybit-tr.com"
    binance_public_rest_base_url: str = "https://data-api.binance.vision"
    okx_public_rest_base_url: str = "https://www.okx.com"
    bybit_public_rest_base_url: str = "https://api.bybit-tr.com"
    binance_public_ws_base_url: str = "wss://data-stream.binance.vision"
    okx_public_ws_base_url: str = "wss://ws.okx.com:8443/ws/v5/public"
    bybit_public_ws_base_url: str = "wss://stream.bybit.kz/v5/public/spot"
    binance_public_http_proxy: str = ""
    okx_public_http_proxy: str = ""
    bybit_public_http_proxy: str = ""
    binance_public_ws_proxy: str = ""
    okx_public_ws_proxy: str = ""
    bybit_public_ws_proxy: str = ""
    public_network_settings_path: str = ""
    public_trust_env: bool = False
    database_url: str = "sqlite:///:memory:"
    redis_url: str = ""
    persistence_required: bool = False
    task_queue_mode: str = "local"
    scheduler_interval_seconds: int = 900
    scheduler_backoff_initial_seconds: int = 300
    scheduler_backoff_max_seconds: int = 21600
    task_max_attempts: int = 3
    task_lease_seconds: int = 900
    task_max_runtime_seconds: int = 3600
    task_max_payload_bytes: int = 262_144
    task_max_result_bytes: int = 2_097_152
    task_max_items: int = 100
    task_recovery_grace_seconds: int = 60
    task_log_retention_days: int = 365
    task_log_backup_path: str = ""
    task_log_restore_path: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        dev_mode = _as_bool(os.getenv("CRYPTO_DEV_MODE"), default=False)
        password = os.getenv("CRYPTO_ADMIN_PASSWORD", "")
        secret = os.getenv("CRYPTO_SESSION_SECRET", "")
        if dev_mode:
            password = password or "local-dev-password"
            secret = secret or "local-dev-session-secret-change-me"
        if not password:
            raise RuntimeError(
                "CRYPTO_ADMIN_PASSWORD is required; set CRYPTO_DEV_MODE=true only for local development"
            )
        if len(secret) < 24:
            raise RuntimeError("CRYPTO_SESSION_SECRET must contain at least 24 characters")

        raw_ttl = os.getenv("CRYPTO_TOKEN_TTL_SECONDS", "28800")
        try:
            ttl = int(raw_ttl)
        except ValueError as error:
            raise RuntimeError("CRYPTO_TOKEN_TTL_SECONDS must be an integer") from error
        if ttl <= 0:
            raise RuntimeError("CRYPTO_TOKEN_TTL_SECONDS must be positive")

        history_data_path = os.getenv("CRYPTO_HISTORY_DATA_PATH", "data/history").strip()
        if not history_data_path:
            raise RuntimeError("CRYPTO_HISTORY_DATA_PATH must not be empty")

        task_queue_mode = os.getenv("CRYPTO_TASK_QUEUE_MODE", "local").strip().lower()
        if task_queue_mode not in {"local", "redis"}:
            raise RuntimeError("CRYPTO_TASK_QUEUE_MODE must be local or redis")

        scheduler_interval_seconds = _as_positive_int(
            os.getenv("CRYPTO_SCHEDULER_INTERVAL_SECONDS"),
            900,
        )
        scheduler_backoff_initial_seconds = _as_bounded_positive_int(
            os.getenv("CRYPTO_SCHEDULER_BACKOFF_INITIAL_SECONDS"),
            300,
            maximum=86400,
            name="CRYPTO_SCHEDULER_BACKOFF_INITIAL_SECONDS",
        )
        scheduler_backoff_max_seconds = _as_bounded_positive_int(
            os.getenv("CRYPTO_SCHEDULER_BACKOFF_MAX_SECONDS"),
            21600,
            maximum=604800,
            name="CRYPTO_SCHEDULER_BACKOFF_MAX_SECONDS",
        )
        if scheduler_backoff_max_seconds < scheduler_backoff_initial_seconds:
            raise RuntimeError(
                "CRYPTO_SCHEDULER_BACKOFF_MAX_SECONDS must be greater than or equal to "
                "CRYPTO_SCHEDULER_BACKOFF_INITIAL_SECONDS"
            )
        task_max_attempts = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_MAX_ATTEMPTS"),
            3,
            maximum=10,
            name="CRYPTO_TASK_MAX_ATTEMPTS",
        )
        task_lease_seconds = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_LEASE_SECONDS"),
            900,
            maximum=86400,
            name="CRYPTO_TASK_LEASE_SECONDS",
        )
        task_max_runtime_seconds = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_MAX_RUNTIME_SECONDS"),
            3600,
            maximum=604800,
            name="CRYPTO_TASK_MAX_RUNTIME_SECONDS",
        )
        task_max_payload_bytes = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_MAX_PAYLOAD_BYTES"),
            262_144,
            maximum=16 * 1024 * 1024,
            name="CRYPTO_TASK_MAX_PAYLOAD_BYTES",
        )
        task_max_result_bytes = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_MAX_RESULT_BYTES"),
            2_097_152,
            maximum=32 * 1024 * 1024,
            name="CRYPTO_TASK_MAX_RESULT_BYTES",
        )
        task_max_items = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_MAX_ITEMS"),
            100,
            maximum=1000,
            name="CRYPTO_TASK_MAX_ITEMS",
        )
        task_recovery_grace_seconds = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_RECOVERY_GRACE_SECONDS"),
            60,
            maximum=3600,
            name="CRYPTO_TASK_RECOVERY_GRACE_SECONDS",
        )
        task_log_retention_days = _as_bounded_positive_int(
            os.getenv("CRYPTO_TASK_LOG_RETENTION_DAYS"),
            365,
            maximum=3650,
            name="CRYPTO_TASK_LOG_RETENTION_DAYS",
        )
        task_log_backup_path = os.getenv("CRYPTO_TASK_LOG_BACKUP_PATH", "").strip()
        task_log_restore_path = os.getenv("CRYPTO_TASK_LOG_RESTORE_PATH", "").strip()
        return cls(
            app_name=os.getenv("CRYPTO_APP_NAME", "Crypto Multi-Market Quant Platform"),
            version=os.getenv("CRYPTO_APP_VERSION", "0.1.0"),
            admin_username=os.getenv("CRYPTO_ADMIN_USERNAME", "admin"),
            admin_password=password,
            session_secret=secret,
            token_ttl_seconds=ttl,
            execution_mode=os.getenv("CRYPTO_EXECUTION_MODE", "DISABLED"),
            allowed_origins=_as_origins(os.getenv("CRYPTO_ALLOWED_ORIGINS")),
            dev_mode=dev_mode,
            history_data_path=history_data_path,
            history_timeout_seconds=_as_float(os.getenv("CRYPTO_HISTORY_TIMEOUT_SECONDS"), 10.0),
            history_page_pause_seconds=_as_float(os.getenv("CRYPTO_HISTORY_PAGE_PAUSE_SECONDS"), 0.05, allow_zero=True),
            history_max_pages=_as_positive_int(os.getenv("CRYPTO_HISTORY_MAX_PAGES"), 2000),
            history_sync_lookback_days=_as_bounded_days(os.getenv("CRYPTO_HISTORY_SYNC_LOOKBACK_DAYS"), 365),
            history_trust_env=_as_bool(os.getenv("CRYPTO_HISTORY_TRUST_ENV"), default=False),
            binance_history_base_url=os.getenv("CRYPTO_BINANCE_HISTORY_BASE_URL", "https://data-api.binance.vision").strip(),
            okx_history_base_url=os.getenv("CRYPTO_OKX_HISTORY_BASE_URL", "https://www.okx.com").strip(),
            bybit_history_base_url=os.getenv("CRYPTO_BYBIT_HISTORY_BASE_URL", "https://api.bybit-tr.com").strip(),
            binance_public_rest_base_url=os.getenv(
                "CRYPTO_BINANCE_PUBLIC_REST_BASE_URL",
                "https://data-api.binance.vision",
            ).strip(),
            okx_public_rest_base_url=os.getenv(
                "CRYPTO_OKX_PUBLIC_REST_BASE_URL",
                "https://www.okx.com",
            ).strip(),
            bybit_public_rest_base_url=os.getenv(
                "CRYPTO_BYBIT_PUBLIC_REST_BASE_URL",
                os.getenv("CRYPTO_BYBIT_PUBLIC_BASE_URL", "https://api.bybit-tr.com"),
            ).strip(),
            binance_public_ws_base_url=os.getenv(
                "CRYPTO_BINANCE_PUBLIC_WS_BASE_URL",
                "wss://data-stream.binance.vision",
            ).strip(),
            okx_public_ws_base_url=os.getenv(
                "CRYPTO_OKX_PUBLIC_WS_BASE_URL",
                "wss://ws.okx.com:8443/ws/v5/public",
            ).strip(),
            bybit_public_ws_base_url=os.getenv(
                "CRYPTO_BYBIT_PUBLIC_WS_BASE_URL",
                os.getenv("CRYPTO_BYBIT_MARKET_WS_URL", "wss://stream.bybit.kz/v5/public/spot"),
            ).strip(),
            binance_public_http_proxy=os.getenv("CRYPTO_BINANCE_PUBLIC_HTTP_PROXY", "").strip(),
            okx_public_http_proxy=os.getenv("CRYPTO_OKX_PUBLIC_HTTP_PROXY", "").strip(),
            bybit_public_http_proxy=os.getenv("CRYPTO_BYBIT_PUBLIC_HTTP_PROXY", "").strip(),
            binance_public_ws_proxy=os.getenv("CRYPTO_BINANCE_PUBLIC_WS_PROXY", "").strip(),
            okx_public_ws_proxy=os.getenv("CRYPTO_OKX_PUBLIC_WS_PROXY", "").strip(),
            bybit_public_ws_proxy=os.getenv("CRYPTO_BYBIT_PUBLIC_WS_PROXY", "").strip(),
            public_network_settings_path=os.getenv("CRYPTO_PUBLIC_NETWORK_SETTINGS_PATH", "").strip(),
            public_trust_env=_as_bool(os.getenv("CRYPTO_PUBLIC_TRUST_ENV"), default=False),
            database_url=os.getenv(
                "CRYPTO_DATABASE_URL",
                "sqlite:///data/history/.runtime/control-plane.sqlite3",
            ).strip(),
            redis_url=os.getenv("CRYPTO_REDIS_URL", "").strip(),
            persistence_required=_as_bool(os.getenv("CRYPTO_PERSISTENCE_REQUIRED"), default=False),
            task_queue_mode=task_queue_mode,
            scheduler_interval_seconds=scheduler_interval_seconds,
            scheduler_backoff_initial_seconds=scheduler_backoff_initial_seconds,
            scheduler_backoff_max_seconds=scheduler_backoff_max_seconds,
            task_max_attempts=task_max_attempts,
            task_lease_seconds=task_lease_seconds,
            task_max_runtime_seconds=task_max_runtime_seconds,
            task_max_payload_bytes=task_max_payload_bytes,
            task_max_result_bytes=task_max_result_bytes,
            task_max_items=task_max_items,
            task_recovery_grace_seconds=task_recovery_grace_seconds,
            task_log_retention_days=task_log_retention_days,
            task_log_backup_path=task_log_backup_path,
            task_log_restore_path=task_log_restore_path,
        )
