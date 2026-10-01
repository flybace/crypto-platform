# 状态持久化实现记录

更新时间：2026-09-16

本文记录历史自动同步运行态从独立 JSON 文件迁移到 PostgreSQL 主存储的实现边界。它只记录源码和验证事实，不把历史计划书中的“已完成”描述直接当成当前运行端状态。

## 1. 范围

本切片处理两类有界状态：

| 状态 | PostgreSQL 快照键 | JSON 恢复副本 |
|---|---|---|
| Scheduler 心跳、退避和最近任务 | `crypto.runtime.history-scheduler` | `data/history/.runtime/history-scheduler.json` |
| 最近同步计划和最近任务 ID | `crypto.runtime.history-sync` | `data/history/.runtime/history-sync.json` |
| 历史任务明细、逐项进度和生命周期结果 | `control_tasks` / `control_task_events` | `data/history/.history_jobs.json` |

历史 K 线、CSV、Manifest、原始响应和 Parquet 不在本切片中迁移。历史任务明细已完成主恢复切换；`.history_jobs.json` 仍保留为首次迁移和恢复副本，优雅停机排空、结果对象归档和死信重放审计属于后续切片。

## 2. 设计

`SqlStateStore` 复用 `control_domain_snapshots` 表，以命名空间隔离有界 JSON 文档。SQL 行是主真相：

1. SQL 行存在时，读取只来自 SQL，不回读旧 JSON；
2. SQL 行不存在且 JSON 存在时，首次读取导入 SQL；
3. SQL 写入提交成功后，尽力更新 JSON 恢复副本；
4. 恢复副本写失败不能把已经提交的 SQL 写入报告为失败；
5. API 与独立 Scheduler 使用同一 PostgreSQL 数据库和快照键，Worker 不需要访问状态文件。

`HistorySchedulerState` 保留只传路径时的 JSON 兼容模式，`HistorySyncService` 同样保留 `state_path` 兼容入口。生产组合根通过 `build_runtime_state()` 注入 SQL 适配器和 JSON 桥；本地旧调用方无需立即改造。

历史任务在 `HistoryJobManager` 启动时先读取按 `kind=history_download` 过滤的任务账本，再解释 JSON。有效 SQL 记录会覆盖同 ID 的旧 JSON；没有 SQL 记录的 JSON-only 活动任务会安全标记为 `interrupted` 并重新登记；账本损坏时优先使用 JSON 恢复副本，并写入 `task_ledger_corrupt` 事件；两边都不能恢复时保留一个可见的 `failed` 任务，避免静默消失。每次状态变更先同步 SQL，再更新 JSON 副本，API、Scheduler 和 Worker 都通过统一账本读取逐项明细。

## 3. 一致性边界

Scheduler 的每次 `patch()` 都先从共享状态读取，再在一个 SQL 快照写入中保存完整状态；API 的 `snapshot()` 每次读取共享状态。同步服务在 `plan()` 和 `last_plan()` 前刷新 SQL，避免长生命周期对象只使用初始化时的任务 ID。历史任务明细恢复以 SQL 账本为主，JSON 只用于迁移/恢复；任务状态和 JSON 副本仍不是同一事务，真正的 Redis/SQL 双写原子性、优雅停机排空和结果对象归档属于后续任务可靠性切片。

## 4. 测试与部署

本地新增回归覆盖：旧 JSON 首次迁移、被篡改恢复副本不覆盖 SQL、第二实例读取 Scheduler 状态、第二实例读取同步计划和任务 ID、SQL 终态覆盖旧历史 JSON、缺失账本的进程重启保护、损坏账本恢复与审计、无 JSON 时的可见失败状态。当前 Windows 证据为：

- `py -3 -m pytest -q`：`186 passed, 1 skipped`；
- `py -3 -m compileall -q backend src tests`：通过；
- `frontend`: `npm run build`：`vue-tsc -b` 和 `vite build` 通过；
- `scripts/check-file-line-limit.ps1`：628 个可维护文本文件，无文件超过 3000 行，`docs/PROJECT_PLAN.md` 仅达到拆分预警线；`data/` 历史 CSV 等运行时数据按文件规模规则不纳入扫描。

本切片已使用 SSH Key 和 `--skip-history` 部署到 Ubuntu `10.10.10.129`，没有覆盖 `data/history`、PostgreSQL、Redis、任务队列或 A 股独立 Compose 数据。独立 `postflight` 确认 backend、frontend、task-worker、task-scheduler、PostgreSQL 和 Redis 六个 Crypto 服务运行，前后端发布地址为 `10.10.10.129:8290/4191`，backend healthy、frontend HTTP 为 200，认证登录为 200，执行模式仍为 `DISABLED`；历史为 18 个数据集、`749,467` 根 K 线，质量为 `gap0_duplicate0`，元数据和 Parquet 均为 `18/18`，历史价差对齐 `105,417` 根，任务账本为 41 条，Scheduler 周期为 900 秒且当前状态为 `active`，任务持久化为 `ready`。A 股 `quant-platform` 仍保持 14 个独立运行服务。

真实浏览器直连 `http://10.10.10.129:4191/login` 完成登录后，历史数据页可读取覆盖、同步计划、下载任务并展开任务详情；运行计划页可读取 `18/27`、`749,467`、`SAFE_PAUSED`、`DISABLED` 和 26 个只读能力。当前浏览器请求中的登录、市场、历史覆盖、历史任务、同步计划、归档和运行计划接口均返回 200；390x844 视口中 `documentWidth=375`、`bodyWidth=375`，无横向溢出，控制台错误和警告均为 0。移动端截图保存在 `output/playwright/runtime-plan-390-20260916.png`。

因此，本切片的当前状态为 `runtime-accepted`。该后验仍不覆盖实时行情长期归档、生产用户/会话库、真实账户、真实订单、自动卖出、提币或 GACE Runtime；状态持久化不会改变任何交易授权。

## 5. 安全边界

本切片不启用行情 Worker、真实账户、私有 API、真实订单、自动卖出、提币或 GACE 写 Action。当前执行模式继续为 `DISABLED`，状态持久化不会改变任何交易授权。
