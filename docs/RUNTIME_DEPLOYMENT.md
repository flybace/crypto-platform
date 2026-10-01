# Standalone Runtime Deployment

## Boundary

- Source and development checkout: Windows, `D:\code\crypto-platform`.
- Runtime target: Ubuntu 24.04.4 LTS at `10.10.10.129`.
- Runtime user: `flybace`.
- Deployment unit: this repository's independent `crypto-platform` Compose project.
- The runtime does not use the A-share system's PostgreSQL, Redis, volumes, network, or secrets.

The password supplied for the initial connection is an out-of-band secret. It is not stored in this file, the project plan, source code, shell history, logs, images, or Git. The follow-up hardening step is SSH key authentication or a Secret Provider.

The deployment helper accepts an explicit SSH key with `--key-file` (or
`CRYPTO_REMOTE_KEY`). This is preferred when the target already trusts a key;
the password fallback remains environment-backed through
`CRYPTO_REMOTE_PASSWORD` and is never a command-line argument.

## Checkout and Runtime Status (2026-09-28)

The current source and its latest deployment were independently postflight
checked on Ubuntu. Local validation is `331 passed, 1 skipped`; Python
compilation, the frontend production build and the 3000-line gate passed. The
only skipped test is the optional `pyarrow` archive test on Windows Python
3.14.

The runtime has seven Crypto containers. The frontend and backend bind to
`10.10.10.129:4191/8290`, execution mode is `DISABLED`, and PostgreSQL, Redis,
the history volume and task ledger were preserved. History contains 27 datasets
and `1,125,676` candles with zero gaps and duplicates and `27/27` Parquet
mirrors; aligned historical spread data contains `108,945` candles.

The Scheduler remains on its automatic 900-second cadence and its persisted
state is currently `backoff`. The latest job completed `25/27`; Bybit BTC/USDT
`1d` and `1h` tasks remain blocked by `NETWORK_ERROR / public REST request
failed`. The scheduler did not mark those tasks as successful.

The authenticated browser postflight verified Binance, OKX and Bybit live
BTC/USDT quotes, cross-venue trend data and single-venue candles. Ticker,
overview, snapshot, compare and candles requests returned HTTP `200`; at
`390x844`, document and body widths were both `375` with no horizontal
overflow. `CRYPTO_MARKET_ARCHIVE_ENABLED` remains disabled, so the live market
path is still a latest-state bridge rather than durable L2 archival.

The task-result endpoint returned `task-result-v1` with archive state `READY`;
completion events retain only an archive reference and summary. Graceful task
shutdown/recovery was accepted on Ubuntu. Redis/SQL atomic dual-write, real
dead-letter replay, private exchange APIs, real orders, automatic selling,
withdrawals and GACE write capability remain disabled or incomplete. Do not
infer a successful Bybit historical REST sync from the healthy public WebSocket
streams.

## Previously Verified Runtime Profile (2026-09-18)

The source tree defines `backend`, `frontend`, `task-worker` and
`task-scheduler` as the normal Ubuntu runtime built from Windows. At the dated
2026-09-18 postflight, the then-current source change had been redeployed and
independently rechecked:

- FastAPI health endpoint;
- market status view;
- execution mode `DISABLED`;
- public history Worker may call configured public REST endpoints, but no private exchange client is started;
- no API key, account, order, withdrawal, transfer, or live-trading capability.

The remote project directory currently contains 27 verified datasets. The
latest authenticated postflight verified 1,095,935 candles with zero gaps or
duplicates and 27/27 Parquet mirrors. The Windows source snapshot is 18
datasets and 747,865 candles. This proves the mounted history is readable and
verified; it does not prove that Ubuntu can reach every exchange endpoint or
download new data.

The public market collector is a separate Compose profile and requires an
explicit public-instrument allowlist. It has no private credentials. The
current Ubuntu runtime enables the profile for
`binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`. Binance, OKX and Bybit public
WebSocket streams are reachable from the target host through their configured
direct or proxy routes. Accepted normalized books and venue status are written
by the Worker to the `runtime/market` state directory; the backend mounts the
same directory read-only. This is a development latest-state bridge, not
historical storage or a PostgreSQL/Redis replacement. Older Bybit ETH/BNB
snapshot files remain in the recoverable
`runtime/market-disabled-snapshots-20260917` directory and are not active
market state.

