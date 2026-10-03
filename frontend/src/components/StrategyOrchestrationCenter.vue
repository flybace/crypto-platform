<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Check, Grid2X2, PackageOpen, Play, RefreshCw, ShieldCheck } from 'lucide-vue-next';
import { api } from '../api';
import type {
  HistoryCoverage,
  HistoryDataset,
  QueuedTaskResponse,
  StrategyDefinition,
  StrategyMatrix,
  StrategyMatrixRun,
  StrategyMatrixSummary,
  StrategyPackage,
  StrategyPackageSummary,
} from '../types';
import { isQueuedTask, resolveTaskResponse, taskStatusLabel } from '../services/taskPolling';

type OrchestrationTab = 'packages' | 'matrices';

const tab = ref<OrchestrationTab>('matrices');
const packages = ref<StrategyPackage[]>([]);
const packageSummary = ref<StrategyPackageSummary | null>(null);
const matrices = ref<StrategyMatrix[]>([]);
const matrixSummary = ref<StrategyMatrixSummary | null>(null);
const strategies = ref<StrategyDefinition[]>([]);
const datasets = ref<HistoryDataset[]>([]);
const loading = ref(false);
const saving = ref(false);
const runningMatrixId = ref('');
const error = ref('');
const notice = ref('');
const taskMessage = ref('');

const packageId = ref('research-baseline');
const packageName = ref('研究基线策略包');
const packageVersion = ref('0.1.0');
const packageNote = ref('只登记版本和策略关系；自定义代码执行保持关闭。');
const packageStrategyIds = ref<string[]>(['sma_cross', 'momentum']);

const matrixName = ref('三币种多市场策略矩阵');
const matrixInterval = ref<'1d' | '1h' | '5m'>('1d');
const matrixStrategyIds = ref<string[]>(['buy_and_hold', 'sma_cross', 'momentum']);
const matrixDatasetIds = ref<string[]>([]);
const initialQuote = ref('10000');
const feeBps = ref('10');
const slippageBps = ref('5');
const fastWindow = ref('10');
const slowWindow = ref('30');
const allocationRatio = ref('1');
const momentumThreshold = ref('0.02');

const backtestStrategies = computed(() => strategies.value.filter((item) => item.enabled !== false && item.modes.includes('backtest')));
const availableDatasets = computed(() => datasets.value.filter((item) => item.interval === matrixInterval.value));
const matrixCombinations = computed(() => matrixStrategyIds.value.length * matrixDatasetIds.value.length);
const latestMatrix = computed(() => matrices.value[0] || null);
const latestRun = computed<StrategyMatrixRun | null>(() => latestMatrix.value?.last_run || null);

const strategyName = (strategyId: string) => strategies.value.find((item) => item.strategy_id === strategyId)?.name || strategyId;
const packageStatus = (item: StrategyPackage) => item.runnable ? '可运行内置包' : '仅元数据';
const matrixStatus = (value: string) => ({ draft: '草稿', completed: '已完成', partial: '部分完成', blocked: '已阻断' }[value] || value);
const datasetLabel = (item: HistoryDataset) => `${item.venue_id.toUpperCase()} · ${item.instrument_key.split(':').pop() || item.native_symbol}`;
const formatNumber = (value: unknown, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
const formatPct = (value: unknown) => Number.isFinite(Number(value)) ? `${Number(value) >= 0 ? '+' : ''}${formatNumber(value)}%` : '—';
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false }) : '—';

const toggleValue = (values: string[], value: string) => values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
const togglePackageStrategy = (strategyId: string) => { packageStrategyIds.value = toggleValue(packageStrategyIds.value, strategyId); };
const toggleMatrixStrategy = (strategyId: string) => { matrixStrategyIds.value = toggleValue(matrixStrategyIds.value, strategyId); };
const toggleMatrixDataset = (datasetId: string) => { matrixDatasetIds.value = toggleValue(matrixDatasetIds.value, datasetId); };

