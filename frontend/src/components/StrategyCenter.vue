<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ArrowRight, Check, FlaskConical, RefreshCw, ShieldCheck } from 'lucide-vue-next';
import { api } from '../api';
import type { StrategyDefinition, StrategyManagement } from '../types';
import StrategyOrchestrationCenter from './StrategyOrchestrationCenter.vue';

const emit = defineEmits<{ openBacktest: [] }>();
const strategies = ref<StrategyDefinition[]>([]);
const management = ref<StrategyManagement[]>([]);
const loading = ref(false);
const saving = ref('');
const error = ref('');
const availableCount = computed(() => strategies.value.filter((item) => item.enabled !== false && item.modes.includes('backtest')).length);
const enabledCount = computed(() => management.value.filter((item) => item.enabled).length);

const loadStrategies = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [catalogResponse, managementResponse] = await Promise.all([
      api.get<{ items: StrategyDefinition[] }>('/strategies/catalog'),
      api.get<{ items: StrategyManagement[] }>('/strategies/management'),
    ]);
    strategies.value = catalogResponse.data.items;
    management.value = managementResponse.data.items;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略目录读取失败';
  } finally {
    loading.value = false;
  }
};

const toggleStrategy = async (strategy: StrategyDefinition) => {
  const next = strategy.enabled === false;
  saving.value = strategy.strategy_id;
  error.value = '';
  try {
    const { data } = await api.patch<StrategyManagement>(`/strategies/management/${strategy.strategy_id}`, { enabled: next });
    strategy.enabled = data.enabled;
    management.value = management.value.map((item) => item.strategy_id === data.strategy_id ? data : item);
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '策略启停保存失败';
  } finally {
    saving.value = '';
  }
};

const modeLabel = (mode: string) => ({ research: '研究', backtest: '回测', paper: '模拟', 'sell-only': '仅卖出' }[mode] || mode);

onMounted(loadStrategies);
</script>

<template>
  <section class="strategy-center" aria-labelledby="strategy-title">
    <section class="page-heading strategy-heading">
      <div><p class="kicker">RESEARCH LAB / 04</p><h1 id="strategy-title">策略中心</h1><p class="muted">策略定义与回测参数分离保存，当前先开放可复现的价格策略基线。</p></div>
      <div class="strategy-actions"><span class="heading-stamp"><ShieldCheck :size="14" /> 真实执行关闭</span><button class="icon-button" type="button" title="刷新策略目录" aria-label="刷新策略目录" :disabled="loading" @click="loadStrategies"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></div>
    </section>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <section class="strategy-metrics" aria-label="策略摘要"><article><span>可回测策略</span><strong>{{ availableCount }}</strong><em>启用的内置策略</em></article><article><span>已启用策略</span><strong>{{ enabledCount }}</strong><em>服务端持久化配置</em></article><article><span>交易模式</span><strong class="metric-word">DISABLED</strong><em>真实执行强制关闭</em></article></section>

    <section class="strategy-list" aria-label="策略目录">
      <article v-for="(strategy, index) in strategies" :key="strategy.strategy_id" class="strategy-row">
        <div class="strategy-number">{{ String(index + 1).padStart(2, '0') }}</div>
        <div class="strategy-icon"><FlaskConical :size="18" /></div>
        <div class="strategy-main"><div class="strategy-title-line"><h2>{{ strategy.name }}</h2><span class="state-pill" :class="strategy.enabled === false ? 'disabled' : 'connected'">{{ strategy.enabled === false ? '已停用' : '可用' }}</span></div><p>{{ strategy.description }}</p><div class="strategy-tags"><span v-for="mode in strategy.modes" :key="mode">{{ modeLabel(mode) }}</span><span v-for="parameter in strategy.parameter_schema || []" :key="String(parameter.key)">参数 · {{ String(parameter.label || parameter.key) }}</span></div></div>
        <label class="strategy-toggle" :class="{ saving: saving === strategy.strategy_id }"><input type="checkbox" :checked="strategy.enabled !== false" :disabled="saving === strategy.strategy_id" @change="toggleStrategy(strategy)" /><span class="toggle-box"><Check :size="12" /></span><span>{{ strategy.enabled === false ? '启用' : '停用' }}</span></label>
        <button class="strategy-open" type="button" title="使用该目录进入回测中心" @click="emit('openBacktest')">回测 <ArrowRight :size="15" /></button>
      </article>
      <div v-if="!loading && !strategies.length" class="strategy-empty"><FlaskConical :size="22" /><p>暂无策略目录</p></div>
      <div v-if="loading" class="strategy-empty"><RefreshCw :size="20" class="spinning" /><p>正在读取策略目录</p></div>
    </section>

    <StrategyOrchestrationCenter />
  </section>
