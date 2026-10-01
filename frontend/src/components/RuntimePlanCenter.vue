<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  CircleDashed,
  Clock3,
  Database,
  FlaskConical,
  KeyRound,
  ListChecks,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  WalletCards,
} from 'lucide-vue-next';
import { api } from '../api';
import type { RuntimePlan, RuntimePlanStep } from '../types';

const plan = ref<RuntimePlan | null>(null);
const loading = ref(false);
const error = ref('');
let timer: number | undefined;

const steps = computed(() => plan.value?.steps || []);
const readySteps = computed(() => steps.value.filter((step) => ['ready', 'partial'].includes(step.status)).length);
const progress = computed(() => steps.value.length ? Math.round(readySteps.value / steps.value.length * 100) : 0);
const recentTasks = computed(() => plan.value?.recent_tasks || []);

const stepIcons: Record<string, typeof Database> = {
  history: Database,
  strategies: FlaskConical,
  research: Activity,
  paper: WalletCards,
  risk: ShieldCheck,
  live: LockKeyhole,
  gace: KeyRound,
};

const statusLabel = (status: string) => ({
  ready: '已就绪',
  partial: '部分就绪',
  running: '运行中',
  waiting: '等待',
  blocked: '已阻断',
  disabled: '未启用',
}[status] || status);

const statusIcon = (status: string) => {
  if (status === 'ready') return CheckCircle2;
  if (status === 'blocked') return CircleAlert;
  if (status === 'partial') return AlertTriangle;
  if (status === 'running') return RefreshCw;
  return CircleDashed;
};

const taskStatusLabel = (status: string) => ({
  completed: '完成',
  filled: '成交',
  failed: '失败',
  blocked: '阻断',
  interrupted: '中断',
  cancelling: '停止中',
  cancelled: '已停止',
  running: '运行中',
  queued: '排队',
  open: '开放',
}[status] || status);

const formatNumber = (value: string | number | undefined, digits = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
};

const formatTime = (value?: string | null) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
};

const taskKindLabel = (kind: string) => ({
  history_download: '数据',
  screening: '筛选',
  backtest: '回测',
  research: '研究',
  compare: '比较',
  tune: '调参',
  paper_order: '模拟订单',
  paper_strategy: '模拟回放',
}[kind] || kind);

const evidenceEntries = (step: RuntimePlanStep) => Object.entries(step.evidence || {}).slice(0, 4);

const load = async (quiet = false) => {
  if (!quiet) loading.value = true;
  try {
    const response = await api.get<RuntimePlan>('/runtime/plan');
    plan.value = response.data;
    error.value = '';
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || cause.message || '运行计划读取失败';
  } finally {
    loading.value = false;
  }
};

onMounted(async () => {
  await load();
  timer = window.setInterval(() => load(true), 15000);
});

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
});
</script>

