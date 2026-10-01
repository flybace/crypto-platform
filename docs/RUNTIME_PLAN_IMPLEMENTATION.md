# 运行计划与 GACE 只读能力实现记录

更新时间：2026-09-16

本文件记录当前工作树新增的运行计划聚合层和 GACE 只读能力清单。它是状态投影和适配合同，不是交易授权，也不把文档状态当成运行完成。

## 1. 目标

现有 A 股系统的交易计划依赖开盘、收盘、交易日历和盘中阶段。数字资产现货是 24/7 市场，不能直接复用股票交易日逻辑。本实现把当前 Crypto 系统已经存在的模块收敛到一条可观察链：

```text
历史数据 -> 策略配置 -> 研究/回测 -> 模拟盘 -> 风险门禁 -> 真实执行阻断
                                      \-> GACE 只读 capability catalog
```

运行计划只读取服务内的当前状态，不创建任务、不启动 Worker、不提交订单、不修改风险配置。

## 2. 当前源码

| 文件 | 职责 | 状态 |
|---|---|---|
| `backend/app/services/runtime_plan.py` | 汇总历史、策略、研究、回测、模拟、风控和任务状态 | implemented |
| `backend/app/api/runtime.py` | 暴露认证后的运行计划接口 | implemented |
| `backend/app/services/capability_catalog.py` | 生成声明式只读能力清单 | implemented |
| `backend/app/api/capabilities.py` | 暴露 GACE 能力发现接口 | implemented |
| `frontend/src/components/RuntimePlanCenter.vue` | 独立工作台运行计划页面 | implemented |
| `contracts/gace/capability-catalog-v1.schema.json` | GACE 只读能力合同 | verified by contract test |

## 3. API 合同

| 方法 | 路径 | 认证 | 作用 |
|---|---|---|---|
| GET | `/api/v1/runtime/plan` | Bearer | 读取 24/7 运行节点和安全边界 |
| GET | `/api/v1/gace/capabilities` | Bearer | 读取未来 GACE App/AI 可发现的只读能力 |

运行计划的关键字段：

- `status`：当前为 `safe_paused`，表示真实执行仍未开放；
- `market_mode`：固定表达为 `24/7 spot research`；
- `steps`：历史数据、策略、研究、模拟盘、风控、真实执行、GACE 适配七个节点；
- `summary`：当前数据集、K 线、策略、回测、研究和模拟统计；
- `risk`：现有服务端风险摘要及所有阻断原因；
- `recent_tasks`：当前任务中心的统一只读投影；
- `capabilities`：只读能力数量和 catalog 路径。

统一任务投影包含历史下载的进度、请求指纹、详情路径、是否可停止和是否可重试；默认 Compose 启动的历史、回测、筛选、研究、模拟策略回放和策略矩阵由独立 Worker 执行，并写入 PostgreSQL 任务账本和生命周期事件，local 模式保留同步开发回退。除文件型历史任务外，策略、币池、筛选、回测、研究、模拟盘、风控、通知、新闻和 AI 助手的有界可变状态均通过 `control_domain_snapshots` 使用 PostgreSQL 主存储；旧 JSON 只用于首次导入和成功写入后的恢复副本。历史 Scheduler 自动规划增量同步，持久化心跳、活跃任务和失败退避状态。历史任务的 `cancelling` 仍属于活跃状态，`interrupted` 属于异常状态。策略孵化池通过 `strategy.incubators.read` 暴露只读发现能力，不改变写入能力为空的约束。

节点状态含义：`ready` 是当前节点条件满足，`partial` 是有可用子集但仍有缺口，`waiting` 是尚未触发，`running` 是模拟配置正在使用，`blocked` 是安全门禁阻断。任何节点状态都不能解释为真实收益或真实交易许可。

## 4. GACE 兼容边界

catalog 只列出 `GET` 只读接口，并带有 `READ_ONLY` 风险等级、scope、认证要求和版本字段。当前 `write_capabilities` 必须为空；真实下单和提币在 `blocked_actions` 中显式列出。未来 GACE App 只能通过正式 App Manifest、身份、Secret、任务和 AI Action 合同接入，不应直接导入 `backend/app/services`。

GACE AI 的后续接入仍需另行实现：

1. 先用 `runtime.plan.read`、`history.coverage.read`、`backtests.runs.read` 等只读 Action；
2. 研究和模拟动作使用带过期时间、幂等键和审计记录的受控写 Action；
3. 真实账户和订单动作必须经过独立授权、风控和人工确认；
4. AI 永远不接触 API Key、数据库连接和任意 SQL。

## 5. 验收边界

当前已完成源码和合同烟测；完整验收需要同时记录：

- Python 全量测试和 capability catalog 合同测试；
- `vue-tsc -b`、`vite build`；
- 当前本地服务的认证 API 响应；
- 浏览器登录后打开“运行计划”，确认当前历史覆盖、七个节点、风险阻断和任务列表；本地数据复核为 18/27 数据集、747,865 根 K 线，Ubuntu 最新后验为 18 个数据集、749,467 根 K 线，自动 Scheduler 周期为 900 秒。
- 390px 视口无横向溢出；
- 未登录访问运行计划和 capability 接口均返回 401；
- 2026-09-16 已在 Ubuntu `10.10.10.129` 完成领域快照、历史 Scheduler/同步运行态和任务结果归档源码变更的独立部署，并通过健康、登录、历史覆盖、Parquet、结果归档接口、10 条领域及运行态快照、独立 Worker 跨进程读取、当前任务优雅排空、`10.10.10.129:8290/4191` 绑定、前端 HTTP 和直连浏览器运行计划后验；移动端 390px 无横向溢出，控制台错误和警告均为 0；仍不扩展到真实行情、账户或交易。
- 当前 Windows 源码新增死信重放审计的定向/全量测试、认证 API、Redis DLQ 引用和 `history_download` 目标 job 验证；Ubuntu 运行端需在本轮部署后独立确认重放 endpoint、PostgreSQL 审计行、Redis 标记和浏览器任务中心。

## 6. 未完成项

- 运行计划暂不持久化每日快照，不替代正式任务调度器；
- 历史数据源仍是 CSV/Manifest；Parquet 已在 Ubuntu 形成 18/18 归档，历史任务状态仍保留文件恢复投影；Scheduler 和同步状态已改为 PostgreSQL `crypto.runtime.*` 主存储，并保留 JSON 恢复副本；领域快照 PostgreSQL 主存储已在 Ubuntu 后验为 8 条，独立 Worker 可跨进程读取策略快照，浏览器运行计划也已验证；
- GACE Runtime、构建中心候选包和 AI 实际调用尚未验收；
- Ubuntu 交易所公网行情 Worker 和从交易所重新下载历史数据尚未完成网络后验；
- 死信重放审计已完成源码和本地自动测试，当前 Ubuntu 版本尚未后验；部署前不得把本地状态写成运行端已完成；
- 真实账户、真实订单、自动卖出和提币能力仍未实现，默认执行模式保持 `DISABLED`。
