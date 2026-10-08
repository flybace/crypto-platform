<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import {
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { X, RefreshCw, TrendingUp, TrendingDown } from 'lucide-vue-next';
import { api } from '../api';

export type PerformanceStats = {
  initial_equity: string | null;
  latest_equity: string | null;
  total_return_pct: string | null;
  max_drawdown_pct: string | null;
  round_trips: number;
  win_rate_pct: string | null;
  profit_factor: string | null;
  total_pnl: string;
  period_start: string | null;
  period_end: string | null;
};

export type PerformancePayload = {
  instance_id: string;
  equity_curve: { ts: string; equity: string }[];
  stats: PerformanceStats;
  trades: Record<string, any>[];
  snapshot_count: number;
};

const props = defineProps<{
  instanceId: string;
  title: string;
}>();
const emit = defineEmits<{ close: [] }>();

const loading = ref(true);
const error = ref('');
const payload = ref<PerformancePayload | null>(null);

const container = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let series: ISeriesApi<'Line'> | null = null;
let resizeObserver: ResizeObserver | null = null;

const stats = computed(() => payload.value?.stats || null);

function fmtPct(v: string | null | undefined): string {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}
function fmtUsdt(v: string | null | undefined): string {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}`;
}
function statClass(v: string | null | undefined): string {
  if (v === null || v === undefined) return '';
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
}
function fmtTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return ts;
  }
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await api.get<PerformancePayload>(
      `/paper/live/instances/${props.instanceId}/performance`,
    );
    payload.value = data;
    await nextTick();
    drawChart();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '绩效数据加载失败';
  } finally {
    loading.value = false;
  }
}

function drawChart() {
  const curve = payload.value?.equity_curve || [];
  if (!container.value) return;
  destroyChart();
  if (!curve.length) return;
  const width = container.value.clientWidth || 640;
  chart = createChart(container.value, {
    width,
    height: 260,
    layout: {
      background: { type: ColorType.Solid, color: '#12191b' },
      textColor: '#aab5b2',
      fontFamily: 'Inter, Noto Sans SC, Microsoft YaHei, sans-serif',
      attributionLogo: false,
    },
    grid: { vertLines: { color: '#273231' }, horzLines: { color: '#273231' } },
    rightPriceScale: { borderColor: '#3b4a48', minimumWidth: 72 },
    timeScale: { borderColor: '#3b4a48', timeVisible: true, secondsVisible: false },
  });
  series = chart.addSeries(LineSeries, {
    color: '#69d3bd',
    lineWidth: 2,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  });
  const first = Number(curve[0]?.equity) || 0;
  series.setData(
    curve.map((p) => ({
      time: Math.floor(new Date(p.ts).getTime() / 1000) as UTCTimestamp,
      value: Number(p.equity),
    })),
  );
  // 零线参考：初始权益
  series.createPriceLine({
    price: first,
    color: '#3b4a48',
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
    title: '初始',
  });
  chart.timeScale().fitContent();
  resizeObserver = new ResizeObserver(() => {
    if (chart && container.value) chart.applyOptions({ width: container.value.clientWidth });
  });
  resizeObserver.observe(container.value);
}

function destroyChart() {
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (chart) {
    chart.remove();
    chart = null;
    series = null;
  }
}

watch(() => props.instanceId, load);
onMounted(load);
onUnmounted(destroyChart);
</script>

<template>
  <div class="perf-overlay" @click.self="emit('close')">
    <div class="perf-dialog" role="dialog" aria-label="实例绩效">
      <header class="perf-head">
        <div>
          <p class="kicker">PERFORMANCE</p>
          <h3>{{ title }} · 绩效</h3>
        </div>
        <div class="perf-head-actions">
          <button class="icon-button" type="button" title="刷新" :disabled="loading" @click="load">
            <RefreshCw :size="16" :class="{ spinning: loading }" />
          </button>
          <button class="icon-button" type="button" title="关闭" @click="emit('close')">
            <X :size="16" />
          </button>
        </div>
      </header>

      <div v-if="loading" class="empty-state"><RefreshCw :size="18" class="spinning" /><p>正在加载绩效</p></div>
      <div v-else-if="error" class="inline-notice error">{{ error }}</div>
      <template v-else-if="payload">
        <div v-if="!payload.equity_curve.length" class="empty-state">
          <p>暂无权益快照——实例启动并运行一段时间后自动记录（每分钟 1 个点）</p>
        </div>
        <template v-else>
          <div class="perf-stats">
            <article>
              <span>累计收益</span>
              <strong :class="statClass(stats?.total_return_pct)">{{ fmtPct(stats?.total_return_pct) }}</strong>
            </article>
            <article>
              <span>最大回撤</span>
              <strong class="neg">-{{ fmtPct(stats?.max_drawdown_pct).replace('-', '').replace('+', '') }}</strong>
            </article>
            <article>
              <span>胜率</span>
              <strong>{{ stats?.win_rate_pct == null ? '—' : fmtPct(stats?.win_rate_pct) }}</strong>
            </article>
            <article>
              <span>盈亏比</span>
              <strong>{{ stats?.profit_factor ?? '—' }}</strong>
            </article>
            <article>
              <span>总 PnL</span>
              <strong :class="statClass(stats?.total_pnl)">{{ fmtUsdt(stats?.total_pnl) }} <em>USDT</em></strong>
            </article>
            <article>
              <span>完整交易轮次</span>
              <strong>{{ stats?.round_trips ?? 0 }}</strong>
            </article>
          </div>

          <div class="perf-chart-wrap">
            <div class="perf-chart-head">
              <span><TrendingUp :size="13" /> 权益曲线</span>
              <small>{{ payload.snapshot_count }} 个快照 · {{ fmtTime(stats?.period_start) }} → {{ fmtTime(stats?.period_end) }}</small>
            </div>
            <div ref="container" class="perf-chart"></div>
          </div>

          <div class="perf-trades">
            <div class="perf-chart-head">
              <span><TrendingDown :size="13" /> 成交记录（带 round-trip PnL）</span>
            </div>
            <table v-if="payload.trades.length">
              <thead><tr><th>时间</th><th>方向</th><th>数量</th><th>成交价</th><th>本轮 PnL</th></tr></thead>
              <tbody>
                <tr v-for="t in payload.trades.slice().reverse()" :key="t.order_id || t.created_at">
                  <td>{{ fmtTime(t.created_at) }}</td>
                  <td :class="t.side === 'BUY' ? 'pos' : 'neg'">{{ t.side }}</td>
                  <td>{{ t.quantity }}</td>
                  <td>{{ t.filled_price || '—' }}</td>
                  <td :class="statClass(t.round_trip_pnl)">{{ t.round_trip_pnl != null ? fmtUsdt(t.round_trip_pnl) : '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="muted">暂无成交</p>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.perf-overlay {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(6, 10, 10, 0.72);
  display: flex; align-items: flex-start; justify-content: center;
  padding: 48px 16px; overflow-y: auto;
}
.perf-dialog {
  width: min(880px, 100%);
  background: #0e1413; border: 1px solid #273231; border-radius: 12px;
  padding: 20px 22px; color: #dfe7e5;
}
.perf-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.perf-head h3 { margin: 2px 0 0; font-size: 18px; }
.perf-head-actions { display: flex; gap: 6px; }
.kicker { font-size: 11px; letter-spacing: 0.14em; color: #7d8b88; margin: 0; }
.perf-stats {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 8px; margin: 12px 0;
}
.perf-stats article {
  background: #12191b; border: 1px solid #273231; border-radius: 8px;
  padding: 10px 12px; display: flex; flex-direction: column; gap: 4px;
}
.perf-stats span { font-size: 11px; color: #7d8b88; }
.perf-stats strong { font-size: 17px; }
.perf-stats strong em { font-style: normal; font-size: 11px; color: #7d8b88; }
.pos { color: #69d3bd; } .neg { color: #e56c6c; }
.perf-chart-wrap { margin: 12px 0; }
.perf-chart-head {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 6px; font-size: 13px; color: #aab5b2;
}
.perf-chart-head small { font-size: 11px; color: #7d8b88; }
.perf-chart { width: 100%; }
.perf-trades table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.perf-trades th, .perf-trades td { padding: 7px 8px; border-bottom: 1px solid #1d2624; text-align: left; }
.perf-trades th { color: #7d8b88; font-weight: 500; }
.empty-state { text-align: center; padding: 28px; color: #7d8b88; }
.inline-notice.error { color: #e56c6c; padding: 10px; }
.muted { color: #7d8b88; font-size: 12.5px; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.icon-button {
  background: transparent; border: 1px solid #273231; border-radius: 8px;
  color: #aab5b2; padding: 6px; cursor: pointer; display: inline-flex;
}
.icon-button:hover { border-color: #3b4a48; color: #dfe7e5; }
.icon-button:disabled { opacity: 0.5; cursor: default; }
</style>
