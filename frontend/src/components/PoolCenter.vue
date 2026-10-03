<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Boxes, Check, Plus, RefreshCw, RotateCw } from 'lucide-vue-next';
import { api } from '../api';
import type { CoinPool } from '../types';

const pools = ref<CoinPool[]>([]);
const selectedId = ref('');
const kind = ref<'all' | 'research' | 'backtest' | 'paper'>('all');
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const showCreate = ref(false);
const form = ref({ name: '', kind: 'research', venue_ids: 'binance,bybit', symbols: 'BTC/USDT,ETH/USDT', interval: 'all', description: '' });
const poolKinds: { value: 'all' | 'research' | 'backtest' | 'paper'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'research', label: '研究' },
  { value: 'backtest', label: '回测' },
  { value: 'paper', label: '模拟盘' },
];

const selected = computed(() => pools.value.find((pool) => pool.pool_id === selectedId.value) || pools.value[0] || null);
const filtered = computed(() => kind.value === 'all' ? pools.value : pools.value.filter((pool) => pool.kind === kind.value));
const totalDatasets = computed(() => pools.value.reduce((sum, pool) => sum + pool.dataset_count, 0));
const kindLabel = (value: string) => ({ research: '研究池', backtest: '回测池', paper: '模拟盘池' }[value] || value);
const coverageLabel = (value: string) => value === 'COMPLETE' ? '双市场覆盖' : '单市场覆盖';
const formatDate = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const setKind = (value: 'all' | 'research' | 'backtest' | 'paper') => { kind.value = value; };

const loadPools = async () => {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await api.get<{ items: CoinPool[] }>('/pools');
    pools.value = data.items;
    if (!selectedId.value || !pools.value.some((pool) => pool.pool_id === selectedId.value)) selectedId.value = pools.value[0]?.pool_id || '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '币池目录读取失败';
  } finally {
    loading.value = false;
  }
};

const refreshSelected = async () => {
  if (!selected.value) return;
  loading.value = true;
  try {
    const { data } = await api.post<CoinPool>(`/pools/${selected.value.pool_id}/refresh`);
    pools.value = pools.value.map((pool) => pool.pool_id === data.pool_id ? data : pool);
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '币池刷新失败';
  } finally {
    loading.value = false;
  }
};

const createPool = async () => {
  saving.value = true;
  error.value = '';
  try {
    const { data } = await api.post<CoinPool>('/pools', {
      ...form.value,
      venue_ids: form.value.venue_ids.split(',').map((item) => item.trim()).filter(Boolean),
      symbols: form.value.symbols.split(',').map((item) => item.trim()).filter(Boolean),
    });
    pools.value = [data, ...pools.value];
    selectedId.value = data.pool_id;
    showCreate.value = false;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '币池创建失败';
  } finally {
    saving.value = false;
  }
};

onMounted(loadPools);
</script>

