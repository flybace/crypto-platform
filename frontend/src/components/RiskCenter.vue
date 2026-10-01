<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { AlertOctagon, Check, RefreshCw, Save, ShieldCheck, ShieldX } from 'lucide-vue-next';
import { api } from '../api';
import type { RiskSummary } from '../types';

const summary = ref<RiskSummary | null>(null);
const events = ref<{ event_id: string; event_type: string; created_at: string }[]>([]);
const loading = ref(false);
const saving = ref(false);
const stopping = ref(false);
const error = ref('');
const notice = ref('');
const form = reactive({
  enabled: false,
  mode: 'DISABLED',
  venue_allowlist: '',
  instrument_allowlist: '',
  quote_asset_allowlist: 'USDT',
  max_order_notional_quote: '0',
  max_daily_sell_notional_quote: '0',
  max_daily_sell_ratio: '0',
  min_base_reserve: '',
  allow_market_order: false,
  max_slippage_bps: '0',
  max_market_age_seconds: 2,
  max_open_orders: 1,
  max_orders_per_minute: 1,
});

const policy = computed(() => summary.value?.policy || {});
const statusText = computed(() => summary.value?.state === 'SAFE_PAUSED' ? '安全暂停' : '候选配置');
const reasonLabel = (value: string) => ({ EXECUTION_MODE_DISABLED: '服务端执行关闭', EMERGENCY_STOP_ACTIVE: '紧急停止已触发', POLICY_DISABLED: '策略开关关闭', POLICY_MODE_DISABLED: '风控模式关闭', VENUE_ALLOWLIST_EMPTY: '交易所白名单为空', INSTRUMENT_ALLOWLIST_EMPTY: '交易对白名单为空', ORDER_LIMIT_UNCONFIGURED: '单笔额度未配置', DAILY_LIMIT_UNCONFIGURED: '单日额度未配置', SLIPPAGE_LIMIT_UNCONFIGURED: '滑点上限未配置' }[value] || value);
const formatDate = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });

const syncForm = (next: Record<string, any>) => {
  Object.assign(form, {
    enabled: next.enabled === true,
    mode: next.mode || 'DISABLED',
    venue_allowlist: (next.venue_allowlist || []).join(','),
    instrument_allowlist: (next.instrument_allowlist || []).join(','),
    quote_asset_allowlist: (next.quote_asset_allowlist || ['USDT']).join(','),
    max_order_notional_quote: String(next.max_order_notional_quote ?? '0'),
    max_daily_sell_notional_quote: String(next.max_daily_sell_notional_quote ?? '0'),
    max_daily_sell_ratio: String(next.max_daily_sell_ratio ?? '0'),
    min_base_reserve: next.min_base_reserve == null ? '' : String(next.min_base_reserve),
    allow_market_order: next.allow_market_order === true,
    max_slippage_bps: String(next.max_slippage_bps ?? '0'),
    max_market_age_seconds: Number(next.max_market_age_seconds ?? 2),
    max_open_orders: Number(next.max_open_orders ?? 1),
    max_orders_per_minute: Number(next.max_orders_per_minute ?? 1),
  });
};

const loadRisk = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [summaryResponse, eventsResponse] = await Promise.all([
      api.get<RiskSummary>('/risk/summary'),
      api.get<{ items: { event_id: string; event_type: string; created_at: string }[] }>('/risk/events'),
    ]);
    summary.value = summaryResponse.data;
    events.value = eventsResponse.data.items;
    syncForm(summary.value.policy);
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '风控状态读取失败';
  } finally {
    loading.value = false;
  }
};

const savePolicy = async () => {
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<RiskSummary>('/risk/policy', {
      ...form,
      venue_allowlist: form.venue_allowlist.split(',').map((item) => item.trim()).filter(Boolean),
      instrument_allowlist: form.instrument_allowlist.split(',').map((item) => item.trim()).filter(Boolean),
      quote_asset_allowlist: form.quote_asset_allowlist.split(',').map((item) => item.trim().toUpperCase()).filter(Boolean),
      min_base_reserve: form.min_base_reserve || null,
    });
    summary.value = data;
    syncForm(data.policy);
    notice.value = '风控配置已保存，真实执行仍由服务端关闭';
    await loadRisk();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '风控配置保存失败';
  } finally {
    saving.value = false;
  }
};

const emergencyStop = async () => {
  stopping.value = true;
  try {
    const { data } = await api.post<RiskSummary>('/risk/emergency-stop');
    summary.value = data;
    notice.value = '安全暂停已触发';
    await loadRisk();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '安全暂停失败';
  } finally {
    stopping.value = false;
  }
};

const releaseStop = async () => {
  stopping.value = true;
  try {
    const { data } = await api.post<RiskSummary>('/risk/emergency-stop/release', { confirmation: true });
    summary.value = data;
    notice.value = '安全暂停已解除，但真实执行仍保持关闭';
    await loadRisk();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '解除安全暂停失败';
  } finally {
    stopping.value = false;
  }
};