The repository now defines independent PostgreSQL 16 and Redis 7 services for
the Crypto runtime. The backend records the task ledger, history metadata and
bounded domain snapshots in the configured `CRYPTO_DATABASE_URL`. Domain
snapshots use the `control_domain_snapshots` table as the authoritative store;
an absent row is bootstrapped from the matching legacy JSON file, and a
successful SQL commit writes a best-effort JSON recovery export. The JSON file
is not read when a SQL row already exists. File-oriented history jobs and the
Scheduler state retain their own recovery files until their separate migration
is completed. `CRYPTO_TASK_QUEUE_MODE=local` keeps development execution in the
API process. The Ubuntu runtime starts the task worker and scheduler by default:
the task worker claims history, backtest, pool-backtest, screening, research,
paper-replay and strategy-matrix messages from Redis, updates the same
PostgreSQL task ledger, and keeps verified history CSV/Manifest files as the
domain data source. Each supported task handler persists lifecycle events and
its domain result. Completed task results are written to the shared
`data/history/.runtime/task-results` archive as `task-result-v1` content-addressed
objects; the PostgreSQL task row and completion event keep only bounded archive
metadata and summaries, and the authenticated result endpoint verifies and reads
the object across processes. Local mode remains a synchronous development
fallback. The scheduler immediately plans an incremental history
sync on startup, repeats it at the configured interval, skips active duplicate
work, and records exponential backoff after a blocked or failed run.

Compose publishes the backend and frontend using
`CRYPTO_BIND_ADDRESS` (default `10.10.10.129`, the Ubuntu runtime address). If
Compose is used for local development, explicitly set
`CRYPTO_BIND_ADDRESS=127.0.0.1`. The Ubuntu `.env` keeps
`CRYPTO_BIND_ADDRESS=10.10.10.129`, so the frontend is available on
`10.10.10.129:4191` and the backend on `10.10.10.129:8290` for controlled FRP
forwarding. Do not publish this development surface directly to the Internet;
restrict the host firewall and FRP ingress to trusted users.

## Current Read-only Postflight (2026-09-17)

This validation first used the existing trusted SSH key for a read-only
inventory, then ran one `--skip-history` source rebuild/deploy and the local
`scripts/remote_deploy.py postflight` command. The deploy preserved existing
CSV/Manifest history, Redis, PostgreSQL/Redis volumes and the separate A-share
Compose project. The separate Bybit ETH/BNB latest-state files had already
been moved with an explicit, recoverable `mv` into
`runtime/market-disabled-snapshots-20260917`; no file was deleted.

Independent remote checks confirmed:

- seven Crypto containers running with restart count `0` and
  `unless-stopped` policy; backend and market-worker were healthy;
- backend/frontend published on `10.10.10.129:8290` and
  `10.10.10.129:4191`; the separate A-share Compose project still reported
  14 running services;
- authenticated backend health and login returned HTTP `200`, with
  `execution_mode=DISABLED`;
- 27 history datasets, 1,095,935 candles, zero gaps/duplicates, 27/27 Parquet
  mirrors, 105,882 aligned historical spread rows, and an automatic
  900-second Scheduler currently active;
- Redis queue, processing and claim-marker counts were `0/0/0`; one old
  malformed `history_download` task remains in the dead-letter list with
  `history task has no serialized queries`, and no replay audit exists. The
  result postflight reported task persistence `ready`;
- PostgreSQL showed no pending dispatch-outbox rows, with all checked task
  event archive rows in `READY`; the Worker and containers had no
  restart or schema-race evidence in this postflight;
- the public market allowlist was exactly
  `binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`; all three normalized snapshots
  were `CONNECTED` with fresh bid/ask levels and positive sequences, and OKX
  sequence values continued to increase during the observation window;

The real browser opened `http://10.10.10.129:4191/login` and rendered the
authenticated market center. Binance, OKX and Bybit showed live read-only L2
best bid/ask values and sequence numbers. Login, market overview, market
status, snapshot, history and instrument requests observed in the session
returned HTTP `200`; the browser console had zero errors and zero warnings. At
a `390x844` viewport, `documentWidth=375` and `bodyWidth=375`, so horizontal
overflow was false. The screenshot is
`.playwright-cli/page-2026-09-17T09-00-47-948Z.png`.

The `task-result-v1` archive, bounded completion-event summaries, audited
dead-letter replay route and current-task graceful drain remain within the
previously accepted read-only runtime boundary. An authenticated result probe
returned HTTP `200`, `completed`, contract `task-result-v1` and archive state
`READY`; the task-log endpoint also returned HTTP `200` with the target task's
13 verified events. A non-empty `pool-backtest` task survived Worker stop,
restart and SQL outbox recovery exactly once with `attempt=1`; its result
archive is `READY` and 188285 bytes, while the SQL task row and completion
event retain only the archive reference and bounded summary. This postflight
did not delete or replay the existing dead-letter task. Redis/SQL atomic
dual-write, production-grade shutdown recovery, complete log archival,
object-store lifecycle, tenant isolation, private accounts, orders,
withdrawals and GACE write capability remain unfinished or blocked.

The SQL `control_tasks` table contained 55 rows in the latest postflight,
while the authenticated task API and browser task center displayed a bounded
merged task projection from the SQL ledger and the legacy/history task
projection (109 items in the current read). These are different projections
and must not be reported interchangeably. The browser
opened `http://10.10.10.129:4191/login` in an authenticated session; the target
task detail showed 13 lifecycle events including `requeued_after_shutdown` and
the final `completed` event. Dynamic requests returned HTTP `200`, console
errors and warnings were both zero, and the `390x844` viewport measured
`documentWidth=375` and `bodyWidth=375`. Current screenshots are
`output/playwright/acceptance-live/task-center-postflight-desktop-20260917.png`
and `output/playwright/acceptance-live/task-center-postflight-390-20260917.png`.

