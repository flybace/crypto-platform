# Crypto Multi-Market Quant Platform 独立建设计划书

## 文档信息

| 项目 | 内容 |
|---|---|
| 项目名称 | Crypto Multi-Market Quant Platform |
| 中文定位 | 数字资产多市场行情、价差研究、回测、模拟交易与受控交易系统 |
| 独立项目路径 | D:\code\crypto-platform |
| Windows 源码端 | 当前 Windows 工作树，负责源码、测试和构建输入 |
| Ubuntu 运行端 | Ubuntu 24.04.4 LTS，`10.10.10.129`，运行用户 `flybace` |
| 现有 A 股系统 | D:\code\quant-platform，仅作逻辑和代码借鉴，不作为运行依赖 |
| GACE 产品源码 | D:\code\gace-system，仅通过正式 App 合同兼容，不直接嵌入业务 |
| GACE 构建中心 | D:\code\gace-build-center，后期用于候选包和制品交付 |
| 计划版本 | 0.9.10 |
| 计划状态 | 最新现场状态（2026-09-28，详见 31.29）：M0/M1 离线核心已验证；公开行情网络配置、OKX 公共 REST/WebSocket、Binance/OKX/Bybit BTC/USDT 只读实时 L2、三家全市场 24H ticker 和 BTC/USDT 币种详情已达到目标 Ubuntu 的有限 `runtime-accepted`；每个交易所仍可在设置页单独修改 REST 品种、盘口、K 线和探测 API 路径。M2-M5 仍为部分实现，实时 L2 长期归档默认关闭，行情持久化、Redis/SQL 原子双写、完整日志/对象存储生命周期、租户隔离和真实私有 API 未完成。执行模式为 `DISABLED`，A 股项目保持隔离；结果归档、完成事件摘要化、Worker 优雅停机/恢复和死信审计路由已完成只读运行验收。真实账户、订单、自动卖出、提币和 GACE 写能力保持关闭。 |
| 编制日期 | 2026-09-17 |

## 阅读规则

本计划是新项目的设计和执行基线，不是当前功能完成报告。状态必须使用以下含义：

> 当前事实以本页最后一节 `31.29`、`docs/PARITY_MATRIX.md` 的“最新现场复核”和 `docs/RUNTIME_DEPLOYMENT.md` 的当前运行状态为准：Ubuntu 已运行 Binance、OKX、Bybit 的 BTC/USDT 公共只读实时行情，并通过公开 REST 提供三家全市场 24H ticker；真实浏览器已验证币种详情、跨市场走势和单市场 K 线。网络设置页支持每个交易所手动维护公共 REST、公共 WebSocket、历史 REST、REST API 路径和 HTTP/WS 代理。实时 L2 归档仍关闭，旧章节中的阻断或两路行情结论均为历史快照，不覆盖 31.29 的结果。

- proposed：提出，尚未实现。
- designed：设计完成，尚未形成可运行实现。
- implemented：源码已实现，但还没有完成对应验收。
- verified：通过指定范围的源码、合同或自动化验证。
- runtime-accepted：在目标运行环境中完成独立后验。
- blocked：存在明确阻塞条件，不能用历史文档或猜测替代。

任何完成结论都必须同时说明源码 Commit、测试证据、构建制品和运行验收范围。历史计划、旧报告、包摘要或 HTTP 200 不能单独证明当前系统已经可用。
文件组织约束见第 29 章：可维护文本文件单文件最多 3000 行，接近 2400 行时应提前拆分。
第 20 章和第 25 章保留规划阶段的历史快照；当前实现证据和未完成边界以第 30、31 章、`docs/HISTORY_DATA_IMPLEMENTATION.md` 以及最近一次源码、测试、构建和浏览器命令为准。文档中写有“交付”或“完成条件”不等于当前已经实现。

---

## 00. 项目边界与总原则

### 00.1 项目边界

本项目是一个全新的独立数字资产系统，目标是同时支持：

1. 独立运行；
2. 多中心化交易所行情接入；
3. 跨市场价差分析；
4. 历史数据与策略回测；
5. 模拟交易；
6. 受控的真实现货交易；
7. 后续作为 GACE App 交付；
8. 后续接受 GACE AI 的声明式调用。

### 00.2 明确不做

第一阶段不做以下事项：

- 不改造现有 A 股量化系统；
- 不把 Crypto 业务放进 GACE Core；
- 不共享 A 股系统或 GACE 的数据库、Redis、任务目录和 Secret；
- 不接触真实 API Key；
- 不开放自动提币或资金转移；
- 不以价差存在直接等同于可以获利；
- 不在第一阶段同时支持所有交易所、所有币种、合约和 DEX；
- 不把 Crypto 系统建设成交易所或资金托管平台；
- 不让 AI 直接访问数据库、交易所密钥或任意下单接口。

### 00.3 设计总原则

1. 独立核心：领域模型、行情、策略、回测、模拟和风险逻辑不依赖 GACE。
2. 端口与适配器：将身份、密钥、通知、任务、存储和交易执行抽象成端口。
3. 双目标交付：Standalone Adapter 和 GACE Adapter 共用同一套业务核心。
4. 观察优先：先观察、回放和模拟，再接真实账户和真实下单。
5. 可审计：订单、成交、策略状态、风险动作和 AI Action 均要有可追溯记录。
6. 故障默认安全：行情失效、订单状态未知、密钥失效和平台停止时，禁止继续扩大风险。
7. GACE 适配不越界：通过正式 App 合同和构建中心交付，不修改平台核心绕过门禁。
8. 证据优先：计划状态、源码状态、构建状态和运行状态分开记录。
9. 源码端与运行端分离：Windows 只负责源码和构建输入，Ubuntu 运行独立 Crypto 服务；不得把运行端目录、凭据和数据卷回写进源码仓库。

### 00.4 本章产出

- 项目边界说明；
- 术语和状态定义；
- 变更范围控制；
- 后续章节的统一验收口径。

### 00.5 本章验收

- 新项目可以在没有 GACE 的情况下启动设计和开发；
- 新项目不依赖 D:\code\quant-platform 的数据库或运行容器；
- 所有 GACE 依赖均位于适配层或交付配置层；
- 当前工作树状态不会被误写成项目功能完成状态。

---

## 01. 背景与问题定义

### 01.1 背景

现有量化系统面向中国 A 股，包含 A 股行情、交易日、T+1、涨跌停、100 股一手、印花税、A 股历史数据和 A 股策略执行假设。它不适合直接改造成跨交易所数字资产系统。

新的系统要解决的是另一个问题：

> 在多个数字资产市场之间统一采集公开行情，识别扣除成本后的可执行价差，并通过回测、模拟和受控执行验证策略。

### 01.2 核心问题

真正的问题不是把 Binance 接进来，而是：

- 不同交易所的品种、命名、精度和交易规则不一致；
- 同一交易对在不同交易所的买卖盘深度不同；
- 最新成交价不等于可成交价；
- 价差必须扣除双边手续费、滑点、延迟和资金调拨成本；
- 跨市场两腿订单不存在天然的原子提交；
- 一边成交、一边失败会产生真实的方向性敞口；
- 历史 K 线不足以准确回测短时套利；
- API、限频、断线、交易所维护和资产冻结都可能改变结果；
- GACE 的 App 构建与动态 Runtime 仍在建设，不能反过来阻塞核心领域开发。

### 01.3 产品定位

产品定位为：

> 独立的数字资产跨市场量化研究与受控交易平台。

第一目标是建立可信的数据和执行模拟能力，第二目标是发现可验证的策略机会，第三目标才是小范围真实交易。

---

## 02. 产品目标与成功标准

### 02.1 总目标

建立一个可以独立运行、可回测、可模拟、可审计，并能在后期作为 GACE App 交付的多市场数字资产系统。

### 02.2 分阶段目标

| 阶段 | 目标 |
|---|---|
| P0 | 完成独立项目、领域边界、合同和开发基线 |
| P1 | 接入少量交易所公开行情并统一展示 |
| P2 | 建立历史数据下载、质量校验和可重复回放 |
| P3 | 完成跨市场价差扫描和机会归因 |
| P4 | 完成回测和模拟交易 |
| P5 | 完成真实账户只读接入与风险告警 |
| P6 | 在人工确认、金额限制和无杠杆条件下评估真实现货交易 |
| P7 | 生成 GACE App 候选并完成构建中心合同验证 |
| P8 | 等 GACE 动态 Runtime 具备真实验收条件后完成 App 安装、AI 和生命周期闭环 |

### 02.3 成功标准

系统成功不以能下单为唯一标准，而以以下结果为标准：

- 可明确说明每条行情的交易所、品种、市场类型和时间来源；
- 可用订单簿深度计算真实可成交价差；
- 回测可复现，数据、策略、参数、成本和制品均有指纹；
- 模拟盘能够处理部分成交和单腿失败；
- 真实账户接入默认只读；
- 真实交易有独立的风险开关和审计记录；
- 独立模式不依赖 GACE；
- GACE 模式不需要修改 GACE Core；
- GACE AI 只能调用已声明、已授权、已审计的 Action。

---

## 03. 用户角色与核心流程

### 03.1 用户角色

1. 研究用户：查看行情、研究价差和运行回测。
2. 策略用户：创建、参数化和比较策略。
3. 模拟交易用户：观察虚拟账户、订单、成交和风险。
4. 交易管理员：管理交易所连接、限额、风险开关和运行状态。
5. GACE 用户：从 GACE App 入口使用系统。
6. GACE AI：通过受控 Action 查询和执行明确操作。

### 03.2 核心流程

#### 流程 A：行情观察

选择交易所、市场类型、交易对和时间范围，系统展示统一后的盘口、成交、价差、数据新鲜度和交易所状态。

#### 流程 B：价差研究

系统读取多个市场的可成交买卖盘，扣除成本后生成机会记录，并展示：

- 买入市场；
- 卖出市场；
- 可成交数量；
- 理论毛价差；
- 费用和滑点；
- 预计净价差；
- 行情延迟；
- 资金库存是否满足；
- 风险阻断原因。

#### 流程 C：历史回测

选择策略、交易所、交易对、时间范围、数据级别、费用和滑点模型，生成可复现的回测报告。

#### 流程 D：模拟交易

系统为每个交易所建立虚拟账户，使用实时或回放订单簿模拟挂单、吃单、部分成交、单腿失败、库存和风险。

#### 流程 E：真实交易

默认流程为：

~~~text
只读账户
→ 绑定白名单交易对
→ 风险参数预览
→ 模拟运行
→ 人工启用交易
→ 小额现货
→ 对账
→ 可随时安全暂停
~~~

#### 流程 F：GACE AI

AI 查询市场、读取回测、生成策略草案或提交模拟任务。真实下单、启用实盘策略和取消订单必须经过高风险确认。

---

## 04. 市场范围与接入策略

### 04.1 市场层次

本项目把市场拆成三个维度：

1. Venue：交易所或链上市场；
2. Market Type：现货、永续、交割、DEX；
3. Instrument：具体交易对或合约。

不允许只用一个字符串例如 BTCUSDT 表示完整品种。系统必须使用交易所命名空间，例如：

~~~text
venue=binance
market_type=spot
base_asset=BTC
quote_asset=USDT
native_symbol=BTCUSDT
canonical_symbol=BTC/USDT
~~~

### 04.2 第一阶段范围

第一阶段只做：

- 中心化交易所；
- 现货；
- USDT 和 USDC 等主流计价资产；
- 少量高流动性、多个交易所共同存在的交易对；
- 公开行情；
- 历史回测；
- 模拟交易。

交易所候选包括 Binance、OKX、Bybit 等，最终名单必须根据账户可用性、地区限制、官方条款、API 稳定性和数据质量确认，不能把候选名单视为已经接入。

第一批交易对建议从 BTC、ETH、SOL 等共同且流动性较好的交易对中选择。BNB 可以观察，但不应因为它是 Binance 生态资产就默认适合作为跨交易所首批套利标的。

### 04.3 后续范围

第二阶段可评估：

- 同交易所三角套利；
- 现货与永续合约基差；
- 跨交易所合约价差；
- 资金费率策略。

第三阶段再评估：

- DEX；
- 链上流动性池；
- CEX 与 DEX 价差；
- Gas、MEV、RPC、Nonce 和桥接风险。

### 04.4 不把稳定币视为绝对一美元

USDT、USDC 等应作为独立资产建模。系统不能固定假设：

~~~text
1 USDT = 1 USD
~~~

计价资产也可能出现脱锚、不同交易所报价差异、兑换成本和提现限制。价差计算必须允许稳定币价格偏差参数。

### 04.5 地区与合规边界

交易所可用性、开户、KYC、API、出入金和数字资产交易规则因地区和时间而变化。系统只提供技术能力，不绕过交易所或地区限制。真实交易前必须由使用者确认账户、地区和平台条款。

### 04.6 本章产出

- Venue Registry；
- Market Type Registry；
- Instrument Registry；
- 第一阶段交易所和交易对白名单；
- 地区和平台可用性检查表。

---

## 05. 总体架构原则

### 05.1 双目标单内核

业务核心只处理数字资产领域问题，不知道自己运行在 Standalone 还是 GACE：

~~~text
业务核心
  ├─ 行情模型
  ├─ 机会模型
  ├─ 策略模型
  ├─ 回测模型
  ├─ 模拟撮合
  ├─ 交易账本
  └─ 风控状态机

平台端口
  ├─ IdentityPort
  ├─ SecretPort
  ├─ StoragePort
  ├─ TaskPort
  ├─ NotificationPort
  └─ LifecyclePort

适配器
  ├─ StandaloneAdapter
  └─ GaceAdapter
~~~

### 05.2 控制平面与执行平面分离

建议区分：

- 控制平面：用户界面、策略配置、回测、权限、风险参数和审计；
- 行情平面：持续连接交易所并维护行情状态；
- 执行平面：管理账户、订单、成交、对账和风险；
- 数据平面：历史归档、回放和报表。

GACE 优先承载控制平面和研究功能。行情平面与真实执行平面可以独立运行。

### 05.3 依赖倒置

领域层不能直接：

- 请求交易所 HTTP API；
- 读取环境变量中的 Secret；
- 写 Redis；
- 调用 GACE API；
- 写用户界面状态；
- 直接执行 Docker 或系统命令。

所有外部能力都必须通过端口注入。

### 05.4 可重放和可解释

任何一次机会、策略信号、模拟成交和真实订单都应能回答：

- 使用了哪些数据；
- 数据在何时进入系统；
- 数据是否过期；
- 使用了哪一个策略版本；
- 使用了哪些参数；
- 使用了哪个费用和滑点模型；
- 为什么允许或阻断；
- 最终采取了什么动作。

---

## 06. 系统总体架构

### 06.1 逻辑结构

~~~mermaid
flowchart LR
    U[独立 Web UI] --> API[Crypto API]
    G[GACE App] --> GA[GACE Adapter]
    GA --> API
    AI[GACE AI] --> AB[AI Action Broker]
    AB --> API

    API --> R[领域核心]
    R --> O[机会扫描器]
    R --> S[策略运行时]
    R --> B[回测与回放]
    R --> P[模拟撮合]
    R --> X[风险与执行编排]

    V1[交易所 A] --> C[Venue Connectors]
    V2[交易所 B] --> C
    V3[交易所 C] --> C
    C --> N[统一行情模型]
    N --> Q[实时事件流]
    N --> H[历史数据存储]
    Q --> O
    Q --> S
    Q --> P
    X --> C
    X --> L[不可变交易账本]
    API --> D[审计与任务]
    R --> D
    D --> NT[GACE 通知或独立通知]
~~~

### 06.2 核心服务建议

第一版可拆分为：

1. crypto-api：用户界面 API、研究 API、状态查询；
2. market-collector：交易所公共行情 WebSocket/REST；
3. market-normalizer：品种、盘口、成交和状态统一；
4. opportunity-engine：可执行价差、成本和阻断判断；
5. research-engine：回测、回放、指标和报告；
6. paper-execution：模拟订单和成交；
7. account-execution：真实账户只读和后续交易执行；
8. risk-service：限额、熔断、策略状态和安全暂停；
9. data-service：历史归档、清洗和回放；
10. task-service：长任务、进度、取消、恢复和审计。

服务是否拆成独立进程，应由数据量和故障隔离决定，不为满足容器数量而拆分。

### 06.3 第一阶段简化方案

为了避免一开始过度工程化，P1-P4 可以先采用：

- 一个 API 服务；
- 一个行情采集 Worker；
- 一个研究和回放 Worker；
- PostgreSQL；
- Redis 或数据库任务队列；
- 独立数据目录；
- 交易所连接器使用明确接口。

真实执行前再拆分执行服务和密钥边界。

---

## 07. 功能模块设计

### 07.1 行情中心

功能：

- 交易所连接状态；
- 交易对目录；
- Ticker；
- 买一卖一；
- L2 订单簿；
- 成交；
- K 线；
- 数据延迟；
- 断线重连；
- 快照与增量校验；
- 交易所维护状态。

### 07.2 市场与品种中心

功能：

- Venue；
- Market Type；
- Instrument；
- 资产和计价资产；
- 精度；
- 最小数量；
- 最小名义金额；
- 价格和数量步长；
- 交易状态；
- 费率快照；
- 原生代码与标准代码映射。

### 07.2a 当前公开品种目录实现

当前已将 Binance、Bybit、OKX 的公开 Spot 品种接口接入独立后端。`GET /api/v1/market/instruments` 按交易所、计价资产和搜索词返回标准交易对，并统一输出原生符号、价格最小变动、数量步长、最小数量和最小名义金额。交易状态不是永久配置：每次刷新只纳入交易所当时标记为可交易的现货品种；公开接口未提供的约束显示为未提供，不推断为可下单。

服务端使用有界本地 JSON 缓存。公开接口成功时状态为 `LIVE`，命中未过期缓存时为 `CACHED`，网络、TLS、限流或上游错误且无可用缓存时为 `BLOCKED`，不生成占位品种。当前 Windows 现场目录读取结果为 Binance 491 个、Bybit 288 个 USDT 现货品种，OKX 为真实网络/TLS 阻断；该结果是本次运行证据，不代表 Ubuntu 出口一定相同。

### 07.3 机会中心

功能：

- 跨交易所现货价差；
- 同所三角路径；
- 现货与合约基差；
- 机会生命周期；
- 机会信号过期；
- 可成交数量；
- 成本拆分；
- 阻断原因；
- 机会告警。

### 07.4 策略中心

功能：

- 策略注册；
- 策略版本；
- 参数 Schema；
- 数据需求；
- 运行模式；
- 回测；
- 模拟；
- 实盘启用；
- 策略状态；
- 策略审计。

### 07.5 回测中心

功能：

- 数据集选择；
- 数据集指纹；
- 参数保存；
- 费用与滑点模型；
- 延迟模型；
- 订单簿深度模拟；
- 多交易所同步；
- 结果报告；
- 结果归档；
- 可重复运行。

### 07.6 模拟交易中心

功能：

- 每个交易所独立虚拟账户；
- 虚拟资产和现金；
- 模拟订单；
- 模拟成交；
- 部分成交；
- 单腿失败；
- 库存变化；
- 资金调拨模拟；
- PnL；
- 交易费用；
- 风险告警。

### 07.7 账户与执行中心

功能：

- API Key 配置引用；
- 账户余额；
- 订单；
- 成交；
- 持仓；
- 订单对账；
- 交易所状态对账；
- 受控下单；
- 取消订单；
- 安全暂停。

### 07.8 任务与通知中心

功能：

- 历史数据任务；
- 回测任务；
- 模拟任务；
- 机会告警；
- 策略异常；
- 账户异常；
- 任务进度；
- 取消与恢复；
- 审计。

### 07.9 运行计划

运行计划是 24/7 数字资产市场的状态聚合视图，不复制 A 股的开盘、收盘和交易日历。它按历史数据、策略配置、研究/回测、模拟盘、风险门禁、真实执行和 GACE 适配七个节点展示当前条件，并把任务、风险阻断和只读能力清单放在同一条链路上。运行计划只读，不是调度器，不是交易授权。

### 07.10 策略孵化池与筛选联动

策略孵化池是指标筛选到组合研究之间的中间层。它以 `dataset_id` 作为候选唯一键，保留交易所、交易对、周期、筛选评分、收益、波动、回撤和来源筛选记录；相同候选重复写入时更新而不是复制。孵化池只保存研究候选，不属于可交易币池，也不产生订单。

当前状态：`implemented`。筛选中心可以把当前通过项写入孵化池；孵化池页面支持按来源筛选记录查看、候选阶段统计和跳转组合研究。进入研究后仍重新经过来源筛选、历史质量、策略启用和风控门禁。该中间层不改变 `system.research.cross_venue`、`system.backtest.verified` 等原有币池的职责。

---

## 08. 领域模型与数据设计

### 08.1 核心实体

建议实体包括：

| 实体 | 作用 |
|---|---|
| Venue | 交易所或市场来源 |
| Market | 交易所下的市场类型 |
| Instrument | 标准化交易品种 |
| Asset | BTC、ETH、USDT 等资产 |
| OrderBookSnapshot | 盘口快照 |
| OrderBookDelta | 盘口增量 |
| TradeTick | 成交事件 |
| Candle | K 线 |
| FundingRate | 资金费率 |
| FeeSchedule | 费率快照 |
| Account | 交易所账户 |
| Balance | 账户资产余额 |
| Order | 委托 |
| Fill | 成交 |
| Position | 持仓 |
| LedgerEntry | 不可变账本条目 |
| Strategy | 策略定义 |
| StrategyRun | 策略运行 |
| Opportunity | 价差机会 |
| RiskEvent | 风险事件 |
| BacktestRun | 回测运行 |
| PaperAccount | 模拟账户 |
| Task | 长任务 |
| AuditEvent | 审计事件 |

### 08.2 时间字段

行情和订单事件至少区分：

- exchange_timestamp：交易所时间；
- event_timestamp：事件产生时间；
- received_timestamp：系统收到时间；
- persisted_timestamp：系统持久化时间；
- processed_timestamp：策略处理时间。

不能用本地收到时间替代交易所事件时间，也不能在回测中丢弃数据延迟信息。

### 08.3 精度和金额

所有价格、数量、费用和余额使用 Decimal 或等价的精确表示。数据库不得用二进制浮点作为资金账本的唯一表示。

每个订单必须保存：

- 原始价格；
- 原始数量；
- 规范化后的价格；
- 规范化后的数量；
- 交易所精度；
- 最小名义金额；
- 手续费资产；
- 手续费金额；
- 订单状态来源。

### 08.4 账本原则

余额、订单、成交和费用采用追加式事件记录，报表余额可以由事件重算。系统不能只更新一个当前余额字段而丢失历史变化。

本地状态与交易所状态发生冲突时：

1. 标记对账异常；
2. 暂停扩大仓位；
3. 读取交易所真实订单和成交；
4. 生成差异报告；
5. 经过规则或人工确认后修正本地投影。

### 08.5 数据隔离

- 公共行情数据：项目数据区；
- 研究数据：研究任务区；
- 模拟账户：模拟账户区；
- 真实账户和订单：受限交易区；
- API 密钥：Secret Broker 或独立密钥存储；
- GACE 用户映射：只保存外部主体映射，不共享 GACE 主库。

---

## 09. 行情采集与历史数据计划

### 09.1 数据来源

每个 Venue Connector 支持：

- REST 元数据；
- REST 历史数据；
- WebSocket 实时数据；
- 账户 API；
- 订单 API；
- 交易所状态 API；
- 费率 API。

公开行情和账户交易接口必须分离，不能因为行情接口失败就尝试使用账户权限。

### 09.2 数据级别

按回测目标分级：

1. K 线：适合趋势、均线、波动率等低频策略；
2. 成交级别：适合成交回放和更细的交易模拟；
3. L2 订单簿：适合盘口价差、深度和部分成交模拟；
4. L3 或逐订单数据：只有在来源真实可得且必要时评估。

跨市场短周期套利不能只用 K 线回测。

### 09.3 数据质量规则

系统必须检查：

- 时间是否单调；
- 快照与增量是否连续；
- 是否有丢包；
- 是否有重复；
- 买卖盘是否交叉异常；
- 数量和价格是否符合精度；
- 交易所是否处于维护状态；
- 数据是否超过最大允许延迟；
- 不同来源是否发生冲突。

### 09.3a 当前公开行情采集编排

当前第一步实现为 `PublicMarketCollector` 和独立 `services.market_worker`：

1. 先通过公开 REST 获取并校验品种和盘口快照；
2. 建立 Binance 或 OKX 的公开 WebSocket 连接；
3. 将原生事件标准化为 `OrderBookDelta` 或 WS 快照；
4. 由 `OrderBookReconstructor` 执行交易所原生序列和交叉盘口门禁；
5. 将通过门禁的重建盘口交给 `MarketDataService`，并写入独立运行目录中的共享状态文件；
6. 发现序列缺口、过期或非法盘口时停止当前流并重新获取 REST 快照；
7. 网络或流失败时保留断开状态并按重连策略恢复。

当前阶段使用 `FileMarketStateStore` 将规范化盘口和连接状态以原子 JSON 文件写入 `CRYPTO_MARKET_STATE_PATH`：Worker 使用读写挂载，API 使用只读挂载。它只解决开发阶段的跨进程最新状态共享，不等同于历史归档、PostgreSQL/Redis 或生产级高频存储。Compose 通过 `market` profile 暴露 Worker，并默认不启用；只有完成 Ubuntu 公网 DNS/路由后验后才允许启动真实公开行情。

### 09.4 历史数据任务

历史任务要支持：

- 按 Venue；
- 按 Market Type；
- 按 Instrument；
- 按时间区间；
- 增量补齐；
- 断点续传；
- 重试上限；
- 任务取消；
- 来源记录；
- 数据集指纹；
- 覆盖率报告。

### 09.4a 当前历史与品种入口

历史数据中心已经可以通过 `HistoryQuery` 接受任意标准现货交易对；交易对由公开品种目录发现后，按交易所原生符号转换并进入现有下载任务。任务请求使用指纹去重，页面可查看逐项状态、停止进行中的任务，并对阻断、失败、部分完成、取消或进程中断的未完成项发起重试。当前本地真实历史已覆盖 18/27 条序列、747,865 根 K 线，并能读取 Binance BTC/USDT 1h/5m 尾部 K 线；Ubuntu 最新独立运行端后验为 18 个数据集、749,467 根 K 线和 18/18 Parquet。5m 研究、回测和筛选读取完整已校验范围，不再固定截断到 100,000 根。目录发现与历史归档仍是两个步骤：发现品种不等于已经拉取历史数据，历史数据不等于有实时盘口或交易权限。

