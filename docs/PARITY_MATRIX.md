# Crypto 与原量化系统功能对照

本文把 `D:/code/quant-platform` 的主要业务能力映射到独立 Crypto 项目。状态只描述当前代码证据，不把计划书中的设计目标当成已完成。

状态含义：

- `verified`：当前源码有自动测试，并在本地接口或真实浏览器路径验证过；
- `implemented`：源码已存在，但还缺少完整运行环境或端到端证据；
- `partial`：只有安全的子集或只读投影；
- `planned`：尚未落码；
- `blocked`：明确被安全、网络或外部依赖阻断。

## 功能对照

| 原量化系统能力 | Crypto 对应模块 | 当前状态 | 说明与下一步 |
|---|---|---:|---|
| 前后端工作台与登录 | `frontend/`、`backend/` | verified | 已有登录、市场总览、模块导航；继续补全操作闭环 |
| PostgreSQL 系统/用户/历史库 | `backend/app/services/task_store.py`、`backend/app/services/history_metadata.py`、`backend/app/services/domain_state.py`、`src/adapters/standalone/state_store.py`、Compose PostgreSQL | verified | 任务账本、历史正式元数据、有界领域快照以及历史 Scheduler/同步运行态主存储已落码；运行态使用 `crypto.runtime.*` 命名空间，旧 JSON 只用于首次导入和恢复副本；Ubuntu 后验确认 8 条领域快照加 2 条运行态快照，Worker 可跨进程读取；仍需拆分生产用户库、行情库和审计库 |
| Redis 缓存、事件和队列 | `backend/app/services/redis_runtime.py`、`backend/app/services/task_queue.py`、`backend/app/services/task_quota.py`、Compose Redis | verified | 默认 Compose 启动的历史同步、回测、筛选、研究、模拟和策略矩阵通过统一 Redis 队列和 Worker 执行；历史 Scheduler 定时规划增量任务并对失败退避；任务已有有界资源配额、尝试次数、死信列表和租约恢复，Worker 当前任务优雅排空与受控停机安全检查点恢复已由本地回归测试和 Ubuntu 非空任务演练覆盖，Compose 已配置 30 秒停机窗口；receipt marker 丢失审计与固定 `message_id` 修复已随应用部署完成远端后验；全量测试 `331 passed, 1 skipped`；Redis/SQL 双写原子性和租户级隔离仍待补齐 |
| 行情快照与交易所状态 | `src/application/market_data.py`、`src/services/market_worker.py`、`backend/app/services/public_tickers.py`、`backend/app/api/market.py` | verified | 公共适配器、状态桥和 Binance/OKX/Bybit BTC/USDT 公共 L2 已在 Ubuntu 验证；认证后的 ticker 聚合支持搜索、排序、分页；BTC/USDT 币种详情、跨市场走势和单市场 K 线已通过真实远端浏览器验收；L2 文件归档支持分段/保留/恢复/限量查询但默认关闭，不代表长期行情存储完成 |
| 交易所公网出口、API 路径与代理 | `src/adapters/venues/api_routes.py`、`src/adapters/venues/proxy.py`、`backend/app/services/public_network_settings.py`、`backend/app/api/settings.py` | verified | 按交易所配置公共 REST、公共 WebSocket、历史 REST、品种/盘口/K 线/探测 REST 路径和 HTTP/WS 代理；认证 API 脱敏并共享给历史/目录/行情进程。当前 Binance/Bybit 直连，OKX 使用保存的 `192.168.68.186:7897`，HTTP `200`、WS 握手成功；设置页允许后续手动替换根地址、路径和代理，JSON 协议变化仍需适配器升级 |
| 股票主数据/交易品种目录 | `backend/app/services/instrument_catalog.py` | verified | Binance、Bybit 公开 USDT 现货目录已接入；OKX 公共品种接口已被实时 Worker 以 `BTC-USDT` 验证，完整 OKX 目录刷新仍需单独观察 |
| 交易规则、精度和最小数量 | `src/domain/venue.py`、交易所适配器 | partial | 现货核心字段已有；还需统一过滤器缓存、精度变更审计和下单前校验 |
| 日线/分钟历史数据 | `src/application/history_storage.py`、`src/application/history_response_archive.py`、`backend/app/services/history_metadata.py` | verified | Windows Binance/Bybit 18 个数据集、747,865 根 K 线，缺口和重复均为 0；Ubuntu 后验为 27 个数据集、`1,125,676` 根 K 线、gap/duplicate 均为 0、Parquet `27/27`；历史价差对齐数据为 `108,945` 根。Bybit `1d`/`1h` 增量任务本轮因公共 REST 网络错误阻断；L2 长期归档默认关闭 |
| Parquet 历史归档 | `src/application/history_archive.py` | verified | Ubuntu 当前 27/27 已生成；本机受 Python 3.14 的 pyarrow 轮子限制 |
| 历史数据任务中心 | `HistoryJobManager`、`TaskStore`、`RedisTaskQueue` | verified | 历史下载已由 Redis 队列、独立 Worker 和 PostgreSQL 账本执行；租约续期、过期回收、自动重试、死信终态、认证死信 API、死信重放审计、结果归档和当前任务优雅排空已有源码/本地测试验证；本轮 Ubuntu 确认非空任务经 Worker 停机/重启后通过 outbox 恢复并唯一完成，SQL 任务表持续可读、事件归档全部 `READY`、结果归档为 `task-result-v1` `READY`；任务中心 API/浏览器可读取合并任务投影。本轮未执行真实 DLQ 重放，不把重放成功事件扩展为运行端事实 |
| 策略管理与模板 | `backend/app/services/strategy_registry.py` | verified | 内置策略注册、启停和版本信息已有；缺第三方策略 SDK |
| 策略包、导入和校验 | `strategy_packages.py`、`contracts/strategy/` | partial | 合同和本地包服务已有；缺沙箱执行、资源限额和完整导入 UI |
| 选股/筛选 | `src/application/screening.py` | verified | 基于已校验 K 线的筛选、候选交接、Redis Worker 路由和任务账本投影已有；需补多因子数据源和资源限额 |
| 股票池/自选池/策略矩阵 | `pool_catalog.py`、`strategy_matrix.py` | verified | 币池、来源审计和矩阵研究已有；需补跨交易所池快照与定时刷新 |
| 回测、自动调参、Walk-forward | `candle_backtest.py`、`portfolio_backtest.py`、`research_runs.py` | verified | 固定输入回测、比较、参数搜索、组合回测、Redis Worker 执行和任务账本投影已有；需补自动任务调度和更完整成本模型 |
| VN.py 执行验证 | Crypto execution ports | planned | 不直接移植 A 股 VN.py；先用 Fake Exchange/Testnet 建立统一执行合同 |
| 模拟交易与成交审计 | `paper_trading.py`、`src/domain/paper.py` | verified | 单腿/双腿模拟、卖出优先风控、策略回放 Worker 和任务账本已有；需补跨进程撮合服务和更完整成交审计 |
| 账户余额、订单、成交、撤单 | `src/ports/account.py`、`execution.py` | partial | 领域端口和只读假账户已有；真实私有 API、回报和对账未接入 |
| 风控、限额、紧急停止 | `risk_policy.py`、`risk_precheck.py` | verified | `DISABLED` 和卖出优先边界已验证；需把限额状态持久化并接入执行网关 |
| 自动交易 | 真实 execution adapter | blocked | 当前没有真实下单能力；必须完成 Testnet、幂等、对账、熔断和人工授权后再开放 |
| 新闻、建议、通知 | `news.py`、`advice.py`、`notifications.py` | partial | 站内研究投影已有；外部消息源和信号联动未完成 |
| AI 助手与能力注册 | `assistant.py`、`capability_catalog.py` | partial | 只读能力合同和显式调用已有；Provider、模型路由、GACE Runtime 未实际接入 |
| 任务中心、日志、取消、重试 | `tasks.py`、`TaskStore`、`src/application/task_lifecycle.py`、`task_result_archive.py` | verified | 历史、回测、筛选、研究、模拟和矩阵任务共享队列、账本、详情、事件、取消、普通重试、死信重放和跨进程结果读取；`task-result-v1` 结果归档、内容寻址校验、归档引用 API、完成事件摘要化、死信 request_id 幂等审计、当前任务优雅排空和受控停机安全检查点重新排队已完成源码、本地测试与 Ubuntu 运行端验证；Redis receipt marker 丢失时的唯一投递修复也已部署后验；非空目标任务经停机/重启后唯一完成，13 个生命周期事件全部可恢复，SQL 任务行与完成事件仅保留摘要和归档引用；Redis/SQL 双写原子性、租户级隔离、生产级完整日志归档和对象存储生命周期仍待完成 |
| 用户、权限、Session 撤销 | `backend/app/auth/` | partial | 当前是单用户开发认证；生产用户、角色、撤销和审计未完成 |
| GACE App 构建与托管 | `contracts/gace/`、`src/adapters/gace/` | partial | App/Capability 合同和只读目录已有；构建中心合同稳定前不绑定 GACE Core |
| 独立 Ubuntu 运行 | `compose.yaml`、部署脚本 | verified | 独立前后端、PostgreSQL、Redis、任务 Worker/Scheduler、历史数据和公开 Market Worker 已部署并现场验收；最新后验确认 7 个 Crypto 服务、绑定、7 个容器重启次数为 0、backend/market-worker healthy、Redis queue/processing/claim marker 为 0、OKX 实时序列持续增长和 A 股 14 服务未受影响；本轮不把 DLQ 重放写成成功事实；源码默认仍关闭 market-worker |

