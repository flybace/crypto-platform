<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Activity, ArrowDown, ArrowUp, RefreshCw, ScanSearch, ShieldCheck } from 'lucide-vue-next';
import { api } from '../api';
import type { AdviceCandidate, AdviceSummary } from '../types';

const interval = ref<'1d' | '1h' | '5m'>('1h');
const summary = ref<AdviceSummary | null>(null);
const candidates = ref<AdviceCandidate[]>([]);
const loading = ref(false);
const generating = ref(false);
const error = ref('');
const notice = ref('');

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [summaryResponse, candidatesResponse] = await Promise.all([
      api.get<AdviceSummary>('/advice/summary', { params: { interval: interval.value, compact: true } }),
      api.get<{ items: AdviceCandidate[] }>('/advice/candidates', { params: { interval: interval.value, limit: 100, compact: true } }),
    ]);
    summary.value = summaryResponse.data;
    candidates.value = candidatesResponse.data.items || [];
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '研究建议读取失败';
  } finally {
    loading.value = false;
  }
};

const generate = async () => {
  generating.value = true;
  error.value = '';
  notice.value = '';
  try {
    await api.post('/advice/generate', null, { params: { interval: interval.value, limit: 100 } });
    notice.value = '研究快照已生成';
    await load();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '研究快照生成失败';
  } finally {
    generating.value = false;
  }
};

const gradeClass = (value: string) => `grade-${value}`;
const riskClass = (value: string) => `risk-${value}`;
const number = (value?: string | null, digits = 2) => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('en-US', { maximumFractionDigits: digits });
};
const pct = (value?: string | null) => value === null || value === undefined ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
const venue = (value?: string | null) => value ? value.toUpperCase() : '—';
const focusItems = computed(() => candidates.value.filter((item) => item.recommendation_grade === 'focus').length);

onMounted(load);
</script>

