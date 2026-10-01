from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_compose_defaults_to_ubuntu_runtime_address() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"${CRYPTO_BIND_ADDRESS:-10.10.10.129}:${CRYPTO_BACKEND_PORT:-8290}:8000"' in compose
    assert '"${CRYPTO_BIND_ADDRESS:-10.10.10.129}:${CRYPTO_FRONTEND_PORT:-4191}:8080"' in compose


def test_scheduler_and_worker_are_default_services() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "  task-worker:" in compose
    assert "  task-scheduler:" in compose
    assert '    profiles:\n      - tasks' not in compose


def test_backend_uses_redis_queue_in_runtime_by_default() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    backend = compose.split("\n  frontend:", 1)[0]

    assert "CRYPTO_TASK_QUEUE_MODE: ${CRYPTO_TASK_QUEUE_MODE:-redis}" in backend


def test_frontend_image_can_be_overlaid_without_reinstalling_dependencies() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "image: ${CRYPTO_FRONTEND_IMAGE:-crypto-platform/frontend:0.1.0}" in compose


def test_compose_exposes_per_venue_public_proxy_controls_without_enabling_them() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "CRYPTO_PUBLIC_TRUST_ENV: ${CRYPTO_PUBLIC_TRUST_ENV:-false}" in compose
    assert "CRYPTO_OKX_PUBLIC_HTTP_PROXY: ${CRYPTO_OKX_PUBLIC_HTTP_PROXY:-}" in compose
    assert "CRYPTO_OKX_PUBLIC_WS_PROXY: ${CRYPTO_OKX_PUBLIC_WS_PROXY:-}" in compose
    assert "CRYPTO_MARKET_WORKER_ENABLED: ${CRYPTO_MARKET_WORKER_ENABLED:-false}" in compose


def test_backend_archive_query_is_disabled_by_default_and_reads_worker_archive_path() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    backend = compose.split("\n  frontend:", 1)[0]

    assert "CRYPTO_MARKET_ARCHIVE_ENABLED: ${CRYPTO_MARKET_ARCHIVE_ENABLED:-false}" in backend
    assert "CRYPTO_MARKET_ARCHIVE_PATH: ${CRYPTO_MARKET_ARCHIVE_PATH:-/runtime/market/archive}" in backend


def test_compose_exposes_editable_public_market_endpoints() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    for venue in ("BINANCE", "OKX", "BYBIT"):
        assert f"CRYPTO_{venue}_PUBLIC_REST_BASE_URL" in compose
        assert f"CRYPTO_{venue}_PUBLIC_WS_BASE_URL" in compose
    assert "CRYPTO_OKX_HISTORY_BASE_URL" in compose


def test_market_worker_healthcheck_requires_fresh_allowlisted_books() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    market_worker = compose.split("\n  market-worker:", 1)[1].split("\n  task-worker:", 1)[0]

    assert "healthcheck:" in market_worker
    assert "CRYPTO_MARKET_INSTRUMENTS" in market_worker
    assert "status-{venue.lower()}.json" in market_worker
    assert "age > 15" in market_worker
    assert "snapshot.get(\"bids\")" in market_worker
    assert "snapshot.get(\"asks\")" in market_worker
