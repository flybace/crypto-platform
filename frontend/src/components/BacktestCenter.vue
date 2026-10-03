<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { BarChart3, Check, CircleAlert, Play, RefreshCw, ShieldCheck, Trash2 } from 'lucide-vue-next';
import { api } from '../api';
import type { BacktestRun, BacktestSummary, HistoryCoverage, HistoryDataset, QueuedTaskResponse, StrategyDefinition } from '../types';
import { isQueuedTask, resolveTaskResponse, taskStatusLabel } from '../services/taskPolling';
import ParameterTuningPanel from './ParameterTuningPanel.vue';

const datasets = ref<HistoryDataset[]>([]);
const strategies = ref<StrategyDefinition[]>([]);
const runs = ref<BacktestRun[]>([]);
const summary = ref<BacktestSummary | null>(null);
const selectedDatasetId = ref('');
const selectedStrategy = ref('sma_cross');
const initialQuote = ref('10000');
const initialBase = ref('0');
const feeBps = ref('10');
const slippageBps = ref('5');
const fastWindow = ref('10');
const slowWindow = ref('30');
const strategyParameters = ref<Record<string, string | number>>({});
const startAt = ref('');
const endAt = ref('');
const currentRun = ref<BacktestRun | null>(null);
const loading = ref(false);
const running = ref(false);
const error = ref('');
const taskMessage = ref('');

const selectedDataset = computed(() => datasets.value.find((dataset) => dataset.dataset_id === selectedDatasetId.value) || null);
const selectedStrategyDefinition = computed(() => strategies.value.find((item) => item.strategy_id === selectedStrategy.value) || null);
const parameterFields = computed(() => {
  const fields = selectedStrategyDefinition.value?.parameter_schema || [];
  return fields
    .map((field) => ({
      key: String(field.key || ''),
      label: String(field.label || field.key || '策略参数'),
      type: String(field.type || 'number'),
      default: field.default as string | number | undefined,
      min: field.min as string | number | undefined,
      max: field.max as string | number | undefined,
    }))
    .filter((field) => field.key && !['fast_window', 'slow_window'].includes(field.key));
});
const curveBars = computed(() => {
  const curve = currentRun.value?.equity_curve || [];
  const values = curve.map((point) => Number(point.equity));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return curve.map((point) => ({ ...point, height: `${Math.max(12, ((Number(point.equity) - min) / span) * 88 + 12)}%` }));
});

const loadData = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [coverageResponse, strategyResponse, runResponse, summaryResponse] = await Promise.all([
      api.get<HistoryCoverage>('/history/coverage'),
      api.get<{ items: StrategyDefinition[] }>('/backtests/strategies'),
      api.get<{ items: BacktestRun[] }>('/backtests/runs'),
      api.get<BacktestSummary>('/backtests/summary'),
    ]);
    datasets.value = coverageResponse.data.datasets.filter((dataset) => dataset.gap_count === 0 && dataset.duplicate_count === 0);
    strategies.value = strategyResponse.data.items;
    runs.value = runResponse.data.items;
    summary.value = summaryResponse.data;
    initializeStrategyParameters();
    if (!selectedDatasetId.value || !datasets.value.some((dataset) => dataset.dataset_id === selectedDatasetId.value)) {
      selectedDatasetId.value = datasets.value.find((dataset) => dataset.interval === '1d')?.dataset_id || datasets.value[0]?.dataset_id || '';
    }
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '回测配置读取失败';
  } finally {
    loading.value = false;
  }
};

const deleteRun = async (runId: string) => {
  error.value = '';
  try {
    await api.delete(`/backtests/runs/${encodeURIComponent(runId)}`);
    runs.value = runs.value.filter((item) => item.run_id !== runId);
    if (currentRun.value?.run_id === runId) currentRun.value = null;
    summary.value = (await api.get<BacktestSummary>('/backtests/summary')).data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '回测记录删除失败';
  }
};

const clearRuns = async () => {
  if (!runs.value.length || !window.confirm('确定清空本地回测记录？此操作不可恢复。')) return;
  error.value = '';
  try {
    await api.delete('/backtests/runs');
    runs.value = [];
    currentRun.value = null;
    summary.value = (await api.get<BacktestSummary>('/backtests/summary')).data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '回测记录清空失败';
  }
};

