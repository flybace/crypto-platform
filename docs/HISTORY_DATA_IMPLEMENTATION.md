# 历史数据实现与现场记录

更新时间：2026-09-16

本文件记录历史行情功能的实际实现和当前证据。它不把计划书里的设计目标当成运行事实，也不把 Windows 本地数据直接当成 Ubuntu `10.10.10.129` 已部署数据；只有远端覆盖接口、质量检查和运行后验通过后，才记录为已同步的运行数据。

## 1. 当前状态

已实现并在本地验收：

- 统一 `Candle` 和 `HistoryQuery`，支持 `1d`、`1h`、`5m`；
- Binance Spot、OKX Spot、Bybit Spot 的公开 K 线分页连接器；
- Binance 正向游标、OKX `after` 游标、Bybit V5 `start`/`end` 游标；
- CSV 原子写入、按时间去重排序、缺口统计和 Manifest；
- 可选 Parquet 研究归档，归档元数据绑定源 Manifest 的 `dataset_id`、行数和 SHA-256；
- `history-raw-response-v1` 原始成功响应合同、敏感头/参数白名单和 `_raw` 原子归档；
- `history_dataset_records` 正式元数据仓储，记录文件、Manifest、原始响应和 Parquet 状态；
- 数据集、原始响应引用和历史元数据的单事务提交，以及 Parquet 失败后的 `DEGRADED` 状态；
- Manifest 保存来源、范围、周期、行数、缺口、下载响应内部重复数和 SHA-256；
- 认证后的数据集、覆盖率、下载任务 API；
- 历史任务请求指纹去重、详情读取、停止和未完成项重试；
- 历史任务与回测、筛选、研究、模拟、策略矩阵共用 Redis 队列、PostgreSQL 账本、Worker 租约和生命周期事件；
- 默认启动的历史 Scheduler，负责启动即检查、周期增量同步、活跃任务去重、持久化运行状态和失败指数退避；
- 认证后的公开现货品种目录 API，含交易状态、精度和缓存/阻断状态；
- 按同一开盘时间对齐两个市场的历史价差研究 API，含双边费率、滑点和阈值统计；
- CLI 批量下载和前端历史数据中心；CLI 仅用于首次回补或临时补数，日常增量由 Scheduler 自动完成；
- Parquet 归档 CLI、归档状态接口和历史页归档操作；
- `task-result-v1` 结果归档、跨进程结果读取、完成事件引用化和 Worker 当前任务优雅排空；
- 无 API Key、无真实账户、无真实订单，服务端执行模式仍为 `DISABLED`。

尚未完成：

- 分布式限频、实时行情长期归档、对象存储生命周期和生产级回放合同；本轮只完成历史 K 线原始响应及其正式元数据闭环；
- Ubuntu 运行端从交易所公网重新下载仍受各交易所出口状态约束，OKX 当前阻断；死信重放审计、Redis/SQL 双写原子性、生产级停机恢复、完整日志归档和对象存储生命周期仍未完成；
- 以正式数据集驱动的完整事件回测报告和生产级策略模块页面；当前已经有 K 线研究工作台和固定输入报告。

## 2. 目录与职责

```text
src/domain/candle.py                          # K 线和查询领域对象
src/ports/history.py                          # 公开历史数据端口
src/adapters/venues/*_history.py              # 交易所分页与载荷标准化
src/application/history_download.py           # 下载用例
src/application/history_response_archive.py   # history-raw-response-v1 原始响应归档
src/application/history_storage.py             # CSV、Manifest、质量统计
src/application/history_archive.py             # Manifest 绑定的 Parquet 镜像
src/application/spread_research.py             # 同周期跨市场历史价差研究
backend/app/services/history_service.py       # 运行时连接器组合
backend/app/services/history_jobs.py           # 有界后台任务
backend/app/services/history_metadata.py       # 正式历史元数据事务适配器
backend/app/services/history_scheduler_state.py # Scheduler 状态与退避持久化
backend/app/api/history.py                     # 认证 API
src/services/task_scheduler.py                 # 默认历史增量调度进程
scripts/pull_history.py                        # Windows/Ubuntu CLI
scripts/archive_history.py                     # 本地 Parquet 归档 CLI
frontend/src/components/HistoryDataCenter.vue  # 可视化数据中心
frontend/src/components/InstrumentCatalogPanel.vue # 行情页公开现货品种目录
frontend/src/components/MarketCenter.vue        # 行情、盘口门禁和历史价差研究
```

## 3. API 合同