本阶段新增历史持久化切片：成功分页响应按 `history-raw-response-v1` 形成无凭据 JSON envelope 并原子写入 `_raw`；`history_dataset_records` 以单事务记录数据集、原始响应引用和 Parquet 状态，并保留旧 `history_dataset_metadata` 兼容投影。提交顺序固定为 CSV/Manifest、Raw、PostgreSQL、Parquet 状态；数据库失败保留可重建文件，Parquet 失败标记 `DEGRADED`，启动协调可恢复。该切片已由故障测试验证，尚未替代 L1/L2 长期归档或对象存储。

### 09.5 存储建议

Standalone 第一版建议：

- PostgreSQL：配置、元数据、订单、账本、任务、审计；
- Redis：实时状态、任务协调和短期事件；
- Parquet 或等价列式文件：大量历史行情和回放数据；
- 本地或对象存储：原始归档和报告。

历史 K 线还必须保留原始公开响应的可审计 envelope，并在 PostgreSQL 中维护正式数据集记录。原始响应不保存认证头、签名、Cookie 或密钥；数据库记录文件、Manifest、Raw 和 Parquet 的状态，不能只看某一个文件是否存在。

数据量增长后再评估 TimescaleDB、ClickHouse 或专用事件存储。不能一开始为了未来容量引入多个未验证基础设施。

### 09.6 本章验收

- 能接收实时行情；
- 断线后不会静默产生错误盘口；
- 能回放固定数据集；
- 数据集指纹可重复；
- 丢包、重复、乱序和过期数据能被识别；
- 历史数据任务可以安全恢复。
- CSV/Manifest、原始响应和 PostgreSQL 元数据提交失败可识别并可重建；Parquet 失败不会被误报为完整归档。

---

## 10. 策略模型与策略运行时

### 10.1 策略类型

第一阶段支持：

1. 市场观察策略：统计价差、波动、深度和延迟；
2. 跨交易所现货价差策略；
3. 同所三角套利研究策略；
4. 低频趋势或动量策略；
5. 现货与合约基差研究策略。

趋势策略可以借鉴现有 A 股系统的计算思想，但不能直接带入 A 股执行规则。

### 10.2 策略输出不是直接下单

策略只输出标准化意图，例如：

~~~text
OpportunityIntent
  venue_buy
  venue_sell
  instrument
  target_quantity
  max_buy_price
  min_sell_price
  expiry
  expected_net_edge
  risk_budget
  idempotency_key
~~~

策略不能直接：

- 调用交易所；
- 读取 API Key；
- 修改余额；
- 绕过风险检查；
- 自己决定是否执行真实下单。

### 10.3 运行模式

每个策略运行必须明确模式：

~~~text
observe
research
backtest
paper
live-pending
live-enabled
paused
stopped
~~~

策略代码不能通过环境变量偷偷从 paper 切换到 live。

### 10.4 策略版本与参数

每次运行绑定：

- 策略 ID；
- 策略版本；
- 参数 JSON；
- 参数 Schema；
- 数据集指纹；
- 费用模型版本；
- 滑点模型版本；
- 延迟模型版本；
- 运行模式；
- 运行主体；
- 运行时间。

### 10.5 策略插件边界

策略插件只接收系统注入的数据和上下文，不得自主打开数据库、网络或修改平台状态。

---

## 11. 跨市场价差与执行编排

### 11.1 可执行价差

系统使用盘口而不是最新成交价计算：

~~~text
gross_edge =
sell_bid_value
- buy_ask_value

net_edge =
gross_edge
- buy_fee
- sell_fee
- expected_slippage
- inventory_cost
- transfer_cost
- latency_buffer
- failure_risk_buffer
~~~

所有金额必须与实际可成交数量绑定。不能用买一卖一的单个价格乘以目标总量而忽略订单簿深度。

### 11.2 资金预置

跨交易所现货套利通常需要：

- 低价市场预置计价资产；
- 高价市场预置基础资产；
- 两边同时或近同时执行；
- 后续进行库存再平衡。

系统不能把交易后再转币当成实时套利的默认路径。转账时间、网络拥堵、提现限制和到账确认都必须单独建模。

### 11.3 两腿执行状态机

建议状态：

~~~text
detected
→ validated
→ reserved
→ leg-a-submitted
→ leg-a-filled
→ leg-b-submitted
→ completed
~~~

异常分支：

~~~text
partial-fill
leg-timeout
leg-rejected
hedge-required
reconciliation-required
manual-review
aborted
~~~

### 11.4 单腿失败处理

系统必须预先定义：

- 另一腿是否立即补单；
- 是否允许价格保护范围；
- 是否使用对冲订单；
- 最大未对冲时间；
- 最大未对冲数量；
- 是否暂停该交易所；
- 是否进入人工审核；
- 是否取消剩余订单。

不能由异常处理代码临时猜测。

### 11.5 机会失效

机会必须有：

- 产生时间；
- 过期时间；
- 数据新鲜度阈值；
- 盘口最小深度；
- 允许的最大滑点；
- 最低净价差；
- 可用资金检查；
- 交易所状态检查。

超过任一阈值，机会自动失效。

### 11.6 第一阶段执行边界

第一阶段只实现：

- 机会发现；
- 模拟两腿执行；
- 单腿失败仿真；
- 订单生命周期；
- 风险阻断；
- 真实账户只读。

真实下单属于后续独立阶段，不和行情功能同时开放。

### 11.7 历史价差研究实现

历史价差研究是可复现的研究工具，不是自动套利器。请求指定交易对、买入市场、卖出市场、周期、时间窗口、单腿费率、单腿滑点和最低净价差阈值；服务从两个已验证 Manifest 数据集读取完整范围，按相同 `open_time` 对齐，再计算：

~~~text
gross_spread_pct = sell_close / buy_close - 1
net_spread_pct = gross_spread_pct - 2 * (fee_bps + slippage_bps) / 100
~~~

结果包含对齐根数、对齐率、正价差次数、阈值机会次数、平均/最大/最小净价差、近期观察点和峰值观察点，并固定返回 `research_only=true`。缺少数据集、数据质量门禁失败、时间范围无交集或存在非正价格时直接阻断，不生成部分结果。历史收盘价不替代 L2 买一卖一、深度、库存、转账、延迟和两腿成交，因此不得直接转为订单。

当前实现：`src/application/spread_research.py`、`GET /api/v1/market/spread-history` 和行情中心历史价差研究表单；真实执行仍受 `DISABLED` 和风险服务端门禁约束。

---

## 12. 回测、回放与模拟交易

### 12.1 三类回测

1. K 线回测：验证低频策略逻辑；
2. 成交回放：验证成交时序和费用；
3. L2 回放：验证盘口深度、价差和部分成交。

报告必须标明使用的数据级别，不能把 K 线回测结果描述成盘口套利结果。

### 12.2 回测必备参数

- 初始资产；
- 每个 Venue 的初始库存；
- 交易对；
- 时间范围；
- 数据集指纹；
- 手续费；
- 费率等级；
- 滑点；
- 延迟；
- 订单类型；
- 部分成交规则；
- 资金转移假设；
- 稳定币偏差；
- 失败腿处理规则。

### 12.3 防止未来数据

策略只能使用当前事件及之前的数据：

- 不使用未来盘口；
- 不使用回测结束后的费率；
- 不使用事后修正的交易所状态；
- 不把收到时间排序替代事件时间；
- 不用完整日线的收盘信息决定盘中订单。

### 12.4 模拟撮合

模拟撮合至少支持：

- 吃单；
- 挂单；
- 盘口深度；
- 部分成交；
- 排队近似；
- 订单过期；
- 价格保护；
- 断线；
- 限频；
- 单腿失败；
- 对账差异。

### 12.5 结果指标

除总收益外，还要统计：

- 净收益；
- 手续费；
- 滑点；
- 成交率；
- 机会捕获率；
- 两腿完成率；
- 单腿失败次数；
- 最大未对冲时间；
- 最大未对冲数量；
- 库存周转；
- 各交易所敞口；
- 最大回撤；
- 数据过期次数；
- API 失败次数；
- 订单对账差异。

### 12.6 模拟盘与实盘分离

模拟账户、模拟订单和真实订单必须使用不同的数据表、不同的状态机和不同的权限。禁止只通过一个 is_paper 字段保护实盘。

---

## 13. 账户、风险、安全与生命周期

### 13.1 API Key 安全

真实交易所 API Key 必须：

- 只允许读取和交易；
- 禁止提币；
- 使用交易所 IP 白名单；
- 加密存储；
- 不进入前端；
- 不进入日志；
- 不进入镜像；
- 不进入 GACE App 包；
- 不通过 URL 参数传递；
- 轮换时可以撤销旧凭据。

### 13.2 风险限制

至少需要：

- 单交易所最大资产；
- 单交易对最大库存；
- 单次最大名义金额；
- 单日最大亏损；
- 单策略最大敞口；
- 最大未对冲时间；
- 最大未对冲数量；
- 最大挂单数量；
- 最大 API 错误次数；
- 最大数据延迟；
- 交易所状态熔断；
- 稳定币偏差阈值。

### 13.3 安全暂停

安全暂停后：

- 禁止新开订单；
- 允许完成必要对账；
- 按策略执行取消订单或保留订单；
- 不自动清算持仓；
- 保留订单和成交记录；
- 记录暂停原因；
- 需要明确动作才能恢复。

### 13.4 GACE 停止与升级

GACE 的 stop、upgrade、uninstall 不能直接等同于删除或平仓。Crypto 生命周期必须定义：

~~~text
收到停止请求
→ 禁止新增风险
→ 进入安全暂停
→ 等待或处理在途订单
→ 对账
→ 撤销临时授权
→ 停止服务
~~~

如果对账状态未知，应进入 reconciliation-required，不能自动重新启动真实执行。

### 13.5 真实交易开关

真实交易至少需要：

- 用户确认；
- 当前用户身份；
- 当前版本策略；
- 交易对和金额白名单；
- 风险参数快照；
- 短时授权；
- 幂等键；
- 审计记录。

---

## 14. 独立运行模式

### 14.1 目标

Standalone 模式必须不依赖 GACE，能够独立完成：

- 用户登录；
- 行情；
- 数据任务；
- 回测；
- 模拟盘；
- 只读账户；
- 风险和审计；
- 后续受控交易。

### 14.2 独立部署

初期采用自己的：

- Web；
- API；
- Worker；
- PostgreSQL；
- Redis 或任务队列；
- 数据存储；
- Secret 配置；
- 日志；
- 备份；
- Compose 或等价部署文件。

所有容器名、网络、卷名和端口使用 Crypto 专属命名，不使用现有量化系统或 GACE 命名。

当前部署边界：

- Windows `D:\code\crypto-platform` 是源码、测试和构建输入端；
- Ubuntu `10.10.10.129` 是独立运行端，系统版本核验为 Ubuntu 24.04.4 LTS，运行用户为 `flybace`；
- Ubuntu 已核验 Docker 29.1.3、Docker Compose 2.40.3、Python 3.12.3 和约 89 GB 根分区可用空间；
- 现有 A 股量化容器使用独立项目和端口，Crypto Compose 使用可配置的 `CRYPTO_BIND_ADDRESS`；运行默认发布 `10.10.10.129:8290`/`10.10.10.129:4191`，本地 Compose 开发必须显式覆盖为回环地址，不占用 A 股既有端口；
- 首次部署已通过受保护的 SSH 会话传输源码并在 Ubuntu 构建独立镜像，后续应改为 SSH Key、签名制品或构建中心制品；
- 用户提供的 SSH 密码属于带外 Secret，只用于连接，不写入计划书、源码、配置、日志、镜像或 Git。

当前运行拓扑：

~~~text
Windows 源码/测试
    │ 受保护传输或签名制品
    ▼
Ubuntu 10.10.10.129
    └─ crypto-platform Compose
       ├─ backend: CRYPTO_BIND_ADDRESS:8290 → container:8000
       ├─ frontend: CRYPTO_BIND_ADDRESS:4191 → container:8080
       ├─ task-scheduler: 启动即检查，按周期规划历史增量同步
       ├─ task-worker: Redis 队列消费与历史落盘
       └─ runtime/market: Worker 写入，API 只读
~~~

第一阶段 Ubuntu 的目标运行态为新版 `backend`、`frontend`、`task-worker` 和 `task-scheduler`，`execution_mode=DISABLED`；Compose 通过 `CRYPTO_BIND_ADDRESS` 发布，目标 `.env` 配置为 `10.10.10.129`，用于受控局域网和 FRP 转发。历史 Scheduler 自动规划增量同步，心跳和退避以 `crypto.runtime.*` PostgreSQL 快照为主并保留 JSON 恢复副本；行情 Worker 在源码中仍是显式 opt-in，本轮运行端通过受控配置额外启用 Bybit 公共 L2，未启用私有 API 或交易能力。本轮已完成重新部署和独立后验，运行说明见 `docs/RUNTIME_DEPLOYMENT.md`。

当前运行后验（2026-09-16）：

- Compose 配置校验通过，`crypto-platform/backend:0.1.0` 和 `crypto-platform/frontend:0.1.0` 均在 Ubuntu 独立构建并启动；
- `crypto-platform-backend-1` 为 `running/healthy`，`crypto-platform-frontend-1`、`task-worker` 和 `task-scheduler` 为 `running`；宿主已绑定 `10.10.10.129:8290` 和 `10.10.10.129:4191`；
- Ubuntu 项目目录当前已有 18 个 CSV 和 18 个 Manifest；本轮独立后验确认 18 个数据集、749,467 根 K 线，全部 `gap_count=0`、`duplicate_count=0`，Parquet 为 18/18 READY；
- Ubuntu 本机 Python `urllib` 后验 `/health` 返回 HTTP 200、执行模式为 `DISABLED`，认证登录返回 HTTP 200，前端首页返回 HTTP 200；历史同步周期为 900 秒，当前持久化状态为 `backoff`，任务账本和结果归档接口均可读；
- Ubuntu PostgreSQL 直接查询确认 `control_domain_snapshots` 为 10 条（8 条领域快照和 2 条 `crypto.runtime.*` 运行态快照）且状态为 `ready`；独立 task-worker 读取 `crypto.domain.strategies` 版本 1，证明跨进程 SQL 主存储可用；
- Ubuntu 只读公网探测中 Binance 和 Bybit K 线接口返回 HTTP 200，OKX `www.okx.com` K 线接口超时；没有为 OKX 生成占位或合成历史数据；
- 真实浏览器直接访问 `http://10.10.10.129:4191/` 已验收登录和运行计划页；页面显示 `18/27`、`749,467`、`safe_paused`、`DISABLED` 和 26 个只读能力，390px 视口无横向溢出，控制台错误和警告均为 0；页面继续显示真实执行关闭；
- A 股 `quant-platform` 仍是独立 Compose 项目，远端后验显示其 14 个服务运行；新项目没有复用其数据库、Redis、卷、网络或 Secret；
- 源码默认关闭 market-worker；本轮 Ubuntu 通过显式配置启用 Bybit BTC/ETH/BNB 公共 L2，三组 50 档快照和序列增长已后验；Binance/OKX 实时通道仍未启用，OKX 公网请求仍超时，因此不伪造 OKX 历史；实时行情长期归档和新增历史任务仍需持续后验；
- 此后验覆盖新版只读/研究运行时、已同步历史数据挂载、认证、领域快照和浏览器入口，不覆盖真实行情、账户、交易、生产用户库或 GACE Runtime。

### 14.3 独立环境

至少区分：

~~~text
dev
replay
paper
live-readonly
live-trading
~~~

每个环境使用独立数据库和数据目录。开发环境不得读取真实交易凭据。

### 14.4 独立模式验收

- 停止 GACE 后系统仍能运行；
- 不读取 GACE 数据库；
- 不读取 A 股量化数据库；
- 回测和模拟可重复；
- 真实账户只读模式可以独立关闭；
- 运行日志不泄露 Secret。

---

## 15. GACE App 兼容与构建中心计划

### 15.1 兼容目标

GACE 兼容不是把页面放到一个 iframe，而是让 App 具备：

- GACE App 身份；
- GACE 认证入口；
- GACE 权限；
- GACE Secret 引用；
- GACE 任务和通知；
- GACE AI Action；
- GACE 生命周期；
- GACE 构建中心可生成的制品；
- GACE 可验证的升级和回滚合同。

### 15.2 适配方式

新项目保留一个 gace-app 适配目录，内容包括：

- manifest.json；
- gace-app.json；
- gace-migration-contract.json；
- gace-multi-container-build.json；
- App 自有 AI Action Schema；
- App 自有 Notification Schema；
- GACE 运行时合同；
- 构建和来源证明；
- SBOM；
- 许可证清单；
- 发布记录。

这些文件描述如何把 Crypto 交付给 GACE，不承载核心交易逻辑。

### 15.3 Runtime 选择

Crypto 系统有后台行情、执行、持久数据和外部网络，概念上更接近：

- web-service；
- docker-oci/v1；
- 或 multi-container-app/v1。

web-static/v1 不适合完整 Crypto 系统，因为静态 App 的网络和后台能力不足。

最终 Profile 必须以 GACE 构建中心和运行时正式合同为准，不能在当前合同未冻结时提前声称已兼容。

### 15.4 GACE 三种运行形态

#### 形态 A：独立完整系统

完整 Crypto 系统独立运行，适合研究、模拟和后续真实执行。

#### 形态 B：GACE 控制台 App

GACE 提供入口、身份、AI、通知和管理界面，行情与真实执行核心继续独立运行。这是实盘套利最推荐的形态。

#### 形态 C：GACE 完整托管

GACE 管理 Crypto 的 Web、API、行情 Worker、策略 Worker 和执行服务。适合在动态 Runtime、Secret、网络、卷、升级和回滚全部验收后，再用于模拟盘或低频交易。

### 15.5 构建中心输入

正式构建必须绑定：

- Crypto 产品 Commit；
- 构建中心 Commit；
- 固定源码归档摘要；
- 工具链摘要；
- App 包摘要；
- OCI 镜像摘要；
- SBOM 摘要；
- 测试和证据摘要。

如果当前构建中心只接受 GACE 产品系统输入，则必须为第三方 App 定义正式输入类型，不能把 Crypto 源码伪装成 gace-system 产品源码。

### 15.6 构建中心交付链

~~~text
Crypto 固定 Commit
→ GACE App 输入校验
→ 构建 Web / API / Worker 镜像
→ 测试
→ SBOM / 许可证 / Secret / 漏洞检查
→ 固定摘要
→ 签名
→ testing 软件源
→ GACE 安装
→ 启动 / 网络 / 数据 / AI / 生命周期验收
→ stable 或回滚
~~~

### 15.7 当前 GACE 依赖风险

当前 GACE 和构建中心仍处于开发和兼容基线阶段。服务型 App 的合同要求固定镜像、隔离网络、Secret 引用、私有数据卷、健康检查、升级和回滚；动态服务的真实安装能力还必须由目标环境验证。

因此本项目采取：

1. 先独立建设 Crypto Core；
2. 同步写 GACE 适配合同；
3. 使用 Developer Kit 和本地校验器提前发现问题；
4. 使用假的交易所或回放数据做 App 测试；
5. 等 GACE 动态 Runtime 稳定后再做完整托管；
6. 任何时候都不修改 GACE Core 来绕过构建中心门禁。

### 15.8 GACE 兼容分级

| 级别 | 含义 |
|---|---|
| C1 | 清单、合同和权限结构符合 |
| C2 | 构建中心能够生成候选制品 |
| C3 | 候选完成签名、SBOM 和供应链验证 |
| C4 | GACE testing 能导入、安装、启动 |
| C5 | GACE 实际完成数据、网络、AI、升级和回滚验收 |
| C6 | stable 交付并有可恢复证据 |

只有达到 C5 才能称为实际兼容 GACE App，达到 C1 或 C2 只能称为合同兼容或候选可构建。

---

## 16. GACE AI 能力接入计划

### 16.1 AI 接入原则

GACE AI 通过已声明的版本化 Action 调用 Crypto App。AI 不直接访问：

- Crypto 数据库；
- 交易所 API Key；
- 其他用户数据；
- 任意内部 HTTP 路由；
- Docker；
- 宿主机；
- 未声明的订单接口。

### 16.2 Action 分级

#### 只读 Action

- market.opportunities.read；
- market.orderbook.read；
- account.balance.read；
- account.orders.read；
- strategy.status.read；
- backtest.result.read；
- risk.events.read。

#### 受控写 Action

- strategy.draft.create；
- strategy.parameters.update；
- paper.run.start；
- backtest.run.start；
- market.watchlist.update；
- alert.rule.create。

#### 高风险执行 Action

- live.strategy.enable；
- live.order.submit；
- live.orders.cancel_all；
- live.trading.pause；
- account.connection.rotate。

高风险 Action 必须要求：

- 当前用户身份；
- Action 输入 Schema；
- 当前策略和版本；
- 金额和交易对白名单；
- 明确确认；
- 幂等请求 ID；
- 审计记录。

### 16.3 AI 适合的第一批功能

优先让 AI 做：

1. 解释不同交易所之间的价差；
2. 读取机会和风险阻断原因；
3. 运行回测；
4. 比较不同参数；
5. 创建模拟策略；
6. 汇总账户和模拟盘状态；
7. 发送和解释告警。

暂不允许 AI 默认自动执行真实订单。

### 16.4 长任务

回测、历史同步和大范围机会扫描通过任务系统异步执行：

~~~text
AI Action 提交任务
→ 返回 taskId
→ 任务中心运行
→ AI 查询进度
→ 结果归档
→ GACE 通知
~~~

AI 不应长时间占用同步请求，也不能通过重复提交制造多个相同回测或订单任务。

### 16.5 通知

可通过 GACE Notification Outbox 发送：

- 机会发现；
- 机会失效；
- 数据中断；
- 策略暂停；
- 单腿失败；
- 对账异常；
- 需要人工确认；
- 风险阈值触发。

通知必须区分公开、私有和严格私有内容，不能把余额、订单和 API 错误泄露给其他用户。

---

## 17. 现有量化系统的复用与禁止复用

### 17.1 可以借鉴的逻辑和代码

| 现有能力 | 复用方式 |
|---|---|
| 策略插件和 SDK 边界 | 借鉴注入数据、策略不直接访问数据库和网络的设计 |
| 信号标准化 | 借鉴版本化、日期/时间校验、参数和上下文检查 |
| 策略包管理 | 借鉴策略身份、版本、Schema 和来源记录 |
| 数据集 Manifest | 借鉴数据指纹、行数、时间边界和来源质量记录 |
| 回测任务 | 借鉴任务状态、取消、恢复、幂等和结果归档 |
| 任务中心 | 借鉴长任务、进度、日志和失败状态 |
| 风险控制 | 借鉴集中式风险检查、账户限额和安全停止思想 |
| 资源管理 | 借鉴 Worker 资源边界、进程清理和任务归属 |
| 测试组织 | 借鉴合同测试、回测测试、生命周期测试和故障测试 |
| Compose 分服务结构 | 只借鉴服务分层思想，不复制现有命名和配置 |

### 17.2 不能直接复用的内容

- execution_rules.py 中的 A 股手续费、印花税、涨跌停和 100 股一手；
- A 股交易日和 T+1；
- A 股股票代码解析；
- A 股历史数据源和供应商降级顺序；
- A 股行业、指数和股票池逻辑；
- 现有 A 股数据库表；
- 现有 A 股 Compose 网络、容器、卷和端口；
- 现有 A 股账户模型；
- 现有 A 股买入数量和现金计算；
- 任何默认假设只有一个市场或一个资产的策略逻辑。

### 17.3 复用流程

1. 先将可复用逻辑列入候选清单；
2. 读取源码和测试；
3. 去除 A 股领域假设；
4. 改造为平台无关接口；
5. 在 Crypto 数据上重新测试；
6. 记录来源 Commit 和改造原因；
7. 不从 dirty worktree 直接复制未知状态的代码。

---

## 18. 非功能要求与运行管理

### 18.1 正确性

- Decimal 资金计算；
- 事件时间明确；
- 订单状态幂等；
- 对账可恢复；
- 数据集可复现；
- 策略不使用未来数据；
- 实盘和模拟严格隔离。

### 18.2 可用性

- 行情断线重连；
- 数据源状态可见；
- Worker 异常可发现；
- 任务不会静默丢失；
- 订单未知状态进入对账状态；
- 服务停止不会自动扩大风险。

### 18.3 性能

第一阶段目标不是高频交易，而是：

- 实时盘口延迟可见；
- 机会计算在数据新鲜度窗口内完成；
- 研究任务不阻塞 API；
- 模拟撮合可连续运行；
- 关键指标可观察。

任何低延迟目标必须通过真实部署位置、网络和数据测量确定，不能仅凭本地开发机结果。

### 18.4 可观测性

至少记录：

- 连接状态；
- 数据延迟；
- 事件速率；
- 丢包和重连；
- 机会数量；
- 机会阻断原因；
- 订单提交和响应；
- 未对冲敞口；
- 对账差异；
- Worker 状态；
- 任务状态；
- 风险状态。

日志必须脱敏，不记录 API Key、完整签名、Cookie 或 Secret 内容。

### 18.5 备份和恢复

需要备份：

- 策略和参数；
- 账户配置元数据；
- 交易账本；
- 订单和成交；
- 回测结果；
- 数据集 Manifest；
- 审计记录。

公共行情原始数据可以按保留策略归档。真实交易状态恢复不能只依赖本地数据库快照，还要与交易所真实订单和余额重新对账。

---

## 19. 测试与验收证据

### 19.1 测试层级

1. 单元测试；
2. 领域合同测试；
3. 交易所连接器测试；
4. 统一模型测试；
5. 数据质量测试；
6. 回放确定性测试；
7. 模拟撮合测试；
8. 风控测试；
9. 任务幂等和恢复测试；
10. 安全和 Secret 泄露测试；
11. 假交易所集成测试；
12. 浏览器 UI 测试；
13. GACE App 合同测试；
14. GACE Build Center 候选测试；
15. 目标环境真实生命周期验收。

### 19.2 假交易所

必须建立本地 Fake Exchange 或录制回放服务，覆盖：

- 正常盘口；
- 断线；
- 限频；
- 订单拒绝；
- 部分成交；
- 延迟；
- 订单状态未知；
- 余额变化；
- 交易所维护。

不能用真实账户作为常规自动化测试环境。

### 19.3 关键验收门