## Deployment Commands

Run these commands on Ubuntu from the repository root:

```bash
mkdir -p runtime/market
docker compose -p crypto-platform config
docker compose -p crypto-platform build backend frontend task-worker task-scheduler
docker run --rm --user 0:0 --read-only \
  -v "$PWD/runtime/market:/runtime/market" \
  crypto-platform/backend:0.1.0 chown 65532:65532 /runtime/market
docker compose -p crypto-platform up -d backend frontend task-worker task-scheduler
docker compose -p crypto-platform ps
python3 -c 'import urllib.request; print(urllib.request.urlopen("http://10.10.10.129:8290/health").read().decode())'
```

The history path is part of the normal runtime and does not require a manual
download command or a separate profile:

```bash
docker compose -p crypto-platform logs --tail=100 task-worker task-scheduler
docker compose -p crypto-platform exec -T backend \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())'
```

The task control plane now also uses bounded defaults for retries and resource
budgets. The protected Ubuntu `.env` may override these values when there is a
measured need, but it must keep them finite:

```text
CRYPTO_TASK_MAX_ATTEMPTS=3
CRYPTO_TASK_LEASE_SECONDS=900
CRYPTO_TASK_MAX_RUNTIME_SECONDS=3600
CRYPTO_TASK_MAX_PAYLOAD_BYTES=262144
CRYPTO_TASK_MAX_RESULT_BYTES=2097152
CRYPTO_TASK_MAX_ITEMS=100
```

Worker failures are retried only up to `CRYPTO_TASK_MAX_ATTEMPTS`; a terminal
failure is recorded in PostgreSQL and moved to the Redis dead-letter list.
Lease recovery changes the SQL projection back to `queued` before the message
is processed again. Resource-limit failures are `blocked` and are not
automatically retried. Inspect the authenticated
`/api/v1/tasks/dead-letters` endpoint before recovery. Dead-letter tasks must
use `POST /api/v1/tasks/dead-letters/{task_id}/replay` with a bounded `reason`
and optional idempotent `request_id`; the SQL replay table and source-task
events record actor, reason, source/target IDs, attempt budget and bounded
outcome summaries. The ordinary `/{task_id}/retry` route rejects
`dead_lettered` tasks. Deployment must preserve the Redis AOF and PostgreSQL
volume; do not flush Redis or recreate the data volumes merely to apply these
code changes.

The default task services were independently verified on Ubuntu on 2026-09-16
after deploying the domain-snapshot source change. PostgreSQL/Redis health,
Worker/Scheduler liveness, the task ledger, and the ten-row PostgreSQL snapshot
table (eight domain snapshots plus two runtime snapshots) were confirmed. The
later result-archive postflight also verified cross-process `task-result-v1`
recovery and current-task graceful drain. Local regression coverage checks
supported task routing, lease acknowledgement, cross-process SQL domain
snapshot reads and the legacy JSON migration bridge. This does not enable
account access or trading.
Worker current-task graceful drain is verified by the worker regression suite and
the Compose signal/stop-grace configuration. The bounded non-empty-task stop,
restart and outbox recovery path is now also accepted on Ubuntu: the PostgreSQL
task row returned to `queued`, the Redis claim was recovered, the task completed
once after restart, and the result/event archives remained consistent.
Dead-letter replay auditing is now also deployed
and independently checked on Ubuntu: the PostgreSQL
`control_task_dead_letter_replays` table exists, the authenticated dead-letter
list returns HTTP 200, and the replay route returns the expected 404 for an
unknown task. The live DLQ was empty, so no real replay mutation or successful
replay event was fabricated during this postflight. Tenant-level budgets,
Redis/SQL atomic dual-write, production-grade shutdown recovery and complete
log archival remain future work. The task result archive is runtime-accepted
for the bounded offline task path, but is not yet a production object-storage
or disaster-recovery system.

## Latest Dead-letter Replay Audit Deployment and Postflight (2026-09-16)

The current source was deployed once with the existing SSH Key and
`--skip-history`, after a read-only inventory and authenticated postflight.
The deployment rebuilt only the Crypto application images and restarted
backend, frontend, task-worker and task-scheduler. PostgreSQL and Redis stayed
running with their existing volumes; the independent A-share Compose project
continued to report 14 running services.

Independent SSH and HTTP postflight confirmed:

- six Crypto services running, with backend/frontend published on
  `10.10.10.129:8290` and `10.10.10.129:4191`;
- backend health HTTP 200, authenticated login HTTP 200 and
  `execution_mode=DISABLED`;
- 18 verified history datasets, 749,467 candles, zero gaps/duplicates and
  18/18 Parquet mirrors; the 900-second automatic Scheduler remained visible;
