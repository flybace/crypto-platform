# Crypto Proxy Stack

独立的 Mihomo 代理栈和智能路由管理器，部署在局域网 Linux 主机 `192.168.68.99`。它与 `crypto-platform` 的 Compose 生命周期隔离；Windows 本机可直接通过局域网地址访问管理页。

## 组成

- `metacubexd-server`：Mihomo 内核、节点 Provider、Clash API 和低级运行面板。
- `route-manager`：观察域名、管理服务规则、编译配置和应用草稿。

`metacubexd-server` 是 MetaCubeX 官方的一体镜像，内部包含 Dashboard、控制代理和 Mihomo 内核；`route-manager` 只负责本项目的策略控制面。

## 端口

当前远端配置全部绑定在 `192.168.68.99`：

- `17880`：Mihomo Dashboard
- `17881`：智能路由管理器
- `17890`：HTTP/SOCKS 混合代理，可供该主机及局域网客户端使用
- `17990`：Mihomo Clash API

直接访问 `http://192.168.68.99:17881` 管理页和 `http://192.168.68.99:17880` Dashboard。控制面和代理端口已进入局域网可达范围，应在可信网络或防火墙规则下使用；尤其 `17890` 尚未配置代理层用户名密码。

## 启动前

运行主机为 Ubuntu `192.168.68.99`，远端目录为 `/home/flybace/proxy-stack`。Windows 本机不运行这套 Compose。

在运行主机上：

1. 复制 `.env.example` 为 `.env`。
2. 设置随机的 `CLASH_SECRET` 和 `CONTROL_TOKEN`。
3. 先保持 `ROUTE_MANAGER_APPLY_MODE=dry-run`。
4. Docker 和 Compose 可用后执行：

```bash
cd /home/flybace/proxy-stack
docker compose config
docker compose up -d
```

首次只在 Dashboard 中确认内核健康，再把 route-manager 的应用模式改为 `controller`。

## 订阅原则

管理页只把订阅保存为 Provider。不要把订阅 URL 作为完整 Profile 导入。Provider 刷新只更新节点缓存，自有服务规则由 route-manager 生成。

这里使用 Mihomo 的 `proxy-providers` 语义：订阅只作为代理节点集合被引用，订阅返回的顶层 `rules`、`proxy-groups`、`dns`、`tun` 等 Profile 字段不会被导入活动配置。活动配置中的规则唯一来自 route-manager 生成的 `rules` 和本项目声明的 `rule-providers`。这条边界由单元测试锁定；真正启用容器后还要用一份带自带规则的测试订阅做运行态验收。

默认规则源包括 Loyalsoldier 的 `reject`、`direct`、`proxy`、`applications`，以及 BlackMatrix7 的 `Global.list`。Global 规则按 `classical` 文本格式加载并统一走 `PROXY`，用于覆盖常见海外服务；用户确认的服务规则排在这些默认规则之前，因此 Binance、OKX 等服务仍可在管理页绑定到指定节点或代理组。默认源可在管理页执行检查，后续自定义规则继续由 route-manager 持有。

MetaCubeXD “代理”页里显示的是策略组（Mihomo `proxy-groups`），不是规则提供者。确认一个服务后，route-manager 会生成一个同名可选策略组；该服务的域名规则指向这个组，组内可切换 `PROXY`、`DIRECT`、`REJECT`、订阅组和订阅节点。`global` 只在“规则 / 规则提供者”页显示为规则源及其 `RuleSet`，不会单独成为代理页的一行。

## 当前原型

原型支持：

- Provider 记录和掩码展示；
- 服务和域名记录；
- 从 URL HTML 静态引用发现 hostname；
- 从 Mihomo connections 发现 hostname；
- 观察候选加入服务；
- 添加网址工作流：支持自动选择出口，也支持手动指定节点或策略组；
- 自动选择只测试有限候选节点，达到可用结果后提前停止，不默认跑完整订阅；
- 通过推荐节点临时探测入口、重定向、静态脚本/CSS、WebSocket、Mihomo 连接和 DNS，并恢复原代理组选择；
- 对发现的域名和 IP 进行带证据的候选确认，可自动创建或选择服务策略草稿；
- 默认规则源健康检查；
- 配置预览和本地草稿输出。

使用路径：打开管理页的“添加网址”，输入网址并选择“自动选择”或“手动选择”，点击“添加网址”即可创建服务。主页面只显示网址、当前策略和后台状态；节点测试、内容探测、DNS 和候选依赖在后台执行。点击服务右侧“详情”后，才查看节点延迟、候选域名/IP并确认要加入的规则。controller 模式会在出口选定后自动应用自有配置，dry-run 模式仍只保存草稿。

AI/GitHub 语义发现保留为后续适配器，不会成为代理运行时硬依赖。
