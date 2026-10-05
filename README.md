# Crypto Multi-Market Quant Platform

数字资产多市场量化研究与模拟交易系统：行情、历史数据、回测、调参、策略准入、币池、模拟盘、风控在一个工作台里闭环。

**主链路**：历史数据 → 回测/调参 → 策略准入 → 启动策略 → 独立策略引擎 → 模拟账户自动下单 → 绩效与风控

> ⚠️ 安全底线：服务端执行模式固定为 `DISABLED`，真实下单路径不可达；所有自动下单都发生在**模拟账户**。详见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md) §4。

## 一键安装

需要 [Docker](https://docs.docker.com/get-docker/)（Windows 用 Docker Desktop）：

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh | bash
```

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/flybace/crypto-platform/main/install.ps1 | iex
```

仓库是私有时，先生成只读 token（GitHub → Settings → Developer settings → Personal access tokens → Fine-grained，`Contents: Read-only`，仅选本仓库）：

```bash
export GITHUB_TOKEN=你的token
curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh | bash
```

脚本自动拉代码、生成配置、构建启动。装好后打开 http://127.0.0.1:4191 ，用户名和随机密码打印在终端（仅显示一次）。

- 重复运行脚本 = 更新到最新版
- 卸载：`./uninstall.sh`（加 `--purge` 连数据一起删）

📖 完整使用方法见 **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)**。

## 功能一览

| 模块 | 说明 |
|---|---|
| 交易驾驶舱 | 资产条、策略健康度、待办事项（风控事件/未评级提醒） |
| 行情 | Binance / OKX / Bybit 三家并排对比，A 股式紧凑表格 |
| 历史数据 | K 线同步、质量门禁（缺口/重复校验），不合格不进回测 |
| 回测 | 收益/回撤/胜率/夏普/权益曲线；自动调参带过拟合检查 |
| 策略准入漏斗 | S/A/B/C/D 评级，评分→复测→跨池验证→准入，只晋级不降级；模拟盘要求 A 级 |
| 动态币池 | 手动 / 24h 成交额 TopN / 近 7 天低波动；候选池→人工确认→交易池；成分快照防未来函数 |
| 模拟盘 | 多策略多实例，各独立模拟账户；引擎每 60 秒轮询，信号触发自动下单（模拟） |
| 市场状态风控 | BTC 日线评估偏强/中性/偏弱/危机，联动仓位系数，危机禁开仓（只拦买入） |
| 实例风控 | 持仓上限、止损、单日最大亏损停牌 |
| 真实账户只读 | Binance Key 只读接入（余额/对账），Secret 0600 文件保存，永不回显 |

## 架构

```
frontend/   Vue 3 + TypeScript + Vite
backend/    FastAPI（API 组合层、任务控制面）
src/        领域模型 / 用例 / 端口 / 交易所适配器
contracts/  版本化数据与能力契约
```

- PostgreSQL 存业务数据，Redis 做任务队列与运行时状态
- `docker compose up` 一键起全部服务（postgres / redis / backend / frontend）

## 开发

```bash
# 后端测试（Python 3.12）
python -m pytest -q

# 前端构建（Node.js 22）
cd frontend && npm ci && npm run build
```

分支规范：一个功能一个分支（`codex/<task>`），验证后合并到 `main` 即删，`main` 永远可运行。

## 文档

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md)：使用手册（安装、各模块说明、典型工作流、FAQ）
- [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md)：项目分析与 AI 接手指南
- [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md)：完整建设计划书
- [docs/AUTO_TRADING_POLICY.md](docs/AUTO_TRADING_POLICY.md)：自动交易安全政策

## 许可证

[MIT](LICENSE)
