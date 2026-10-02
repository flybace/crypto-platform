<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Activity, BarChart3, Check, Filter, RefreshCw, SlidersHorizontal, Sprout } from 'lucide-vue-next';
import { api } from '../api';
import type { CoinPool, QueuedTaskResponse, ScreeningRun } from '../types';
import { isQueuedTask, resolveTaskResponse, taskStatusLabel } from '../services/taskPolling';

const emit = defineEmits<{ openBacktest: []; openIncubator: [] }>();
const pools = ref<CoinPool[]>([]);
const runs = ref<ScreeningRun[]>([]);
const current = ref<ScreeningRun | null>(null);
const poolId = ref('');
const interval = ref('1d');
const lookback = ref('90');
const minReturnPct = ref('0');
const maxVolatilityPct = ref('');
const minQuoteVolume = ref('0');
const minMomentumPct = ref('0');
const limit = ref('50');
const loading = ref(false);
const running = ref(false);
const error = ref('');
const taskMessage = ref('');
const savingIncubator = ref(false);
const success = ref('');

const selectedPool = computed(() => pools.value.find((pool) => pool.pool_id === poolId.value) || null);
const passedItems = computed(() => current.value?.items.filter((item) => item.passed) || []);
const formatNumber = (value: string | number, maximumFractionDigits = 2) => new Intl.NumberFormat('en-US', { maximumFractionDigits }).format(Number(value));
const formatPct = (value: string | number) => `${Number(value) >= 0 ? '+' : ''}${formatNumber(value)}%`;
const formatDate = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const reasonLabel = (reason: string) => ({ RETURN_BELOW_THRESHOLD: '收益不足', VOLATILITY_ABOVE_LIMIT: '波动过高', VOLUME_BELOW_THRESHOLD: '成交额不足', MOMENTUM_BELOW_THRESHOLD: '动量不足' }[reason] || reason);

const loadData = async () => {
  loading.value = true;
  error.value = '';
  success.value = '';
  try {
    const [poolResponse, runResponse] = await Promise.all([
      api.get<{ items: CoinPool[] }>('/pools'),
      api.get<{ items: ScreeningRun[] }>('/screening/runs'),
    ]);
    pools.value = poolResponse.data.items.filter((pool) => pool.kind === 'research' || pool.kind === 'backtest');
    runs.value = runResponse.data.items;
    if (!poolId.value || !pools.value.some((pool) => pool.pool_id === poolId.value)) poolId.value = pools.value[0]?.pool_id || '';
    if (!current.value && runs.value.length) current.value = runs.value[0] || null;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '筛选配置读取失败';
  } finally {
    loading.value = false;
  }
};

const writeCurrentToIncubator = async () => {
  if (!current.value || !passedItems.value.length) return;
  savingIncubator.value = true;
  error.value = '';
  success.value = '';
  try {
    const { data } = await api.post<{ message?: string }>(`/incubators/from-screen/${encodeURIComponent(current.value.run_id)}`, { mode: 'append', limit: 500 });
    success.value = data.message || '筛选结果已写入策略孵化池';
    emit('openIncubator');
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '写入策略孵化池失败';
  } finally {
    savingIncubator.value = false;
  }
};

const runScreening = async () => {
  if (!poolId.value) {
    error.value = '请先选择币池';
    return;
  }
  running.value = true;
  error.value = '';
  taskMessage.value = '';
  try {
    const response = await api.post<ScreeningRun | QueuedTaskResponse>('/screening/run', {
      pool_id: poolId.value,
      interval: interval.value,
      lookback: Number(lookback.value),
      min_return_pct: minReturnPct.value,
      max_volatility_pct: maxVolatilityPct.value || null,
      min_quote_volume: minQuoteVolume.value,
      min_momentum_pct: minMomentumPct.value,
      limit: Number(limit.value),
    });
    if (isQueuedTask(response.data)) {
      taskMessage.value = `筛选已排队 · ${response.data.task_id}`;
      const queuedRunId = response.data.run_id || '';
      const detail = await resolveTaskResponse<ScreeningRun>(response.data, {
        onUpdate: (task) => {
          const progress = task.progress.completed !== undefined
            ? ` · ${task.progress.completed}/${task.progress.total}`
            : '';
          taskMessage.value = `筛选${taskStatusLabel(task.status)}${progress}`;
        },
      });
      await loadData();
      current.value = runs.value.find((item) => item.run_id === queuedRunId) || detail;
      taskMessage.value = '筛选已完成，候选结果已重新载入';
    } else {
      current.value = response.data;
      runs.value = [response.data, ...runs.value.filter((item) => item.run_id !== response.data.run_id)].slice(0, 30);
    }
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '筛选执行失败';
  } finally {
    running.value = false;
  }
};

onMounted(loadData);
</script>