| 门 | 验收内容 |
|---|---|
| G0 | 项目目录、边界、合同和安全基线 |
| G1 | 多交易所行情与统一品种 |
| G2 | 历史数据、质量和回放 |
| G3 | 价差计算和机会阻断 |
| G4 | 回测和结果复现 |
| G5 | 模拟双腿交易和单腿失败 |
| G6 | 真实账户只读、密钥和对账 |
| G7 | 受控真实现货候选 |
| G8 | GACE C1-C3 合同和构建 |
| G9 | GACE C4-C5 安装、AI 和生命周期 |

### 19.4 证据内容

每个门至少保存：

- 源码 Commit；
- 测试命令和摘要；
- 数据集摘要；
- 配置摘要；
- 包或镜像摘要；
- 日志摘要；
- 失败场景；
- 运行环境；
- 后验结果；
- 未完成事项。

---

## 20. 里程碑、交付物与推进顺序

### M0：独立项目基线

交付：

- 项目目录；
- README；
- 本计划书；
- 思维导图；
- .gitignore；
- 领域边界；
- 初始合同目录；
- Git 仓库初始化基线（当前未提交 Commit）；
- `pyproject.toml` 与测试入口；
- 版本化市场、策略、模拟、执行、GACE 和 AI 合同 Schema；
- `Instrument`、`OrderBookSnapshot`、`Balance`、`OrderIntent` 和风险配置领域对象；
- SELL_ONLY 风险前置检查、执行端口和 Fake Exchange 网关；
- M0 单元、合同、Fake Exchange、集成和验收测试。
- 独立运行入口 `Dockerfile`、`compose.yaml` 和只读 ASGI 应用入口；

完成条件：

- 不依赖现有量化系统和 GACE；
- 计划章节和思维导图编号一致；
- 未写入真实密钥和运行数据。

状态：`verified`（离线基线范围）；只读运行入口在 Ubuntu 目标环境完成有限 `runtime-accepted` 后验。当前工作树未提交 Commit，M0 不包含交易所联网、真实账户、持久化、完整回测、前端或 GACE Runtime。

当前验证证据（2026-09-14）：

- 系统 Python `3.14.3`（满足项目 `>=3.12`）执行完整测试套件：`112 passed`；
- `pwsh -NoProfile -File .\scripts\check-file-line-limit.ps1` 和 Windows PowerShell 等价命令均通过：当前检查结果以第 30 章末尾快照为准，全部不超过 3000 行；
- 仅使用 Fake Exchange 和固定测试数据，没有真实 API Key、真实订单或外部交易所请求。

### M1：市场与行情骨架

交付：

- Venue Registry；
- Instrument Registry；
- 统一行情模型；
- 一个交易所适配器；
- Fake Exchange；
- 行情状态 API；
- 基础数据质量检查。

完成条件：

- 能接收、规范化和回放最小行情；
- 能识别断线和过期；
- 不支持真实下单。

状态：`verified`（离线行情、公开 REST、WebSocket 前置、采集编排和共享状态范围）；Bybit 公共 L2 已完成有限 Ubuntu `runtime-accepted` 后验，但 M1 整体尚未达到全交易所 `runtime-accepted`。当前实现包括 `VenueRegistry`、`InstrumentRegistry`、`MarketDataService`、`SnapshotReplay`、Binance Spot、Bybit Spot 和 OKX Spot 公开盘口载荷标准化、公开 REST 网关、`OrderBookDelta`、`OrderBookReconstructor`、`PublicMarketStream`、`PublicMarketCollector`、`WebsocketsJsonConnector`、Fake market-data gateway、独立 `market-worker` 入口、`FileMarketStateStore` 和状态/快照查询视图。公开 REST 请求构造、WebSocket 事件解析、快照后序列重建、重建序列跳跃、重复/缺口/过期/交叉盘口阻断、重采集、共享文件原子写入和 Fake 重连测试已通过；Ubuntu Bybit BTC/ETH/BNB 公共 WebSocket 写入已独立验证，Binance/OKX 实时通道、长期归档和生产级高频存储仍未完成。

当前验证证据（2026-09-14）：

- 系统 Python `3.14.3`（满足项目 `>=3.12`）执行完整测试套件：`112 passed`；
- Fake JSON transport 和 `httpx.MockTransport` 覆盖 Binance/OKX 公开 REST 请求、REST 时间戳缺失、429 限流和连接状态降级；
- 固定 Binance Spot 和 OKX Spot symbol/order-book 载荷通过精度、过滤器、时间戳和序列标准化测试；
- 固定回放通过连续序列、序列间隙、显式 resync、过期和断开状态测试；
- 没有发起外部交易所网络请求，没有真实订单。

### M2：多市场行情与历史数据

交付：

- 第二、第三个交易所适配器；
- 历史数据任务；
- 原始数据归档；
- 数据集 Manifest；
- L1/L2 数据回放。

完成条件：

- 同一交易对可以在多个 Venue 对齐；
- 数据质量报告可复现；
- 任务支持恢复和幂等。

状态：`implemented`（公开 K 线和历史持久化切片），未达到 M2 完成。当前工作树已有确定性 `DatasetManifest`、CSV 持久化、缺口/重复/SHA-256 质量校验、Binance/OKX/Bybit 三个公开 K 线分页连接器、WebSocket 增量序列重建、公开行情采集 Worker 入口、历史任务 API、公开现货品种目录、完整 `5m` 读取和固定快照回放测试；Windows 已真实写入 Binance 的 1d、1h、5m 以及 Bybit 官方区域端点 `api.bybit-tr.com` 的 1d、1h、5m，共 18 个数据集和 747,865 根 K 线，全部已落盘数据的缺口与重复校验为 0。新增 `history-raw-response-v1` 原始响应归档、`history_dataset_records` 正式元数据仓储和 Parquet `DEGRADED` 失败状态，文件成功/数据库失败、重复归档和镜像失败已有测试；Ubuntu 最新后验确认 18 个数据集、749,467 根 K 线、18/18 Parquet 和正式元数据可读。Binance 公开目录现场为 491 个 USDT 现货品种，Bybit 为 288 个，OKX 当前为网络/TLS 阻断；这些目录数量和网络状态会变化，不能替代历史覆盖。Bybit V5 分页已修正为官方 `start`/`end` 参数，覆盖矩阵会引用最新历史任务解释阻断项。币池、回测、筛选、模拟盘、研究运行、历史价差研究和风险配置已接入 PostgreSQL 主存储或完整历史读取；历史任务已接入 PostgreSQL 任务账本、Redis 队列、独立 Worker/Scheduler，实时行情长期归档和正式回测数据合同仍未完成，详细记录见 `docs/HISTORY_DATA_IMPLEMENTATION.md`。

### M3：机会扫描

交付：

- 盘口价差计算；
- 手续费和滑点模型；
- 资金库存检查；
- 机会生命周期；
- 告警和阻断原因。

完成条件：

- 只使用可成交价格；
- 净价差包含成本；
- 过期机会不会进入执行队列。

状态：`implemented`（前置子集），未达到 M3 完成。`OpportunityScanner` 已按可成交的最佳买卖盘、盘口数量、手续费、滑点、库存/转账/延迟/失败缓冲和过期时间计算净价差，并对过期、交易所不匹配和无流动性进行阻断；尚未实现真实余额库存校验、机会持久化、告警服务或执行队列。

### M4：回测和模拟盘

交付：

- 事件驱动回放；
- L2 模拟撮合；
- 模拟账户；
- 双腿执行状态机；
- 单腿失败处理；
- 结果报告。

完成条件：

- 回测可重复；
- 模拟盘不触碰真实账户；
- 能复现部分成交和未对冲状态。

状态：`implemented`（固定数据、OHLCV 研究和组合回测前置子集），未达到 M4 完成。当前已有 `PaperAccount`、多档 L2 `PaperBroker`、`PaperBacktestRunner`、`TwoLegPaperExecutor`、基于已校验 K 线的 `CandleBacktestEngine`、组合回测、策略比较、参数搜索、策略信号筛选和模拟策略历史回放；固定测试覆盖全成、部分成交、单腿失败、未对冲、对账、历史读取和 next-open 回测分支。研究、回测、筛选和模拟盘记录已通过本地原子 JSON 文件在开发环境恢复；MACD 长历史信号已改为一次性 O(n) 序列计算并有参考一致性测试。当前仍未实现历史大数据事件驱动回放、完整 L1/L2 组合报告、跨进程撮合服务或生产级撮合规则；统一任务账本和 Redis Worker 任务链路已有独立实现。

### M5：只读账户和风险中心

交付：

- 真实账户只读连接；
- 余额和订单查询；
- 本地账本；
- 对账；
- 风险限额；
- 安全暂停。

完成条件：

- API Key 没有提币权限；
- 真实账户数据与模拟账户隔离；
- 对账异常会阻断风险扩大。

状态：`implemented`（只读账户与风控前置子集），未达到 M5 完成。当前已有 `AccountSnapshot`、`LedgerProjection`、`ReconciliationService`、只读 Fake Account 和服务端 `SELL_ONLY` 风控门禁，并有固定测试；尚未实现真实交易所只读连接器、Secret Provider、真实余额/订单同步、持久化账本、对账调度和安全暂停编排，因此尚不能证明任何真实 API Key 权限结论。

### M6：受控现货交易候选

交付：

- 真实下单适配器；
- 订单幂等；
- 人工确认；
- 金额和交易对白名单；
- 在途订单处理；
- 实盘审计。

完成条件：

- 先完成 Fake Exchange 和 Paper 验收；
- 无杠杆；
- 小额；
- 可随时安全暂停；
- 不开放自动提币。

### M7：GACE 合同和候选包

交付：

- GACE App 适配目录；
- Manifest；
- Migration Contract；
- Build Contract；
- AI Action Schema；
- Notification Schema；
- SBOM 和许可证；
- 构建中心候选。

完成条件：

- 达到 GACE C1-C3；
- 不修改 GACE Core；
- 构建输入和制品摘要可追溯。

### M8：GACE 运行验收

交付：

- testing 安装；
- 认证入口；
- App 生命周期；
- Secret；
- 网络；
- AI；
- 通知；
- 备份；
- 升级；
- 回滚。

完成条件：

- 达到 GACE C5；
- 独立模式仍可运行；
- GACE stop 不会导致未审计的风险扩大；
- 真实页面和运行状态均有当前证据。

---

## 21. 风险登记表

| 风险 | 影响 | 缓解措施 | 默认状态 |
|---|---|---|---|
| GACE 合同持续变化 | App 适配返工 | 适配层隔离、版本化合同、本地校验 | 持续关注 |
| 构建中心未完成 | 无法正式发布 App | 先独立运行，先做候选和合同 | 已知阻塞 |
| 交易所限频 | 行情和订单失败 | 连接器限频、退避、熔断 | 必须设计 |
| WebSocket 丢包 | 盘口错误 | 快照重建、序列检查、过期阻断 | 必须设计 |
| 价差小于成本 | 理论机会变亏损 | 使用可成交净价差 | 必须设计 |
| 单腿成交 | 产生方向性敞口 | 两腿状态机、对冲和人工审核 | 必须设计 |
| 交易所宕机或冻结 | 无法平衡库存 | 多市场库存、交易所状态熔断 | 必须设计 |
| 稳定币脱锚 | 计价失真 | 独立资产建模和偏差阈值 | 必须设计 |
| API Key 泄露 | 资金风险 | 交易权限、禁提币、Secret Broker | 必须设计 |
| 本地账本损坏 | 对账困难 | 追加账本、备份、交易所复核 | 必须设计 |
| GACE 停止或升级 | 实盘状态不一致 | 安全暂停、对账、禁止自动重启实盘 | 必须设计 |
| 过早扩展 DEX/合约 | 范围失控 | 分阶段范围门禁 | 默认阻断 |
| 地区和平台限制 | 账户不可用 | 使用者确认条款和可用性 | 外部依赖 |
| 误把历史文档当事实 | 错误发布 | 当前源码、构建和运行证据分离 | 持续遵守 |

---

## 22. Definition of Done

### 22.1 独立模式完成

- Standalone 可安装和启动；
- 行情、回测、模拟和审计可用；
- 数据、账户和 Secret 隔离；
- 停止和重启不会丢失关键账本；
- 没有 GACE 依赖。

### 22.2 GACE 合同兼容完成

- App 清单和版本一致；
- 权限最小化；
- 数据和 Secret 声明完整；
- 网络出口有白名单；
- 镜像摘要固定；
- SBOM 和许可证完整；
- AI Action Schema 已声明；
- 本地构建中心校验通过。

这只代表合同兼容，不代表实际安装。

### 22.3 GACE 实际 App 完成

- 构建中心固定输入成功；
- 制品已签名；
- testing 可导入；
- GACE 可安装并启动；
- 真实用户身份生效；
- AI Action 生效；
- 通知生效；
- 停止、启动、升级和回滚通过；
- Secret 不泄露；
- 数据和账本保留策略通过；
- 真实页面和运行状态有独立后验。

### 22.4 实盘交易完成

- Paper 全部通过；
- 只读账户全程可对账；
- Fake Exchange 故障矩阵通过；
- 实盘开关需要人工确认；
- 无提币权限；
- 订单幂等和单腿失败处理通过；
- 风险暂停和恢复通过；
- 真实执行与 GACE 生命周期边界通过。

---

## 23. 待确认决策

以下事项在开始相应开发前需要通过实际环境或小范围验证确认：

1. 第一阶段最终接入的交易所名单；
2. 账户所在地区和平台可用性；
3. 第一批交易对；
4. 历史 L2 数据来源和保留周期；
5. PostgreSQL + Redis + Parquet 是否满足第一阶段数据量；
6. GACE 是否提供可供第三方 App 使用的持久数据服务；
7. GACE 多容器 App 是否允许 Crypto 所需的外部 WebSocket 出口；
8. Crypto 完整执行服务是否部署在 GACE 外部；
9. GACE App 是控制台模式还是完整托管模式；
10. GACE AI Action 的最终请求和确认协议；
11. 真实交易是否只做现货；
12. 是否需要多用户、租户或团队协作。

默认决策：

- 2 到 3 个中心化交易所现货；
- BTC、ETH 等共同高流动性交易对；
- 先只读、回测、模拟；
- 真实交易无杠杆、禁提币；
- GACE 先做控制台和 AI 入口；
- 真实执行核心独立运行；
- 自动交易第一阶段采用 SELL_ONLY，自动卖出现有资产并换成 USDT；
- 自动交易额度默认关闭，启用前必须完成一次性策略授权和限额配置；
- 不修改现有 A 股系统；
- 不修改 GACE Core。

---

## 24. 计划文件与后续目录

当前工作树已建立目录；以下实现文件均已按当前工作树核对，具体状态以 M0-M5 的范围说明为准：

~~~text
D:\code\crypto-platform\
├─ README.md
├─ .gitignore
├─ contracts\
│  ├─ market\
│  ├─ strategy\
│  ├─ paper\
│  ├─ execution\
│  ├─ gace\
│  └─ ai\
├─ docs\
│  ├─ PROJECT_PLAN.md
│  ├─ PROJECT_MINDMAP.md
│  ├─ EXCHANGE_API_RESEARCH.md
│  ├─ AUTO_TRADING_POLICY.md
│  ├─ FILE_SIZE_POLICY.md
│  └─ RUNTIME_DEPLOYMENT.md
├─ frontend\
│  ├─ src\views\       # 登录页和工作台
│  ├─ src\stores\      # 会话状态
│  ├─ Dockerfile
│  └─ nginx.conf
├─ backend\
│  ├─ app\auth\        # 开发阶段认证
│  ├─ app\api\         # 认证和市场 API
│  ├─ Dockerfile
│  └─ .env.example
├─ src\
│  ├─ domain\
│  ├─ application\
│  ├─ ports\
│  ├─ adapters\
│  │  ├─ venues\
│  │  ├─ standalone\
│  │  └─ gace\
│  ├─ services\
│  └─ web\
├─ tests\
│  ├─ unit\
│  ├─ contract\
│  ├─ replay\
│  ├─ fake-exchange\
│  ├─ integration\
│  └─ acceptance\
├─ scripts\
│  ├─ check-file-line-limit.ps1
│  └─ check-plan-mindmap.ps1
├─ Dockerfile
├─ compose.yaml
└─ .dockerignore
~~~

当前目录内的 `.gitkeep` 只是占位文件，表示模块边界已建立，不代表业务代码、合同实现或测试已经完成。后续新增文件必须遵守第 29 章的单文件规模规则。

M0 主要实现文件：

- `pyproject.toml`：Python 3.12 包元数据和测试入口；
- `contracts/*/`：市场、策略、模拟、执行、GACE 和 AI 的 v1 JSON Schema；
- `src/domain/market.py`：品种、精度、盘口和时间字段；
- `src/domain/trading.py`：余额、订单意图、方向、订单类型和状态；
- `src/domain/risk.py`：限额与风险上下文；
- `src/application/risk_precheck.py`：SELL_ONLY 服务端风险门禁；
- `src/application/execution.py`：风险检查到执行端口的一次性编排；
- `src/ports/execution.py`：交易执行端口；
- `src/adapters/venues/fake_exchange.py`：确定性的 SELL_ONLY Fake Exchange；
- `tests/`：M0/M1 单元、合同、Fake Exchange、回放、集成和验收测试。

M1 主要实现文件：

- `src/domain/venue.py`：Venue 和内存注册表；
- `src/domain/instrument_registry.py`：大小写不敏感的品种注册表；
- `src/domain/market_status.py`：连接、过期和降级状态；
- `src/application/market_data.py`：序列、过期和 resync 门禁；
- `src/adapters/venues/binance_spot.py`：Binance Spot 公开载荷标准化；
- `src/adapters/venues/okx_spot.py`：OKX Spot 公开载荷标准化；
- `src/ports/rest.py`：公开 JSON REST 传输端口和错误合同；
- `src/adapters/venues/httpx_transport.py`：HTTP 状态、限流、网络和 JSON 错误分类；
- `src/adapters/venues/public_rest_base.py`：公开网关状态与失败处理；
- `src/adapters/venues/binance_public_rest.py`：Binance Spot 公开 REST 品种和盘口网关；
- `src/adapters/venues/okx_public_rest.py`：OKX Spot 公开 REST 品种和盘口网关；
- `src/domain/market_events.py`：盘口增量和值为零的删除更新模型；
- `src/application/order_book.py`：Binance 区间序列与 OKX 前序 ID 序列的盘口重建；
- `src/application/market_stream.py`：带停止信号和退避的公开流重连运行器；
- `src/application/market_collector.py`：REST 快照、WebSocket 增量、序列重建、重采集和状态门禁编排；
- `src/ports/websocket.py`：异步 JSON WebSocket 端口；
- `src/adapters/venues/websockets_transport.py`：`websockets` JSON 传输实现；
- `src/adapters/venues/fake_market_data.py`：固定数据源；
- `src/services/replay.py`：确定性快照回放；
- `src/services/market_worker.py`：独立公开行情 Worker 入口，默认关闭且不含私有凭据；
- `src/web/market_status.py`：后续 HTTP 适配器可复用的状态视图。
- `tests/fake-exchange/test_public_rest_gateways.py`：公开 REST Fake/Mock transport 和错误路径测试。
- `tests/fake-exchange/test_binance_spot_adapter.py`、`tests/fake-exchange/test_okx_spot_adapter.py`：公开 WebSocket 载荷规范化测试；
- `tests/unit/test_order_book_reconstructor.py`、`tests/unit/test_market_stream.py`、`tests/fake-exchange/test_market_collector.py`：序列门禁、盘口重建、断线重连和采集重同步测试。

M2/M3 前置实现文件：

- `src/domain/history.py`：数据级别和可复现 Manifest；
- `src/application/history.py`：幂等 Manifest 目录；
- `src/domain/opportunity.py`：费用、成本和机会值对象；
- `src/application/opportunity.py`：基于盘口深度的跨市场净价差计算。

M4/M5 前置实现文件：

- `src/domain/paper.py`：模拟账户、模拟成交和订单状态；
- `src/domain/two_leg.py`：双腿模拟执行状态；
- `src/domain/account.py`：只读账户快照、账本投影和对账结果；
- `src/application/paper.py`：基于 L2 盘口的多档模拟撮合；
- `src/application/backtest.py`：固定输入的模拟回测摘要；
- `src/application/two_leg.py`：双腿 Paper 执行和单腿失败分支；
- `src/application/reconciliation.py`：本地账本与外部快照对账；
- `src/adapters/venues/fake_account.py`：只读账户 Fake gateway；
- `src/web/http_api.py`：仅供开发和测试使用的只读 FastAPI surface；
- `backend/app/services/research_runs.py`、`src/application/portfolio_backtest.py`：研究编排、组合回测、策略比较、参数搜索和信号筛选；
- `src/application/spread_research.py`、`backend/app/api/market.py`：同周期历史价差研究和明确的双边成本模型；
- `src/application/screening.py`：基于质量通过历史的指标筛选；
- `backend/app/services/paper_trading.py`：模拟策略历史回放和模拟订单状态；
- `tests/integration/`、`tests/unit/` 和 `tests/acceptance/`：对应固定数据、隔离和边界测试。

M0/M1 离线核心、公开 REST/WebSocket 前置代码、盘口重建、公开行情采集编排和跨进程最新状态桥，以及 M2-M5 的前置子集已经落码并通过当前自动测试；Windows 工作树新增的 `backend/` 和 `frontend/` 提供真实应用边界、开发认证、市场总览、历史数据中心和研究工作台，已完成本地浏览器/真实数据 API 验收。历史服务已真实写入 Binance 的 1d、1h、5m 和 Bybit 官方区域端点 `api.bybit-tr.com` 的 1d、1h、5m 数据，18 个数据集和 747,865 根 K 线已在 Windows 验证；Ubuntu 最新后验已确认 18 个数据集、749,467 根 K 线、18/18 Parquet、历史价差研究对齐 105,417 根 K 线。历史价差研究已完成代码、接口和自动测试，Ubuntu 18/18 Parquet 归档已完成，历史正式元数据与有界领域快照均已接入 PostgreSQL。币池、回测、筛选、研究、模拟盘和风险配置已由 PostgreSQL 主存储配合恢复副本运行，默认启动的 Redis Worker/Scheduler 已支持统一任务账本、Worker 执行、租约、取消、重试、详情、跨进程结果读取和历史增量自动同步，Scheduler 具有持久化状态及失败退避；`task-result-v1` 结果归档和 Worker 当前任务优雅排空已完成有界运行端后验。旧的 `src/web/http_api.py` 仍保留为兼容的只读核心入口；新版 `backend`、`frontend`、`task-worker`、`task-scheduler` 已通过 Compose 独立运行，`market-worker` 默认关闭，规范化最新盘口和状态可以写入 `runtime/market` 并由 API 只读查询。当前没有真实账户或真实订单证据，任何真实交易功能都必须晚于 M4 和 M5 的完整验收。

---

## 25. 当前结论

本项目采用：

> 独立 Crypto Core + Standalone Adapter + GACE Adapter + GACE AI Action

这不是两套系统，也不是把独立系统硬塞进 GACE，而是一套业务核心对应两个运行目标。独立模式保证开发和运行不被 GACE 当前进度阻塞；GACE 适配层保证未来可以由构建中心生成符合 GACE 要求的 App，并使用 GACE 的身份、权限、Secret、任务、通知和 AI 能力。

管理上，GACE 会让安装、权限、审计、AI 和生命周期更统一；运行上，真实套利执行仍应保持独立和可控，不能把 GACE 的通用 App 生命周期误当成交易执行保障。

当前实现边界（以本次工作树、测试结果和最近 Ubuntu 后验为准）：M0/M1 的离线领域、风险、注册表、Binance/OKX 公开载荷标准化、公开 REST 客户端、WebSocket 事件模型、快照后序列重建、可测试重连运行器、公开行情采集编排、回放、`FileMarketStateStore` 和状态/快照查询，以及 M2-M5 的 Manifest、机会计算、L2 Paper 撮合、固定输入回测、双腿 Paper、只读账户快照、账本投影和对账前置对象已经通过自动测试；历史公开 K 线已增加三家分页连接器、CSV/Manifest 质量校验、后台任务 API、完整 `5m` 读取和前端数据中心，Windows 已保存 Binance 的真实 1d、1h、5m 与 Bybit 官方区域端点 `api.bybit-tr.com` 的真实 1d、1h、5m，共 18 个数据集、747,865 根 K 线，全部已落盘数据缺口和重复校验为 0。历史价差研究服务、API、成本模型和自动测试已完成；Ubuntu 后验已确认 24 个数据集、980,363 根 K 线、运行端绑定、认证、24/24 Parquet、正式历史元数据和自动 Scheduler；网络设置页支持三家交易所分别维护公共 REST、公共 WebSocket、历史 REST 及 HTTP/WS 代理，OKX 公共 REST/WebSocket 和三路 BTC/USDT 只读 L2 已完成有限真实网络验收。`task-result-v1` 结果归档、跨进程读取、完成事件摘要化、Worker 当前任务优雅排空和受控停机恢复已完成有界运行端后验。运行计划聚合、GACE 只读 capability catalog、合同和前端页面已落码并通过当前测试、构建和浏览器验收；币池、回测、筛选、研究、模拟盘、风险配置和策略孵化池已经使用 PostgreSQL 主存储及恢复副本，默认启动的 Redis Worker/Scheduler 已通过本地路由测试并承载统一任务账本、Worker、取消、重试、租约、跨进程详情读取、历史增量自动同步、调度状态和失败退避。公开实时状态目前只是最新值桥接，长期行情归档、PostgreSQL/Redis 行情持久化、生产用户数据库与会话撤销、完整 L1/L2 事件回测报告、真实账户连接器、真实下单、GACE App Runtime 和 GACE AI 实际接入仍未完成；当前没有真实 API Key、真实订单或真实账户运行证据，执行模式保持 `DISABLED`。

---

## 26. 技术选型与基础设施方案

### 26.1 选型原则

技术选型围绕当前目标确定：先完成公开行情、历史数据、回测、模拟交易和受控现货交易，再考虑更低延迟和更大规模。第一阶段不为了未来可能的高频场景引入多语言、分布式消息系统或多个时序数据库。

新项目可以借鉴现有 A 股量化系统已经验证过的工程模式，但必须使用自己的依赖锁、镜像、配置、数据库、Redis、数据目录和 Secret。现有项目的 A 股领域规则不作为 Crypto 业务规则。

### 26.2 技术基线

