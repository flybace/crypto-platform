<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import {
  ColorType,
  createChart,
  AreaSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { AlertTriangle, RefreshCw } from 'lucide-vue-next';
import { api } from '../api';
import type { PerformancePayload } from './PaperPerformance.vue';

interface LiveInstance {
  instance_id: string;
  venue_id: string;
  symbol: string;
  interval: string;
  strategy_id: string;
  enabled: boolean;
  last_tick_at: string | null;
  last_signal: string | null;
  trade_count: number;
  last_risk_event: string | null;
  account?: { balances: Record<string, string> } | null;
}

interface Regime {
  regime: string;
  score: number;
  factor: number;
  blocks_new_positions: boolean;
  reason: string;
}

interface StrategyPerf {
  instance_id: string;
  label: string;
  strategy_id: string;
  rating: string;
  pnl: number;
  returnPct: number | null;
  winRate: number | null;
  maxDD: number | null;
  trades: number;
}

const instances = ref<LiveInstance[]>([]);
const perfs = ref<StrategyPerf[]>([]);
const portfolioCurve = ref<{ time: UTCTimestamp; value: number }[]>([]);
const regime = ref<Regime | null>(null);
const loading = ref(false);
const error = ref('');
const updatedAt = ref('');

const container = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let series: ISeriesApi<'Area'> | null = null;
let resizeObserver: ResizeObserver | null = null;
let autoTimer: number | null = null;

const regimeLabel = (r: string) =>
  ({ strong: '偏强', neutral: '中性', weak: '偏弱', crisis: '危机' }[r] || r);
const regimeColor = (r: string) =>
  ({ strong: '#1faa53', neutral: '#38c6f4', weak: '#ff9800', crisis: '#f0433a' }[r] || '#8a9492');

// ---- KPI ----
const totalEquity = computed(() => {
  let sum = 0;
  for (const inst of instances.value) {
    const usdt = inst.account?.balances?.['USDT'];
    if (usdt) sum += parseFloat(usdt) || 0;
  }
  return sum;
});
const totalPnl = computed(() => perfs.value.reduce((s, p) => s + p.pnl, 0));
const totalReturnPct = computed(() => {
  const base = totalEquity.value - totalPnl.value;
  if (base <= 0) return null;
  return (totalPnl.value / base) * 100;
});
const runningCount = computed(() => instances.value.filter((i) => i.enabled).length);
const totalTrades = computed(() => instances.value.reduce((s, i) => s + (i.trade_count || 0), 0));
const avgWinRate = computed(() => {
  const ws = perfs.value.map((p) => p.winRate).filter((v): v is number => v !== null);
  if (!ws.length) return null;
  return ws.reduce((s, v) => s + v, 0) / ws.length;
});
const worstDD = computed(() => {
  const dds = perfs.value.map((p) => p.maxDD).filter((v): v is number => v !== null);
  if (!dds.length) return null;
  return Math.min(...dds);
});

const ranked = computed(() => [...perfs.value].sort((a, b) => b.pnl - a.pnl));
const maxAbsPnl = computed(() => Math.max(1, ...ranked.value.map((p) => Math.abs(p.pnl))));

const alerts = computed(() => {
  const items: { level: 'warn' | 'info'; text: string }[] = [];
  if (regime.value?.blocks_new_positions) {
    items.push({ level: 'warn', text: `市场${regimeLabel(regime.value.regime)}：已禁止开新仓` });
  }
  for (const inst of instances.value) {
    if (inst.last_risk_event) {
      items.push({ level: 'warn', text: `${inst.strategy_id}: ${inst.last_risk_event}` });
    }
  }
  return items.slice(0, 5);
});

function fmtUsdt(v: number): string {
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtSigned(v: number | null, suffix = ''): string {
  if (v === null || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}${suffix}`;
}
function pnlClass(v: number | null): string {
  if (v === null) return '';
  return v > 0 ? 'pos' : v < 0 ? 'neg' : '';
}

function destroyChart() {
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }
  if (chart) { chart.remove(); chart = null; series = null; }
}

function renderChart() {
  if (!container.value) return;
  destroyChart();
  const data = portfolioCurve.value;
  if (!data.length) return;
  const width = container.value.clientWidth || 640;
  const first = data[0];
  const last = data[data.length - 1];
  const up = last && first ? last.value >= first.value : true;
  const lineColor = up ? '#1faa53' : '#f0433a';
  chart = createChart(container.value, {
    width,
    height: 280,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#8a9492',
      fontFamily: 'Inter, Noto Sans SC, Microsoft YaHei, sans-serif',
      attributionLogo: false,
    },
    grid: { vertLines: { color: 'rgba(60,75,72,.4)' }, horzLines: { color: 'rgba(60,75,72,.4)' } },
    rightPriceScale: { borderColor: '#3b4a48', minimumWidth: 76 },
    timeScale: { borderColor: '#3b4a48', timeVisible: true, secondsVisible: false },
  });
  series = chart.addSeries(AreaSeries, {
    lineColor,
    topColor: up ? 'rgba(31,170,83,.28)' : 'rgba(240,67,58,.28)',
    bottomColor: 'rgba(31,170,83,0)',
    lineWidth: 2,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  });
  series.setData(data);
  chart.timeScale().fitContent();
  resizeObserver = new ResizeObserver(() => {
    if (chart && container.value) chart.applyOptions({ width: container.value.clientWidth });
  });
  resizeObserver.observe(container.value);
}

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [liveRes, regimeRes] = await Promise.all([
      api.get<{ instances: LiveInstance[] }>('/paper/live'),
      api.get<Regime>('/market-regime').catch(() => ({ data: null })),
    ]);
    const list = liveRes.data.instances || [];
    instances.value = list;
    regime.value = (regimeRes as any).data || null;

    // 并行拉各实例绩效，单个失败不影响整体
    const perfResults = await Promise.all(
      list.map((inst) =>
        api
          .get<PerformancePayload>(`/paper/live/instances/${inst.instance_id}/performance`)
          .then((r) => ({ inst, payload: r.data }))
          .catch(() => null),
      ),
    );

    const stratPerfs: StrategyPerf[] = [];
    const buckets = new Map<number, number>();
    for (const pr of perfResults) {
      if (!pr) continue;
      const { inst, payload } = pr;
      const st = payload.stats;
      const pnl = Number(st.total_pnl) || 0;
      stratPerfs.push({
        instance_id: inst.instance_id,
        label: `${inst.venue_id} ${inst.symbol} ${inst.interval}`,
        strategy_id: inst.strategy_id,
        rating: '',
        pnl,
        returnPct: st.total_return_pct !== null ? Number(st.total_return_pct) : null,
        winRate: st.win_rate_pct !== null ? Number(st.win_rate_pct) : null,
        maxDD: st.max_drawdown_pct !== null ? Number(st.max_drawdown_pct) : null,
        trades: st.round_trips || 0,
      });
      for (const p of payload.equity_curve || []) {
        const t = Math.floor(new Date(p.ts).getTime() / 60000) * 60;
        if (!Number.isFinite(t)) continue;
        buckets.set(t, (buckets.get(t) || 0) + (Number(p.equity) || 0));
      }
    }
    perfs.value = stratPerfs;
    portfolioCurve.value = [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([t, v]) => ({ time: t as UTCTimestamp, value: Math.round(v * 100) / 100 }));

    updatedAt.value = new Date().toLocaleString('zh-CN', { hour12: false });
    await nextTick();
    renderChart();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '大屏数据读取失败';
  } finally {
    loading.value = false;
  }
};

onMounted(() => {
  load();
  autoTimer = window.setInterval(load, 60000);
});
onUnmounted(() => {
  if (autoTimer) window.clearInterval(autoTimer);
  destroyChart();
});
</script>

<template>
  <section class="dash" aria-label="数据大屏">
    <header class="dash-head">
      <div>
        <p class="kicker">DATA SCREEN</p>
        <h2>交易大屏</h2>
      </div>
      <div class="head-right">
        <span v-if="updatedAt" class="updated">更新于 {{ updatedAt }} · 每60秒自动刷新</span>
        <button class="refresh-btn" type="button" :disabled="loading" @click="load">
          <RefreshCw :size="14" :class="{ spinning: loading }" /> 刷新
        </button>
      </div>
    </header>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>

    <!-- KPI 大数字 -->
    <div class="kpis">
      <article class="kpi hero">
        <span class="kpi-label">模拟总权益</span>
        <strong class="kpi-num">{{ fmtUsdt(totalEquity) }} <small>USDT</small></strong>
        <em class="kpi-sub" :class="pnlClass(totalPnl)">累计 {{ fmtSigned(totalPnl) }} USDT</em>
      </article>
      <article class="kpi">
        <span class="kpi-label">累计收益率</span>
        <strong class="kpi-num" :class="pnlClass(totalReturnPct)">{{ fmtSigned(totalReturnPct, '%') }}</strong>
        <em class="kpi-sub">相对初始本金</em>
      </article>
      <article class="kpi">
        <span class="kpi-label">运行中策略</span>
        <strong class="kpi-num">{{ runningCount }}<small> / {{ instances.length }}</small></strong>
        <em class="kpi-sub">策略实例</em>
      </article>
      <article class="kpi">
        <span class="kpi-label">累计成交</span>
        <strong class="kpi-num">{{ totalTrades }}<small> 笔</small></strong>
        <em class="kpi-sub">全部实例</em>
      </article>
      <article class="kpi">
        <span class="kpi-label">平均胜率</span>
        <strong class="kpi-num">{{ avgWinRate === null ? '—' : avgWinRate.toFixed(1) + '%' }}</strong>
        <em class="kpi-sub">round-trip</em>
      </article>
      <article class="kpi">
        <span class="kpi-label">最大回撤</span>
        <strong class="kpi-num neg">{{ worstDD === null ? '—' : worstDD.toFixed(2) + '%' }}</strong>
        <em class="kpi-sub">最差实例</em>
      </article>
    </div>

    <!-- 主区：曲线 + 排行 -->
    <div class="main-grid">
      <div class="panel chart-panel">
        <h3>组合权益曲线 <em>全部实例加总</em></h3>
        <div v-if="!portfolioCurve.length && !loading" class="empty">暂无权益数据（等待实例 tick 积累快照）</div>
        <div ref="container" class="chart-box"></div>
      </div>
      <div class="panel">
        <h3>策略盈亏排行 <em>{{ ranked.length }} 个实例</em></h3>
        <div v-if="!ranked.length" class="empty">暂无策略实例</div>
        <div v-for="(p, idx) in ranked" :key="p.instance_id" class="rank-row">
          <span class="rank-idx">{{ idx + 1 }}</span>
          <div class="rank-main">
            <strong>{{ p.strategy_id }}</strong>
            <span>{{ p.label }}</span>
          </div>
          <div class="rank-bar"><i :class="p.pnl >= 0 ? 'pos' : 'neg'" :style="{ width: (Math.abs(p.pnl) / maxAbsPnl * 100).toFixed(1) + '%' }"></i></div>
          <strong class="rank-pnl" :class="pnlClass(p.pnl)">{{ fmtSigned(p.pnl) }}</strong>
        </div>
      </div>
    </div>

    <!-- 底区：市场状态 + 告警 -->
    <div class="sub-grid">
      <div class="panel">
        <h3>市场状态</h3>
        <div v-if="!regime" class="empty">暂无市场状态数据</div>
        <div v-else class="regime-box">
          <strong class="regime-name" :style="{ color: regimeColor(regime.regime) }">{{ regimeLabel(regime.regime) }}</strong>
          <div class="score-bar"><i :style="{ width: regime.score.toFixed(0) + '%', background: regimeColor(regime.regime) }"></i></div>
          <span class="regime-meta">{{ regime.score.toFixed(0) }}分 · 仓位系数 ×{{ regime.factor }}{{ regime.blocks_new_positions ? ' · 禁止开新仓' : '' }}</span>
          <p v-if="regime.reason" class="regime-reason">{{ regime.reason }}</p>
        </div>
      </div>
      <div class="panel">
        <h3><AlertTriangle :size="14" /> 风险告警 <em>{{ alerts.length }}</em></h3>
        <div v-if="!alerts.length" class="empty">一切正常，无告警</div>
        <div v-for="(a, idx) in alerts" :key="idx" class="alert-row" :class="a.level">
          <span>{{ a.text }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dash { display: grid; gap: 14px; }
.dash-head { display: flex; align-items: flex-end; justify-content: space-between; }
.kicker { margin: 0; font-size: 11px; letter-spacing: .2em; color: var(--cyan); }
.dash-head h2 { margin: 2px 0 0; font-size: 24px; }
.head-right { display: flex; align-items: center; gap: 12px; }
.updated { color: var(--dim); font-size: 11px; }
.inline-error { color: #f0433a; background: rgba(240,67,58,.08); border: 1px solid rgba(240,67,58,.3); border-radius: 6px; padding: 8px 12px; font-size: 13px; }

.kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }
.kpi { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); padding: 14px 14px 12px; display: grid; gap: 4px; align-content: start; }
.kpi.hero { border-color: rgba(56,198,244,.35); background: linear-gradient(135deg, rgba(56,198,244,.08), transparent); }
.kpi-label { color: var(--dim); font-size: 11px; }
.kpi-num { font-size: 22px; font-variant-numeric: tabular-nums; }
.kpi-num small { font-size: 11px; color: var(--dim); font-weight: 400; }
.kpi-sub { font-style: normal; color: var(--dim); font-size: 11px; }
.pos { color: #1faa53; } .neg { color: #f0433a; }

.main-grid { display: grid; grid-template-columns: 1.65fr 1fr; gap: 10px; }
.sub-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.panel { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); padding: 14px 16px; min-width: 0; }
.panel h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; font-size: 14px; }
.panel h3 em { font-style: normal; color: var(--dim); font-size: 11px; font-weight: 400; }
.chart-box { width: 100%; }
.empty { color: var(--dim); font-size: 13px; padding: 16px 0; }

.rank-row { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.rank-row:last-child { border-bottom: 0; }
.rank-idx { width: 20px; color: var(--dim); font-size: 12px; text-align: center; font-variant-numeric: tabular-nums; }
.rank-main { display: grid; gap: 1px; flex: 1; min-width: 0; }
.rank-main span { color: var(--dim); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rank-bar { flex: 1; height: 6px; background: rgba(120,135,132,.15); border-radius: 3px; overflow: hidden; min-width: 40px; }
.rank-bar i { display: block; height: 100%; border-radius: 3px; }
.rank-bar i.pos { background: #1faa53; } .rank-bar i.neg { background: #f0433a; }
.rank-pnl { font-variant-numeric: tabular-nums; min-width: 80px; text-align: right; }

.regime-box { display: grid; gap: 8px; }
.regime-name { font-size: 26px; }
.score-bar { height: 8px; background: rgba(120,135,132,.15); border-radius: 4px; overflow: hidden; }
.score-bar i { display: block; height: 100%; border-radius: 4px; transition: width .4s; }
.regime-meta { color: var(--dim); font-size: 12px; }
.regime-reason { margin: 0; color: var(--muted); font-size: 12px; }
.alert-row { padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; font-size: 13px; }
.alert-row.warn { background: rgba(240,67,58,.08); border: 1px solid rgba(240,67,58,.3); }
.alert-row.info { background: rgba(33,150,243,.08); border: 1px solid rgba(33,150,243,.3); }

.refresh-btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1100px) { .kpis { grid-template-columns: repeat(3, 1fr); } .main-grid { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .kpis { grid-template-columns: 1fr 1fr; } .sub-grid { grid-template-columns: 1fr; } }
</style>
