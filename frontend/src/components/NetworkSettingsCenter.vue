<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Check, CircleAlert, Globe2, RefreshCw, Save, ShieldCheck, Trash2, Wifi } from 'lucide-vue-next';
import { api } from '../api';
import type { PublicNetworkProbe, PublicNetworkSettings } from '../types';

type VenueId = 'binance' | 'okx' | 'bybit';
type ProbeChannel = 'HTTP' | 'WebSocket';
type NetworkForm = {
  public_rest_base_url: string;
  public_ws_base_url: string;
  history_base_url: string;
  instruments_path: string;
  tickers_path: string;
  order_book_path: string;
  candles_path: string;
  time_path: string;
  http_proxy: string;
  ws_proxy: string;
  clear_http_proxy: boolean;
  clear_ws_proxy: boolean;
};

const venueIds: VenueId[] = ['binance', 'okx', 'bybit'];
const venueLabels: Record<VenueId, string> = { binance: 'Binance', okx: 'OKX', bybit: 'Bybit' };
const venueNotes: Record<VenueId, string> = {
  binance: 'data-api.binance.vision · stream.binance.com',
  okx: 'www.okx.com · ws.okx.com',
  bybit: 'api.bybit-tr.com · stream.bybit.kz',
};

const settings = ref<PublicNetworkSettings | null>(null);
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const notice = ref('');
const probeBusy = ref<string | null>(null);
const probes = reactive<Partial<Record<VenueId, Partial<Record<ProbeChannel, PublicNetworkProbe>>>>>({});
const forms = reactive<Record<VenueId, NetworkForm>>({
  binance: { public_rest_base_url: '', public_ws_base_url: '', history_base_url: '', instruments_path: '', tickers_path: '', order_book_path: '', candles_path: '', time_path: '', http_proxy: '', ws_proxy: '', clear_http_proxy: false, clear_ws_proxy: false },
  okx: { public_rest_base_url: '', public_ws_base_url: '', history_base_url: '', instruments_path: '', tickers_path: '', order_book_path: '', candles_path: '', time_path: '', http_proxy: '', ws_proxy: '', clear_http_proxy: false, clear_ws_proxy: false },
  bybit: { public_rest_base_url: '', public_ws_base_url: '', history_base_url: '', instruments_path: '', tickers_path: '', order_book_path: '', candles_path: '', time_path: '', http_proxy: '', ws_proxy: '', clear_http_proxy: false, clear_ws_proxy: false },
});

const sourceLabel = computed(() => {
  if (settings.value?.source === 'saved') return '已保存配置';
  if (settings.value?.source === 'invalid_file') return '配置文件异常，当前按直连处理';
  return '环境变量默认值';
});

const venueState = (venue: VenueId) => settings.value?.venues.find((item) => item.venue_id === venue) || null;
const channelState = (venue: VenueId, channel: 'http_proxy' | 'ws_proxy') => venueState(venue)?.[channel];

const formatDate = (value: string | null | undefined) => value
  ? new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false })
  : '尚未保存';

const channelLabel = (configured: boolean) => configured ? '代理' : '直连';
const probeLabel = (probe: PublicNetworkProbe | undefined, channel: ProbeChannel) => {
  if (!probe) return '尚未测试';
  if (probe.state === 'REACHABLE') {
    const status = channel === 'HTTP' ? `HTTP ${probe.http_status}` : '握手成功';
    return `${status} · ${probe.latency_ms} ms`;
  }
  return probe.error?.kind || probe.state;
};

const hydrateForm = () => {
  for (const venue of venueIds) {
    const state = venueState(venue);
    forms[venue].public_rest_base_url = state?.public_rest_base_url || '';
    forms[venue].public_ws_base_url = state?.public_ws_base_url || '';
    forms[venue].history_base_url = state?.history_base_url || '';
    forms[venue].instruments_path = state?.instruments_path || '';
    forms[venue].tickers_path = state?.tickers_path || '';
    forms[venue].order_book_path = state?.order_book_path || '';
    forms[venue].candles_path = state?.candles_path || '';
    forms[venue].time_path = state?.time_path || '';
    // Proxy credentials are intentionally write-only in the API.
    forms[venue].http_proxy = '';
    forms[venue].ws_proxy = '';
    forms[venue].clear_http_proxy = false;
    forms[venue].clear_ws_proxy = false;
  }
};

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const response = await api.get<PublicNetworkSettings>('/settings/network');
    settings.value = response.data;
    hydrateForm();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '网络设置读取失败';
  } finally {
    loading.value = false;
  }
};