</template>

<style scoped>
.strategy-center { display: grid; gap: 30px; }
.strategy-heading { margin-bottom: 0; }
.strategy-actions { display: flex; align-items: center; gap: 12px; }
.strategy-metrics { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.strategy-metrics article { display: grid; gap: 8px; min-height: 120px; padding: 20px 24px; border-right: 1px solid var(--line); }
.strategy-metrics article:last-child { border-right: 0; }
.strategy-metrics span, .strategy-metrics em { color: var(--dim); font-size: 11px; }
.strategy-metrics em { font-style: normal; }
.strategy-metrics strong { align-self: center; color: var(--ink); font-size: 28px; font-weight: 560; }
.strategy-metrics .metric-word { font-size: 20px; }
.strategy-list { display: grid; border-top: 1px solid var(--line); }
.strategy-row { display: grid; grid-template-columns: 38px 38px minmax(0, 1fr) auto auto; align-items: center; gap: 16px; min-height: 126px; border-bottom: 1px solid var(--line); }
.strategy-number { color: var(--amber); font-size: 11px; letter-spacing: .1em; }
.strategy-icon { display: grid; place-items: center; width: 33px; height: 33px; border: 1px solid var(--line-bright); color: var(--cyan); }
.strategy-main { min-width: 0; }
.strategy-title-line { display: flex; align-items: center; gap: 10px; }
.strategy-title-line h2 { margin: 0; color: var(--ink); font-size: 15px; font-weight: 570; }
.strategy-main p { margin: 8px 0 10px; color: var(--muted); font-size: 11px; line-height: 1.6; }
.strategy-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.strategy-tags span { border: 1px solid var(--line); padding: 4px 7px; color: var(--dim); font-size: 9px; }
.strategy-toggle { display: inline-flex; align-items: center; gap: 6px; color: var(--dim); font-size: 9px; white-space: nowrap; }
.strategy-toggle input { position: absolute; opacity: 0; pointer-events: none; }
.toggle-box { display: grid; place-items: center; width: 18px; height: 18px; border: 1px solid var(--line-bright); color: transparent; background: #13191b; }
.strategy-toggle input:checked + .toggle-box { border-color: var(--cyan); color: #11201e; background: var(--cyan); }
.strategy-toggle input:focus-visible + .toggle-box { outline: 2px solid var(--cyan); outline-offset: 2px; }
.strategy-toggle.saving { opacity: .55; }
.strategy-open { display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; color: var(--muted); background: transparent; font-size: 10px; }
.strategy-open:hover { border-color: var(--cyan); color: var(--cyan); }
.strategy-empty { display: grid; justify-items: center; gap: 8px; min-height: 160px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); }
.strategy-empty p { margin: 0; color: var(--muted); font-size: 12px; }
@media (max-width: 700px) { .strategy-actions .heading-stamp { display: none; } .strategy-metrics { grid-template-columns: 1fr; } .strategy-metrics article { min-height: 82px; border-right: 0; border-bottom: 1px solid var(--line); } .strategy-metrics article:last-child { border-bottom: 0; } .strategy-row { grid-template-columns: 30px 34px minmax(0, 1fr); gap: 10px; padding: 16px 0; } .strategy-toggle { grid-column: 3; justify-self: start; } .strategy-open { grid-column: 3; justify-self: start; } }
</style>