- PostgreSQL table `control_task_dead_letter_replays` present, with the task
  ledger and history retained;
- Redis queue, processing list and DLQ all empty after deployment, with no
  complete `payload` field present in DLQ metadata;
- authenticated `GET /api/v1/tasks/dead-letters` HTTP 200, unknown-task replay
  route HTTP 404, and authenticated result endpoint HTTP 200 with a
  `task-result-v1` `READY` archive reference.

The real browser opened `http://10.10.10.129:4191/login`, submitted the
authenticated form, and rendered both the runtime-plan and task-center views.
The runtime plan showed `18/27`, `749,467`, `SAFE_PAUSED`, `DISABLED` and 26
read-only capabilities; the task center showed 43 tasks and task details
included the completion event and archived result. Browser dynamic requests
returned HTTP 200 and console errors/warnings were both zero. At `390x844`,
both runtime-plan and task-center views measured `documentWidth=375` with no
horizontal overflow.

This accepts the dead-letter audit route, result archive route, current task
projection and preserved-data runtime surface. It does not claim a live
dead-letter replay because the target DLQ had no sample task. It does not
accept Redis/SQL atomic dual-write, tenant isolation, production shutdown
recovery, complete log archival, object-store lifecycle, real accounts,
orders, withdrawals or GACE write capability.

## Latest Bybit Public L2 Postflight (2026-09-16)

The current source was deployed once with the existing SSH Key and
`--skip-history`; the deployment did not upload history or clear Redis and
PostgreSQL data. Independent SSH checks confirmed seven Crypto containers
running: backend, frontend, the Bybit `market-worker`, task-worker,
task-scheduler, PostgreSQL and Redis. Backend was healthy, all seven restart
counts were zero, and backend/frontend remained published on
`10.10.10.129:8290` and `10.10.10.129:4191`. The separate A-share
`quant-platform` Compose project remained at 14 running services.

The market profile was explicitly configured with
`CRYPTO_MARKET_WORKER_ENABLED=true`,
`CRYPTO_MARKET_INSTRUMENTS=bybit:BTCUSDT,bybit:ETHUSDT,bybit:BNBUSDT` and
`wss://stream.bybit.kz/v5/public/spot`. The Worker fetched all three initial
REST books with HTTP 200 and wrote normalized state to `runtime/market`; the
backend read the same files through its read-only mount. A direct container
read found three snapshots, each with 50 bid levels and 50 ask levels. BNB
and ETH sequences increased during a six-second observation window, and no
Worker restart occurred. Redis AOF was enabled; queue, processing and DLQ
lengths were all zero. PostgreSQL retained the task ledger, ten domain/runtime
snapshot rows and the dead-letter replay table.

The real browser opened `http://10.10.10.129:4191/login`, logged in and
rendered the current market center. It displayed Bybit BTC/ETH/BNB public
real-time L2 cards with best bid/ask and sequence, while Binance and OKX
remained visibly without live public data. Market refresh, status, snapshot,
history and instrument requests returned HTTP 200. At `390x844`, the page
measured `documentWidth=375` and `bodyWidth=375`; console errors and warnings
were both zero. Fresh screenshots are
`output/playwright/market-live-postflight-390-20260916.png`,
`output/playwright/market-live-postflight-desktop-20260916.png` and
`output/playwright/market-live-section-desktop-20260916.png`.

This is bounded runtime acceptance for the Bybit public L2 path only. The
source default remains disabled; Binance/OKX live streams, cross-venue live
normalization, long-term market archive, PostgreSQL/Redis market storage,
production reconnect/recovery drills, private accounts and trading remain
unfinished or blocked. The latest-state JSON bridge is not an executable
quote, an account permission, or an order gateway.

## Previous Task Result Archive Postflight (2026-09-16)

Before deployment, the project inventory confirmed the existing six Crypto
services, 18 history CSV/Manifest pairs, A-share `quant-platform` services and
the existing data volumes. The current source was then deployed once with
`--skip-history`; PostgreSQL, Redis, history files and the task ledger were
preserved. Independent postflight confirmed the six Crypto services running,
`10.10.10.129:8290/4191` binding, non-root read-only application containers,
`CRYPTO_TASK_QUEUE_MODE=redis`, `DISABLED` execution mode, Redis `PONG` and
empty queued/processing/DLQ lists. History remained 18 datasets and 749,467
candles with zero gaps or duplicates and 18/18 Parquet mirrors; the separate
A-share Compose project remained at 14 running services.

An authenticated real browser session submitted one Binance BNB/USDT 1d
offline backtest. The submission returned HTTP `202`; the Redis Worker
completed it on attempt 1. PostgreSQL `control_tasks.result_json` contained
only the `task-result-v1` archive reference, and the completion event contained
only the status summary and reference. The running backend verified the shared
archive object at 25,290 bytes with the SHA-256 referenced by SQL. The
authenticated `GET /api/v1/tasks/{task_id}/result` returned HTTP `200`, status
`completed`, archive state `READY`, and the full result. The browser console
had zero errors and zero warnings, and the 390x844 page measured 375px for
both document and body width. The Worker was then stopped and restarted in an
empty-queue state; its logs recorded SIGTERM, draining, and graceful shutdown,
and the post-restart queue remained empty with a 30-second stop timeout.

