<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { AlertTriangle, BriefcaseBusiness, Check, CircleDollarSign, FlaskConical, Play, RefreshCw, RotateCcw, Save, Send, ShieldCheck, Square } from 'lucide-vue-next';
import { api } from '../api';
import type { HistoryCoverage, HistoryDataset, PaperAccountSummary, PaperAutomation, PaperFollowConfig, PaperFollowSnapshot, PaperFollowState, PaperLiveConfig, PaperOrder, PaperSummary, QueuedTaskResponse, StrategyDefinition } from '../types';
import { isQueuedTask, resolveTaskResponse, taskStatusLabel } from '../services/taskPolling';

const summary = ref<PaperSummary | null>(null);
const orders = ref<PaperOrder[]>([]);
const strategyRuns = ref<any[]>([]);
const strategies = ref<StrategyDefinition[]>([]);
const datasets = ref<HistoryDataset[]>([]);
const selectedDatasetId = ref('');
const side = ref<'BUY' | 'SELL'>('SELL');
const quantity = ref('0.01');
const limitPrice = ref('');
const loading = ref(false);
const submitting = ref(false);
const strategyRunning = ref(false);
const resetting = ref(false);
const error = ref('');
const notice = ref('');
const taskMessage = ref('');
const strategyId = ref('sma_cross');
const replayParams = ref<Record<string, string>>({});
const newsGate = ref(false);
const expandedRunId = ref<string | null>(null);
const expandedSnapshotId = ref<string | null>(null);
const automationRunning = ref(false);
const automationSaving = ref(false);
const automationDatasetId = ref('');
const automationForm = ref<PaperAutomation>({
  enabled: false,
  venue_id: 'binance',
  symbol: 'BTC/USDT',
  interval: '1h',
  strategy_id: 'sma_cross',
  initial_quote: '10000',
  initial_base: '0',
  fee_bps: '10',
  slippage_bps: '5',
  fast_window: 10,
  slow_window: 30,
  allocation_ratio: '1',
  momentum_threshold_pct: '0.02',
  strategy_parameters: {},
  last_run_id: null,
  updated_at: null,
});
const followForm = ref<PaperFollowConfig>({
  enabled: false,
  interval_seconds: 86400,
  alert_min_return_pct: '0',
  alert_max_drawdown_pct: '20',
  alert_underperform_pct: '5',
  last_follow_at: null,
  last_dispatched_at: null,
  last_follow_run_id: null,
  updated_at: null,
});
const followState = ref<PaperFollowState | null>(null);
const followSaving = ref(false);
const followRunning = ref(false);
const liveForm = ref<PaperLiveConfig>({
  enabled: false,
  venue_id: 'binance',
  symbol: 'BTC/USDT',
  interval: '1h',
  strategy_id: 'macd_reversal',
  strategy_parameters: { fast: 8, slow: 26, signal: 7 },
  allocation_ratio: '1',
  last_candle_time: null,
  last_signal: null,
  last_tick_at: null,
  last_order_id: null,
  trade_count: 0,
  updated_at: null,
  recent_trades: [],
});
const liveSaving = ref(false);
const liveRunning = ref(false);
const liveResult = ref('');
const followIntervals = [
  { value: 3600, label: '每小时' },
  { value: 21600, label: '每 6 小时' },
  { value: 43200, label: '每 12 小时' },
  { value: 86400, label: '每天' },
  { value: 604800, label: '每周' },
];

const tradableDatasets = computed(() => datasets.value.filter((dataset) => dataset.interval === '1h' && dataset.gap_count === 0 && dataset.duplicate_count === 0));
const selectedDataset = computed(() => datasets.value.find((dataset) => dataset.dataset_id === selectedDatasetId.value) || tradableDatasets.value[0] || null);
const automationDatasets = computed(() => datasets.value.filter((dataset) => dataset.gap_count === 0 && dataset.duplicate_count === 0));
const positions = computed(() => summary.value?.positions || []);
const accountItems = computed(() => summary.value?.accounts || []);
const formatNumber = (value: string | number, maximumFractionDigits = 6) => new Intl.NumberFormat('en-US', { maximumFractionDigits }).format(Number(value));
const formatDate = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const statusLabel = (value: string) => ({ FILLED: '已成交', PARTIALLY_FILLED: '部分成交', OPEN: '挂单中', CANCELED: '已撤销', REJECTED: '已拒绝' }[value] || value);

const datasetSymbol = (dataset: HistoryDataset | null) => dataset?.instrument_key.split(':').pop() || '';
const venueLabel = (venueId: string) => ({ binance: 'Binance', okx: 'OKX', bybit: 'Bybit' }[venueId] || venueId.toUpperCase());
const accountPosition = (account: PaperAccountSummary, asset: string) => account.positions.find((item) => item.asset === asset);
const accountMark = (account: PaperAccountSummary, asset: string) => {
  const markPrice = accountPosition(account, asset)?.mark_price;
  return markPrice ? '≈ ' + formatNumber(markPrice, 4) + ' USDT' : '余额';
};
const resetLabel = (venueId: string) => '重置 ' + venueLabel(venueId) + ' 模拟账户';

const alertLabel = (code: string) => ({ return_below_threshold: '收益跌破阈值', drawdown_breach: '回撤超限', underperforms_market: '跑输市场' }[code] || code);
const followAlerted = computed(() => (followState.value?.snapshots || []).some((item) => (item.alerts || []).length > 0));