| 层级 | 第一阶段选择 | 说明 |
|---|---|---|
| 后端与领域核心 | Python 3.12 | 适合策略研究、异步行情、任务编排和现有代码复用；容器固定小版本和依赖锁 |
| API | FastAPI + Pydantic v2 | REST、OpenAPI、Action 输入输出和配置校验 |
| 持久化访问 | SQLAlchemy 2 + Alembic + psycopg3 | 事务、迁移、类型边界和 PostgreSQL 访问 |
| 交易所连接器 | asyncio + httpx + WebSocket 客户端 | REST、公开 WebSocket、账户和订单接口分层；优先官方 API |
| 策略与研究 | NumPy + Pandas + PyArrow | 指标、数据处理、Parquet 读写和事件回放；L2 模拟撮合由本项目维护 |
| 前端 | TypeScript + Vue 3 + Vite + Pinia | 与现有前端技术经验一致，但使用新项目自己的包和构建配置 |
| 图表 | Lightweight Charts 或同类轻量图表库 | K 线、成交、价差和回测曲线；盘口深度先使用可审计的表格或专用视图 |
| 主数据库 | PostgreSQL 15 起步 | 新建独立实例和数据卷；第一阶段保存元数据、订单、成交、账本、任务、审计和配置 |
| 热状态与协调 | Redis 7 | 缓存、最新行情、Streams、租约、限频和短期状态，不作为资金账本 |
| 历史行情 | Parquet + Apache Arrow | 原始行情、标准化行情、回放数据和数据集分片；后续可接对象存储 |
| 任务事件 | Redis Streams + 有界 Worker | 先满足行情处理、历史同步、回测和模拟任务；规模证据充分后再评估 NATS 或 Kafka |
| 本地部署 | Docker Compose | API、采集 Worker、研究 Worker、模拟 Worker、PostgreSQL、Redis 和前端分服务 |
| GACE 交付 | OCI 镜像或 multi-container App | 通过 GACE Adapter 和正式构建合同交付，不修改 GACE Core |
| 测试 | Pytest、pytest-asyncio、Hypothesis、Vitest、Playwright | 覆盖领域合同、连接器、回放、撮合、风控、UI 和 GACE App 合同 |

依赖管理要求：后端使用 `pyproject.toml` 和锁定文件，前端只保留一种包管理器和一种锁文件，容器构建禁止依赖未固定的 `latest`。具体工具版本必须在 M0 的构建合同中固定，并记录 Python、Node、基础镜像和系统包摘要。

### 26.3 数据库和数据目录职责

第一阶段使用一个独立的 Crypto PostgreSQL 服务，但至少按 `core`、`research`、`paper` 和 `audit` 逻辑域隔离表、数据库用户或 Schema。进入真实账户阶段时，为 `live-trading` 建立单独数据库用户；在目标环境允许时使用独立数据库和数据卷。

PostgreSQL 保存：

- Venue、Market、Instrument、Asset 和费率快照；
- 策略、参数、版本、运行实例和数据集 Manifest；
- 模拟账户、订单、成交、持仓、费用和账本；
- 真实账户元数据、订单状态、对账差异和风险事件；
- 任务、通知、AI Action 和审计记录。

资金和交易字段使用 Decimal 语义，数据库使用 `NUMERIC`；事件时间使用 UTC `TIMESTAMPTZ`。订单、成交、费用和账本必须有唯一约束和幂等键。

Redis 保存：

- 最新 Ticker、盘口快照和数据新鲜度；
- 行情处理的短期事件流和 Worker 租约；
- 任务进度、限频计数和短期锁；
- 前端实时状态的发布订阅信息。

Redis 丢失后，系统应可以从 PostgreSQL、原始归档、交易所快照或任务状态恢复，不能把 Redis 作为唯一资金真相。

Parquet 按 `venue/market_type/instrument/date` 分区保存原始和标准化行情。每个数据集记录来源、时间边界、行数、缺口、版本和摘要。数据量和查询压力没有实测前，不引入 TimescaleDB、ClickHouse 或 Kafka。

### 26.4 语言边界和性能策略

Python 足以支持本项目第一阶段的研究、低频策略、受控套利和任务编排。行情连接器使用异步 I/O，计算和写入使用有界队列，避免单个交易所事件阻塞全部市场。

只有在真实测量确认 Python 热路径无法满足目标时，才评估使用 Rust 或 Go 独立实现盘口合并、行情网关或执行网关。即使增加高性能组件，也必须继续通过稳定的领域合同和适配器与 Python 策略层通信，不能把系统变成没有统一合同的多语言集合。

CCXT 可以作为品种元数据和部分 REST 接口的辅助工具，但不能隐藏交易所的精度、订单簿序列、限频、订单状态和错误语义。生产连接器必须保留 Venue 专属能力和原始响应摘要。

### 26.5 技术选型验收

- 新项目可在没有 GACE 和 A 股系统的情况下构建和启动；
- 数据库、Redis、Parquet 和 Secret 均与现有项目隔离；
- 订单和账本使用事务、精确数值和幂等约束；
- Redis 不承载不可恢复的唯一资金状态；
- 固定数据集可以从 Parquet 重放并得到一致结果；
- API、Worker、前端和 GACE Adapter 均有版本化合同；
- 未出现 Rust、Go、Kafka、ClickHouse 等未经容量或延迟测量证明必要的早期依赖。

---

## 27. 交易所开放 API 与 QMT 类能力调研

### 27.1 调研范围和状态

本章是截至 2026-09-14 的技术调研快照，重点关注 Binance、OKX、Bybit、Coinbase Advanced Trade 和 Kraken 的公开行情、历史数据、账户交易接口以及测试环境。交易所文档、地区服务、限额和测试环境会变化；正式实现前必须重新读取对应官方文档并对具体账户、网络和接口逐项验证。

详细来源和核验记录见 `D:\code\crypto-platform\docs\EXCHANGE_API_RESEARCH.md`。本章的结论不能替代地区可用性、账户资格、API 权限或法律合规判断。

### 27.2 交易所 API 能力分层

| 能力层 | 是否通常需要 API Key | 典型内容 | 系统用途 |
|---|---|---|---|
| 公开市场 REST | 否 | 交易对、Ticker、K 线、成交、订单簿、交易所时间 | 行情采集和历史补齐 |
| 公开市场 WebSocket | 否 | 实时成交、Ticker、盘口快照和增量 | 实时行情和价差扫描 |
| 私有账户查询 | 是 | 余额、订单、成交、持仓和账户状态 | 只读账户和对账 |
| 私有交易接口 | 是 | 下单、撤单、订单查询和用户流 | 受控真实执行 |
| 历史批量数据 | 视来源而定 | K 线、成交、部分盘口归档 | 回测和回放 |
| 测试网或 Demo | 通常需要测试凭据 | 虚拟余额、测试订单和接口行为 | 订单语义验证，不代表真实流动性 |

开放 API 不等于无限制 API。每个平台都有请求权重、并发、订单频率、WebSocket 连接寿命、IP 或账户限额。连接器必须实现退避、重连、序列检查、状态查询和未知结果处理。

### 27.3 主要交易所调研结论

| 平台 | 已确认或需核验的开放能力 | 对本项目的价值 | 第一阶段建议 |
|---|---|---|---|
| Binance Spot | 官方文档区分公开 REST、市场数据专用入口、公开 WebSocket、签名账户/交易接口，并提供 Spot Testnet 和历史数据下载入口 | 公开行情和生态资料完整，适合作为第一个连接器 | 先接公开行情和 Testnet 订单语义，不接真实下单 |
| OKX | V5 文档以 REST 和 WebSocket 覆盖市场、账户、交易等模块，并统一描述现货、衍生品等产品；Demo 能力和地区限制需逐接口核验 | 适合第二个市场，用于跨交易所行情对比 | 先做公开行情，确认 Demo 和账户可用性后再决定 |
| Bybit | 官方 V5 文档将 Spot、Derivatives、Options 纳入统一 API，提供 REST、WebSocket、Testnet 和官方 SDK 入口 | 产品覆盖广，适合作为第二或第三个市场 | 先做公开行情和 Testnet，合约功能后置 |
| Coinbase Advanced Trade | 官方提供 Advanced Trade REST/WebSocket 文档，并有 Sandbox 入口；Sandbox 的接口覆盖和模拟行为需逐项测试 | 可作为额外现货市场和地区可用性对照 | 不列入首批必接，先做可用性和数据覆盖验证 |
| Kraken | 官方提供 Spot REST/WebSocket 文档；历史深度、测试环境和账户可用性需单独确认 | 可作为后续市场，增加价格源多样性 | 后置，待首批连接器稳定 |

Binance 官方资料同时提示：公开市场数据可以使用专用数据 API，WebSocket 连接具有生命周期和 Ping/Pong 要求，超出请求限制后需要退避，持续违规可能触发更严格的 IP 限制。由此本项目不能用“定时循环请求最新价”代替正规连接器，必须优先使用 WebSocket、快照加增量同步和数据新鲜度门禁。

### 27.4 历史数据现实边界

交易所通常都能提供部分 K 线、成交和当前订单簿接口，但不同交易所对历史 L2 订单簿、深度、保留周期和下载方式差异很大。跨市场短时价差策略不能只依赖 K 线：

1. K 线适合趋势、波动率和低频策略；
2. 成交数据适合成交回放和较粗粒度模拟；
3. L2 快照和增量适合盘口深度、可成交数量和部分成交模拟；
4. 历史 L2 不完整时，必须先建立自己的持续采集和归档，不能把缺失数据当成零滑点；
5. 测试网的虚拟流动性不能证明生产市场的价差、成交概率或延迟。

因此 M1 先采集实时公开行情，M2 建立自己的历史归档和 Manifest，M4 用 Fake Exchange 加录制回放验证，不能把测试网直接当成回测数据源。

### 27.5 是否存在 QMT 类官方能力

Binance 等交易所的官方 API 主要提供“数据和交易通道”，通常不等同于 A 股 QMT 的完整终端。QMT 类系统一般还包含：

- 行情数据中心和本地历史库；
- 策略 SDK 和策略运行时；
- 回测、参数研究和结果归档；
- 模拟账户和撮合器；
- 实盘账户、订单、风控和对账；
- 策略池、任务中心、通知和可视化管理。

Binance 官方 API 不提供一套可以直接替代 QMT 的本地策略终端。Binance 网页端的交易工具或网格机器人也不等于可编程、可审计、可跨交易所迁移的 QMT 内核。

### 27.6 可参考的第三方框架

| 框架 | 更接近的能力 | 不足和边界 |
|---|---|---|
| vn.py | 国内量化平台形态，包含网关、CTA、回测、价差、数据管理和风控等模块 | 不是 Binance 官方产品；具体 Crypto Gateway、版本和维护状态必须逐项核验；跨交易所双腿执行仍需审查 |
| Freqtrade | Python 交易机器人，具备历史数据、回测、Dry-Run、Live、Web UI 和多交易所接入 | 更适合策略机器人；不能直接当成跨交易所双腿套利和完整账本内核 |
| Hummingbot | 开源 Python 框架，强调 CEX/DEX 连接器、做市和套利执行 | 更偏执行和做市，研究、数据治理、审计和 GACE App 合同仍需自行建设 |

这些框架可以用于学习连接器、策略生命周期和模拟模式，暂不作为 Crypto Core 的运行依赖。直接把其中一个框架嵌入新系统，会把其账户模型、订单状态、数据质量和升级节奏带入核心，增加后续 GACE 适配和跨市场执行改造风险。

### 27.7 本项目的 QMT 类实现路径

本项目不等待交易所提供 QMT，而是在交易所 API 之上构建自己的 Web 化 QMT 能力：

~~~text
交易所公开 API / 私有 API
→ Venue Connector
→ 统一行情与账户模型
→ 数据中心与历史归档
→ Strategy SDK 与策略运行时
→ 回测 / Replay / Paper Broker
→ Risk Center 与双腿 Execution
→ 独立 Web UI 或 GACE App
→ GACE AI Action
~~~

首批连接顺序建议为：

1. Binance Spot 公开行情；
2. Binance Spot Testnet 订单语义；
3. OKX 或 Bybit 公开行情，形成至少两个市场的价差比较；
4. Fake Exchange 和录制回放故障矩阵；
5. 真实账户只读和对账；
6. 经过人工确认、无杠杆、小额和禁提币门禁后，才评估真实现货下单。

结论是：交易所提供底层数据和交易接口，本项目负责构建 QMT 类的策略、回测、模拟、风控、审计和管理能力。这样既保留独立运行能力，也能通过 GACE Adapter 迁移为 GACE App；不会把某一家交易所的终端锁死为系统核心。

---

## 28. 自动交易目标、卖出模式与风险限额

### 28.1 自动交易目标

本项目的最终目标不是只展示行情，而是让用户完成一次性授权、配置交易范围和风险限额后，由系统自动执行符合策略的现货交易，用户不需要逐笔手动下单。

自动交易的含义是“逐笔订单无需人工确认”，不是“取消所有安全门禁”。以下操作仍必须要求当前用户进行一次明确授权：

- 首次启用真实自动交易；
- 提高单笔、单日或累计额度；
- 增加交易所或交易对；
- 从 SELL_ONLY 切换到允许买入的模式；
- 启用全部退出或紧急市价卖出；
- 解除风险暂停；
- 更换或扩大 API Key 权限。

一次授权之后，系统可以在授权有效期内自动执行订单；授权、策略版本、限额版本和每笔订单都必须进入审计账本。AI 或 GACE App 不得用参数覆盖、伪造确认或绕过服务端风控。

### 28.2 第一阶段 SELL_ONLY 模式

由于用户当前主要希望自动卖出，本项目第一阶段真实交易只开放 `SELL_ONLY`：

- 只支持中心化交易所现货；
- 只允许 `SELL` 方向；
- 默认卖出 BTC、ETH、BNB 等白名单资产换取 USDT；
- 只能卖出交易所报告的可用现货余额；
- 不买入、不做空、不借贷、不使用杠杆；
- 不支持合约、期权、保证金、跨链和资金划转；
- 不调用提币、内部转账或地址管理接口；
- 默认使用限价或受控的 IOC 订单，禁止无限制市价单；
- 资产卖完或达到限额后自动停止，不会为了继续交易而自动补仓。

Binance 的 API 权限通常按读取、交易、用户数据和产品权限划分，公开文档没有提供一个可以直接限制为“只允许卖出”的标准 API Key 权限。因此 `SELL_ONLY` 必须由多层控制共同实现：

1. 独立 Binance 账户或子账户只放置允许卖出的资产；
2. API Key 只开启读取和现货交易，不开启提币和转账；
3. API Key 配置 IP 白名单并通过独立 Secret Provider 注入；
4. 应用层拒绝所有 BUY、借贷、杠杆、转账和提币意图；
5. 合同测试验证 SELL_ONLY 不会生成买单；
6. 即使应用被攻破，账户隔离也限制可被影响的资金范围。

必须明确：应用层 SELL_ONLY 不是交易所权限层面的绝对卖出权限。若要求“密钥本身被盗也绝对不能买入”，仅使用 Binance 现货 API Key 无法完全保证，必须依靠账户隔离、低余额、IP 限制、密钥保管和外部人工监控共同降低风险。

### 28.3 自动交易限额模型

所有限额在服务端执行，前端显示只是辅助。限额必须绑定 `venue`、`instrument`、`account`、`strategy`、`mode`、时间窗口和策略版本，不能只在前端保存。

| 限额 | 初始默认 | 触发后的动作 |
|---|---|---|
| 自动交易总开关 | 关闭 | 未完成一次性授权前不得提交真实订单 |
| 运行模式 | SELL_ONLY | 任何 BUY 意图直接拒绝并记录 |
| 交易所白名单 | 空 | 未列入白名单的交易所禁止交易 |
| 交易对和资产白名单 | 空 | 未列入白名单的交易对禁止交易 |
| 计价资产 | USDT | 非 USDT 计价订单默认拒绝 |
| 单笔最大名义金额 | 0，必须显式配置 | 超过则拒绝，不自动拆单绕过额度 |
| 单日最大卖出名义金额 | 0，必须显式配置 | 达到后停止该策略和账户的新增卖单 |
| 单日最大卖出比例 | 0，必须显式配置 | 不得超过可用基础资产比例 |
| 最低资产保留量 | 必须显式配置 | 卖出后不得低于保留量 |
| 单交易对未完成订单数 | 初始建议 1 | 超过后不再提交新订单 |
| 下单频率 | 初始建议每分钟 1 笔 | 超过后限流并记录 |
| 最大允许滑点 | 必须显式配置，初始建议 20 bps | 预计成交价不满足价格保护时拒绝 |
| 行情最大年龄 | 初始建议 2 秒，按实测调整 | 数据过期、断线或序列异常时禁止新单 |
| 连续拒单次数 | 初始建议 3 次 | 进入安全暂停 |
| 未知订单数量 | 0 | 存在未知状态时禁止新增订单，先查询和对账 |
| 市价单 | 关闭 | 只有独立的紧急退出授权才可评估 |

上述“初始建议”只是工程安全起点，不是收益承诺，也不是未经用户确认的投资额度。真正启用前必须填写绝对金额、比例、保留量和交易对范围；配置为空或为零时，系统保持关闭。

推荐的第一份真实候选配置是：每笔和每日同时受绝对 USDT 金额与基础资产比例限制，保留至少一部分基础资产，只允许一个未完成卖单，使用限价订单，数据过期立即停止。具体数值必须由用户根据账户规模和风险承受能力设置并审计确认。

### 28.4 订单前置检查

每一笔自动卖单必须按以下顺序检查：

1. 运行模式是 `SELL_ONLY` 且授权未过期；
2. Venue、账户、交易对和策略版本都在白名单；
3. 订单方向是 SELL，基础资产可用余额足够，卖出后不低于保留量；
4. 行情新鲜，盘口快照和增量序列连续；
5. 可成交买盘深度足够，预计成交价格和滑点满足策略阈值；
6. 价格、数量、最小名义金额和订单数量符合交易所 `exchangeInfo` 过滤器；
7. 单笔、单策略、单账户和单日额度均未超限；
8. 没有未对账订单、风险暂停或重复的幂等键；
9. 生成不可重复的 `clientOrderId`，记录策略、限额和行情快照摘要；
10. 通过所有检查后才允许签名并提交订单。

交易所的 `PRICE_FILTER`、`LOT_SIZE`、`MIN_NOTIONAL` 或 `NOTIONAL`、`MAX_NUM_ORDERS` 等过滤器是交易所门槛，系统自己的风险限额必须比交易所门槛更严格，不能把交易所接受订单误认为系统允许承担该风险。

### 28.5 订单状态和失败处理

自动卖出必须使用状态机：

~~~text
SellIntent
→ RiskPrecheck
→ Submitted
→ Accepted / Rejected / Unknown
→ PartialFilled / Filled / Canceled / Expired
→ Reconcile
→ LedgerProjection
~~~

- 请求超时、HTTP 5xx 或连接断开时，订单状态进入 `Unknown`，不能直接生成新订单重试；
- 使用 `clientOrderId` 和订单查询确认状态，避免重复卖出；
- 部分成交时只对剩余数量进行后续处理，并再次经过额度和保留量检查；
- 收到 429 必须退避，持续违规或出现 418 时自动暂停该连接器；
- WebSocket 断线、盘口序列断裂或行情过期时禁止新增订单；
- 安全暂停时可以取消已知且可确认的未成交订单，未知状态必须先查询和对账；
- 对账发现余额、成交或费用异常时，停止新增订单并生成告警；
- 硬风险暂停不得自动恢复，恢复需要一次新的授权和审计记录。

Binance 官方文档说明订单请求超时或 5xx 可能对应未知的撮合状态，因此“API 请求失败”不能等同于“订单没有成交”。这条规则必须进入 Fake Exchange、Testnet 和生产候选的故障测试。

### 28.6 自动卖出与跨市场套利的边界

SELL_ONLY 可以在多个交易所比较可成交买价，并选择拥有足够基础资产且净价格最优的市场卖出。但它本质上是“库存退出或自动换成 USDT”，不是完整的持续跨交易所套利。

完整跨市场套利通常需要一边买入、一边卖出，并持续做资产和 USDT 再平衡。若永远禁止买入，卖出库存会逐渐减少，策略最终自动停止。因此后续若要做持续套利，必须新增独立的 `BUY_SELL` 模式、独立权限、独立额度和更高等级授权，不能偷偷复用 SELL_ONLY 放开买入。

### 28.7 自动交易与 GACE 生命周期

GACE App 或 GACE AI 只能调用已经声明并授权的 Action，例如查询状态、启动模拟、暂停卖出或提交受限的自动卖出任务。高风险 Action 必须包含：

- 当前用户和账户主体；
- 策略、交易所、交易对和限额版本；
- 卖出数量和金额上限；
- 幂等请求 ID；
- 明确授权和过期时间；
- 审计 trace 和结果。

GACE stop、App 升级、网络断开或 Worker 重启不能直接等同于已经平仓。系统先进入安全暂停，确认订单状态和真实余额，再决定是否恢复；停止服务不能自动追加卖单，也不能自动转移资金。

### 28.8 自动交易实施门禁

自动交易拆成以下门禁：

1. `AT0`：SELL_ONLY 领域合同、策略合同和限额 Schema；
2. `AT1`：Fake Exchange 验证买单拒绝、卖出额度、保留量和重复请求；
3. `AT2`：历史回放和模拟盘验证卖出策略、滑点、部分成交和余额变化；
4. `AT3`：Binance Spot Testnet 验证签名、过滤器、订单状态和撤单流程；
5. `AT4`：真实账户只读、余额和订单对账，不提交真实订单；
6. `AT5`：独立低余额账户的 SELL_ONLY 小额候选，人工完成一次性授权；
7. `AT6`：观察期内验证限额、暂停、恢复、审计和 GACE 生命周期；
8. `AT7`：只有在候选证据完整后，才允许扩大交易对或额度。

任何门禁失败都回到安全暂停，不通过提高重试次数或关闭风控来推进。

### 28.9 自动交易验收标准

- SELL_ONLY 在单元、合同、集成和浏览器测试中都不能产生 BUY、转账或提币请求；
- 未配置白名单、限额或授权时，真实下单路径不可达；
- 单笔、单日、比例、保留量、滑点、订单频率和未完成订单限制可被独立测试；
- 断线、乱序、过期、429、418、5xx、超时、拒单、部分成交和未知订单均能进入预期状态；
- 重复任务和重复订单不会导致重复卖出；
- 所有订单、成交、费用、余额变化、策略版本、限额版本和授权均可审计；
- API Key、Secret、签名和完整请求不会出现在日志、前端、镜像和 App 包；
- 自动暂停和人工恢复行为有当前运行证据；
- GACE stop、升级和回滚不会被解释成已经完成平仓；
- 独立模式在 GACE 不可用时仍能安全暂停和对账。

本章的自动交易能力仍属于设计和风险基线。完成本章不代表已经接入真实账户或取得真实交易许可，真实下单必须以 AT0-AT5 的当前证据为准。

---

## 29. 文件规模、目录边界与拆分门禁

### 29.1 硬性规则

为控制后期阅读、审查和变更风险，本项目对所有可维护文本文件设置单文件硬上限：

- 源码、合同、测试、配置、脚本和 Markdown 文档最多 3000 行；
- 3000 行是硬失败线，不能通过增加空行、注释、生成后提交或拆成不可读的伪文件绕过；
- 达到 2400 行时必须进行拆分评估；达到 2700 行时不得继续向该文件添加无关职责；
- 二进制资产不按行数衡量，但不得把二进制或生成数据伪装成可维护文本；
- `.git`、依赖、构建产物和运行时数据目录不属于源码交付物，也不应提交到 Git。

完整规则记录在 `D:\code\crypto-platform\docs\FILE_SIZE_POLICY.md`。本章是计划约束，规则文件和脚本才是日常执行入口。

### 29.2 拆分原则

1. 按主要职责、依赖方向和变更边界拆分，而不是在任意行号截断；
2. 领域模型、用例、端口、交易所连接器、Standalone、GACE、风险和 Web 层分别归属对应目录；
3. 每个交易所连接器独立管理原生规则，不把多交易所条件集中到一个巨型文件；
4. 合同按领域和版本拆分，测试按单元、合同、回放、假交易所、集成和验收场景拆分；
5. 拆分后保留清晰的公共接口、版本信息和测试追踪，不以复制代码换取行数下降。

### 29.3 自动检查

`D:\code\crypto-platform\scripts\check-file-line-limit.ps1` 默认扫描项目根下的可维护文本文件：

```powershell
pwsh -File .\scripts\check-file-line-limit.ps1
```

脚本在 2400 行及以上输出预警，任何文件超过 3000 行则返回失败。该脚本应在本地开发和 CI 门禁中执行，并作为 M0 的基础工程检查。

计划书与思维导图章节一致性检查：

```powershell
pwsh -File .\scripts\check-plan-mindmap.ps1
```

该脚本只检查 `00` 至 `29` 的编号和标题，不把思维导图节点当成功能验收证据。

### 29.4 本章验收

- 计划书、思维导图、README 和文件规模规则对单文件上限的描述一致；
- 目标目录已在当前工作树创建，空目录通过 `.gitkeep` 保留；
- 检查脚本可扫描当前工作树并返回成功；
- 当前所有可维护文本文件不超过 3000 行；
- 目录骨架和规则建立不被误写成行情、策略、回测或交易功能已实现。

---

## 30. 前后端工程骨架与最小垂直切片

### 30.1 本章目的

本章纠正早期只部署只读 API、没有用户界面的工程偏差，规定从本阶段开始必须按完整应用组织推进。目标不是一次性完成全部交易功能，而是先拥有一个能独立启动、能在浏览器登录、能进入工作台并读取受保护市场状态的真实项目骨架。

本章的 `implemented` 只表示 Windows 工作树已经形成对应源码和启动配置；只有完成后端测试、前端构建和真实浏览器登录验收后，才能标记为 `verified`。Ubuntu 旧版只读 API 不因本章落码而自动变成新版运行时。

### 30.2 顶层目录合同

新项目使用与现有 A 股量化系统相近的应用级组织方式，但业务核心和运行资源保持独立：

~~~text
crypto-platform/
├─ frontend/              # Vue 3 + TypeScript + Vite 用户界面
│  ├─ src/views/          # 登录和工作台页面
│  ├─ src/stores/         # Pinia 会话状态
│  ├─ src/api.ts          # 同源 API 客户端和 401 处理
│  ├─ Dockerfile          # 前端构建和 Nginx 运行镜像
│  └─ nginx.conf          # /api 同源反向代理
├─ backend/               # FastAPI 应用边界
│  ├─ app/auth/           # 认证服务和依赖
│  ├─ app/api/            # 版本化 API 路由
│  ├─ app/settings.py     # 环境变量配置
│  └─ Dockerfile          # 后端服务镜像
├─ src/                   # 可复用 Crypto Core
│  ├─ domain/             # 领域模型
│  ├─ application/        # 用例编排
│  ├─ ports/              # 外部能力端口
│  ├─ adapters/           # 交易所和运行适配器
│  └─ services/           # Worker 和跨模块服务
├─ contracts/             # 领域、AI、GACE 和 API 合同
├─ tests/                 # 单元、合同、假交易所、集成和验收
└─ compose.yaml           # backend + frontend + 可选 Worker
~~~