<template>
  <section class="advice-center" aria-labelledby="advice-title">
    <section class="page-heading advice-heading">
      <div><p class="kicker">DECISION QUEUE / 06</p><h1 id="advice-title">研究建议</h1><p class="muted">把已校验行情和筛选结果整理成候选队列，所有记录都必须先经过回测和模拟盘。</p></div>
      <button class="icon-button" type="button" title="刷新研究建议" aria-label="刷新研究建议" :disabled="loading" @click="load"><RefreshCw :size="17" :class="{ spinning: loading }" /></button>
    </section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="notice" class="inline-notice" role="status">{{ notice }}</div>
    <section class="advice-toolbar">
      <label><span>数据周期</span><select v-model="interval" @change="load"><option value="1h">1 小时</option><option value="1d">1 日</option><option value="5m">5 分钟</option></select></label>
      <div class="advice-toolbar-note"><Activity :size="15" /><span>研究投影 · 不产生订单</span><button class="primary-small" type="button" :disabled="generating" @click="generate"><RefreshCw v-if="generating" :size="14" class="spinning" /><ScanSearch v-else :size="14" />生成快照</button></div>
    </section>
    <section class="advice-metrics" aria-label="研究建议摘要">
      <article><span>候选总数</span><strong>{{ summary?.total || 0 }}</strong><em>{{ interval }} 数据</em></article>
      <article><span>重点研究</span><strong class="positive">{{ focusItems }}</strong><em>不是买入信号</em></article>
      <article><span>观察</span><strong>{{ summary?.watch || 0 }}</strong><em>等待回测确认</em></article>
      <article><span>执行资格</span><strong class="warn">关闭</strong><em>服务端安全门禁</em></article>
    </section>
    <section class="advice-panel" aria-labelledby="advice-table-title">
      <header class="section-heading"><div><p class="kicker">RESEARCH CANDIDATES</p><h2 id="advice-table-title">候选队列</h2></div><span class="section-meta">{{ candidates.length }} ITEMS</span></header>
      <div v-if="!candidates.length && !loading" class="advice-empty"><ScanSearch :size="24" /><strong>当前周期没有候选</strong><span>先补齐历史数据，或切换到已有覆盖周期。</span></div>
      <div v-else class="advice-table-wrap">
        <table class="advice-table">
          <thead><tr><th>候选</th><th>研究评级</th><th>变化</th><th>跨市场末价差</th><th>风险</th><th>下一步</th></tr></thead>
          <tbody>
            <tr v-for="item in candidates" :key="`${item.venue_id}-${item.symbol}-${item.rank}`">
              <td><strong>{{ item.symbol }}</strong><small>{{ venue(item.venue_id) }} · {{ number(item.close, 4) }}</small></td>
              <td><span class="grade-pill" :class="gradeClass(item.recommendation_grade)">{{ item.recommendation_label }}</span><small>评分 {{ number(item.recommendation_score) }}</small></td>
              <td :class="Number(item.change_pct) >= 0 ? 'positive' : 'negative'"><ArrowUp v-if="Number(item.change_pct) >= 0" :size="13" /><ArrowDown v-else :size="13" />{{ pct(item.change_pct) }}</td>
              <td><strong>{{ pct(item.spread_pct) }}</strong><small>{{ venue(item.spread_buy_venue_id) }} → {{ venue(item.spread_sell_venue_id) }}</small></td>
              <td><span class="risk-pill" :class="riskClass(item.risk_level)">{{ item.risk_level }}</span></td>
              <td class="next-action">{{ item.next_action }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="advice-footnote"><ShieldCheck :size="14" />候选来自已校验历史 K 线或筛选结果；即使评级为重点，也不会绕过策略、模拟盘、风控和人工授权。</p>
    </section>
  </section>
</template>

<style scoped>
.advice-center{display:grid;gap:24px}.advice-heading{margin-bottom:0}.inline-notice{border-left:2px solid var(--cyan);padding:8px 12px;color:var(--cyan);background:rgba(108,229,208,.07);font-size:12px}.advice-toolbar{display:flex;align-items:end;justify-content:space-between;gap:14px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:13px 0}.advice-toolbar label{display:grid;gap:6px;color:var(--muted);font-size:10px}.advice-toolbar select{min-width:130px;border:1px solid var(--line-bright);border-radius:5px;padding:8px;color:var(--ink);background: var(--input-bg)}.advice-toolbar-note{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:11px}.advice-toolbar-note svg{color:var(--cyan)}.primary-small{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--cyan);border-radius:5px;padding:8px 11px;color:#11201e;background:var(--cyan);font-size:11px;font-weight:700}.primary-small:disabled{opacity:.6}.advice-metrics{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.advice-metrics article{display:grid;gap:7px;min-height:104px;padding:17px 20px;border-right:1px solid var(--line)}.advice-metrics article:last-child{border-right:0}.advice-metrics span,.advice-metrics em{color:var(--dim);font-size:10px;font-style:normal}.advice-metrics strong{align-self:center;color:var(--ink);font:600 23px Consolas,monospace}.positive{color:var(--cyan)!important}.negative{color:var(--red)!important}.warn{color:var(--amber)!important}.advice-panel{border:1px solid var(--line);background:var(--panel);padding:22px 18px 14px}.advice-panel .section-heading{margin-bottom:14px}.advice-table-wrap{overflow-x:auto}.advice-table{width:100%;min-width:820px;border-collapse:collapse;font-variant-numeric:tabular-nums}.advice-table th,.advice-table td{padding:10px 8px;border-top:1px solid var(--line);color:var(--muted);font-size:10px;text-align:left;vertical-align:middle}.advice-table th{color:var(--dim);font-size:9px;font-weight:600;letter-spacing:.08em}.advice-table td:first-child,.advice-table td:nth-child(2),.advice-table td:nth-child(4){display:grid;gap:4px}.advice-table strong{color:var(--ink);font-size:11px;font-weight:600}.advice-table small{color:var(--dim);font-size:9px}.advice-table td svg{vertical-align:-3px}.grade-pill,.risk-pill{display:inline-flex;width:max-content;border:1px solid var(--line-bright);padding:4px 6px;font-size:9px}.grade-focus{border-color:#42645d;color:var(--cyan);background:rgba(108,229,208,.07)}.grade-watch{color:var(--amber);border-color:#665637}.grade-avoid{color:var(--red);border-color:#734943}.risk-low{color:var(--cyan)}.risk-medium{color:var(--amber)}.risk-high{color:var(--red)}.next-action{max-width:270px;line-height:1.5}.advice-empty{display:grid;justify-items:center;gap:7px;min-height:180px;place-content:center;border:1px dashed var(--line-bright);color:var(--dim)}.advice-empty strong{color:var(--ink);font-size:13px}.advice-empty span{font-size:10px}.advice-footnote{display:flex;align-items:start;gap:7px;margin:14px 0 0;color:var(--dim);font-size:10px;line-height:1.6}.advice-footnote svg{flex:0 0 auto;color:var(--amber)}@media(max-width:720px){.advice-toolbar{align-items:start;flex-direction:column}.advice-toolbar-note{justify-content:space-between;width:100%;flex-wrap:wrap}.advice-toolbar-note span{margin-right:auto}.advice-metrics{grid-template-columns:1fr 1fr}.advice-metrics article:nth-child(2n){border-right:0}.advice-metrics article:nth-child(-n+2){border-bottom:1px solid var(--line)}.advice-metrics article:nth-child(3){border-bottom:0}.advice-metrics article:nth-child(4){border-bottom:0}}
</style>