<template>
  <section class="runtime-plan-center" aria-live="polite">
    <section class="page-heading runtime-plan-heading">
      <div>
        <p class="kicker">RUNTIME CONTROL / 11</p>
        <h1>运行计划</h1>
        <p class="muted">把历史数据、策略研究、模拟盘和风险门禁放在同一条状态链上。</p>
      </div>
      <button class="icon-button" type="button" title="刷新运行计划" aria-label="刷新运行计划" :disabled="loading" @click="load()">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
      </button>
    </section>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>

    <section v-if="plan" class="runtime-stage" :class="`tone-${plan.activity.tone}`">
      <div class="runtime-stage-main">
        <span class="stage-icon"><Activity :size="22" /></span>
        <div>
          <p>{{ plan.phase }} / {{ plan.market_mode }}</p>
          <h2>{{ plan.activity.title }}</h2>
          <span>{{ plan.activity.detail }}</span>
        </div>
      </div>
      <div class="runtime-stage-meta">
        <div><span>整体状态</span><strong>{{ plan.status === 'safe_paused' ? '安全暂停' : '受控' }}</strong><small>{{ plan.execution_mode }}</small></div>
        <div><span>计划进度</span><strong>{{ progress }}%</strong><small>{{ readySteps }} / {{ steps.length }} 个节点</small></div>
        <div><span>更新时间</span><strong>{{ formatTime(plan.now) }}</strong><small>UTC</small></div>
      </div>
    </section>
    <div v-else-if="loading" class="loading-block"><RefreshCw :size="17" class="spinning" /> 正在读取运行计划</div>

    <section v-if="plan" class="runtime-metrics" aria-label="运行摘要">
      <article><span>已校验数据集</span><strong>{{ formatNumber(plan.summary.verified_dataset_count) }}<small> / {{ formatNumber(plan.summary.expected_dataset_count) }}</small></strong><em>{{ plan.summary.coverage_ratio_pct }}% 覆盖</em></article>
      <article><span>历史 K 线</span><strong>{{ formatNumber(plan.summary.row_count) }}</strong><em>Manifest 质量门禁</em></article>
      <article><span>启用策略</span><strong>{{ formatNumber(plan.summary.enabled_strategy_count) }}</strong><em>研究 / 回测 / 模拟</em></article>
      <article><span>模拟资产</span><strong>{{ formatNumber(plan.paper.equity_quote, 2) }}</strong><em>USDT 计价</em></article>
      <article><span>只读能力</span><strong>{{ formatNumber(plan.capabilities.read_only_count) }}</strong><em>GACE catalog</em></article>
    </section>

    <section v-if="plan" class="runtime-step-panel" aria-labelledby="runtime-step-title">
      <header class="section-heading">
        <div><p class="kicker">EXECUTION RAIL</p><h2 id="runtime-step-title">运行节点</h2></div>
        <span class="section-meta">{{ steps.length }} STEPS · 24/7</span>
      </header>
      <div class="runtime-step-grid">
        <article v-for="step in steps" :key="step.id" class="runtime-step" :class="`status-${step.status}`">
          <header>
            <span class="runtime-step-icon"><component :is="stepIcons[step.id] || Clock3" :size="17" /></span>
            <span class="runtime-status"><component :is="statusIcon(step.status)" :size="13" />{{ statusLabel(step.status) }}</span>
          </header>
          <p>{{ step.window }}</p>
          <h3>{{ step.title }}</h3>
          <span class="runtime-step-detail">{{ step.action }}</span>
          <div class="runtime-evidence">
            <div v-for="entry in evidenceEntries(step)" :key="entry[0]"><span>{{ entry[0] }}</span><strong>{{ entry[1] ?? '—' }}</strong></div>
          </div>
        </article>
      </div>
    </section>

    <section v-if="plan" class="runtime-lower-grid">
      <article class="runtime-panel runtime-focus-panel">
        <header><div><p class="kicker">CURRENT / NEXT</p><h2>当前链路</h2></div><ArrowRight :size="18" class="panel-icon" /></header>
        <div class="focus-row">
          <div class="focus-block current"><span>当前节点</span><strong>{{ plan.current_step?.title || '已进入稳定等待' }}</strong><small>{{ plan.current_step?.action || plan.activity.detail }}</small></div>
          <ArrowRight :size="17" class="focus-arrow" />
          <div class="focus-block next"><span>下一节点</span><strong>{{ plan.next_step?.title || '按需触发研究或模拟' }}</strong><small>{{ plan.next_step?.action || '没有自动执行动作。' }}</small></div>
        </div>
        <div class="checkpoint-list">
          <div><span>最近回测</span><strong>{{ plan.checkpoints.latest_backtest?.run_id || '暂无' }}</strong></div>
          <div><span>最近研究</span><strong>{{ plan.checkpoints.latest_research?.run_id || '暂无' }}</strong></div>
          <div><span>模拟自动回放</span><strong>{{ plan.checkpoints.paper_automation_enabled ? '已配置' : '未配置' }}</strong></div>
          <div><span>风险状态</span><strong>{{ plan.checkpoints.risk_state || 'SAFE_PAUSED' }}</strong></div>
        </div>
      </article>

      <article class="runtime-panel runtime-gate-panel">
        <header><div><p class="kicker">SAFETY GATE</p><h2>执行边界</h2></div><ShieldCheck :size="18" class="panel-icon" /></header>
        <div class="gate-state"><LockKeyhole :size="19" /><div><strong>真实执行保持阻断</strong><span>当前服务端没有真实订单权限。</span></div></div>
        <div class="gate-list">
          <div v-for="gate in plan.risk.gates" :key="gate.key"><span :class="gate.passed ? 'gate-pass' : 'gate-blocked'"><CheckCircle2 v-if="gate.passed" :size="13" /><CircleAlert v-else :size="13" />{{ gate.label }}</span><small>{{ gate.detail }}</small></div>
        </div>
        <p class="runtime-boundary-note">{{ plan.capabilities.write_count }} 个写入能力已对 GACE 只读目录开放；提币和真实下单均在阻断清单中。</p>
      </article>
    </section>

    <section v-if="plan" class="runtime-panel task-ledger-panel" aria-labelledby="runtime-task-title">
      <header><div><p class="kicker">TASK LEDGER</p><h2 id="runtime-task-title">最近任务</h2></div><span>{{ plan.tasks.active }} 活跃 · {{ plan.tasks.failed }} 异常</span></header>
      <div v-if="recentTasks.length" class="runtime-task-list">
        <div v-for="task in recentTasks" :key="task.task_id" class="runtime-task-row">
          <span class="task-status-dot" :class="task.status"><CheckCircle2 v-if="['completed', 'filled'].includes(task.status)" :size="14" /><CircleAlert v-else-if="['failed', 'blocked', 'rejected', 'interrupted'].includes(task.status)" :size="14" /><Clock3 v-else :size="14" /></span>
          <div><strong>{{ task.label }}</strong><small>{{ taskKindLabel(task.kind) }} · {{ task.task_id }}</small></div>
          <span class="runtime-task-status">{{ taskStatusLabel(task.status) }}</span>
          <time>{{ formatTime(task.updated_at) }}</time>
        </div>
      </div>
      <div v-else class="runtime-empty"><ListChecks :size="21" /><span>暂无任务记录</span></div>
    </section>

    <p v-if="plan" class="runtime-footnote"><ShieldCheck :size="13" /> 运行计划是状态投影，不是交易授权。历史数据来自已校验文件，模拟盘不会触碰真实账户，GACE 当前只暴露声明式只读能力。</p>
  </section>