const initializeStrategyParameters = () => {
  strategyParameters.value = Object.fromEntries(
    parameterFields.value.map((field) => [field.key, field.default ?? '']),
  );
};

watch(selectedDatasetId, () => {
  currentRun.value = null;
});

watch(selectedStrategy, () => {
  currentRun.value = null;
  initializeStrategyParameters();
});

const runBacktest = async () => {
  if (!selectedDataset.value) {
    error.value = '请先选择质量通过的数据集';
    return;
  }
  running.value = true;
  error.value = '';
  taskMessage.value = '';
  const payload: Record<string, any> = {
    venue_id: selectedDataset.value.venue_id,
    symbol: selectedDataset.value.instrument_key.split(':').pop() || selectedDataset.value.native_symbol,
    interval: selectedDataset.value.interval,
    strategy_id: selectedStrategy.value,
    initial_quote: initialQuote.value,
    initial_base: initialBase.value,
    fee_bps: feeBps.value,
    slippage_bps: slippageBps.value,
    fast_window: Number(fastWindow.value),
    slow_window: Number(slowWindow.value),
    strategy_parameters: { ...strategyParameters.value },
  };
  if (startAt.value) payload.start_at = `${startAt.value}T00:00:00Z`;
  if (endAt.value) payload.end_at = `${endAt.value}T00:00:00Z`;
  try {
    const response = await api.post<BacktestRun | QueuedTaskResponse>('/backtests/run', payload);
    if (isQueuedTask(response.data)) {
      taskMessage.value = `回测已排队 · ${response.data.task_id}`;
      const queuedRunId = response.data.run_id || '';
      const detail = await resolveTaskResponse<BacktestRun>(response.data, {
        onUpdate: (task) => {
          const progress = task.progress.completed !== undefined
            ? ` · ${task.progress.completed}/${task.progress.total}`
            : '';
          taskMessage.value = `回测${taskStatusLabel(task.status)}${progress}`;
        },
      });
      await loadData();
      currentRun.value = runs.value.find((item) => item.run_id === queuedRunId) || detail;
      taskMessage.value = '回测已完成，结果已重新载入';
    } else {
      currentRun.value = response.data;
      runs.value = [response.data, ...runs.value.filter((item) => item.run_id !== response.data.run_id)].slice(0, 30);
    }
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '回测执行失败';
  } finally {
    running.value = false;
  }
};

const strategyName = (strategyId: string) => strategies.value.find((item) => item.strategy_id === strategyId)?.name || strategyId;
const datasetLabel = (dataset: HistoryDataset) => `${dataset.venue_id.toUpperCase()} · ${dataset.instrument_key.split(':').pop() || dataset.native_symbol} · ${dataset.interval} · ${dataset.row_count} 行`;
const formatNumber = (value: string | number) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(Number(value));
const formatPct = (value: string) => `${Number(value) >= 0 ? '+' : ''}${formatNumber(value)}%`;
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });

onMounted(loadData);
</script>

