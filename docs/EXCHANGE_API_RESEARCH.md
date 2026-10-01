# 交易所开放 API 与 QMT 类能力调研

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 调研对象 | Binance、OKX、Bybit、Coinbase Advanced Trade、Kraken，以及 vn.py、Freqtrade、Hummingbot |
| 调研日期 | 2026-09-14 |
| 调研目的 | 确认公开行情、历史数据、账户交易和测试环境能力，判断是否存在 QMT 类官方能力 |
| 适用项目 | D:\code\crypto-platform |
| 状态 | 技术调研快照，不是生产接入验收 |

交易所 API、地区服务、产品覆盖、限频和测试环境会变化。正式开发前必须重新读取官方文档，并使用目标账户、目标网络和目标地区逐项验证。

## 2. 结论摘要

1. Binance 等平台普遍提供公开 REST、公开 WebSocket、私有账户查询和签名交易接口。
2. 部分平台还提供测试网、Demo 或 Sandbox，但测试资金、流动性、撮合行为和生产环境不等价。
3. 交易所 API 是数据和交易通道，不是 A 股 QMT 那种完整的本地量化终端。
4. Binance 官方没有一套可以直接替代 QMT 的本地策略、回测、模拟、风控和跨交易所执行平台。
5. Freqtrade、Hummingbot、vn.py 等第三方框架可以参考或单独试用，但不直接作为本项目 Crypto Core 的依赖。
6. 本项目应在交易所 API 之上建设自己的数据中心、策略 SDK、回测、模拟交易、双腿执行、风控、审计和 GACE 适配能力。

## 3. API 能力分层

### 3.1 公开市场接口

通常无需 API Key，常见内容包括：

- 交易所时间和服务状态；
- 交易对、资产、价格和数量精度；
- Ticker、K 线、成交；
- 买卖一和 L2 订单簿快照；
- 部分交易所的订单簿增量 WebSocket。

这些接口适合 M1 行情接入和 M2 历史补齐，但仍然受到 IP、请求权重、并发和连接数限制。

### 3.2 私有账户和交易接口

需要 API Key、Secret 或等价签名凭据，常见内容包括：

- 余额、账户状态和资产；
- 当前订单、历史订单、成交和持仓；
- 下单、撤单和批量撤单；
- 用户数据 WebSocket；
- 资金划转或提币相关接口。

本项目默认只申请读取和交易权限，禁止提币、内部转账和外部资金转移。真实凭据不得进入源码、前端、镜像、日志或 App 包。

### 3.3 历史数据接口

历史数据的形式并不统一：

- K 线通常最容易获得；
- 成交数据的时间范围和分页方式各不相同；
- L2 订单簿常见的是当前快照和实时增量，完整历史深度往往有限；
- 历史批量下载可能独立于交易 API，保留周期和文件格式需要单独核验。

价差策略不能因为两个交易所都提供 K 线，就认为已经具备可靠的套利回测数据。项目需要从 M1 开始持续归档实时行情，并为每个数据集记录来源、缺口、时间边界和摘要。

## 4. 交易所能力快照

### 4.1 Binance Spot

官方资料：

