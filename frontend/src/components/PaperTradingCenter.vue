<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { AlertTriangle, BriefcaseBusiness, Check, CircleDollarSign, FlaskConical, Play, RefreshCw, RotateCcw, Save, Send, ShieldCheck, Square } from 'lucide-vue-next';
import { api } from '../api';
import type { HistoryCoverage, HistoryDataset, PaperAccountSummary, PaperAutomation, PaperFollowConfig, PaperFollowSnapshot, PaperFollowState, PaperLiveConfig, PaperLiveInstance, PaperOrder, PaperSummary, QueuedTaskResponse, StrategyDefinition } from '../types';
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
const liveInstances = ref<PaperLiveInstance[]>([]);
const editingInstanceId = ref<string | null>(null);
const marketRegime = ref<{ regime: string; score: number; factor: number; blocks_new_positions: boolean; reason: string } | null>(null);
const globalAutoTrading = ref<boolean | null>(null);
const tradingPools = ref<{ pool_id: string; name: string; venue_id: string; current_members: string[] }[]>([]);
const usePool = ref(false);
const loadTradingPools = async () => {
  try {
    const { data } = await api.get<{ items: any[] }>('/coin-pools', { params: { role: 'trading' } });
    tradingPools.value = data.items;
  } catch { tradingPools.value = []; }
};
const poolName = (poolId: string | null) => tradingPools.value.find((p) => p.pool_id === poolId)?.name || poolId || '';
const showLiveEditor = ref(false);
const liveForm = ref<PaperLiveConfig>({
  enabled: false,
  auto_trading: false,
  venue_id: 'binance',
  symbol: 'BTC/USDT',
  pool_id: null,
  parent_pool_id: null,
  interval: '1h',
  strategy_id: 'macd_reversal',
  strategy_parameters: { fast: 8, slow: 26, signal: 7 },
  allocation_ratio: '1',
  max_position_ratio: '1',
  stop_loss_pct: '0',
  daily_max_loss_pct: '0',
  last_candle_time: null,
  last_signal: null,
  last_tick_at: null,
  last_order_id: null,
  trade_count: 0,
  entry_price: null,
  last_risk_event: null,
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
      api.get<{ instances: PaperLiveInstance[] }>('/paper/live'),
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
    const rawInstances = (liveResponse.data.instances || []) as PaperLiveInstance[];
    try {
      const { data: regimeData } = await api.get('/market-regime');
      marketRegime.value = regimeData;
    } catch { /* regime optional */ }
    try {
      const { data: tradingData } = await api.get<{ auto_trading_enabled: boolean }>('/settings/trading');
      globalAutoTrading.value = tradingData.auto_trading_enabled;
    } catch { globalAutoTrading.value = null; }
    liveInstances.value = rawInstances.map((inst) => ({
      ...inst,
      strategy_parameters: Object.fromEntries(
        Object.entries(inst.strategy_parameters || {}).map(([key, value]) => [key, String(value ?? '')]),
      ),
    }));
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

const blankLiveForm = (): PaperLiveConfig => ({
  enabled: false,
  auto_trading: false,
  venue_id: 'binance',
  symbol: 'BTC/USDT',
  pool_id: null,
  parent_pool_id: null,
  interval: '1h',
  strategy_id: 'macd_reversal',
  strategy_parameters: { fast: 8, slow: 26, signal: 7 },
  allocation_ratio: '1',
  max_position_ratio: '1',
  stop_loss_pct: '0',
  daily_max_loss_pct: '0',
  last_candle_time: null,
  last_signal: null,
  last_tick_at: null,
  last_order_id: null,
  trade_count: 0,
  entry_price: null,
  last_risk_event: null,
  updated_at: null,
  recent_trades: [],
});

const startNewInstance = () => {
  editingInstanceId.value = null;
  liveForm.value = blankLiveForm();
  usePool.value = false;
  onLiveStrategyChange();
  showLiveEditor.value = true;
};

const editInstance = (inst: PaperLiveInstance) => {
  editingInstanceId.value = inst.instance_id;
  const { instance_id: _drop, ...cfg } = inst;
  liveForm.value = { ...cfg };
  showLiveEditor.value = true;
};

const cancelEdit = () => {
  editingInstanceId.value = null;
  showLiveEditor.value = false;
};

const regimeLabel = (r: string) => ({ strong: '偏强', neutral: '中性', weak: '偏弱', crisis: '危机' }[r] || r);
const refreshInstances = async () => {
  const { data } = await api.get<{ instances: PaperLiveInstance[] }>('/paper/live');
  liveInstances.value = data.instances || [];
};

const saveLive = async () => {
  liveSaving.value = true;
  error.value = '';
  notice.value = '';
  if (!usePool.value) liveForm.value.pool_id = null;
  try {
    if (editingInstanceId.value) {
      const { data } = await api.put<PaperLiveInstance>(`/paper/live/instances/${editingInstanceId.value}`, liveForm.value);
      const idx = liveInstances.value.findIndex((i) => i.instance_id === editingInstanceId.value);
      if (idx >= 0) liveInstances.value[idx] = data;
      notice.value = (data as any)._notice || '策略实例已更新';
    } else {
      const { data } = await api.post<PaperLiveInstance>('/paper/live/instances', liveForm.value);
      liveInstances.value.push(data);
      notice.value = '策略实例已创建';
    }
    editingInstanceId.value = null;
    showLiveEditor.value = false;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略实例保存失败';
  } finally {
    liveSaving.value = false;
  }
};

const setInstanceEnabled = async (inst: PaperLiveInstance, enabled: boolean) => {
  liveSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperLiveInstance>(
      `/paper/live/instances/${inst.instance_id}`,
      { enabled },
    );
    const idx = liveInstances.value.findIndex((i) => i.instance_id === inst.instance_id);
    if (idx >= 0) liveInstances.value[idx] = data;
    notice.value = enabled
      ? '策略已启动：引擎每 60 秒检查一次信号。自动下单需另行开启本策略的"自动交易"开关（且全局总开关也要开）。'
      : '策略已停止：不再检查信号，也不再自动下单';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || (enabled ? '策略启动失败' : '策略停止失败');
  } finally {
    liveSaving.value = false;
  }
};

const setInstanceAutoTrading = async (inst: PaperLiveInstance, autoTrading: boolean) => {
  liveSaving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.put<PaperLiveInstance>(
      `/paper/live/instances/${inst.instance_id}`,
      { auto_trading: autoTrading },
    );
    const idx = liveInstances.value.findIndex((i) => i.instance_id === inst.instance_id);
    if (idx >= 0) liveInstances.value[idx] = data;
    notice.value = autoTrading
      ? '自动交易已开启：有信号时自动下单（模拟）。注意还需在账户页打开全局自动交易总开关，两者同时打开才生效。'
      : '自动交易已关闭：引擎只记录信号，不再自动下单（含止损单）';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || (autoTrading ? '自动交易开启失败' : '自动交易关闭失败');
  } finally {
    liveSaving.value = false;
  }
};