const save = async () => {
  saving.value = true;
  error.value = '';
  notice.value = '';
  const venues: Record<string, Record<string, string | boolean>> = {};
  for (const venue of venueIds) {
    const form = forms[venue];
    const update: Record<string, string | boolean> = {};
    if (form.public_rest_base_url.trim()) update.public_rest_base_url = form.public_rest_base_url.trim();
    if (form.public_ws_base_url.trim()) update.public_ws_base_url = form.public_ws_base_url.trim();
    if (form.history_base_url.trim()) update.history_base_url = form.history_base_url.trim();
    if (form.instruments_path.trim()) update.instruments_path = form.instruments_path.trim();
    if (form.tickers_path.trim()) update.tickers_path = form.tickers_path.trim();
    if (form.order_book_path.trim()) update.order_book_path = form.order_book_path.trim();
    if (form.candles_path.trim()) update.candles_path = form.candles_path.trim();
    if (form.time_path.trim()) update.time_path = form.time_path.trim();
    if (form.http_proxy.trim()) update.http_proxy = form.http_proxy.trim();
    if (form.ws_proxy.trim()) update.ws_proxy = form.ws_proxy.trim();
    if (form.clear_http_proxy) update.clear_http_proxy = true;
    if (form.clear_ws_proxy) update.clear_ws_proxy = true;
    venues[venue] = update;
  }
  try {
    const response = await api.put<PublicNetworkSettings>('/settings/network', { venues });
    settings.value = response.data;
    hydrateForm();
    notice.value = response.data.applied === false
      ? '配置已保存，但部分运行组件将在下次任务或重连时应用。'
      : '配置已保存，后端立即生效，行情 Worker 会自动重连应用。';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '网络设置保存失败';
  } finally {
    saving.value = false;
  }
};

const probe = async (venue: VenueId, channel: ProbeChannel) => {
  const key = `${venue}:${channel}`;
  probeBusy.value = key;
  error.value = '';
  try {
    const response = await api.post<PublicNetworkProbe>('/settings/network/probe', { venue_id: venue, channel });
    if (!probes[venue]) probes[venue] = {};
    probes[venue]![channel] = response.data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || `${venueLabels[venue]} ${channel} 连通性测试失败`;
  } finally {
    probeBusy.value = null;
  }
};

onMounted(load);
</script>