- [Binance Spot REST API](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)
- [Binance WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)
- [Binance Spot Testnet](https://github.com/binance/binance-spot-api-docs/blob/master/testnet.md)
- [Binance Public Market Data](https://data.binance.vision/)

调研结论：

- REST 文档区分公共市场数据、用户数据和交易相关安全级别；
- 文档提供公开行情、账户查询、下单、撤单和用户数据流的接口说明；
- 市场数据可以使用专用数据 API，WebSocket 支持原始流和组合流；
- WebSocket 连接需要处理 Ping/Pong 和连接生命周期；
- 请求限制按权重和订单频率计算，收到 429 后必须退避，持续违规可能触发更严格的 IP 限制；
- Spot Testnet 适合验证订单参数、订单状态和签名流程，不代表生产流动性、延迟和成交概率；
- 历史批量数据有独立下载入口，但具体品种、时间范围和文件覆盖仍需按数据集检查。

适合本项目作为第一个连接器：先接公开行情和 Testnet 订单语义，不接真实下单。

### 4.2 OKX

官方资料：[OKX API V5 文档](https://www.okx.com/docs-v5/en/)

官方 V5 文档按公共数据、市场、账户、交易和资产等模块组织 REST 与 WebSocket 接口，并覆盖现货及其他产品线。Demo Trading、区域可用性、账户模式和具体接口覆盖需要以目标账户逐项验证，不能只依据网页目录推断生产可用。

适合第二阶段作为公开行情连接器。对于跨市场套利，必须单独保存 OKX 的原生品种代码、精度、最小下单额、费率、订单状态和限频规则。

### 4.3 Bybit

官方资料：

- [Bybit V5 API Introduction](https://bybit-exchange.github.io/docs/v5/intro)
- [Bybit Testnet](https://testnet.bybit.com/)
- [Bybit Python SDK pybit](https://github.com/bybit-exchange/pybit)

调研结论：

- V5 将 Spot、Derivatives 和 Options 统一到同一套 API 规范；
- API 路径按 market、order、position、account、asset 等模块划分；
- 文档提供 REST 和 WebSocket 入口，并提供 Testnet；
- 官方页面列出了 Python 等语言的 SDK 入口；
- 统一 API 不代表不同产品可以共用同一风险模型，现货和合约仍必须使用不同的领域能力。

适合第二或第三个公开行情连接器。合约、保证金和期权不进入首批交易范围。

### 4.4 Coinbase Advanced Trade

官方资料：[Advanced Trade API](https://docs.cdp.coinbase.com/advanced-trade/docs/welcome)

官方提供 Advanced Trade REST 和 WebSocket 文档，并提供 Sandbox 入口。Sandbox 的接口覆盖、订单撮合行为和数据是否为模拟或静态响应，必须按具体 Endpoint 重新测试，不能把 Sandbox 当成生产市场仿真。

可作为后续现货市场和地区可用性对照，不列入首批必接交易所。

### 4.5 Kraken

官方资料：[Kraken API 文档](https://docs.kraken.com/api/)

官方提供 Spot REST 和 WebSocket API 入口。历史深度、测试环境、账户权限和地区可用性需要单独核验。可作为后续市场，暂不作为 M1 的必选连接器。

## 5. 交易所接入时必须抽象的差异

每个 Venue Connector 至少要保存和实现：

- 原生交易所代码与标准化 Instrument 映射；
- Spot、Margin、Perpetual、Futures、Options 等 Market Type；
- 价格、数量和金额精度；
- 最小数量、最小名义金额和步长；
- Maker/Taker 费率和费率资产；
- REST 请求权重、订单频率和 WebSocket 限制；
- 快照、增量、序列号和重建规则；
- 订单状态、成交状态、部分成交和未知状态；
- 交易所时钟、维护状态和错误码；
- 账户余额、可用余额、冻结余额和资产转移能力；
- 连接器版本和官方文档版本。

统一接口不能抹平这些差异。尤其是跨交易所两腿执行，不能只依赖一个通用 `create_order` 函数，还要保留原始请求摘要和交易所响应状态。

## 6. 历史数据与回测判断

### 6.1 数据等级

| 数据 | 可支持的研究 | 主要不足 |
|---|---|---|
| K 线 | 趋势、波动率、低频策略 | 无法还原盘口深度和短时成交 |
| 成交 | 成交回放、粗粒度滑点 | 无法完整还原当时订单簿 |
| L1 | 买一卖一、价差观察 | 无法估计多档深度和排队 |
| L2 | 可成交数量、深度、部分成交模拟 | 数据量大、历史覆盖和重建复杂 |
| L3 | 逐订单和队列研究 | 公开可得性、存储和实现成本更高 |

### 6.2 项目判断

第一阶段用 K 线和成交数据做基础研究，但跨交易所短时价差回测必须逐步引入 L2。历史 L2 不能稳定从所有交易所直接下载，因此要持续采集：

~~~text
WebSocket 快照和增量
→ 序列校验和盘口重建
→ 原始事件归档
→ 标准化 Parquet
→ 数据质量报告和 Manifest
→ 事件回放与模拟撮合
~~~

测试网只验证 API 语义和订单生命周期；Fake Exchange 和录制回放才是自动化测试和故障矩阵的主要依据。

## 7. QMT 类能力对比

### 7.1 QMT 与交易所 API 的边界

| 能力 | 交易所官方 API | QMT 类平台 |
|---|---|---|
| 实时行情 | 通常提供 | 提供统一行情和缓存 |
| 历史数据中心 | 部分提供 | 下载、清洗、版本和数据集管理 |
| 策略运行时 | 通常不提供通用本地运行时 | 提供策略 SDK 和生命周期 |
| 回测 | 通常只有数据接口 | 提供撮合、费用、滑点和报告 |
| 模拟交易 | 测试网或有限 Demo | 提供完整虚拟账户和模拟撮合 |
| 实盘交易 | 提供底层下单接口 | 提供账户、执行、风控和对账 |
| 策略池和任务 | 通常不提供 | 提供策略管理、任务中心和通知 |
| AI 与审计 | 通常不是完整能力 | 可以提供受控 Action 和审计 |

Binance 等平台主要处于左侧的“数据和交易通道”，不会直接给出右侧完整 QMT 能力。

### 7.2 第三方框架对比

| 框架 | 官方定位或可见能力 | 是否直接替代本项目 |
|---|---|---|
| vn.py | 开源量化交易平台，文档包含交易网关、CTA、CTA 回测、价差交易、数据管理和风险管理模块 | 否。可以参考模块边界；具体 Crypto Gateway 和版本状态需逐项验证 |
| Freqtrade | Python 加密货币交易机器人，包含历史数据、回测、Dry-Run、Live、Web UI 和多交易所支持 | 否。更接近可用机器人，跨交易所双腿执行和完整审计需要重新设计 |
| Hummingbot | 开源 Python 框架，强调 CEX/DEX 连接器、做市和套利执行 | 否。更偏执行和做市，研究数据治理、账本和 GACE 合同仍需自行建设 |

官方参考：

- [vn.py](https://www.vnpy.com/docs/cn/)
- [Freqtrade](https://www.freqtrade.io/en/stable/)
- [Hummingbot](https://hummingbot.org/)

第三方项目的支持交易所、许可证、配置格式和生命周期会变化，不能把当前网页上的“支持”直接当成本项目生产验收。

### 7.3 2026-09-17 Ubuntu 公网出口复核（历史观察）

公开 API 是否“需要梯子”不是交易所的固定属性，而是目标主机、DNS、区域 CDN 和当前网络策略的组合结果。本项目把代理配置放在每个交易所自己的 HTTP/REST 与 WebSocket 通道上，支持 HTTP/HTTPS URL，留空直连；代理凭据只在运行端配置文件中使用，API 和页面只显示脱敏值。

对 Ubuntu `10.10.10.129` 的只读直连探测结果如下：

| 交易所 | 公共 HTTP 端点 | 结果 | 当前判断 |
|---|---|---|---|
| Binance | `data-api.binance.vision/api/v3/time` | HTTP `200` | 当前不需要代理 |
| Bybit | `api.bybit-tr.com/v5/market/time` | HTTP `200` | 当前使用区域端点直连 |
| OKX | `www.okx.com/api/v5/public/time` | 12 秒超时 | 当前需要备用出口或代理 |

同一探测还观察到 `www.okx.com` 在该时段解析为 `169.254.0.2`，不能据此推断 OKX 永久不可用。上述表格和 `192.168.68.183:7897` 结论只代表当时的直连/旧代理观察，不代表当前运行状态。`127.0.0.1` 作为代理主机只指向 Ubuntu 本机；如果代理运行在 Windows 或其他机器，应填写 Ubuntu 能访问的局域网地址并确认防火墙放行。

2026-09-18 运行端后验已使用新的局域网出口复核：网络设置 API 返回 `public-network-settings-v2`，Binance/Bybit 继续直连，OKX 的 HTTP 与 WebSocket 代理均为 `http://192.168.68.186:7897`。OKX 公共 REST 探测返回 HTTP `200`，公共 WebSocket 完成订阅并持续产生盘口快照；独立 `market-worker` 的 `okx:BTC-USDT` 状态为 `CONNECTED`，后验观察窗口内序列持续增长。该验证仍只覆盖公开行情，不进入私有 API、真实账户或真实交易。

## 8. 本项目的最终判断

### 8.1 推荐方案

不把 Binance 或任何一家交易所当作 QMT 系统本身，而是：

~~~text
交易所 API
→ Venue Connector
→ 统一行情、账户和订单模型
→ 数据中心与历史归档
→ 策略 SDK 与策略运行时
→ 回测 / Replay / Paper Broker
→ Risk Center 与双腿 Execution
→ 独立 Web UI 或 GACE App
→ GACE AI Action
~~~

### 8.2 接入顺序

1. Binance Spot 公开行情；
2. Binance Spot Testnet 订单语义；
3. OKX 或 Bybit 公开行情，形成至少两个市场的价差比较；
4. Fake Exchange、录制回放和异常故障矩阵；
5. 真实账户只读、余额查询和订单对账；
6. 人工确认、无杠杆、小额、禁提币条件下评估真实现货交易。

### 8.3 禁止的误判

- 有公开 API 不等于可以无限拉取历史数据；
- 有测试网不等于可以证明生产策略盈利；
- 有统一 SDK 不等于交易所规则已经完全相同；
- 有网格机器人不等于拥有 QMT 的策略开发和回测能力；
- 有理论价差不等于扣除手续费、滑点、延迟和资金成本后仍然可获利；
- API 返回成功不等于订单已经成交，超时和 5xx 必须进入未知状态并重新查询。

## 9. 调研来源和现场核验说明

本次调研现场读取了 Binance 官方 Spot API 文档仓库、Binance Spot Testnet 公开接口、Bybit V5 官方文档以及 Hummingbot、Freqtrade、vn.py 官方文档页面。部分交易所文档在当前网络环境出现 TLS 访问限制，因此 OKX、Coinbase Advanced Trade 和 Kraken 的本章内容只作为官方入口和待复核范围，正式实现前必须重新取得目标环境下的页面、接口响应和账户测试证据。

本文件不保存任何 API Key、Secret、Cookie、账户标识或真实交易数据。