<template>
  <section class="screening-center" aria-labelledby="screening-title">
    <section class="page-heading screening-heading"><div><p class="kicker">SIGNAL RESEARCH / 07</p><h1 id="screening-title">指标筛选</h1><p class="muted">从币池中的已校验 K 线计算收益、波动、动量与成交额，生成可回测候选。</p></div><button class="icon-button" type="button" title="刷新筛选数据" aria-label="刷新筛选数据" :disabled="loading" @click="loadData"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="taskMessage" class="inline-notice" role="status" aria-live="polite">{{ taskMessage }}</div>
    <div v-if="success" class="inline-success" role="status">{{ success }}</div>

    <section class="screening-layout">
      <section class="screening-panel screening-config" aria-labelledby="screening-config-title"><div class="section-heading"><div><p class="kicker">SCREEN CONFIGURATION</p><h2 id="screening-config-title">筛选条件</h2></div><SlidersHorizontal :size="18" class="section-icon" /></div><form class="screening-form" @submit.prevent="runScreening"><label><span>币池</span><select v-model="poolId"><option v-for="pool in pools" :key="pool.pool_id" :value="pool.pool_id">{{ pool.name }} · {{ pool.member_count }} 数据集</option></select><small>{{ selectedPool?.description || '选择历史数据池' }}</small></label><div class="field-grid"><label><span>周期</span><select v-model="interval"><option value="all">全部</option><option value="1d">1 日</option><option value="1h">1 小时</option></select></label><label><span>回看根数</span><input v-model="lookback" type="number" min="2" max="10000" /></label></div><div class="field-grid"><label><span>最低区间收益 %</span><input v-model="minReturnPct" type="number" min="0" step="0.1" /></label><label><span>最低动量 %</span><input v-model="minMomentumPct" type="number" min="0" step="0.1" /></label></div><div class="field-grid"><label><span>最大年化波动 %</span><input v-model="maxVolatilityPct" type="number" min="0" step="0.1" placeholder="不限制" /></label><label><span>最低平均成交额</span><input v-model="minQuoteVolume" type="number" min="0" step="1000" /></label></div><label><span>最多返回</span><select v-model="limit"><option value="20">20 条</option><option value="50">50 条</option><option value="100">100 条</option></select></label><button class="screening-submit" type="submit" :disabled="running || !poolId"><Filter v-if="!running" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ running ? '计算中' : '运行筛选' }}</span></button></form><p class="screening-note"><Activity :size="13" /> 缺口、重复或不足两根 K 线的数据集不会进入候选。</p></section>

      <section class="screening-panel screening-result" aria-labelledby="screening-result-title"><div class="section-heading"><div><p class="kicker">LATEST CANDIDATES</p><h2 id="screening-result-title">筛选结果</h2></div><span class="section-meta">{{ current ? `${current.candidate_count} PASSED` : 'WAITING' }}</span></div><div v-if="!current" class="empty-state"><Filter :size="23" /><p>运行筛选查看候选</p></div><template v-else><div class="screening-summary"><article><span>通过</span><strong class="positive">{{ current.candidate_count }}</strong><em>/ {{ current.dataset_count }} 数据集</em></article><article><span>阻断</span><strong class="warn">{{ current.blocked_dataset_count }}</strong><em>质量或数据不足</em></article><article><span>回看</span><strong>{{ current.lookback }}</strong><em>{{ current.interval }} K 线</em></article></div><div class="candidate-table-wrap"><table class="candidate-table"><thead><tr><th>标的</th><th>末价</th><th>区间收益</th><th>波动</th><th>回撤</th><th>评分</th><th>状态</th></tr></thead><tbody><tr v-for="item in current.items" :key="item.dataset_id"><td><strong>{{ item.symbol }}</strong><small>{{ item.venue_id.toUpperCase() }} · {{ item.interval }}</small></td><td>{{ formatNumber(item.last_close, 6) }}</td><td :class="Number(item.period_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(item.period_return_pct) }}</td><td>{{ formatNumber(item.volatility_pct) }}%</td><td class="negative">-{{ formatNumber(item.max_drawdown_pct) }}%</td><td>{{ formatNumber(item.score, 3) }}</td><td><span :class="item.passed ? 'positive' : 'warn'">{{ item.passed ? '通过' : '过滤' }}</span><small v-if="!item.passed">{{ (item.reasons || []).map(reasonLabel).join(' / ') }}</small></td></tr><tr v-if="!current.items.length"><td colspan="7" class="table-empty">当前条件没有可计算数据</td></tr></tbody></table></div><footer class="screening-result-foot"><span>完成于 {{ formatDate(current.created_at) }}</span><div v-if="passedItems.length" class="screening-result-actions"><button class="secondary-button" type="button" @click="emit('openBacktest')"><BarChart3 :size="14" /> 去回测中心</button><button class="secondary-button" type="button" :disabled="savingIncubator" @click="writeCurrentToIncubator"><RefreshCw v-if="savingIncubator" :size="14" class="spinning" /><Sprout v-else :size="14" />{{ savingIncubator ? '写入中' : '写入孵化池' }}</button></div></footer></template></section>
    </section>

    <section class="screening-panel run-history" aria-labelledby="screening-history-title"><div class="section-heading"><div><p class="kicker">SCREEN ARCHIVE</p><h2 id="screening-history-title">筛选记录</h2></div><span class="section-meta">{{ runs.length }} RUNS</span></div><div v-if="!runs.length" class="empty-state compact"><Filter :size="19" /><p>还没有筛选记录</p></div><div v-else class="run-list"><button v-for="run in runs" :key="run.run_id" type="button" class="run-row" @click="current = run"><span class="run-mark"><Check :size="13" /></span><span><strong>{{ run.pool_id }}</strong><small>{{ run.interval }} · {{ run.lookback }} 根 · {{ formatDate(run.created_at) }}</small></span><b class="positive">{{ run.candidate_count }}</b><small>通过</small></button></div></section>
  </section>
