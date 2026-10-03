<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { BarChart3, Check, FlaskConical, Grid2X2, ListFilter, RefreshCw, Search, SlidersHorizontal, Sparkles } from 'lucide-vue-next';
import { api } from '../api';
import type { CoinPool, HistoryCoverage, HistoryDataset, QueuedTaskResponse, StrategyDefinition } from '../types';
import { isQueuedTask, resolveTaskResponse, taskStatusLabel } from '../services/taskPolling';

type ResearchMode = 'compare' | 'portfolio' | 'tune' | 'screen';

const mode = ref<ResearchMode>('compare');
const datasets = ref<HistoryDataset[]>([]);
const pools = ref<CoinPool[]>([]);
const screeningRuns = ref<any[]>([]);
const strategies = ref<StrategyDefinition[]>([]);
const runs = ref<any[]>([]);
const datasetId = ref('');
const poolId = ref('');
const screeningRunId = ref('');
const strategyId = ref('sma_cross');
const feeBps = ref('10');
const slippageBps = ref('5');
const loading = ref(false);
const running = ref(false);
const error = ref('');
const taskMessage = ref('');
const result = ref<any>(null);
const focusNotice = ref('');
const focusedPoolId = ref('');
const focusedInterval = ref('');

const researchModeByKind: Record<string, ResearchMode> = {
  strategy_compare: 'compare',
  portfolio: 'portfolio',
  parameter_tune: 'tune',
  strategy_screen: 'screen',
};

const selectedDataset = computed(() => datasets.value.find((item) => item.dataset_id === datasetId.value) || null);
const selectedPool = computed(() => pools.value.find((item) => item.pool_id === poolId.value) || null);
const strategy = computed(() => strategies.value.find((item) => item.strategy_id === strategyId.value) || null);
const portfolioInterval = computed(() => {
  if (selectedPool.value?.interval) return selectedPool.value.interval;
  if (focusedPoolId.value === poolId.value && ['1d', '1h', '5m'].includes(focusedInterval.value)) return focusedInterval.value;
  return '1d';
});
const modeLabel = computed(() => ({ compare: '策略比较', portfolio: '组合回测', tune: '参数搜索', screen: '信号筛选' }[mode.value]));
const modeDescription = computed(() => ({
  compare: '同一数据集、同一成本模型下比较策略。',
  portfolio: '按币池分配资金，汇总多标的净值和交易指标。',
  tune: '用训练区间搜索参数，再用验证区间排序。',
  screen: '读取币池各标的最新 K 线，筛出当前策略信号。',
}[mode.value]));

const tuneGrid = computed(() => {
  const grids: Record<string, Record<string, unknown[]>> = {
    sma_cross: { fast_window: [5, 10, 15], slow_window: [30, 45, 60] },
    momentum: { lookback: [10, 20, 30], threshold: [0, 0.02, 0.05] },
    trend_breakout: { window: [10, 20, 30], min_return: [0, 0.02, 0.05] },
    rsi_rebound: { period: [7, 14, 21], max_rsi: [30, 35, 40] },
    bollinger_breakout: { window: [15, 20, 30], std_multiplier: [1.5, 2, 2.5] },
    macd_reversal: { fast: [8, 12], slow: [24, 26], signal: [7, 9] },
    volume_momentum: { window: [10, 20, 30], min_return: [0, 0.01, 0.02] },
    volatility_breakout: { window: [10, 20, 30], atr_multiplier: [1, 1.5, 2] },
  };
  return grids[strategyId.value] || { window: [10, 20, 30] };
});