This accepts the bounded offline task result archive and current-task drain
path on Ubuntu. That earlier postflight did not include the later dead-letter
replay audit source change. It does not accept Redis/SQL atomic dual-write,
tenant isolation, production shutdown recovery, object-store lifecycle, real
accounts, orders, withdrawals, or GACE write capability.

The backend deployment requires `CRYPTO_ADMIN_PASSWORD` and
`CRYPTO_SESSION_SECRET` from an out-of-band Secret Provider or protected host
environment. Do not put those values in this file or in a command copied into
shell history. The S4-S5 login, protected-route and logout checks were run
against the direct Ubuntu address after the current deployment; repeat them
after every runtime image or authentication change.

After the target host has a verified public egress path, the collector can be
started explicitly with public symbols only. The current runtime has passed
this gate for the two-symbol Binance/Bybit public allowlist:

```bash
CRYPTO_MARKET_WORKER_ENABLED=true \
CRYPTO_MARKET_INSTRUMENTS=binance:BTCUSDT,bybit:BTCUSDT \
CRYPTO_BYBIT_MARKET_WS_URL=wss://stream.bybit.kz/v5/public/spot \
docker compose -p crypto-platform --profile market up -d market-worker
docker compose -p crypto-platform --profile market logs --tail=100 market-worker
```

Do not add Binance or OKX symbols while their DNS, routing, or exchange
terms/region checks remain unresolved. Starting this public-only profile does
not enable account access or trading.

If `curl` is installed, equivalent `curl --fail` requests are also valid. The target host used for the first postflight did not have `curl`, so Python `urllib` is the documented baseline.

The first runtime acceptance only proves the container starts and the read-only endpoints respond. It does not prove real market connectivity, historical ingestion, production persistence, account access, or trading safety.

When the public network gate is passed, an authenticated client can check
`/api/v1/market/snapshot` after the Worker has written a state file. The
endpoint exposes normalized timestamps, sequence, and price levels only; it
does not expose raw exchange payloads or credentials.

## Previous Domain Snapshot Postflight (2026-09-16)

After the domain-snapshot source change was deployed with the existing SSH Key,
the independent postflight confirmed six Crypto Compose services running:
backend, frontend, task-worker, task-scheduler, PostgreSQL and Redis. The
backend/frontend remained published on `10.10.10.129:8290`/`10.10.10.129:4191`,
the backend was healthy, frontend HTTP returned 200, login returned 200, and
`execution_mode=DISABLED`. The separate A-share `quant-platform` Compose
project still had 14 running services. Deployment used `--skip-history`, so
the existing CSV/Manifest history was not overwritten.

The authenticated history projection reported 18 datasets and 748,867 candles,
with zero gaps and duplicates, `18/18 READY` Parquet mirrors, 105,325 aligned
historical spread rows at 30 bps cost, a 900-second Scheduler interval and 40
task-ledger records. The Scheduler was in persisted `backoff` because existing
OKX network-blocked tasks remain retryable; Binance and Bybit verified history
remained available and no placeholder OKX data was created.

The direct PostgreSQL read reported `control_domain_snapshots` as healthy with
ten rows: the eight existing `crypto.domain.*` rows plus
`crypto.runtime.history-scheduler` at version 3 and
`crypto.runtime.history-sync` at version 1. A separate task-worker process read
the same `crypto.domain.strategies` row at version 1. Authenticated
system/runtime reads reported domain snapshots `ready`, runtime
`safe_paused`, execution `DISABLED`, GACE read-only mode and zero write
capabilities.

The real browser session opened `http://10.10.10.129:4191/`, logged in and
rendered the runtime-plan page. It displayed `18/27` datasets, `748,867`
candles, `safe_paused`, `DISABLED`, and 26 read-only capabilities. At a
390x844 viewport, `documentElement.scrollWidth=375` and
`body.scrollWidth=375`, so horizontal overflow was false. Browser console
errors and warnings were both zero. The current acceptance screenshots are
`output/playwright/runtime-state-20260916-mobile.png` and
`output/playwright/runtime-state-20260916-desktop.png`. This accepts the current read-only
research/history runtime and domain-snapshot migration only; it does not
accept live market collection, account access, real orders, automated selling,
withdrawals, or GACE Runtime.

## Previous Verified Postflight (2026-09-15 14:29 UTC)

On 2026-09-15, the current source was rebuilt and deployed with the SSH key
to the Ubuntu runtime. Independent postflight confirmed six Crypto Compose
services running: backend, frontend, task-worker, task-scheduler, PostgreSQL
and Redis. Backend/frontend remained published on
`10.10.10.129:8290`/`10.10.10.129:4191`; backend was healthy, frontend HTTP
returned 200, login returned 200, and `execution_mode=DISABLED`. The separate
A-share `quant-platform` Compose project still had 14 running services.

