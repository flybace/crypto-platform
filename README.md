# Crypto Multi-Market Quant Platform

## 一键安装

先装好 [Docker](https://docs.docker.com/get-docker/)（Windows 用 Docker Desktop），然后一条命令：

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh | bash
```

```powershell
# Windows（PowerShell）
irm https://raw.githubusercontent.com/flybace/crypto-platform/main/install.ps1 | iex
```

> **私有仓库**：本仓库是私有的，直接拉取会 401。先在 GitHub 生成一个**只读** token：
> Settings → Developer settings → Personal access tokens → Fine-grained tokens →
> 权限只勾 `Contents: Read-only`，Repository access 只选 `flybace/crypto-platform`。
> 然后带上 token 运行：
>
> ```bash
> # Linux / macOS
> export GITHUB_TOKEN=你的token
> curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
>   https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh | bash
> ```
> ```powershell
> # Windows
> $env:GITHUB_TOKEN = "你的token"
> irm -Headers @{Authorization="Bearer $env:GITHUB_TOKEN"} `
>   https://raw.githubusercontent.com/flybace/crypto-platform/main/install.ps1 | iex
> ```
> token 只用来拉代码，不会写进 `.git/config` 或 `.env`。

脚本会自动拉取代码、生成配置、构建启动。装好后打开 http://127.0.0.1:4191 ，用户名和随机密码会打印在终端里（仅显示一次）。

- 重复运行脚本 = 更新到最新版
- 停止：`docker compose stop`；卸载：`./uninstall.sh`（`--purge` 连数据一起删）
- 执行模式默认 `DISABLED`，真实下单强制关闭


独立的数字资产多市场行情、价差研究、回测、模拟交易与受控交易系统。

本项目与 D:/code/quant-platform、D:/code/gace-system、D:/code/gace-build-center 保持源码、数据库、运行环境和发布边界隔离。源码在 Windows `D:/code/crypto-platform`，运行目标为独立 Ubuntu `10.10.10.129`（用户 `flybace`）；项目可以独立运行，也从第一天保留未来生成 GACE App 所需的适配层和合同。

当前阶段：M0/M1 的离线范围已验证；M2-M5 已落有固定数据前置实现，但尚未完成对应里程碑。公开 REST、Binance/OKX/Bybit WebSocket、盘口序列重建、可测试重连运行器、`PublicMarketCollector`、跨进程状态桥和按交易所网络设置已落地；独立 `market-worker` 源码入口仍默认关闭。历史 K 线下载、三家公开分页连接器、质量校验、原始响应归档、正式元数据状态和历史任务 API 已落码；Ubuntu 运行端已显式启用 Binance、OKX、Bybit 的 BTC/USDT 公共只读实时行情。真实账户、私有 API、真实下单和 GACE Runtime 均未接入。

当前工作台还提供筛选到策略孵化池的候选交接、研究入口以及历史任务详情、停止和未完成项重试；默认 Compose 会把历史、回测、筛选、研究、模拟和矩阵任务交给独立 Worker，local 模式仍可在 API 进程同步运行。历史页的手动同步按钮只用于首次回补或临时补数，日常增量由 Scheduler 自动完成。这些操作只改变独立项目的本地研究状态，服务端执行模式仍固定为 `DISABLED`。

## 公开出口与代理设置

三家交易所的公开 REST、WebSocket 和历史接口均已接入同一套按交易所网络配置。工作台的“网络设置”页面支持分别填写 Binance、OKX、Bybit 的公共 REST 根地址、公共 WebSocket 地址、历史 K 线 REST 根地址、品种目录/盘口/K 线/探测 REST API 路径、HTTP/REST 代理 URL 和 WebSocket 代理 URL；域名、区域入口或 API 版本路径改变时可直接手动修改，支持 `http://`/`https://` 代理，留空表示直连，保存后 API 立即生效，历史 Worker/Scheduler 在下一次任务或周期读取，行情 Worker 自动重连。请求参数和返回 JSON 规范化仍由交易所适配器负责。代理凭据不会通过 API 回显，保存文件位于 `data/history/.runtime/public-network-settings.json`，运行时按私有文件写入。

Ubuntu `10.10.10.129` 的独立后验确认 Binance、OKX、Bybit 公共 HTTP/WebSocket 均可达；Binance/Bybit 直连，OKX 当前通过已保存的 `http://192.168.68.186:7897`。OKX HTTP 测试为 `200`，WebSocket 测试握手成功，实时 Worker 的 OKX 序列持续增长。该结论会随出口、DNS 和交易所区域策略变化，可在每个交易所卡片上重新测试。代理服务必须能从 Ubuntu 访问，Ubuntu 内的 `127.0.0.1` 只代表 Ubuntu 自身，不代表 Windows 主机。

`CRYPTO_PUBLIC_TRUST_ENV=false` 默认关闭进程环境中的隐式代理，避免不同 Worker 使用不同出口；如不使用页面配置，也可通过 `CRYPTO_<VENUE>_PUBLIC_HTTP_PROXY` 和 `CRYPTO_<VENUE>_PUBLIC_WS_PROXY` 提供启动默认值。此功能只服务公开只读接口，不包含 API Key、账户、订单或提币权限。

后台任务控制面已增加服务端资源配额、有限尝试次数、Redis 死信队列和租约过期恢复；超过配额的任务进入 `blocked`，超过尝试上限的任务进入 `dead_lettered`，任务中心保留认证后的死信查询和带审计的人工重放入口。死信重放要求有界 `reason`，可用 `request_id` 幂等，重复请求不会创建第二个任务；普通 retry 对死信任务 fail-closed。该切片已通过 Windows 本地测试和前端构建，Ubuntu 运行端后验在本轮部署后单独记录。

## 文档入口

- [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md)：项目分析、AI 接手指南、本轮验证和后续优先级
- [AGENTS.md](AGENTS.md)：AI 开发约束、项目边界和验证命令
- [docs/STATUS_2026_09_28.md](docs/STATUS_2026_09_28.md)：计划书 31.29/31.30 的历史状态记录
- docs/PROJECT_PLAN.md：完整建设计划书
- docs/PROJECT_MINDMAP.md：与计划章节一一对应的思维导图
- docs/EXCHANGE_API_RESEARCH.md：交易所开放 API 与 QMT 类能力调研
- docs/HISTORY_DATA_IMPLEMENTATION.md：历史数据实现、真实拉取记录与验收证据
- `py -3 .\scripts\archive_history.py`：把已校验 CSV 镜像为绑定 Manifest 摘要的 Parquet
- docs/AUTO_TRADING_POLICY.md：自动卖出、风险限额与熔断策略
- docs/FILE_SIZE_POLICY.md：单文件 3000 行硬上限与拆分规则
- docs/RUNTIME_DEPLOYMENT.md：Windows 源码到 Ubuntu 运行端的边界和部署说明
- docs/RUNTIME_PLAN_IMPLEMENTATION.md：运行计划和 GACE 只读能力实现记录
- docs/PARITY_MATRIX.md：与原 A 股量化系统的逐项功能对照和剩余缺口

## 设计原则

1. 独立 Crypto Core，不依赖 GACE Core。
2. Standalone Adapter 与 GACE Adapter 共用同一套业务核心。
3. 先公共行情、回测和模拟盘，后只读账户，最后才评估真实下单。
4. 初期只做中心化交易所现货，合约、DEX 和复杂跨链能力后置。
5. API Key 默认只允许交易，不允许提币；密钥不进入代码、前端、镜像或 App 包。
6. 文档状态、构建状态和运行验收状态分开记录，不把历史文档当作当前运行事实。

## 目录

~~~text
crypto-platform/
├─ frontend/           # Vue 3 + TypeScript + Vite 用户界面
│  ├─ src/views/       # 登录页和工作台
│  ├─ src/components/  # 历史数据、行情和公开品种目录页面
│  ├─ src/stores/      # Pinia 会话状态
│  ├─ Dockerfile       # Nginx 静态运行时
│  └─ nginx.conf       # 前端到后端的同源代理
├─ backend/            # FastAPI 应用、认证和 API 路由
│  ├─ app/auth/        # 开发阶段签名会话
│  ├─ app/api/         # 认证和市场 API
│  ├─ Dockerfile       # 后端服务镜像
│  └─ .env.example     # 本地配置样例，不含真实密钥
├─ contracts/
│  ├─ market/          # 市场、品种和行情合同
│  ├─ strategy/        # 策略和参数合同
│  ├─ paper/           # 模拟交易合同
│  ├─ execution/       # 执行、订单和账本合同
│  ├─ gace/            # GACE App 适配合同
│  └─ ai/              # AI Action 合同
├─ docs/               # 计划、架构、决策和验收证据
├─ src/
│  ├─ domain/          # 与平台无关的领域核心
│  ├─ application/     # 用例编排
│  ├─ ports/            # 外部能力端口
│  ├─ adapters/         # 交易所和独立运行适配器
│  ├─ services/         # 跨模块服务
│  └─ web/              # 旧只读 HTTP 入口，逐步收拢到 backend/
├─ tests/
│  ├─ unit/            # 单元测试
│  ├─ contract/        # 合同测试
│  ├─ replay/          # 录制回放测试
│  ├─ fake-exchange/   # 假交易所和故障测试
│  ├─ integration/     # 集成测试
│  └─ acceptance/      # 验收测试
├─ scripts/            # 工程检查脚本
├─ Dockerfile          # Crypto Core/Worker 兼容镜像
├─ compose.yaml        # Ubuntu 独立 Compose 入口
└─ README.md
~~~

`frontend/` 和 `backend/` 是应用层边界，`src/` 是可被 Standalone 和未来 GACE Adapter 共用的领域核心与适配器库。当前登录只使用环境变量配置的开发账号；没有真实 API Key、真实订单或真实交易能力。

## 本地开发闭环

先启动后端（PowerShell，直接运行后端时仍可使用回环地址）：

~~~powershell
$env:CRYPTO_DEV_MODE = "true"
$env:CRYPTO_ADMIN_PASSWORD = "local-dev-password"
$env:CRYPTO_SESSION_SECRET = "local-dev-session-secret-change-me"
py -3 -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8290
~~~

另开终端启动前端：

~~~powershell
cd frontend
npm install
npm run dev
~~~

浏览器访问 `http://127.0.0.1:4191/login`，使用 `admin` 和本地开发密码登录。前端代理 `/api` 到后端，登录成功后进入市场总览；默认执行模式始终为 `DISABLED`。工作台包含运行计划、历史数据、行情、策略、研究、回测、币池、筛选、模拟盘、风控和任务模块。

Ubuntu 运行端默认使用 `CRYPTO_BIND_ADDRESS=10.10.10.129` 发布前后端，适合在受控局域网中由 FRP 转发：前端 `4191`，后端 `8290`。如在本机用 Compose 做开发，必须显式设置 `CRYPTO_BIND_ADDRESS=127.0.0.1`。运行 `docker compose -p crypto-platform up -d` 会同时启动 PostgreSQL、Redis、backend、frontend、task-worker 和 task-scheduler；Scheduler 启动后立即检查一次，之后按 `CRYPTO_SCHEDULER_INTERVAL_SECONDS` 定时增量同步。网络或队列失败会进入持久化指数退避，状态可通过认证接口 `/api/v1/history/sync/status` 和历史数据页查看。不要把这两个端口直接暴露到公网。

## 工程门禁

所有可维护文本文件不超过 3000 行；达到 2400 行时应评估拆分。检查命令：

~~~powershell
pwsh -File .\scripts\check-file-line-limit.ps1
~~~

当前测试命令：

~~~powershell
py -3 -m pytest -q
~~~

当前工作树验证结果（2026-09-18）：`py -3 -m pytest -q` 为 `317 passed, 1 skipped`；`py -3 -m compileall -q backend src tests`、前端 `npm run build` 和 3000 行可维护源文件门禁均通过；唯一跳过项是当前 Python 3.14 没有可用的可选 `pyarrow` 轮子。Ubuntu 最新只读后验确认 7 个 Crypto 容器运行，前后端绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`，allowlist 为 `binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`；OKX REST `200`、WS 握手成功、快照序列持续增长。真实浏览器网络设置页显示三家交易所的四类 REST API 路径、根地址和代理字段，行情页显示 974 个合并公开 ticker 和三路实时只读盘口，控制台错误/警告为 `0/0`，390x844 无横向溢出。结果归档、完成事件摘要化、死信审计和 Worker 优雅排空已在既定只读边界内验证；执行模式保持 `DISABLED`，不代表真实交易或私有 API 已验收。

## 全市场公开行情

行情中心的“全市场行情”通过公开 REST ticker 接口读取 Binance、OKX、Bybit 的现货 24 小时数据，统一展示币种价格、24H 涨跌、区间位置、成交额和跨市场价差。当前默认 USDT 计价，支持 USDC/BTC/ETH、币种搜索、成交量/涨幅/价差/币种排序和分页，页面每 10 秒刷新，后端缓存 5 秒；交易所响应按市场独立处理，单一市场阻断不会丢掉其他市场数据。

本轮真实后验确认接口返回 974 个合并币种，Binance/OKX/Bybit 分别返回 683/406/395 个 USDT 现货 ticker；搜索 BTC 后可看到 BTC、WBTC、BTCST、BTCDOWN、BTCUP 的三家报价和 24H 变化。该功能只读、研究用途，不构成同步盘口套利信号；历史行情长期归档、账户、私有 API、下单和 GACE 写能力仍未开放。

## 公开行情 Worker

`market-worker` 位于 Compose 的 `market` profile 中，只有显式设置 `CRYPTO_MARKET_WORKER_ENABLED=true` 和 `CRYPTO_MARKET_INSTRUMENTS=venue:native_symbol,...` 才会采集公开行情。例如：

~~~bash
CRYPTO_MARKET_WORKER_ENABLED=true \
CRYPTO_MARKET_INSTRUMENTS=binance:BTCUSDT,okx:BTC-USDT \
docker compose -p crypto-platform --profile market up -d market-worker
~~~

当前 Worker 只把经过快照、增量、序列和新鲜度门禁的盘口写入 `CRYPTO_MARKET_STATE_PATH` 的规范化 JSON 状态文件并输出有限日志；它不写数据库、不接 API Key、不下单。Ubuntu 运行端已经完成公开只读实时行情验收；API 通过只读挂载读取该状态，但状态文件仍是最新值桥接，不是历史行情归档或高频生产存储。

当前 Ubuntu 运行端使用公开 WebSocket 端点显式订阅 `binance:BTCUSDT`、`bybit:BTCUSDT` 和 `okx:BTC-USDT`；三路只读取公开盘口并写入最新状态桥，不代表账户连接或交易权限。OKX 通过网络设置中的 HTTP/WS 代理接入，状态为实时；最新状态桥不是历史行情归档或可执行报价。