const loadData = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [coverageResponse, poolResponse, strategyResponse, runResponse, screeningResponse] = await Promise.all([
      api.get<HistoryCoverage>('/history/coverage'),
      api.get<{ items: CoinPool[] }>('/pools'),
      api.get<{ items: StrategyDefinition[] }>('/strategies/catalog'),
      api.get<{ items: any[] }>('/research/runs'),
      api.get<{ items: any[] }>('/screening/runs'),
    ]);
    datasets.value = coverageResponse.data.datasets.filter((item) => item.gap_count === 0 && item.duplicate_count === 0);
    pools.value = poolResponse.data.items;
    strategies.value = strategyResponse.data.items;
    runs.value = runResponse.data.items;
    screeningRuns.value = screeningResponse.data.items;
    if (!datasetId.value || !datasets.value.some((item) => item.dataset_id === datasetId.value)) datasetId.value = datasets.value.find((item) => item.interval === '1d')?.dataset_id || datasets.value[0]?.dataset_id || '';
    if (!poolId.value || !pools.value.some((item) => item.pool_id === poolId.value)) poolId.value = pools.value.find((item) => item.kind === 'backtest')?.pool_id || pools.value[0]?.pool_id || '';
    if (screeningRunId.value && !screeningRuns.value.some((item) => item.run_id === screeningRunId.value)) screeningRunId.value = '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '研究配置读取失败';
  } finally {
    loading.value = false;
  }
};

const runResearch = async () => {
  running.value = true;
  error.value = '';
  taskMessage.value = '';
  result.value = null;
  try {
    if (mode.value === 'compare' || mode.value === 'tune') {
      if (!selectedDataset.value) throw new Error('请先选择已验证数据集');
    } else if (!poolId.value) {
      throw new Error('请先选择币池');
    }
    const base = { fee_bps: feeBps.value, slippage_bps: slippageBps.value, initial_quote: '10000', initial_base: '0' };
    const resolveResult = async (value: any) => {
      if (!isQueuedTask(value)) return value;
      taskMessage.value = `研究任务已排队 · ${value.task_id}`;
      const detail = await resolveTaskResponse<any>(value as QueuedTaskResponse, {
        onUpdate: (task) => {
          const progress = task.progress.completed !== undefined
            ? ` · ${task.progress.completed}/${task.progress.total}`
            : '';
          taskMessage.value = `研究任务${taskStatusLabel(task.status)}${progress}`;
        },
      });
      await loadData();
      taskMessage.value = '研究任务已完成，结果已重新载入';
      return detail;
    };
    if (mode.value === 'compare') {
      const { data } = await api.post<any | QueuedTaskResponse>('/research/compare', {
        ...base,
        venue_id: selectedDataset.value!.venue_id,
        symbol: selectedDataset.value!.instrument_key.split(':').pop(),
        interval: selectedDataset.value!.interval,
        strategy_ids: strategies.value.map((item) => item.strategy_id),
      });
      result.value = await resolveResult(data);
    } else if (mode.value === 'portfolio') {
      const { data } = await api.post<any | QueuedTaskResponse>('/research/portfolio', { ...base, pool_id: poolId.value, screening_run_id: screeningRunId.value || undefined, interval: portfolioInterval.value, strategy_id: strategyId.value, max_datasets: 20 });
      result.value = await resolveResult(data);
    } else if (mode.value === 'tune') {
      const { data } = await api.post<any | QueuedTaskResponse>('/research/tune', {
        ...base,
        venue_id: selectedDataset.value!.venue_id,
        symbol: selectedDataset.value!.instrument_key.split(':').pop(),
        interval: selectedDataset.value!.interval,
        strategy_id: strategyId.value,
        parameter_grid: tuneGrid.value,
        max_runs: 30,
      });
      result.value = await resolveResult(data);
    } else {
      const { data } = await api.post<any | QueuedTaskResponse>('/research/screen', { ...base, pool_id: poolId.value, interval: portfolioInterval.value, strategy_id: strategyId.value, lookback: 200, limit: 50 });
      result.value = await resolveResult(data);
    }
    runs.value = [result.value, ...runs.value].slice(0, 30);
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || cause.message || '研究任务执行失败';
  } finally {
    running.value = false;
  }
};

