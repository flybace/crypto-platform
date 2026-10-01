# Project Analysis and AI Handoff

Prepared on 2026-10-01 from this local checkout. This is a source handoff,
not a deployment or a fresh verification of the Ubuntu runtime.

## Start Here

Read `AGENTS.md`, this document, `STATUS_2026_09_28.md`, then
`PARITY_MATRIX.md` and the relevant section of `PROJECT_PLAN.md`.
`RUNTIME_DEPLOYMENT.md` contains deployment procedures and historical
acceptance. Paths and LAN addresses in those documents describe the
original operator's environment; they are not cloud development endpoints.

## Architecture

| Area | Implementation | Responsibility |
| --- | --- | --- |
| Workbench | Vue 3 / TypeScript / Vite / Pinia | Market views, research, history, tasks, settings |
| API | FastAPI, `backend/app/main.py` | Authentication, service composition, API routes |
| Core | `src/domain`, `src/application`, `src/ports` | Market contracts, replay, research, paper execution |
| Adapters | `src/adapters` | Binance/OKX/Bybit public APIs, standalone storage, GACE scaffolding |
| Control plane | `backend/app/services/task_*` | SQL ledger/outbox, Redis queue, leases, quotas, archives |
| Processes | `src/services` | Market worker, task worker, history scheduler |
| Persistence | PostgreSQL, Redis, local archive files | Control state, dispatch, history and result/event archives |
| Optional proxy | `proxy-stack` | Independent Mihomo routing and rule management |

The API, worker and scheduler share persistent task/domain state. Public
market workers reconstruct and validate books before writing a latest
state bridge. The API reads that bridge; optional segmented L2 archives
are a distinct storage path. History downloads validate candles and keep
manifests/raw-response archives, with optional Parquet mirrors.

## Implemented Capabilities and Limits

- Public spot REST/WebSocket adapters for Binance, OKX and Bybit; ticker
  aggregation, book reconstruction, freshness/sequence checks, configurable
  endpoints/proxies and coin details with cross-market trends/candles.
- History download jobs, quality/coverage checks, incremental scheduling
  and network-failure backoff.
- Candle/portfolio backtests, screening, coin pools, research, strategy
  matrices/incubation and paper replay. These are research workflows;
  the presence of a screen is not evidence of a production trading system.
- Task lifecycle, cancellation/retry, bounded quotas, dead letters,
  SQL dispatch outbox, consistency repair, worker shutdown/recovery,
  content-addressed result/event archives and log lifecycle tools.
- L2 archive segmentation, retention, tail recovery, bounded reads and
  governance status have source/tests. Runtime enablement is a separate gate.
- Authentication is a single configured development user with signed
  bearer tokens. Production users, roles, revocation and tenant isolation
  remain incomplete.
- Real private exchange/account/order execution and GACE Runtime writes
  remain unavailable or disabled. Execution mode must stay `DISABLED`.

## Prioritized Development

1. Complete SQL/Redis dispatch consistency and fault recovery. Start with
   `task_store.py`, `task_queue.py`, `task_dispatch_consistency.py`,
   `task_dispatcher.py` and `src/services/task_worker.py`. Existing outbox
   and repair logic do not establish cross-system atomicity. Extend the
   isolated dispatch fault matrix/recovery tests with crash windows and
   stable identifiers; prove unique completion and archive retrieval.
2. Complete isolated dead-letter replay, result/event backup and restore,
   retention and disaster recovery exercises. Log lifecycle source exists;
   inspect its tests before treating the capability as missing. Production
   DLQ replay and complete disaster recovery still need separate acceptance.
3. Validate L2 archival with bounded capacity/retention and restart recovery
   in an isolated runtime. Add monitoring for disk growth, stale feeds and
   cleanup. Runtime enablement needs operator authorization.
4. Improve history REST resiliency and observability. Historical notes
   record Bybit REST failures despite working WebSockets; exercise failures
   and partial progress with fake endpoints before live verification.
5. Add production session/permission management and strategy import sandbox
   before expanding to private exchange capabilities.

`backend/app/services/task_store.py` is already above the 2400-line split
warning. Preserve existing contracts when changing it. The large plan was
split only at its final two status sections to meet the 3000-line limit.

## Reproducible Development

Use Python 3.12 and Node.js 22. Python runtime dependencies include numpy,
pandas and pyarrow, so Python 3.14 is not the recommended baseline.

```sh
python -m venv .venv
# Activate the environment using the command appropriate for your shell.
python -m pip install -e '.[runtime,test]'
python -m pytest -q
```

Start the API in local mode with disposable credentials:

```sh
CRYPTO_DEV_MODE=true CRYPTO_ADMIN_PASSWORD=local-dev-password \
CRYPTO_SESSION_SECRET=local-dev-session-secret-change-me \
CRYPTO_EXECUTION_MODE=DISABLED CRYPTO_TASK_QUEUE_MODE=local \
python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8290
```

`Settings.from_env()` reads process environment. It does not automatically
load `backend/.env.example`. PowerShell equivalents are in `README.md`.

In another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:4191/login` with username `admin` and the disposable
password above. A fresh clone has no captured history, SQL ledger or market
state. Use test fixtures or deliberately download public data; do not expect
the original runtime's dataset counts in a clean checkout.

Compose needs configured credentials and a reachable bind address. For local
development explicitly set `CRYPTO_BIND_ADDRESS=127.0.0.1`; the existing
Compose defaults describe the original LAN deployment. Cloud AI tasks should
use fixtures and local services rather than the original deployment script.

## Verification on 2026-10-01

| Check | Current local evidence |
| --- | --- |
| Main Python suite, Windows Python 3.14.3 | 333 passed, 1 skipped; optional pyarrow unavailable |
| Independent proxy suite | 11 passed |
| Python compileall, backend/src/tests | Passed |
| Frontend type check and production build | Passed |
| Ubuntu and authenticated product browser acceptance | Not rerun for this upload |

GitHub CI uses Python 3.12 with runtime/test dependencies, Node.js 22,
the proxy suite and PowerShell document gates. Its result must be checked
after GitHub executes it; local success does not prove hosted CI success.

## Upload Contents

Source, tests, contracts, documentation, Docker/Compose definitions and the
frontend lockfile belong in the private repository. Runtime datasets,
`.env` files, credentials, logs, screenshots, browser-command captures,
dependencies and generated build files are excluded and preserved locally.
Archived screenshot references in historical documents remain references;
those local artifacts are intentionally not part of the source handoff.