<template>
  <section class="network-settings" aria-labelledby="network-settings-title">
    <section class="page-heading network-heading">
      <div>
        <p class="kicker">PUBLIC EGRESS / 15</p>
        <h1 id="network-settings-title">网络设置</h1>
        <p class="muted">按交易所维护公开接口地址与代理。接口地址改变时可直接修改，代理留空表示直连。</p>
      </div>
      <button class="icon-button" type="button" title="刷新网络设置" aria-label="刷新网络设置" :disabled="loading || saving" @click="load">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
      </button>
    </section>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="notice" class="inline-notice" role="status"><Check :size="14" /> {{ notice }}</div>

    <section class="network-summary" aria-label="当前网络设置摘要">
      <div class="summary-mark"><Globe2 :size="20" /></div>
      <div><span>当前来源</span><strong>{{ sourceLabel }}</strong><small>更新时间：{{ formatDate(settings?.updated_at) }}</small></div>
      <div class="summary-fact"><span>代理协议</span><strong>HTTP / HTTPS</strong></div>
      <div class="summary-fact"><span>执行模式</span><strong class="safe">DISABLED</strong></div>
    </section>

    <div v-if="settings?.file_error" class="inline-warning" role="status"><CircleAlert :size="14" /> {{ settings.file_error }}</div>

    <form class="network-form" @submit.prevent="save">
      <article v-for="venue in venueIds" :key="venue" class="network-card">
        <header class="network-card-heading">
          <div class="venue-heading-icon"><Globe2 :size="17" /></div>
          <div><p>{{ venue.toUpperCase() }}</p><h2>{{ venueLabels[venue] }}</h2><small>{{ venueNotes[venue] }}</small></div>
          <div class="probe-actions">
            <button class="probe-button" type="button" :disabled="probeBusy === `${venue}:HTTP`" @click="probe(venue, 'HTTP')">
              <RefreshCw v-if="probeBusy === `${venue}:HTTP`" :size="14" class="spinning" /><Wifi v-else :size="14" />测试 HTTP
            </button>
            <button class="probe-button" type="button" :disabled="probeBusy === `${venue}:WebSocket`" @click="probe(venue, 'WebSocket')">
              <RefreshCw v-if="probeBusy === `${venue}:WebSocket`" :size="14" class="spinning" /><Wifi v-else :size="14" />测试 WS
            </button>
          </div>
        </header>

        <div class="current-channels">
          <div v-for="channel in (['http_proxy', 'ws_proxy'] as const)" :key="channel" class="channel-row">
            <span class="channel-icon" :class="channelState(venue, channel)?.mode"><ShieldCheck :size="13" /></span>
            <span><strong>{{ channel === 'http_proxy' ? 'HTTP / REST' : 'WebSocket / 实时' }}</strong><small>{{ channelState(venue, channel)?.display || '未配置，使用直连' }}</small></span>
            <b :class="channelState(venue, channel)?.configured ? 'proxy' : 'direct'">{{ channelLabel(!!channelState(venue, channel)?.configured) }}</b>
          </div>
        </div>

        <div class="endpoint-grid">
          <label :for="`${venue}-public-rest`"><span>公共 REST 根地址</span><input :id="`${venue}-public-rest`" v-model="forms[venue].public_rest_base_url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://api.example.com" :aria-describedby="`${venue}-endpoint-help`" /><small>品种目录、公开盘口和连通性测试使用。</small></label>
          <label :for="`${venue}-public-ws`"><span>公共 WebSocket 地址</span><input :id="`${venue}-public-ws`" v-model="forms[venue].public_ws_base_url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="wss://stream.example.com" :aria-describedby="`${venue}-endpoint-help`" /><small>实时行情订阅使用。</small></label>
          <label :for="`${venue}-history`"><span>历史 K 线 REST 根地址</span><input :id="`${venue}-history`" v-model="forms[venue].history_base_url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://data.example.com" :aria-describedby="`${venue}-endpoint-help`" /><small>历史下载和 Scheduler 使用。</small></label>
        </div>
        <p :id="`${venue}-endpoint-help`" class="card-help">REST 只接受 HTTP/HTTPS，实时地址只接受 WS/WSS；不要填写 API Key、账号密码或 query 参数。</p>

        <div class="api-route-grid">
          <label :for="`${venue}-instruments-path`"><span>品种目录 API 路径</span><input :id="`${venue}-instruments-path`" v-model="forms[venue].instruments_path" type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="/api/v5/public/instruments" :aria-describedby="`${venue}-route-help`" /><small>市场列表和交易对元数据。</small></label>
          <label :for="`${venue}-tickers-path`"><span>全市场行情 API 路径</span><input :id="`${venue}-tickers-path`" v-model="forms[venue].tickers_path" type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="/api/v5/market/tickers" :aria-describedby="`${venue}-route-help`" /><small>全币种价格、涨跌和成交量。</small></label>
          <label :for="`${venue}-order-book-path`"><span>盘口快照 API 路径</span><input :id="`${venue}-order-book-path`" v-model="forms[venue].order_book_path" type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="/api/v5/market/books" :aria-describedby="`${venue}-route-help`" /><small>实时 WS 重连前的 REST 快照。</small></label>
          <label :for="`${venue}-candles-path`"><span>历史 K 线 API 路径</span><input :id="`${venue}-candles-path`" v-model="forms[venue].candles_path" type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="/api/v5/market/history-candles" :aria-describedby="`${venue}-route-help`" /><small>历史任务和 Scheduler 使用。</small></label>
          <label :for="`${venue}-time-path`"><span>连通性探测 API 路径</span><input :id="`${venue}-time-path`" v-model="forms[venue].time_path" type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="/api/v5/public/time" :aria-describedby="`${venue}-route-help`" /><small>测试 HTTP 按钮使用。</small></label>
        </div>
        <p :id="`${venue}-route-help`" class="card-help">路径必须以 / 开头，不能带 query 或 fragment。这里可手动切换 API 版本或区域路径；请求参数和返回 JSON 的规范化仍由该交易所适配器负责。</p>

        <div class="network-input-grid">
          <label :for="`${venue}-http-proxy`"><span>HTTP / REST 代理 URL</span><input :id="`${venue}-http-proxy`" v-model="forms[venue].http_proxy" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="http://主机:端口" :aria-describedby="`${venue}-proxy-help`" /><small>用于品种目录、历史 K 线和 REST 快照。</small></label>
          <label :for="`${venue}-ws-proxy`"><span>WebSocket 代理 URL</span><input :id="`${venue}-ws-proxy`" v-model="forms[venue].ws_proxy" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="http://主机:端口" :aria-describedby="`${venue}-proxy-help`" /><small>用于实时行情；通常与 HTTP 代理填写相同。</small></label>
        </div>
        <p :id="`${venue}-proxy-help`" class="card-help">支持完整的 HTTP/HTTPS URL，可带代理认证。已配置值不会回显原文；代理输入留空保持不变。</p>
        <div class="clear-options">
          <label><input v-model="forms[venue].clear_http_proxy" type="checkbox" /><Trash2 :size="13" />清除 HTTP 代理</label>
          <label><input v-model="forms[venue].clear_ws_proxy" type="checkbox" /><Trash2 :size="13" />清除 WebSocket 代理</label>
          <span class="probe-result" :class="probes[venue]?.HTTP?.state?.toLowerCase()">HTTP {{ probeLabel(probes[venue]?.HTTP, 'HTTP') }}</span>
          <span class="probe-result" :class="probes[venue]?.WebSocket?.state?.toLowerCase()">WS {{ probeLabel(probes[venue]?.WebSocket, 'WebSocket') }}</span>
        </div>
      </article>

      <footer class="network-actions">
        <span><ShieldCheck :size="14" />只读公开接口 · 不包含 API Key、账户或订单权限</span>
        <button class="primary-small" type="submit" :disabled="saving || loading"><RefreshCw v-if="saving" :size="14" class="spinning" /><Save v-else :size="14" />保存网络设置</button>
      </footer>
    </form>

    <p class="network-footnote"><CircleAlert :size="14" />连接测试分别验证公开 HTTP 与 WebSocket。实时行情 Worker 会在配置文件变化后自动断开并通过新代理重连。</p>
  </section>