| 方法 | 路径 | 认证 | 用途 |
|---|---|---|---|
| GET | `/api/v1/history/datasets` | Bearer | 列出已落盘且 SHA 校验通过的数据集 |
| GET | `/api/v1/history/coverage` | Bearer | 汇总数据集、行数和交易所覆盖 |
| GET | `/api/v1/history/archive` | Bearer | 查看 Parquet 归档状态和源摘要绑定 |
| POST | `/api/v1/history/archive` | Bearer | 为所有已校验数据集生成或刷新 Parquet 镜像 |
| GET | `/api/v1/history/sync/plan` | Bearer | 预览增量或回补窗口、现有数据质量和待下载序列 |
| GET | `/api/v1/history/sync/status` | Bearer | 查看自动 Scheduler 心跳、最近任务和退避状态 |
| POST | `/api/v1/history/sync` | Bearer | 只提交缺失、过期或质量阻断的数据同步任务 |
| GET | `/api/v1/history/jobs` | Bearer | 查看下载任务 |
| POST | `/api/v1/history/jobs` | Bearer | 创建公开历史数据下载任务 |
| GET | `/api/v1/history/jobs/{job_id}` | Bearer | 查看单个任务及逐项结果 |
| GET | `/api/v1/tasks/history:{job_id}` | Bearer | 查看统一任务投影和历史任务详情 |
| POST | `/api/v1/tasks/history:{job_id}/cancel` | Bearer | 停止排队或执行中的历史任务 |
| POST | `/api/v1/tasks/history:{job_id}/retry` | Bearer | 只重试未完成的历史任务项 |
| GET | `/api/v1/market/instruments` | Bearer | 查询交易所公开现货品种和约束元数据 |
| GET | `/api/v1/market/spread-history` | Bearer | 对齐两个市场的历史收盘价并扣除双边成本 |

请求示例：

```json
{
  "venue_ids": ["binance", "okx", "bybit"],
  "symbols": ["BTC/USDT", "ETH/USDT"],
  "interval": "1d",
  "start_at": "2025-09-14T00:00:00Z",
  "end_at": "2026-09-14T00:00:00Z"
}
```

日期范围是左闭右开。周期和交易所原生符号由后端转换，现阶段只允许中心化交易所现货数据。

同步计划支持 `incremental` 和 `backfill` 两种模式。增量模式从已验证
数据集的末端继续下载，回补模式按 `lookback_days` 重新覆盖目标起点；
服务端只把 `MISSING`、`STALE`、`BACKFILL_REQUIRED` 和 `QUALITY_BLOCKED`
项转换成任务，不会为 `UP_TO_DATE` 数据重复建任务。计划摘要同时返回
`verified_dataset_count`、`verified_row_count`、`missing_count`、
`quality_blocked_count`、`download_count` 和 `status`，便于页面区分“已有
数据”和“尚未拉取”。真正的网络结果仍以历史任务逐项状态、Manifest 和
SHA-256 校验为准。

历史价差研究使用指定买入市场和卖出市场的同一 `open_time` K 线收盘价，
计算毛价差后扣除两腿费率和两腿滑点。它只产生 `research_only=true` 的
统计结果，不读取 L2，不包含转账、库存、延迟、深度和两腿原子性，也不会
创建订单或改变执行模式。缺少任一数据集、Manifest 质量不通过或没有对齐
的正价格时，API 返回 `422` 阻断，不生成部分结果。

## 4. 存储合同

默认本地路径为 `data/history`，Compose 路径为 `/runtime/history`。每个序列使用：

```text
{venue}/{market_type}/{native_symbol}/{interval}.csv
{venue}/{market_type}/{native_symbol}/{interval}.csv.manifest.json
_parquet/{venue}/{market_type}/{native_symbol}/{interval}.parquet
_raw/{venue}/{market_type}/{native_symbol}/{interval}/{response_id}.json
```

CSV 字段固定为 `open_time`、`close_time`、`open`、`high`、`low`、`close`、`volume`、`quote_volume`、`trade_count`。读取数据集时会重新计算文件 SHA-256；文件缺失或哈希不一致会让覆盖率接口失败，不会继续把该数据集当作可信数据。

Parquet 是 CSV/Manifest 的研究镜像，不替代源文件。镜像使用 UTC 时间戳、整数成交笔数和字符串金额字段以保留 Decimal 文本精度，并在 Parquet schema metadata 中写入 `crypto_dataset_id`、`crypto_content_sha256`、`crypto_row_count` 和 `crypto_storage_key`。归档状态接口会对照当前 Manifest；源文件更新后旧镜像会显示为 `STALE`，不会继续标记为可用。