## 历史现场复核（2026-09-18）

源码和自动验证：全量 `py -3 -m pytest -q` 为 `311 passed, 1 skipped`；Python 编译、前端类型检查/构建和 3000 行可维护源文件门禁均通过。唯一跳过项是 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。代理设置页面、OKX HTTP/WS 测试和移动视口已在真实浏览器验收。

Ubuntu 独立后验：7 个 Crypto 容器运行，应用镜像为 `backend:0.1.0`/`frontend:0.1.0`，PostgreSQL 与 Redis 容器及数据卷保持运行；backend/market-worker healthy，前后端绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`。本次只读后验历史数据为 27 个数据集、`1,097,726` 根 K 线、27/27 Parquet，Scheduler 为 `active/900s`，任务持久化为 `ready`，任务账本为 166 条。公开行情 allowlist 为 `binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`；Binance、Bybit、OKX 均为 `CONNECTED`，OKX 当前序列为 `81267251658`，快照年龄约 `0.088s`。

真实浏览器直连登录后，网络设置页显示三家交易所各自的公共 REST、公共 WebSocket、历史 REST、四类 REST API 路径和 HTTP/WS 代理字段，OKX 代理脱敏显示；OKX 页面测试为 HTTP `200 · 815 ms`、WS 握手成功 `1894 ms`。行情中心显示 Binance/OKX/Bybit 实时只读 L2、买一/卖一和序列；OKX 序列从 `81261229073` 变为 `81261234276`，接收时间持续更新。既有认证结果接口、日志接口、移动视口和控制台验收保持 HTTP `200`、`0/0` 和无横向溢出。远端保留 1 条真实旧 history_download 死信，本轮没有删除、重放或记录虚假的成功事件。

## 当前源码可靠性增量（2026-09-18）

`TaskDispatchConsistency` 已能发现当前 Redis receipt marker 与 SQL receipt 不一致，并在唯一正确 envelope 存活时用固定 `message_id` 安全补回。该切片的本地全量测试已达到 `307 passed, 1 skipped`，并已随本轮应用部署完成远端服务后验；Redis/SQL 跨系统原子双写、完整日志归档、对象存储生命周期、灾备恢复和真实 DLQ 重放仍未完成。

## 历史部署后验（2026-09-18）

本轮使用现有 SSH Key 完成一次 `--skip-history` 部署，保留 PostgreSQL、Redis、历史数据和任务账本，没有清空 Redis、重建 PostgreSQL 数据卷或修改 A 股项目。Ubuntu 运行端确认 7 个 Crypto 容器健康运行，前后端绑定 `10.10.10.129:8290/4191`，Scheduler 自动运行，执行模式为 `DISABLED`。

网络设置页按 Binance、OKX、Bybit 分别提供 REST/WS/历史根地址、品种/盘口/K 线/时间四类 REST 路径、HTTP 代理和 WebSocket 代理。OKX 使用保存的 `http://192.168.68.186:7897`，HTTP 探测返回 `200`、WebSocket 握手成功；三路公共 L2 均为 `CONNECTED`，OKX 序列在观察窗口持续增长。

