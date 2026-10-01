const state = {
  data: null,
  proxies: [],
  catalog: [],
  analysis: null,
  analysisId: null,
  analysisSelections: {},
};

let analysisPollTimer = null;

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

function showToast(message, tone = "success") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3600);
}

function setMessage(message, tone = "") {
  const node = $("#discover-message");
  if (!node) return;
  node.textContent = message;
  node.className = `inline-message ${tone}`.trim();
}

function setRuleMessage(message, tone = "") {
  const node = $("#rule-check-message");
  if (!node) return;
  node.textContent = message;
  node.className = `inline-message ${tone}`.trim();
}

function discoveryErrorMessage(error) {
  const message = String(error?.message || error || "未知错误");
  if (/没有节点|推荐节点|测试尚未完成|分析任务仍在运行/.test(message)) return message;
  if (/\b400\b/.test(message)) {
    return "目标网站拒绝了静态抓取（HTTP 400），这不是代理服务故障。可改用“同步当前连接”，或手动添加服务域名。";
  }
  if (/\b(401|403)\b/.test(message)) {
    return "目标网站拒绝了抓取请求。可改用“同步当前连接”，或手动添加服务域名。";
  }
  if (/timeout|timed out|failed to fetch/i.test(message)) {
    return "目标网站暂时无法抓取。可改用“同步当前连接”，或手动添加服务域名。";
  }
  return message;
}

const ROUTES = {
  overview: {
    kicker: "00 / OVERVIEW",
    title: "代理入口",
    description: "查看局域网代理地址，以及已经确认会走指定策略的服务网址。",
  },
  subscriptions: {
    kicker: "01 / SUBSCRIPTIONS",
    title: "订阅与规则",
    description: "节点订阅、策略组、默认规则和配置应用都在这里管理。",
  },
  discovery: {
    kicker: "02 / ADD URL",
    title: "添加网址",
    description: "输入网址后自动选择出口；后台分析结果可在详情中查看。",
  },
};

