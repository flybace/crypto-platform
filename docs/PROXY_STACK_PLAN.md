# Proxy Stack专项计划

## 目标

在局域网 Linux 主机 `192.168.68.99` 上建立一个独立的 Docker 代理栈，服务于该主机和后续受控局域网客户端。Windows 本机只负责开发和局域网访问。节点订阅只作为 Mihomo `proxy-providers` 节点源；规则、代理组、服务域名和智能发现结果由本项目独立持有。

## 边界

- 代理栈目录：远端 `/home/flybace/proxy-stack`（源码位于本地 `proxy-stack/`），不加入 `crypto-platform/compose.yaml` 的服务生命周期。
- 运行主机：`192.168.68.99` Ubuntu；Windows 开发机不是代理栈运行节点。当前四个端口均绑定该地址，局域网可直接访问。
- 数据面：官方 `ghcr.io/metacubex/metacubexd-server`，提供 Mihomo、Clash API、Web 控制台和持久化数据目录。
- 策略面：`proxy-route-manager`，提供观察模式、候选域名确认、服务策略和配置编译。
- 订阅：只写入 provider 节点缓存；不导入订阅方的 `rules`、`proxy-groups`、`dns` 或 `tun`。
- 默认规则：使用已核验可访问的 `Loyalsoldier/clash-rules` 和 `blackmatrix7/ios_rule_script` 远程 rule-providers；本地服务规则排在默认规则之前。
- TUN/透明网关：首版关闭。当前提供显式 HTTP/SOCKS 代理；Linux 主机的透明网关另行评估。

## 用户流程

1. 在管理页添加订阅，模式固定为“仅节点”。
2. 选择一个 Provider 组或运行中的 Mihomo 出口。
3. 在“添加网址”输入网址，选择自动出口，或直接指定节点/策略组；提交后立即创建该网址的服务记录。
4. 自动模式先测试当前出口和少量候选节点，最多扩展到有限上限；达到可用成功数后提前停止，不默认测试完整订阅。手动模式只验证指定出口。
5. 出口选定后，管理器在后台受控切换，通过该出口抓取入口、重定向、静态脚本/CSS、WebSocket 引用、Mihomo 连接和 DNS 结果，然后恢复原策略选择。
6. 主页面只显示网址、当前策略和后台状态；用户点击“详情”后才查看节点测试、延迟和依赖候选。所有域名和 IP 进入带来源证据的待确认候选，确认后加入服务规则。
7. 管理器生成配置草稿并执行本地结构校验；controller 模式在出口选定后自动应用，dry-run 模式保留草稿。
8. 配置应用失败保持旧配置，并在网址详情显示错误状态。

## 数据所有权

| 数据 | 所有者 | 更新方式 |
| --- | --- | --- |
| Provider URL 和名称 | route-manager | 管理页保存 |
| Provider 节点缓存 | Mihomo | 订阅刷新 |
| 服务与域名策略 | route-manager | 用户确认/后续 AI 建议 |
| 默认 rule-providers | route-manager 模板 | 远程版本按日刷新 |
| 运行中的配置 | Mihomo | 校验通过后应用 |
| 观察到的域名 | route-manager | Mihomo 连接 API/URL 发现 |

## 默认规则

截至 2026-09-18 已检查以下 raw 地址返回 HTTP 200。Loyalsoldier 的四个源是 `payload` YAML；BlackMatrix7 Global 是 Clash classical 文本规则，文件头标注约 35,048 条并带有当天更新时间。运行时继续跟随这些仓库的发布地址，管理页可再次执行健康检查并记录响应 SHA-256。

- `https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/reject.txt`
- `https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/direct.txt`
- `https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/proxy.txt`
- `https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/applications.txt`
- `https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/Global/Global.list`

本次实际接口检查记录：`reject.txt` 5,439,797 bytes，SHA-256 `2d5928a8656653917dc890cdfe1c680a66a472daba2531bdd3f8414f123e9842`；`direct.txt` 2,299,594 bytes，SHA-256 `206654921e5364e33eacd864aa3f2de9e6a66e2d99e2b29026d7919856a27fa7`；`proxy.txt` 617,718 bytes，SHA-256 `7010f7be744dfac04bbdc292d4a6d7d1050d4ca79e6bad144b46966cf8b45b40`；`applications.txt` 2,821 bytes，SHA-256 `33bc8f07bacf74082fcb5f361eded1f6f9d3abcedcbe37ada2eb2ab4ae031732`。

默认顺序为：本地私网直连、用户确认的服务规则、reject、direct、global、proxy、applications、最后 `DIRECT`。默认不会把所有未知网站送入代理。

订阅隔离是不变量：订阅 URL 只出现在 `proxy-providers`，而且每个 Provider 只声明 `type/url/path/interval/health-check` 等节点源字段；订阅自身可能返回的 `rules`、`proxy-groups`、`dns` 和 `tun` 不会复制进主配置。订阅刷新可以改变节点缓存，但不会改变 `route-manager` 持有的服务规则和默认规则源。

## 策略组展示

这里采用 Mihomo 的“策略组”（`proxy-groups`）语义：每个已确认的服务会生成一个以服务名称命名的 `select` 策略组，域名规则指向这个组，而不是直接指向通用 `PROXY`。策略组默认使用服务记录中的 `target`，同时展示 `PROXY`、`DIRECT`、`REJECT`、订阅组和订阅节点，用户可以在 MetaCubeXD 的“代理”页按行切换出口。`global` 是底层规则提供者，不是这个页面里的策略组。