<template>
  <section class="pool-center" aria-labelledby="pool-title">
    <section class="page-heading pool-heading">
      <div><p class="kicker">UNIVERSE CONTROL / 06</p><h1 id="pool-title">币池中心</h1><p class="muted">按用途隔离研究池、回测池和模拟盘池，成员只来自已校验历史数据。</p></div>
      <div class="pool-actions"><button class="secondary-button" type="button" @click="showCreate = !showCreate"><Plus :size="15" /> 新建币池</button><button class="icon-button" type="button" title="刷新币池目录" aria-label="刷新币池目录" :disabled="loading" @click="loadPools"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></div>
    </section>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <section class="pool-metrics" aria-label="币池摘要"><article><span>池数量</span><strong>{{ pools.length }}</strong><em>系统与自定义</em></article><article><span>数据集引用</span><strong>{{ totalDatasets }}</strong><em>按池用途隔离</em></article><article><span>当前状态</span><strong class="metric-word">{{ loading ? '刷新中' : '可研究' }}</strong><em>历史质量门禁</em></article></section>

    <section v-if="showCreate" class="pool-panel create-panel" aria-labelledby="create-pool-title">
      <div class="section-heading"><div><p class="kicker">POOL BUILDER</p><h2 id="create-pool-title">新建自定义币池</h2></div><Boxes :size="18" class="section-icon" /></div>
      <form class="pool-form" @submit.prevent="createPool">
        <label><span>名称</span><input v-model="form.name" required placeholder="例如：主流币观察池" /></label>
        <label><span>用途</span><select v-model="form.kind"><option value="research">研究</option><option value="backtest">回测</option><option value="paper">模拟盘</option></select></label>
        <label><span>交易所（逗号分隔）</span><input v-model="form.venue_ids" required /></label>
        <label><span>交易对（逗号分隔）</span><input v-model="form.symbols" required /></label>
        <label><span>周期</span><select v-model="form.interval"><option value="all">全部</option><option value="1d">1 日</option><option value="1h">1 小时</option><option value="5m">5 分钟</option></select></label>
        <label class="wide"><span>备注</span><input v-model="form.description" maxlength="240" placeholder="可选" /></label>
        <button class="primary-button compact-button" type="submit" :disabled="saving"><Plus :size="15" /><span>{{ saving ? '创建中' : '保存币池' }}</span></button>
      </form>
    </section>

    <div class="pool-tabs" role="tablist" aria-label="币池用途"><button v-for="item in poolKinds" :key="item.value" type="button" :class="{ active: kind === item.value }" @click="setKind(item.value)">{{ item.label }}</button></div>

    <section class="pool-grid">
      <section class="pool-panel pool-list-panel" aria-labelledby="pool-list-title"><div class="section-heading"><div><p class="kicker">POOL DIRECTORY</p><h2 id="pool-list-title">用途目录</h2></div><span class="section-meta">{{ filtered.length }} POOLS</span></div><div class="pool-list"><button v-for="pool in filtered" :key="pool.pool_id" type="button" class="pool-row" :class="{ active: selected?.pool_id === pool.pool_id }" @click="selectedId = pool.pool_id"><span class="pool-mark"><Check :size="14" /></span><span class="pool-row-main"><strong>{{ pool.name }}</strong><small>{{ kindLabel(pool.kind) }} · {{ pool.dataset_count }} 个数据集</small></span><b>{{ pool.symbols.length }} 币</b></button><div v-if="!filtered.length" class="empty-state"><Boxes :size="22" /><p>暂无匹配币池</p></div></div></section>

      <section class="pool-panel pool-detail-panel" aria-labelledby="pool-detail-title"><div class="section-heading"><div><p class="kicker">POOL DETAIL</p><h2 id="pool-detail-title">{{ selected?.name || '等待选择' }}</h2></div><button v-if="selected" class="icon-button" type="button" title="刷新当前币池" aria-label="刷新当前币池" :disabled="loading" @click="refreshSelected"><RotateCw :size="16" :class="{ spinning: loading }" /></button></div><template v-if="selected"><p class="pool-description">{{ selected.description || '由历史数据自动生成的用途池。' }}</p><div class="detail-strip"><span><small>用途</small><strong>{{ kindLabel(selected.kind) }}</strong></span><span><small>交易所</small><strong>{{ selected.venue_ids.join(' / ').toUpperCase() || '—' }}</strong></span><span><small>周期</small><strong>{{ selected.interval || '全部' }}</strong></span></div><div class="member-table-wrap"><table class="member-table"><thead><tr><th>交易对</th><th>市场</th><th>周期</th><th>行数</th><th>覆盖</th></tr></thead><tbody><tr v-for="member in selected.members" :key="member.dataset_id"><td><strong>{{ member.symbol }}</strong><small>{{ member.quality_status }}</small></td><td>{{ member.venue_id.toUpperCase() }}</td><td>{{ member.interval }}</td><td>{{ member.row_count.toLocaleString() }}</td><td><span :class="member.coverage_status === 'COMPLETE' ? 'positive' : 'warn'">{{ coverageLabel(member.coverage_status) }}</span></td></tr><tr v-if="!selected.members.length"><td colspan="5" class="table-empty">当前用途没有匹配的已校验数据</td></tr></tbody></table></div><footer class="pool-detail-foot"><span>更新于 {{ formatDate(selected.updated_at) }}</span><span>{{ selected.complete_symbol_count }} 个币有双市场覆盖</span></footer></template><div v-else class="empty-state"><Boxes :size="22" /><p>请选择一个币池</p></div></section>
    </section>
  </section>
</template>