`frontend/` 和 `backend/` 是应用部署边界，`src/` 不是被遗弃的旧目录，而是由应用层调用的独立领域核心。后续如果核心代码规模增长，再按职责迁入 `backend/core` 或独立包；本阶段不通过大规模移动破坏已经通过测试的导入路径。

### 30.3 最小垂直切片

第一条完整用户路径固定为：

~~~text
浏览器 /login
→ POST /api/v1/auth/login
→ 返回短时 Bearer 会话
→ 路由守卫恢复 /api/v1/auth/me
→ 进入 / 工作台
→ GET /api/v1/market/overview
→ 展示 Binance、OKX、Bybit 状态
~~~

本切片必须满足：

1. 未认证用户访问 `/` 自动转到 `/login`；
2. 错误账号或密码返回 401，前端显示可理解的错误；
3. 登录成功后只把访问令牌保存到浏览器会话存储位置，不把密码写入日志、源码或镜像；
4. 认证后市场接口必须拒绝缺少或伪造 Bearer 令牌的请求；
5. 工作台明确显示 `DISABLED` 执行模式和各市场数据状态；
6. 登出后令牌清理，重新访问工作台必须再次登录；
7. 未配置认证环境变量时，后端 fail closed，不生成默认生产账号；
8. 本切片不启动真实行情 Worker、不读取私有 API Key、不创建真实订单。

### 30.4 后端应用合同

后端第一阶段使用 FastAPI + Pydantic，组合根位于 `backend/app/main.py`。路由合同如下：

| 方法 | 路径 | 认证 | 用途 |
|---|---|---|---|
| GET | `/health` | 否 | 进程、版本和执行模式健康检查 |
| POST | `/api/v1/auth/login` | 否 | 校验开发账号并签发短时会话 |
| GET | `/api/v1/auth/me` | Bearer | 恢复当前用户 |
| POST | `/api/v1/auth/logout` | Bearer | 记录登出意图并由前端清理会话 |
| GET | `/api/v1/market/overview` | Bearer | 获取多交易所状态总览 |
| GET | `/api/v1/market/status/{venue_id}` | Bearer | 获取单个交易所状态 |
| GET | `/api/v1/market/snapshot` | Bearer | 读取共享的规范化盘口快照 |
| GET | `/api/v1/history/datasets` | Bearer | 列出已校验的历史 K 线数据集 |
| GET | `/api/v1/history/coverage` | Bearer | 汇总历史数据覆盖、行数和质量 |
| GET | `/api/v1/history/jobs` | Bearer | 查看历史下载任务 |
| POST | `/api/v1/history/jobs` | Bearer | 创建公开历史 K 线下载任务 |
| GET | `/api/v1/history/jobs/{job_id}` | Bearer | 查看历史下载逐项结果 |

本阶段的用户存储是环境变量驱动的单用户开发实现，只为验证边界和浏览器路径。进入多人、生产或 GACE 托管模式前，必须替换为 PostgreSQL 用户、密码哈希、会话撤销、权限和审计表；不能把开发实现直接作为生产认证。

### 30.5 前端应用合同

前端第一阶段使用 Vue 3、TypeScript、Vite、Pinia、Vue Router、Axios 和 Lucide 图标，结构与 A 股量化系统的前端组织保持一致：

- `src/router.ts`：登录路由、受保护工作台和路由守卫；
- `src/stores/auth.ts`：登录、恢复、登出和本地令牌清理；
- `src/api.ts`：同源 `/api/v1` 客户端、Bearer 注入和 401 回登录；
- `src/views/LoginView.vue`：认证入口，不显示真实密钥或交易配置；
- `src/views/WorkspaceView.vue`：市场状态、执行模式和工作区模块切换；
- `src/components/HistoryDataCenter.vue`：公开历史 K 线下载、覆盖率和任务记录；
- `src/styles.css`：响应式工作台样式和可访问焦点状态。

前端页面不是营销落地页，首屏必须是可操作的登录或工作台。后续模块按“行情、市场、策略、回测、模拟、风险、执行、任务”逐步加入，每个模块拥有独立视图和 API 边界，不把全部业务继续堆进一个巨型组件。

### 30.6 独立运行方式

开发环境支持前后端分别启动：

~~~powershell
$env:CRYPTO_DEV_MODE = "true"
$env:CRYPTO_ADMIN_PASSWORD = "local-dev-password"
$env:CRYPTO_SESSION_SECRET = "local-dev-session-secret-change-me"
py -3 -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8290
~~~

~~~powershell
cd frontend
npm install
npm run dev
~~~

Vite 在 `4191` 端口把 `/api` 和 `/health` 转发到本地后端 `8290`。容器模式由 `compose.yaml` 启动 `backend`、`frontend`、历史 `task-worker` 和 `task-scheduler`；Nginx 将 `/api` 代理到 Compose 服务名 `backend`，浏览器不需要跨域访问第二个公开端口。Scheduler 启动即执行一次增量计划，之后按周期检查并在失败时退避；`market-worker` 仍是显式 `market` profile，默认关闭。

### 30.7 安全和运行边界

1. 开发账号只用于本地垂直切片，密码通过环境变量输入；
2. `CRYPTO_DEV_MODE` 只允许本地开发默认值，真实运行必须显式提供密码和会话 Secret；
3. 认证令牌不代表真实交易授权，执行模式继续由服务端固定为 `DISABLED`；
4. 认证接口和市场接口与交易所私有 API 完全分离；
5. Compose 的前后端服务通过 `CRYPTO_BIND_ADDRESS` 发布；Ubuntu 绑定 `10.10.10.129` 仅用于受控局域网和 FRP，未完成访问策略前不得直接暴露公网；
6. Ubuntu 运行端只在本地浏览器验收通过后才接受新版制品，不能用旧 API 的健康检查替代前端验收；
7. 本章不修改 `D:\code\quant-platform`、`D:\code\gace-system` 或任何现有运行容器。

### 30.8 验收门

本章按以下证据顺序推进：

| 门 | 证据 | 通过条件 |
|---|---|---|
| S0 | 目录检查 | `frontend/`、`backend/`、`src/` 和 Compose 文件存在，职责清楚 |
| S1 | 后端单元/验收测试 | 登录、错误密码、令牌、401 和市场接口通过 |
| S2 | 前端类型检查和构建 | `vue-tsc -b` 与 `vite build` 成功 |
| S3 | 本地服务后验 | `/health` 返回当前版本和 `auth=enabled` |
| S4 | 浏览器 DOM 验收 | 未登录转登录页，提交正确账号后进入工作台并显示市场状态 |
| S5 | 负向浏览器验收 | 错误密码被拒绝，登出后受保护页面再次要求登录 |
| S6 | 远端发布 | 仅在 S0-S5 完成后，单独构建、上传、启动和现场验收 |

HTTP 200、静态构建成功或 Swagger 能打开都不能单独通过 S4。S4 和 S5 必须使用真实浏览器 DOM、当前网络响应和当前服务地址确认。

### 30.9 当前执行状态

本章落码后状态记录为：

- S0：implemented，目录和服务边界已建立；
- S1：verified，后端全量测试以最近一次独立命令输出为准，跳过项仅因当前 Windows Python 3.14 没有可用的 `pyarrow<20` 轮子；测试包含登录、错误密码、令牌、401、市场接口、公开品种目录、历史分页、存储幂等、覆盖矩阵任务状态、研究 API、历史价差研究、MACD 长历史计算、历史任务详情/停止/重试、模拟盘、风控、AI、通知、统一任务账本事件和无凭据 fail-closed；
- S2：verified，`vue-tsc -b` 和 `vite build` 均成功；
- S3：verified，新版本地服务返回 `auth=enabled`、`execution_mode=DISABLED`；
- S4：verified，2026-09-15 真实浏览器在 Ubuntu `http://10.10.10.129:4191/login` 提交正确账号后进入工作台，历史页显示远端 18 个已验证数据集和 748,273 根 K 线，自动同步状态显示每 15 分钟运行；直接浏览器网络请求均为 `200`，控制台错误和警告均为 0；本地开发地址和公开品种、研究、模拟盘等既有验收仍按各自证据记录；
- S5：verified，错误密码返回页面错误，登出后工作台受保护路由回到登录页，浏览器直接访问市场接口返回 401；
- S6：runtime-accepted，已通过 SSH Key 传输并在 Ubuntu 构建、绑定 `10.10.10.129`、启动新版 backend/frontend/task-worker/task-scheduler；远端后验确认健康、登录、历史覆盖、质量检查、Parquet、自动 Scheduler、任务账本和前端 HTTP 均通过，并由真实浏览器直接完成登录和历史页验收。交易所公网 Worker、真实账户和真实下单仍保持关闭。

本章完成后，项目具备继续开发行情中心、策略中心、回测中心和模拟交易页面的正确工程承载方式；历史数据中心已实现公开 K 线子集。完成骨架和历史子集不等于全部业务模块完成，也不等于自动交易已经开放。

### 30.10 本章验收

- 目录中同时存在顶层 `frontend/` 和 `backend/`，且两者均有可运行入口；
- 后端认证、前端登录和认证后工作台形成最小闭环；
- Compose 定义 `backend`、`frontend`、默认历史 `task-worker`/`task-scheduler` 和可选 `market-worker`，默认不启用真实交易；
- README、计划书和思维导图描述一致；
- 所有新增可维护文件不超过 3000 行；
- 本地浏览器验收前不把新版前后端部署到 Ubuntu；
- 计划状态区分源码、测试、构建、浏览器和远端运行证据。

截至 2026-09-15，PowerShell 7 的文件规模脚本将在本轮新增文档和页面后重新执行；历史的 301 个文本文件、2,562 行最大值只作为旧快照，不作为当前结果。后续继续扩展计划时应按章节拆分，不能把旧快照当作永久当前值。

---

## 31. 运行计划与 GACE 只读能力实现

### 31.1 设计目的

参考 A 股系统的“交易计划”按交易日历和盘中阶段组织；Crypto 现货没有统一开收盘，运行计划改为 24/7 状态链。它把历史数据、策略、研究、回测、模拟盘、风险和执行边界汇总成一个只读页面，方便独立模式运行，也方便未来 GACE App 读取。

### 31.2 当前实现

- `backend/app/services/runtime_plan.py`：从当前应用状态构造七个运行节点；
- `backend/app/api/runtime.py`：提供认证后的 `GET /api/v1/runtime/plan`；
- `backend/app/services/capability_catalog.py`：生成 GACE 只读能力清单；
- `backend/app/api/capabilities.py`：提供认证后的 `GET /api/v1/gace/capabilities`；
- `backend/app/api/incubators.py`、`src/application/strategy_incubator.py`：提供筛选候选孵化池及来源审计；
- `backend/app/api/tasks.py`、`backend/app/services/history_jobs.py`：提供历史任务详情、停止、请求指纹去重和未完成项重试；
- `frontend/src/components/RuntimePlanCenter.vue`：工作台“运行计划”页面；
- `frontend/src/components/StrategyIncubatorCenter.vue`：工作台“策略孵化池”页面；
- `contracts/gace/capability-catalog-v1.schema.json`：只读能力合同，并纳入合同测试。

### 31.3 状态和边界

运行计划会显示 `ready`、`partial`、`waiting`、`running` 和 `blocked`。`partial` 表示已有可用子集但仍有覆盖缺口；`blocked` 表示安全门禁或数据条件不满足。当前默认状态应为 `safe_paused`，真实执行保持阻断，不能因为页面显示“研究链路就绪”而推断真实交易可用。

Capability catalog 只列 `GET` 和 `READ_ONLY` 能力，`write_capabilities` 固定为空；真实下单和提币显式位于阻断清单。未来 GACE App 或 AI 只能经正式合同调用，不直接导入独立后端内部模块，也不能访问 API Key、数据库或任意 SQL。

历史任务的停止与重试属于独立后台任务控制，不属于 GACE 写入能力。GACE 只读目录可以读取孵化池和任务状态；任何未来的任务提交、候选写入或真实执行 Action 都必须另行定义幂等键、权限、审计和高风险确认。

### 31.4 验收门

| 门 | 要求 | 当前状态 |
|---|---|---|
| RP0 | 源码、路由、类型和合同存在，单文件不超过 3000 行 | implemented |
| RP1 | API 认证、七个节点、747,865 根本地 K 线当前状态和安全阻断可读 | verified |
| RP2 | 浏览器打开运行计划，页面显示当前数据和任务，不产生交易副作用 | verified |
| RP3 | 390px 无横向溢出，刷新后状态仍来自接口 | verified |
| RP4 | Ubuntu 新版运行端独立部署并完成现场后验 | runtime-accepted |

详细字段和限制见 `docs/RUNTIME_PLAN_IMPLEMENTATION.md`。本章的 `implemented` 不代表 GACE Runtime、真实账户或真实交易已经完成。

### 31.5 2026-09-15 继续实施记录：任务基础设施第一阶段

本轮不把已有文档状态当作当前实现，先完成工作树和历史数据的独立复核：本地 18 个 Binance/Bybit 历史数据集真实存在，共 747,865 根 K 线，当前保存数据的连续性缺口和重复均为 0；Ubuntu 后验为 18 个数据集、747,937 根 K 线和 18/18 Parquet。历史数据不是“没有拉取”，但仍属于文件/Parquet 数据层，不能等同于历史元数据完全 PostgreSQL 化。

本轮新增并验证的实现：

- `backend/app/services/task_store.py`：使用 SQLAlchemy Core 建立 `control_tasks` 和 `control_task_events`，统一保存任务状态、进度、生命周期时间、结果、错误、取消标记、版本号和事件；支持幂等键去重；
- `backend/app/services/redis_runtime.py`：对 Redis 协调服务提供不泄露凭据的健康投影；
- `backend/app/services/history_jobs.py`：历史下载任务继续以 JSON 为恢复兜底，同时镜像到统一任务账本；任务详情新增账本和事件读取；
- `backend/app/api/tasks.py`：增加 `GET /api/v1/tasks/{task_id}/events`，任务汇总返回持久化状态；
- `compose.yaml`：加入 PostgreSQL 16 和 Redis 7 服务，生产 Compose 默认要求任务基础设施可用；历史 Worker/Scheduler 默认启动并定时执行增量同步，本地 `Settings` 默认使用内存 SQLite，避免测试依赖外部服务；
- `docs/PARITY_MATRIX.md`：逐项对照原 A 股量化系统的功能和 Crypto 当前状态，明确剩余缺口。

本轮完成的是“持久化和任务运行基座”以及第一批通用异步任务迁移。历史同步、回测、筛选、研究、模拟和策略矩阵已经使用统一任务合同，由 Redis Worker 执行并由 PostgreSQL 记录；local 模式仍保留同步开发回退。租约续期、取消、重试、过期回收、结果详情和跨进程 JSON 读取已覆盖，死信、资源限额、优雅重启恢复和完整日志归档仍待补齐。真实账户、私有 API、自动卖出和 GACE 写能力仍保持阻断。

当前状态：任务账本和通用异步任务 `verified`，本地 SQLite/Redis 路由/接口验证已通过；Ubuntu PostgreSQL/Redis、历史 Worker/Scheduler、18/18 Parquet、任务事件、`10.10.10.129` 绑定和浏览器历史页现场运行验证 `runtime-accepted`；历史 CSV/Manifest `verified`，历史增量 Scheduler 已实现默认启动、活跃任务去重、持久化状态和失败退避，并已完成本轮 Ubuntu 后验；回测、筛选、研究、模拟和策略矩阵的统一 Worker 执行已落码，死信、资源限额和生产级重启恢复 `planned`。

### 31.6 2026-09-15 08:25 UTC 运行端刷新后验

本次仅执行项目自带的只读 `postflight`，没有重新部署、重启容器或启用行情 Worker。Ubuntu `10.10.10.129` 当前 backend 健康、登录认证和前端 HTTP 均通过，执行模式仍为 `DISABLED`；远端历史覆盖为 18 个数据集、748,177 根 K 线，全部 `gap_count=0`、`duplicate_count=0`，Parquet 为 18/18 READY，历史价差研究对齐 105,219 根 K 线、成本模型 30 bps，统一任务账本为 27 条记录。A 股 `quant-platform` 仍有 14 个独立运行服务。本次后验仍不覆盖真实账户、真实下单、自动卖出、实时行情 Worker 或 GACE Runtime。

### 31.7 2026-09-15 09:36 UTC 运行端绑定与自动同步后验

本轮先完成只读 inventory，确认旧运行端仍绑定 `127.0.0.1:8290`/`4191`，再使用已信任的 SSH Key 重新构建并部署，没有覆盖远端历史文件，也没有操作 A 股 `quant-platform` 服务。当前 Compose 后验确认 backend/frontend/task-worker/task-scheduler 均运行，宿主绑定为 `10.10.10.129:8290`/`4191`，backend healthy，`execution_mode=DISABLED`，A 股项目仍有 14 个运行服务。

独立认证后验确认远端有 18 个数据集、748,273 根 K 线、全部缺口和重复为 0、Parquet 为 18/18 READY、任务账本 33 条；`/api/v1/history/sync/status` 返回 Scheduler `active`、周期 900 秒。真实浏览器直接访问 `http://10.10.10.129:4191/login` 完成登录并打开历史页，页面显示自动同步“任务执行中、每 15 分钟”；登录、覆盖、任务、同步计划、归档和 Scheduler 请求均为 HTTP 200，控制台错误和警告均为 0。该后验仍不覆盖真实账户、真实订单、实时行情 Worker、OKX 阻断解除或 GACE Runtime。

### 31.8 2026-09-15 14:29 UTC M2 原始响应归档与远端部署后验

本轮先修复历史持久化切片的两个测试收集错误，再按独立命令重新验证：`py -3 -m pytest -q` 为 `169 passed, 1 skipped`，`compileall` 通过，文件规模检查为 617 个文本文件且没有超过 3000 行，计划书与思维导图为 32 个章节一致；前端 `npm run build` 的 `vue-tsc -b` 和 `vite build` 均通过。唯一跳过项是当前 Windows Python 3.14 没有可用的 `pyarrow<20` 轮子。

本轮使用 SSH Key 将当前源码重新构建并部署到 Ubuntu `10.10.10.129`。独立后验确认 backend、frontend、task-worker、task-scheduler、PostgreSQL 和 Redis 共 6 个 Crypto 服务运行，backend/frontend 发布地址仍为 `10.10.10.129:8290`/`10.10.10.129:4191`，backend healthy，前端 HTTP 为 200，执行模式为 `DISABLED`；A 股 `quant-platform` 仍为独立 Compose 项目并运行 14 个服务。远端历史为 18 个数据集、748,627 根 K 线，所有已验证数据 `gap_count=0`、`duplicate_count=0`，Parquet 为 18/18 READY，历史价差研究对齐 105,288 根 K 线，成本模型为 30 bps，统一任务账本为 39 条。认证 Scheduler 周期为 900 秒，当前因已有 OKX 网络阻断任务处于持久化退避；这不影响 Binance/Bybit 已验证数据，也不产生占位数据。

为验证 M2 原始响应链路，使用真实浏览器登录后提交了一个 Binance BTC/USDT 1d、单日窗口的受控公开历史任务。任务响应为 HTTP 202，单项最终为 `completed`；认证读取 `/api/v1/history/raw-responses` 返回 HTTP 200、`status=READY`、合同版本 `history-raw-response-v1`、`response_count=1`、`invalid_count=0`，原始响应文件为 `_raw/binance/spot/BTCUSDT/1d/...json`。只读容器检查确认合同字段正确，敏感字段匹配数为 0。未认证访问同一路径返回 HTTP 401，说明新接口仍受保护。真实浏览器刷新后历史页显示 18/27、748,627 根 K 线、18/18 Parquet、任务已完成；浏览器控制台错误和警告均为 0。

本节证明的是当前 M2 持久化切片、独立运行端、认证和历史任务后验，不证明实时行情 Worker、真实账户、真实订单、自动卖出、提币、OKX 网络可用或 GACE Runtime 已完成。

### 31.9 2026-09-16 任务可靠性第二切片

本轮继续按 M2 任务运行基座推进，范围限定为研究、历史下载、回测、筛选、研究、模拟和策略矩阵等后台任务，不触碰真实账户、私有 API、真实订单或 GACE 写 Action。

新增实现：

- `backend/app/services/task_quota.py`：服务端资源配额，限制任务运行时间、序列化请求大小、结果大小和处理项数量；Worker 在启动、进度和完成检查点执行配额门禁，超限进入 `blocked`，不自动重试；
- `backend/app/services/task_store.py`：为既有 `control_tasks` 增加尝试次数、最大尝试次数、Worker、租约过期时间和死信时间字段；启动时对旧表执行幂等补列，任务可记录租约恢复和 `dead_lettered` 终态；
- `backend/app/services/task_queue.py`：Redis envelope 带 `attempt`/`max_attempts`，新增死信列表、死信元数据读取和租约回收详情；重试和恢复先写目标队列再清理旧消息，缩小 Redis 写入失败时的丢任务窗口；
- `src/services/task_worker.py`：领取时记录 SQL 租约，租约过期后同步恢复为 `queued`；可重试异常按次数重新入队，超过上限进入 Redis DLQ 和 SQL `dead_lettered`，不可重试输入错误直接进入死信；重复领取已终态任务只确认消息，不重复执行；
- `backend/app/api/tasks.py`：增加认证后的 `GET /api/v1/tasks/dead-letters`，任务摘要和任务中心识别死信状态；前端任务中心将死信显示为异常并保留人工重试入口；
- `backend/app/settings.py`、`backend/.env.example`、`compose.yaml`：增加任务尝试次数、租约和资源配额配置，生产默认值有界且可通过环境变量调整。

本地证据：`py -3 -m pytest -q` 为 `176 passed, 1 skipped`；`py -3 -m compileall -q backend src tests` 通过；前端 `npm run build` 的 `vue-tsc -b` 和 `vite build` 通过；`check-file-line-limit.ps1` 通过，`docs/PROJECT_PLAN.md` 仍处于 2400 行拆分预警范围但未超过 3000 行。测试覆盖配额拒绝、死信搬运、租约回收、SQL 租约恢复、自动重试、死信终态、认证死信 API 和旧任务状态兼容。

当前状态：源码和本地自动验证为 `verified`；Ubuntu 新版尚未部署，不能把上一版运行端后验扩展到本切片。完成远端 Compose 校验、镜像重建、容器切换、任务状态和浏览器登录后，再单独记录 `runtime-accepted`。远端 Redis 的死信列表是运行数据，部署不得清空现有队列、任务账本或历史数据卷。

本切片仍不等于完整生产级任务系统：Redis/SQL 双写尚未形成原子事务，死信重放审计、租户级资源配额、优雅停机排空、结果对象存储和完整日志归档仍待后续；领域快照从 JSON 迁移到 PostgreSQL 已完成当前运行端后验，OKX 公网阻断、实时行情长期归档、真实账户、真实执行和 GACE Runtime 仍按原计划推进。

### 31.10 2026-09-16 领域快照 PostgreSQL 主存储切片

本轮继续按“独立运行优先、运行状态可验证”的主计划推进，范围限定为有界领域状态的一致性，不触碰真实账户、私有 API、真实订单或 GACE 写 Action。领域快照源码变更已随独立 Compose 部署到 Ubuntu，部署使用 `--skip-history`，保留既有历史和控制面数据；本节以下证据只接受本轮独立后验。

新增实现：

- `backend/app/services/task_store.py`：新增 `control_domain_snapshots` 表和 SQLAlchemy Core 读写接口，以命名空间保存策略、策略包、策略矩阵、币池、筛选、回测、研究、模拟盘、风险、通知、新闻、建议、机会和 AI 助手等有界状态；写入在单个 SQL 事务中完成并递增版本；
- `src/adapters/standalone/state_store.py`：新增 `SqlStateStore`，SQL 行是主真相；仅当 SQL 行不存在时导入旧 JSON，成功 SQL 提交后再尽力写恢复副本，SQL 已有记录时不会回读旧 JSON；
- `backend/app/services/domain_state.py`、`backend/app/services/history_scheduler_state.py`、`backend/app/services/history_sync.py`、`src/services/task_scheduler.py`：历史 Scheduler 和同步状态接入 `crypto.runtime.*` SQL 快照命名空间，继续保留 JSON 恢复副本；同步计划和最近任务读取前刷新共享状态；
- `backend/app/main.py`、`backend/app/services/system_status.py`：核心 API/Worker 服务统一接入命名空间快照，并将领域快照健康度、数量和后端投影到系统状态；
- `src/application/strategy_incubator.py`：孵化池跨进程写入前刷新最新 SQL 快照，避免长生命周期 Worker 用旧内存覆盖 API 的新成员；
- `tests/unit/test_domain_state.py`、`tests/unit/test_strategy_incubator.py`、`tests/unit/test_history_scheduler.py`、`tests/unit/test_history_sync.py`：覆盖旧 JSON 首次迁移、跨实例读取、跨实例合并、快照健康状态和 SQL 优先于恢复副本。

本轮 Windows 源码证据：全量测试 `181 passed, 1 skipped`；跳过项仍是 Python 3.14 环境缺少可用的可选 `pyarrow` 轮子。编译、前端构建、计划/文件门禁已通过；随后使用 SSH Key 完成 Ubuntu 独立部署，并保留现有 PostgreSQL/Redis 数据卷、任务账本、死信和历史数据。远端后验确认 `control_domain_snapshots` 表 10 条（8 条领域快照和 2 条运行态快照）、认证系统状态、跨进程 Worker 读取、`10.10.10.129:8290/4191` 绑定和真实浏览器运行计划页面。

本切片不等于所有状态都已数据库化：历史任务恢复文件、品种目录缓存和 `runtime/market` 最新盘口仍属于独立运行/缓存边界；Scheduler 与同步状态已改为 `crypto.runtime.*` PostgreSQL 主存储并保留 JSON 恢复副本。实时行情长期归档、生产用户/会话库、真实账户、真实执行、自动卖出、GACE Runtime 和 GACE AI 实际调用继续保持未完成或阻断。下一步是治理剩余历史任务文件状态、优雅重启恢复和结果归档，之后才进入 Testnet 私有 API。

