<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Check, CircleAlert, Database, Moon, RefreshCw, ServerCog, ShieldCheck, Sun, X } from 'lucide-vue-next';
import { api } from '../api';
import { useTheme } from '../composables/useTheme';
import type { SystemStatus } from '../types';

const { theme, applyTheme } = useTheme();

const status = ref<SystemStatus | null>(null);
const loading = ref(false);
const error = ref('');
const overallLabel = computed(() => ({ blocked: '数据阻断', partial: '部分就绪', safe_paused: '安全暂停', ready: '就绪' }[status.value?.status || ''] || status.value?.status || '读取中'));
const load = async () => {
  loading.value = true;
  error.value = '';
  try { const { data } = await api.get<SystemStatus>('/system/status'); status.value = data; } catch (cause: any) { error.value = cause.response?.data?.detail || '系统状态读取失败'; } finally { loading.value = false; }
};
const stateClass = (value: string) => value === 'ready' || value === 'online' ? 'good' : value === 'blocked' || value === 'offline' ? 'bad' : 'warn';
const date = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false }) : '—';
onMounted(load);
</script>

<template>
  <section class="system-center" aria-labelledby="system-title">
    <section class="page-heading system-heading"><div><p class="kicker">RUNTIME READINESS / 14</p><h1 id="system-title">系统状态</h1><p class="muted">只显示当前独立运行端的真实状态；安全暂停不是故障，是真实交易尚未授权。</p></div><button class="icon-button" type="button" title="刷新系统状态" aria-label="刷新系统状态" :disabled="loading" @click="load"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <section class="system-hero"><div class="hero-status" :class="stateClass(status?.status || '')"><ServerCog :size="22" /><div><p>STANDALONE RUNTIME / {{ status?.runtime.version || '—' }}</p><h2>{{ overallLabel }}</h2><span>{{ status?.runtime.market_mode || '24/7 spot research' }} · {{ status?.runtime.execution_mode || 'DISABLED' }}</span></div></div><div class="hero-facts"><div><span>研究链路</span><strong>{{ status?.readiness.overall.research_ready ? '可用' : '等待' }}</strong></div><div><span>真实执行</span><strong class="bad">关闭</strong></div><div><span>检查时间</span><strong>{{ date(status?.checked_at) }}</strong></div></div></section>
    <section class="system-metrics"><article><span>已校验数据集</span><strong>{{ status?.data.coverage.verified_dataset_count || 0 }}<small>/ {{ status?.data.coverage.expected_dataset_count || 0 }}</small></strong><em>{{ status?.data.coverage.coverage_ratio_pct || '0.00' }}%</em></article><article><span>历史 K 线</span><strong>{{ (status?.data.coverage.row_count || 0).toLocaleString() }}</strong><em>{{ status?.data.status || '—' }}</em></article><article><span>策略启用</span><strong>{{ status?.strategy.enabled_count || 0 }}<small>/ {{ status?.strategy.strategy_count || 0 }}</small></strong><em>研究目录</em></article><article><span>模拟账户</span><strong>{{ status?.paper.execution_mode || 'PAPER' }}</strong><em>独立隔离</em></article></section>
    <section class="system-panel theme-panel" aria-labelledby="theme-title">
      <header class="section-heading"><div><p class="kicker">APPEARANCE</p><h2 id="theme-title">外观配色</h2></div></header>
      <div class="theme-options" role="radiogroup" aria-label="配色方案">
        <button type="button" role="radio" :aria-checked="theme === 'dark'" class="theme-option" :class="{ active: theme === 'dark' }" @click="applyTheme('dark')">
          <span class="theme-swatch dark-swatch"><Moon :size="16" /></span>
          <span><strong>黑色</strong><small>深色护眼，默认</small></span>
          <Check v-if="theme === 'dark'" :size="15" class="theme-check" />
        </button>
        <button type="button" role="radio" :aria-checked="theme === 'light'" class="theme-option" :class="{ active: theme === 'light' }" @click="applyTheme('light')">
          <span class="theme-swatch light-swatch"><Sun :size="16" /></span>
          <span><strong>白色</strong><small>浅色明亮</small></span>
          <Check v-if="theme === 'light'" :size="15" class="theme-check" />
        </button>
      </div>
      <p class="theme-note">配色保存在本机浏览器中，切换即时生效。</p>
    </section>
    <section class="system-layout"><section class="system-panel" aria-labelledby="services-title"><header class="section-heading"><div><p class="kicker">SERVICE MATRIX</p><h2 id="services-title">服务与数据</h2></div><Database :size="18" class="section-icon" /></header><div class="service-list"><div v-for="item in status?.services || []" :key="item.name"><span class="service-icon" :class="stateClass(item.status)"><Check v-if="item.ok" :size="14" /><X v-else :size="14" /></span><span><strong>{{ item.name }}</strong><small>{{ item.message }}</small></span><b :class="stateClass(item.status)">{{ item.status }}</b></div></div></section><section class="system-panel" aria-labelledby="readiness-title"><header class="section-heading"><div><p class="kicker">READINESS GATES</p><h2 id="readiness-title">就绪门禁</h2></div><ShieldCheck :size="18" class="section-icon" /></header><div class="check-list"><div v-for="item in status?.readiness.checks || []" :key="item.key"><span :class="item.passed ? 'good' : 'bad'"><Check v-if="item.passed" :size="14" /><CircleAlert v-else :size="14" /></span><span><strong>{{ item.label }}</strong><small>{{ item.detail }}</small></span><b :class="item.passed ? 'good' : 'bad'">{{ item.passed ? 'PASS' : 'BLOCKED' }}</b></div></div><div class="next-actions"><strong>下一步</strong><span v-for="item in status?.readiness.next_actions || []" :key="item">{{ item }}</span></div></section></section>
    <p class="system-footnote"><ShieldCheck :size="14" />状态来自服务端当前内存和持久化投影；文档、旧截图或单独 HTTP 200 不会改变就绪结论。</p>
  </section>
