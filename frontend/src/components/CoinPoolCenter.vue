<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Check, Coins, Plus, RefreshCw, Trash2, Undo2 } from 'lucide-vue-next';
import { api } from '../api';

interface CoinPool {
  pool_id: string;
  name: string;
  pool_type: 'static' | 'top_volume' | 'low_volatility';
  role: 'candidate' | 'trading';
  confirmed: boolean;
  venue_id: string;
  rule: Record<string, any>;
  current_members: string[];
  snapshots: { snapshot_id: string; taken_at: string; member_count: number; members: string[] }[];
  created_at: string;
  updated_at: string;
  last_refresh_at: string | null;
}

const pools = ref<CoinPool[]>([]);
const selectedId = ref('');
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const showCreate = ref(false);
const form = ref({
  name: '', pool_type: 'top_volume', venue_id: 'binance',
  top_n: 20, max_daily_volatility: 0.03, symbols: 'BTC/USDT,ETH/USDT,SOL/USDT',
});

const typeLabel = (t: string) => ({ static: '手动', top_volume: '24h成交额TopN', low_volatility: '近7天低波动' }[t] || t);
const selected = computed(() => pools.value.find((p) => p.pool_id === selectedId.value) || pools.value[0] || null);

const load = async () => {
  loading.value = true; error.value = '';
  try {
    const { data } = await api.get<{ items: CoinPool[] }>('/coin-pools');
    pools.value = data.items;
    if (!selectedId.value || !pools.value.some((p) => p.pool_id === selectedId.value)) selectedId.value = pools.value[0]?.pool_id || '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '动态币池读取失败';
  } finally { loading.value = false; }
};

const create = async () => {
  saving.value = true; error.value = '';
  try {
    const { data } = await api.post<CoinPool>('/coin-pools', {
      name: form.value.name,
      pool_type: form.value.pool_type,
      venue_id: form.value.venue_id,
      rule: form.value.pool_type === 'low_volatility'
        ? { top_n: form.value.top_n, max_daily_volatility: form.value.max_daily_volatility }
        : { top_n: form.value.top_n },
      symbols: form.value.symbols.split(/[,，\s]+/).filter(Boolean),
    });
    pools.value = [...pools.value, data];
    selectedId.value = data.pool_id;
    showCreate.value = false;
    form.value.name = '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '创建失败';
  } finally { saving.value = false; }
};

const refreshPool = async (pool: CoinPool) => {
  loading.value = true; error.value = '';
  try {
    const { data } = await api.post<CoinPool>(`/coin-pools/${pool.pool_id}/refresh`);
    pools.value = pools.value.map((p) => (p.pool_id === data.pool_id ? data : p));
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '刷新失败';
  } finally { loading.value = false; }
};

const confirmPool = async (pool: CoinPool) => {
  if (!window.confirm(`确认将「${pool.name}」从候选池升级为交易池？升级后策略实例可选用该池逐币运行。`)) return;
  loading.value = true; error.value = '';
  try {
    const { data } = await api.post<CoinPool>(`/coin-pools/${pool.pool_id}/confirm`);
    pools.value = pools.value.map((p) => (p.pool_id === data.pool_id ? data : p));
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '确认失败';
  } finally { loading.value = false; }
};

const demotePool = async (pool: CoinPool) => {
  loading.value = true; error.value = '';
  try {
    const { data } = await api.post<CoinPool>(`/coin-pools/${pool.pool_id}/demote`);
    pools.value = pools.value.map((p) => (p.pool_id === data.pool_id ? data : p));
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '降级失败';
  } finally { loading.value = false; }
};

const deletePool = async (pool: CoinPool) => {
  if (!window.confirm(`删除币池「${pool.name}」？`)) return;
  loading.value = true; error.value = '';
  try {
    await api.delete(`/coin-pools/${pool.pool_id}`);
    pools.value = pools.value.filter((p) => p.pool_id !== pool.pool_id);
    selectedId.value = pools.value[0]?.pool_id || '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '删除失败';
  } finally { loading.value = false; }
};

onMounted(load);
</script>

