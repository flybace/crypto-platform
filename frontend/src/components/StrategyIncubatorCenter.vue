<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ArrowRight, BarChart3, Check, Database, FlaskConical, ListChecks, RefreshCw, Sprout } from 'lucide-vue-next';
import { api } from '../api';
import type { IncubatorScreen, StrategyIncubatorPool, StrategyIncubatorSnapshot } from '../types';

const emit = defineEmits<{ openResearch: [] }>();
const pools = ref<StrategyIncubatorPool[]>([]);
const screens = ref<IncubatorScreen[]>([]);
const summary = ref<StrategyIncubatorSnapshot['summary']>({ incubator_total: 0, incubator_items: 0, ready_for_retest: 0, collecting: 0, stage_thresholds: {} });
const activePoolId = ref('');
const loading = ref(false);
const saving = ref('');
const error = ref('');
const message = ref('');
const showProcessed = ref(false);

const activePool = computed(() => pools.value.find((pool) => pool.pool_id === activePoolId.value) || pools.value[0] || null);
const pendingScreens = computed(() => screens.value.filter((screen) => !screen.already_in_incubator && screen.new_candidate_count > 0));
const processedScreens = computed(() => screens.value.filter((screen) => screen.already_in_incubator || screen.new_candidate_count <= 0));
const visibleScreens = computed(() => showProcessed.value ? processedScreens.value : pendingScreens.value);
const readyPools = computed(() => pools.value.filter((pool) => pool.stage.key === 'ready_for_retest'));

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await api.get<StrategyIncubatorSnapshot>('/incubators', { params: { limit: 100 } });
    pools.value = data.items || [];
    screens.value = data.recent_screens || [];
    summary.value = data.summary;
    if (!activePoolId.value || !pools.value.some((pool) => pool.pool_id === activePoolId.value)) activePoolId.value = pools.value[0]?.pool_id || '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略孵化池读取失败';
  } finally {
    loading.value = false;
  }
};

const writeScreen = async (screen: IncubatorScreen) => {
  saving.value = screen.run_id;
  error.value = '';
  message.value = '';
  try {
    const { data } = await api.post<{ pool: StrategyIncubatorPool; message: string }>(`/incubators/from-screen/${encodeURIComponent(screen.run_id)}`, { mode: 'append', limit: 500 });
    activePoolId.value = data.pool.pool_id;
    message.value = data.message || '筛选结果已写入策略孵化池';
    await load();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '写入策略孵化池失败';
  } finally {
    saving.value = '';
  }
};

const openResearch = (pool: StrategyIncubatorPool) => {
  window.localStorage.setItem('crypto.research-focus', JSON.stringify({
    mode: 'portfolio',
    pool_id: pool.source_pool_id,
    screening_run_id: pool.latest_screen_run_id,
    incubator_pool_id: pool.pool_id,
  }));
  emit('openResearch');
};

const formatNumber = (value: string | number, digits = 2) => {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(number) : '—';
};
const formatPct = (value: string | number) => `${Number(value) >= 0 ? '+' : ''}${formatNumber(value)}%`;
const formatTime = (value: string | null | undefined) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false }) : '—';
const stageLabel = (key: string) => ({ collecting: '收集中', sample_validation: '样本验证', ready_for_retest: '可重测' }[key] || key);

onMounted(load);
</script>