const selectedStrategyDef = computed(() => strategies.value.find((item) => item.strategy_id === strategyId.value) || null);
const replayParameterFields = computed(() => {
  const schema = selectedStrategyDef.value?.parameter_schema;
  if (!Array.isArray(schema)) return [];
  return (schema as any[])
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

const initReplayParams = () => {
  replayParams.value = Object.fromEntries(replayParameterFields.value.map((field) => [field.key, String(field.default ?? '')]));
};

const onStrategyChange = () => {
  initReplayParams();
};

const automationStrategyDef = computed(() => strategies.value.find((item) => item.strategy_id === automationForm.value.strategy_id) || null);
const automationParameterFields = computed(() => {
  const schema = automationStrategyDef.value?.parameter_schema;
  if (!Array.isArray(schema)) return [];
  return (schema as any[])
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

const onAutomationStrategyChange = () => {
  const params = automationForm.value.strategy_parameters;
  automationForm.value.strategy_parameters = Object.fromEntries(
    automationParameterFields.value.map((field) => [field.key, String(params?.[field.key] ?? field.default ?? '')]),
  );
};

const liveStrategyDef = computed(() => strategies.value.find((item) => item.strategy_id === liveForm.value.strategy_id) || null);
const liveParameterFields = computed(() => {
  const schema = liveStrategyDef.value?.parameter_schema;
  if (!Array.isArray(schema)) return [];
  return (schema as any[])
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

const onLiveStrategyChange = () => {
  const params = liveForm.value.strategy_parameters;
  liveForm.value.strategy_parameters = Object.fromEntries(
    liveParameterFields.value.map((field) => [field.key, String((params as any)?.[field.key] ?? field.default ?? '')]),
  );
};

const paramSummary = (params: any) => {
  if (!params || typeof params !== 'object') return '默认参数';
  const entries = Object.entries(params).filter(([, value]) => value !== '' && value !== null && value !== undefined);
  if (!entries.length) return '默认参数';
  return entries.map(([key, value]) => `${key}=${value}`).join(' · ');
};

const curveBarsFor = (run: any) => {
  const curve = run?.equity_curve || [];
  const values = curve.map((point: any) => Number(point.equity));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return curve.map((point: any) => ({ ...point, height: `${Math.max(12, ((Number(point.equity) - min) / span) * 88 + 12)}%` }));
};

const toggleRunDetail = (runId: string) => {
  expandedRunId.value = expandedRunId.value === runId ? null : runId;
};

const toggleSnapshotDetail = (runId: string) => {
  expandedSnapshotId.value = expandedSnapshotId.value === runId ? null : runId;
};

const loadData = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [summaryResponse, orderResponse, coverageResponse, strategyRunResponse, strategyResponse, automationResponse, followResponse, liveResponse] = await Promise.all([
      api.get<PaperSummary>('/paper/summary'),
      api.get<{ items: PaperOrder[] }>('/paper/orders'),
      api.get<HistoryCoverage>('/history/coverage'),
      api.get<{ items: any[] }>('/paper/strategy-runs'),
      api.get<{ items: StrategyDefinition[] }>('/strategies/catalog'),
      api.get<PaperAutomation>('/paper/automation'),
      api.get<PaperFollowState>('/paper/follow'),
      api.get<PaperLiveConfig>('/paper/live'),
    ]);
    summary.value = summaryResponse.data;
    orders.value = orderResponse.data.items;
    strategyRuns.value = strategyRunResponse.data.items;
    strategies.value = strategyResponse.data.items;
    datasets.value = coverageResponse.data.datasets;
    automationForm.value = automationResponse.data;
    automationForm.value.strategy_parameters = Object.fromEntries(
      Object.entries(automationForm.value.strategy_parameters || {}).map(([key, value]) => [key, String(value ?? '')]),
    );
    followState.value = followResponse.data;
    followForm.value = { ...followForm.value, ...followResponse.data.config };
    liveForm.value = { ...liveForm.value, ...liveResponse.data };
    liveForm.value.strategy_parameters = Object.fromEntries(
      Object.entries(liveForm.value.strategy_parameters || {}).map(([key, value]) => [key, String(value ?? '')]),
    );
    const configuredDataset = automationDatasets.value.find((dataset) => (
      dataset.venue_id === automationForm.value.venue_id
      && dataset.interval === automationForm.value.interval
      && dataset.instrument_key.split(':').pop() === automationForm.value.symbol
    ));
    automationDatasetId.value = configuredDataset?.dataset_id || automationDatasets.value[0]?.dataset_id || '';
    if (!selectedDatasetId.value || !tradableDatasets.value.some((item) => item.dataset_id === selectedDatasetId.value)) selectedDatasetId.value = tradableDatasets.value[0]?.dataset_id || '';
    initReplayParams();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '模拟盘状态读取失败';
  } finally {
    loading.value = false;
  }
};

const applyAutomationDataset = () => {
  const dataset = automationDatasets.value.find((item) => item.dataset_id === automationDatasetId.value);
  if (!dataset) return;
  automationForm.value.venue_id = dataset.venue_id;
  automationForm.value.symbol = dataset.instrument_key.split(':').pop() || dataset.native_symbol;
  automationForm.value.interval = dataset.interval;
};

const saveAutomation = async () => {
  automationSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperAutomation>('/paper/automation', automationForm.value);
    automationForm.value = data;
    notice.value = '模拟自动回放配置已保存';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '模拟自动回放配置保存失败';
  } finally {
    automationSaving.value = false;
  }
};

const runAutomation = async () => {
  automationRunning.value = true;
  error.value = '';
  notice.value = '';
  taskMessage.value = '';
  try {
    const response = await api.post<{ automation: PaperAutomation; run: any } | QueuedTaskResponse>('/paper/automation/run');
    let data: { automation: PaperAutomation; run: any };
    if (isQueuedTask(response.data)) {
      taskMessage.value = `模拟自动回放已排队 · ${response.data.task_id}`;
      data = await resolveTaskResponse<{ automation: PaperAutomation; run: any }>(response.data, {
        onUpdate: (task) => { taskMessage.value = `模拟自动回放${taskStatusLabel(task.status)}`; },
      });
      await loadData();
      taskMessage.value = '模拟自动回放已完成，结果已重新载入';
    } else {
      data = response.data;
    }
    automationForm.value = data.automation;
    strategyRuns.value = [data.run, ...strategyRuns.value.filter((item) => item.run_id !== data.run.run_id)];
    notice.value = `模拟自动回放完成 · ${data.run.orders} 次交易 · ${formatNumber(data.run.total_return_pct, 3)}%`;
  } catch (cause: any) {
    taskMessage.value = '';
    error.value = cause.response?.data?.detail || '模拟自动回放失败';
  } finally {
    automationRunning.value = false;
  }
};

const saveFollow = async () => {
  followSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperFollowState>('/paper/follow', followForm.value);
    followState.value = data;
    followForm.value = { ...followForm.value, ...data.config };
    notice.value = '模拟盘自动跟盯配置已保存';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '自动跟盯配置保存失败';
  } finally {
    followSaving.value = false;
  }
};

const runFollow = async () => {
  followRunning.value = true;
  error.value = '';
  notice.value = '';
  taskMessage.value = '';
  try {
    const response = await api.post<{ snapshot: PaperFollowSnapshot } | QueuedTaskResponse>('/paper/follow/run');
    let snapshot: PaperFollowSnapshot;
    if (isQueuedTask(response.data)) {
      taskMessage.value = `自动跟盯已排队 · ${response.data.task_id}`;
      const data = await resolveTaskResponse<{ snapshot: PaperFollowSnapshot }>(response.data, {
        onUpdate: (task) => { taskMessage.value = `自动跟盯${taskStatusLabel(task.status)}`; },
      });
      snapshot = data.snapshot;
      taskMessage.value = '自动跟盯已完成，结果已重新载入';
    } else {
      snapshot = response.data.snapshot;
    }
    await loadData();
    const alerts = (snapshot.alerts || []).map(alertLabel).join('、');
    notice.value = `自动跟盯完成 · 策略 ${formatNumber(snapshot.strategy_return_pct, 2)}% / 市场 ${formatNumber(snapshot.market_return_pct, 2)}%${alerts ? ' · 告警：' + alerts : ''}`;
  } catch (cause: any) {
    taskMessage.value = '';
    error.value = cause.response?.data?.detail || '自动跟盯失败';
  } finally {
    followRunning.value = false;
  }
};

const saveLive = async () => {
  liveSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperLiveConfig>('/paper/live', liveForm.value);
    liveForm.value = { ...liveForm.value, ...data };
    notice.value = '实盘模拟配置已保存';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '实盘模拟配置保存失败';
  } finally {
    liveSaving.value = false;
  }
};

const startLiveStrategy = async () => {
  liveSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperLiveConfig>('/paper/live', { ...liveForm.value, enabled: true });
    liveForm.value = { ...liveForm.value, ...data };
    notice.value = '策略已启动：引擎每 60 秒检查一次，有信号自动下单（模拟）';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略启动失败';
  } finally {
    liveSaving.value = false;
  }
};