The authenticated history projection reported 18 datasets and 748,627 candles,
with zero gaps and duplicates, `18/18 READY` Parquet mirrors, 105,288 aligned
historical spread rows at 30 bps cost, and 39 task-ledger records. The
Scheduler interval was 900 seconds and its current state was persisted
backoff because existing OKX network-blocked tasks remain retryable. Binance
and Bybit verified history remained available; no placeholder OKX data was
created.

The browser acceptance opened `http://10.10.10.129:4191/login`, logged in, and
rendered the current history page. A controlled one-item Binance BTC/USDT 1d
public history task returned 202 and completed. The authenticated
`/api/v1/history/raw-responses` request returned 200 with
`contract_version=history-raw-response-v1`, `status=READY`, one response and
zero invalid records. The unauthenticated request returned 401. A read-only
container inspection confirmed the archive contract and zero sensitive-field
matches. Browser console errors and warnings were both zero. This accepts the
current read-only research/history runtime and M2 raw-response slice only; it
does not accept live market collection, account access, real orders, automated
selling, withdrawals, or GACE Runtime.

## Previous Verified Postflight (2026-09-15 09:36 UTC refresh)

On 2026-09-15, an independent postflight against the current remote Compose
project reported:

- `crypto-platform-backend-1` was running and healthy, and
  `crypto-platform-frontend-1`, `crypto-platform-task-worker-1` and
  `crypto-platform-task-scheduler-1` were running;
- the host published backend/frontend on `10.10.10.129:8290` and
  `10.10.10.129:4191`;
- backend health returned HTTP 200 with `execution_mode=DISABLED`;
- authenticated login returned HTTP 200;
- the current deployment's authenticated history coverage returned 18 datasets
  and 748,273 rows, with `gap_count=0` and
  `duplicate_count=0` for every dataset;
- the remote directory contained 18 history CSV files and 18 Manifest files;
- Parquet status was `18/18 READY`, PostgreSQL and Redis were ready, and the
  unified task ledger contained 33 records, and the authenticated scheduler
  status reported `active` with a 900-second interval;
- the frontend root returned HTTP 200;
- the separate A-share `quant-platform` Compose project still had 14 running
  services;
- a read-only public probe from Ubuntu returned HTTP 200 for Binance and Bybit;
  the OKX `www.okx.com` candle endpoint timed out, so no OKX placeholder or
  synthetic history was generated;
- the public market Worker remained stopped by default.

The direct browser acceptance opened `http://10.10.10.129:4191/login`, logged
in, and rendered the history page. The page displayed 18/27 verified datasets,
748,273 candles, `18/18` archive readiness, and automatic synchronization as
active at 15 minutes. Browser requests for login, coverage, jobs, sync plan,
archive and scheduler status returned HTTP 200; the browser console reported
zero errors and zero warnings. This is `runtime-accepted` for the read-only,
research and synchronized-history surface only.

It does not cover Ubuntu exchange egress, live market collection, new history
downloads, production persistence, account access, real orders, or GACE
Runtime.

## Previous Verified Postflight (Old API)

On 2026-09-14, the target host completed the latest independent postflight for
the previous `api`-only read-only profile:

- Compose configuration validation passed;
- image `crypto-platform/api:0.1.0` was built and started;
- image ID was `sha256:3283bb28ba967fd060f2c26431bd355c07dc6183da277dfa9a4b925ed35337e1`;
- container state was `running`, health was `healthy`, restart count was `0`;
- container user was `65532:65532` and the root filesystem was read-only;
- Python `urllib` requests from Ubuntu returned HTTP 200 for `/health` and `/v1/market/status/BINANCE`;
- the responses reported `execution_mode=DISABLED` and `DISCONNECTED/NO_DATA` for Binance;
- the image imported `websockets=15.0.1` and `WebsocketsJsonConnector` without starting a market stream;
- the Compose configuration declared `api` and the opt-in `market-worker` profile; the profile was not started;
- a network-isolated one-off container imported `services.market_worker` and exited with `public market worker disabled` when no enable flag or instrument allowlist was supplied;
- a non-root temporary container wrote a synthetic `smoke:spot:TST/USDT` state through the read-write mount, the API read the status and normalized snapshot through its read-only mount, and both synthetic files were removed afterward;
- the state directory was initialized as mode `775`, owned by UID/GID `65532:65532`; the running API remained non-root;
- an earlier read-only Binance REST probe from the container failed with `Network is unreachable`; the latest host-network probe timed out after 5 seconds and returned no market payload;
- the host has a default IPv4 route, but public egress/DNS behavior is still not accepted for market connectivity;
- the log tail contained no API key, Secret, or request signature.

This is a limited historical runtime acceptance of the old read-only
development entrypoint, not acceptance of the new `backend`/`frontend` profile
or the full platform.