`_raw` 保存每次成功公开分页响应的合同化 JSON envelope，包括查询身份、请求路径和公开参数、成功状态码、白名单响应头、页序号、载荷 SHA-256 和原始载荷。归档文件按响应身份幂等写入；不会保存 Authorization、API Key、Secret、签名、Token 或 Cookie。原始响应是审计和重放证据，不直接替代规范化 CSV。

正式元数据记录使用 `history_dataset_records`，并与兼容旧版本的 `history_dataset_metadata` 一起在同一 PostgreSQL/SQLite 事务中更新。数据文件和数据库无法由单一文件系统事务覆盖，因此系统采用明确的提交顺序：CSV/Manifest 原子提交，原始响应归档，正式元数据事务，最后更新 Parquet 状态。数据库提交失败会保留可重建的文件和 `_raw`，Parquet 失败会把记录标记为 `DEGRADED`，下一次启动或任务会重新协调，不把部分完成误报为完整一致。

同一序列重复下载是幂等的。`duplicate_count` 只统计本次公开响应内部重复的开盘时间，已有文件与新响应之间的正常重叠不会被当成质量故障。`gap_count` 按周期和开盘时间计算连续序列缺口。

## 5. 本地真实拉取记录

以下数据在 Windows 工作树真实请求交易所公开端点后写入 `D:/code/crypto-platform/data/history`。该目录被 Git 忽略，不提交大文件。

| 交易所 | 端点 | 交易对 | 周期 | 行数 | 缺口 | 响应内部重复 | SHA-256 前缀 |
|---|---|---|---|---:|---:|---:|---|
| Binance | `data-api.binance.vision` | BTC/USDT | 1d | 2,083 | 0 | 0 | `5558b3bd2c4197f1` |
| Binance | `data-api.binance.vision` | ETH/USDT | 1d | 2,083 | 0 | 0 | `38269545b5a69134` |
| Binance | `data-api.binance.vision` | BNB/USDT | 1d | 2,083 | 0 | 0 | `00d27a32d079f3c7` |
| Bybit | `api.bybit-tr.com` | BTC/USDT | 1d | 1,898 | 0 | 0 | `d815560d23798d3c` |
| Bybit | `api.bybit-tr.com` | ETH/USDT | 1d | 1,898 | 0 | 0 | `6fb91cac37849da4` |
| Bybit | `api.bybit-tr.com` | BNB/USDT | 1d | 1,650 | 0 | 0 | `bcf344d1d15e9e73` |
| Binance | `data-api.binance.vision` | BTC/USDT | 1h | 17,523 | 0 | 0 | `59d72d995e1a075a` |
| Binance | `data-api.binance.vision` | ETH/USDT | 1h | 17,523 | 0 | 0 | `cc6d0e03119ea045` |
| Binance | `data-api.binance.vision` | BNB/USDT | 1h | 17,523 | 0 | 0 | `7f49381697cec539` |
| Bybit | `api.bybit-tr.com` | BTC/USDT | 1h | 17,523 | 0 | 0 | `136670b75934acc5` |
| Bybit | `api.bybit-tr.com` | ETH/USDT | 1h | 17,523 | 0 | 0 | `8f64e7474a7dfb02` |
| Bybit | `api.bybit-tr.com` | BNB/USDT | 1h | 17,523 | 0 | 0 | `433af327ccd62b99` |
| Binance | `data-api.binance.vision` | BTC/USDT | 5m | 105,157 | 0 | 0 | `4b52e1a3ac400cbe` |
| Binance | `data-api.binance.vision` | ETH/USDT | 5m | 105,157 | 0 | 0 | `6c2d38689ca6d00b` |
| Binance | `data-api.binance.vision` | BNB/USDT | 5m | 105,157 | 0 | 0 | `d94acd46e40ef884` |
| Bybit | `api.bybit-tr.com` | BTC/USDT | 5m | 105,157 | 0 | 0 | `9dfa4b6f575ae26c` |
| Bybit | `api.bybit-tr.com` | ETH/USDT | 5m | 105,157 | 0 | 0 | `6dbc77c7c65d0b1f` |
| Bybit | `api.bybit-tr.com` | BNB/USDT | 5m | 105,157 | 0 | 0 | `920909588caa200f` |