const stopLiveStrategy = async () => {
  liveSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperLiveConfig>('/paper/live', { ...liveForm.value, enabled: false });
    liveForm.value = { ...liveForm.value, ...data };
    notice.value = '策略已停止：不再自动下单';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略停止失败';
  } finally {
    liveSaving.value = false;
  }
};

const runLive = async () => {
  liveRunning.value = true;
  error.value = '';
  notice.value = '';
  liveResult.value = '';
  try {
    const { data } = await api.post<{ status: string; signal?: string | null; trade?: { side: string; quantity: string } }>('/paper/live/run');
    await loadData();
    const statusText: Record<string, string> = { traded: '已下单', checked: '已检查(无信号)', no_new_candle: '无新 K 线', skipped: '已跳过' };
    liveResult.value = `执行结果：${statusText[data.status] || data.status}${data.signal ? ' · 信号 ' + data.signal : ''}${data.trade ? ` · ${data.trade.side === 'BUY' ? '买入' : '卖出'} ${formatNumber(data.trade.quantity, 6)}` : ''}`;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '实盘模拟执行失败';
  } finally {
    liveRunning.value = false;
  }
};

const runStrategy = async () => {
  if (!selectedDataset.value) {
    error.value = '没有质量通过的小时线数据集';
    return;
  }
  strategyRunning.value = true;
  error.value = '';
  notice.value = '';
  taskMessage.value = '';
  try {
    const response = await api.post<any | QueuedTaskResponse>('/paper/strategy-runs', {
      venue_id: selectedDataset.value.venue_id,
      symbol: datasetSymbol(selectedDataset.value),
      interval: selectedDataset.value.interval,
      strategy_id: strategyId.value,
      fee_bps: summary.value?.fee_bps || '10',
      slippage_bps: '5',
      strategy_parameters: { ...replayParams.value },
      news_gate: newsGate.value,
    });
    let data: any;
    if (isQueuedTask(response.data)) {
      taskMessage.value = `模拟策略回放已排队 · ${response.data.task_id}`;
      data = await resolveTaskResponse<any>(response.data, {
        onUpdate: (task) => { taskMessage.value = `模拟策略回放${taskStatusLabel(task.status)}`; },
      });
      await loadData();
      taskMessage.value = '模拟策略回放已完成，结果已重新载入';
    } else {
      data = response.data;
    }
    strategyRuns.value = [data, ...strategyRuns.value.filter((item) => item.run_id !== data.run_id)];
    notice.value = `模拟策略回放完成 · ${data.orders} 次交易 · ${formatNumber(data.total_return_pct, 3)}%`;
  } catch (cause: any) {
    taskMessage.value = '';
    error.value = cause.response?.data?.detail || '模拟策略回放失败';
  } finally {
    strategyRunning.value = false;
  }
};

const submitOrder = async () => {
  if (!selectedDataset.value) {
    error.value = '没有质量通过的小时线数据集';
    return;
  }
  submitting.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.post<PaperOrder>('/paper/orders', {
      venue_id: selectedDataset.value.venue_id,
      symbol: datasetSymbol(selectedDataset.value),
      interval: selectedDataset.value.interval,
      side: side.value,
      quantity: quantity.value,
      limit_price: limitPrice.value || null,
    });
    orders.value = [data, ...orders.value.filter((item) => item.order_id !== data.order_id)];
    notice.value = `${data.side === 'SELL' ? '卖出' : '买入'}${statusLabel(data.status)} · ${data.filled_quantity} ${data.symbol.split('/')[0]}`;
    await loadData();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '模拟订单提交失败';
  } finally {
    submitting.value = false;
  }
};

const resetAccount = async (venueId?: string) => {
  resetting.value = true;
  error.value = '';
  try {
    await api.post('/paper/reset', venueId ? { venue_id: venueId } : {});
    notice.value = venueId ? venueLabel(venueId) + ' 模拟账户已重置' : '全部模拟账户已重置为演示余额';
    await loadData();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '模拟账户重置失败';
  } finally {
    resetting.value = false;
  }
};

onMounted(loadData);
</script>