</template>

<style scoped>
.system-center{display:grid;gap:24px}.system-heading{margin-bottom:0}.system-hero{display:grid;grid-template-columns:minmax(0,1fr) auto;border:1px solid var(--line);background:var(--panel)}.hero-status{display:flex;align-items:center;gap:13px;padding:18px 20px;border-left:3px solid var(--amber);color:var(--amber)}.hero-status.good{border-left-color:var(--cyan);color:var(--cyan)}.hero-status.bad{border-left-color:var(--red);color:var(--red)}.hero-status div{display:grid;gap:3px}.hero-status p{margin:0;color:var(--muted);font:9px Consolas,monospace;letter-spacing:.08em}.hero-status h2{margin:0;color:var(--ink);font-size:22px}.hero-status span{color:var(--muted);font-size:10px}.hero-facts{display:grid;grid-template-columns:repeat(3,125px);border-left:1px solid var(--line)}.hero-facts div{display:grid;align-content:center;gap:5px;padding:10px 13px;border-right:1px solid var(--line)}.hero-facts div:last-child{border-right:0}.hero-facts span{color:var(--dim);font-size:9px}.hero-facts strong{color:var(--ink);font:600 11px Consolas,monospace}.system-metrics{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.system-metrics article{display:grid;gap:6px;min-height:102px;padding:16px 18px;border-right:1px solid var(--line)}.system-metrics article:last-child{border-right:0}.system-metrics span,.system-metrics em{color:var(--dim);font-size:10px;font-style:normal}.system-metrics strong{align-self:center;color:var(--ink);font:600 20px Consolas,monospace}.system-metrics strong small{margin-left:4px;color:var(--dim);font-size:12px}.system-layout{display:grid;grid-template-columns:1fr 1fr;gap:13px;align-items:start}.system-panel{min-width:0;border:1px solid var(--line);background:var(--panel);padding:21px 18px 13px}.system-panel .section-heading{margin-bottom:14px}.service-list,.check-list{display:grid;border-top:1px solid var(--line)}.service-list>div,.check-list>div{display:grid;grid-template-columns:27px minmax(0,1fr) auto;align-items:center;gap:9px;min-height:54px;border-bottom:1px solid var(--line)}.service-list>div>span:nth-child(2),.check-list>div>span:nth-child(2){display:grid;gap:4px}.service-list strong,.check-list strong{color:var(--ink);font-size:11px}.service-list small,.check-list small{color:var(--dim);font-size:9px}.service-icon{display:grid;place-items:center;width:24px;height:24px;border:1px solid var(--line-bright)}.service-list b,.check-list b{font:700 9px Consolas,monospace}.good{color:var(--cyan)}.warn{color:var(--amber)}.bad{color:var(--red)}.next-actions{display:grid;gap:6px;padding-top:13px}.next-actions strong{color:var(--dim);font-size:10px}.next-actions span{border-left:2px solid var(--amber);padding:5px 8px;color:var(--muted);background:rgba(228,179,109,.06);font-size:9px;line-height:1.5}.system-footnote{display:flex;align-items:start;gap:7px;margin:0;color:var(--dim);font-size:10px;line-height:1.6}.system-footnote svg{flex:0 0 auto;color:var(--amber)}
.theme-panel{margin-top:13px}.theme-options{display:grid;grid-template-columns:repeat(2,minmax(0,220px));gap:12px}.theme-option{display:flex;align-items:center;gap:12px;border:1px solid var(--line-bright);border-radius:6px;padding:12px 14px;background:transparent;color:var(--muted);text-align:left}.theme-option:hover{border-color:var(--cyan)}.theme-option.active{border-color:var(--cyan);color:var(--ink)}.theme-option span:nth-child(2){display:grid;gap:3px;flex:1}.theme-option strong{font-size:12px}.theme-option small{color:var(--dim);font-size:10px}.theme-swatch{display:grid;place-items:center;width:38px;height:38px;border-radius:6px;border:1px solid var(--line-bright)}.dark-swatch{background:#111619;color:#6ce5d0}.light-swatch{background:#ffffff;color:#0b9b86}.theme-check{color:var(--cyan)}.theme-note{margin:12px 0 0;color:var(--dim);font-size:10px}@media(max-width:800px){.system-hero{grid-template-columns:1fr}.hero-facts{border-top:1px solid var(--line);border-left:0;grid-template-columns:repeat(3,1fr)}.hero-facts div{min-height:64px}.system-metrics{grid-template-columns:1fr 1fr}.system-metrics article:nth-child(2n){border-right:0}.system-metrics article:nth-child(-n+2){border-bottom:1px solid var(--line)}.system-layout{grid-template-columns:1fr}}@media(max-width:520px){.hero-facts{grid-template-columns:1fr}.hero-facts div{border-right:0;border-bottom:1px solid var(--line)}.hero-facts div:last-child{border-bottom:0}.system-metrics{grid-template-columns:1fr}.system-metrics article{border-right:0;border-bottom:1px solid var(--line)}.system-metrics article:last-child{border-bottom:0}}
</style>