<template>
  <section class="backtest-center" aria-labelledby="backtest-title">
    <section class="page-heading backtest-heading">
      <div><p class="kicker">RESEARCH ENGINE / 05</p><h1 id="backtest-title">回测中心</h1><p class="muted">使用已校验历史数据，按下一根开盘成交规则复现策略表现。</p></div>
      <div class="backtest-heading-status"><span class="heading-stamp"><ShieldCheck :size="14" /> 仅离线研究</span><button class="icon-button" type="button" title="刷新回测配置" aria-label="刷新回测配置" :disabled="loading" @click="loadData"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></div>
    </section>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="taskMessage" class="inline-notice" role="status" aria-live="polite">{{ taskMessage }}</div>
    <section class="backtest-metrics" aria-label="回测摘要"><article><span>运行次数</span><strong>{{ summary?.completed_count || 0 }}</strong><em>已完成</em></article><article><span>平均收益</span><strong :class="Number(summary?.average_return_pct || 0) >= 0 ? 'positive' : 'negative'">{{ summary ? formatPct(summary.average_return_pct) : '—' }}</strong><em>全部完成记录</em></article><article><span>累计交易</span><strong>{{ summary?.trade_count || 0 }}</strong><em>策略成交次数</em></article><article><span>最佳收益</span><strong class="positive">{{ summary?.best_run ? formatPct(summary.best_run.total_return_pct) : '—' }}</strong><em>{{ summary?.best_run ? strategyName(summary.best_run.strategy_id) : '等待回测' }}</em></article></section>
    <section class="backtest-grid">
      <section class="backtest-panel config-panel" aria-labelledby="config-title">
        <div class="section-heading"><div><p class="kicker">RUN CONFIGURATION</p><h2 id="config-title">回测配置</h2></div><BarChart3 :size="18" class="section-icon" /></div>
        <form class="backtest-form" @submit.prevent="runBacktest">
          <label class="backtest-field"><span>数据集</span><select v-model="selectedDatasetId"><option v-for="dataset in datasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ datasetLabel(dataset) }}</option></select></label>
          <div v-if="selectedDataset" class="dataset-gate"><Check :size="14" /><span>质量通过 · {{ selectedDataset.row_count }} 根 · {{ selectedDataset.start_at.slice(0, 10) }} 至 {{ selectedDataset.end_at.slice(0, 10) }}</span></div>
          <label class="backtest-field"><span>策略</span><select v-model="selectedStrategy"><option v-for="strategy in strategies" :key="strategy.strategy_id" :value="strategy.strategy_id">{{ strategy.name }}</option></select><small>{{ selectedStrategyDefinition?.description || '选择一个已注册策略' }}</small></label>
          <div class="backtest-field-grid"><label class="backtest-field"><span>初始 USDT</span><input v-model="initialQuote" type="number" min="1" step="100" /></label><label class="backtest-field"><span>初始币数量</span><input v-model="initialBase" type="number" min="0" step="0.001" /></label></div>
          <div class="backtest-field-grid"><label class="backtest-field"><span>费率（bps）</span><input v-model="feeBps" type="number" min="0" step="1" /></label><label class="backtest-field"><span>滑点（bps）</span><input v-model="slippageBps" type="number" min="0" step="1" /></label></div>
          <div class="backtest-field-grid"><label class="backtest-field"><span>快窗口</span><input v-model="fastWindow" type="number" min="2" step="1" /></label><label class="backtest-field"><span>慢窗口</span><input v-model="slowWindow" type="number" min="3" step="1" /></label></div>
          <div v-if="parameterFields.length" class="parameter-section"><div class="parameter-heading"><span>策略专属参数</span><small>{{ parameterFields.length }} FIELDS</small></div><div class="backtest-field-grid"><label v-for="field in parameterFields" :key="field.key" class="backtest-field"><span>{{ field.label }}</span><input v-model="strategyParameters[field.key]" :type="field.type === 'integer' ? 'number' : 'number'" :min="field.min" :max="field.max" :step="field.type === 'integer' ? 1 : 0.01" /></label></div></div>
          <div class="backtest-field-grid"><label class="backtest-field"><span>开始日（可选）</span><input v-model="startAt" type="date" /></label><label class="backtest-field"><span>结束日（可选）</span><input v-model="endAt" type="date" /></label></div>
          <button class="backtest-submit" type="button" :disabled="running || !selectedDataset" @click="runBacktest"><Play v-if="!running" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ running ? '运行中' : '运行回测' }}</span></button>
        </form>
        <p class="backtest-note"><CircleAlert :size="13" /> 回测仅使用已归档 K 线；结果不等于盘口套利、实盘收益或未来表现。</p>
      </section>

      <section class="backtest-panel result-panel" aria-labelledby="result-title">
        <div class="section-heading"><div><p class="kicker">LATEST RESULT</p><h2 id="result-title">最新结果</h2></div><span class="section-meta">{{ currentRun ? strategyName(currentRun.strategy_id) : '等待运行' }}</span></div>
        <div v-if="!currentRun" class="backtest-empty"><BarChart3 :size="25" /><p>运行一次回测查看净值和交易记录</p><small>当前数据集已从历史数据中心接入。</small></div>
        <template v-else>
          <div class="result-summary"><article><span>总收益</span><strong :class="Number(currentRun.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(currentRun.total_return_pct) }}</strong><em>{{ formatNumber(currentRun.initial_equity) }} → {{ formatNumber(currentRun.final_equity) }}</em></article><article><span>最大回撤</span><strong class="negative">-{{ formatPct(currentRun.max_drawdown_pct).replace('+', '') }}</strong><em>{{ formatNumber(currentRun.max_drawdown_quote) }} USDT</em></article><article><span>交易次数</span><strong>{{ currentRun.orders }}</strong><em>{{ currentRun.round_trips }} 个完整轮次</em></article><article><span>手续费</span><strong>{{ formatNumber(currentRun.fees_quote) }}</strong><em>USDT</em></article></div>
          <div class="equity-block"><div class="result-subhead"><strong>净值曲线</strong><span>{{ currentRun.candle_count }} 根 K 线 · {{ formatTime(currentRun.start_at) }} 至 {{ formatTime(currentRun.end_at) }}</span></div><div class="equity-bars" aria-label="净值曲线"><span v-for="point in curveBars" :key="point.timestamp" class="equity-bar" :style="{ height: point.height }" :title="`${formatTime(point.timestamp)} · ${formatNumber(point.equity)} USDT`" /></div></div>
          <div class="trade-block"><div class="result-subhead"><strong>成交记录</strong><span>{{ currentRun.trade_log.length }} 条</span></div><div class="trade-list"><div v-for="trade in currentRun.trade_log.slice(-8).reverse()" :key="`${trade.timestamp}-${trade.side}`" class="trade-row"><span :class="trade.side === 'BUY' ? 'positive' : 'negative'">{{ trade.side === 'BUY' ? '买入' : '卖出' }}</span><span>{{ formatTime(trade.timestamp) }}</span><strong>{{ formatNumber(trade.price) }}</strong><span>{{ formatNumber(trade.quantity) }}</span><small>{{ trade.reason }}</small></div></div></div>
        </template>
      </section>
    </section>

    <section class="backtest-panel run-history-panel" aria-labelledby="run-history-title"><div class="section-heading"><div><p class="kicker">RUN ARCHIVE</p><h2 id="run-history-title">回测记录</h2></div><div class="run-history-actions"><span class="section-meta">{{ runs.length }} RUNS</span><button class="icon-button" type="button" title="清空回测记录" aria-label="清空回测记录" :disabled="!runs.length" @click="clearRuns"><Trash2 :size="15" /></button></div></div><div v-if="!runs.length" class="backtest-empty compact"><BarChart3 :size="20" /><p>还没有回测记录</p></div><div v-else class="run-list"><article v-for="run in runs" :key="run.run_id" class="run-row" @click="currentRun = run"><div class="run-mark"><Check :size="14" /></div><div><strong>{{ strategyName(run.strategy_id) }}</strong><span>{{ run.dataset.venue_id.toUpperCase() }} · {{ run.dataset.instrument_key.split(':').pop() }} · {{ formatTime(run.created_at) }}</span></div><b :class="Number(run.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(run.total_return_pct) }}</b><span class="state-pill connected">已完成</span><button class="icon-button run-delete" type="button" title="删除这条回测记录" aria-label="删除这条回测记录" @click.stop="deleteRun(run.run_id)"><Trash2 :size="14" /></button></article></div></section>
      <ParameterTuningPanel :strategies="strategies" venue-id="binance" symbol="BTC/USDT" interval="1d" />
  </section>