当前本地覆盖率为 18 个已验证数据集，占配置基线 27 条序列的 66.67%，Manifest 行数合计为 747,865。2026-09-15 增量补齐 Binance/Bybit 的 1h 和 5m 尾端后，18 个落盘文件的 `gap_count=0`、`duplicate_count=0`，并重新核对了 Manifest SHA-256。`data/history` 被 Git 忽略，数据通过 Manifest 和 SHA-256 在本机复核；Ubuntu 运行端另有独立的 18/18 Parquet 后验，不把大文件伪装成已提交源码。

### 5.2 2026-09-15 增量刷新

本轮只写入已结束的公开 K 线窗口：Binance/Bybit 的 BTC、ETH、BNB，`1h` 从 17,522 增至 17,523 行，`5m` 从 105,141 增至 105,157 行；最新 `5m` 开盘时间为 `2026-09-15T03:00:00Z`，Manifest 的 `end_at` 为 `2026-09-15T03:05:00Z`。第一次只拉取 `02:00` 之后窗口时，质量门禁发现 `01:45`、`01:50`、`01:55` 三根缺口；随后回补 `01:40` 至 `02:00`，六个 `5m` 数据集均恢复为 `gap_count=0`。

本轮增量后的 5m Manifest 摘要：

| Venue | 交易对 | 行数 | gap | duplicate | SHA-256 前缀 |
|---|---|---:|---:|---:|---|
| Binance | BTC/USDT | 105,157 | 0 | 0 | `4b52e1a3ac400cbe` |
| Binance | ETH/USDT | 105,157 | 0 | 0 | `6c2d38689ca6d00b` |
| Binance | BNB/USDT | 105,157 | 0 | 0 | `d94acd46e40ef884` |
| Bybit | BTC/USDT | 105,157 | 0 | 0 | `9dfa4b6f575ae26c` |
| Bybit | ETH/USDT | 105,157 | 0 | 0 | `6dbc77c7c65ae26c` |
| Bybit | BNB/USDT | 105,157 | 0 | 0 | `920909588caa200f` |

这次刷新仍未生成 OKX 数据；OKX 的 9 条基线序列仍是网络阻断，不能计入已验证覆盖。Ubuntu 运行端已在本轮代码、构建、浏览器和远端后验通过后同步历史，并完成 18/18 Parquet 归档。

### 5.1 公开现货品种目录现场记录

2026-09-15 在同一 Windows 运行端通过公开接口刷新目录，未提供 API Key：

| 交易所 | 公开端点 | USDT 现货品种 | 状态 | 缓存/阻断 |
|---|---|---:|---|---|
| Binance | `data-api.binance.vision/api/v3/exchangeInfo` | 491 | LIVE | 已写入本地目录缓存 |
| Bybit | `api.bybit-tr.com/v5/market/instruments-info` | 288 | LIVE | 已写入本地目录缓存 |
| OKX | `www.okx.com/api/v5/public/instruments` | 0 | BLOCKED | 当前 Windows 出口 TLS 网络错误 |

目录 API 的返回状态与历史数据状态分离：`LIVE` 表示本次公开请求成功，`CACHED` 表示使用最近成功结果，`BLOCKED` 表示无可用数据且不生成占位品种。交易对发现不等于已经归档历史数据；新交易对必须再通过历史任务下载、Manifest 校验和回测前置检查才能进入研究链路。

## 6.1 真实历史数据研究烟测

在上述本地数据上，使用认证后的 FastAPI TestClient 运行当前研究 API，未接入私有 API Key，也未产生真实订单：

| 用例 | 输入 | 结果 |
|---|---|---|
| 策略比较 | Binance BTC/USDT 1d，9 个已注册策略 | 9/9 完成，当前样本最佳为 `buy_and_hold` |
| 参数搜索 | Binance BTC/USDT 1d，MACD 4 组参数 | 4/4 完成 |
| 策略信号筛选 | 跨市场研究池，1h，6 个数据集 | 6 个数据集通过质量门禁，当前没有 BUY 信号 |
| 组合回测 | 跨市场研究池，1h，均线交叉 | 6 个数据集完成，0 个失败，样本收益 `-13.24095857698841872152065551%` |
| 筛选联动 | 空候选筛选结果传入组合回测 | 返回 `422` 阻断，未运行空组合 |

这组结果证明的是研究链路和数据质量门禁可运行，不是收益承诺。筛选结果为空时阻断是预期行为；有候选的联动路径由自动化验收夹具覆盖。

## 6. 阻塞记录