function renderRoute() {
  const requested = window.location.hash.slice(1).toLowerCase();
  const route = ROUTES[requested] ? requested : "overview";
  if (requested !== route) history.replaceState(null, "", `#${route}`);
  document.querySelectorAll(".route-view").forEach((view) => {
    view.hidden = view.dataset.view !== route;
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    const active = link.dataset.route === route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const metadata = ROUTES[route];
  const kicker = $("#page-kicker");
  const title = $("#page-title");
  const description = $("#page-description");
  if (kicker) kicker.textContent = metadata.kicker;
  if (title) title.textContent = metadata.title;
  if (description) description.textContent = metadata.description;
  document.title = `${metadata.title} · Proxy Route Manager`;
}

function configureDashboardLink() {
  const link = $("#metacubexd-link");
  if (!link) return;
  const host = window.location.hostname || "192.168.68.99";
  link.href = `${window.location.protocol}//${host}:17880`;
  link.title = `打开 MetaCubeXD：${link.href}`;
}

function renderCore(core) {
  const node = $("#core-status");
  if (core.status === "connected") {
    node.textContent = `核心 ${core.version?.version || "已连接"}`;
    node.className = "status-chip status-connected";
  } else {
    node.textContent = "核心未连接";
    node.className = "status-chip status-error";
  }
}

function renderAccess(access) {
  const urls = access?.urls || {};
  const fields = {
    "#access-http": urls.http_proxy,
    "#access-socks5": urls.socks5_proxy,
    "#access-manager": urls.route_manager,
    "#access-dashboard": urls.metacubexd,
    "#access-api": urls.clash_api,
  };
  Object.entries(fields).forEach(([selector, value]) => {
    const node = $(selector);
    if (node) node.textContent = value || "不可用";
  });
  const managerOpen = $("#access-manager-open");
  const dashboardOpen = $("#access-dashboard-open");
  if (managerOpen) managerOpen.href = urls.route_manager || "#";
  if (dashboardOpen) dashboardOpen.href = urls.metacubexd || "#";
  const dashboardLink = $("#metacubexd-link");
  if (dashboardLink && urls.metacubexd) {
    dashboardLink.href = urls.metacubexd;
    dashboardLink.title = `打开 MetaCubeXD：${urls.metacubexd}`;
  }

  const status = $("#access-status");
  const meta = $("#access-meta");
  if (!status || !meta) return;
  const runtime = access?.runtime || {};
  const stateLabels = {
    ready: "代理运行中 · 局域网开放",
    degraded: "核心已连接 · 待确认局域网监听",
    offline: "代理核心未连接",
  };
  status.textContent = stateLabels[access?.status] || "检查中";
  status.className = `status-chip ${access?.status === "ready" ? "status-connected" : access?.status === "offline" ? "status-error" : "status-neutral"}`;
  const lan = runtime.allow_lan ? "allow-lan 已开启" : "allow-lan 未开启";
  const bind = runtime.bind_address ? `监听 ${runtime.bind_address}` : "监听地址未知";
  const version = runtime.version ? `Mihomo ${runtime.version}` : "版本未知";
  meta.className = `access-meta status-${access?.status || "offline"}`;
  meta.textContent = `${version} · ${bind} · ${lan} · 模式 ${runtime.mode || "未知"}`;
  if (access?.errors?.length) meta.textContent += ` · ${access.errors.join(" · ")}`;
}

function renderProviders() {
  const providers = state.data.providers || [];
  const count = $("#provider-count");
  const list = $("#provider-list");
  if (count) count.textContent = `${providers.length} 个节点源`;
  if (!list) return;
  list.innerHTML = providers.length ? providers.map((provider) => `
    <div class="entity-row">
      <div class="entity-main"><strong>${escapeHtml(provider.name)}</strong><span>${escapeHtml(provider.group || `PROVIDER_${provider.id.toUpperCase()}`)}</span></div>
      <div class="entity-sub">${escapeHtml(provider.url)} · 每 ${provider.interval}s 更新 · 仅节点</div>
    </div>`).join("") : `<div class="empty-row">尚未添加节点订阅。</div>`;
}

function renderImportedPolicy() {
  const policy = state.data.imported_policy;
  const node = $("#policy-import-status");
  if (!node) return;
  node.textContent = policy
    ? `${policy.source_name} · ${policy.proxy_group_count} 个策略组 · ${policy.rule_source_count} 个规则源 · ${policy.rule_count} 条规则`
    : "当前使用默认规则";
}

function serviceOptions() {
  return (state.data.services || []).map((service) => `<option value="${escapeHtml(service.id)}">${escapeHtml(service.name)}</option>`).join("");
}

function analysisServiceOptions() {
  const host = state.analysis?.target_host || "目标网址";
  const services = state.data.services || [];
  const selected = state.analysis?.service_id;
  return `${services.map((service) => `<option value="${escapeHtml(service.id)}" ${service.id === selected ? "selected" : ""}>${escapeHtml(service.name)}</option>`).join("") || `<option value="">自动创建：${escapeHtml(host)} 服务</option>`}`;
}

function renderObservations() {
  const observations = (state.data.observations || []).filter((item) => item.status === "pending");
  const count = $("#observation-count");
  const table = $("#observation-table");
  if (count) count.textContent = observations.length;
  if (!table) return;
  const rows = observations.slice(0, 80).map((item) => `
    <tr>
      <td><span class="observation-host">${escapeHtml(item.host)}</span></td>
      <td><div class="source-list">${(item.sources || []).map((source) => `<span class="source-tag">${escapeHtml(source)}</span>`).join(" · ")}</div></td>
      <td>${escapeHtml(item.hits)}</td>
      <td>
        <div class="row-actions">
          <select class="table-select observation-service" data-host="${escapeHtml(item.host)}" aria-label="选择服务">
            <option value="">选择服务</option>${serviceOptions()}
          </select>
          <select class="table-select observation-match" data-host="${escapeHtml(item.host)}" aria-label="选择匹配方式">
            <option value="domain" ${item.match === "domain" ? "selected" : ""}>精确域名</option>
            <option value="suffix" ${item.match === "suffix" ? "selected" : ""}>域名后缀</option>
            <option value="ip" ${item.match === "ip" ? "selected" : ""}>IP /32</option>
            <option value="ip-cidr" ${item.match === "ip-cidr" ? "selected" : ""}>IP 网段</option>
          </select>
          <button class="button button-quiet assign-observation" data-host="${escapeHtml(item.host)}" type="button">加入</button>
          <button class="button button-quiet ignore-observation" data-host="${escapeHtml(item.host)}" type="button">忽略</button>
        </div>
      </td>
    </tr>`).join("");
  table.innerHTML = rows || `<tr><td class="empty-row" colspan="4">还没有观察到待确认域名。</td></tr>`;
}

function analysisStatusLabel(status) {
  return {
    testing: "后台测试中",
    ready: "出口已选择",
    probing: "后台发现依赖",
    completed: "待确认依赖",
    failed: "没有可用出口",
    "restore-failed": "恢复出口失败",
  }[status] || status || "尚未开始";
}

function analysisNodeStatus(item) {
  return {
    queued: "排队",
    testing: "测试中",
    success: "通过",
    "target-failed-node-reachable": "目标失败 / 节点可达",
    unreachable: "不可达",
    failed: "失败",
  }[item.status] || item.status || "未知";
}

function stopAnalysisPolling() {
  if (analysisPollTimer) {
    window.clearTimeout(analysisPollTimer);
    analysisPollTimer = null;
  }
}

function ensureAnalysisPolling() {
  if (analysisPollTimer) return;
  const tick = async () => {
    try {
      const path = state.analysisId ? `/api/analysis/${encodeURIComponent(state.analysisId)}` : "/api/analysis/latest";
      const payload = await api(path);
      state.analysis = payload.analysis || payload;
      renderAnalysis();
      if (state.analysis && ["testing", "probing"].includes(state.analysis.status)) {
        analysisPollTimer = window.setTimeout(tick, 1200);
      } else {
        analysisPollTimer = null;
        await refresh();
      }
    } catch (_) {
      analysisPollTimer = window.setTimeout(tick, 2000);
    }
  };
  tick();
}

function renderAnalysis() {
  const analysis = state.analysis;
  const status = $("#analysis-status");
  const detailState = $("#analysis-detail-state");
  const detailPanel = $("#analysis-detail-panel");
  const message = $("#analysis-message");
  const progress = $("#analysis-progress");
  const phase = $("#analysis-phase");
  const progressCount = $("#analysis-progress-count");
  const progressBar = $("#analysis-progress-bar");
  const nodeTable = $("#analysis-node-table");
  const recommendation = $("#analysis-recommendation");
  const probeActions = $("#analysis-probe-actions");
  const candidatePanel = $("#analysis-candidate-panel");
  const startButton = $("#start-analysis");
  if (!status || !message || !nodeTable) return;
  if (!analysis) {
    status.textContent = "尚未开始";
    status.className = "status-chip status-neutral";
    message.textContent = "添加后自动处理。";
    nodeTable.innerHTML = `<tr><td class="empty-row" colspan="5">还没有节点测试任务。</td></tr>`;
    if (detailPanel) detailPanel.hidden = true;
    if (detailState) detailState.textContent = "尚未开始";
    if (progress) progress.hidden = true;
    if (recommendation) recommendation.hidden = true;
    if (probeActions) probeActions.hidden = true;
    if (candidatePanel) candidatePanel.hidden = true;
    if (startButton) startButton.disabled = false;
    return;
  }

  const active = ["testing", "probing"].includes(analysis.status);
  const tone = ["failed", "restore-failed"].includes(analysis.status) ? "status-error" : ["ready", "completed"].includes(analysis.status) ? "status-connected" : "status-neutral";
  status.textContent = analysisStatusLabel(analysis.status);
  status.className = `status-chip ${tone}`;
  if (detailPanel) detailPanel.hidden = false;
  if (detailState) {
    detailState.textContent = analysisStatusLabel(analysis.status);
    detailState.className = `status-chip ${tone}`;
  }
  if (startButton) startButton.disabled = active;
  if (message) {
    message.textContent = analysis.status === "failed" ? analysis.error || "没有节点通过测试。"
      : analysis.status === "restore-failed" ? analysis.error || "探测完成，但原出口没有恢复。"
      : analysis.status === "completed" ? `发现 ${analysis.candidate_count || 0} 个依赖，打开详情确认。`
      : analysis.status === "probing" ? "正在后台发现依赖。"
      : analysis.status === "ready" ? "出口已选择，准备发现依赖。"
      : "正在后台选择可用出口。";
    message.className = `inline-message ${["failed", "restore-failed"].includes(analysis.status) ? "error" : ["ready", "completed"].includes(analysis.status) ? "success" : ""}`.trim();
  }
  if (progress) progress.hidden = !active;
  if (active && phase && progressCount && progressBar) {
    phase.textContent = analysis.status === "probing" ? "内容探测" : "节点测试";
    const total = analysis.nodes?.length || 0;
    const current = analysis.status === "probing" ? total : Math.min(analysis.current_index || 0, total);
    progressCount.textContent = `${current} / ${total}`;
    progressBar.style.width = `${total ? Math.round((current / total) * 100) : 0}%`;
  }
  const rows = (analysis.nodes || []).map((item) => `
    <tr>
      <td><span class="analysis-node-name">${escapeHtml(item.name)}</span><span class="analysis-node-type">${escapeHtml(item.type)}</span></td>
      <td><span class="analysis-result-status analysis-${escapeHtml(item.status || "unknown")}">${escapeHtml(analysisNodeStatus(item))}</span></td>
      <td>${item.delay_ms ? `<code>${escapeHtml(item.delay_ms)} ms</code>` : "—"}</td>
      <td>${item.score ? `<code>${escapeHtml(item.score)}</code>` : "—"}</td>
      <td class="analysis-error">${escapeHtml(item.error || item.response_check || "")}</td>
    </tr>`).join("");
  nodeTable.innerHTML = rows || `<tr><td class="empty-row" colspan="5">没有可测试的节点。</td></tr>`;
  const nodeCount = $("#analysis-node-count");
  if (nodeCount) nodeCount.textContent = `${(analysis.nodes || []).length}`;

  if (recommendation) {
    const best = analysis.recommendation;
    recommendation.hidden = !best;
    recommendation.innerHTML = best ? `<div><span class="section-label">当前策略</span><strong>${escapeHtml(best.proxy)}</strong><span class="service-meta">${escapeHtml(best.type || "出口")}${best.delay_ms ? ` · ${escapeHtml(best.delay_ms)} ms` : ""} · ${escapeHtml(best.reason || "")}</span></div>` : "";
  }
  if (probeActions) {
    probeActions.hidden = !["ready"].includes(analysis.status);
    const probeButton = $("#probe-analysis");
    if (probeButton) probeButton.disabled = active;
  }
  if (candidatePanel) {
    candidatePanel.hidden = analysis.status !== "completed";
    if (!candidatePanel.hidden) renderAnalysisCandidates();
  }
}

function renderAnalysisCandidates() {
  const analysis = state.analysis;
  const list = $("#analysis-candidate-list");
  const count = $("#analysis-candidate-count");
  const summary = $("#analysis-candidate-summary");
  const serviceSelect = $("#analysis-service-select");
  if (!analysis || !list) return;
  const candidates = analysis.candidates || [];
  if (count) count.textContent = candidates.length;
  if (summary) summary.textContent = `${analysis.final_url || analysis.url} · ${analysis.resource_count || 0} 个静态资源 · ${analysis.dns_answer_count || 0} 个 DNS 结果`;
  if (serviceSelect) {
    const selectedService = serviceSelect.value;
    serviceSelect.innerHTML = analysisServiceOptions();
    if ([...serviceSelect.options].some((option) => option.value === selectedService)) serviceSelect.value = selectedService;
  }
  list.innerHTML = candidates.length ? candidates.map((item) => {
    const remembered = state.analysisSelections[item.host];
    const defaultChecked = item.status !== "accepted" && item.match !== "ip";
    const checked = remembered ? remembered.checked : defaultChecked;
    const match = remembered?.match || item.match || "domain";
    const matchOptions = item.match === "ip"
      ? `<option value="ip" ${match === "ip" ? "selected" : ""}>IP /32</option><option value="ip-cidr" ${match === "ip-cidr" ? "selected" : ""}>IP 网段</option>`
      : `<option value="domain" ${match === "domain" ? "selected" : ""}>精确域名</option><option value="suffix" ${match === "suffix" ? "selected" : ""}>域名后缀</option>`;
    return `<div class="analysis-candidate-row">
      <label class="candidate-check"><input type="checkbox" data-analysis-candidate="${escapeHtml(item.host)}" ${checked ? "checked" : ""} /><span class="analysis-node-name">${escapeHtml(item.host)}</span></label>
      <span class="candidate-kind">${item.match === "ip" ? "IP" : "域名"}</span>
      <select class="table-select analysis-candidate-match" data-host="${escapeHtml(item.host)}" aria-label="候选匹配方式">${matchOptions}</select>
      <span class="candidate-evidence">${escapeHtml((item.evidence || []).join(" · "))}</span>
    </div>`;
  }).join("") : `<div class="empty-row">没有发现可确认候选。</div>`;
}

function renderServices() {
  const services = state.data.services || [];
  const count = $("#service-count");
  const discoveryCount = $("#discovery-service-count");
  if (count) count.textContent = services.length;
  if (discoveryCount) discoveryCount.textContent = services.length;
  const serviceAnalysis = (service) => (state.data.analyses || []).find((item) => item.id === service.analysis_id);
  const routeStatus = (service) => {
    const analysis = serviceAnalysis(service);
    if (!analysis) return { label: "已配置", tone: "status-connected" };
    if (["testing", "probing"].includes(analysis.status)) return { label: "后台分析中", tone: "status-neutral" };
    if (analysis.status === "completed") return { label: `待确认 ${analysis.candidate_count || 0} 个依赖`, tone: "status-neutral" };
    if (["failed", "restore-failed"].includes(analysis.status)) return { label: "需要处理", tone: "status-error" };
    return { label: "已选出口", tone: "status-connected" };
  };
  const markup = services.length ? services.map((service) => {
    const status = routeStatus(service);
    const analysis = serviceAnalysis(service);
    const hosts = (service.domains || []).map((domain) => domain.host).slice(0, 3);
    const more = Math.max(0, (service.domains || []).length - hosts.length);
    return `
    <article class="service-row">
      <div class="service-main">
        <div class="service-head"><strong>${escapeHtml(service.name)}</strong><span class="status-chip ${status.tone}">${escapeHtml(status.label)}</span></div>
        <div class="service-domains">${hosts.map((host) => `<span class="domain-pill">${escapeHtml(host)}</span>`).join("") || `<span class="service-meta">尚未绑定域名</span>`}${more ? `<span class="service-meta">+${more}</span>` : ""}</div>
      </div>
      <div class="service-route"><span class="section-label">当前策略</span><strong>${escapeHtml(service.target || "PROXY")}</strong></div>
      <button class="button button-quiet service-details" data-analysis-id="${escapeHtml(analysis?.id || "")}" data-service-id="${escapeHtml(service.id)}" type="button">详情</button>
    </article>`;
  }).join("") : `<div class="empty-row">尚未添加网址。</div>`;
  ["#service-list", "#discovery-service-list"].forEach((selector) => {
    const node = $(selector);
    if (node) node.innerHTML = markup;
  });
}

function renderCatalog() {
  const node = $("#catalog-list");
  if (node) node.innerHTML = state.catalog.map((item) => `<button class="catalog-button" data-catalog="${escapeHtml(item.id)}" type="button">${escapeHtml(item.name)}</button>`).join("");
}

function renderTargets() {
  const providerTargets = (state.data.providers || []).map((provider) => provider.group || `PROVIDER_${provider.id.toUpperCase()}`);
  const groupTypes = new Set(["COMPATIBLE", "FALLBACK", "LOADBALANCE", "RELAY", "SELECTOR", "URLTEST"]);
  const runtimeTargets = state.proxies.map((item) => item.name);
  const targets = [...new Set(["PROXY", "DIRECT", ...providerTargets, ...runtimeTargets])].sort();
  const node = $("#proxy-targets");
  if (node) node.innerHTML = targets.map((target) => `<option value="${escapeHtml(target)}"></option>`).join("");
  const select = $("#analysis-manual-target");
  if (!select) return;
  const groups = state.proxies.filter((item) => groupTypes.has(String(item.type || "").toUpperCase()));
  const nodes = state.proxies.filter((item) => !groupTypes.has(String(item.type || "").toUpperCase()) && !["DIRECT", "REJECT", "PASS"].includes(String(item.name).toUpperCase()));
  const option = (item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}${item.now ? ` · 当前 ${escapeHtml(item.now)}` : ""}</option>`;
  select.innerHTML = `<option value="PROXY">PROXY · 默认策略组</option>${groups.filter((item) => item.name !== "PROXY").map(option).join("")}${nodes.map(option).join("")}`;
}

function renderRuleChecks() {
  const checks = state.data.rule_checks || {};
  const failed = Object.values(checks).filter((item) => item.status === "error").length;
  if (Object.keys(checks).length) setRuleMessage(failed ? `${failed} 个规则源检查失败。` : "规则源检查通过。", failed ? "error" : "success");
  else setRuleMessage(`${(state.data.rule_sources || []).length} 个规则源已配置，尚未执行连通性检查。`);
}

function renderRuleSources() {
  const node = $("#rule-source-list");
  if (!node) return;
  const checks = state.data.rule_checks || {};
  const sources = state.data.rule_sources || [];
  node.innerHTML = sources.length ? sources.map((source) => {
    const check = checks[source.id];
    const status = !check ? "未检查" : check.status === "error" ? "检查失败" : `HTTP ${check.status}`;
    const tone = !check ? "" : check.status === "error" ? "error" : "ok";
    const behavior = source.behavior ? ` · ${source.behavior}` : "";
    return `<div class="rule-source-row">
      <div class="rule-source-main"><strong>${escapeHtml(source.name || source.id)}</strong><span>${escapeHtml(source.url || "本地规则")}${escapeHtml(behavior)}</span></div>
      <span class="rule-source-status ${tone}">${escapeHtml(status)}</span>
    </div>`;
  }).join("") : `<div class="empty-row">当前没有规则源。</div>`;
}

async function refresh() {
  const [data, health, core, proxies, catalog, access, latestAnalysis] = await Promise.all([
    api("/api/state"), api("/api/health"), api("/api/core"), api("/api/proxies"), api("/api/catalog"), api("/api/access"), api("/api/analysis/latest"),
  ]);
  state.data = data;
  state.proxies = proxies.proxies || [];
  state.catalog = catalog;
  const selectedAnalysis = state.analysisId && (data.analyses || []).find((item) => item.id === state.analysisId);
  state.analysis = state.analysisId ? (selectedAnalysis || latestAnalysis.analysis) : null;
  renderCore(core);
  renderAccess(access);
  const captureToggle = $("#capture-toggle");
  if (captureToggle) captureToggle.checked = Boolean(data.capture_enabled);
  const applyMode = String(health.apply_mode || "dry-run").toLowerCase();
  const applyModeNode = $("#apply-mode");
  const applyNode = $("#apply-config");
  if (applyModeNode) applyModeNode.textContent = applyMode === "controller"
    ? "CONTROLLER"
    : String(data.last_apply?.status || "DRY RUN").toUpperCase();
  if (applyNode) applyNode.textContent = applyMode === "controller" ? "应用到 Mihomo" : "保存草稿";
  renderProviders();
  renderImportedPolicy();
  renderObservations();
  renderServices();
  renderCatalog();
  renderTargets();
  renderRuleChecks();
  renderRuleSources();
  renderAnalysis();
  if (state.analysis && ["testing", "probing"].includes(state.analysis.status)) ensureAnalysisPolling();
}

async function createCatalogService(item) {
  await api("/api/services", { method: "POST", body: JSON.stringify({ id: item.id, name: item.name, scope: item.scope, target: "PROXY" }) });
  for (const host of item.domains) {
    await api(`/api/services/${encodeURIComponent(item.id)}/domains`, { method: "POST", body: JSON.stringify({ host, match: "domain", source: item.sources.join(", ") }) });
  }
  await refresh();
  showToast(`${item.name} 已加入服务策略`);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".service-details");
  if (!button) return;
  const analysisId = button.dataset.analysisId;
  if (!analysisId) {
    window.location.hash = "#discovery";
    showToast("这个服务没有后台分析记录", "error");
    return;
  }
  try {
    const payload = await api(`/api/analysis/${encodeURIComponent(analysisId)}`);
    state.analysisId = analysisId;
    state.analysis = payload;
    window.location.hash = "#discovery";
    renderAnalysis();
    const detail = $("#analysis-detail-panel");
    if (detail) {
      detail.hidden = false;
      detail.open = true;
      detail.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (["testing", "probing"].includes(payload.status)) ensureAnalysisPolling();
  } catch (error) { showToast(discoveryErrorMessage(error), "error"); }
});

const closeAnalysisDetail = $("#close-analysis-detail");
if (closeAnalysisDetail) closeAnalysisDetail.addEventListener("click", () => {
  const detail = $("#analysis-detail-panel");
  if (detail) detail.open = false;
});

document.querySelectorAll("input[name=selection_mode]").forEach((input) => input.addEventListener("change", () => {
  const manual = document.querySelector("input[name=selection_mode]:checked")?.value === "manual";
  const wrap = $("#manual-target-wrap");
  if (wrap) wrap.hidden = !manual;
}));

$("#analysis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("#analysis-url").value.trim();
  const expectedStatus = Number($("#analysis-status-code").value || 200);
  const selectionMode = document.querySelector("input[name=selection_mode]:checked")?.value || "auto";
  const manualTarget = selectionMode === "manual" ? $("#analysis-manual-target").value : null;
  const serviceName = $("#analysis-service-name")?.value.trim() || null;
  stopAnalysisPolling();
  try {
    const result = await api("/api/analysis/start", {
      method: "POST",
      body: JSON.stringify({ url, expected_status: expectedStatus, selection_mode: selectionMode, manual_target: manualTarget, service_name: serviceName }),
    });
    state.analysisId = result.id;
    state.analysis = result;
    state.analysisSelections = {};
    await refresh();
    ensureAnalysisPolling();
  } catch (error) {
    showToast(discoveryErrorMessage(error), "error");
  }
});

$("#probe-analysis").addEventListener("click", async () => {
  const analysis = state.analysis;
  if (!analysis?.id) return;
  try {
    const result = await api(`/api/analysis/${encodeURIComponent(analysis.id)}/probe`, {
      method: "POST",
      body: JSON.stringify({ proxy: analysis.recommendation?.proxy }),
    });
    state.analysis = result;
    renderAnalysis();
    ensureAnalysisPolling();
  } catch (error) { showToast(discoveryErrorMessage(error), "error"); }
});

$("#analysis-candidate-list").addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-analysis-candidate]");
  const match = event.target.closest(".analysis-candidate-match");
  const host = checkbox?.dataset.analysisCandidate || match?.dataset.host;
  if (!host) return;
  const existing = state.analysisSelections[host] || { checked: true, match: "domain" };
  if (checkbox) existing.checked = checkbox.checked;
  if (match) existing.match = match.value;
  state.analysisSelections[host] = existing;
});

$("#draft-analysis").addEventListener("click", async () => {
  const analysis = state.analysis;
  if (!analysis?.id) return;
  const candidates = [...document.querySelectorAll("[data-analysis-candidate]")]
    .filter((checkbox) => checkbox.checked)
    .map((checkbox) => {
      const host = checkbox.dataset.analysisCandidate;
      const match = document.querySelector(`.analysis-candidate-match[data-host="${CSS.escape(host)}"]`)?.value || "domain";
      return { host, match };
    });
  if (!candidates.length) {
    showToast("至少选择一个候选", "error");
    return;
  }
  try {
    const serviceId = $("#analysis-service-select").value || null;
    await api(`/api/analysis/${encodeURIComponent(analysis.id)}/draft`, {
      method: "POST",
      body: JSON.stringify({ service_id: serviceId, candidates }),
    });
    await refresh();
    showToast("候选已加入服务规则草稿，请到订阅与规则生成预览");
  } catch (error) { showToast(error.message, "error"); }
});

$("#discover-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("#discover-url").value.trim();
  setMessage("正在抓取静态引用…");
  try {
    const result = await api("/api/discover/url", { method: "POST", body: JSON.stringify({ url }) });
    setMessage(`发现 ${result.hosts.length} 个候选域名，等待确认。`, "success");
    await refresh();
  } catch (error) { setMessage(discoveryErrorMessage(error), "error"); }
});

$("#sync-connections").addEventListener("click", async () => {
  setMessage("正在同步 Mihomo 连接…");
  try {
    const result = await api("/api/capture/sync", { method: "POST" });
    setMessage(`同步 ${result.connections} 条连接，得到 ${result.hosts} 个域名。`, "success");
    await refresh();
  } catch (error) { setMessage(discoveryErrorMessage(error), "error"); }
});

$("#capture-toggle").addEventListener("change", async (event) => {
  try { await api("/api/capture", { method: "POST", body: JSON.stringify({ enabled: event.target.checked }) }); await refresh(); }
  catch (error) { event.target.checked = !event.target.checked; showToast(error.message, "error"); }
});

$("#provider-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    id: $("#provider-id").value.trim(), name: $("#provider-name").value.trim(), url: $("#provider-url").value.trim(),
    group: $("#provider-group").value.trim() || null, interval: Number($("#provider-interval").value),
  };
  try { await api("/api/providers", { method: "POST", body: JSON.stringify(body) }); event.target.reset(); $("#provider-interval").value = 86400; await refresh(); showToast("节点源已保存，规则未被导入"); }
  catch (error) { showToast(error.message, "error"); }
});

$("#policy-import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("#policy-import-file").files?.[0];
  if (!file) return;
  try {
    const yamlText = await file.text();
    const result = await api("/api/import/policy", {
      method: "POST",
      body: JSON.stringify({ name: file.name, yaml_text: yamlText }),
    });
    await refresh();
    showToast(`已导入 ${result.proxy_group_count} 个策略组和 ${result.rule_count} 条规则，请生成预览后应用`);
  } catch (error) { showToast(error.message, "error"); }
});

$("#service-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = { id: $("#service-id").value.trim(), name: $("#service-name").value.trim(), scope: $("#service-scope").value.trim(), target: $("#service-target").value.trim() };
  try { await api("/api/services", { method: "POST", body: JSON.stringify(body) }); event.target.reset(); $("#service-scope").value = "public-market"; $("#service-target").value = "PROXY"; await refresh(); showToast("服务策略已保存"); }
  catch (error) { showToast(error.message, "error"); }
});

$("#catalog-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-catalog]");
  if (!button) return;
  const item = state.catalog.find((candidate) => candidate.id === button.dataset.catalog);
  if (!item) return;
  try { await createCatalogService(item); } catch (error) { showToast(error.message, "error"); }
});

$("#observation-table").addEventListener("click", async (event) => {
  const assign = event.target.closest(".assign-observation");
  const ignore = event.target.closest(".ignore-observation");
  const host = (assign || ignore)?.dataset.host;
  if (!host) return;
  try {
    if (ignore) await api(`/api/observations/${encodeURIComponent(host)}/ignore`, { method: "POST" });
    else {
      const service = document.querySelector(`.observation-service[data-host="${CSS.escape(host)}"]`).value;
      const match = document.querySelector(`.observation-match[data-host="${CSS.escape(host)}"]`).value;
      if (!service) throw new Error("先选择服务");
      await api(`/api/observations/${encodeURIComponent(host)}/assign`, { method: "POST", body: JSON.stringify({ service_id: service, match }) });
    }
    await refresh();
    showToast(ignore ? "候选已忽略" : "域名已加入服务策略");
  } catch (error) { showToast(error.message, "error"); }
});

$("#check-rules").addEventListener("click", async () => {
  setRuleMessage("正在检查规则源…");
  try { await api("/api/rule-sources/check", { method: "POST" }); await refresh(); }
  catch (error) { setRuleMessage(error.message, "error"); }
});

async function preview() {
  const result = await api("/api/preview");
  $("#preview-digest").textContent = result.sha256;
  $("#config-preview").textContent = result.yaml;
  $("#preview-warnings").innerHTML = (result.warnings || []).map((warning) => `<div class="warning">${escapeHtml(warning)}</div>`).join("");
  return result;
}

$("#preview-config").addEventListener("click", async () => {
  try { await preview(); showToast("配置预览已更新"); } catch (error) { showToast(error.message, "error"); }
});

$("#apply-config").addEventListener("click", async () => {
  try { const result = await api("/api/apply", { method: "POST" }); await preview(); showToast(result.status === "applied" ? "配置已应用" : "草稿已保存"); await refresh(); }
  catch (error) { showToast(error.message, "error"); }
});

configureDashboardLink();
renderRoute();
window.addEventListener("hashchange", renderRoute);
refresh().catch((error) => showToast(error.message, "error"));
window.setInterval(() => refresh().catch(() => {}), 5000);

$("#access-panel").addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-access");
  if (!button) return;
  const target = $("#" + button.dataset.copyTarget);
  const value = target?.textContent?.trim();
  if (!value || value === "不可用" || value === "读取中…") return;
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
    else {
      const helper = document.createElement("textarea");
      helper.value = value;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      if (!document.execCommand("copy")) throw new Error("浏览器拒绝复制");
      helper.remove();
    }
    button.textContent = "已复制";
    showToast("地址已复制");
    window.setTimeout(() => { button.textContent = "复制"; }, 1600);
  } catch (error) { showToast(`复制失败：${error.message}`, "error"); }
});