<template>
  <section class="coin-pool-center" aria-label="动态币池">
    <div class="section-heading">
      <div>
        <p class="kicker">COIN POOLS</p>
        <h2>动态币池</h2>
        <p class="muted">行情 → 筛选 → 候选池 → 人工确认 → 交易池 → 策略实例。候选池不能直接交易。</p>
      </div>
      <div class="heading-actions">
        <button class="btn" type="button" @click="load" :disabled="loading"><RefreshCw :size="14" /> 刷新</button>
        <button class="btn primary" type="button" @click="showCreate = !showCreate"><Plus :size="14" /> 新建币池</button>
      </div>
    </div>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>

    <div v-if="showCreate" class="create-form">
      <label>名称 <input v-model="form.name" placeholder="如：主流成交额池" /></label>
      <label>类型
        <select v-model="form.pool_type">
          <option value="top_volume">24h成交额 TopN</option>
          <option value="low_volatility">近7天低波动</option>
          <option value="static">手动</option>
        </select>
      </label>
      <label>市场
        <select v-model="form.venue_id">
          <option value="binance">Binance</option>
          <option value="okx">OKX</option>
          <option value="bybit">Bybit</option>
        </select>
      </label>
      <label v-if="form.pool_type !== 'static'">数量 TopN <input v-model.number="form.top_n" type="number" min="1" max="100" /></label>
      <label v-if="form.pool_type === 'low_volatility'">最大日波动率 <input v-model.number="form.max_daily_volatility" type="number" step="0.005" min="0.005" max="0.5" /></label>
      <label v-if="form.pool_type === 'static'" class="wide">币种（逗号分隔） <input v-model="form.symbols" placeholder="BTC/USDT,ETH/USDT" /></label>
      <div class="form-actions wide">
        <button class="btn primary" type="button" :disabled="saving || !form.name" @click="create">创建（从候选池开始）</button>
      </div>
    </div>

    <div v-if="!pools.length && !loading" class="empty">还没有币池，点右上角「新建币池」开始。</div>

    <div class="pool-layout">
      <div class="pool-list">
        <article v-for="pool in pools" :key="pool.pool_id" class="pool-card" :class="{ active: selected?.pool_id === pool.pool_id }" @click="selectedId = pool.pool_id">
          <div class="pool-top">
            <strong>{{ pool.name }}</strong>
            <span class="role-badge" :class="pool.role">{{ pool.role === 'trading' ? '交易池' : '候选池' }}</span>
          </div>
          <div class="pool-meta">
            <span>{{ typeLabel(pool.pool_type) }}</span> · <span>{{ pool.venue_id }}</span> ·
            <span>{{ pool.current_members.length }} 个币种</span>
          </div>
          <div class="pool-actions">
            <button class="mini-btn" type="button" title="刷新成分" @click.stop="refreshPool(pool)"><RefreshCw :size="12" /></button>
            <button v-if="pool.role === 'candidate'" class="mini-btn ok" type="button" title="人工确认升级为交易池" @click.stop="confirmPool(pool)"><Check :size="12" /> 确认</button>
            <button v-else class="mini-btn" type="button" title="降级回候选池" @click.stop="demotePool(pool)"><Undo2 :size="12" /></button>
            <button class="mini-btn danger" type="button" title="删除" @click.stop="deletePool(pool)"><Trash2 :size="12" /></button>
          </div>
        </article>
      </div>

      <div v-if="selected" class="pool-detail">
        <h3><Coins :size="15" /> {{ selected.name }} <span class="role-badge" :class="selected.role">{{ selected.role === 'trading' ? '交易池' : '候选池' }}</span></h3>
        <p class="muted" v-if="selected.role === 'candidate'">候选池只能做研究/回测，不能直接交易。点「确认」人工升级为交易池后，策略实例可选用。</p>
        <h4>当前成分（{{ selected.current_members.length }}）</h4>
        <div class="member-chips">
          <span v-for="m in selected.current_members" :key="m" class="chip">{{ m }}</span>
          <span v-if="!selected.current_members.length" class="muted">暂无成分，先刷新</span>
        </div>
        <h4>成分快照（回测防未来函数）</h4>
        <div v-if="!selected.snapshots.length" class="muted">暂无快照</div>
        <div v-for="snap in [...selected.snapshots].reverse().slice(0, 5)" :key="snap.snapshot_id" class="snap-row">
          <span>{{ new Date(snap.taken_at).toLocaleString('zh-CN', { hour12: false }) }}</span>
          <span>{{ snap.member_count }} 个币种</span>
          <code>{{ snap.snapshot_id }}</code>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.coin-pool-center { display: grid; gap: 14px; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.kicker { color: var(--dim); font-size: 11px; letter-spacing: 2px; margin: 0 0 4px; }
.section-heading h2 { margin: 0; font-size: 20px; }
.muted { color: var(--dim); font-size: 12px; }
.heading-actions { display: flex; gap: 8px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; }
.btn.primary { background: var(--accent, #2196f3); border-color: transparent; color: #fff; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.inline-error { color: #f0433a; font-size: 13px; padding: 8px 12px; border: 1px solid rgba(240,67,58,.4); border-radius: 6px; }
.create-form { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; }
.create-form label { display: grid; gap: 4px; font-size: 12px; color: var(--dim); }
.create-form label.wide, .form-actions.wide { grid-column: 1 / -1; }
.create-form input, .create-form select { padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--input-bg, #13191b); color: var(--text); font-size: 13px; }
.form-actions { display: flex; justify-content: flex-end; }
.empty { color: var(--dim); padding: 24px; text-align: center; border: 1px dashed var(--line); border-radius: 8px; }
.pool-layout { display: grid; grid-template-columns: 340px 1fr; gap: 12px; }
.pool-list { display: grid; gap: 10px; align-content: start; }
.pool-card { padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }
.pool-card.active { border-color: var(--accent, #2196f3); }
.pool-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.role-badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.role-badge.candidate { background: rgba(255,152,0,.15); color: #ff9800; border: 1px solid rgba(255,152,0,.4); }
.role-badge.trading { background: rgba(31,170,83,.15); color: #1faa53; border: 1px solid rgba(31,170,83,.4); }
.pool-meta { font-size: 12px; color: var(--dim); margin-bottom: 8px; }
.pool-actions { display: flex; gap: 6px; }
.mini-btn { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; font-size: 11px; border: 1px solid var(--line); border-radius: 4px; background: transparent; color: var(--muted); cursor: pointer; }
.mini-btn.ok { color: #1faa53; border-color: rgba(31,170,83,.4); }
.mini-btn.danger { color: #f0433a; }
.pool-detail { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; }
.pool-detail h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; font-size: 15px; }
.pool-detail h4 { margin: 14px 0 8px; font-size: 13px; }
.member-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 3px 10px; font-size: 12px; border: 1px solid var(--line); border-radius: 12px; }
.snap-row { display: flex; gap: 12px; align-items: center; font-size: 12px; padding: 6px 0; border-bottom: 1px solid var(--line); color: var(--muted); }
.snap-row code { font-size: 11px; color: var(--dim); }
@media (max-width: 900px) { .pool-layout { grid-template-columns: 1fr; } .create-form { grid-template-columns: 1fr; } }
</style>