const ensureDefaults = () => {
  const supportedIds = new Set(backtestStrategies.value.map((item) => item.strategy_id));
  packageStrategyIds.value = packageStrategyIds.value.filter((item) => supportedIds.has(item));
  matrixStrategyIds.value = matrixStrategyIds.value.filter((item) => supportedIds.has(item));
  if (!matrixStrategyIds.value.length) matrixStrategyIds.value = backtestStrategies.value.slice(0, 3).map((item) => item.strategy_id);
  const ids = new Set(availableDatasets.value.map((item) => item.dataset_id));
  matrixDatasetIds.value = matrixDatasetIds.value.filter((item) => ids.has(item));
  if (!matrixDatasetIds.value.length) matrixDatasetIds.value = availableDatasets.value.slice(0, 6).map((item) => item.dataset_id);
};

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [packageResponse, matrixResponse, strategyResponse, coverageResponse] = await Promise.all([
      api.get<StrategyPackageSummary>('/strategies/packages/summary'),
      api.get<StrategyMatrixSummary>('/strategies/matrices/summary'),
      api.get<{ items: StrategyDefinition[] }>('/strategies/catalog'),
      api.get<HistoryCoverage>('/history/coverage'),
    ]);
    packageSummary.value = packageResponse.data;
    packages.value = packageResponse.data.items || [];
    matrixSummary.value = matrixResponse.data;
    matrices.value = matrixResponse.data.items || [];
    strategies.value = strategyResponse.data.items || [];
    datasets.value = (coverageResponse.data.datasets || []).filter((item) => item.gap_count === 0 && item.duplicate_count === 0);
    ensureDefaults();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略编排状态读取失败';
  } finally {
    loading.value = false;
  }
};

watch(matrixInterval, ensureDefaults);

const registerPackage = async () => {
  if (!packageStrategyIds.value.length) {
    error.value = '策略包至少登记一个回测策略';
    return;
  }
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.post<StrategyPackage>('/strategies/packages', {
      package_id: packageId.value,
      name: packageName.value,
      version: packageVersion.value,
      strategy_ids: packageStrategyIds.value,
      note: packageNote.value,
    });
    packages.value = [...packages.value.filter((item) => item.package_id !== data.package_id), data];
    packageSummary.value = { ...(packageSummary.value as StrategyPackageSummary), items: packages.value, package_count: packages.value.length, custom_count: packages.value.filter((item) => item.source_type !== 'builtin').length, builtin_count: packages.value.filter((item) => item.source_type === 'builtin').length, runnable_count: packages.value.filter((item) => item.runnable).length };
    notice.value = `策略包 ${data.package_id} 已登记为草稿，尚未开放自定义代码执行。`;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略包登记失败';
  } finally {
    saving.value = false;
  }
};

const createMatrix = async () => {
  if (!matrixStrategyIds.value.length || !matrixDatasetIds.value.length) {
    error.value = '策略矩阵至少选择一个策略和一个质量通过的数据集';
    return;
  }
  if (matrixCombinations.value > 60) {
    error.value = '单次矩阵最多运行 60 个策略 × 数据集组合';
    return;
  }
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.post<StrategyMatrix>('/strategies/matrices', {
      name: matrixName.value,
      strategy_ids: matrixStrategyIds.value,
      dataset_ids: matrixDatasetIds.value,
      interval: matrixInterval.value,
      initial_quote: initialQuote.value,
      initial_base: '0',
      fee_bps: feeBps.value,
      slippage_bps: slippageBps.value,
      fast_window: Number(fastWindow.value),
      slow_window: Number(slowWindow.value),
      allocation_ratio: allocationRatio.value,
      momentum_threshold_pct: momentumThreshold.value,
      parameter_sets: {},
    });
    matrices.value = [data, ...matrices.value.filter((item) => item.matrix_id !== data.matrix_id)];
    matrixSummary.value = { ...(matrixSummary.value as StrategyMatrixSummary), items: matrices.value, matrix_count: matrices.value.length };
    notice.value = `矩阵 ${data.name} 已创建，共 ${matrixCombinations.value} 个组合；可在右侧运行。`;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略矩阵创建失败';
  } finally {
    saving.value = false;
  }
};