### 31.11 2026-09-16 历史任务账本主恢复与损坏保护切片

本轮继续按主计划治理剩余历史任务状态，范围限定为历史下载任务的跨进程恢复、进程重启保护和账本损坏处理，不触碰真实账户、私有 API、真实订单、自动卖出或 GACE 写 Action。

新增实现：

- `backend/app/services/task_store.py`：任务列表增加 `kind` 过滤，历史恢复只读取 `history_download` 记录，避免其他后台任务污染历史任务投影；
- `backend/app/services/history_jobs.py`：启动时先读取 PostgreSQL 任务账本，再解释 `.history_jobs.json`；有效 SQL 明细覆盖同 ID 的旧 JSON，JSON 只作为首次迁移/恢复副本；正常状态变更先同步 SQL，再更新 JSON；
- `backend/app/services/history_jobs.py`：增加任务状态、逐项状态、进度、结果和错误结构校验；缺失 SQL 账本的活动任务标记 `interrupted`，损坏账本优先用 JSON 恢复并写入 `task_ledger_corrupt` 事件，两边都不可恢复时保留可见 `failed` 任务；
- `tests/unit/test_history_jobs.py`：增加 SQL 终态覆盖旧 JSON、缺失账本进程重启、损坏账本恢复审计和无 JSON 失败保护回归。

本轮 Windows 源码证据：`py -3 -m pytest -q` 为 `186 passed, 1 skipped`，`py -3 -m compileall -q backend src tests` 通过。随后使用 SSH Key 和 `--skip-history` 将当前源码重新构建并部署到 Ubuntu `10.10.10.129`，保留现有历史数据、PostgreSQL、Redis、任务队列和 A 股独立 Compose。独立后验确认六个 Crypto 服务运行，前后端绑定 `10.10.10.129:8290/4191`，backend healthy、前端 HTTP 和认证登录均为 200，执行模式为 `DISABLED`；历史为 18 个数据集、`749,467` 根 K 线，质量为 `gap0_duplicate0`，元数据和 Parquet 均为 `18/18`，历史价差对齐 `105,417` 根，任务账本为 41 条，Scheduler 周期为 900 秒且状态为 `active`，任务持久化为 `ready`。

真实浏览器直连 `http://10.10.10.129:4191/login` 完成登录后，历史数据页可读取覆盖、同步计划、下载任务并展开任务详情；运行计划页显示 `18/27`、`749,467`、`SAFE_PAUSED`、`DISABLED` 和 26 个只读能力。登录、市场、历史、同步计划和运行计划请求均返回 HTTP 200；390x844 视口 `documentWidth=375`、`bodyWidth=375`，无横向溢出，控制台错误和警告均为 0，截图为 `output/playwright/runtime-plan-390-20260916.png`。本切片当前状态为 `runtime-accepted`，不是所有系统功能完成。

本切片完成历史任务“PostgreSQL 主恢复、JSON 恢复副本、缺失/损坏保护”的目标；结果对象归档、当前任务优雅排空和死信重放审计的源码、自动测试与本轮 Ubuntu 运行端后验已完成。Redis/SQL 双写原子性、生产级停机恢复、完整日志归档和对象存储生命周期仍未完成；在这些边界完成前不进入 Testnet 私有 API。

### 31.12 2026-09-16 任务结果归档与 Worker 优雅排空后验

本轮继续按任务可靠性顺序推进，范围限定为离线历史、回测、筛选、研究、模拟和策略矩阵任务，不触碰真实账户、私有 API、真实订单、自动卖出、提币或 GACE 写 Action。

新增并验证的实现：

- `backend/app/services/task_result_archive.py`：定义 `task-result-v1` 结果合同，使用任务身份分片和内容 SHA-256 寻址；结果文件先写临时文件、`fsync` 后原子替换，并在读取时校验路径边界、合同、任务身份、字节数和摘要；
- `backend/app/services/task_dispatcher.py`、`src/services/task_worker.py`：API 与独立 Worker 共用 `.runtime/task-results`；完成任务的 SQL 结果只保存归档引用，完成事件只保存状态摘要和归档引用，不重复写入完整结果；Worker 仍会在收到 SIGTERM 后停止领取新任务并完成当前任务，Compose 为 Worker 保留 30 秒停机窗口；
- `backend/app/api/tasks.py`：增加认证后的 `GET /api/v1/tasks/{task_id}/result`，由运行端校验归档对象后返回完整结果和引用元数据；`compose.yaml`：backend 默认明确使用 Redis 队列，避免生产运行端回退到 API 进程本地执行。

本地证据：定向归档、任务分发、Worker 和控制面 API 测试 `19 passed`；全量 `py -3 -m pytest -q` 为 `197 passed, 1 skipped`，跳过项仍为 Python 3.14 环境缺少可用的可选 `pyarrow` 轮子。`py -3 -m compileall -q backend src tests`、前端 `npm run build`、计划/文件规模门禁均通过；计划书追加本节后仍未超过 3000 行。

部署前先用 SSH Key 完成只读 inventory，确认远端已有 6 个 Crypto 服务、18 个历史 CSV/Manifest、A 股 `quant-platform` 14 个服务和现有数据卷；随后只执行一次 `--skip-history` 部署。独立远端后验确认 backend/frontend/task-worker/task-scheduler、PostgreSQL 和 Redis 仍运行，前后端仍绑定 `10.10.10.129:8290/4191`，容器非 root、只读根文件系统、`CRYPTO_TASK_QUEUE_MODE=redis`、执行模式为 `DISABLED`，Redis 队列/处理中/DLQ 均为 0，历史为 18 个数据集、`749,467` 根 K 线、`gap0_duplicate0`、Parquet `18/18`，A 股服务未受影响。

真实浏览器重新登录远端后提交一条 Binance BNB/USDT 1d 离线回测，提交响应为 HTTP `202`，任务由独立 Worker 以 `attempt=1` 完成。PostgreSQL `control_tasks.result_json` 只包含 `task-result-v1` 引用，完成事件 payload 只有 `summary` 和 `archive`，没有完整回测字段；容器内归档对象大小为 `25290` 字节，SHA-256 与引用一致。认证读取 `/api/v1/tasks/{task_id}/result` 返回 HTTP `200`、`completed`、归档状态 `READY`，结果可恢复。真实浏览器在 `390x844` 下 `documentWidth=375`、`bodyWidth=375`，控制台错误和警告均为 0，当前证据截图为 `output/playwright/task-result-20260916-mobile.png`。

当前状态：结果归档、跨进程结果读取、完成事件去重和 Worker 当前任务优雅排空为源码/自动测试 `verified`，本次 Ubuntu 运行端结果链路为 `runtime-accepted`。Redis/SQL 双写尚未形成原子事务，死信重放审计、租户级资源隔离、生产级停机恢复、完整日志归档和对象存储生命周期仍未完成；在这些边界完成前不进入 Testnet 私有 API，不开放真实账户、真实订单、自动卖出、提币或 GACE 写能力。

### 31.13 2026-09-16 死信重放审计切片

本轮继续按任务可靠性顺序推进，范围限定为 Redis Worker 的失败任务恢复，不触碰真实账户、私有 API、真实订单、自动卖出、提币或 GACE 写 Action。死信重放只使用 PostgreSQL 任务账本中的原始 payload，Redis DLQ 继续不保存完整 payload；当前代码仍保持服务端执行模式 `DISABLED`。

新增实现：

- `backend/app/services/task_store.py`：新增 `control_task_dead_letter_replays` 审计表；以唯一 `request_id` 原子预留重放，记录 actor、reason、源任务/目标任务、原 attempt/max_attempts 和有界结果/错误摘要；请求、成功和失败事件只保存引用与摘要，不复制任务 payload 或完整结果；
- `backend/app/services/task_queue.py`：新增 DLQ 重放方法，使用 SQL 恢复的 payload 生成一次新 envelope，并在原 DLQ 元数据上写入重放引用；重复 request_id 不产生第二个 Redis 消息；
- `backend/app/services/task_dispatcher.py`、`backend/app/api/tasks.py`：新增认证 `POST /api/v1/tasks/dead-letters/{task_id}/replay`；目标任务 ID 按源任务和 request_id 内容寻址，普通 retry 对 `dead_lettered` 源任务 fail-closed；
- `backend/app/services/history_jobs.py`：历史下载死信重放创建确定性目标 job，保持 `history_download` 的 job 状态、SQL ledger、JSON 恢复投影和 Redis envelope 一致；
- `src/services/task_worker.py`：SQL `dead_lettered` 事件写入 Redis DLQ 引用，不重复写入队列 payload；`frontend/src/components/TaskCenter.vue`：死信按钮调用审计重放 endpoint；
- `tests/unit/test_task_queue.py`、`tests/unit/test_task_store.py`、`tests/unit/test_task_dispatcher.py`、`tests/unit/test_task_worker.py`、`tests/unit/test_history_jobs.py`、`tests/acceptance/test_control_plane_api.py`：覆盖 Redis 重放幂等、SQL 审计事件、通用任务、历史下载、Worker 引用、认证 API 和普通 retry 拒绝。

本轮 Windows 源码证据：定向任务/历史/控制面测试 `36 passed`；全量 `py -3 -m pytest -q` 为 `202 passed, 1 skipped`，跳过项仍是 Python 3.14 环境缺少可用的可选 `pyarrow` 轮子。`py -3 -m compileall -q backend src tests`、前端 `npm run build` 的 `vue-tsc -b`/`vite build` 均通过；文件规模门禁无超限或接近阈值文件，当前 `docs/PROJECT_PLAN.md` 为 2050 行。

本轮独立部署和后验确认：六个 Crypto 服务仍运行，backend/frontend 继续绑定 `10.10.10.129:8290/4191`，A 股 `quant-platform` 仍为 14 个运行服务；PostgreSQL/Redis 容器和数据卷保留，历史为 18 个数据集、`749,467` 根 K 线、`gap0_duplicate0`、Parquet `18/18`，执行模式为 `DISABLED`。PostgreSQL 直接查询确认 `control_task_dead_letter_replays` 表存在；Redis queue、processing 和 DLQ 均为 0，DLQ 检查未发现完整 `payload` 字段。认证 `/api/v1/tasks/dead-letters` 返回 HTTP `200`，未知任务的 replay 路由返回预期 `404`，结果接口返回 HTTP `200`、`task-result-v1` 和 `READY` 归档引用。由于当前 DLQ 为空，本轮没有创建或重放真实死信任务，因此不把“重放成功事件”写成运行端事实。

真实浏览器直连 `http://10.10.10.129:4191/login` 登录后，运行计划页显示 `18/27`、`749,467`、`SAFE_PAUSED`、`DISABLED` 和 26 个只读能力；任务中心显示 43 条任务，任务详情可读取完成事件和归档结果。浏览器动态请求均返回 HTTP `200`，控制台错误/警告为 `0/0`；运行计划和任务中心在 `390x844` 下的 `documentWidth=375`，无横向溢出。

当前状态：死信重放 SQL reservation、Redis DLQ 引用、request_id 幂等、通用任务/历史任务路径、认证 API、完成事件摘要化、结果归档和 Worker 当前任务优雅排空为源码/自动测试 `verified`，上述边界已完成本轮 Ubuntu `runtime-accepted` 后验。Redis/SQL 双写原子性、租户级资源隔离、生产级停机恢复、完整日志归档、对象存储生命周期和真实死信恢复演练仍待完成；在这些边界前继续禁止 Testnet 私有 API，不开放真实账户、真实订单、自动卖出、提币或 GACE 写能力。

本切片不等于完整生产级任务系统：Redis/SQL 双写仍未形成原子事务，结果恢复演练、生产级停机恢复、完整日志归档、对象存储生命周期和租户级资源隔离仍待完成；在这些边界完成前继续禁止进入 Testnet 私有 API，不开放真实账户、真实订单、自动卖出、提币或 GACE 写能力。

### 31.14 2026-09-16 Bybit 公共 L2 行情运行端后验

本轮在任务结果归档和死信重放审计之后，继续补齐“真实公开行情能否进入运行端”的最小闭环。范围只包含 Bybit 公共现货 L2，不触碰 API Key、账户、私有 WebSocket、真实订单、自动卖出、提币或 GACE 写 Action；服务端执行模式仍为 `DISABLED`。

本轮运行端证据：

- 使用现有 SSH Key 完成一次当前源码的 `--skip-history` 部署；没有上传或覆盖历史 CSV/Manifest，没有清空 Redis，没有重建 PostgreSQL/Redis 数据卷，A 股 `quant-platform` 保持 14 个独立运行服务；
- 独立后验确认 7 个 Crypto 容器均为 `running`，backend 为 `healthy`，七个容器重启次数均为 0，backend/frontend 仍绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`；
- Ubuntu 运行端通过环境变量显式启用 `market-worker`，allowlist 仅为 `bybit:BTCUSDT,bybit:ETHUSDT,bybit:BNBUSDT`，WebSocket 为 `wss://stream.bybit.kz/v5/public/spot`。Worker 首次 REST 快照请求均返回 HTTP 200；规范化状态通过 `runtime/market` 写入，backend 以只读挂载读取；
- 独立读取确认 3 个快照都存在、每组有 50 买档和 50 卖档；6 秒窗口内 BNB 和 ETH 序列继续增长，接收时间前移，Worker 无需重启；
- 真实浏览器直连 `http://10.10.10.129:4191/login` 登录后打开行情中心，页面显示 Bybit BTC/ETH/BNB 的“实时”只读盘口、买一/卖一、序列和 `BYBIT · READ ONLY`，Binance/OKX 仍显示无实时公共通道；行情刷新、状态、三组快照、历史覆盖和目录请求均返回 HTTP `200`；
- 390x844 视口 `documentWidth=375`、`bodyWidth=375`，无横向溢出，浏览器控制台错误和警告为 `0/0`。本轮截图为 `output/playwright/market-live-postflight-390-20260916.png`、`output/playwright/market-live-postflight-desktop-20260916.png` 和 `output/playwright/market-live-section-desktop-20260916.png`。

当前状态：Bybit 公共 L2 的 REST 快照、WebSocket 更新、序列/深度门禁、跨进程文件状态桥、认证 API 和真实浏览器展示已达到有界 `runtime-accepted`；源码默认仍关闭行情 Worker。Binance/OKX 实时通道、跨交易所统一实时接入、盘口长期归档、PostgreSQL/Redis 行情存储、断线长期恢复演练和交易能力仍未完成或受网络阻断。上述运行端证据不代表可成交、不代表账户权限，也不改变 Redis/SQL 双写、生产级停机恢复、完整日志归档、对象存储生命周期和租户隔离尚未完成的结论；在这些边界前继续禁止 Testnet 私有 API 和真实交易。

### 31.15 2026-09-17 当前运行端与本地门禁复核

本轮从上一未完成切片继续，先修正 `scripts/remote_deploy.py` 后验脚本的嵌套 f-string；定向结果归档、任务分发、Worker、任务账本和历史任务测试为 `17 passed`。随后独立运行全量门禁：`py -3 -m pytest -q` 为 `212 passed, 1 skipped`，`py -3 -m compileall -q backend src tests scripts`、前端 `npm run type-check`、`npm run build` 和 3000 行文件门禁均通过；唯一跳过项是当前 Python 3.14 没有可用的可选 `pyarrow` 轮子。

本轮没有重新部署或重启 Compose。使用现有 SSH Key 执行只读 `postflight`，确认 Ubuntu `10.10.10.129` 的 7 个 Crypto 容器均运行，重启次数均为 0，backend/market-worker 健康，前后端仍绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`；A 股 `quant-platform` 仍有 14 个运行服务。认证后验报告 24 个历史数据集、978,695 根 K 线、`gap0_duplicate0`、24/24 Parquet、105,492 根历史价差对齐、45 条任务账本和 900 秒 `active` Scheduler；Redis queue、processing、DLQ 均为 0，任务持久化为 `ready`。

公开行情后验确认当前运行端 allowlist 精确为 `binance:BTCUSDT,bybit:BTCUSDT`；两路公共 WebSocket 归一化盘口均为 `CONNECTED`，序列和新鲜度有效，OKX 未进入健康 allowlist并保持过期/阻断。上轮遗留的 Bybit ETH/BNB 最新状态文件未删除，而是由 root 权限的只读镜像容器移动到 `/home/flybace/crypto-platform/runtime/market-disabled-snapshots-20260917`，活动目录仅保留 BTC 快照。

真实浏览器直连 `http://10.10.10.129:4191/login` 完成登录并打开行情中心：Binance、Bybit 显示实时只读 L2 买一/卖一和序列，OKX 显示过期；登录、市场总览、状态、快照、历史和品种目录请求均返回 HTTP `200`，控制台错误和警告均为 `0/0`。`390x844` 视口测得 `documentWidth=375`、`bodyWidth=375`，无横向溢出，截图为 `output/playwright/market-390-20260917.png`。

结果归档 `task-result-v1`、完成事件摘要化、死信重放审计和 Worker 当前任务优雅排空仍保持源码、自动测试与既定运行端边界内的 `runtime-accepted`；本轮因远端 DLQ 为空没有创建或重放真实死信任务。Redis/SQL 双写原子性、生产级停机恢复、完整日志归档、对象存储生命周期、租户隔离、私有账户、真实交易和 GACE 写能力仍未完成或保持阻断。

### 31.16 2026-09-17 最新源码部署与租约恢复复核

本轮使用现有 SSH Key 完成一次当前源码的 `--skip-history` 部署，保留既有 PostgreSQL、Redis、任务账本、死信队列和历史数据；未清空 Redis、未重建数据卷，A 股 `quant-platform` 未修改。部署后独立后验确认 7 个 Crypto 容器均运行且重启次数为 `0`，backend/market-worker healthy，前后端绑定 `10.10.10.129:8290/4191`，执行模式仍为 `DISABLED`。

本地源码和自动验证为：结果归档、任务分发、Worker、任务账本和控制面定向回归 `37 passed`；全量 `py -3 -m pytest -q` 为 `224 passed, 1 skipped`；Python 编译、前端类型检查/构建、计划一致性和 3000 行文件门禁通过。跳过项仍是 Python 3.14 没有可用的可选 `pyarrow` 轮子。

远端认证后验确认 24 个历史数据集、`978,941` 根 K 线、`gap0_duplicate0`、Parquet `24/24`、`105,568` 根历史价差对齐、45 条任务账本和 `active/900s` Scheduler；Redis queue、processing、DLQ 和 claim 标记均为 `0`，SQL outbox 有 1 条 `published` 记录。Worker 日志确认回收 1 条旧 processing 租约，当前未出现建表唯一约束竞态。结果接口返回 HTTP `200`、任务 `completed`、合同 `task-result-v1` 和归档状态 `READY`。

本次后验未制造或重放真实死信任务，因为远端 DLQ 为空。结果归档、完成事件摘要化、死信重放审计路由和当前任务优雅排空继续保持既定只读边界内的 `runtime-accepted`；Redis/SQL 双写原子性、生产级停机恢复、完整日志归档、对象存储生命周期、租户隔离、私有 API、真实交易和 GACE 写能力仍未完成或保持阻断。

### 31.17 2026-09-17 结果归档部署后独立后验复核

本轮从 31.16 的源码与运行端边界继续，先完成全量本地门禁，再使用现有 SSH Key 只部署一次当前源码。部署命令使用 `--skip-history`；没有上传或覆盖历史 CSV/Manifest，没有清空 Redis，没有重建 PostgreSQL/Redis 数据卷，也没有修改 A 股 `quant-platform`。

本轮本地证据：`py -3 -m pytest -q` 为 `238 passed, 1 skipped`；定向结果归档、任务分发、Worker、控制面回归为 `37 passed`；`compileall`、前端 `npm run type-check`、`npm run build`、计划一致性和 3000 行文件门禁均通过。唯一跳过项是 Windows Python 3.14 没有可用的可选 `pyarrow` 轮子；文件行数预警为 `task_store.py` 2457 行、计划书 2825 行，均未超过硬上限。

部署前只读 inventory 和部署后独立后验均确认 Ubuntu `10.10.10.129` 的 7 个 Crypto 容器运行，重启次数为 `0`、重启策略为 `unless-stopped`，backend/market-worker healthy，前后端绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`；A 股 `quant-platform` 仍有 14 个运行服务。远端保留 24 个历史数据集、978,941 根 K 线、`gap0_duplicate0`、Parquet `24/24` 和 105,568 根历史价差对齐；Scheduler 自动调度仍开启，现场持久化状态为 `backoff/900s`，原因是已有失败或阻断同步任务进入退避，不把它误写成 `active`。

独立 Redis/SQL 检查确认 queue、processing、DLQ 和 claim marker 均为 `0`；SQL 任务表 39 条，dispatch outbox 有 1 条 `published`，任务事件归档 4701 条全部 `READY`，死信重放审计表为 0 条。Worker 心跳为 `running` 且没有 active task。认证结果接口返回 HTTP `200`、任务状态 `completed`、合同 `task-result-v1`、归档 `READY`；同一任务的日志接口返回 HTTP `200`，3 个事件均可通过归档恢复。

真实浏览器直连 `http://10.10.10.129:4191/login` 登录后，行情中心显示 Binance/Bybit 新鲜公共只读 L2、OKX 过期状态；任务中心显示 45 条任务、0 条运行中，任务详情可读取回测结果。动态接口请求均为 HTTP `200`，控制台错误/警告为 `0/0`；`390x844` 视口 `documentWidth=375`、`bodyWidth=375`，无横向溢出，结果页验收截图为 `output/playwright/task-result-postflight-390-20260917.png`。

本切片达到 `runtime-accepted` 的范围是：结果对象跨进程读取、完成事件摘要化、完整任务事件归档读取、死信审计路由、Worker 当前任务优雅排空和租约/心跳恢复边界。远端 DLQ 为空，本轮没有制造真实死信或宣称重放成功。Redis/SQL 原子双写、生产级停机恢复演练、对象存储生命周期、租户隔离、私有 API、真实账户、真实订单、自动卖出、提币和 GACE 写能力仍未完成或保持阻断；在这些边界完成前不进入 Testnet 私有 API。

### 31.18 2026-09-17 受控停机恢复源码切片

本轮继续处理 Redis Worker 的生产生命周期边界，范围仍限定为历史、回测、筛选、研究、模拟和策略矩阵等离线任务，不触碰私有 API、真实账户、真实订单、自动卖出、提币或 GACE 写 Action。当前代码和测试先独立完成，部署后验不沿用历史结果。

新增实现：

- `backend/app/services/task_dispatcher.py`：增加 `TaskExecutionInterrupted` 和可注入的 Worker 停机事件；任务只在明确的安全检查点响应停机，不把受控停机误记为用户取消；
- `src/services/task_worker.py`：Worker 收到停机请求后不再领取新任务；当前任务在安全检查点中断时，通过统一恢复路径清除当前 SQL 租约、保留同一 attempt，并让 SQL outbox 产生一次可重试的重新投递意图；已完成的当前任务仍允许正常提交结果；
- `backend/app/services/task_store.py`：将 Worker 失联恢复方法泛化为 Worker 失联和受控停机共用的事务边界，SQL 状态变更与恢复 outbox 意图在同一事务内完成；调用方仅在 SQL 成功返回后确认旧 Redis claim，恢复事件随后通过既有事件归档路径追加；
- `tests/unit/test_task_worker.py`、`tests/replay/test_task_recovery_drill.py`：覆盖停机安全检查点重新排队，以及“停机进程 -> 重启进程 -> outbox relay -> 完成任务”的隔离演练。

本地证据：`py -3 -m pytest -q` 为 `240 passed, 1 skipped`；受控停机和恢复相关定向回归为 `32 passed`；`py -3 -m compileall -q backend src tests scripts`、前端 `npm run type-check`、`npm run build` 均通过。唯一跳过项是 Windows Python 3.14 没有可用的可选 `pyarrow` 轮子。当前工作树尚未部署本切片，因此 Ubuntu 运行端状态仍只接受 31.17 的独立后验。

当前状态：受控停机协作中断、SQL 恢复和 outbox 重新投递为源码/自动测试 `verified`；生产运行端的非空任务停机、重启、队列保留、任务唯一完成和浏览器/服务后验待本轮单次应用部署后确认。Redis/SQL 原子双写、对象存储生命周期、租户级隔离、完整灾备恢复、私有 API、真实账户、真实订单、自动卖出、提币和 GACE 写能力仍未完成或保持阻断；在这些边界完成前不进入 Testnet 私有 API。

### 公开 API 出口与按交易所代理配置源码说明（2026-09-17）

本轮先处理真实接入的网络前置，不启用账户、私有 API、真实订单、自动卖出、提币或 GACE 写能力，执行模式继续保持 `DISABLED`。目标是让受限交易所可以由操作员在设置页手动填写代理，并让历史、目录、实时行情和连接测试使用同一份配置。

新增实现：

- `src/adapters/venues/proxy.py`：校验绝对 HTTP/HTTPS 代理 URL，拒绝 SOCKS、目标路径、查询参数和片段；返回页面与接口使用的脱敏地址；
- `backend/app/services/public_network_settings.py`：以 `public-network-settings-v1` 原子保存三家交易所的 HTTP/REST 与 WebSocket 代理，保存文件位于历史数据卷 `.runtime`，仅返回脱敏值；
- `backend/app/api/settings.py`、`frontend/src/components/NetworkSettingsCenter.vue`：增加认证后的网络设置读取、保存、公开 HTTP 探测和按交易所手动填写页面；
- `src/adapters/venues/httpx_transport.py`、`websockets_transport.py`、历史服务、目录服务、Scheduler、Task Worker 和 Market Worker：接入显式代理；行情 Worker 监听配置文件变化并自动重连；
- `tests/unit/test_public_network_settings.py`、`tests/acceptance/test_network_settings_api.py` 及受影响链路回归：覆盖凭据脱敏、保存/清除、鉴权和非 HTTP 代理拒绝；Scheduler 兼容注入式测试构造。

本地源码证据：代理相关定向回归 `25 passed`，全量 `py -3 -m pytest -q` 为 `244 passed, 1 skipped`；Python 编译、前端类型检查/构建和 3000 行文件门禁通过。唯一跳过项是当前 Windows Python 3.14 没有可用的可选 `pyarrow` 轮子。

目标 Ubuntu `10.10.10.129` 的只读直连复核为 Binance HTTP `200`、Bybit 区域端点 HTTP `200`，OKX 公共时间接口 12 秒超时；当前 `.env` 和保存配置均没有代理值。源码切片尚未把任何代理地址写入运行端；下一步是使用 `--skip-history` 单次部署，独立验证设置 API、脱敏文件、三家探测、实时 Worker 重连和浏览器页面。此切片只解决网络配置能力，不把 OKX 代理可用、实时全市场接入或交易能力写成已完成。