## 观察模式限制

- HTTP 可看到完整请求 URL，但不保存 query、Cookie、Authorization 或请求体。
- HTTPS/WebSocket 通常只观察 hostname、端口和连接元数据，不能从 Mihomo 直接看到加密路径。
- URL 抓取只产生候选，不自动把第三方 CDN、统计和广告域名加入服务。
- 节点消失时不自动静默切换到另一节点；策略应进入待处理状态。

## 添加网址与后台分析

这是网址个性化配置的首个智能化实现阶段，目标是解决“目标网址本来就无法直连，不能先靠直连发现依赖”的问题。

- `POST /api/analysis/start` 接受 `selection_mode=auto|manual`。自动模式读取 Mihomo 当前叶子节点，先测有限候选，逐个调用 `/proxies/{node}/delay` 测试目标网址；策略组本身不会被当作叶子节点重复测试。手动模式只验证 `manual_target`。
- 节点结果保留目标测试、健康探针、延迟、评分和错误原因。目标返回预期状态才成为成功推荐；目标失败但健康探针成功的节点会显示为“节点可达、目标失败”，不会误推荐。
- 自动或手动出口确定后，管理器自动选择包含目标出口的 `PROXY`/兼容策略组，记录原选择，临时切换、通过容器内混合代理抓取，再恢复原选择。恢复失败会进入 `restore-failed`，不会伪称流程完成。
- 内容探测只保存 hostname、IP、来源类别和少量统计，不保存 query、Cookie、Authorization、请求体或页面原文。静态资源最多抓取 24 个，每个最多 700 KB；入口响应最多 2 MB。
- IP 以 `IP-CIDR`/`IP-CIDR6` 草稿呈现，默认不勾选；CDN 地址带 DNS/连接来源，不能因为一次探测自动并入主域名后缀。
- 节点测试、内容探测和候选结果持久化在 route-manager 状态中。输入的网址主域名会立即进入自有服务规则；依赖域名/IP 保持待确认，只有详情中确认后才加入该服务。
- AI API 不是该链路的运行时依赖。未来可把脱敏候选和证据摘要交给 AI 做命名、去重和匹配建议，但 AI 不能直接切换节点、写 YAML 或覆盖活动配置。

## 阶段计划

### P0：独立栈和只读观察

- Compose、持久化目录、默认 rule-providers。
- route-manager 健康检查、Provider/服务/观察数据模型。
- 从 Mihomo `/connections` 读取 hostname。
- 从 HTTP/HTTPS 页面抓取静态 URL hostname。

### P1：可视化策略应用

- 服务与域名确认。
- Provider 仅节点模式。
- 预览、版本、草稿和应用接口。
- 通过 Clash API 应用配置，失败保持旧状态。

### P1.5：添加网址与后台智能分析（本次实施）

- 添加网址服务、自动/手动出口选择、有限候选测试、健康探针、延迟评分和推荐节点。
- 出口选定后自动执行受控临时探测，后台抓取入口和静态依赖。
- 合并重定向、WebSocket、Mihomo 连接、DNS A/AAAA/CNAME 的候选域名/IP。
- 主页面仅展示网址、当前策略和任务状态；详情区域展示节点延迟、来源证据和候选规则。
- 保留 `/api/analysis/start`、`/api/analysis/{id}`、`/probe`、`/draft` 兼容接口，并支持 `selection_mode`、`manual_target`、`service_name`。

### P2：语义增强

- GitHub 可信源检索和 commit 留证。
- AI API 只接收去敏域名和来源摘要，输出结构化候选。
- AI 不直接写 YAML、不直接覆盖活动配置。

### P3：局域网和生产边界

- 将混合端口限制到指定 LAN 网段，并为控制面和 Clash API 配置防火墙隔离。
- 为混合代理配置认证或其他访问控制，避免在不可信网络形成开放代理。
- 镜像固定 digest，订阅 URL 和 API 密钥不进入 Git。
- Linux 主机再评估 TProxy/TUN。

## 验收门槛

- 订阅更新前后自有服务规则版本和内容不变。
- Provider 节点列表可以变化，主策略不被替换。
- 带有自带规则的测试订阅刷新后，活动配置仍没有订阅方 `rules`、`proxy-groups`、`dns` 和 `tun`。
- 观察到的 hostname 可以进入待确认列表。
- 对一个直连不可达但某订阅节点可达的网址，系统能逐节点测试并给出延迟最低的成功节点推荐。
- 推荐节点内容探测会产生入口、静态资源、WebSocket、连接和 DNS 候选，且探测后恢复原策略选择。
- 输入网址的主域名立即出现在自有服务规则中；后台发现的依赖候选在详情确认前不进入服务规则。
- 详情确认后生成 `DOMAIN`、`DOMAIN-SUFFIX` 或 `IP-CIDR` 自有规则。
- 配置校验失败不会替换当前活动配置。
- 默认规则源无法访问时有明确状态，不伪造成功。
- route-manager 离线不影响已激活的 Mihomo 配置。
- 未配置 Docker 时，静态校验和本地 route-manager 测试仍可完成，但不能宣称容器运行验收通过。