## Public Egress Configuration (2026-09-17)

The source now exposes authenticated `GET`/`PUT /api/v1/settings/network` and
`POST /api/v1/settings/network/probe`. The workbench has a Network Settings
section with, for each venue, editable public REST, public WebSocket, history
REST, REST API path, HTTP/REST proxy and WebSocket proxy fields. The API path
fields cover instrument discovery, order-book snapshots, historical candles and
the HTTP time probe. Only `http://` and `https://`
proxy URLs are accepted; blank values use direct access. Values with
credentials are persisted under the existing history volume at
`data/history/.runtime/public-network-settings.json`, returned only in
redacted form, and shared by backend, history Worker, Scheduler, instrument
catalog and market Worker. The market Worker watches the file and reconnects
after a change. REST paths are relative and cannot contain query credentials;
request parameters and response JSON normalization remain the venue adapter
contract.

The independent post-deployment probe and real browser acceptance on
2026-09-17 confirmed the saved `public-network-settings-v2` configuration.
Binance and Bybit use direct access; OKX HTTP and WebSocket use
`http://192.168.68.186:7897`. The authenticated settings page exposes, for
each venue, public REST, public WebSocket, history REST, HTTP proxy and
WebSocket proxy fields. OKX HTTP testing returned `200`, and OKX WebSocket
testing reported a successful handshake.

The deployed Market Worker allowlist is
`binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`. Container logs show OKX public
instrument and order-book REST responses with HTTP `200`; the state bridge
contains a real OKX book and its sequence advances over time. The probe and
settings endpoints do not enable accounts, private APIs, orders, withdrawals
or GACE writes. The source default remains disabled; only this explicitly
configured Ubuntu runtime enables the public read-only allowlist.

Never place a production proxy credential in source, screenshots or test
fixtures. Enter it only through the authenticated settings page or an
out-of-band protected runtime environment, and verify the resulting HTTP
probe before enabling any new public instrument.

## Latest API-route and OKX postflight (2026-09-18)

The current source was validated locally with `307 passed, 1 skipped`, Python
compilation, frontend production build, and the maintainable-source file limit.
The only skipped test is the optional `pyarrow` archive test on Windows Python
3.14. After a read-only inventory, one `--skip-history` deployment used the
existing SSH key and rebuilt only the application images. PostgreSQL, Redis,
the history volume, the task ledger, and the separate A-share Compose project
were preserved.

The authenticated runtime returned `public-network-settings-v2`. For each of
Binance, OKX, and Bybit it now exposes editable instrument, order-book,
candle, and HTTP probe REST paths in addition to the REST/WS/history roots and
HTTP/WS proxy fields. The deployed OKX configuration uses
`http://192.168.68.186:7897` for both public channels. An independent probe
returned HTTP `200` and WebSocket `REACHABLE` through that proxy. The remote
postflight observed OKX sequence `81267251658` with age about `0.088s`; the authenticated browser
observed the current OKX sequence `81267320085` while the
page remained open. Binance, OKX, and Bybit were all `CONNECTED` and the runtime
execution mode remained `DISABLED`.

A later read-only postflight, without another deployment or restart, confirmed
27 history datasets, `1,097,726` candles, `27/27` Parquet mirrors, an active
900-second Scheduler, task persistence `ready`, 166 task-ledger rows, and
three live public market instruments. PostgreSQL, Redis, the history volume
and the separate A-share Compose project remained in place.

The real browser logged in at `http://10.10.10.129:4191/login`, showed the
three venue cards and all four editable REST paths, reported OKX HTTP `200`
and a successful WS handshake, and showed three live read-only L2 cards.
Authenticated dynamic requests were HTTP `200`; the console had zero errors
and warnings. At a `390x844` viewport, document and body widths were both
`375`, so horizontal overflow was false. This postflight still does not claim
long-term market archival: the Worker writes the latest normalized market
state bridge only. Redis/SQL market persistence, atomic dual-write, private
API access, orders, withdrawals and GACE writes remain disabled or unfinished.

## Latest Read-Only Follow-Up (2026-09-18)

This follow-up was read-only: no deployment, restart, Redis cleanup, PostgreSQL
volume rebuild, history replacement, or A-share runtime change was performed.
The local full suite is `307 passed, 1 skipped`; Python compilation, the
frontend production build, and the 3000-line source gate passed. The skipped
test is the optional `pyarrow` archive test on Windows Python 3.14.

The current Ubuntu runtime has seven Crypto containers, binds the backend and
frontend to `10.10.10.129:8290/4191`, keeps `DISABLED`, and has a healthy
Market Worker. Read-only postflight reports 27 verified datasets,
`1,097,726` candles, `27/27` Parquet mirrors, an active 900-second Scheduler,
ready task persistence, and 166 task-ledger rows. PostgreSQL, Redis, the
history volume, and the separate A-share Compose project remain in place.