在同一 Windows 环境对 OKX `www.okx.com`、`aws.okx.com`、`eea.okx.com`、`my.okx.com` 和 `us.okx.com` 发起真实公开请求，均收到 TLS 握手失败；没有生成空数据或伪造数据。Bybit EU (`api.bybit.eu`) 被 CloudFront 按地区拒绝，返回 `403`。Bybit V5 的 `api.bybit-tr.com` 和 `api.bybit.kz` 能返回真实公开 K 线，`api.bybit.ae` 在本次探测中超时，因此默认配置改为 `api.bybit-tr.com`，区域端点仍保留为可配置项。Ubuntu 发布后仍需按实际出口重新探测。

这只证明当前 Windows 出口到 OKX 默认域名受阻；2026-09-15 从 Ubuntu 运行端对 Binance、Bybit 和 OKX 做只读探测时，Binance/Bybit 返回 HTTP 200，OKX `www.okx.com` 请求超时，也没有生成空数据或占位数据。它不证明 OKX API 永久不可用；Ubuntu 后续仍需在网络条件变化时重新做公开端点探测和数据落盘验收。

## 7. 验收证据

- Python：全量测试以最近一次独立命令输出为准，跳过项仅因当前 Windows Python 3.14 没有可用的 `pyarrow<20` 轮子；
- 前端：`vue-tsc -b` 和 `vite build` 成功；
- 文件规模：无文件超过 3000 行；`docs/PROJECT_PLAN.md` 达到拆分预警线，后续应按章节拆分；
- 浏览器：上一轮 SSH 隧道地址 `http://127.0.0.1:14191/` 登录后，历史数据页显示 `18 / 27` 和 `747,937` 根 K 线，Parquet 显示 `18/18 READY`，Binance BTC/USDT 5m 尾部和历史价差研究可读取远端数据；本次最新只读 API 后验更新为 18 个数据集、748,177 根 K 线，Windows 本地对应覆盖为 18 个数据集、747,865 根 K 线；
- 真实下载：Binance 的 BTC/ETH/BNB 1d、1h、5m，以及 Bybit `api.bybit-tr.com` 的 BTC/ETH/BNB 1d、1h、5m 已落盘，全部 `gap_count=0`、`duplicate_count=0`，Manifest SHA-256 校验通过；当前 Binance/Bybit BTC/USDT 1d 分别为 2,083 和 1,898 行，Bybit BNB/USDT 1d 为 1,650 行，1h 各为 17,523 行，5m 各为 105,157 行；Bybit 5m 的分页使用官方 `start`/`end` 参数；
- 浏览器当前网络记录：`GET /api/v1/history/coverage` 为 `200`，`POST /api/v1/history/jobs` 为 `202`，OKX 阻断任务及后续轮询均为 `200`；覆盖矩阵同时显示最新任务产生的阻断原因；
- 任务控制：统一任务详情、停止、重试和同请求指纹去重由验收测试覆盖；取消中的任务显示 `cancelling`，进程恢复异常显示 `interrupted`，不会被静默归类为成功；
- M2 持久化切片：原始响应白名单、合同 envelope、重复归档、文件成功但元数据失败、Parquet 失败降级和正式元数据状态由单元、合同和任务测试覆盖；
- Ubuntu：此前 2026-09-15 本轮重新部署后的独立后验确认 backend/frontend healthy、认证通过、PostgreSQL/Redis ready、历史为 18 个数据集和 747,937 根 K 线、全部缺口与重复为 0、Parquet 为 18/18、历史价差研究对齐 105,182 根 K 线、任务账本有 13 条记录；SSH 隧道浏览器确认同样的数据和任务中心状态。该证据不覆盖 Ubuntu 交易所公网实时行情 Worker、真实账户或真实交易；OKX 的 9 条基线序列仍未拉取。

## 2026-09-15 08:25 UTC 运行端历史只读后验

项目自带 `scripts/remote_deploy.py postflight` 通过隐藏的运行端凭据执行，没有重新部署或重启服务。Ubuntu `10.10.10.129` 当前健康检查、认证登录和前端 HTTP 均通过，执行模式为 `DISABLED`；18 个数据集共 748,177 根 K 线，全部缺口和重复为 0，Parquet 为 18/18 READY，历史价差研究对齐 105,219 根 K 线、成本模型 30 bps，统一任务账本为 27 条记录。A 股 `quant-platform` 仍为独立 Compose 项目并运行 14 个服务。该后验仍不覆盖真实账户、真实订单、自动卖出或 GACE Runtime。

## 2026-09-15 09:36 UTC 运行端自动同步后验