<template>
  <section class="paper-center" aria-labelledby="paper-title">
    <section class="page-heading paper-heading"><div><p class="kicker">SIMULATION DESK / 08</p><h1 id="paper-title">模拟盘</h1><p class="muted">使用已校验小时线和 L2 撮合器运行隔离的虚拟账户，余额与真实交易完全分离。</p></div><div class="paper-actions"><span class="heading-stamp"><ShieldCheck :size="14" /> 真实执行关闭</span><button class="icon-button" type="button" title="刷新模拟盘" aria-label="刷新模拟盘" :disabled="loading" @click="loadData"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></div></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div><div v-if="notice" class="inline-notice" role="status">{{ notice }}</div>
    <div v-if="taskMessage" class="inline-notice" role="status" aria-live="polite">{{ taskMessage }}</div>
    <section class="paper-banner"><AlertTriangle :size="17" /><div><strong>模拟环境</strong><span>所有订单只写入内存模拟账户，服务端真实执行模式为 DISABLED。</span></div></section>
    <section class="paper-panel automation-panel" aria-labelledby="paper-automation-title"><div class="section-heading"><div><p class="kicker">PAPER AUTOMATION</p><h2 id="paper-automation-title">自动策略回放</h2></div><Play :size="18" class="section-icon" /></div><div class="automation-form"><label><span>历史数据集</span><select v-model="automationDatasetId" @change="applyAutomationDataset"><option v-for="dataset in automationDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.venue_id.toUpperCase() }} · {{ datasetSymbol(dataset) }} · {{ dataset.interval }} · {{ dataset.row_count }} 根</option></select></label><label><span>策略</span><select v-model="automationForm.strategy_id" @change="onAutomationStrategyChange"><option v-for="strategy in strategies" :key="strategy.strategy_id" :value="strategy.strategy_id">{{ strategy.name }}</option></select></label><div v-if="automationParameterFields.length" class="replay-params"><label v-for="field in automationParameterFields" :key="field.key"><span>{{ field.label }}</span><input v-model="automationForm.strategy_parameters[field.key]" type="number" :min="field.min" :max="field.max" :step="field.type === 'integer' ? 1 : 0.01" /></label></div><label class="automation-check"><input v-model="automationForm.enabled" type="checkbox" /><span>允许手动触发</span></label><button class="secondary-button" type="button" :disabled="automationSaving" @click="saveAutomation"><Save v-if="!automationSaving" :size="14" /><RefreshCw v-else :size="14" class="spinning" /><span>{{ automationSaving ? '保存中' : '保存配置' }}</span></button><button class="paper-submit" type="button" :disabled="automationRunning || !automationForm.enabled || !automationDatasetId" @click="runAutomation"><Play v-if="!automationRunning" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ automationRunning ? '运行中' : '立即回放' }}</span></button></div><p class="paper-note"><ShieldCheck :size="13" /> 仅在当前服务中执行历史策略回放，不提交交易所订单，不改变模拟账户余额。</p></section>
    <section class="paper-panel follow-panel" aria-labelledby="paper-follow-title"><div class="section-heading"><div><p class="kicker">PAPER FOLLOW</p><h2 id="paper-follow-title">自动跟盯</h2></div><Play :size="18" class="section-icon" /></div><div class="automation-form follow-form"><label><span>跟盯周期</span><select v-model="followForm.interval_seconds"><option v-for="option in followIntervals" :key="option.value" :value="option.value">{{ option.label }}</option></select></label><label><span>收益告警线（%）</span><input v-model="followForm.alert_min_return_pct" type="number" step="0.1" /></label><label><span>回撤告警线（%）</span><input v-model="followForm.alert_max_drawdown_pct" type="number" step="0.5" min="0" /></label><label><span>跑输市场告警（%）</span><input v-model="followForm.alert_underperform_pct" type="number" step="0.5" min="0" /></label><label class="automation-check"><input v-model="followForm.enabled" type="checkbox" /><span>启用自动跟盯</span></label><button class="secondary-button" type="button" :disabled="followSaving" @click="saveFollow"><Save v-if="!followSaving" :size="14" /><RefreshCw v-else :size="14" class="spinning" /><span>{{ followSaving ? '保存中' : '保存配置' }}</span></button><button class="paper-submit" type="button" :disabled="followRunning || !followForm.enabled || !automationForm.enabled" @click="runFollow"><Play v-if="!followRunning" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ followRunning ? '跟盯中' : '立即跟盯' }}</span></button></div><div v-if="followState?.latest" class="follow-latest" aria-label="最新跟盯快照"><article><span>快照时间</span><strong class="follow-time">{{ formatDate(followState.latest.at) }}</strong></article><article><span>策略收益</span><strong :class="Number(followState.latest.strategy_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(followState.latest.strategy_return_pct, 2) }}%</strong></article><article><span>市场收益</span><strong :class="Number(followState.latest.market_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(followState.latest.market_return_pct, 2) }}%</strong></article><article><span>超额收益</span><strong :class="Number(followState.latest.excess_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(followState.latest.excess_return_pct, 2) }}%</strong></article><article><span>最大回撤</span><strong>{{ formatNumber(followState.latest.max_drawdown_pct, 2) }}%</strong></article><article><span>告警</span><strong v-if="followState.latest.alerts.length" class="follow-alert">{{ followState.latest.alerts.map(alertLabel).join('、') }}</strong><strong v-else class="follow-ok">正常</strong></article></div><div v-if="followState && followState.snapshots.length" class="order-table-wrap"><table class="order-table"><thead><tr><th>时间</th><th>策略</th><th>策略收益</th><th>市场收益</th><th>超额</th><th>最大回撤</th><th>交易</th><th>告警</th></tr></thead><tbody><template v-for="snapshot in followState.snapshots" :key="snapshot.run_id"><tr class="snapshot-row" :class="{ expanded: expandedSnapshotId === snapshot.run_id }" @click="toggleSnapshotDetail(snapshot.run_id)"><td>{{ formatDate(snapshot.at) }}</td><td>{{ snapshot.strategy_id }}</td><td :class="Number(snapshot.strategy_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(snapshot.strategy_return_pct, 2) }}%</td><td :class="Number(snapshot.market_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(snapshot.market_return_pct, 2) }}%</td><td :class="Number(snapshot.excess_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(snapshot.excess_return_pct, 2) }}%</td><td>{{ formatNumber(snapshot.max_drawdown_pct, 2) }}%</td><td>{{ snapshot.orders }}</td><td><span v-if="snapshot.alerts.length" class="follow-alert">{{ snapshot.alerts.map(alertLabel).join('、') }}</span><span v-else>—</span></td></tr><tr v-if="expandedSnapshotId === snapshot.run_id" class="snapshot-detail-row"><td colspan="8"><div class="snapshot-detail"><span><strong>参数</strong>{{ paramSummary(snapshot.strategy_parameters) }}</span><span><strong>胜率</strong>{{ formatNumber(snapshot.win_rate_pct, 1) }}%</span><span><strong>区间</strong>{{ formatDate(snapshot.start_at) }} → {{ formatDate(snapshot.end_at) }}</span><span><strong>K 线</strong>{{ snapshot.candle_count }} 根</span></div></td></tr></template></tbody></table></div><p class="paper-note"><ShieldCheck :size="13" /> 调度器按周期用最新历史自动回放当前策略，并与买入持有基准对比，只做绩效记录与告警，不提交交易所订单。</p></section>
    <section class="paper-panel live-panel" aria-labelledby="paper-live-title"><div class="section-heading"><div><p class="kicker">LIVE PAPER</p><h2 id="paper-live-title">实盘模拟</h2></div><BriefcaseBusiness :size="18" class="section-icon" /></div><div class="automation-form"><label><span>市场</span><select v-model="liveForm.venue_id"><option value="binance">Binance</option><option value="okx">OKX</option><option value="bybit">Bybit</option></select></label><label><span>品种</span><input v-model="liveForm.symbol" placeholder="BTC/USDT" /></label><label><span>周期</span><select v-model="liveForm.interval"><option value="5m">5 分钟</option><option value="1h">1 小时</option><option value="1d">1 日</option></select></label><label><span>策略</span><select v-model="liveForm.strategy_id" @change="onLiveStrategyChange"><option v-for="strategy in strategies" :key="strategy.strategy_id" :value="strategy.strategy_id">{{ strategy.name }}</option></select></label><div v-if="liveParameterFields.length" class="replay-params"><label v-for="field in liveParameterFields" :key="field.key"><span>{{ field.label }}</span><input v-model="liveForm.strategy_parameters[field.key]" type="number" :min="field.min" :max="field.max" :step="field.type === 'integer' ? 1 : 0.01" /></label></div><label><span>仓位比例</span><input v-model="liveForm.allocation_ratio" type="number" min="0.01" max="1" step="0.05" /></label><button class="secondary-button" type="button" :disabled="liveSaving" @click="saveLive"><Save v-if="!liveSaving" :size="14" /><RefreshCw v-else :size="14" class="spinning" /><span>{{ liveSaving ? '保存中' : '保存配置' }}</span></button><button v-if="!liveForm.enabled" class="paper-submit" type="button" :disabled="liveSaving" @click="startLiveStrategy"><Play :size="15" /><span>启动策略</span></button><button v-else class="danger-button" type="button" :disabled="liveSaving" @click="stopLiveStrategy"><Square :size="15" /><span>停止策略</span></button><button class="secondary-button" type="button" :disabled="liveRunning || !liveForm.enabled" @click="runLive"><Play v-if="!liveRunning" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ liveRunning ? '执行中' : '立即执行一次' }}</span></button></div><div v-if="liveResult" class="inline-notice" role="status"><Check :size="14" />{{ liveResult }}</div><div class="live-status" aria-label="实盘模拟状态"><article><span>状态</span><strong :class="liveForm.enabled ? 'positive' : ''">{{ liveForm.enabled ? '运行中' : '已停止' }}</strong></article><article><span>上次执行</span><strong class="follow-time">{{ liveForm.last_tick_at ? formatDate(liveForm.last_tick_at) : '—' }}</strong></article><article><span>上次信号</span><strong>{{ liveForm.last_signal || '—' }}</strong></article><article><span>累计下单</span><strong>{{ liveForm.trade_count }}</strong></article></div><div v-if="liveForm.recent_trades.length" class="order-table-wrap"><table class="order-table"><thead><tr><th>时间</th><th>信号</th><th>方向</th><th>数量</th><th>成交价</th><th>订单</th></tr></thead><tbody><tr v-for="trade in liveForm.recent_trades" :key="trade.order_id || trade.created_at"><td>{{ formatDate(trade.created_at) }}</td><td>{{ trade.signal || '—' }}</td><td :class="trade.side === 'SELL' ? 'negative' : 'positive'">{{ trade.side === 'SELL' ? '卖出' : '买入' }}</td><td>{{ formatNumber(trade.quantity, 6) }}</td><td>{{ trade.filled_price ? formatNumber(trade.filled_price, 2) : '—' }}</td><td class="mono">{{ (trade.order_id || '—').slice(0, 8) }}</td></tr></tbody></table></div><p class="paper-note"><ShieldCheck :size="13" /> 策略启动后独立运行：引擎每 60 秒检查最新已收盘 K 线，有信号自动下单（每根 K 线最多一笔）；只写模拟账本，不连接真实 API Key，真实执行保持关闭。</p></section>
    <section class="paper-metrics" aria-label="模拟盘摘要"><article><span>模拟净值</span><strong>{{ summary ? formatNumber(summary.equity_quote, 2) : '—' }}</strong><em>USDT</em></article><article><span>基础资产</span><strong>{{ positions.length }}</strong><em>有余额的币种</em></article><article><span>已提交订单</span><strong>{{ summary?.order_count ?? 0 }}</strong><em>模拟撮合记录</em></article><article><span>策略回放</span><strong>{{ summary?.strategy_run_count ?? strategyRuns.length }}</strong><em>历史样本运行</em></article><article><span>费率</span><strong>{{ summary?.fee_bps || '10' }}</strong><em>bps</em></article></section>

    <section class="paper-panel strategy-replay-panel" aria-labelledby="paper-strategy-title"><div class="section-heading"><div><p class="kicker">PAPER STRATEGY REPLAY</p><h2 id="paper-strategy-title">模拟策略回放</h2></div><FlaskConical :size="18" class="section-icon" /></div><div class="replay-form"><label><span>历史数据集</span><select v-model="selectedDatasetId"><option v-for="dataset in tradableDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.venue_id.toUpperCase() }} · {{ datasetSymbol(dataset) }} · {{ dataset.row_count }} 根</option></select></label><label><span>策略</span><select v-model="strategyId" @change="onStrategyChange"><option v-for="strategy in strategies" :key="strategy.strategy_id" :value="strategy.strategy_id">{{ strategy.name }}</option></select></label><div v-if="replayParameterFields.length" class="replay-params"><label v-for="field in replayParameterFields" :key="field.key"><span>{{ field.label }}</span><input v-model="replayParams[field.key]" type="number" :min="field.min" :max="field.max" :step="field.type === 'integer' ? 1 : 0.01" /></label></div><label class="replay-check"><input v-model="newsGate" type="checkbox" /><span>新闻门控(风险事件后按建议时长拦截新开仓)</span></label><button class="paper-submit" type="button" :disabled="strategyRunning || !selectedDataset" @click="runStrategy"><FlaskConical v-if="!strategyRunning" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ strategyRunning ? '回放中' : '运行策略回放' }}</span></button></div><p class="paper-note"><CircleDollarSign :size="13" /> 回放结果只写入模拟研究记录，不改变虚拟账户余额。</p></section>

    <section class="paper-layout">
      <section class="paper-panel order-panel" aria-labelledby="paper-order-title"><div class="section-heading"><div><p class="kicker">PAPER ORDER</p><h2 id="paper-order-title">提交模拟订单</h2></div><Send :size="18" class="section-icon" /></div><form class="paper-form" @submit.prevent="submitOrder"><label><span>行情数据集</span><select v-model="selectedDatasetId"><option v-for="dataset in tradableDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.venue_id.toUpperCase() }} · {{ datasetSymbol(dataset) }} · {{ dataset.row_count }} 根</option></select></label><label><span>方向</span><select v-model="side"><option value="SELL">卖出</option><option value="BUY">买入</option></select></label><label><span>数量</span><input v-model="quantity" type="number" min="0.000001" step="0.000001" required /></label><label><span>限价（留空按 IOC 参考价）</span><input v-model="limitPrice" type="number" min="0" step="0.00000001" placeholder="自动使用尾部收盘附近价格" /></label><button class="paper-submit" type="submit" :disabled="submitting || !selectedDataset"><Send v-if="!submitting" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ submitting ? '撮合中' : '提交模拟单' }}</span></button></form><p class="paper-note"><CircleDollarSign :size="13" /> 当前账户：{{ summary?.account_id || 'paper-main' }} · 数据源：已校验历史 K 线</p></section>

      <section class="paper-panel account-panel" aria-labelledby="paper-account-title"><div class="section-heading"><div><p class="kicker">PAPER ACCOUNT</p><h2 id="paper-account-title">虚拟账户</h2></div><button class="secondary-button" type="button" :disabled="resetting" @click="resetAccount()"><RotateCcw :size="14" /> 重置</button></div><div class="balance-list"><div v-for="(value, asset) in summary?.balances || {}" :key="asset" class="balance-row"><span><strong>{{ asset }}</strong><small>{{ positions.find((item) => item.asset === asset)?.mark_price ? `≈ ${formatNumber(positions.find((item) => item.asset === asset)?.mark_price || '0', 4)} USDT` : '余额' }}</small></span><b>{{ formatNumber(value, 8) }}</b></div><div v-if="!summary" class="empty-state compact"><RefreshCw :size="18" class="spinning" /><p>正在读取账户</p></div></div><footer class="account-foot"><span>仅模拟</span><strong>不连接真实 API Key</strong></footer></section>
    </section>

    <section class="paper-panel order-history" aria-labelledby="paper-orders-title"><div class="section-heading"><div><p class="kicker">ORDER LEDGER</p><h2 id="paper-orders-title">模拟订单记录</h2></div><span class="section-meta">{{ orders.length }} ORDERS</span></div><div class="order-table-wrap"><table class="order-table"><thead><tr><th>时间</th><th>市场</th><th>方向</th><th>请求量</th><th>成交量</th><th>均价</th><th>状态</th><th>原因</th></tr></thead><tbody><tr v-for="order in orders" :key="order.order_id"><td>{{ formatDate(order.created_at) }}</td><td>{{ order.venue_id.toUpperCase() }} · {{ order.symbol }}</td><td :class="order.side === 'SELL' ? 'negative' : 'positive'">{{ order.side === 'SELL' ? '卖出' : '买入' }}</td><td>{{ formatNumber(order.quantity, 8) }}</td><td>{{ formatNumber(order.filled_quantity, 8) }}</td><td>{{ order.average_price ? formatNumber(order.average_price, 6) : '—' }}</td><td><span :class="`order-status ${order.status.toLowerCase()}`">{{ statusLabel(order.status) }}</span></td><td>{{ order.reason || '—' }}</td></tr><tr v-if="!orders.length"><td colspan="8" class="table-empty">还没有模拟订单</td></tr></tbody></table></div></section>
    <section class="paper-panel account-breakdown-panel" aria-labelledby="paper-account-breakdown-title">
      <div class="section-heading"><div><p class="kicker">VENUE ACCOUNTS</p><h2 id="paper-account-breakdown-title">交易所账户隔离</h2></div><span class="section-meta">{{ summary?.venue_count || 0 }} VENUES</span></div>
      <div v-if="accountItems.length" class="account-grid">
        <article v-for="account in accountItems" :key="account.venue_id" class="venue-account">
          <header><div><strong>{{ venueLabel(account.venue_id) }}</strong><small>{{ account.account_id }} · 净值 {{ formatNumber(account.equity_quote, 2) }} USDT</small></div><button class="account-reset" type="button" :disabled="resetting" :aria-label="resetLabel(account.venue_id)" :title="resetLabel(account.venue_id)" @click="resetAccount(account.venue_id)"><RotateCcw :size="13" /></button></header>
          <div class="venue-balances"><div v-for="(value, asset) in account.balances" :key="asset" class="venue-balance"><span>{{ asset }}</span><b>{{ formatNumber(value, 8) }}</b><small>{{ accountMark(account, asset) }}</small></div></div>
          <footer><span>{{ account.order_count }} 笔订单 · {{ account.strategy_run_count }} 次回放</span><strong>仅模拟</strong></footer>
        </article>
      </div>
      <div v-else class="empty-state compact"><RefreshCw :size="18" class="spinning" /><p>正在读取账户</p></div>
    </section>
    <section class="paper-panel strategy-history" aria-labelledby="paper-strategy-history-title"><div class="section-heading"><div><p class="kicker">STRATEGY REPLAY LOG</p><h2 id="paper-strategy-history-title">策略回放记录</h2></div><span class="section-meta">{{ strategyRuns.length }} RUNS</span></div><div class="strategy-run-list"><template v-for="run in strategyRuns" :key="run.run_id"><div class="strategy-run-row" :class="{ expanded: expandedRunId === run.run_id }" @click="toggleRunDetail(run.run_id)" role="button" tabindex="0" @keydown.enter="toggleRunDetail(run.run_id)"><span><strong>{{ run.strategy_id }}</strong><small>{{ run.venue_id?.toUpperCase() }} · {{ run.symbol }} · {{ formatDate(run.created_at) }}</small><small class="run-params">{{ paramSummary(run.parameters?.strategy_parameters) }}</small></span><b :class="Number(run.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatNumber(run.total_return_pct, 3) }}%</b><em>{{ run.orders }} 笔</em></div><div v-if="expandedRunId === run.run_id" class="strategy-run-detail"><div class="run-stats"><article><span>胜率</span><strong>{{ formatNumber(run.win_rate_pct, 1) }}%</strong></article><article><span>盈利 / 全部</span><strong>{{ run.winning_round_trips }} / {{ run.round_trips }}</strong></article><article><span>最大回撤</span><strong>{{ formatNumber(run.max_drawdown_pct, 2) }}%</strong></article><article><span>手续费</span><strong>{{ formatNumber(run.fees_quote, 4) }}</strong></article><article><span>K 线</span><strong>{{ run.candle_count }}</strong></article><article><span>新闻门控拦截</span><strong>{{ run.news_blocked_entries ?? 0 }} 次</strong></article><article><span>参数</span><strong class="run-params-full">{{ paramSummary(run.parameters?.strategy_parameters) }}</strong></article></div><div class="equity-block"><div class="result-subhead"><strong>净值曲线</strong><span>{{ formatNumber(run.initial_equity, 2) }} → {{ formatNumber(run.final_equity, 2) }} USDT</span></div><div class="equity-bars" aria-label="净值曲线"><span v-for="point in curveBarsFor(run)" :key="point.timestamp" class="equity-bar" :style="{ height: point.height }" :title="`${formatDate(point.timestamp)} · ${formatNumber(point.equity)} USDT`" /></div></div><div v-if="run.trade_log && run.trade_log.length" class="trade-block"><div class="result-subhead"><strong>成交记录</strong><span>{{ run.trade_log.length }} 条 · 仅显示最近 8 条</span></div><div class="trade-list"><div v-for="trade in run.trade_log.slice(-8).reverse()" :key="`${trade.timestamp}-${trade.side}`" class="trade-row"><span :class="trade.side === 'BUY' ? 'positive' : 'negative'">{{ trade.side === 'BUY' ? '买入' : '卖出' }}</span><span>{{ formatDate(trade.timestamp) }}</span><strong>{{ formatNumber(trade.price) }}</strong><span>{{ formatNumber(trade.quantity, 6) }}</span><small>{{ trade.reason }}</small></div></div></div></div></template><div v-if="!strategyRuns.length" class="table-empty">还没有策略回放记录</div></div></section>
  </section>