<template>
  <section class="incubator-center" aria-labelledby="incubator-title">
    <section class="page-heading incubator-heading">
      <div><p class="kicker">STRATEGY PIPELINE / 08</p><h1 id="incubator-title">策略孵化池</h1><p class="muted">把指标筛选命中沉淀为候选集合，再用同一批数据进入研究和组合回测。</p></div>
      <button class="icon-button" type="button" title="刷新策略孵化池" aria-label="刷新策略孵化池" :disabled="loading" @click="load"><RefreshCw :size="17" :class="{ spinning: loading }" /></button>
    </section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="message" class="inline-success" role="status">{{ message }}</div>

    <section class="incubator-metrics" aria-label="孵化池摘要">
      <article><span>孵化池</span><strong>{{ summary.incubator_total }}</strong><em>按来源币池归档</em></article>
      <article><span>候选数据集</span><strong>{{ summary.incubator_items }}</strong><em>交易所与周期独立</em></article>
      <article><span>可重测</span><strong class="positive">{{ summary.ready_for_retest }}</strong><em>达到 {{ summary.stage_thresholds.retest || 6 }} 条基线</em></article>
      <article><span>待收集</span><strong class="warn">{{ summary.collecting }}</strong><em>{{ readyPools.length ? '仍可继续补充' : '等待筛选结果' }}</em></article>
    </section>

    <section class="incubator-layout">
      <section class="incubator-panel pool-rail" aria-labelledby="incubator-list-title">
        <div class="section-heading"><div><p class="kicker">CANDIDATE POOLS</p><h2 id="incubator-list-title">候选池目录</h2></div><span class="section-meta">{{ pools.length }} POOLS</span></div>
        <div v-if="!pools.length" class="incubator-empty"><Sprout :size="23" /><p>还没有孵化池</p><small>先在筛选中心产生通过项。</small></div>
        <div v-else class="pool-rail-list">
          <button v-for="pool in pools" :key="pool.pool_id" type="button" class="pool-rail-row" :class="{ active: activePool?.pool_id === pool.pool_id }" @click="activePoolId = pool.pool_id">
            <span class="rail-mark"><Check :size="13" /></span><span><strong>{{ pool.name }}</strong><small>{{ pool.source_pool_id }} · {{ pool.strategy_id }}</small></span><b>{{ pool.candidate_count }}</b><em>{{ stageLabel(pool.stage.key) }}</em>
          </button>
        </div>
      </section>

      <section v-if="activePool" class="incubator-panel pool-detail" aria-labelledby="incubator-detail-title">
        <header class="detail-header"><div><p class="kicker">{{ activePool.pool_id }}</p><h2 id="incubator-detail-title">{{ activePool.name }}</h2><p>{{ activePool.description }}</p></div><div class="detail-actions"><button class="secondary-button" type="button" @click="openResearch(activePool)"><BarChart3 :size="14" /> 进入研究</button></div></header>
        <div class="stage-strip"><div><Sprout :size="18" /><strong>{{ activePool.stage.label }}</strong><span>{{ activePool.stage.summary }}</span></div><b>{{ activePool.candidate_count }} / {{ summary.stage_thresholds.retest || 6 }}</b><i><em :style="{ width: `${activePool.stage.progress}%` }" /></i></div>
        <div class="source-strip"><span><small>来源币池</small><strong>{{ activePool.source_pool_id || '—' }}</strong></span><span><small>最近筛选</small><strong>{{ activePool.latest_screen_run_id?.slice(0, 12) || '—' }}</strong></span><span><small>交易所</small><strong>{{ activePool.venue_ids.join(' / ').toUpperCase() || '—' }}</strong></span><span><small>候选币种</small><strong>{{ activePool.symbol_count }}</strong></span></div>
        <div class="candidate-table-wrap"><table class="candidate-table"><thead><tr><th>标的</th><th>来源</th><th>评分</th><th>区间收益</th><th>波动</th><th>回撤</th><th>更新时间</th></tr></thead><tbody><tr v-for="item in activePool.items" :key="item.candidate_id"><td><strong>{{ item.symbol }}</strong><small>{{ item.venue_id.toUpperCase() }} · {{ item.interval }} · {{ item.dataset_id.slice(-12) }}</small></td><td>{{ item.source_run_id.slice(0, 12) }}</td><td>{{ formatNumber(item.score, 3) }}</td><td :class="Number(item.period_return_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(item.period_return_pct) }}</td><td>{{ formatNumber(item.volatility_pct) }}%</td><td class="negative">-{{ formatNumber(item.max_drawdown_pct) }}%</td><td>{{ formatTime(item.updated_at) }}</td></tr><tr v-if="!activePool.items.length"><td colspan="7" class="table-empty">当前孵化池没有候选数据集</td></tr></tbody></table></div>
        <footer class="detail-foot"><span><Database :size="13" /> 只保存研究候选，不生成交易指令</span><span>更新于 {{ formatTime(activePool.updated_at) }}</span></footer>
      </section>
      <section v-else class="incubator-panel incubator-empty large"><Sprout :size="28" /><p>选择一个孵化池查看候选</p></section>
    </section>

    <section class="incubator-panel screen-panel" aria-labelledby="screen-source-title">
      <div class="section-heading"><div><p class="kicker">SCREENING HAND-OFF</p><h2 id="screen-source-title">最近筛选结果</h2></div><div class="screen-actions"><span class="section-meta">待写入 {{ pendingScreens.length }} / 已处理 {{ processedScreens.length }}</span><button class="secondary-button" type="button" @click="showProcessed = !showProcessed">{{ showProcessed ? '看待写入' : '看已处理' }}</button></div></div>
      <div v-if="!visibleScreens.length" class="incubator-empty compact"><ListChecks :size="20" /><p>{{ showProcessed ? '暂无已处理筛选记录' : '暂无待写入筛选记录' }}</p></div>
      <div v-else class="screen-list"><article v-for="screen in visibleScreens" :key="screen.run_id" class="screen-row"><div class="screen-main"><span class="screen-icon"><ListChecks :size="15" /></span><div><strong>{{ screen.pool_id }}</strong><small>{{ screen.interval }} · {{ screen.lookback }} 根 · {{ formatTime(screen.created_at) }}</small><em>{{ screen.already_in_incubator ? '已进入候选池' : screen.new_candidate_count ? `可新增 ${screen.new_candidate_count} 条` : '没有新增候选' }} · 共 {{ screen.candidate_count }} 条通过</em></div></div><b>{{ screen.candidate_count }}</b><button class="secondary-button" type="button" :disabled="!!saving || screen.already_in_incubator || screen.new_candidate_count <= 0" @click="writeScreen(screen)"><RefreshCw v-if="saving === screen.run_id" :size="14" class="spinning" /><ArrowRight v-else :size="14" />{{ saving === screen.run_id ? '写入中' : screen.already_in_incubator ? '已入池' : '写入孵化池' }}</button></article></div>
    </section>
    <p class="incubator-note"><FlaskConical :size="13" /> 孵化池是筛选到研究的中间层；进入研究后仍必须通过历史质量、回测、模拟盘和风险门禁，当前不会触发真实交易。</p>
  </section>