本轮使用 SSH Key 重新部署 Compose，默认启动历史 `task-worker` 和 `task-scheduler`，并将宿主发布地址设为 `10.10.10.129:8290`（backend）和 `10.10.10.129:4191`（frontend）。独立后验确认远端 18 个数据集、748,273 根 K 线、全部缺口和重复为 0、Parquet 为 18/18 READY，统一任务账本为 33 条；认证 Scheduler 状态为 `active`、周期为 900 秒。真实浏览器直连 Ubuntu 地址登录后，历史页显示自动同步“任务执行中、每 15 分钟”，相关请求全部 HTTP 200，控制台无错误和警告。A 股 `quant-platform` 仍有 14 个独立运行服务，执行模式仍为 `DISABLED`；OKX 网络阻断、实时行情 Worker、真实账户和真实交易仍未解除或启用。

## 2026-09-15 04:10 UTC 增量刷新后验

前一份记录中的 `747,775` 是增量刷新前快照。本次先运行 `HistorySyncService.plan()`，发现 Binance/Bybit 的 1h、5m 共 12 个窗口落后当前完整周期，1d 已是最新；随后只执行这 12 个窗口的公开分页下载，没有重拉整年数据。

- 12/12 个增量请求返回 `completed`；
- 当前本地历史为 18 个数据集、`747,865` 根 K 线；Ubuntu 独立运行端后验为 18 个数据集、`747,937` 根 K 线；
- 每个已落盘 Manifest 的 `gap_count=0`、`duplicate_count=0`；
- 二次同步计划显示 Binance/Bybit 18/18 条序列为 `UP_TO_DATE`、`download_count=0`；
- OKX 9 条基线仍因当前网络/TLS 出口阻断而未生成数据，不能计入覆盖；
- 本轮新增的 PostgreSQL/Redis/任务账本代码已部署并通过独立健康和任务后验；领域历史源仍以 CSV/Manifest 为准。领域快照迁移代码已在 Windows 工作树完成本地验证，但尚未随本轮源码重新部署到 Ubuntu。

## 7.1 2026-09-15 14:29 UTC M2 原始响应与远端部署后验

本轮先修复历史持久化切片的两个测试收集错误，再按独立命令重新验证：`py -3 -m pytest -q` 为 `169 passed, 1 skipped`，`compileall` 通过，文件规模检查为 617 个文本文件且没有超过 3000 行，计划书与思维导图为 32 个章节一致；前端 `npm run build` 的 `vue-tsc -b` 和 `vite build` 均通过。唯一跳过项是当前 Windows Python 3.14 没有可用的 `pyarrow<20` 轮子。

本轮使用 SSH Key 将当前源码重新构建并部署到 Ubuntu `10.10.10.129`。独立后验确认 backend、frontend、task-worker、task-scheduler、PostgreSQL 和 Redis 共 6 个 Crypto 服务运行，backend/frontend 发布地址仍为 `10.10.10.129:8290`/`10.10.10.129:4191`，backend healthy，前端 HTTP 为 200，执行模式为 `DISABLED`；A 股 `quant-platform` 仍为独立 Compose 项目并运行 14 个服务。远端历史为 18 个数据集、748,627 根 K 线，所有已验证数据 `gap_count=0`、`duplicate_count=0`，Parquet 为 18/18 READY，历史价差研究对齐 105,288 根 K 线，成本模型为 30 bps，统一任务账本为 39 条。认证 Scheduler 周期为 900 秒，当前因已有 OKX 网络阻断任务处于持久化退避；这不影响 Binance/Bybit 已验证数据，也不产生占位数据。

为验证 M2 原始响应链路，使用真实浏览器登录后提交了一个 Binance BTC/USDT 1d、单日窗口的受控公开历史任务。任务响应为 HTTP 202，单项最终为 `completed`；认证读取 `/api/v1/history/raw-responses` 返回 HTTP 200、`status=READY`、合同版本 `history-raw-response-v1`、`response_count=1`、`invalid_count=0`，原始响应文件为 `_raw/binance/spot/BTCUSDT/1d/...json`。只读容器检查确认合同字段正确，敏感字段匹配数为 0。未认证访问同一路径返回 HTTP 401，说明新接口仍受保护。真实浏览器刷新后历史页显示 18/27、748,627 根 K 线、18/18 Parquet、任务已完成；浏览器控制台错误和警告均为 0。

本节证明的是当前 M2 持久化切片、独立运行端、认证和历史任务后验，不证明实时行情 Worker、真实账户、真实订单、自动卖出、提币、OKX 网络可用或 GACE Runtime 已完成。

## 7.2 2026-09-16 领域快照与运行端后验（此前切片）