const deleteInstance = async (inst: PaperLiveInstance) => {
  if (!confirm(`确定删除策略实例 ${inst.venue_id} ${inst.symbol} ${inst.interval} 吗？`)) return;
  liveSaving.value = true;
  error.value = '';
  try {
    await api.delete(`/paper/live/instances/${inst.instance_id}`);
    liveInstances.value = liveInstances.value.filter((i) => i.instance_id !== inst.instance_id);
    if (editingInstanceId.value === inst.instance_id) { editingInstanceId.value = null; showLiveEditor.value = false; }
    notice.value = '策略实例已删除';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '删除失败';
  } finally {
    liveSaving.value = false;
  }
};

const resetInstanceAccount = async (inst: PaperLiveInstance) => {
  if (!confirm(`确定重置该策略的模拟账户为纯 USDT 10000 吗？当前持仓会被清空。`)) return;
  liveSaving.value = true;
  error.value = '';
  try {
    await api.post(`/paper/live/instances/${inst.instance_id}/reset-account`);
    await refreshInstances();
    notice.value = '模拟账户已重置为纯 USDT 10000';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '账户重置失败';
  } finally {
    liveSaving.value = false;
  }
};

const runInstance = async (inst: PaperLiveInstance) => {
  liveRunning.value = true;
  error.value = '';
  liveResult.value = '';
  try {
    const { data } = await api.post(`/paper/live/instances/${inst.instance_id}/run`);
    liveResult.value = JSON.stringify(data);
    await refreshInstances();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '立即执行失败';
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

onMounted(() => { loadData(); loadTradingPools(); });
</script>

<template>
  <section class="paper-center" aria-labelledby="paper-title">
    <section class="page-heading paper-heading"><div><p class="kicker">SIMULATION DESK / 08</p><h1 id="paper-title">模拟盘</h1><p class="muted">使用已校验小时线和 L2 撮合器运行隔离的虚拟账户，余额与真实交易完全分离。</p></div><div class="paper-actions"><span class="heading-stamp"><ShieldCheck :size="14" /> 真实执行关闭</span><button class="icon-button" type="button" title="刷新模拟盘" aria-label="刷新模拟盘" :disabled="loading" @click="loadData"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></div></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div><div v-if="notice" class="inline-notice" role="status">{{ notice }}</div>
    <div v-if="taskMessage" class="inline-notice" role="status" aria-live="polite">{{ taskMessage }}</div>
    <section class="paper-banner"><AlertTriangle :size="17" /><div><strong>模拟环境</strong><span>所有订单只写入内存模拟账户，服务端真实执行模式为 DISABLED。</span></div></section>
    
    
    <section class="paper-panel live-panel" aria-labelledby="paper-live-title">
      <div class="section-heading">
        <div><p class="kicker">LIVE PAPER</p><h2 id="paper-live-title">实盘模拟</h2></div>
        <BriefcaseBusiness :size="18" class="section-icon" />
      </div>
      <div v-if="marketRegime" class="regime-banner" :class="'regime-' + marketRegime.regime">
        <span class="regime-label">市场状态</span>
        <strong>{{ regimeLabel(marketRegime.regime) }}</strong>
        <span class="regime-score">{{ marketRegime.score.toFixed(1) }} 分</span>
        <span class="regime-factor">仓位系数 ×{{ marketRegime.factor }}</span>
        <span v-if="marketRegime.blocks_new_positions" class="regime-block">⛔ 禁止开仓</span>
        <span class="regime-reason">{{ marketRegime.reason }}</span>
      </div>
      
      <!-- 实例列表 -->
      <div v-if="liveInstances.length" class="order-table-wrap">
        <table class="order-table">
          <thead><tr><th>市场</th><th>品种</th><th>周期</th><th>策略</th><th>状态</th><th>自动交易</th><th>账户余额</th><th>上次执行</th><th>信号</th><th>下单</th><th>风控</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="inst in liveInstances" :key="inst.instance_id">
              <td>{{ inst.venue_id }}</td>
              <td><span v-if="inst.pool_id" class="pool-tag">币池 · {{ poolName(inst.pool_id) }}</span><span v-else>{{ inst.symbol }}</span><span v-if="inst.parent_pool_id" class="child-tag">池子实例</span></td>
              <td>{{ inst.interval }}</td>
              <td>{{ inst.strategy_id }}</td>
              <td><strong :class="inst.enabled ? 'positive' : ''">{{ inst.enabled ? '运行中' : '已停止' }}</strong></td>
              <td>
                <button v-if="!inst.auto_trading" class="secondary-button" type="button" :disabled="liveSaving" @click="setInstanceAutoTrading(inst, true)" title="开启后，有信号时自动下模拟单（还需账户页全局总开关同时打开）">开启</button>
                <button v-else class="danger-button" type="button" :disabled="liveSaving" @click="setInstanceAutoTrading(inst, false)" title="关闭后，引擎只记录信号，不再自动下单（含止损单）">关闭</button>
                <div v-if="inst.auto_trading && globalAutoTrading === false" class="muted" style="font-size: 12px; margin-top: 4px;">总开关未开，不下单</div>
              </td>
              <td class="mono">{{ inst.account ? Object.entries(inst.account.balances).map(([k, v]) => k + ':' + formatNumber(v, k === 'USDT' ? 2 : 6)).join(' ') : '—' }}</td>
              <td class="follow-time">{{ inst.last_tick_at ? formatDate(inst.last_tick_at) : '—' }}</td>
              <td>{{ inst.last_signal || '—' }}</td>
              <td>{{ inst.trade_count }}</td>
              <td v-if="inst.last_risk_event" class="negative">{{ inst.last_risk_event }}</td><td v-else>—</td>
              <td>
                <button v-if="!inst.enabled" class="secondary-button" type="button" :disabled="liveSaving" @click="setInstanceEnabled(inst, true)">启动</button>
                <button v-else class="danger-button" type="button" :disabled="liveSaving" @click="setInstanceEnabled(inst, false)">停止</button>
                <button class="secondary-button" type="button" :disabled="liveRunning || !inst.enabled" @click="runInstance(inst)">执行</button>
                <button class="secondary-button" type="button" @click="editInstance(inst)">编辑</button>
                <button class="secondary-button" type="button" :disabled="liveSaving" @click="resetInstanceAccount(inst)" title="重置为纯 USDT 10000">重置账户</button>
                <button class="danger-button" type="button" :disabled="liveSaving" @click="deleteInstance(inst)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="paper-note">暂无策略实例。点击下方"添加策略"创建第一个。</p>
      
      <div style="margin: 12px 0;">
        <button class="paper-submit" type="button" @click="startNewInstance"><Play :size="15" /><span>添加策略</span></button>
      </div>
      
      <!-- 编辑器 -->
      <div v-if="showLiveEditor" class="automation-form">
        <h3 style="margin: 0 0 8px;">{{ editingInstanceId ? '编辑策略' : '新策略' }}</h3>
        <label><span>市场</span><select v-model="liveForm.venue_id"><option value="binance">Binance</option><option value="okx">OKX</option><option value="bybit">Bybit</option></select></label>
        <label><span>标的类型</span><select v-model="usePool"><option :value="false">单一品种</option><option :value="true">币池（逐币运行）</option></select></label>
        <label v-if="!usePool"><span>品种</span><input v-model="liveForm.symbol" placeholder="BTC/USDT" /></label>
        <label v-else><span>交易币池</span><select v-model="liveForm.pool_id"><option :value="null">请选择已确认的交易池</option><option v-for="pool in tradingPools" :key="pool.pool_id" :value="pool.pool_id">{{ pool.name }}（{{ pool.current_members.length }}币）</option></select></label>
        <label><span>周期</span><select v-model="liveForm.interval"><option value="5m">5 分钟</option><option value="1h">1 小时</option><option value="1d">1 日</option></select></label>
        <label><span>策略</span><select v-model="liveForm.strategy_id" @change="onLiveStrategyChange"><option v-for="strategy in strategies" :key="strategy.strategy_id" :value="strategy.strategy_id">{{ strategy.name }}</option></select></label>
        <div v-if="liveParameterFields.length" class="replay-params">
          <label v-for="field in liveParameterFields" :key="field.key"><span>{{ field.label }}</span><input v-model="liveForm.strategy_parameters[field.key]" type="number" :min="field.min" :max="field.max" :step="field.type === 'integer' ? 1 : 0.01" /></label>
        </div>
        <label><span>仓位比例</span><input v-model="liveForm.allocation_ratio" type="number" min="0.01" max="1" step="0.05" /></label>
        <label><span>持仓上限</span><input v-model="liveForm.max_position_ratio" type="number" min="0.01" max="1" step="0.05" title="持仓市值占总权益的最大比例" /></label>
        <label><span>止损 %</span><input v-model="liveForm.stop_loss_pct" type="number" min="0" max="0.5" step="0.01" title="0=关闭；如 0.05 表示浮亏 5% 强制平仓" /></label>
        <label><span>单日最大亏损 %</span><input v-model="liveForm.daily_max_loss_pct" type="number" min="0" max="0.5" step="0.01" title="0=关闭；超限则停牌至次日 UTC 0 点" /></label>
        <label class="automation-check"><input v-model="liveForm.auto_trading" type="checkbox" /><span>自动交易（需策略通过准入漏斗 paper_approved；还需账户页全局总开关同时打开，默认关闭）</span></label>
        <button class="secondary-button" type="button" :disabled="liveSaving" @click="saveLive"><Save v-if="!liveSaving" :size="14" /><RefreshCw v-else :size="14" class="spinning" /><span>{{ liveSaving ? '保存中' : '保存' }}</span></button>
        <button v-if="editingInstanceId" class="secondary-button" type="button" @click="cancelEdit">取消</button>
      </div>
      
      <div v-if="liveResult" class="inline-notice" role="status"><Check :size="14" />{{ liveResult }}</div>
      <p class="paper-note"><ShieldCheck :size="13" /> 每个策略独立运行、独立账户：引擎每 60 秒轮询所有已启动的策略，各自检查最新已收盘 K 线（每根 K 线最多一笔）。自动下单需要两级开关同时打开——本策略的"自动交易"和账户页的"自动交易"总开关；任一关闭时引擎只记录信号、不下单（含止损单）。开启自动交易要求策略+参数通过准入漏斗（paper_approved，评分→复测→跨池验证→准入）；改参数后视为新版本，会自动关闭自动交易，需重过漏斗。只写模拟账本，不连接真实 API Key，真实执行保持关闭。</p>
    </section>
    <section class="paper-metrics" aria-label="模拟盘摘要"><article><span>模拟净值</span><strong>{{ summary ? formatNumber(summary.equity_quote, 2) : '—' }}</strong><em>USDT</em></article><article><span>基础资产</span><strong>{{ positions.length }}</strong><em>有余额的币种</em></article><article><span>已提交订单</span><strong>{{ summary?.order_count ?? 0 }}</strong><em>模拟撮合记录</em></article><article><span>策略回放</span><strong>{{ summary?.strategy_run_count ?? strategyRuns.length }}</strong><em>历史样本运行</em></article><article><span>费率</span><strong>{{ summary?.fee_bps || '10' }}</strong><em>bps</em></article></section>

    

    <section class="paper-layout">
      

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
.regime-banner { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; border: 1px solid var(--line); }
.regime-label { color: var(--dim); font-size: 11px; }
.regime-banner strong { font-size: 15px; }
.regime-strong { border-color: #1faa53; background: rgba(31,170,83,.08); } .regime-strong strong { color: #1faa53; }
.regime-neutral { border-color: var(--line-bright); } .regime-neutral strong { color: var(--cyan); }
.regime-weak { border-color: #ff9800; background: rgba(255,152,0,.08); } .regime-weak strong { color: #ff9800; }
.regime-crisis { border-color: #f0433a; background: rgba(240,67,58,.1); } .regime-crisis strong { color: #f0433a; }
.regime-score, .regime-factor { color: var(--muted); font-size: 12px; }
.regime-block { color: #f0433a; font-weight: 700; }
.regime-reason { color: var(--dim); font-size: 11px; margin-left: auto; }

.pool-tag { display: inline-block; padding: 2px 8px; font-size: 11px; border-radius: 10px; background: rgba(33,150,243,.12); color: #2196f3; border: 1px solid rgba(33,150,243,.35); }
.child-tag { display: inline-block; margin-left: 6px; padding: 1px 6px; font-size: 10px; border-radius: 8px; background: rgba(128,128,128,.12); color: var(--dim); }
</style>