const runMatrix = async (matrix: StrategyMatrix) => {
  runningMatrixId.value = matrix.matrix_id;
  error.value = '';
  notice.value = '';
  taskMessage.value = '';
  try {
    const response = await api.post<{ matrix: StrategyMatrix; run: StrategyMatrixRun } | QueuedTaskResponse>(`/strategies/matrices/${encodeURIComponent(matrix.matrix_id)}/run`);
    let data: { matrix: StrategyMatrix; run: StrategyMatrixRun };
    if (isQueuedTask(response.data)) {
      taskMessage.value = `策略矩阵已排队 · ${response.data.task_id}`;
      data = await resolveTaskResponse<{ matrix: StrategyMatrix; run: StrategyMatrixRun }>(response.data, {
        onUpdate: (task) => {
          const progress = task.progress.completed !== undefined
            ? ` · ${task.progress.completed}/${task.progress.total}`
            : '';
          taskMessage.value = `策略矩阵${taskStatusLabel(task.status)}${progress}`;
        },
      });
      await load();
      taskMessage.value = '策略矩阵已完成，结果已重新载入';
    } else {
      data = response.data;
    }
    matrices.value = matrices.value.map((item) => item.matrix_id === data.matrix.matrix_id ? data.matrix : item);
    matrixSummary.value = { ...(matrixSummary.value as StrategyMatrixSummary), items: matrices.value };
    notice.value = `矩阵运行完成：${data.run.completed_count} 个组合完成，${data.run.blocked_count} 个组合被质量或策略门禁阻断。`;
  } catch (cause: any) {
    taskMessage.value = '';
    error.value = cause.response?.data?.detail || '策略矩阵运行失败';
  } finally {
    runningMatrixId.value = '';
  }
};

onMounted(load);
</script>