<style scoped>
.pool-center { display: grid; gap: 30px; }
.pool-heading { margin-bottom: 0; }
.pool-actions { display: flex; align-items: center; gap: 10px; }
.secondary-button, .compact-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 36px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 11px; color: var(--muted); background: transparent; font-size: 11px; }
.secondary-button:hover { border-color: var(--cyan); color: var(--cyan); }
.pool-metrics { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.pool-metrics article { display: grid; gap: 8px; min-height: 112px; padding: 20px 24px; border-right: 1px solid var(--line); }
.pool-metrics article:last-child { border-right: 0; }
.pool-metrics span, .pool-metrics em { color: var(--dim); font-size: 11px; font-style: normal; }
.pool-metrics strong { align-self: center; color: var(--ink); font-size: 28px; font-weight: 560; }
.pool-metrics .metric-word { font-size: 19px; }
.pool-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 19px; background: var(--panel); }
.create-panel { display: grid; gap: 16px; }
.pool-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 13px; }
.pool-form label { display: grid; gap: 7px; color: var(--muted); font-size: 11px; }
.pool-form label.wide { grid-column: span 2; }
.pool-form input, .pool-form select { min-height: 37px; width: 100%; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.pool-form input:focus, .pool-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.compact-button { align-self: end; min-height: 37px; border-color: var(--cyan); color: #11201e; background: var(--cyan); font-weight: 720; }
.compact-button:hover:not(:disabled) { background: #94f0df; }
.pool-tabs { display: flex; gap: 0; border-bottom: 1px solid var(--line); }
.pool-tabs button { border: 0; border-bottom: 2px solid transparent; padding: 10px 17px; color: var(--dim); background: transparent; font-size: 11px; }
.pool-tabs button.active { border-bottom-color: var(--cyan); color: var(--cyan); }
.pool-grid { display: grid; grid-template-columns: minmax(280px, .65fr) minmax(0, 1.35fr); gap: 13px; align-items: start; }
.pool-list { display: grid; border-top: 1px solid var(--line); }
.pool-row { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 67px; border: 0; border-bottom: 1px solid var(--line); padding: 8px 0; color: var(--muted); background: transparent; text-align: left; }
.pool-row:hover, .pool-row.active { background: var(--panel-soft); }
.pool-row.active { box-shadow: inset 2px 0 0 var(--cyan); }
.pool-mark { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid var(--line-bright); color: var(--dim); }
.pool-row.active .pool-mark { border-color: var(--cyan); color: var(--cyan); }
.pool-row-main { display: grid; gap: 5px; min-width: 0; }
.pool-row-main strong { color: var(--ink); font-size: 12px; font-weight: 570; }
.pool-row-main small, .pool-row b { color: var(--dim); font-size: 10px; font-weight: 500; }
.pool-row b { white-space: nowrap; }
.pool-description { margin: -4px 0 17px; color: var(--muted); font-size: 11px; line-height: 1.6; }
.detail-strip { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); margin-bottom: 15px; }
.detail-strip span { display: grid; gap: 5px; min-width: 0; padding: 12px 10px; border-right: 1px solid var(--line); }
.detail-strip span:last-child { border-right: 0; }
.detail-strip small { color: var(--dim); font-size: 10px; }
.detail-strip strong { overflow: hidden; color: var(--ink); font: 600 11px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.member-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.member-table { width: 100%; min-width: 580px; border-collapse: collapse; }
.member-table th, .member-table td { padding: 10px 11px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.member-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
.member-table th:first-child, .member-table td:first-child { text-align: left; }
.member-table tr:last-child td { border-bottom: 0; }
.member-table td:first-child { display: grid; gap: 3px; }
.member-table td:first-child strong { color: var(--ink); font-size: 11px; font-weight: 570; }
.member-table td:first-child small { color: var(--dim); font-size: 9px; }
.positive { color: var(--cyan) !important; }
.warn { color: var(--amber) !important; }
.pool-detail-foot { display: flex; justify-content: space-between; gap: 12px; margin-top: 13px; color: var(--dim); font-size: 10px; }
.empty-state { display: grid; justify-items: center; gap: 8px; min-height: 150px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
.table-empty { padding: 28px !important; color: var(--dim) !important; text-align: center !important; }
@media (max-width: 900px) { .pool-grid { grid-template-columns: 1fr; } }
@media (max-width: 680px) { .pool-actions .secondary-button { display: none; } .pool-metrics, .pool-form { grid-template-columns: 1fr; } .pool-metrics article { min-height: 82px; border-right: 0; border-bottom: 1px solid var(--line); } .pool-metrics article:last-child { border-bottom: 0; } .pool-form label.wide { grid-column: auto; } .pool-tabs { overflow-x: auto; } .pool-tabs button { flex: 0 0 auto; padding-inline: 13px; } .detail-strip { grid-template-columns: 1fr; } .detail-strip span { border-right: 0; border-bottom: 1px solid var(--line); } .detail-strip span:last-child { border-bottom: 0; } .pool-detail-foot { align-items: start; flex-direction: column; } }
</style>