</template>

<style scoped>
.network-settings{display:grid;gap:24px}.network-heading{margin-bottom:0}.network-summary{display:grid;grid-template-columns:42px minmax(0,1fr) repeat(2,150px);align-items:center;gap:14px;border:1px solid var(--line);background:var(--panel);padding:16px 18px}.summary-mark{display:grid;place-items:center;width:36px;height:36px;border:1px solid var(--line-bright);color:var(--cyan)}.network-summary>div:nth-child(2){display:grid;gap:4px;min-width:0}.network-summary span,.summary-fact span{color:var(--dim);font-size:9px}.network-summary strong{color:var(--ink);font-size:12px}.network-summary small{color:var(--muted);font-size:10px}.summary-fact{display:grid;gap:5px;border-left:1px solid var(--line);padding-left:15px}.summary-fact strong{font:600 11px Consolas,monospace}.summary-fact .safe{color:var(--amber)}.inline-notice{display:flex;align-items:center;gap:8px;border-left:2px solid var(--cyan);padding:8px 12px;color:var(--cyan);background:rgba(108,229,208,.08);font-size:12px}.inline-warning{display:flex;align-items:start;gap:8px;border-left:2px solid var(--amber);padding:8px 12px;color:var(--amber);background:rgba(228,179,109,.08);font-size:11px;line-height:1.5}.inline-warning svg,.network-footnote svg{flex:0 0 auto}.network-form{display:grid;gap:13px}.network-card{min-width:0;border:1px solid var(--line);border-radius:var(--radius);padding:19px;background:var(--panel)}.network-card-heading{display:grid;grid-template-columns:34px minmax(0,1fr) auto;align-items:center;gap:11px;padding-bottom:16px;border-bottom:1px solid var(--line)}.venue-heading-icon{display:grid;place-items:center;width:32px;height:32px;border:1px solid var(--line-bright);color:var(--cyan)}.network-card-heading div:nth-child(2){display:grid;gap:3px;min-width:0}.network-card-heading p{margin:0;color:var(--cyan);font:9px Consolas,monospace;letter-spacing:.1em}.network-card-heading h2{margin:0;color:var(--ink);font-size:18px;font-weight:580}.network-card-heading small{color:var(--dim);font-size:10px;overflow-wrap:anywhere}.probe-actions{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:7px}.probe-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:36px;border:1px solid var(--line-bright);border-radius:5px;padding:8px 10px;color:var(--muted);background:transparent;font-size:10px;white-space:nowrap}.probe-button:hover:not(:disabled){border-color:var(--cyan);color:var(--cyan)}.probe-button:disabled{opacity:.55}.current-channels{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:15px 0}.channel-row{display:grid;grid-template-columns:26px minmax(0,1fr) auto;align-items:center;gap:8px;min-width:0}.channel-icon{display:grid;place-items:center;width:24px;height:24px;border:1px solid var(--line-bright);color:var(--dim)}.channel-icon.proxy{color:var(--amber);border-color:#5f503a}.channel-row>span:nth-child(2){display:grid;gap:3px;min-width:0}.channel-row strong{color:var(--muted);font-size:10px}.channel-row small{color:var(--dim);font-size:9px;overflow-wrap:anywhere}.channel-row b{font:700 9px Consolas,monospace}.channel-row b.proxy{color:var(--amber)}.channel-row b.direct{color:var(--cyan)}.endpoint-grid,.network-input-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding-top:15px;border-top:1px solid var(--line)}.network-input-grid{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:14px}.endpoint-grid label,.network-input-grid label{display:grid;gap:7px;min-width:0}.endpoint-grid label>span,.network-input-grid label>span{color:var(--muted);font-size:10px}.endpoint-grid input,.network-input-grid input{width:100%;min-height:38px;border:1px solid var(--line-bright);border-radius:5px;padding:9px 10px;color:var(--ink);background:#13191b;outline:none;font:11px Consolas,monospace;transition:border-color .18s ease,box-shadow .18s ease}.endpoint-grid input:focus,.network-input-grid input:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(108,229,208,.1)}.endpoint-grid input::placeholder,.network-input-grid input::placeholder{color:var(--dim)}.endpoint-grid label>small,.network-input-grid label>small{color:var(--dim);font-size:9px;line-height:1.5}.card-help{margin:12px 0 0;color:var(--dim);font-size:9px;line-height:1.6}.clear-options{display:flex;align-items:center;flex-wrap:wrap;gap:14px;margin-top:13px;padding-top:12px;border-top:1px solid var(--line)}.clear-options label{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:10px}.clear-options input{accent-color:var(--amber)}.clear-options svg{color:var(--amber)}.probe-result{color:var(--dim);font:10px Consolas,monospace}.probe-result+.probe-result{margin-left:auto}.probe-result.reachable{color:var(--cyan)}.probe-result.blocked,.probe-result.rejected{color:var(--red)}.network-actions{display:flex;align-items:center;justify-content:space-between;gap:16px;border-top:1px solid var(--line);padding-top:18px;color:var(--dim);font-size:10px}.network-actions>span{display:inline-flex;align-items:center;gap:7px;line-height:1.5}.network-actions svg{color:var(--amber)}.primary-small{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:36px;border:1px solid var(--cyan);border-radius:5px;padding:8px 12px;color:#11201e;background:var(--cyan);font-size:10px;font-weight:700;white-space:nowrap}.primary-small:hover:not(:disabled){background:#94f0df}.primary-small:disabled{cursor:not-allowed;opacity:.55}.network-footnote{display:flex;align-items:start;gap:7px;margin:0;color:var(--dim);font-size:10px;line-height:1.6}
@media(max-width:800px){.network-summary{grid-template-columns:36px minmax(0,1fr);gap:11px}.summary-fact{border-left:0;border-top:1px solid var(--line);padding:12px 0 0}.network-summary .summary-fact:nth-child(3){grid-column:1 / -1}.network-summary .summary-fact:nth-child(4){grid-column:1 / -1}.network-card-heading{grid-template-columns:34px minmax(0,1fr)}.probe-actions{grid-column:2;justify-self:start;justify-content:flex-start}.network-actions{align-items:start;flex-direction:column}.primary-small{width:100%}.endpoint-grid{grid-template-columns:1fr}}
@media(max-width:520px){.current-channels,.network-input-grid{grid-template-columns:1fr}.network-card{padding:15px 13px}.clear-options{align-items:start;flex-direction:column;gap:9px}.probe-result,.probe-result+.probe-result{margin-left:0}.network-heading{gap:16px}}
.api-route-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px;padding-top:15px;border-top:1px solid var(--line)}.api-route-grid label{display:grid;gap:7px;min-width:0}.api-route-grid label>span{color:var(--muted);font-size:10px}.api-route-grid input{width:100%;min-height:38px;border:1px solid var(--line-bright);border-radius:5px;padding:9px 10px;color:var(--ink);background:#13191b;outline:none;font:11px Consolas,monospace;transition:border-color .18s ease,box-shadow .18s ease}.api-route-grid input:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(108,229,208,.1)}.api-route-grid input::placeholder{color:var(--dim)}.api-route-grid label>small{color:var(--dim);font-size:9px;line-height:1.5}
@media(max-width:800px){.api-route-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.api-route-grid{grid-template-columns:1fr}}
</style>