任务结果归档接口返回 `task-result-v1` 和 `READY`，完成事件只保留归档引用与摘要；死信重放请求、排队、失败和终态结算均使用有界审计事件，事件和接口不重复保存完整结果。公开行情目前仍是最新状态桥，不是长期行情归档；Redis/SQL 原子双写、私有 API、真实账户、真实订单、自动卖出、提币和 GACE 写能力继续关闭或未完成。

## 推进顺序

1. 补完整日志归档和生产级停机恢复；持续观测已部署的死信重放审计与历史同步，下一次必须在有真实 DLQ 样本的隔离演练中验证重放成功事件和恢复结果。
2. 在 Redis/SQL 双写原子性和恢复演练完成前，继续保持 Testnet 私有 API 与真实交易能力关闭。
3. 完成交易所私有 API 的账户同步、订单状态机、撤单、成交回报和对账；先 Fake Exchange，再 Testnet。
4. 补策略 SDK、第三方策略包沙箱、多因子数据和自动研究任务。
5. 最后实现受控卖出自动交易、生产权限和 GACE Runtime Action；在此之前执行模式保持 `DISABLED`。

## 验收原则

每一项都必须分别记录源码状态、自动测试、当前本地接口/浏览器状态和 Ubuntu 运行状态。HTTP 200、容器启动或文档勾选不能替代真实功能验收。