onMounted(loadRisk);
</script>

<template>
  <section class="risk-center" aria-labelledby="risk-title">
    <section class="page-heading risk-heading"><div><p class="kicker">GUARDRAILS / 09</p><h1 id="risk-title">风控中心</h1><p class="muted">服务端保存限额、白名单和安全暂停状态；配置完整也不会绕过真实执行门禁。</p></div><button class="icon-button" type="button" title="刷新风控状态" aria-label="刷新风控状态" :disabled="loading" @click="loadRisk"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div><div v-if="notice" class="inline-notice" role="status">{{ notice }}</div>
    <section class="risk-banner"><ShieldX :size="17" /><div><strong>真实订单路由已阻断</strong><span>当前服务端 execution_mode = {{ summary?.execution_mode || 'DISABLED' }}，本页面只管理候选策略的风险配置。</span></div><span class="risk-state">{{ statusText }}</span></section>
    <section class="risk-metrics" aria-label="风控摘要"><article><span>硬状态</span><strong :class="summary?.state === 'SAFE_PAUSED' ? 'warn' : 'positive'">{{ statusText }}</strong><em>{{ summary?.blocking_reasons.length || 0 }} 个阻断原因</em></article><article><span>运行模式</span><strong>{{ policy.mode || 'DISABLED' }}</strong><em>首阶段仅 SELL_ONLY</em></article><article><span>自动交易</span><strong class="metric-word">{{ summary?.real_orders_allowed ? '候选' : '关闭' }}</strong><em>服务端强制</em></article></section>

    <section class="risk-layout"><section class="risk-panel policy-panel" aria-labelledby="policy-title"><div class="section-heading"><div><p class="kicker">POLICY CONFIGURATION</p><h2 id="policy-title">限额配置</h2></div><ShieldCheck :size="18" class="section-icon" /></div><form class="risk-form" @submit.prevent="savePolicy"><label class="check-field"><input v-model="form.enabled" type="checkbox" /><span>启用候选风控策略</span></label><label><span>模式</span><select v-model="form.mode"><option value="DISABLED">DISABLED</option><option value="SELL_ONLY">SELL_ONLY</option><option value="BUY_SELL">BUY_SELL（后置）</option></select></label><label><span>交易所白名单</span><input v-model="form.venue_allowlist" placeholder="binance,bybit" /></label><label><span>交易对白名单</span><input v-model="form.instrument_allowlist" placeholder="binance:spot:BTC/USDT" /></label><label><span>计价资产</span><input v-model="form.quote_asset_allowlist" /></label><div class="field-grid"><label><span>单笔最大 USDT</span><input v-model="form.max_order_notional_quote" type="number" min="0" step="1" /></label><label><span>单日最大 USDT</span><input v-model="form.max_daily_sell_notional_quote" type="number" min="0" step="1" /></label></div><div class="field-grid"><label><span>单日卖出比例</span><input v-model="form.max_daily_sell_ratio" type="number" min="0" max="1" step="0.01" /></label><label><span>最低保留数量</span><input v-model="form.min_base_reserve" type="number" min="0" step="0.000001" placeholder="必填才允许" /></label></div><div class="field-grid"><label><span>最大滑点 bps</span><input v-model="form.max_slippage_bps" type="number" min="0" step="1" /></label><label><span>行情最大年龄秒</span><input v-model="form.max_market_age_seconds" type="number" min="1" /></label></div><label class="check-field"><input v-model="form.allow_market_order" type="checkbox" /><span>允许市价单（当前仅记录配置）</span></label><button class="risk-submit" type="submit" :disabled="saving"><Save v-if="!saving" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ saving ? '保存中' : '保存风控配置' }}</span></button></form></section>

      <section class="risk-panel gate-panel" aria-labelledby="gate-title"><div class="section-heading"><div><p class="kicker">EXECUTION GATES</p><h2 id="gate-title">门禁状态</h2></div><AlertOctagon :size="18" class="section-icon" /></div><div class="gate-list"><article v-for="gate in summary?.gates || []" :key="gate.key" :class="{ passed: gate.passed }"><span class="gate-icon"><Check v-if="gate.passed" :size="14" /><ShieldX v-else :size="14" /></span><div><strong>{{ gate.label }}</strong><small>{{ gate.detail }}</small></div><b>{{ gate.passed ? 'PASS' : 'BLOCKED' }}</b></article></div><div class="reason-list"><strong>当前阻断</strong><span v-for="reason in summary?.blocking_reasons || []" :key="reason">{{ reasonLabel(reason) }}</span></div><div class="stop-actions"><button class="danger-button" type="button" :disabled="stopping || policy.emergency_stop === true" @click="emergencyStop"><AlertOctagon :size="14" /> 紧急停止</button><button v-if="policy.emergency_stop === true" class="secondary-button" type="button" :disabled="stopping" @click="releaseStop"><ShieldCheck :size="14" /> 确认解除</button></div></section></section>

    <section class="risk-panel event-panel" aria-labelledby="risk-events-title"><div class="section-heading"><div><p class="kicker">RISK AUDIT</p><h2 id="risk-events-title">风控事件</h2></div><span class="section-meta">{{ events.length }} EVENTS</span></div><div v-if="!events.length" class="empty-state"><AlertOctagon :size="20" /><p>暂无风控事件</p></div><div v-else class="event-list"><div v-for="event in events" :key="event.event_id" class="event-row"><span>{{ event.event_type }}</span><strong>{{ event.event_id }}</strong><small>{{ formatDate(event.created_at) }}</small></div></div></section>
  </section>