### 31.19 2026-09-17 受控停机恢复运行端后验

本轮从 31.18 的源码切片继续，先使用现有 SSH Key 做只读 inventory，再只执行一次当前源码的 `--skip-history` 部署。部署没有上传或覆盖历史 CSV/Manifest，没有清空 Redis，没有重建 PostgreSQL/Redis 数据卷，也没有修改 A 股 `quant-platform`；前端验收复用已有认证会话，未重复部署。

本轮本地与远端证据：

- 本地全量 `py -3 -m pytest -q` 为 `240 passed, 1 skipped`；受控停机和恢复相关定向回归为 `32 passed`；`compileall`、前端 `npm run type-check`/`npm run build`、计划一致性和 3000 行文件门禁均通过。唯一跳过项是 Windows Python 3.14 没有可用的可选 `pyarrow` 轮子；工作树仍未提交 Commit。
- Ubuntu `10.10.10.129` 独立后验确认 7 个 Crypto 容器均运行、重启次数为 0、重启策略为 `unless-stopped`，backend/market-worker healthy，前后端绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`；A 股项目仍有 14 个运行服务。
- 远端保留 24 个历史数据集、978,941 根 K 线、`gap0_duplicate0`、Parquet `24/24` 和 105,568 根历史价差对齐；Scheduler 自动调度仍开启，持久化状态为 `backoff/900s`，原因是已有失败或阻断同步任务触发退避。
- Redis queue、processing、DLQ 和 claim marker 均为 `0`；SQL `control_tasks` 为 45 条，`control_task_events` 为 5028 条且全部 `READY`，dispatch outbox 为 8 条 `published`、0 条 pending，死信重放审计为 0 条。任务中心 API 和真实浏览器页面合并显示 99 条任务，这是 SQL 账本与历史任务投影的合计，不把它误写成 SQL 表行数。
- 非空目标任务 `pool-backtest:321cab867f99442783a3c2df573a4126` 经 Worker 停机、重启和 outbox 恢复后唯一完成，`attempt=1`；任务结果引用保持 `task-result-v1`，归档为 `READY`、188,285 bytes，任务行与完成事件仅保存摘要和归档引用，未重复保存完整回测结果；13 个任务事件全部可通过事件归档恢复。
- 真实浏览器直连 `http://10.10.10.129:4191/login` 登录后，任务中心目标详情显示 13 条生命周期事件、`requeued_after_shutdown` 和最终 `completed`；动态请求均为 HTTP `200`，控制台错误/警告为 `0/0`。桌面验收截图为 `output/playwright/acceptance-live/task-center-postflight-desktop-20260917.png`，`390x844` 截图为 `output/playwright/acceptance-live/task-center-postflight-390-20260917.png`；移动视口实测 `documentWidth=375`、`bodyWidth=375`，无横向溢出。

当前状态：受控停机协作中断、SQL 恢复、outbox 重新投递、非空任务唯一完成、结果归档跨进程读取、完成事件摘要化和有界任务事件归档已达到源码/自动测试/Ubuntu `runtime-accepted`。这只是离线任务的有界恢复演练，不等于 Redis/SQL 原子双写、完整生产级日志归档、对象存储生命周期、租户隔离或完整灾备恢复；远端 DLQ 为空，本轮没有制造或宣称真实死信重放成功。私有 API、真实账户、真实订单、自动卖出、提币和 GACE 写能力继续保持阻断，在上述可靠性边界完成前不进入 Testnet 私有 API。

### 31.20 2026-09-17 公开 API 出口与按交易所代理配置运行后验（前一运行端快照，已被 31.21 覆盖）

本轮完成真实公开 API 出口的配置前置，范围仍限定为公开 HTTP/WebSocket 行情、历史和品种目录，不触碰 API Key、账户、私有 WebSocket、真实订单、自动卖出、提币或 GACE 写 Action；服务端执行模式继续保持 `DISABLED`。

本地源码和自动验证：

- `src/adapters/venues/proxy.py`、`HttpxJsonTransport` 和 `WebsocketsJsonConnector` 支持显式 HTTP/HTTPS 代理，拒绝 SOCKS、目标路径、查询参数和片段；代理凭据只允许在运行端使用，接口和页面返回脱敏值；
- `GET/PUT /api/v1/settings/network` 与 `POST /api/v1/settings/network/probe` 已接入认证控制面；历史 Worker、Scheduler、品种目录、Task Worker 和 Market Worker 共享同一份按交易所配置，Market Worker 监听变更并自动重连；
- 网络设置、连接器和受影响链路定向回归 `25 passed`；全量 `py -3 -m pytest -q` 为 `244 passed, 1 skipped`，`compileall`、前端类型检查/构建、计划一致性和 3000 行文件门禁均通过。唯一跳过项是 Windows Python 3.14 没有可用的可选 `pyarrow` 轮子。

目标 Ubuntu `10.10.10.129` 已用现有 SSH Key 完成一次 `--skip-history` 部署，并独立后验确认：7 个 Crypto 容器运行且重启次数均为 `0`、重启策略为 `unless-stopped`，backend/market-worker healthy，前后端绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`；历史为 24 个数据集、`979,859` 根 K 线、`gap0_duplicate0`、Parquet `24/24`；直接 SQL 只读计数为 `control_tasks=46`、`control_task_events=5226`、`control_task_dispatch_outbox=9`，outbox 全部为 `published`；Redis queue/processing/DLQ/claim marker 均为 `0`，A 股 `quant-platform` 仍有 14 个运行服务。

当前 Ubuntu 代理来源为 `.env`，保存文件 `data/history/.runtime/public-network-settings.json` 尚未创建：Binance 和 Bybit 的 HTTP/WS 代理为空，OKX 的 HTTP/WS 代理为现有 `http://192.168.68.183:7897`。真实浏览器登录 `http://10.10.10.129:4191/login` 后进入“网络设置”，页面实际显示三家卡片、两个通道字段、脱敏后的 OKX 地址和 `DISABLED`；三个 HTTP 测试结果为 Binance `REACHABLE/200/used_proxy=false`、Bybit `REACHABLE/200/used_proxy=false`、OKX `BLOCKED/NETWORK_ERROR/used_proxy=true`。因此当前判断是 Binance、Bybit 不需要梯子，OKX 需要可达备用出口；现有代理完成了配置接入但没有解决 OKX 的出口/TLS 连接，不能标记为 OKX 已接入。

浏览器动态请求均为 HTTP `200`，控制台错误/警告为 `0/0`；`390x844` 视口测得 `documentWidth=375`、`bodyWidth=375`，无横向溢出，截图为 `output/playwright/network-settings-390-20260917.png`。结果归档、完成事件摘要化、死信重放审计、Worker 当前任务优雅排空和非空任务停机恢复仍按 31.19 的源码/测试/Ubuntu 运行端结论有效；Redis/SQL 双写原子性、生产级完整日志归档、对象存储生命周期、租户隔离和真实私有 API 仍未完成或保持阻断。

### 31.21 2026-09-17 OKX 公共实时行情与按交易所接口配置运行后验

本轮完成按计划书要求的真实公开行情前置：只启用公开 REST/WebSocket，使用设置页保存的代理接入 OKX，不接 API Key、私有 WebSocket、账户、订单、提币或 GACE 写 Action；执行模式继续为 `DISABLED`。

本轮源码与自动验证：

- `PublicNetworkSettingsStore` v2 为三家交易所分别保存公共 REST、公共 WebSocket、历史 REST、HTTP 代理和 WebSocket 代理，并兼容已有 v1 文件；设置页可手动修改接口根地址，修改后后端立即应用，历史/Scheduler 下次任务应用，Market Worker 自动重连。
- `GET/PUT /api/v1/settings/network` 与 `POST /api/v1/settings/network/probe` 已通过认证控制面；代理凭据不回显，地址协议、query、片段和越界输入有校验。每个交易所的品种、盘口、历史 K 线和探测 REST 路径也可在同一设置中修改，并热应用到目录、历史任务、Scheduler 和 Market Worker。
- 当前工作树全量 `py -3 -m pytest -q` 为 `262 passed, 1 skipped`；`compileall`、前端 `npm run type-check`、`npm run build` 和 3000 行文件门禁均通过。唯一跳过项是 Windows Python 3.14 没有可用的可选 `pyarrow` 轮子。

目标 Ubuntu `10.10.10.129` 已使用现有 SSH Key 完成一次 `--skip-history` 部署，保留 PostgreSQL、Redis、历史数据、任务账本和 A 股项目。独立后验确认 7 个 Crypto 容器运行，backend/market-worker healthy，前后端绑定 `10.10.10.129:8290/4191`，重启次数为 `0`，allowlist 精确为 `binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`。保存配置实际使用 OKX `http://192.168.68.186:7897`；OKX 公共品种与盘口 REST 返回 HTTP `200`，状态为 `CONNECTED`，规范化快照有真实买卖盘，序列在观察窗口持续增长。

真实浏览器登录 `http://10.10.10.129:4191/login` 后，网络设置页显示三家交易所的三类接口地址、HTTP/WS 代理字段和脱敏 OKX 代理；OKX HTTP 测试为 `200`，WebSocket 测试为“握手成功”。行情页显示 Binance、OKX、Bybit 三路实时只读盘口及买一/卖一/序列；390x844 视口 `documentWidth=375`、`bodyWidth=375`，控制台错误/警告为 `0/0`，无横向溢出。

当前结论：网络设置、OKX 公共 REST、OKX 公共 WebSocket、Market Worker 实时状态桥和浏览器展示达到 `runtime-accepted`；状态桥仍是最新值桥接，不是长期行情归档，不代表可成交报价。历史 K 线长期增量归档、PostgreSQL/Redis 行情存储、Redis/SQL 原子双写、完整日志/对象存储生命周期、租户隔离、私有账户、真实交易和 GACE 写能力仍未完成或保持阻断；在这些边界完成前不进入 Testnet 私有 API。

### 31.22 2026-09-17 当前运行端与配置后验复核

本节覆盖 31.21 之后的独立只读复核，未重新部署、重启或清理任何容器、Redis、PostgreSQL 数据卷或历史数据，也未修改 A 股 `quant-platform`。本次确认的是当前运行事实，不回写历史章节中的旧数字。

- 本地全量 `py -3 -m pytest -q` 为 `270 passed, 1 skipped`；网络设置、OKX 公开适配器、Market Worker、任务结果归档、任务分发、Worker 和控制面回归为 `40 passed`；Python 编译、前端类型检查/构建和 3000 行文件门禁在本节文档更新前已通过。唯一跳过项是 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。
- Ubuntu `10.10.10.129` 只读后验确认 7 个 Crypto 容器均运行，重启次数均为 `0`，策略均为 `unless-stopped`；backend/market-worker healthy，前后端仍绑定 `10.10.10.129:8290/4191`，执行模式为 `DISABLED`，A 股项目仍有 14 个运行服务。
- 历史覆盖为 27 个数据集、`1,095,935` 根 K 线，全部 `gap_count=0`、`duplicate_count=0`，Parquet `27/27`；自动 Scheduler 周期为 900 秒，SQL outbox pending 为 `0`，任务事件归档未就绪数为 `0`。
- Redis 当前 queue、processing 和 claim marker 分别为 `0/0/0`；存在 1 条真实 `history_download` DLQ 记录 `history:1f698be863de4cf2a976028a79095688`，失败原因是旧任务缺少序列化查询，`attempt=1`，重放审计为 `0`。本轮没有删除、重放或伪造成功事件。
- 认证结果接口返回 HTTP `200`、任务状态 `completed`、合同 `task-result-v1`、归档状态 `READY`；同一非历史任务的 SQL 结果只含 `archive` 引用，完成事件只有 `archive` 与 `summary`，日志接口归档为 `READY`，没有把完整结果重复写入事件。
- 真实浏览器登录 `http://10.10.10.129:4191/login` 后，网络设置页显示 Binance、OKX、Bybit 各自的三类接口地址和 HTTP/WS 代理字段；OKX 代理脱敏显示，HTTP 测试为 `200`、WS 测试为“握手成功”。行情页显示三路实时只读 L2 买一/卖一和序列，OKX 序列在观察窗口持续增长；390x844 无横向溢出，控制台错误/警告为 `0/0`。

当前结论仍是：公开网络配置、OKX 公共实时行情和结果归档的有界运行路径达到 `runtime-accepted`；这不等于长期行情归档、行情 PostgreSQL/Redis 持久化、Redis/SQL 原子双写、完整日志/对象存储生命周期、租户隔离、私有账户、真实交易或 GACE 写能力已经完成。REST 根地址和固定路径可手动维护，交易所返回 JSON 结构、字段语义、请求参数和 WS 订阅消息仍属于适配器合同，发生协议级变更时仍需代码升级。死信重放只具备审计路由和本地验证，当前运行端没有重放成功证据；在可靠性边界完成前不进入 Testnet 私有 API。

### 31.23 2026-09-18 按交易所 API 路径设置与 OKX 实时复核

本节记录本轮源码扩展及其独立后验。目标仍是公开只读行情，执行模式保持 `DISABLED`，不接入 API Key、私有账户、真实订单、提币或 GACE 写能力。

- 新增 `src/adapters/venues/api_routes.py`，为 Binance、OKX、Bybit 提供品种目录、盘口快照、历史 K 线和连通性探测四类默认 REST 路径；路径校验拒绝完整 URL、query、fragment、空白和模板占位符。
- `PublicNetworkSettingsStore` 继续兼容 `public-network-settings-v1/v2`，新保存内容保留旧字段并加入四类 API 路径；设置页在每个交易所卡片中显示公共 REST/WS/历史根地址、四类 REST 路径以及 HTTP/WS 代理。
- API 路径变化会被后端目录服务、历史服务、Task Worker、Scheduler 和 Market Worker 共享；保存后目录和历史服务热更新，行情 Worker 通过配置指纹变化自动重连。WS 地址仍可直接改完整地址，WS 消息格式和 JSON 响应规范化继续由适配器负责。
- 受影响的 REST/历史 Fake Exchange、设置 API、代理、Market Worker 和 Scheduler 回归已补齐；本地完整测试、编译、前端构建和 3000 行门禁结果，以及 Ubuntu 部署后的服务、端口、OKX HTTP/WS、三路实时状态和浏览器验收，统一记录在本节末尾的后验结果中。

当前结论：按交易所手动维护接口根地址、WS 地址、历史根地址、REST API 路径和代理已经形成可运行实现；它解决域名、区域入口、API 版本路径和出口变化，不承诺交易所改动 JSON 协议后无需适配器代码。长期行情归档、Redis/SQL 行情持久化、原子双写、私有 API、真实交易和 GACE 写能力仍保持未完成或关闭。

### 31.24 2026-09-18 部署后独立后验

本轮使用现有 SSH Key 对 `10.10.10.129` 完成一次 `--skip-history` 部署。部署前完成只读 inventory，部署中未清空 Redis、未重建 PostgreSQL 数据卷、未覆盖历史数据和任务账本，也未触碰 A 股 `quant-platform`。应用镜像更新为 `crypto-platform/backend:0.1.0` 和 `crypto-platform/frontend:0.1.0`，7 个 Crypto 容器运行，前后端仍绑定 `10.10.10.129:8290/4191`，执行模式仍为 `DISABLED`。

- 源码/测试：全量 `py -3 -m pytest -q` 为 `298 passed, 1 skipped`；`compileall`、前端类型检查、生产构建和可维护源文件 3000 行门禁通过。唯一跳过项是 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。
- 网络/接口：认证 `GET /api/v1/settings/network` 返回 `public-network-settings-v2`；Binance、OKX、Bybit 的品种目录、盘口、历史 K 线、探测四类 REST 路径均可读写和校验。运行端保存文件兼容 `public-network-settings-v1`，服务对外统一返回 v2。OKX 使用保存的 `http://192.168.68.186:7897`；本轮浏览器通过该代理实测 HTTP `200`、WebSocket 握手成功。独立后验确认三路公共行情均为 `CONNECTED`，OKX 序列为 `81258294857`，年龄约 `0.084s`。
- 数据/运行：历史覆盖 27 个数据集、`1,097,069` 根 K 线、27/27 Parquet；自动 Scheduler 为 `active`、周期 900 秒；SQL 任务持久化为 `ready`，任务账本为 145 条。`market-worker` 为 `healthy`，allowlist 为 `binance:BTCUSDT,bybit:BTCUSDT,okx:BTC-USDT`，Redis、PostgreSQL 和历史数据均保留。
- 浏览器：真实浏览器登录 `http://10.10.10.129:4191/login` 后，网络设置页显示每个交易所的根地址、四类 API 路径、HTTP/WS 代理字段和脱敏 OKX 代理；OKX 页面测试显示 HTTP `200 · 831 ms`、WS 握手成功 `1484 ms`。行情页显示三路实时只读 L2 买一/卖一/序列；OKX 序列从 `81258360427` 变为 `81258373132`，接收时间持续更新。既有移动视口和控制台验收仍为无横向溢出、错误/警告 `0/0`。

本节不扩大功能边界：实时行情当前仍写入 `runtime/market` 最新状态桥，不是长期行情归档；Redis/SQL 行情持久化、原子双写、完整日志/对象存储生命周期、租户隔离、私有 API、真实订单、自动卖出、提币和 GACE 写能力继续未完成或关闭。在这些可靠性边界完成前不进入 Testnet 私有 API。

### 31.25 2026-09-18 当前只读运行复核

本次只读复核没有重新部署、重启、清理 Redis 或 PostgreSQL，也没有修改 A 股项目。全量测试为 `301 passed, 1 skipped`，Python 编译、前端生产构建和 3000 行门禁通过；唯一跳过项仍是 Python 3.14 缺少可用的可选 `pyarrow` 轮子。

Ubuntu `10.10.10.129` 当前运行 7 个 Crypto 容器，前端/后端绑定 `4191/8290`，Market Worker healthy，执行模式为 `DISABLED`。历史为 27 个数据集、`1,097,294` 根 K 线、`27/27` Parquet，Scheduler 为 `active/900s`，任务账本为 151 条；Redis、PostgreSQL、历史数据卷和 A 股服务均保留。

设置页已经实现按 Binance、OKX、Bybit 分别修改公共 REST、公共 WebSocket、历史 REST 根地址、品种/盘口/K 线/时间四类 REST 路径，以及 HTTP/WS 代理；OKX 当前通过保存的 `192.168.68.186:7897`，HTTP `200`、WS 握手成功。Market Worker 的 `binance:BTCUSDT`、`bybit:BTCUSDT`、`okx:BTC-USDT` 均 `CONNECTED`，OKX 序列在浏览器观察窗口持续增长。

这项配置可覆盖域名、区域入口、API 版本路径和代理变化；若交易所修改请求参数、JSON 字段语义或 WebSocket 消息协议，仍需升级对应适配器代码。实时状态当前是最新值桥接，不是长期行情归档；Redis/SQL 行情原子双写、私有 API、真实交易和 GACE 写能力继续关闭或未完成。

### 31.26 2026-09-18 Redis delivery receipt 丢失修复（源码切片）

本轮继续可靠性主线，处理“Redis 队列仍有唯一正确 envelope，但 delivery receipt marker 丢失”的故障矩阵。该切片只修改源码和自动测试，未部署、未重启远端服务，也未修改 PostgreSQL、Redis 或 A 股项目。

- `TaskDispatchConsistency` 现在同时记录 SQL receipt 和当前 Redis receipt；已发布的活动投递不再只相信 SQL 上一次的 `REDIS_ACCEPTED`。
- 当状态为 `QUEUED`/`PROCESSING`、匹配 envelope 恰好一条且无 mismatch、duplicate 或 DLQ 时，`audit_and_repair()` 使用原固定 `message_id` 调用 `ensure_enqueued()`，补回 marker 并刷新 SQL receipt；不会重新插入重复消息。
- `SETTLED_NO_PUBLISH` 的终端任务保持合法结算，不会因为没有 live Redis marker 被误报为异常；mismatch、unknown、duplicate 和 DLQ 仍 fail-closed。
- 新增 marker 丢失回归，验证 repair 前 `degraded/UNVERIFIED`、repair 后 `REDIS_ACCEPTED` 且队列仍为一条。

本地证据：全量 `py -3 -m pytest -q` 为 `302 passed, 1 skipped`；一致性/队列/TaskStore 定向回归为 `50 passed`，Worker/Dispatcher/控制面为 `24 passed`；Python 编译、前端生产构建和 3000 行文件门禁通过，跳过项仍为 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。

当前边界：本切片尚未形成 Ubuntu 运行端证据，远端仍以 31.25 的只读后验为准；Redis/SQL 仍不是跨系统原子事务，完整日志归档、对象存储生命周期、灾备恢复和真实 DLQ 隔离重放仍待完成。在这些边界完成前继续禁止 Testnet 私有 API。

### 31.27 2026-09-18 最新部署后验：代理、接口路径、OKX 实时行情与结果归档

本轮使用现有 SSH Key 完成一次 `--skip-history` 部署。部署前完成只读 inventory，部署中保留 PostgreSQL、Redis、历史数据和任务账本，未清空 Redis、重建 PostgreSQL 数据卷或修改 A 股 `quant-platform`；执行模式继续为 `DISABLED`。

- 本地全量 `py -3 -m pytest -q` 为 `307 passed, 1 skipped`；Python 编译、前端生产构建和 3000 行可维护源文件门禁通过。唯一跳过项是 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。
- Ubuntu 独立后验确认 7 个 Crypto 容器运行，前后端绑定 `10.10.10.129:8290/4191`，Market Worker 健康，Scheduler 自动运行；只读后验报告 27 个历史数据集、`1,097,726` 根 K 线、`27/27` Parquet、`active/900s` Scheduler、`ready` 任务持久化和 166 条任务账本；历史数据、PostgreSQL、Redis 和任务账本均保留。
- 设置页按 Binance、OKX、Bybit 分别支持公共 REST/WS/历史根地址、品种/盘口/K 线/时间四类 REST 路径、HTTP 代理和 WebSocket 代理；路径、域名、区域入口或出口改变时可手动维护，JSON 字段语义和 WebSocket 协议改变仍需适配器升级。
- OKX 使用保存的 `http://192.168.68.186:7897`，公共 HTTP 探测返回 `200`、WebSocket 握手成功；`binance:BTCUSDT`、`bybit:BTCUSDT`、`okx:BTC-USDT` 三路公共 L2 均为 `CONNECTED`，OKX 序列在浏览器观察窗口持续增长。
- 结果接口返回 `task-result-v1`、归档状态 `READY`；完成事件只保留归档引用与摘要。死信重放请求、排队、失败和终态结算使用有界审计事件，不把完整结果重复写入事件表。

当前边界不变：实时行情是 `runtime/market` 最新值桥，不是长期行情归档；Redis/SQL 行情原子双写、完整日志/对象存储生命周期、租户隔离、私有 API、真实账户、真实订单、自动卖出、提币和 GACE 写能力仍未完成或关闭。在这些可靠性边界完成前不进入 Testnet 私有 API。

### 31.28 2026-09-18 全市场公开 24H 行情中心部署后验

本轮继续保持只读边界，只新增公开 REST ticker 聚合和前端展示，不启用私有 API、真实账户、订单、自动卖出、提币或 GACE 写能力；执行模式仍为 `DISABLED`。

- `backend/app/services/public_tickers.py` 新增 Binance、OKX、Bybit ticker 格式归一化、按币种合并、24H 涨跌/区间/成交额/价差计算、搜索、排序、分页和 5 秒缓存；三家 REST 请求并行执行，单一交易所阻断时保留其他市场数据。缺失时间不会被序列化为字符串 `None`。
- `GET /api/v1/market/tickers` 已受认证保护；`frontend/src/components/PublicTickerBoard.vue` 在行情中心展示三家价格、24H 变化、区间位置、跨市场价差、成交额，并提供计价资产、搜索、排序、分页和 10 秒刷新。
- 本地 `py -3 -m pytest -q` 为 `317 passed, 1 skipped`；行情定向测试、Python 编译、前端生产构建和 3000 行门禁均通过；唯一跳过项是 Windows Python 3.14 缺少可用的可选 `pyarrow` 轮子。
- 一次 `--skip-history` 部署完成后，Ubuntu 仍为 7 个 Crypto 容器，PostgreSQL、Redis、历史数据和任务账本保留，前后端绑定 `10.10.10.129:8290/4191`，Market Worker healthy，A 股项目保持 14 个容器。
- 独立认证接口后验返回 974 个合并 USDT 币种，Binance/OKX/Bybit 分别为 683/406/395；`as_of` 为有效 ISO 时间，执行模式为 `DISABLED`。真实浏览器显示 `3/3` 市场可用、`1-60/974`，搜索 BTC 显示 5 条结果和三家报价；三路 L2 盘口继续更新，控制台 `0/0`，390px 无横向溢出。

当前边界：全市场 ticker 是公开实时快照，不是长期行情归档，也不代表可成交套利信号；跨市场价差只用于研究。Redis/SQL 行情原子双写、长期行情存储、私有 API、真实交易和 GACE 写能力仍未完成或关闭。

### 31.29 2026-09-28 实时 L2 归档治理与币种走势详情远端后验