</template>

<style scoped>
.runtime-plan-center{display:grid;gap:30px}.runtime-plan-heading{margin-bottom:0}.runtime-plan-heading .icon-button{margin-bottom:4px}.runtime-stage{display:grid;grid-template-columns:minmax(0,1fr) auto;border:1px solid var(--line);background:var(--panel);overflow:hidden}.runtime-stage:before{content:"";width:4px;background:var(--cyan)}.runtime-stage.tone-blocked:before{background:var(--red)}.runtime-stage.tone-running:before{background:var(--amber)}.runtime-stage-main{display:flex;align-items:center;gap:15px;padding:20px 19px}.stage-icon{display:grid;place-items:center;width:48px;height:48px;border:1px solid var(--line-bright);color:var(--cyan);background:rgba(108,229,208,.06)}.runtime-stage.tone-blocked .stage-icon{color:var(--red);background:rgba(238,129,120,.06)}.runtime-stage.tone-running .stage-icon{color:var(--amber);background:rgba(228,179,109,.06)}.runtime-stage-main p{margin:0 0 4px;color:var(--cyan);font-size:9px;letter-spacing:.1em}.runtime-stage.tone-blocked .runtime-stage-main p{color:var(--red)}.runtime-stage h2{margin:0 0 5px;font-size:22px;font-weight:570}.runtime-stage-main div>span{color:var(--muted);font-size:11px}.runtime-stage-meta{display:grid;grid-template-columns:repeat(3,126px);border-left:1px solid var(--line)}.runtime-stage-meta div{display:grid;align-content:center;gap:4px;padding:13px;border-right:1px solid var(--line)}.runtime-stage-meta div:last-child{border-right:0}.runtime-stage-meta span,.runtime-stage-meta small{color:var(--dim);font-size:9px}.runtime-stage-meta strong{color:var(--ink);font:600 14px Consolas,monospace}.runtime-metrics{display:grid;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.runtime-metrics article{display:grid;gap:7px;min-height:108px;padding:16px 17px;border-right:1px solid var(--line)}.runtime-metrics article:last-child{border-right:0}.runtime-metrics span,.runtime-metrics em{color:var(--dim);font-size:10px;font-style:normal}.runtime-metrics strong{align-self:center;color:var(--ink);font-size:24px;font-weight:560}.runtime-metrics strong small{color:var(--dim);font-size:13px}.runtime-step-panel{border-top:1px solid var(--line);padding-top:32px}.runtime-step-panel .section-heading{margin-bottom:18px}.runtime-step-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.runtime-step{min-width:0;min-height:205px;border:1px solid var(--line);padding:14px 14px 12px;background:var(--panel);transition:border-color .18s ease,transform .18s ease}.runtime-step:hover{border-color:var(--line-bright);transform:translateY(-1px)}.runtime-step.status-ready{border-top:2px solid var(--cyan)}.runtime-step.status-partial{border-top:2px solid var(--amber)}.runtime-step.status-running{border-top:2px solid var(--amber);background:rgba(228,179,109,.04)}.runtime-step.status-blocked{border-top:2px solid var(--red);background:rgba(238,129,120,.04)}.runtime-step header{display:flex;align-items:center;justify-content:space-between;gap:8px}.runtime-step-icon{display:grid;place-items:center;width:30px;height:30px;border:1px solid var(--line-bright);color:var(--cyan)}.runtime-step.status-partial .runtime-step-icon,.runtime-step.status-running .runtime-step-icon{color:var(--amber)}.runtime-step.status-blocked .runtime-step-icon{color:var(--red)}.runtime-status{display:inline-flex;align-items:center;gap:4px;color:var(--dim);font-size:9px}.runtime-step.status-ready .runtime-status{color:var(--cyan)}.runtime-step.status-partial .runtime-status,.runtime-step.status-running .runtime-status{color:var(--amber)}.runtime-step.status-blocked .runtime-status{color:var(--red)}.runtime-step>p{margin:17px 0 4px;color:var(--dim);font:9px Consolas,monospace;letter-spacing:.08em}.runtime-step h3{margin:0 0 8px;color:var(--ink);font-size:15px;font-weight:570}.runtime-step-detail{display:block;min-height:34px;color:var(--muted);font-size:10px;line-height:1.5}.runtime-evidence{display:grid;gap:4px;margin-top:13px;padding-top:9px;border-top:1px solid var(--line)}.runtime-evidence div{display:flex;justify-content:space-between;gap:8px;color:var(--dim);font-size:9px}.runtime-evidence strong{overflow:hidden;color:var(--muted);font:600 9px Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.runtime-lower-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(310px,.75fr);gap:12px}.runtime-panel{min-width:0;border:1px solid var(--line);background:var(--panel)}.runtime-panel>header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 15px;border-bottom:1px solid var(--line)}.runtime-panel>header h2{margin:0;font-size:16px;font-weight:570}.runtime-panel>header .kicker{margin-bottom:5px}.panel-icon{color:var(--dim)}.focus-row{display:grid;grid-template-columns:minmax(0,1fr) 26px minmax(0,1fr);align-items:center;padding:16px 15px 14px}.focus-block{display:grid;gap:6px;min-width:0}.focus-block span{color:var(--cyan);font-size:9px;letter-spacing:.1em}.focus-block.next span{color:var(--dim)}.focus-block strong{overflow:hidden;color:var(--ink);font-size:14px;font-weight:560;text-overflow:ellipsis;white-space:nowrap}.focus-block small{color:var(--muted);font-size:10px;line-height:1.5}.focus-arrow{justify-self:center;color:var(--dim)}.checkpoint-list{display:grid;grid-template-columns:repeat(2,1fr);border-top:1px solid var(--line)}.checkpoint-list div{display:flex;justify-content:space-between;gap:10px;padding:10px 14px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);font-size:10px}.checkpoint-list div:nth-child(2n){border-right:0}.checkpoint-list div:nth-last-child(-n+2){border-bottom:0}.checkpoint-list span{color:var(--dim)}.checkpoint-list strong{max-width:150px;overflow:hidden;color:var(--muted);font:600 9px Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.gate-state{display:flex;align-items:center;gap:10px;margin:15px;border-left:2px solid var(--red);padding:10px 11px;color:var(--red);background:rgba(238,129,120,.06)}.gate-state div{display:grid;gap:3px}.gate-state strong{font-size:13px;font-weight:570}.gate-state span{color:var(--muted);font-size:10px}.gate-list{display:grid;margin:0 15px;border-top:1px solid var(--line)}.gate-list div{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:10px}.gate-list div span{display:inline-flex;align-items:center;gap:5px}.gate-pass{color:var(--cyan)}.gate-blocked{color:var(--red)}.gate-list small{color:var(--dim);font-size:9px;text-align:right}.runtime-boundary-note{margin:12px 15px 15px;color:var(--dim);font-size:9px;line-height:1.6}.task-ledger-panel>header>span{color:var(--dim);font-size:10px}.runtime-task-list{display:grid}.runtime-task-row{display:grid;grid-template-columns:25px minmax(0,1fr) auto auto;align-items:center;gap:10px;min-height:53px;padding:0 15px;border-bottom:1px solid var(--line)}.runtime-task-row:last-child{border-bottom:0}.task-status-dot{display:grid;place-items:center;color:var(--dim)}.task-status-dot.completed,.task-status-dot.filled{color:var(--cyan)}.task-status-dot.failed,.task-status-dot.blocked,.task-status-dot.rejected{color:var(--red)}.task-status-dot.running,.task-status-dot.queued{color:var(--amber)}.runtime-task-row div{display:grid;gap:3px;min-width:0}.runtime-task-row strong{overflow:hidden;color:var(--ink);font-size:11px;font-weight:550;text-overflow:ellipsis;white-space:nowrap}.runtime-task-row small{overflow:hidden;color:var(--dim);font-size:9px;text-overflow:ellipsis;white-space:nowrap}.runtime-task-status{color:var(--muted);font-size:9px}.runtime-task-row time{color:var(--dim);font:9px Consolas,monospace}.runtime-empty{display:grid;justify-items:center;gap:8px;min-height:100px;place-content:center;color:var(--dim);font-size:11px}.runtime-footnote{display:flex;align-items:start;gap:7px;margin:0;color:var(--dim);font-size:10px;line-height:1.6}.runtime-footnote svg{flex:0 0 auto;color:var(--amber)}
@media (max-width:1050px){.runtime-stage{grid-template-columns:1fr}.runtime-stage-meta{border-top:1px solid var(--line);border-left:0}.runtime-step-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.runtime-metrics{grid-template-columns:repeat(3,1fr)}.runtime-metrics article:nth-child(3){border-right:0}.runtime-metrics article:nth-child(-n+3){border-bottom:1px solid var(--line)}.runtime-lower-grid{grid-template-columns:1fr}}
@media (max-width:620px){.runtime-stage-main{align-items:flex-start;padding:16px 14px}.runtime-stage h2{font-size:18px}.runtime-stage-meta{grid-template-columns:1fr 1fr}.runtime-stage-meta div{min-height:76px}.runtime-stage-meta div:last-child{grid-column:1/-1;border-top:1px solid var(--line);border-right:0}.runtime-metrics{grid-template-columns:1fr 1fr}.runtime-metrics article{min-height:88px;padding:13px 12px}.runtime-metrics article:nth-child(2n){border-right:0}.runtime-metrics article:nth-child(-n+4){border-bottom:1px solid var(--line)}.runtime-metrics article:last-child{grid-column:1/-1;border-bottom:0}.runtime-step-grid{grid-template-columns:1fr}.runtime-step{min-height:0}.focus-row{grid-template-columns:1fr;gap:12px}.focus-arrow{transform:rotate(90deg)}.checkpoint-list{grid-template-columns:1fr}.checkpoint-list div,.checkpoint-list div:nth-child(2n){border-right:0}.checkpoint-list div:nth-last-child(-n+2){border-bottom:1px solid var(--line)}.checkpoint-list div:last-child{border-bottom:0}.runtime-task-row{grid-template-columns:25px minmax(0,1fr) auto}.runtime-task-row time{grid-column:2}.runtime-task-status{grid-column:3;grid-row:1}.runtime-task-row time{justify-self:start}}
</style>
