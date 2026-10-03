<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Activity, AlertTriangle, Briefcase, Gauge, RefreshCw, TrendingUp, Wallet } from 'lucide-vue-next';
import { api } from '../api';

interface LiveInstance {
  instance_id: string;
  venue_id: string;
  symbol: string;
  interval: string;
  strategy_id: string;
  enabled: boolean;
  last_tick_at: string | null;
  last_signal: string | null;
  trade_count: number;
  last_risk_event: string | null;
  account?: { balances: Record<string, string> } | null;
}

interface Regime {
  regime: string;
  score: number;
  factor: number;
  blocks_new_positions: boolean;
  reason: string;
}

const instances = ref<LiveInstance[]>([]);
const regime = ref<Regime | null>(null);
const funnels = ref<Record<string, string>>({});
const loading = ref(false);
const error = ref('');

const regimeLabel = (r: string) => ({ strong: '偏强', neutral: '中性', weak: '偏弱', crisis: '危机' }[r] || r);
const runningCount = computed(() => instances.value.filter((i) => i.enabled).length);
const totalTrades = computed(() => instances.value.reduce((s, i) => s + (i.trade_count || 0), 0));
const totalUsdt = computed(() => {
  let sum = 0;
  for (const inst of instances.value) {
    const usdt = inst.account?.balances?.['USDT'];
    if (usdt) sum += parseFloat(usdt) || 0;
  }
  return sum;
});
const riskEvents = computed(() =>
  instances.value
    .filter((i) => i.last_risk_event)
    .map((i) => ({ instance: `${i.venue_id} ${i.symbol}`, event: i.last_risk_event as string }))
);
const todos = computed(() => {
  const items: { level: 'warn' | 'info'; text: string }[] = [];
  if (regime.value?.blocks_new_positions) {
    items.push({ level: 'warn', text: `市场状态${regimeLabel(regime.value.regime)}：已禁止开仓` });
  }
  for (const re of riskEvents.value) {
    items.push({ level: 'warn', text: `${re.instance}: ${re.event}` });
  }
  const unrated = instances.value.filter((i) => i.enabled && !funnels.value[i.strategy_id]);
  for (const u of unrated) {
    items.push({ level: 'info', text: `${u.strategy_id} 尚未评级，建议回测后走准入漏斗` });
  }
  return items;
});

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [liveRes, regimeRes, funnelRes] = await Promise.all([
      api.get<{ instances: LiveInstance[] }>('/paper/live'),
      api.get<Regime>('/market-regime').catch(() => ({ data: null })),
      api.get<{ items: any[] }>('/strategy/funnel').catch(() => ({ data: { items: [] } })),
    ]);
    instances.value = liveRes.data.instances || [];
    regime.value = (regimeRes as any).data || null;
    const byStrategy: Record<string, string> = {};
    for (const f of (funnelRes.data.items || [])) {
      const order: Record<string, number> = { D: 0, C: 1, B: 2, A: 3, S: 4 };
      const sid = f.strategy_id as string;
      const r = f.rating as string;
      if (!byStrategy[sid] || (order[r] || 0) > (order[byStrategy[sid] as string] || 0)) {
        byStrategy[sid] = r;
      }
    }
    funnels.value = byStrategy;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '驾驶舱数据读取失败';
  } finally {
    loading.value = false;
  }
};

onMounted(load);
</script>