本轮领域快照源码变更已使用 SSH Key 部署到 Ubuntu `10.10.10.129`，部署参数使用 `--skip-history`，没有覆盖现有 CSV/Manifest、PostgreSQL 或 Redis 数据。独立后验确认 backend、frontend、task-worker、task-scheduler、PostgreSQL 和 Redis 六个 Crypto 服务运行，前后端仍绑定 `10.10.10.129:8290/4191`；A 股 `quant-platform` 仍保持 14 个独立运行服务。

- 认证历史覆盖为 18 个数据集、`748,867` 根 K 线，全部 `gap_count=0`、`duplicate_count=0`，Parquet 为 `18/18 READY`；历史价差研究对齐 `105,325` 根 K 线，成本模型为 30 bps；
- 自动 Scheduler 周期为 900 秒，当前为持久化 `backoff`，原因是已有 OKX 网络阻断任务仍可重试；任务账本为 40 条；
- PostgreSQL 直接查询确认 `control_domain_snapshots` 为 8 条，状态为 `ready`；独立 task-worker 读取 `crypto.domain.strategies` 版本 1，证明跨进程共享 SQL 主存储生效；
- 认证系统/运行计划读取确认运行状态为 `safe_paused`、执行模式为 `DISABLED`、GACE 只读能力为 26、写能力为 0；
- 真实浏览器直连 `http://10.10.10.129:4191/` 登录后打开运行计划，显示 `18/27`、`748,867`、安全暂停和 `DISABLED`；390x844 视口检查中页面和 body 均无横向溢出，控制台错误和警告均为 0。

本节只接受当前只读研究、历史同步、任务基础设施和领域快照迁移的运行后验，不接受实时行情长期归档、真实账户、真实订单、自动卖出、提币或 GACE Runtime。

## 7.3 2026-09-16 Scheduler 与同步状态 SQL 主存储

本轮继续处理剩余运行态 JSON，范围仅限历史自动同步的状态可恢复性，不触碰真实账户、私有 API、真实订单或 GACE 写 Action。`HistorySchedulerState` 和 `HistorySyncService` 现在都支持注入统一 `StateStore`；API 和独立 `task-scheduler` 通过 `TaskStore` 使用以下 PostgreSQL 快照键：

- `crypto.runtime.history-scheduler`：自动调度心跳、最近计划/任务、失败次数和退避时间；
- `crypto.runtime.history-sync`：最近同步计划和最近任务 ID。

两个 JSON 文件仍挂载在 `/runtime/history/.runtime/`，但只承担旧版本首次导入和成功 SQL 写入后的恢复副本。SQL 记录存在时不会回读旧 JSON；同步服务在生成计划和读取最近计划前刷新 SQL，降低长生命周期 API/Scheduler 实例覆盖其他进程状态的风险。历史任务自身的 `.history_jobs.json` 已在下一切片完成主恢复治理：有效 `control_tasks` 记录优先，JSON 只承担迁移和恢复副本。

Windows 当前源码证据：`py -3 -m pytest -q` 为 `181 passed, 1 skipped`；`compileall`、前端 `vue-tsc -b`/`vite build` 和 3000 行文件门禁均通过。此前的单次全量测试出现过 1 个异步收尾波动，单测复现通过，随后全量回归通过；不能用那次失败输出代替本轮最终结果。

本节代码随后已使用 SSH Key 和 `--skip-history` 部署到 Ubuntu；独立后验确认没有覆盖远端 CSV/Manifest、PostgreSQL、Redis、任务队列或 A 股 `quant-platform` 服务。直接查询 PostgreSQL 确认 `crypto.runtime.history-scheduler` 为版本 3、`crypto.runtime.history-sync` 为版本 1；认证 API 和真实浏览器均读取到当前运行态。截图保存在 `output/playwright/runtime-state-20260916-mobile.png` 和 `output/playwright/runtime-state-20260916-desktop.png`。本节仍不扩展为真实交易授权。

本轮运行端后验摘要：6 个 Crypto 服务运行，前后端绑定 `10.10.10.129:8290/4191`，A 股项目 14 个服务保持运行；历史 18 个数据集、`748,867` 根 K 线、质量缺口/重复为 0、Parquet `18/18 READY`、历史价差对齐 `105,325` 根、任务账本 40 条，Scheduler 900 秒且处于既有 OKX 阻断任务的持久化 `backoff`。真实浏览器运行计划显示 `18/27`、`safe_paused`、`DISABLED`、只读能力 26/写能力 0；390x844 视口无横向溢出，控制台错误和警告均为 0。该后验仍只对应上一切片，不能证明本轮历史任务恢复变更已经部署。