</template>

<style scoped>
.screening-center { display: grid; gap: 30px; }
.screening-heading { margin-bottom: 0; }
.inline-notice { border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; line-height: 1.5; }
.screening-layout { display: grid; grid-template-columns: minmax(280px, .68fr) minmax(0, 1.32fr); gap: 13px; align-items: start; }
.screening-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 19px; background: var(--panel); }
.screening-form { display: grid; gap: 14px; }
.screening-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.screening-form input, .screening-form select { width: 100%; min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.screening-form input:focus, .screening-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.screening-form small { color: var(--dim); font-size: 10px; line-height: 1.45; }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.screening-submit { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 39px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 12px; font-weight: 720; }
.screening-submit:hover:not(:disabled) { background: #94f0df; }
.screening-submit:disabled { cursor: not-allowed; opacity: .55; }
.screening-note { display: flex; align-items: start; gap: 7px; margin: 17px 0 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.screening-note svg { flex: 0 0 auto; color: var(--amber); }
.screening-summary { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); margin-bottom: 15px; }
.screening-summary article { display: grid; gap: 6px; min-height: 91px; padding: 12px 10px; border-right: 1px solid var(--line); }
.screening-summary article:last-child { border-right: 0; }
.screening-summary span, .screening-summary em { color: var(--dim); font-size: 10px; font-style: normal; }
.screening-summary strong { align-self: center; color: var(--ink); font-size: 22px; font-weight: 560; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.warn { color: var(--amber) !important; }
.candidate-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.candidate-table { width: 100%; min-width: 760px; border-collapse: collapse; }
.candidate-table th, .candidate-table td { padding: 10px 9px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.candidate-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; letter-spacing: .07em; text-transform: uppercase; }
.candidate-table th:first-child, .candidate-table td:first-child { text-align: left; }
.candidate-table tr:last-child td { border-bottom: 0; }
.candidate-table td:first-child, .candidate-table td:last-child { display: grid; gap: 3px; }
.candidate-table td:first-child strong { color: var(--ink); font-size: 11px; font-weight: 570; }
.candidate-table small { color: var(--dim); font-size: 9px; }
.screening-result-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 14px; color: var(--dim); font-size: 10px; }
.screening-result-actions { display: flex; flex-wrap: wrap; justify-content: end; gap: 8px; }
.secondary-button { display: inline-flex; align-items: center; gap: 7px; min-height: 32px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 7px 10px; color: var(--muted); background: transparent; font-size: 10px; }
.secondary-button:hover { border-color: var(--cyan); color: var(--cyan); }
.empty-state { display: grid; justify-items: center; gap: 8px; min-height: 230px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.empty-state.compact { min-height: 84px; }
.empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
.table-empty { padding: 30px !important; color: var(--dim) !important; text-align: center !important; }
.run-history { padding-bottom: 13px; }
.run-list { display: grid; border-top: 1px solid var(--line); }
.run-row { display: grid; grid-template-columns: 27px minmax(0, 1fr) auto auto; align-items: center; gap: 10px; min-height: 56px; border: 0; border-bottom: 1px solid var(--line); color: var(--muted); background: transparent; text-align: left; }
.run-row:hover { background: var(--panel-soft); }
.run-row span:nth-child(2) { display: grid; gap: 5px; min-width: 0; }
.run-row strong { overflow: hidden; color: var(--ink); font-size: 11px; font-weight: 570; text-overflow: ellipsis; white-space: nowrap; }
.run-row small { color: var(--dim); font-size: 9px; }
.run-mark { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid var(--line-bright); color: var(--cyan); }
.run-row b { font-size: 12px; font-weight: 650; }
@media (max-width: 920px) { .screening-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .field-grid, .screening-summary { grid-template-columns: 1fr; } .screening-summary article { min-height: 72px; border-right: 0; border-bottom: 1px solid var(--line); } .screening-summary article:last-child { border-bottom: 0; } .screening-result-foot { align-items: start; flex-direction: column; } .screening-result-actions { justify-content: start; } .run-row { grid-template-columns: 27px minmax(0, 1fr) auto; } .run-row > small { display: none; } }
</style>