</template>

<style scoped>
.backtest-center { display: grid; gap: 30px; }
.backtest-heading { margin-bottom: 0; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; line-height: 1.5; }
.backtest-heading-status { display: flex; align-items: center; gap: 12px; }
.backtest-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.backtest-metrics article { display: grid; gap: 7px; min-height: 104px; padding: 17px 19px; border-right: 1px solid var(--line); }
.backtest-metrics article:last-child { border-right: 0; }
.backtest-metrics span, .backtest-metrics em { color: var(--dim); font-size: 10px; font-style: normal; }
.backtest-metrics strong { align-self: center; color: var(--ink); font-size: 21px; font-weight: 570; }
.backtest-grid { display: grid; grid-template-columns: minmax(290px, .7fr) minmax(0, 1.3fr); gap: 14px; align-items: start; }
.backtest-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 27px 22px 22px; background: var(--panel); }
.backtest-panel .section-heading { margin-bottom: 22px; }
.backtest-form { display: grid; gap: 15px; }
.parameter-section { display: grid; gap: 10px; border-top: 1px solid var(--line); padding-top: 14px; }
.parameter-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; color: var(--muted); font-size: 11px; }
.parameter-heading small { color: var(--dim); font-size: 9px; letter-spacing: .08em; }
.backtest-field { display: grid; gap: 7px; min-width: 0; color: var(--muted); font-size: 11px; }
.backtest-field input, .backtest-field select { width: 100%; min-height: 38px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; color: var(--ink); background: #13191b; outline: none; }
.backtest-field input:focus, .backtest-field select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.backtest-field small { color: var(--dim); font-size: 10px; line-height: 1.5; }
.backtest-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.dataset-gate { display: flex; align-items: center; gap: 7px; border-left: 2px solid var(--cyan); padding: 7px 9px; color: var(--cyan); background: rgba(108, 229, 208, .06); font-size: 10px; line-height: 1.4; }
.backtest-submit { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 41px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 12px; font-weight: 720; }
.backtest-submit:hover:not(:disabled) { background: #94f0df; }
.backtest-submit:disabled { cursor: not-allowed; opacity: .55; }
.backtest-note { display: flex; align-items: start; gap: 7px; margin: 17px 0 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.backtest-note svg { flex: 0 0 auto; color: var(--amber); }
.backtest-empty { display: grid; justify-items: center; gap: 9px; min-height: 370px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.backtest-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.backtest-empty small { color: var(--dim); font-size: 10px; }
.backtest-empty.compact { min-height: 90px; }
.result-summary { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.result-summary article { display: grid; gap: 7px; min-width: 0; min-height: 104px; padding: 14px 13px; border-right: 1px solid var(--line); }
.result-summary article:last-child { border-right: 0; }
.result-summary span, .result-summary em { color: var(--dim); font-size: 10px; font-style: normal; }
.result-summary strong { align-self: center; color: var(--ink); font-size: 20px; font-weight: 570; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.equity-block, .trade-block { margin-top: 23px; }
.result-subhead { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 11px; }
.result-subhead strong { color: var(--ink); font-size: 12px; font-weight: 560; }
.result-subhead span { color: var(--dim); font-size: 9px; }
.equity-bars { display: flex; align-items: end; gap: 2px; height: 145px; border-bottom: 1px solid var(--line-bright); padding: 12px 4px 0; background: repeating-linear-gradient(to bottom, transparent 0, transparent 35px, rgba(59, 74, 72, .28) 36px); }
.equity-bar { flex: 1 1 0; min-width: 2px; max-width: 10px; border-top: 2px solid var(--cyan); background: rgba(108, 229, 208, .23); }
.trade-list { display: grid; border-top: 1px solid var(--line); }
.trade-row { display: grid; grid-template-columns: 42px 1.4fr 1fr 1fr 1.1fr; gap: 8px; align-items: center; min-height: 36px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 9px; }
.trade-row strong { color: var(--ink); font-weight: 550; text-align: right; }
.trade-row small { overflow: hidden; color: var(--dim); text-overflow: ellipsis; white-space: nowrap; }
.run-history-panel { padding-bottom: 18px; }
.run-history-actions { display: flex; align-items: center; gap: 10px; }
.run-list { display: grid; border-top: 1px solid var(--line); }
.run-row { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto auto auto; align-items: center; gap: 12px; min-height: 60px; border-bottom: 1px solid var(--line); cursor: pointer; }
.run-row:hover { background: var(--panel-soft); }
.run-mark { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid rgba(108, 229, 208, .5); color: var(--cyan); }
.run-row div:nth-child(2) { display: grid; gap: 5px; min-width: 0; }
.run-row strong { color: var(--ink); font-size: 11px; font-weight: 550; }
.run-row span:not(.state-pill) { overflow: hidden; color: var(--dim); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.run-row b { font-size: 11px; font-weight: 560; }
.run-delete { width: 28px; height: 28px; color: var(--dim); }
.run-delete:hover { color: var(--red); border-color: var(--red); }
@media (max-width: 1050px) { .backtest-grid { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .backtest-heading-status .heading-stamp { display: none; } .backtest-metrics { grid-template-columns: 1fr 1fr; } .backtest-metrics article:nth-child(2) { border-right: 0; } .backtest-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } .backtest-field-grid, .result-summary { grid-template-columns: 1fr 1fr; } .result-summary article:nth-child(2) { border-right: 0; } .result-summary article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } .trade-row { grid-template-columns: 42px 1.3fr 1fr 1fr; } .trade-row small { display: none; } .run-row { grid-template-columns: 28px minmax(0, 1fr) auto auto; } .run-row .state-pill { grid-column: 2 / -1; justify-self: start; } .run-delete { grid-column: 4; grid-row: 1; } }
</style>