The authenticated Network Settings page exposes per-venue REST, WebSocket and
history roots, four REST paths, and HTTP/WS proxy fields. The current OKX
proxy is `http://192.168.68.186:7897`; browser probes returned HTTP `200` and
a successful WebSocket handshake. The public allowlist remains
`binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`, and the three live L2 state
bridges are `CONNECTED` with the OKX sequence advancing during observation.
These settings cover endpoint, region-path, API-version-path and proxy changes.
Request parameter changes, JSON schema changes, and WebSocket message-contract
changes still require the corresponding venue adapter update.

## Latest Source-Only Reliability Follow-Up (2026-09-18)

The source now audits the current Redis delivery receipt marker separately from
the SQL receipt recorded at publish time. If a published task remains as one
matching envelope in `queue` or `processing` but its marker is missing,
`audit_and_repair()` uses the same fixed `message_id` through
`ensure_enqueued()` and refreshes the SQL receipt. It does not push a second
envelope. Mismatch, duplicate, dead-letter and unknown delivery states remain
fail-closed; terminal `SETTLED_NO_PUBLISH` tasks remain valid.

This was the source-only state at the time of that snapshot and is superseded
by the deployment verification below. Redis and SQL still do not form an
atomic cross-system transaction; complete log archival, object-storage
lifecycle, disaster-recovery rehearsal and isolated real-DLQ replay evidence
remain future work.

## Latest Deployment Verification (2026-09-18)

A single `--skip-history` deployment used the existing SSH key after a
read-only inventory. PostgreSQL, Redis, the history volume and task ledger
were preserved, and the separate A-share runtime was not touched. The
postflight confirmed seven Crypto containers, backend/frontend bindings at
`10.10.10.129:8290/4191`, a healthy Market Worker, an active Scheduler, and
execution mode `DISABLED`.

Local verification for the deployed source is `307 passed, 1 skipped`; Python
compilation, the frontend production build and the 3000-line source gate also
passed. The only skipped test is the optional `pyarrow` archive test on
Windows Python 3.14.

The authenticated Network Settings page exposes per-venue REST, WebSocket and
history roots, instrument/order-book/candle/time REST paths, HTTP proxies and
WebSocket proxies. The deployed public allowlist is
`binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`. Binance and Bybit use direct
access; OKX uses `http://192.168.68.186:7897`. Independent HTTP probing
returned `200`, the OKX WebSocket probe completed successfully, all three
public L2 bridges were `CONNECTED`, and the OKX sequence advanced during
observation.

The result endpoint returned `task-result-v1` with archive state `READY`;
completion events retained only an archive reference and summary. Dead-letter
replay requests and terminal settlement are audited without copying complete
results into the event table. The public market bridge remains a latest-value
state file, not long-term market archival or an executable quote. Private API,
real accounts, orders, automatic selling, withdrawals and GACE writes remain
disabled.

## Future Changes

The 2026-09-17 source slice adds cooperative shutdown recovery for a task
reaching a safe checkpoint: the SQL task is returned to `queued` and a new
outbox delivery intent is committed before the old Redis claim is acknowledged.
The local source, isolated recovery drill and Ubuntu non-empty-task postflight
all passed within the offline/read-only task boundary. The next reliability
slice is to make Redis/SQL dual-write behavior atomic and to complete
production-grade log archival, object-storage lifecycle, tenant isolation and
disaster-recovery verification. The current remote DLQ contains one old
malformed history task; it has not been replayed or claimed as successful.

Before adding real credentials or external exposure, add all of the following:

1. SSH key or managed Secret Provider;
2. replace development authentication with production user, session and operator access;
3. PostgreSQL/Redis backups, credential rotation, and independent runtime postflight;
4. real public market connector tests and data freshness gates;
5. separate read-only account profile;
6. production monitoring, rollback, and current runtime postflight evidence.

## Latest Public Ticker Verification (2026-09-18)

After a read-only inventory, one `--skip-history` deployment rebuilt the
application images. PostgreSQL, Redis, the history volume, the task ledger and
the separate A-share runtime were preserved. The runtime still binds
`10.10.10.129:8290/4191`, runs seven Crypto containers with a healthy Market
Worker, and keeps execution mode `DISABLED`.

The authenticated public ticker endpoint returned 974 merged USDT symbols.
Binance, OKX and Bybit returned 683, 406 and 395 spot tickers respectively.
The response carried a valid ISO `as_of` timestamp, and the three REST fetches
are independent and parallel. The browser showed `3/3` markets available and
`1-60/974`; searching `BTC` returned five rows with the three venue prices,
24-hour changes, range position, spread and quote volume. The existing three
read-only L2 cards continued to receive fresh snapshots.

Local verification was `317 passed, 1 skipped`, with Python compilation, the
frontend production build and the 3000-line source gate passing. Browser
console errors/warnings were `0/0`; at `390x844`, document and body widths
were both `375`. This verifies a public read-only snapshot, not long-term
market archival, executable arbitrage, private API access or trading.