<template>
  <section class="orchestration-center" aria-labelledby="orchestration-title">
    <header class="orchestration-heading">
      <div><p class="kicker">STRATEGY ORCHESTRATION / 04B</p><h2 id="orchestration-title">策略编排</h2><p class="muted">统一登记策略版本，并在已验证历史上做有界的多策略、多市场比较。</p></div>
      <div class="orchestration-status"><span><ShieldCheck :size="14" /> 研究模式</span><button class="icon-button" type="button" title="刷新策略编排" aria-label="刷新策略编排" :disabled="loading" @click="load"><RefreshCw :size="16" :class="{ spinning: loading }" /></button></div>
    </header>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="notice" class="inline-notice" role="status">{{ notice }}</div>
    <div v-if="taskMessage" class="inline-notice" role="status" aria-live="polite">{{ taskMessage }}</div>
    <nav class="orchestration-tabs" aria-label="策略编排视图">
      <button type="button" :class="{ active: tab === 'matrices' }" @click="tab = 'matrices'"><Grid2X2 :size="15" /> 策略矩阵 <small>{{ matrixSummary?.matrix_count || 0 }}</small></button>
      <button type="button" :class="{ active: tab === 'packages' }" @click="tab = 'packages'"><PackageOpen :size="15" /> 策略包 <small>{{ packageSummary?.package_count || 0 }}</small></button>
    </nav>

    <template v-if="tab === 'packages'">
      <section class="orchestration-grid package-grid">
        <section class="orchestration-panel" aria-labelledby="package-form-title">
          <div class="panel-title"><div><p class="kicker">PACKAGE REGISTRY</p><h3 id="package-form-title">登记策略包</h3></div><PackageOpen :size="18" class="section-icon" /></div>
          <form class="orchestration-form" @submit.prevent="registerPackage">
            <label><span>包标识</span><input v-model="packageId" maxlength="80" autocomplete="off" /></label>
            <label><span>显示名称</span><input v-model="packageName" maxlength="120" /></label>
            <div class="form-grid"><label><span>版本</span><input v-model="packageVersion" maxlength="40" /></label><label><span>登记说明</span><input v-model="packageNote" maxlength="300" /></label></div>
            <fieldset><legend>包含策略</legend><label v-for="strategy in backtestStrategies" :key="strategy.strategy_id" class="check-option"><input type="checkbox" :checked="packageStrategyIds.includes(strategy.strategy_id)" @change="togglePackageStrategy(strategy.strategy_id)" /><span class="check-box"><Check :size="12" /></span><span>{{ strategy.name }}</span></label></fieldset>
            <button class="primary-small" type="submit" :disabled="saving"><RefreshCw v-if="saving" :size="14" class="spinning" /><Check v-else :size="14" />登记元数据</button>
          </form>
          <p class="panel-note"><ShieldCheck :size="13" /> 内置包可复用可信回测引擎；自定义包只保存版本和策略关系，当前不会加载或执行任意代码。</p>
        </section>
        <section class="orchestration-panel" aria-labelledby="package-list-title">
          <div class="panel-title"><div><p class="kicker">PACKAGE CATALOG</p><h3 id="package-list-title">策略包目录</h3></div><span class="section-meta">{{ packages.length }} PACKAGES</span></div>
          <div v-if="!packages.length" class="orchestration-empty"><PackageOpen :size="22" /><span>暂无策略包</span></div>
          <div v-else class="package-list"><article v-for="item in packages" :key="item.package_id" class="package-row"><div class="package-mark"><Check v-if="item.runnable" :size="14" /><PackageOpen v-else :size="14" /></div><div><strong>{{ item.name }}</strong><span>{{ item.package_id }} · v{{ item.version }}</span><small>{{ item.strategy_ids.map(strategyName).join('、') }}</small></div><b :class="item.runnable ? 'status-good' : 'status-warn'">{{ packageStatus(item) }}</b></article></div>
        </section>
      </section>
    </template>

    <template v-else>
      <section class="orchestration-grid matrix-grid">
        <section class="orchestration-panel" aria-labelledby="matrix-form-title">
          <div class="panel-title"><div><p class="kicker">MATRIX CONFIGURATION</p><h3 id="matrix-form-title">创建研究矩阵</h3></div><Grid2X2 :size="18" class="section-icon" /></div>
          <form class="orchestration-form" @submit.prevent="createMatrix">
            <label><span>矩阵名称</span><input v-model="matrixName" maxlength="120" /></label>
            <label><span>统一周期</span><select v-model="matrixInterval"><option value="1d">1 日</option><option value="1h">1 小时</option><option value="5m">5 分钟</option></select></label>
            <fieldset><legend>策略（{{ matrixStrategyIds.length }}）</legend><div class="option-grid"><label v-for="strategy in backtestStrategies" :key="strategy.strategy_id" class="check-option"><input type="checkbox" :checked="matrixStrategyIds.includes(strategy.strategy_id)" @change="toggleMatrixStrategy(strategy.strategy_id)" /><span class="check-box"><Check :size="12" /></span><span>{{ strategy.name }}</span></label></div></fieldset>
            <fieldset><legend>质量通过数据集（{{ matrixDatasetIds.length }} / {{ availableDatasets.length }}）</legend><div class="dataset-options"><label v-for="dataset in availableDatasets" :key="dataset.dataset_id" class="check-option"><input type="checkbox" :checked="matrixDatasetIds.includes(dataset.dataset_id)" @change="toggleMatrixDataset(dataset.dataset_id)" /><span class="check-box"><Check :size="12" /></span><span>{{ datasetLabel(dataset) }}<small>{{ formatNumber(dataset.row_count, 0) }} 根 · {{ dataset.start_at.slice(0, 10) }} 至 {{ dataset.end_at.slice(0, 10) }}</small></span></label></div></fieldset>
            <div class="form-grid"><label><span>初始 USDT</span><input v-model="initialQuote" type="number" min="1" step="1" /></label><label><span>费率（bps）</span><input v-model="feeBps" type="number" min="0" step="1" /></label><label><span>滑点（bps）</span><input v-model="slippageBps" type="number" min="0" step="1" /></label><label><span>资金分配比</span><input v-model="allocationRatio" type="number" min="0.01" max="1" step="0.01" /></label><label><span>快窗口</span><input v-model="fastWindow" type="number" min="2" step="1" /></label><label><span>慢窗口</span><input v-model="slowWindow" type="number" min="3" step="1" /></label><label><span>动量阈值</span><input v-model="momentumThreshold" type="number" min="0" step="0.01" /></label></div>
            <div class="matrix-submit-row"><span>{{ matrixCombinations }} 个有界组合</span><button class="primary-small" type="submit" :disabled="saving || !matrixCombinations"><RefreshCw v-if="saving" :size="14" class="spinning" /><Check v-else :size="14" />创建矩阵</button></div>
          </form>
          <p class="panel-note"><ShieldCheck :size="13" /> 只读取 Manifest、连续性和重复检查通过的 K 线；矩阵结果用于比较，不代表盘口可成交收益。</p>
        </section>
        <section class="orchestration-panel matrix-result-panel" aria-labelledby="matrix-list-title">
          <div class="panel-title"><div><p class="kicker">MATRIX ARCHIVE</p><h3 id="matrix-list-title">矩阵记录</h3></div><span class="section-meta">{{ matrices.length }} MATRICES</span></div>
          <div v-if="!matrices.length" class="orchestration-empty"><Grid2X2 :size="22" /><span>创建矩阵后，运行记录会出现在这里</span></div>
          <div v-else class="matrix-list"><article v-for="matrix in matrices" :key="matrix.matrix_id" class="matrix-row"><div class="matrix-row-head"><div><strong>{{ matrix.name }}</strong><span>{{ matrix.interval }} · {{ matrix.strategy_ids.length }} 策略 × {{ matrix.dataset_ids.length }} 数据集</span></div><b class="matrix-status" :class="`matrix-${matrix.status}`">{{ matrixStatus(matrix.status) }}</b></div><div v-if="matrix.last_run" class="matrix-last-run"><span>最近运行 {{ formatTime(matrix.last_run.created_at) }}</span><span>{{ matrix.last_run.completed_count }}/{{ matrix.last_run.item_count }} 完成</span><strong v-if="matrix.last_run.best" :class="Number(matrix.last_run.best.total_return_pct) >= 0 ? 'status-good' : 'status-bad'">最佳 {{ formatPct(matrix.last_run.best.total_return_pct) }}</strong></div><div class="matrix-row-actions"><span v-if="matrix.last_run?.research_only" class="research-only">RESEARCH ONLY</span><button class="secondary-small" type="button" :disabled="runningMatrixId === matrix.matrix_id" @click="runMatrix(matrix)"><RefreshCw v-if="runningMatrixId === matrix.matrix_id" :size="13" class="spinning" /><Play v-else :size="13" />{{ runningMatrixId === matrix.matrix_id ? '运行中' : '运行矩阵' }}</button></div></article></div>
        </section>
      </section>
      <section v-if="latestRun" class="matrix-detail-panel" aria-labelledby="matrix-detail-title"><div class="panel-title"><div><p class="kicker">LATEST MATRIX RESULT</p><h3 id="matrix-detail-title">最近组合结果</h3></div><span class="section-meta">{{ latestRun.completed_count }} COMPLETED</span></div><div class="matrix-table-wrap"><table class="matrix-table"><thead><tr><th>策略</th><th>数据集</th><th>收益</th><th>最大回撤</th><th>交易</th><th>状态</th></tr></thead><tbody><tr v-for="item in latestRun.items.slice(0, 12)" :key="`${item.strategy_id}-${item.dataset_id}`"><td>{{ strategyName(item.strategy_id) }}</td><td>{{ item.dataset_id.split(':').slice(3, 5).join(' · ') }}</td><td :class="Number(item.total_return_pct) >= 0 ? 'status-good' : 'status-bad'">{{ formatPct(item.total_return_pct) }}</td><td>{{ formatPct(item.max_drawdown_pct) }}</td><td>{{ item.orders ?? '—' }}</td><td><span :class="item.status === 'completed' ? 'status-good' : 'status-warn'">{{ item.status === 'completed' ? '完成' : item.reason || '阻断' }}</span></td></tr></tbody></table></div></section>
    </template>
  </section>