</template>

<style scoped>
.risk-center { display: grid; gap: 30px; }
.risk-heading { margin-bottom: 0; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; }
.risk-banner { display: flex; align-items: start; gap: 10px; border-left: 2px solid var(--red); padding: 12px 14px; color: var(--red); background: rgba(238, 129, 120, .07); }
.risk-banner div { display: grid; gap: 4px; flex: 1; }
.risk-banner strong { color: var(--ink); font-size: 12px; font-weight: 620; }
.risk-banner span { color: var(--muted); font-size: 11px; line-height: 1.5; }
.risk-state { color: var(--amber) !important; white-space: nowrap; }
.risk-metrics { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.risk-metrics article { display: grid; gap: 7px; min-height: 106px; padding: 18px 20px; border-right: 1px solid var(--line); }
.risk-metrics article:last-child { border-right: 0; }
.risk-metrics span, .risk-metrics em { color: var(--dim); font-size: 10px; font-style: normal; }
.risk-metrics strong { align-self: center; color: var(--ink); font: 600 20px Consolas, monospace; }
.risk-metrics .metric-word { font-family: inherit; }
.positive { color: var(--cyan) !important; }
.warn { color: var(--amber) !important; }
.risk-layout { display: grid; grid-template-columns: minmax(290px, .9fr) minmax(0, 1.1fr); gap: 13px; align-items: start; }
.risk-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 19px; background: var(--panel); }
.risk-form { display: grid; gap: 13px; }
.risk-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.risk-form input, .risk-form select { width: 100%; min-height: 36px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 9px; color: var(--ink); background: #13191b; outline: none; }
.risk-form input:focus, .risk-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.check-field { display: flex !important; align-items: center; gap: 8px; }
.check-field input { width: auto; min-height: auto; accent-color: var(--cyan); }
.risk-submit { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 39px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 12px; font-weight: 720; }
.risk-submit:hover:not(:disabled) { background: #94f0df; }
.risk-submit:disabled { opacity: .55; }
.gate-list { display: grid; border-top: 1px solid var(--line); }
.gate-list article { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 61px; border-bottom: 1px solid var(--line); }
.gate-icon { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid #734943; color: var(--red); }
.gate-list article.passed .gate-icon { border-color: #42645d; color: var(--cyan); }
.gate-list article > div { display: grid; gap: 4px; min-width: 0; }
.gate-list strong { color: var(--ink); font-size: 11px; font-weight: 580; }
.gate-list small { color: var(--dim); font-size: 10px; }
.gate-list b { color: var(--red); font: 700 9px Consolas, monospace; }
.gate-list article.passed b { color: var(--cyan); }
.reason-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 14px 0 2px; }
.reason-list strong { width: 100%; color: var(--dim); font-size: 10px; font-weight: 500; }
.reason-list span { border: 1px solid #734943; padding: 5px 7px; color: #ffaaa2; background: rgba(238, 129, 120, .06); font-size: 9px; }
.stop-actions { display: flex; gap: 8px; margin-top: 17px; }
.danger-button, .secondary-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 33px; border-radius: 5px; padding: 7px 10px; font-size: 10px; }
.danger-button { border: 1px solid var(--red); color: var(--red); background: transparent; }
.danger-button:hover:not(:disabled) { background: rgba(238, 129, 120, .09); }
.danger-button:disabled { opacity: .45; }
.secondary-button { border: 1px solid var(--line-bright); color: var(--muted); background: transparent; }
.secondary-button:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.event-panel { padding-bottom: 12px; }
.event-list { display: grid; border-top: 1px solid var(--line); }
.event-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 12px; min-height: 43px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; }
.event-row span { color: var(--ink); }
.event-row strong, .event-row small { color: var(--dim); font: 9px Consolas, monospace; }
.empty-state { display: grid; justify-items: center; gap: 8px; min-height: 110px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); }
.empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
@media (max-width: 900px) { .risk-layout { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .risk-metrics { grid-template-columns: 1fr; } .risk-metrics article { min-height: 80px; border-right: 0; border-bottom: 1px solid var(--line); } .risk-metrics article:last-child { border-bottom: 0; } .field-grid { grid-template-columns: 1fr; } .risk-banner { flex-wrap: wrap; } .risk-state { width: 100%; margin-left: 27px; } .event-row { grid-template-columns: 1fr auto; } .event-row small { grid-column: 1 / -1; padding-bottom: 8px; } }
</style>