const formatNumber = (value: unknown, digits = 2) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(numeric) : '—';
};
const formatPct = (value: unknown) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric >= 0 ? '+' : ''}${formatNumber(numeric)}%` : '—';
};
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const datasetLabel = (item: HistoryDataset) => `${item.venue_id.toUpperCase()} · ${item.instrument_key.split(':').pop()} · ${item.interval} · ${formatNumber(item.row_count, 0)} 根`;
const portfolioScreeningRuns = computed(() => screeningRuns.value.filter((item) => item.pool_id === poolId.value && ["all", portfolioInterval.value].includes(String(item.interval || 'all'))));

const setMode = (next: ResearchMode) => {
  mode.value = next;
  result.value = null;
  error.value = '';
};

const runKind = (item: any) => String(item.kind || item.mode || 'research');
const selectRun = (item: any) => {
  const nextMode = researchModeByKind[runKind(item)];
  if (nextMode) mode.value = nextMode;
  result.value = item;
  error.value = '';
};

const applyResearchFocus = () => {
  const raw = window.localStorage.getItem('crypto.research-focus');
  if (!raw) return;
  window.localStorage.removeItem('crypto.research-focus');
  try {
    const focus = JSON.parse(raw) as {
      mode?: string;
      pool_id?: string;
      screening_run_id?: string;
      incubator_pool_id?: string;
    };
    if (focus.mode === 'portfolio') mode.value = 'portfolio';
    if (focus.pool_id && pools.value.some((item) => item.pool_id === focus.pool_id)) {
      poolId.value = focus.pool_id;
      focusedPoolId.value = focus.pool_id;
    }
    if (focus.screening_run_id && screeningRuns.value.some((item) => item.run_id === focus.screening_run_id)) {
      screeningRunId.value = focus.screening_run_id;
      const sourceRun = screeningRuns.value.find((item) => item.run_id === focus.screening_run_id);
      if (sourceRun && ['1d', '1h', '5m'].includes(String(sourceRun.interval))) focusedInterval.value = String(sourceRun.interval);
    }
    if (focus.incubator_pool_id) focusNotice.value = `已加载孵化池 ${focus.incubator_pool_id} 的研究入口；运行前仍会重新校验来源筛选和历史质量。`;
  } catch {
    focusNotice.value = '研究入口状态无法读取，已回到默认配置。';
  }
};

onMounted(async () => {
  await loadData();
  applyResearchFocus();
});
</script>

<template>
  <section class="research-center" aria-labelledby="research-title">
    <section class="page-heading research-heading"><div><p class="kicker">RESEARCH WORKBENCH / 08</p><h1 id="research-title">研究工作台</h1><p class="muted">策略、组合、筛选和参数研究统一使用已验证历史数据。</p></div><button class="icon-button" type="button" title="刷新研究配置" aria-label="刷新研究配置" :disabled="loading" @click="loadData"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="taskMessage" class="inline-notice" role="status" aria-live="polite">{{ taskMessage }}</div>
    <div v-if="focusNotice" class="research-focus" role="status"><Sparkles :size="14" /><span>{{ focusNotice }}</span></div>

    <section class="research-tabs" aria-label="研究模式">
      <button type="button" :class="{ active: mode === 'compare' }" @click="setMode('compare')"><Grid2X2 :size="15" /> 策略比较</button>
      <button type="button" :class="{ active: mode === 'portfolio' }" @click="setMode('portfolio')"><BarChart3 :size="15" /> 组合回测</button>
      <button type="button" :class="{ active: mode === 'tune' }" @click="setMode('tune')"><SlidersHorizontal :size="15" /> 参数搜索</button>
      <button type="button" :class="{ active: mode === 'screen' }" @click="setMode('screen')"><ListFilter :size="15" /> 信号筛选</button>
    </section>

    <section class="research-layout">
      <section class="research-panel config-panel" aria-labelledby="research-config-title"><div class="section-heading"><div><p class="kicker">RUN CONFIGURATION</p><h2 id="research-config-title">{{ modeLabel }}</h2></div><FlaskConical :size="18" class="section-icon" /></div><p class="mode-description">{{ modeDescription }}</p><form class="research-form" @submit.prevent="runResearch"><label v-if="mode === 'compare' || mode === 'tune'"><span>已验证数据集</span><select v-model="datasetId"><option v-for="item in datasets" :key="item.dataset_id" :value="item.dataset_id">{{ datasetLabel(item) }}</option></select></label><template v-else><label><span>研究币池</span><select v-model="poolId"><option v-for="item in pools" :key="item.pool_id" :value="item.pool_id">{{ item.name }} · {{ item.member_count }} 个数据集</option></select></label><label v-if="mode === 'portfolio'"><span>筛选结果（可选）</span><select v-model="screeningRunId"><option value="">使用币池全部合格数据集</option><option v-for="item in portfolioScreeningRuns" :key="item.run_id" :value="item.run_id">{{ item.run_id.slice(0, 12) }} · {{ item.candidate_count }} 个候选</option></select><small>只使用同一币池、同一周期且筛选通过的数据集。</small></label></template><label v-if="mode !== 'compare'"><span>策略</span><select v-model="strategyId"><option v-for="item in strategies" :key="item.strategy_id" :value="item.strategy_id">{{ item.name }}</option></select><small>{{ strategy?.description || '选择策略' }}</small></label><div class="research-field-grid"><label><span>费率（bps）</span><input v-model="feeBps" type="number" min="0" step="1" /></label><label><span>滑点（bps）</span><input v-model="slippageBps" type="number" min="0" step="1" /></label></div><div v-if="mode === 'tune'" class="grid-preview"><div><span>搜索空间</span><strong>{{ Object.keys(tuneGrid).length }} 个参数</strong></div><code>{{ JSON.stringify(tuneGrid) }}</code></div><button class="research-submit" type="submit" :disabled="running || (mode !== 'compare' && !poolId) || ((mode === 'compare' || mode === 'tune') && !datasetId)"><Search v-if="!running" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ running ? '计算中' : '运行研究' }}</span></button></form><p class="research-note"><Sparkles :size="13" /> 当前策略运行结果只用于研究、组合回测和模拟回放。</p></section>

      <section class="research-panel result-panel" aria-labelledby="research-result-title"><div class="section-heading"><div><p class="kicker">LATEST RESEARCH</p><h2 id="research-result-title">研究结果</h2></div><span class="section-meta">{{ result ? formatTime(result.created_at) : 'WAITING' }}</span></div><div v-if="!result" class="research-empty"><FlaskConical :size="25" /><p>选择研究模式并运行一次</p><small>所有结果都绑定数据集指纹和成本参数。</small></div><template v-else><div v-if="mode === 'compare'" class="result-table-wrap"><table class="result-table"><thead><tr><th>策略</th><th>收益</th><th>回撤</th><th>交易</th><th>评分</th><th>状态</th></tr></thead><tbody><tr v-for="item in result.items" :key="item.strategy_id"><td><strong>{{ strategies.find((entry) => entry.strategy_id === item.strategy_id)?.name || item.strategy_id }}</strong><small>{{ item.strategy_id }}</small></td><td :class="Number(item.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ item.status === 'completed' ? formatPct(item.total_return_pct) : '—' }}</td><td class="negative">{{ item.status === 'completed' ? `-${formatNumber(item.max_drawdown_pct)}%` : '—' }}</td><td>{{ item.orders ?? '—' }}</td><td>{{ item.score ? formatNumber(item.score, 3) : '—' }}</td><td><span :class="item.status === 'completed' ? 'positive' : 'warn'">{{ item.status === 'completed' ? '完成' : '阻断' }}</span></td></tr></tbody></table></div><div v-else-if="mode === 'portfolio'" class="portfolio-result"><div class="result-metrics"><article><span>组合收益</span><strong :class="Number(result.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(result.total_return_pct) }}</strong></article><article><span>最大回撤</span><strong class="negative">-{{ formatNumber(result.max_drawdown_pct) }}%</strong></article><article><span>交易次数</span><strong>{{ result.orders }}</strong></article><article><span>覆盖数据集</span><strong>{{ result.dataset_count }}</strong></article></div><div class="result-table-wrap"><table class="result-table"><thead><tr><th>市场</th><th>收益</th><th>回撤</th><th>交易</th><th>状态</th></tr></thead><tbody><tr v-for="item in result.items" :key="item.dataset_id"><td><strong>{{ item.venue_id.toUpperCase() }} · {{ item.symbol }}</strong><small>{{ item.interval }} · {{ item.candle_count }} 根</small></td><td :class="Number(item.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(item.total_return_pct) }}</td><td class="negative">-{{ formatNumber(item.max_drawdown_pct) }}%</td><td>{{ item.orders }}</td><td class="positive">完成</td></tr></tbody></table></div></div><div v-else-if="mode === 'tune'" class="tune-result"><div class="tune-best" v-if="result.best"><span>验证集最佳参数</span><strong>{{ formatNumber(result.best.robust_score, 3) }}</strong><code>{{ JSON.stringify(result.best.parameters) }}</code></div><div class="result-table-wrap"><table class="result-table"><thead><tr><th>试验</th><th>参数</th><th>验证收益</th><th>验证回撤</th><th>稳健评分</th></tr></thead><tbody><tr v-for="item in result.trials" :key="item.trial"><td>#{{ item.trial }}</td><td><code>{{ JSON.stringify(item.parameters) }}</code></td><td :class="Number(item.validation?.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ item.validation ? formatPct(item.validation.total_return_pct) : '—' }}</td><td class="negative">{{ item.validation ? `-${formatNumber(item.validation.max_drawdown_pct)}%` : '—' }}</td><td>{{ item.robust_score ? formatNumber(item.robust_score, 3) : '阻断' }}</td></tr></tbody></table></div></div><div v-else class="screen-result"><div class="result-metrics"><article><span>候选信号</span><strong class="positive">{{ result.candidate_count }}</strong></article><article><span>扫描数据集</span><strong>{{ result.dataset_count }}</strong></article><article><span>质量阻断</span><strong class="warn">{{ result.blocked_dataset_count }}</strong></article><article><span>策略</span><strong class="metric-word">{{ strategy?.name || result.strategy_id }}</strong></article></div><div class="result-table-wrap"><table class="result-table"><thead><tr><th>标的</th><th>信号</th><th>区间收益</th><th>末价</th><th>时间</th></tr></thead><tbody><tr v-for="item in result.items" :key="item.dataset_id"><td><strong>{{ item.venue_id.toUpperCase() }} · {{ item.symbol }}</strong><small>{{ item.interval }}</small></td><td :class="item.passed ? 'positive' : 'muted-text'">{{ item.signal }}</td><td :class="Number(item.period_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(item.period_return_pct) }}</td><td>{{ formatNumber(item.last_close, 6) }}</td><td>{{ formatTime(item.as_of) }}</td></tr></tbody></table></div></div></template></section>
    </section>

    <section class="research-panel archive-panel" aria-labelledby="research-archive-title"><div class="section-heading"><div><p class="kicker">RESEARCH ARCHIVE</p><h2 id="research-archive-title">最近运行</h2></div><span class="section-meta">{{ runs.length }} RUNS</span></div><div v-if="!runs.length" class="research-empty compact"><FlaskConical :size="19" /><p>还没有研究记录</p></div><div v-else class="archive-list"><button v-for="item in runs.slice(0, 8)" :key="item.run_id" type="button" class="archive-row" @click="selectRun(item)"><span class="archive-mark"><Check :size="13" /></span><span><strong>{{ runKind(item) }}</strong><small>{{ item.strategy_id || item.pool_id || item.symbol || '研究任务' }} · {{ formatTime(item.created_at) }}</small></span><b>{{ item.total_return_pct ? formatPct(item.total_return_pct) : item.candidate_count ?? item.trial_count ?? '—' }}</b></button></div></section>
  </section>
</template>

<style scoped>
.research-center { display: grid; gap: 30px; }
.research-heading { margin-bottom: 0; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; line-height: 1.5; }
.research-focus { display: flex; align-items: start; gap: 8px; border-left: 2px solid var(--cyan); padding: 9px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 11px; line-height: 1.5; }
.research-focus svg { flex: 0 0 auto; margin-top: 1px; }
.research-tabs { display: flex; gap: 0; overflow-x: auto; border-bottom: 1px solid var(--line); }
.research-tabs button { display: inline-flex; align-items: center; gap: 7px; min-height: 42px; border: 0; border-bottom: 2px solid transparent; padding: 9px 16px; color: var(--dim); background: transparent; font-size: 11px; white-space: nowrap; }
.research-tabs button.active { border-bottom-color: var(--cyan); color: var(--cyan); }
.research-tabs button:hover { color: var(--ink); }
.research-layout { display: grid; grid-template-columns: minmax(280px, .7fr) minmax(0, 1.3fr); gap: 14px; align-items: start; }
.research-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 25px 21px 20px; background: var(--panel); }
.research-panel .section-heading { margin-bottom: 18px; }
.mode-description { margin: -5px 0 18px; color: var(--muted); font-size: 11px; line-height: 1.6; }
.research-form { display: grid; gap: 14px; }
.research-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.research-form input, .research-form select { width: 100%; min-height: 38px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; color: var(--ink); background: var(--input-bg); outline: none; }
.research-form input:focus, .research-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.research-form small { color: var(--dim); font-size: 10px; line-height: 1.5; }
.research-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.grid-preview { display: grid; gap: 8px; border-left: 2px solid var(--amber); padding: 8px 10px; background: rgba(228, 179, 109, .06); }
.grid-preview div { display: flex; justify-content: space-between; gap: 10px; color: var(--dim); font-size: 10px; }
.grid-preview strong { color: var(--ink); font-weight: 550; }
.grid-preview code, .tune-best code { overflow: hidden; color: var(--muted); font: 9px/1.5 Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.research-submit { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 12px; font-weight: 720; }
.research-submit:hover:not(:disabled) { background: #94f0df; }
.research-submit:disabled { cursor: not-allowed; opacity: .55; }
.research-note { display: flex; align-items: start; gap: 7px; margin: 17px 0 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.research-note svg { flex: 0 0 auto; color: var(--amber); }
.research-empty { display: grid; justify-items: center; gap: 9px; min-height: 300px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.research-empty.compact { min-height: 80px; }
.research-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.research-empty small { color: var(--dim); font-size: 10px; }
.result-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.result-table { width: 100%; min-width: 620px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.result-table th, .result-table td { padding: 10px 9px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.result-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
.result-table th:first-child, .result-table td:first-child { text-align: left; }
.result-table tr:last-child td { border-bottom: 0; }
.result-table td:first-child { display: grid; gap: 3px; }
.result-table strong { color: var(--ink); font-size: 11px; font-weight: 560; }
.result-table small { color: var(--dim); font-size: 9px; }
.result-table code { max-width: 220px; overflow: hidden; color: var(--dim); font: 9px Consolas, monospace; text-overflow: ellipsis; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.warn { color: var(--amber) !important; }
.muted-text { color: var(--dim) !important; }
.result-metrics { display: grid; grid-template-columns: repeat(4, 1fr); margin-bottom: 15px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.result-metrics article { display: grid; gap: 7px; min-height: 88px; padding: 12px 11px; border-right: 1px solid var(--line); }
.result-metrics article:last-child { border-right: 0; }
.result-metrics span { color: var(--dim); font-size: 10px; }
.result-metrics strong { align-self: center; color: var(--ink); font-size: 20px; font-weight: 570; }
.result-metrics .metric-word { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.tune-result, .portfolio-result, .screen-result { display: grid; gap: 15px; }
.tune-best { display: grid; gap: 6px; border-left: 2px solid var(--cyan); padding: 10px 11px; background: rgba(108, 229, 208, .06); }
.tune-best span { color: var(--dim); font-size: 10px; }
.tune-best strong { color: var(--cyan); font-size: 23px; font-weight: 570; }
.archive-panel { padding-bottom: 14px; }
.archive-list { display: grid; border-top: 1px solid var(--line); }
.archive-row { display: grid; grid-template-columns: 27px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 56px; border: 0; border-bottom: 1px solid var(--line); color: var(--muted); background: transparent; text-align: left; }
.archive-row:hover { background: var(--panel-soft); }
.archive-mark { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid var(--line-bright); color: var(--cyan); }
.archive-row > span:nth-child(2) { display: grid; gap: 4px; min-width: 0; }
.archive-row strong { color: var(--ink); font-size: 11px; font-weight: 560; }
.archive-row small { overflow: hidden; color: var(--dim); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.archive-row b { color: var(--cyan); font-size: 11px; font-weight: 570; }
@media (max-width: 920px) { .research-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .research-field-grid, .result-metrics { grid-template-columns: 1fr 1fr; } .result-metrics article:nth-child(2) { border-right: 0; } .result-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } .result-metrics article:nth-child(3), .result-metrics article:nth-child(4) { border-bottom: 0; } }
</style>