</template>

<style scoped>
.orchestration-center { display: grid; gap: 18px; margin-top: 8px; border-top: 1px solid var(--line); padding-top: 30px; }
.orchestration-heading, .panel-title, .orchestration-status, .orchestration-tabs, .matrix-row-head, .matrix-last-run, .matrix-row-actions, .matrix-submit-row { display: flex; align-items: center; }
.orchestration-heading, .panel-title, .matrix-row-head { justify-content: space-between; gap: 18px; }
.orchestration-heading h2 { margin: 0; color: var(--ink); font-size: 21px; font-weight: 560; }
.orchestration-heading .muted { max-width: 720px; margin-top: 9px; font-size: 11px; }
.orchestration-status { gap: 11px; color: var(--amber); font-size: 10px; }
.orchestration-status span { display: inline-flex; align-items: center; gap: 6px; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; }
.orchestration-tabs { gap: 6px; border-bottom: 1px solid var(--line); }
.orchestration-tabs button { display: inline-flex; align-items: center; gap: 7px; border: 0; border-bottom: 2px solid transparent; padding: 10px 8px 11px; color: var(--dim); background: transparent; font-size: 11px; }
.orchestration-tabs button.active { border-color: var(--cyan); color: var(--cyan); }
.orchestration-tabs small { color: inherit; font: 10px Consolas, monospace; }
.orchestration-grid { display: grid; grid-template-columns: minmax(330px, .95fr) minmax(0, 1.05fr); gap: 14px; align-items: start; }
.orchestration-panel, .matrix-detail-panel { min-width: 0; border: 1px solid var(--line); padding: 20px 18px 15px; background: var(--panel); }
.panel-title { margin-bottom: 16px; }
.panel-title h3 { margin: 0; color: var(--ink); font-size: 14px; font-weight: 560; }
.panel-title .kicker { margin-bottom: 8px; }
.section-meta { color: var(--dim); font: 9px Consolas, monospace; letter-spacing: .1em; }
.orchestration-form { display: grid; gap: 13px; }
.orchestration-form label, fieldset { display: grid; gap: 6px; min-width: 0; }
.orchestration-form label > span, legend { color: var(--muted); font-size: 10px; }
.orchestration-form input, .orchestration-form select { width: 100%; min-height: 36px; border: 1px solid var(--line-bright); border-radius: 4px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; font-size: 11px; }
.orchestration-form input:focus, .orchestration-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
fieldset { margin: 0; padding: 12px 0 0; border: 0; border-top: 1px solid var(--line); }
legend { padding: 0; }
.option-grid, .dataset-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 12px; max-height: 220px; overflow-y: auto; padding: 2px 0; }
.dataset-options { grid-template-columns: 1fr; }
.check-option { display: flex !important; grid-template-columns: none !important; align-items: start; gap: 7px !important; min-height: 27px; color: var(--muted); cursor: pointer; font-size: 10px; }
.check-option input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.check-box { display: grid; place-items: center; flex: 0 0 auto; width: 16px; height: 16px; border: 1px solid var(--line-bright); color: transparent; background: var(--input-bg); }
.check-option input:checked + .check-box { border-color: var(--cyan); color: #11201e; background: var(--cyan); }
.check-option input:focus-visible + .check-box { outline: 2px solid var(--cyan); outline-offset: 2px; }
.check-option small { display: block; margin-top: 3px; color: var(--dim); font: 9px Consolas, monospace; }
.primary-small, .secondary-small { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; border-radius: 4px; padding: 7px 10px; font-size: 10px; font-weight: 700; white-space: nowrap; }
.primary-small { border: 1px solid var(--cyan); color: #11201e; background: var(--cyan); }
.primary-small:hover:not(:disabled) { background: #94f0df; }
.secondary-small { border: 1px solid var(--line-bright); color: var(--muted); background: transparent; font-weight: 550; }
.secondary-small:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.primary-small:disabled, .secondary-small:disabled { cursor: not-allowed; opacity: .55; }
.panel-note { display: flex; align-items: start; gap: 7px; margin: 14px 0 0; color: var(--dim); font-size: 9px; line-height: 1.6; }
.panel-note svg { flex: 0 0 auto; color: var(--amber); }
.package-list, .matrix-list { display: grid; border-top: 1px solid var(--line); }
.package-row { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; align-items: center; gap: 9px; min-height: 64px; border-bottom: 1px solid var(--line); }
.package-mark { display: grid; place-items: center; width: 26px; height: 26px; border: 1px solid var(--line-bright); color: var(--cyan); }
.package-row > div:nth-child(2) { display: grid; gap: 3px; min-width: 0; }
.package-row strong { overflow: hidden; color: var(--ink); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.package-row span, .package-row small { color: var(--dim); font-size: 9px; }
.package-row small { color: var(--muted); }
.package-row b { font-size: 9px; font-weight: 550; white-space: nowrap; }
.status-good { color: var(--cyan) !important; }
.status-warn { color: var(--amber) !important; }
.status-bad { color: var(--red) !important; }
.orchestration-empty { display: grid; justify-items: center; gap: 8px; min-height: 160px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); font-size: 11px; }
.matrix-submit-row { justify-content: space-between; gap: 10px; color: var(--dim); font: 9px Consolas, monospace; }
.matrix-row { display: grid; gap: 10px; padding: 13px 0; border-bottom: 1px solid var(--line); }
.matrix-row-head > div { display: grid; gap: 4px; min-width: 0; }
.matrix-row-head strong { overflow: hidden; color: var(--ink); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.matrix-row-head span, .matrix-last-run, .research-only { color: var(--dim); font-size: 9px; }
.matrix-status { border: 1px solid var(--line-bright); padding: 4px 6px; color: var(--muted); font-size: 9px; font-weight: 550; white-space: nowrap; }
.matrix-completed { border-color: rgba(108, 229, 208, .45); color: var(--cyan); }
.matrix-partial { border-color: rgba(228, 179, 109, .45); color: var(--amber); }
.matrix-blocked { border-color: rgba(238, 129, 120, .45); color: var(--red); }
.matrix-last-run { gap: 12px; flex-wrap: wrap; }
.matrix-last-run strong { margin-left: auto; font-size: 10px; font-weight: 600; }
.matrix-row-actions { justify-content: space-between; gap: 10px; }
.research-only { letter-spacing: .1em; }
.matrix-detail-panel { padding-bottom: 10px; }
.matrix-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.matrix-table { width: 100%; min-width: 680px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.matrix-table th, .matrix-table td { padding: 9px 10px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: left; white-space: nowrap; }
.matrix-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; font-weight: 600; letter-spacing: .08em; }
.matrix-table tr:last-child td { border-bottom: 0; }
.matrix-table td:nth-child(3), .matrix-table td:nth-child(4), .matrix-table td:nth-child(5) { font-family: Consolas, monospace; }
@media (max-width: 760px) { .orchestration-heading { align-items: start; flex-direction: column; } .orchestration-status { width: 100%; justify-content: space-between; } .orchestration-grid { grid-template-columns: 1fr; } .package-grid { grid-template-columns: 1fr; } }
@media (max-width: 520px) { .form-grid, .option-grid { grid-template-columns: 1fr; } .orchestration-panel, .matrix-detail-panel { padding: 17px 13px 13px; } .package-row { grid-template-columns: 28px minmax(0, 1fr); } .package-row b { grid-column: 2; } }
</style>