</template>

<style scoped>
.paper-center { display: grid; gap: 30px; }
.paper-heading { margin-bottom: 0; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; line-height: 1.5; }
.paper-actions { display: flex; align-items: center; gap: 12px; }
.paper-banner { display: flex; align-items: start; gap: 10px; border-left: 2px solid var(--amber); padding: 12px 14px; color: var(--amber); background: rgba(228, 179, 109, .07); }
.paper-banner div { display: grid; gap: 4px; }
.paper-banner strong { color: var(--ink); font-size: 12px; font-weight: 620; }
.paper-banner span { color: var(--muted); font-size: 11px; line-height: 1.5; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; }
.paper-metrics { display: grid; grid-template-columns: repeat(5, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.paper-metrics article { display: grid; gap: 7px; min-height: 112px; padding: 19px 20px; border-right: 1px solid var(--line); }
.paper-metrics article:last-child { border-right: 0; }
.paper-metrics span, .paper-metrics em { color: var(--dim); font-size: 10px; font-style: normal; }
.paper-metrics strong { align-self: center; color: var(--ink); font-size: 25px; font-weight: 560; }
.paper-layout { display: grid; grid-template-columns: minmax(280px, .8fr) minmax(0, 1.2fr); gap: 13px; align-items: start; }
.automation-panel { display: grid; gap: 15px; }
.automation-form { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(150px, .8fr) auto auto auto; gap: 10px; align-items: end; }
.automation-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.automation-form input, .automation-form select { width: 100%; min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.automation-form input:focus, .automation-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.automation-check { display: inline-flex !important; align-items: center; gap: 7px !important; min-height: 37px; white-space: nowrap; }
.replay-check { display: inline-flex !important; align-items: center; gap: 7px !important; min-height: 37px; white-space: nowrap; color: var(--muted); font-size: 11px; }
.automation-check input { width: 16px; min-height: 16px; accent-color: var(--cyan); }
.follow-panel { display: grid; gap: 15px; }
.follow-form { grid-template-columns: minmax(150px, .9fr) minmax(130px, .7fr) minmax(130px, .7fr) minmax(140px, .7fr) auto auto auto; }
.follow-latest { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; padding: 12px 14px; border: 1px solid var(--line-bright); border-radius: 6px; background: var(--panel-soft); }
.follow-latest article { display: grid; gap: 5px; }
.follow-latest span { color: var(--muted); font-size: 11px; }
.follow-latest strong { font-size: 15px; font-weight: 650; }
.follow-time { font-size: 12px !important; font-weight: 500 !important; color: var(--muted); }
.follow-alert { color: var(--red); font-size: 12px !important; }
.follow-ok { color: var(--up); font-size: 12px !important; }
.live-status { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 12px; padding: 12px 14px; border: 1px solid var(--line-bright); border-radius: 6px; background: var(--panel-soft); }
.live-status article { display: grid; gap: 5px; }
.live-status span { color: var(--muted); font-size: 11px; }
.live-status strong { font-size: 15px; font-weight: 650; }
.mono { font-family: Consolas, monospace; font-size: 11px; }
@media (max-width: 1050px) { .follow-form { grid-template-columns: 1fr 1fr; } .follow-latest { grid-template-columns: repeat(3, 1fr); } }
.strategy-replay-panel { display: grid; gap: 15px; }
.replay-form { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(180px, .8fr) auto; gap: 12px; align-items: end; }
.replay-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.replay-form input, .replay-form select { width: 100%; min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.strategy-history { padding-bottom: 14px; }
.strategy-run-list { display: grid; border-top: 1px solid var(--line); }
.strategy-run-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 18px; min-height: 53px; border-bottom: 1px solid var(--line); }
.strategy-run-row > span { display: grid; gap: 4px; min-width: 0; }
.strategy-run-row strong { color: var(--ink); font-size: 11px; font-weight: 560; }
.strategy-run-row small, .strategy-run-row em { color: var(--dim); font-size: 9px; font-style: normal; }
.strategy-run-row b { font-size: 11px; font-weight: 570; }
.paper-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 19px; background: var(--panel); }
.account-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.venue-account { min-width: 0; border: 1px solid var(--line); background: var(--panel-soft); }
.venue-account header { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px; border-bottom: 1px solid var(--line); }
.venue-account header > div { display: grid; gap: 4px; min-width: 0; }
.venue-account header strong { color: var(--ink); font-size: 12px; font-weight: 620; }
.venue-account header small { color: var(--dim); font-size: 9px; }
.account-reset { display: inline-grid; place-items: center; width: 28px; aspect-ratio: 1; border: 1px solid var(--line-bright); border-radius: 4px; color: var(--muted); background: transparent; }
.account-reset:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.account-reset:disabled { opacity: .5; }
.venue-balances { display: grid; padding: 0 12px; }
.venue-balance { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 3px 10px; align-items: center; min-height: 42px; border-bottom: 1px solid var(--line); }
.venue-balance span { color: var(--muted); font-size: 10px; }
.venue-balance b { color: var(--ink); font: 600 11px Consolas, monospace; }
.venue-balance small { grid-column: 1 / -1; color: var(--dim); font-size: 8px; }
.venue-account footer { display: flex; justify-content: space-between; gap: 10px; padding: 10px 12px; color: var(--dim); font-size: 9px; }
.venue-account footer strong { color: var(--amber); font-weight: 500; }
.paper-form { display: grid; gap: 13px; }
.paper-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.paper-form input, .paper-form select { width: 100%; min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.paper-form input:focus, .paper-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.paper-submit { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 39px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 12px; font-weight: 720; }
.paper-submit:hover:not(:disabled) { background: #94f0df; }
.paper-submit:disabled { opacity: .55; cursor: not-allowed; }
.danger-button { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 39px; border: 1px solid #f0433a; border-radius: 5px; color: #fff; background: #f0433a; font-size: 12px; font-weight: 720; }
.danger-button:hover:not(:disabled) { background: #d63a32; }
.danger-button:disabled { opacity: .55; cursor: not-allowed; }
.paper-note { display: flex; align-items: start; gap: 7px; margin: 16px 0 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.paper-note svg { flex: 0 0 auto; color: var(--amber); }
.secondary-button { display: inline-flex; align-items: center; gap: 7px; min-height: 32px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 7px 10px; color: var(--muted); background: transparent; font-size: 10px; }
.secondary-button:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.secondary-button:disabled { opacity: .55; }
.balance-list { display: grid; border-top: 1px solid var(--line); }
.balance-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 49px; border-bottom: 1px solid var(--line); }
.balance-row span { display: grid; gap: 4px; }
.balance-row strong { color: var(--ink); font-size: 12px; font-weight: 580; }
.balance-row small { color: var(--dim); font-size: 9px; }
.balance-row b { color: var(--ink); font: 600 12px Consolas, monospace; }
.account-foot { display: flex; justify-content: space-between; gap: 12px; margin-top: 13px; color: var(--dim); font-size: 10px; }
.account-foot strong { color: var(--amber); font-weight: 500; }
.empty-state { display: grid; justify-items: center; gap: 8px; min-height: 150px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); }
.empty-state.compact { min-height: 80px; border: 0; }
.empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
.order-history { padding-bottom: 14px; }
.order-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.order-table { width: 100%; min-width: 900px; border-collapse: collapse; }
.order-table th, .order-table td { padding: 10px 9px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.order-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; letter-spacing: .07em; text-transform: uppercase; }
.order-table th:first-child, .order-table td:first-child, .order-table th:nth-child(2), .order-table td:nth-child(2) { text-align: left; }
.order-table tr:last-child td { border-bottom: 0; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.order-status { padding: 4px 6px; color: var(--muted); background: #222a2a; font-size: 9px; }
.order-status.filled { color: var(--cyan); background: rgba(108, 229, 208, .1); }
.order-status.partially_filled, .order-status.open { color: var(--amber); background: rgba(228, 179, 109, .1); }
.order-status.rejected { color: var(--red); background: rgba(238, 129, 120, .1); }
.table-empty { padding: 28px !important; color: var(--dim) !important; text-align: center !important; }
@media (max-width: 1050px) { .automation-form { grid-template-columns: 1fr 1fr; } .automation-form .automation-check { grid-column: 1 / -1; } }
@media (max-width: 900px) { .paper-layout { grid-template-columns: 1fr; } .replay-form { grid-template-columns: 1fr 1fr; } .replay-form button { grid-column: 1 / -1; } }
@media (max-width: 900px) { .account-grid { grid-template-columns: 1fr; } }
@media (max-width: 1100px) { .paper-metrics { grid-template-columns: repeat(3, 1fr); } .paper-metrics article:nth-child(3) { border-right: 0; } .paper-metrics article:nth-child(-n + 3) { border-bottom: 1px solid var(--line); } }
@media (max-width: 680px) { .paper-actions .heading-stamp { display: none; } .paper-metrics { grid-template-columns: 1fr 1fr; } .paper-metrics article:nth-child(2n) { border-right: 0; } .paper-metrics article:nth-child(-n + 4) { border-bottom: 1px solid var(--line); } }
@media (max-width: 420px) { .paper-metrics { grid-template-columns: 1fr; } .paper-metrics article { border-right: 0; border-bottom: 1px solid var(--line); } .paper-metrics article:last-child { border-bottom: 0; } }
.replay-params { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; grid-column: 1 / -1; }
.replay-params label { display: grid; gap: 6px; color: var(--muted); font-size: 11px; }
.replay-params input { width: 100%; min-height: 35px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 7px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.strategy-run-row { cursor: pointer; }
.strategy-run-row:hover { background: var(--panel-soft); }
.strategy-run-row.expanded { border-bottom: 0; background: var(--panel-soft); }
.run-params { color: var(--dim) !important; font-size: 10px !important; }
.strategy-run-detail { display: grid; gap: 18px; padding: 16px 4px 22px; border-bottom: 1px solid var(--line); }
.run-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); }
.run-stats article { display: grid; gap: 6px; padding: 12px; background: var(--panel); }
.run-stats span { color: var(--dim); font-size: 10px; }
.run-stats strong { color: var(--ink); font-size: 13px; font-weight: 600; }
.run-params-full { font-size: 11px !important; font-weight: 500 !important; word-break: break-all; }
.result-subhead { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 11px; }
.result-subhead strong { color: var(--ink); font-size: 12px; font-weight: 560; }
.result-subhead span { color: var(--dim); font-size: 10px; }
.equity-bars { display: flex; align-items: end; gap: 2px; height: 145px; border-bottom: 1px solid var(--line-bright); padding: 12px 4px 0; background: repeating-linear-gradient(to bottom, transparent 0, transparent 35px, rgba(59, 74, 72, .28) 36px); }
.equity-bar { flex: 1 1 0; min-width: 2px; max-width: 10px; border-top: 2px solid var(--cyan); background: rgba(108, 229, 208, .23); }
.trade-list { display: grid; border-top: 1px solid var(--line); }
.trade-row { display: grid; grid-template-columns: 42px 1.4fr 1fr 1fr 1.1fr; gap: 8px; align-items: center; min-height: 36px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; }
.trade-row strong { color: var(--ink); font-weight: 550; text-align: right; }
.trade-row small { overflow: hidden; color: var(--dim); text-overflow: ellipsis; white-space: nowrap; }
.snapshot-row { cursor: pointer; }
.snapshot-row:hover { background: var(--panel-soft); }
.snapshot-row.expanded { background: var(--panel-soft); }
.snapshot-detail-row td { padding: 0 !important; border-bottom: 1px solid var(--line); }
.snapshot-detail { display: flex; flex-wrap: wrap; gap: 8px 28px; padding: 12px 14px; background: var(--panel-soft); color: var(--muted); font-size: 11px; }
.snapshot-detail strong { color: var(--dim); font-weight: 600; margin-right: 8px; font-size: 10px; }
</style>