<template>
  <section class="cockpit" aria-label="交易驾驶舱">
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>

    <!-- 资产条 -->
    <div class="cockpit-assets">
      <article><Wallet :size="16" /><div><span>模拟总权益</span><strong>{{ totalUsdt.toFixed(2) }} <small>USDT</small></strong></div></article>
      <article><Briefcase :size="16" /><div><span>运行中策略</span><strong>{{ runningCount }} <small>/ {{ instances.length }}</small></strong></div></article>
      <article><Activity :size="16" /><div><span>累计下单</span><strong>{{ totalTrades }}</strong></div></article>
      <article><Gauge :size="16" /><div><span>市场状态</span><strong :class="'regime-' + (regime?.regime || 'none')">{{ regime ? regimeLabel(regime.regime) : '—' }}</strong><em v-if="regime">{{ regime.score.toFixed(0) }}分 · 系数×{{ regime.factor }}</em></div></article>
    </div>

    <div class="cockpit-grid">
      <!-- 策略健康度 -->
      <div class="cockpit-panel">
        <h3><TrendingUp :size="15" /> 策略健康度</h3>
        <div v-if="!instances.length" class="empty">暂无策略实例</div>
        <div v-for="inst in instances" :key="inst.instance_id" class="strategy-row">
          <span class="dot" :class="inst.enabled ? 'live' : 'dead'"></span>
          <div class="s-main">
            <strong>{{ inst.strategy_id }}</strong>
            <span>{{ inst.venue_id }} {{ inst.symbol }} {{ inst.interval }}</span>
          </div>
          <span v-if="funnels[inst.strategy_id]" class="rating-badge" :class="'rating-' + funnels[inst.strategy_id]">{{ funnels[inst.strategy_id] }}</span>
          <span v-else class="rating-badge rating-none">—</span>
          <span class="s-signal">{{ inst.last_signal || '无信号' }}</span>
        </div>
      </div>

      <!-- 待办事项 -->
      <div class="cockpit-panel">
        <h3><AlertTriangle :size="15" /> 待办 <em>{{ todos.length }}</em></h3>
        <div v-if="!todos.length" class="empty">无待办事项</div>
        <div v-for="(todo, idx) in todos" :key="idx" class="todo-row" :class="todo.level">
          <span>{{ todo.text }}</span>
        </div>
      </div>
    </div>

    <button class="refresh-btn" type="button" :disabled="loading" @click="load">
      <RefreshCw :size="14" :class="{ spinning: loading }" /> 刷新
    </button>
  </section>
</template>

<style scoped>
.cockpit { display: grid; gap: 16px; }
.cockpit-assets { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.cockpit-assets article { display: flex; gap: 10px; align-items: center; padding: 14px 16px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
.cockpit-assets article > div { display: grid; gap: 2px; }
.cockpit-assets span { color: var(--dim); font-size: 11px; }
.cockpit-assets strong { font-size: 20px; }
.cockpit-assets small { font-size: 11px; color: var(--dim); font-weight: 400; }
.cockpit-assets em { font-style: normal; color: var(--dim); font-size: 11px; }
.regime-strong { color: #1faa53; } .regime-neutral { color: var(--cyan); }
.regime-weak { color: #ff9800; } .regime-crisis { color: #f0433a; } .regime-none { color: var(--dim); }
.cockpit-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 12px; }
.cockpit-panel { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; background: var(--panel); }
.cockpit-panel h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 12px; font-size: 14px; }
.cockpit-panel h3 em { font-style: normal; color: var(--dim); font-size: 11px; }
.strategy-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.strategy-row:last-child { border-bottom: 0; }
.dot { width: 8px; height: 8px; border-radius: 50%; } .dot.live { background: #1faa53; } .dot.dead { background: var(--dim); }
.s-main { display: grid; gap: 1px; flex: 1; } .s-main span { color: var(--dim); font-size: 11px; }
.s-signal { color: var(--muted); font-size: 12px; }
.rating-badge { min-width: 24px; height: 20px; display: inline-grid; place-items: center; border-radius: 4px; font-size: 11px; font-weight: 700; padding: 0 5px; }
.rating-S { background: #ffd700; color: #000; } .rating-A { background: #1faa53; color: #fff; }
.rating-B { background: #2196f3; color: #fff; } .rating-C { background: #ff9800; color: #fff; }
.rating-D { background: #f0433a; color: #fff; } .rating-none { border: 1px dashed var(--line); color: var(--dim); }
.todo-row { padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; font-size: 13px; }
.todo-row.warn { background: rgba(240,67,58,.08); border: 1px solid rgba(240,67,58,.3); }
.todo-row.info { background: rgba(33,150,243,.08); border: 1px solid rgba(33,150,243,.3); }
.empty { color: var(--dim); font-size: 13px; padding: 12px 0; }
.refresh-btn { justify-self: start; display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer; font-size: 12px; }
@media (max-width: 800px) { .cockpit-assets { grid-template-columns: 1fr 1fr; } .cockpit-grid { grid-template-columns: 1fr; } }
</style>