## 7.4 2026-09-16 历史任务账本主恢复切片

本切片继续处理 `.history_jobs.json` 的剩余边界，范围限定为历史下载任务的跨进程读取、进程重启恢复和损坏保护，不触碰真实账户、私有 API、真实订单、自动卖出或 GACE 写 Action。

新增实现：

- `backend/app/services/task_store.py`：任务列表支持按 `kind` 过滤，历史任务启动恢复时只读取 `history_download` 账本，避免把其他后台任务误当成历史任务；
- `backend/app/services/history_jobs.py`：启动时先采用 PostgreSQL 任务账本，再加载/迁移 JSON；SQL 有效记录覆盖同 ID 的旧 JSON，JSON-only 活动任务在无账本时标记 `interrupted`；
- `backend/app/services/history_jobs.py`：增加账本结构校验，校验失败时优先使用 JSON 恢复副本并写入 `task_ledger_corrupt` 事件；两边都不可恢复时保留可见 `failed` 任务；状态同步顺序调整为先写 SQL，再写 JSON 恢复副本；
- `tests/unit/test_history_jobs.py`：增加 SQL 终态覆盖旧 JSON、缺账本进程重启、损坏账本恢复审计和无 JSON 失败保护测试。

Windows 本轮证据：`py -3 -m pytest -q` 为 `186 passed, 1 skipped`，`py -3 -m compileall -q backend src tests` 通过。随后使用 SSH Key 和 `--skip-history` 将本切片源码重新构建并部署到 Ubuntu `10.10.10.129`，保留现有 CSV/Manifest、PostgreSQL、Redis、任务队列和 A 股独立 Compose。

本轮独立运行后验确认 backend、frontend、task-worker、task-scheduler、PostgreSQL 和 Redis 六个 Crypto 服务运行，backend/frontend 绑定 `10.10.10.129:8290/4191`，backend healthy、前端 HTTP 为 200、认证登录为 200，执行模式为 `DISABLED`；历史为 18 个数据集、`749,467` 根 K 线，`gap0_duplicate0`，元数据 `18/18`，Parquet `18/18 READY`，历史价差对齐 `105,417` 根、成本模型 30 bps，统一任务账本为 41 条，Scheduler 周期为 900 秒且状态为 `active`，任务持久化为 `ready`。A 股 `quant-platform` 仍有 14 个独立运行服务。

真实浏览器直连 Ubuntu 地址完成登录后，历史页读取到历史覆盖、同步计划、下载任务和任务详情，运行计划页读取到 `18/27`、`749,467`、`SAFE_PAUSED`、`DISABLED` 和 26 个只读能力；相关认证接口均返回 HTTP 200，390x844 视口 `documentWidth=375`、`bodyWidth=375`，无横向溢出，控制台错误和警告均为 0。截图为 `output/playwright/runtime-plan-390-20260916.png`。本切片状态为 `runtime-accepted`，但不扩展为实时行情长期归档、真实账户、真实订单、自动卖出、提币或 GACE Runtime 已完成。

这一切片完成“历史任务明细以 PostgreSQL 为主要恢复来源”的目标，但没有解决 Redis/SQL 双写原子性、优雅停机排空、结果对象存储、完整日志归档或死信重放审计。下一步按顺序处理优雅停机、结果归档和死信重放，再扩大长时段异步研究任务；在这些边界完成前不进入 Testnet 私有 API。

## 8. 下一步

1. 在 Ubuntu 运行端启用行情 Worker 或新增历史任务前，先单独确认 OKX、Binance、Bybit 区域端点和 DNS/TLS；
2. 用 1 小时、5 分钟小窗口持续验证分页、缺口和限频，再扩大数据范围；
3. 在 Ubuntu 新版本运行端触发一次受控公开历史任务，后验 `_raw`、正式元数据和 Parquet 状态；
4. 将已校验 K 线继续扩展到组合研究、策略矩阵和跨市场任务，严格区分未完成 K 线、缺口数据和已验收数据；
5. 已完成历史任务的 SQL 主恢复、缺失/损坏账本保护；继续处理优雅停机排空和结果恢复，再扩大 5 分钟数据覆盖；
6. 持续对运行计划和 GACE 只读 capability catalog 做当前浏览器/API 验收，并在构建中心合同稳定后再做正式 App 包验收；
7. 为 Redis 任务链路补死信重放审计、优雅停机和结果归档后，再扩大长时段异步研究任务；
8. 在历史、回测、模拟和风控验收前不开放真实账户或真实下单。