</template>

<style scoped>
.incubator-center { display: grid; gap: 30px; }
.incubator-heading { margin-bottom: 0; }
.inline-success { margin: 0; border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .08); font-size: 12px; }
.incubator-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.incubator-metrics article { display: grid; gap: 8px; min-height: 112px; padding: 19px 21px; border-right: 1px solid var(--line); }
.incubator-metrics article:last-child { border-right: 0; }
.incubator-metrics span, .incubator-metrics em { color: var(--dim); font-size: 10px; font-style: normal; }
.incubator-metrics strong { align-self: center; color: var(--ink); font-size: 27px; font-weight: 560; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.warn { color: var(--amber) !important; }
.incubator-layout { display: grid; grid-template-columns: minmax(260px, .6fr) minmax(0, 1.4fr); gap: 14px; align-items: start; }
.incubator-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 18px; background: var(--panel); }
.pool-rail-list { display: grid; border-top: 1px solid var(--line); }
.pool-rail-row { display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; gap: 9px; align-items: center; min-height: 68px; border: 0; border-bottom: 1px solid var(--line); padding: 8px 0; color: var(--muted); background: transparent; text-align: left; }
.pool-rail-row:hover, .pool-rail-row.active { background: var(--panel-soft); }
.pool-rail-row.active { box-shadow: inset 2px 0 0 var(--cyan); }
.pool-rail-row > span:nth-child(2) { display: grid; gap: 5px; min-width: 0; }
.pool-rail-row strong, .pool-rail-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pool-rail-row strong { color: var(--ink); font-size: 11px; font-weight: 570; }
.pool-rail-row small, .pool-rail-row em { color: var(--dim); font-size: 9px; font-style: normal; }
.pool-rail-row b { color: var(--cyan); font: 600 13px Consolas, monospace; }
.pool-rail-row em { grid-column: 2 / -1; }
.rail-mark { display: grid; place-items: center; width: 23px; height: 23px; border: 1px solid var(--line-bright); color: var(--dim); }
.pool-rail-row.active .rail-mark { border-color: var(--cyan); color: var(--cyan); }
.detail-header { display: flex; justify-content: space-between; gap: 15px; margin-bottom: 18px; }
.detail-header h2 { margin: 0; color: var(--ink); font-size: 20px; font-weight: 570; }
.detail-header p:not(.kicker) { max-width: 650px; margin: 8px 0 0; color: var(--muted); font-size: 11px; line-height: 1.6; }
.detail-actions, .screen-actions { display: flex; align-items: center; gap: 10px; }
.secondary-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 34px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 7px 10px; color: var(--muted); background: transparent; font-size: 10px; white-space: nowrap; }
.secondary-button:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.secondary-button:disabled { cursor: not-allowed; opacity: .55; }
.stage-strip { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; border: 1px solid var(--line); padding: 11px; background: var(--panel-soft); }
.stage-strip > div { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: center; gap: 7px; min-width: 0; }
.stage-strip svg { color: var(--amber); }
.stage-strip span { overflow: hidden; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.stage-strip b { color: var(--cyan); font: 600 14px Consolas, monospace; white-space: nowrap; }
.stage-strip i { grid-column: 1 / -1; height: 5px; overflow: hidden; background: var(--line); }
.stage-strip em { display: block; height: 100%; background: var(--cyan); }
.source-strip { display: grid; grid-template-columns: repeat(4, 1fr); margin: 15px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.source-strip span { display: grid; gap: 5px; min-width: 0; padding: 11px 9px; border-right: 1px solid var(--line); }
.source-strip span:last-child { border-right: 0; }
.source-strip small { color: var(--dim); font-size: 9px; }
.source-strip strong { overflow: hidden; color: var(--ink); font: 600 10px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.candidate-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.candidate-table { width: 100%; min-width: 790px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.candidate-table th, .candidate-table td { padding: 9px 8px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 9px; text-align: right; white-space: nowrap; }
.candidate-table th { color: var(--dim); background: var(--panel-soft); font-size: 8px; letter-spacing: .08em; text-transform: uppercase; }
.candidate-table th:first-child, .candidate-table td:first-child { text-align: left; }
.candidate-table tr:last-child td { border-bottom: 0; }
.candidate-table td:first-child { display: grid; gap: 3px; }
.candidate-table td strong { color: var(--ink); font-size: 10px; font-weight: 570; }
.candidate-table td small { color: var(--dim); font-size: 8px; }
.detail-foot, .incubator-note { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--dim); font-size: 9px; }
.detail-foot { margin-top: 12px; }
.detail-foot span:first-child, .incubator-note { display: flex; align-items: center; gap: 6px; }
.detail-foot svg, .incubator-note svg { color: var(--amber); }
.screen-panel { padding-bottom: 12px; }
.screen-list { display: grid; border-top: 1px solid var(--line); }
.screen-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 12px; min-height: 65px; border-bottom: 1px solid var(--line); }
.screen-main { display: flex; align-items: center; gap: 9px; min-width: 0; }
.screen-main > div { display: grid; gap: 4px; min-width: 0; }
.screen-main strong, .screen-main small, .screen-main em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.screen-main strong { color: var(--ink); font-size: 11px; font-weight: 570; }
.screen-main small, .screen-main em { color: var(--dim); font-size: 9px; font-style: normal; }
.screen-icon { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid var(--line-bright); color: var(--cyan); }
.screen-row > b { color: var(--cyan); font: 600 13px Consolas, monospace; }
.incubator-empty { display: grid; justify-items: center; gap: 8px; min-height: 180px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.incubator-empty.large { min-height: 340px; }
.incubator-empty.compact { min-height: 100px; }
.incubator-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.incubator-empty small { color: var(--dim); font-size: 10px; }
.table-empty { padding: 28px !important; color: var(--dim) !important; text-align: center !important; }
.incubator-note { justify-content: start; margin: -12px 0 0; line-height: 1.6; }
@media (max-width: 900px) { .incubator-layout { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .incubator-metrics, .source-strip { grid-template-columns: 1fr 1fr; } .incubator-metrics article:nth-child(2), .source-strip span:nth-child(2) { border-right: 0; } .incubator-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } .source-strip span:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } .source-strip span:nth-child(2) { border-right: 0; } .detail-header, .screen-actions { align-items: start; flex-direction: column; } .screen-row { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; padding: 10px 0; } .screen-row .secondary-button { grid-column: 1 / -1; justify-self: start; } .screen-row > b { grid-column: 2; grid-row: 1; } .detail-foot { align-items: start; flex-direction: column; } }
@media (max-width: 430px) { .incubator-metrics, .source-strip { grid-template-columns: 1fr; } .incubator-metrics article, .source-strip span { border-right: 0 !important; border-bottom: 1px solid var(--line); } .incubator-metrics article:last-child, .source-strip span:last-child { border-bottom: 0; } }
</style>