## 历史现场复核：全市场公开 24H 行情（2026-09-18）

本轮在不清理 Redis、不重建 PostgreSQL 数据卷、不修改 A 股项目的前提下，完成一次 `--skip-history` 应用部署。全量测试为 `317 passed, 1 skipped`，Python 编译、前端生产构建和 3000 行门禁通过；唯一跳过项为 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。

认证接口 `GET /api/v1/market/tickers?quote_asset=USDT` 实测返回 974 个合并币种，Binance/OKX/Bybit 分别为 683/406/395 个 ticker，时间字段为有效 ISO 时间；三家请求并行处理，单一市场失败不会阻塞其他市场。行情中心真实浏览器显示 `3/3 市场可用` 和 `1-60/974`，搜索 BTC 返回 5 条结果，每行可查看三家价格、24H 涨跌、区间位置、价差和成交额；三路 L2 盘口持续刷新。

浏览器登录、动态请求和 ticker 接口均为 HTTP `200`，控制台错误/警告为 `0/0`，390x844 下 `documentWidth=375`、`bodyWidth=375`。本轮仍只证明公开只读行情快照，不证明长期行情归档、可执行套利、私有 API、真实账户或真实交易。

## 最新现场复核（2026-09-28）

- 源码/自动测试：L2 归档具备时间/大小分段、retention、尾部恢复和认证限量查询；币种详情提供三市场 ticker、跨市场共同时间走势和单市场 K 线。全量 `py -3 -m pytest -q` 为 `331 passed, 1 skipped`，编译、前端构建、3000 行门禁通过；跳过项为可选 `pyarrow`。
- Ubuntu/真实浏览器：7 个 Crypto 容器运行，前后端绑定 `10.10.10.129:4191/8290`，执行模式 `DISABLED`；BTC/USDT 详情显示 Binance、OKX、Bybit 实时报价、跨市场走势和单市场 K 线，ticker/overview/snapshot/compare/candles 请求均为 HTTP `200`。390x844 下 document/body 宽度均为 375，无横向溢出。
- 数据与调度：27 个历史数据集、`1,125,676` 根 K 线、gap/duplicate 均为 0、Parquet `27/27`；历史价差对齐数据 `108,945` 根。Scheduler 周期 900 秒，状态为 `backoff`，最近任务 `25/27` 完成，Bybit `1d`/`1h` 因公共 REST 网络错误阻断。
- 运行边界：`CRYPTO_MARKET_ARCHIVE_ENABLED` 继续关闭，行情仍是最新状态桥而非长期 L2 归档；结果归档为 `task-result-v1/READY`，完成事件只保留归档引用和摘要，Worker 优雅停机恢复已验收。Redis/SQL 原子双写、死信真实重放、私有账户、真实订单、自动卖出、提币和 GACE 写能力仍禁用或未完成。