完整记录迁至 [STATUS_2026_09_28.md](STATUS_2026_09_28.md#3129-2026-09-28-实时-l2-归档治理与币种走势详情远端后验)，内容按原验收日期保留。

### 31.30 2026-09-28 L2 归档治理状态源码切片

完整记录迁至 [STATUS_2026_09_28.md](STATUS_2026_09_28.md#3130-2026-09-28-l2-归档治理状态源码切片)，内容按原验收日期保留。

### 31.31 2026-10-02 M5 真实账户只读接入底座（源码切片）

用户明确要求：先做只读底座，真实账号后续再接（当前无 API Key）；自动交易必须在设置中显式打开才可用，默认关闭。本轮只做只读，不碰下单。

本轮新增（源码 + 自动测试）：

- `src/adapters/venues/binance_account.py`：Binance HMAC-SHA256 签名私有 REST 客户端，只实现只读端点（`GET /api/v3/account` 余额、`GET /api/v3/openOrders` 挂单）；**不实现任何下单/撤单/转账端点**，签名密钥不进日志。
- `src/ports/secrets.py` + `src/adapters/standalone/env_secret_provider.py`：`SecretProvider` 协议与环境变量实现（`CRYPTO_BINANCE_API_KEY` / `CRYPTO_BINANCE_API_SECRET`）；未配置时账户功能明确不可用，不静默降级。
- `src/adapters/venues/binance_account.py` 内 `BinanceReadOnlyAccountGateway`：实现 `ReadOnlyAccountGateway` 协议，`fetch_account()` 返回 `AccountSnapshot`（余额映射为 `Balance`，挂单映射为订单 ID）；网络/签名/权限错误映射为明确异常，不伪造数据。
- `backend/app/api/account.py`：`GET /api/v1/account/status`（是否已配置）、`GET /api/v1/account/balances`、`GET /api/v1/account/orders`；全部需认证、全部只读，无写操作。
- `CRYPTO_AUTO_TRADING_ENABLED`（默认 `false`）：自动交易显式总开关；`GET /api/v1/settings/trading` 查询、`PUT /api/v1/settings/trading` 修改（需认证）。本轮该开关只做状态管理，不接任何交易执行路径（M6 未开始）。
- `frontend/src/components/AccountCenter.vue`：账户页面（只读余额表 + 挂单列表 + 配置状态提示），接入工作台导航。

安全边界（本轮强制）：

- 只读底座不包含任何下单能力；`ReadOnlyAccountGateway` 协议层面就没有写方法。
- API Key/Secret 不经过前端、不进日志、不进 API 响应。
- 自动交易开关默认关闭；后续 M6 的任何执行路径必须先检查该开关，关闭时订单提交路径不可达。

当前边界：真实 Binance 账号尚未接入（用户暂无 API Key）；Bybit/OKX 私有端点未实现；对账调度、持久化账本、安全暂停编排仍是 M5 未完成项。执行模式保持 `DISABLED`。

### 31.32 2026-10-02 M5 对账调度、持久化账本与安全暂停（源码切片）

延续 31.31 的只读账户底座，完成 M5 剩余三项：持久化账本、对账调度、安全暂停编排。仍不涉及真实下单。

本轮新增（源码 + 自动测试）：

- `backend/app/services/account_ledger_store.py`：`AccountLedgerStore`，SQLAlchemy 关系存储，四张表：
  - `account_snapshots`：账户快照历史（余额 JSON、挂单 ID、状态）；
  - `account_ledger_entries`：余额变动条目（资产、delta、事件类型、时间）；
  - `account_reconciliation_runs`：对账运行记录（是否平衡、差异明细）；
  - `account_safety_pause`：单行安全暂停状态（暂停/原因/触发与解除时间/操作人）。
- `backend/app/services/account_reconciliation_scheduler.py`：`AccountReconciliationScheduler`，后台线程每 300 秒一轮：
  1. 经网关拉取快照（未配置时跳过，不静默造数）；
  2. 校验：负余额、ERROR 状态判为异常；
  3. 与上次快照 diff → 余额变动记账本条目；
  4. 持久化新快照，记录对账运行；
  5. 异常（拉取失败、负余额、ERROR 状态、单轮资产跌超 50%）→ 触发安全暂停（fail closed）。
- `backend/app/api/account.py` 新增：`GET /snapshots`、`GET /ledger`、`GET /reconciliation`（调度器状态+最近运行+暂停状态）、`POST /reconciliation/run`（手动触发）、`GET /pause`、`POST /pause/clear`（需认证手动解除）。
- `frontend/src/components/AccountCenter.vue` 新增：安全暂停告警条（带解除按钮）、对账状态面板（调度器运行状态、间隔、网关配置、上次结果、最近 5 次对账表）。
- `main.py` 接线：创建 `AccountLedgerStore` 与调度器，随应用启停；网关未配置时调度器空转跳过。

安全边界：对账异常自动暂停，暂停后需人工在页面确认解除；所有接口需认证；执行模式保持 `DISABLED`；自动交易开关仍默认关闭。

当前边界：真实 Binance 账号尚未接入（用户暂无 API Key），调度器当前空转跳过；M5 至此完成只读账户与风控前置全集。M6 真实下单未开始。

测试：362 passed（11 新增）。

### 31.33 2026-10-02 十大策略真实数据验证（回测后验）

用户要求：策略必须用真实数据验证，不只看代码和测试。本轮对 10 个内置策略做两轮真实历史回测。

**数据集**：Binance BTC/USDT 真实 K 线（1h 最近 7 天 168 根；1d 最近 1 年 365 根）。初始资金 10000 USDT，费率 10bps，滑点 5bps。

**第一轮：1h 7 天（震荡市）**

| 策略 | 收益 |
|---|---|
| buy_and_hold | -0.06% |
| sma_cross | -4.44% |
| momentum | 0%（无交易） |
| inventory_exit | 0%（无交易） |
| trend_breakout | -2.37% |
| rsi_rebound | -1.50% |
| bollinger_breakout | -1.99% |
| macd_reversal | -3.57% |
| volume_momentum | -1.06% |
| volatility_breakout | -1.77% |

结论：震荡市中所有策略都不赚钱，亏损主要来自手续费。策略需要震荡过滤或降频。

**第二轮：1d 1 年（熊市，BTC 基准 -29.79%）**

| 策略 | 收益 | 最大回撤 | 胜率 | 交易数 |
|---|---|---|---|---|
| buy_and_hold | -29.79% | 52.97% | - | 2 |
| volume_momentum | **+5.05%** | 15.82% | 22.7% | 40 |
| macd_reversal | **+4.00%** | 15.37% | 30.8% | 26 |
| volatility_breakout | **+2.74%** | 7.11% | 25.0% | 8 |
| trend_breakout | -11.14% | 19.44% | 22.2% | 18 |
| sma_cross | -12.07% | 20.28% | 28.6% | 14 |
| bollinger_breakout | -13.26% | 26.70% | 14.3% | 14 |
| momentum | -17.55% | 33.80% | 18.8% | 32 |
| rsi_rebound | -18.04% | 25.45% | 17.6% | 34 |
| inventory_exit | 0% | 0% | - | 0（无库存可卖） |

结论：
- 3 个策略在熊市中取得正收益，大幅跑赢 buy_and_hold 基准（-29.79%）：**volume_momentum（+5.05%）、macd_reversal（+4.00%）、volatility_breakout（+2.74%）**。
- volatility_breakout 回撤最小（7.11%），风险调整后最稳；volume_momentum 交易最频繁（40 次），收益最高但回撤也大。
- 其余 6 个策略在熊市中亏损，rsi_rebound 最差（-18.04%）。
- inventory_exit 是 SELL_ONLY 库存退出策略，无初始库存时无交易，符合设计。

**下一步**：对 3 个盈利策略做参数搜索优化，并在 ETH 数据集上做交叉验证，确认不是过拟合。

### 31.34 2026-10-02 策略自动调参与交叉验证（回测后验）

用户要求：系统有回测功能，要跑回测并自动调参。系统此前只有手动参数回测，无自动调参；本轮用网格搜索对 31.33 中盈利的 3 个策略做自动调参，再用 ETH 数据交叉验证防过拟合。

**调参方法**：网格搜索，BTC/USDT 1d（1 年）为训练集，按收益排序、回撤次之。

**macd_reversal**（27 组：fast∈{8,12,16} × slow∈{20,26,32} × signal∈{7,9,11}）

| 参数 | 收益 | 回撤 |
|---|---|---|
| 默认 (12,26,9) | +4.00% | 15.37% |
| 最优 (8,26,7) | **+36.21%** | 11.38% |

**volume_momentum**（27 组：window∈{10,20,30} × min_return∈{0.005,0.01,0.02} × min_volume_ratio∈{1.0,1.2,1.5}）

| 参数 | 收益 | 回撤 |
|---|---|---|
| 默认 (20,0.01,1.2) | +5.05% | 15.82% |
| 最优 (20,0.02,1.2) | **+8.49%** | 12.82% |

**volatility_breakout**（9 组：window∈{10,20,30} × atr_multiplier∈{1.0,1.5,2.0}）

| 参数 | 收益 | 回撤 |
|---|---|---|
| 默认 (20,1.5) | +2.74% | 7.11% |
| 最优 (10,1.5) | **+11.28%** | 4.60% |

**ETH/USDT 1d 交叉验证**（最优参数，不做二次调参）：

| 策略 | BTC 训练集 | ETH 验证集 | 结论 |
|---|---|---|---|
| macd_reversal (8,26,7) | +36.21% | **+17.23%** | ✅ 泛化通过 |
| volume_momentum (20,0.02,1.2) | +8.49% | -10.29% | ❌ 过拟合 |
| volatility_breakout (10,1.5) | +11.28% | -12.67% | ❌ 过拟合 |

**结论**：
- 只有 **macd_reversal(8,26,7)** 通过交叉验证，在 BTC 和 ETH 上均为正收益且大幅跑赢基准，是目前唯一值得继续投入的策略。
- volume_momentum 和 volatility_breakout 的 BTC 最优参数在 ETH 上亏损，判定为过拟合，不建议直接使用。
- 系统暂无内置自动调参 API，本轮调参通过外部脚本调用回测接口完成；如需产品化，建议新增 `POST /api/v1/backtests/tune` 网格搜索端点。

### 31.35 2026-10-02 自动调参系统功能（源码切片）

用户明确：要把自动调参做成系统功能（其 A 股量化系统有此功能），不是用外部脚本跑。本轮将调参能力内置为平台一等功能。

本轮新增（源码 + 自动测试）：

- `backend/app/services/parameter_tuning.py`：`ParameterTuner` 网格搜索服务。输入策略 ID、参数网格、优化指标（总收益率/最大回撤/胜率，默认总收益率）、组合上限（默认 100），对每个参数组合调用 `BacktestRunManager.run` 做回测，按指标排序返回。失败组合计数不中断整体。
- `POST /api/v1/backtests/tune`：调参 API，需认证。请求含 venue/symbol/interval/strategy_id/param_grids/metric/max_combinations 及回测基础配置；响应含 tune_id、各组合收益/回撤/胜率/交易数、最优参数。组合数超限返回 422。
- `frontend/src/components/ParameterTuningPanel.vue`：回测中心新增"自动调参"面板。选择策略后自动按参数 Schema 生成默认网格（整数±4、浮点±30% 各 3 档），可手动改候选值；优化目标可选总收益/最小回撤/胜率；一键调参，结果表高亮最优行，显示前 10 名。
- 6 个单测覆盖：最优选择、空网格拒绝、超限拒绝、非法指标拒绝、越小越优指标、运行失败容错。

验证：
- 后端 368 passed（6 新增）；前端构建成功；真实浏览器确认调参面板渲染正常。
- 实测 `volatility_breakout` 4 组合调参：最优 (window=10, atr_multiplier=1.5) +11.28%，与脚本版结果一致。

当前边界：同步执行，大网格（>100 组合）需分批；未做异步任务队列版本。31.34 的脚本调参结论依然有效。

### 31.36 2026-10-02 自动调参 v2：异步 + 训练/验证 + 过拟合门禁（源码切片）

用户要求：继续完善策略功能（大头），策略要有回测和自动调参，"调到能赚钱为止"；还要接入新闻等消息面。

本轮新增（源码 + 自动测试）：

- `backend/app/services/parameter_tuning.py`：`ParameterTuner.tune()` 新增 `validation_ratio`（默认 0.3）和 `validation_top_n`（默认 10）参数。启用时按时间切分训练/验证窗口：全部组合跑训练窗口，Top-N 跑验证窗口，最终按稳健得分（验证指标 - 0.25×|回撤|）排序。过拟合门禁：仅当训练和验证双窗口都盈利才算通过。
- `backend/app/services/backtest_runs.py`：新增 `dataset_time_range()`，供调参切分窗口用。
- 异步化：`task_dispatcher.py` 新增 `parameter_tune` 任务类型；`src/services/task_worker.py` 新增 `_run_parameter_tune`；`POST /api/v1/backtests/tune` 在 Redis 队列启用时返回 202 排队，否则同步执行。
- 持久化：新增 `backend/app/services/tune_history.py`（调参历史，JSON 落盘，跨进程 mtime 感知重载）和 `backend/app/services/strategy_presets.py`（策略参数预设）；API 新增 `GET/DELETE /api/v1/backtests/tunes`、`GET/POST/DELETE /api/v1/backtests/presets`。
- 前端 `ParameterTuningPanel.vue`：绑定当前数据集（去掉硬编码 binance/BTC/1d）、支持异步任务轮询、验证集比例/Top-N 输入、训练 vs 验证双列结果表、过拟合徽章（双窗口盈利/疑似过拟合/未验证）、调参历史查看、一键"保存为参数预设"。
- 前端 `BacktestCenter.vue`：策略参数区新增"加载预设"；回测结果区新增"消息面时间线"，展示回测区间内的相关新闻（情绪标签+高风险标记）。
- `backend/app/services/news.py` + `backend/app/api/news.py`：新闻事件查询新增 `start_at`/`end_at` 时间过滤，供回测复盘用。
- 9 个单测（3 新增）：验证切分过拟合标记、双窗口盈利通过、非法 validation_ratio 拒绝。

验证：
- 后端 335 passed（unit+integration，acceptance 除外）；前端构建成功。
- 真实链路：`macd_reversal` 在 Binance BTC/USDT 1d 上 2 组合调参经 worker 异步完成，最优 (fast_period=8, slow_period=26)，训练 +1.27%、验证 +9.50%，过拟合门禁通过；结果落盘 tune-history.json 并可通过 API 查询。

当前边界：新闻时间线仅用于复盘展示，回测引擎本身不做消息面过滤；"调到赚钱"指历史双窗口验证通过，不构成实盘盈利保证。

### 31.37 2026-10-02 调参→模拟盘一键应用闭环（源码切片）

用户确认：将调参验证通过的参数一键应用到模拟盘，打通"历史→模拟"最后一步。

本轮新增：
- `ParameterTuningPanel.vue`：最优参数旁新增"应用到模拟盘"按钮，调用 `POST /api/v1/paper/strategy-runs`；未通过过拟合检查时弹出二次确认；支持异步排队提示。
- `BacktestCenter.vue`：预设列表每行新增"模拟盘"快捷按钮，同样支持二次确认和排队提示。

验证：
- 真实链路：`macd_reversal(8,26)`（调参最优）一键应用到模拟盘，worker 异步执行完成，模拟盘收益 +4.00%。
- 前端构建成功。

当前边界：模拟盘为历史回放模式，非实时跟单；策略在模拟盘的表现仍不保证实盘。

### 31.38 2026-10-03 模拟盘自动化跟盯（源码切片）

用户确认：在调参→模拟盘闭环之后，做"模拟盘自动化跟盯"——让模拟盘按周期自动用最新历史重跑策略、跟踪绩效、超阈值告警。

本轮新增：
- `backend/app/services/paper_follow.py`：`PaperFollowService`（跟盯配置 + 快照时间线，上限 60 条，mtime 跨进程重载）与 `execute_paper_follow` 编排（策略回放 + buy_and_hold 基准回放，同一数据集可比）。
- 任务种类 `paper_follow`：`task_dispatcher.py` 的 `TASK_KINDS`/`TASK_PREFIXES` 新增；`task_worker.py` 新增 `_run_paper_follow`，回放只记录、不下单。
- `task_scheduler.py`：历史同步 tick 为 up_to_date 且自动回放与跟盯均启用时，按 `interval_seconds` 投递 `paper_follow` 任务（防重复投递）。
- API：`GET /paper/follow`（配置+快照+最新）、`PUT /paper/follow`（开关/周期/告警阈值）、`POST /paper/follow/run`（手动触发，支持排队）。
- 前端 `PaperTradingCenter.vue`：新增"自动跟盯"面板——周期（每小时~每周）、收益/回撤/跑输市场三档告警阈值、最新快照卡片（策略收益 vs 市场收益 vs 超额 vs 回撤 vs 告警）、快照时间线表格。
- 告警码：`return_below_threshold`（收益跌破阈值）、`drawdown_breach`（回撤超限）、`underperforms_market`（跑输市场）。

验证：
- 单元测试 15 项通过（配置校验、调度门禁、告警判定、快照上限、跨进程落盘、编排逻辑）。
- 全量回归：unit + integration 299 passed。
- 真实链路：Binance BTC/USDT 1h（168 根）`macd_reversal(8,26,7)` 跟盯一次，worker 异步完成，快照：策略 -3.57% / 市场 -0.06% / 超额 -3.51% / 回撤 4.59%，正确触发"收益跌破阈值"告警。
- 前端构建成功，Playwright 截图验证面板渲染（`crypto-shots/28-自动跟盯面板.png`）。

当前边界：跟盯是"周期性历史回放 + 绩效记录"，不是实时逐笔跟单；调度器默认跟盯关闭，需在模拟盘页手动启用；快照不保证未来表现，不得直接用于真钱决策。

### 31.39 2026-10-03 模拟盘完善：回放可带参数、可展开详情

用户要求"完善模拟盘"。本轮把之前埋的坑补上：回放记录只能看汇总、参数看不到、手动回放调不了参。

本轮新增：
- 后端 `paper_follow.py`：`execute_paper_follow` 快照新增 `strategy_parameters`（从策略回放记录的 `parameters.strategy_parameters` 提取），跟盯时间线可知每次用的是哪套参数。
- 前端 `PaperTradingCenter.vue`：
  - "模拟策略回放"面板：按所选策略的 `parameter_schema` 动态生成参数输入（macd_reversal 快/慢/信号线等），提交时带入 `strategy_parameters`，调参结果可直接手动回放验证。
  - "自动策略回放"面板：同理暴露 `strategy_parameters` 输入，保存进自动配置，跟盯调度沿用该套参数。
  - "策略回放记录"：每条可展开——参数摘要、统计（胜率/盈亏笔数/最大回撤/手续费/K线数）、净值曲线柱状图、最近 8 条成交明细。
  - "自动跟盯"时间线：每行可展开——参数、胜率、回放区间、K 线数。

验证：
- 单元测试：`tests/unit/test_paper_follow.py` 新增快照参数断言，15 passed。
- 全量回归：unit + integration 299 passed；前端 `npm run build` 成功。
- 真实链路：手动回放 `macd_reversal(fast=8, slow=26, signal=7)` 完成，记录含参数；跟盯一次，快照含 `{"fast":"8","signal":"7","slow":"26"}`。验证后跟盯与自动回放均恢复关闭。
- Playwright 截图：`crypto-shots/29-模拟盘完善-回放参数.png`、`30-模拟盘完善-回放详情.png`、`31-模拟盘完善-跟盯详情.png`。

### 31.40 2026-10-03 新闻智能第一层：RSS 接入 + 规则智能 + 选币榜 + 策略建议 + 回放门控 + AI 接入

分支：`codex/news-intel`。

做了什么：
- RSS 自动接入：CoinDesk / CoinTelegraph RSS + alternative.me 恐惧贪婪指数，无需 key；scheduler 每 15 分钟自动抓取（`NewsIngestor`，失败不影响 tick），重大事件写 WARNING 日志；`POST /api/v1/news/ingest/run` 可手动触发。
- 规则智能（`backend/app/services/news_intel.py`，无 LLM、确定性、可审计）：品种映射（关键词 + `$BTC` 标签，18 个主流币）、分类（hack/regulation/etf/macro/listing/whale/upgrade 带权重）、加密词典情绪打分（-1~+1）、影响分（热度×情绪极端度×分类权重）、跨来源聚类（标题相似度≥0.5 合并，heat=报道家数）、重大判定（影响≥65 且 heat≥2，或 risk 且影响≥60）。
- 策略建议：`GET /api/v1/news/advice`，每条重大事件按品种生成机器可读建议（暂停新开仓 / 关注做多信号 / 继续观察 + 原因）。
- 消息面选币榜：`GET /api/v1/news/ranking`，72h 按品种加权聚合新闻分与情绪方向。
- 回放新闻门控：`CandleBacktestConfig.news_gate` + `news_block_hours`；引擎按事件 `published_at` 建阻断窗口（只看过去、不偷看未来），风险窗口内拦截新开仓、不拦截离场；结果含 `news_blocked_entries`；API `POST /paper/strategy-runs` 新增 `news_gate` 参数（直连与 worker 两条链路都透传事件快照）。
- AI 接入（前端直连）：`frontend/src/ai/provider.ts`，OpenAI 兼容网关（本地 sub2api），配置存本机浏览器 localStorage，Key 不经过服务端；AI 能力页新增"模型接入"面板（保存 + 测试连接）；新闻事件行新增"AI 解读"按钮，调用户网关生成一句话解读 + 品种影响 + 策略建议。

验证：
- 单元测试：`tests/unit/test_news_intel.py` 10 passed（映射/分类/情绪/聚类/重大/建议/选币榜）；`tests/unit/test_news_gate.py` 5 passed（拦截/不拦离场/忽略未来事件/忽略非风险/窗口过期）。
- 全量回归：unit + integration 314 passed；`compileall` 通过；前端 `npm run build` 成功。
- 真实链路：真实 RSS 抓取 51 条入库 0 错误；选币榜正确识别 NEAR 被黑事件（risk，3 家报道）；重大建议生成正常；sma_cross 在 BTC/USDT 1h 上关门控 12 笔、开门控 6 笔/拦截 3 次；scheduler 重启后自动抓取并告警 6 个重大事件。
- Playwright 截图：`crypto-shots/32-新闻智能-选币榜.png`、`33-AI能力-模型接入.png`、`34-模拟盘-新闻门控.png`。

边界：
- 规则智能不懂语义，情绪词典精度有限；真正的语义解读走用户自备网关（第二层）。
- 新闻仍不直接下单（执行 DISABLED）；门控默认关闭，需用户在回放时显式开启。

### 31.41 2026-10-03 新闻页改版：折叠长尾面板 + AI 解读入口显性化

分支：`codex/news-intel`。

做了什么：
- 页头一句话讲清新闻页用途：自动抓取 → 规则分析（选币榜/策略建议）→ 事件行「AI 解读」看语义分析。
- 事件记录上移到选币榜/策略建议之后；事件行图标按钮改为带文字的「AI 解读」按钮（解读中显示「解读中」）。
- 未配置模型时顶部显示引导横幅，一键跳转「AI 能力」页（`go-assistant` 事件，`WorkspaceView` 切换 section）。
- 「登记研究事件」（手动表单，RSS 自动抓取后很少用）与「行情共振」默认折叠，点击标题展开/收起。

验证：
- 前端 `npm run build` 成功。
- Playwright 真实浏览器验证（登录 → 新闻页）：56 个「AI 解读」按钮、引导横幅、两个折叠面板均正常；截图 `crypto-shots/36-新闻页-改版.png`、`37-新闻页-事件AI按钮.png`、`38-新闻页-折叠面板.png`。

边界：
- 无后端改动；AI 解读仍走用户浏览器直连自备网关，Key 不经过服务端。

### 31.42 2026-10-03 AI 研判：新闻 + 规则 + 技术面综合给出投资建议

分支：`codex/news-intel`。

做了什么：
- 策略建议卡新增「AI 研判」按钮（手动触发，避免烧用户模型额度）；点击后弹窗展示结构化研判。
- 研判 prompt（`frontend/src/ai/provider.ts::judgeAdvice`）把多因素一起喂给模型：新闻事由/依据标题/相关事件摘要 + 规则层结论（建议动作/紧急度/建议时长/仓位指引/四类策略话术）+ 价格技术面（该品种近 1h 涨跌，取自行情共振）。
- 模型按五节输出简体中文：综合方向、置信度、四类策略操作建议、风险提示、有效期；prompt 明确"新闻只是因素之一，不要只看新闻字面下结论"，不预测具体点位。
- 仍走用户浏览器直连自备网关，Key 不经过服务端；研判结果仅供研究参考，不构成投资建议，不下单。
- 中文新闻源结论：金色财经 / PANews / Foresight / 巴比特均无稳定可用 RSS（无 feed、连不上或被 Cloudflare 拦截），不硬接；AI 解读/研判本身输出简体中文，英文原文不影响使用。

验证：
- 前端 `npm run build` 成功；`buildJudgePrompt` 组装测试通过。
- Playwright 真实浏览器验证：12 张建议卡均有「AI 研判」按钮；未配置模型时点击正确提示去 AI 能力页配置（守卫生效）。

边界：
- 手动触发，未做定时自动研判（自动跑会持续消耗用户 DeepSeek 额度，需用户明确批准再做）。

### 31.43 2026-10-03 实盘模拟循环：策略信号直达模拟账号下单

分支：`codex/paper-live`。

背景：此前模拟盘的策略回放与模拟账号是脱节的——回放只记绩效记录，从不在模拟账号上下单；`POST /paper/orders` 只能手单。用户要求"模拟盘用模拟账号先弄起来"。

做了什么：
- 新增 `backend/app/services/paper_live.py::PaperLiveService`：实盘模拟循环。每 tick 取最新 K 线，用 `CandleBacktestEngine.signal_at` 计算最新收盘 K 线的策略信号（不偷看未来），按信号翻仓：BUY 且空仓 → 按 `allocation_ratio` 比例用 quote 余额买入；SELL 且持仓 → 全平。经 `PaperTradingService.submit` 走模拟撮合，只写模拟账本。
- 幂等：每根 K 线最多交易一次（`last_candle_time`）；订单 request_id 按 `paper-live:{venue}:{symbol}:{interval}:{candle}:{side}` 确定性生成，重试不重复下单。
- 调度器（`src/services/task_scheduler.py`）：历史同步 `up_to_date` 后自动跑一轮 `tick()`；失败只记 warning，不影响主流程。默认关闭，用户在模拟盘页显式启用。
- API：`GET /paper/live`、`PUT /paper/live`（配置）、`POST /paper/live/run`（手动执行一次）。
- 前端模拟盘页新增「实盘模拟」面板：市场/品种/周期/策略/参数/仓位比例、启用开关、保存配置、立即执行一次、状态（上次执行/信号/累计下单）与最近成交表。
- 运维：Binance 在本机被地理封锁、Bybit 403，调度器改为 `--venues okx` 只同步 OKX（否则全局退避导致自动循环不触发）；setup.sh 待同步该改动。

验证：
- 单测 `tests/unit/test_paper_live.py` 16 passed（信号→订单决策表、每 K 线只交易一次、确定性 request_id、配置校验）；全量 unit+integration 332 passed。
- 真实链路：启用后手动 tick，OKX BTC/USDT 1h 最新 K 线信号为 null → 正确无交易；前端构建通过；Playwright 真实浏览器验证面板渲染与"运行中"状态。

边界：
- 只碰模拟撮合器，真实执行保持 DISABLED；不连真实 API Key。
- 首次启用不会追补历史信号（避免追旧信号），只对新 K 线动作。
- 回测/调参里"调到赚钱"的边界不变：实盘模拟的盈亏同样不代表未来。
