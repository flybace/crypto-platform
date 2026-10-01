<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { CheckCircle2, CircleAlert, CircleStop, ListChecks, RefreshCw, RotateCcw, Timer, X } from 'lucide-vue-next';
import { api } from '../api';
import type { PlatformTask, TaskDetailResponse } from '../types';

const tasks = ref<PlatformTask[]>([]);
const summary = ref({ total: 0, active: 0, failed: 0, dead_lettered: 0, completed: 0 });
const loading = ref(false);
const error = ref('');
const filter = ref<'all' | 'active' | 'failed' | 'completed'>('all');
const selectedTaskId = ref('');
const selectedTaskDetail = ref<TaskDetailResponse | null>(null);
const detailLoading = ref(false);
const actionLoading = ref('');
let refreshTimer: number | undefined;
const taskFilters: { value: 'all' | 'active' | 'failed' | 'completed'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '运行中' },
  { value: 'failed', label: '异常' },
  { value: 'completed', label: '完成' },
];

const filtered = computed(() => tasks.value.filter((task) => {
  if (filter.value === 'active') return ['queued', 'running', 'cancelling', 'open', 'accepted', 'partially_filled'].includes(task.status);
  if (filter.value === 'failed') return ['failed', 'partial', 'blocked', 'cancelled', 'rejected', 'interrupted', 'dead_lettered'].includes(task.status);
  if (filter.value === 'completed') return task.status === 'completed' || task.status === 'filled';
  return true;
}));
const statusLabel = (value: string) => ({ queued: '排队', running: '运行中', cancelling: '停止中', completed: '完成', partial: '部分完成', cancelled: '已停止', blocked: '阻断', failed: '失败', interrupted: '中断', dead_lettered: '死信', filled: '已成交', partially_filled: '部分成交', rejected: '拒绝', open: '挂单中', accepted: '已接受' }[value] || value);
const kindLabel = (value: string) => ({ history_download: '历史下载', screening: '指标筛选', backtest: '策略回测', pool_backtest: '币池回测', research: '研究任务', paper_order: '模拟订单', paper_strategy: '模拟策略', paper_automation: '自动回放', strategy_matrix: '策略矩阵' }[value] || value);
const formatDate = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const formatJson = (value: unknown) => JSON.stringify(value, null, 2);
const setFilter = (value: 'all' | 'active' | 'failed' | 'completed') => { filter.value = value; };

const loadTasks = async (silent = false) => {
  if (!silent) loading.value = true;
  error.value = '';
  try {
    const [tasksResponse, summaryResponse] = await Promise.all([
      api.get<{ items: PlatformTask[] }>('/tasks', { params: { limit: 100 } }),
      api.get<typeof summary.value>('/tasks/summary'),
    ]);
    tasks.value = tasksResponse.data.items;
    summary.value = summaryResponse.data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '任务中心读取失败';
  } finally {
    if (!silent) loading.value = false;
  }
};

const selectTask = async (task: PlatformTask) => {
  selectedTaskId.value = task.task_id;
  detailLoading.value = true;
  selectedTaskDetail.value = null;
  try {
    selectedTaskDetail.value = (await api.get<TaskDetailResponse>(`/tasks/${encodeURIComponent(task.task_id)}`)).data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '任务详情读取失败';
  } finally {
    detailLoading.value = false;
  }
};

const closeDetail = () => {
  selectedTaskId.value = '';
  selectedTaskDetail.value = null;
};

const cancelTask = async (task: PlatformTask) => {
  actionLoading.value = task.task_id;
  error.value = '';
  try {
    await api.post(`/tasks/${encodeURIComponent(task.task_id)}/cancel`);
    await loadTasks(true);
    const updated = tasks.value.find((item) => item.task_id === task.task_id);
    if (updated) await selectTask(updated);
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '任务停止请求失败';
  } finally {
    actionLoading.value = '';
  }
};

const retryTask = async (task: PlatformTask) => {
  actionLoading.value = task.task_id;
  error.value = '';
  try {
    const isDeadLetter = task.status === 'dead_lettered';
    const endpoint = isDeadLetter
      ? `/tasks/dead-letters/${encodeURIComponent(task.task_id)}/replay`
      : `/tasks/${encodeURIComponent(task.task_id)}/retry`;
    const body = isDeadLetter ? { reason: '任务中心人工重放死信任务' } : undefined;
    const { data } = await api.post<{ task?: PlatformTask; task_id?: string; replay_task_id?: string }>(endpoint, body);
    await loadTasks(true);
    const nextId = data.task?.task_id || data.task_id || data.replay_task_id;
    const next = nextId ? tasks.value.find((item) => item.task_id === nextId) : undefined;
    if (next) await selectTask(next);
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || (task.status === 'dead_lettered' ? '死信重放请求失败' : '任务重试请求失败');
  } finally {
    actionLoading.value = '';
  }
};

onMounted(async () => {
  await loadTasks();
  refreshTimer = window.setInterval(() => {
    if (summary.value.active && !loading.value) void loadTasks(true);
  }, 2500);
});

onBeforeUnmount(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
});
</script>

<template>
  <section class="task-center" aria-labelledby="task-title">
    <section class="page-heading task-heading"><div><p class="kicker">ORCHESTRATION / 10</p><h1 id="task-title">任务中心</h1><p class="muted">统一查看历史下载、筛选、回测和模拟订单的当前状态。</p></div><button class="icon-button" type="button" title="刷新任务" aria-label="刷新任务" :disabled="loading" @click="loadTasks()"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <section class="task-metrics" aria-label="任务摘要"><article><span>全部</span><strong>{{ summary.total }}</strong><em>最近任务</em></article><article><span>运行中</span><strong class="positive">{{ summary.active }}</strong><em>排队或执行</em></article><article><span>异常</span><strong class="negative">{{ summary.failed }}</strong><em>失败或阻断</em></article><article><span>完成</span><strong>{{ summary.completed }}</strong><em>已归档状态</em></article></section>
    <div class="task-tabs" role="tablist" aria-label="任务筛选"><button v-for="item in taskFilters" :key="item.value" type="button" :class="{ active: filter === item.value }" @click="setFilter(item.value)">{{ item.label }}</button></div>
    <section class="task-panel" aria-labelledby="task-list-title"><div class="section-heading"><div><p class="kicker">TASK LEDGER</p><h2 id="task-list-title">任务记录</h2></div><span class="section-meta">{{ filtered.length }} ITEMS</span></div><div v-if="!filtered.length" class="empty-state"><ListChecks :size="22" /><p>暂无匹配任务</p></div><div v-else class="task-list"><article v-for="task in filtered" :key="task.task_id" class="task-row" :class="{ selected: selectedTaskId === task.task_id }" tabindex="0" @click="selectTask(task)" @keydown.enter="selectTask(task)"><span class="task-icon" :class="task.status"><CheckCircle2 v-if="['completed', 'filled'].includes(task.status)" :size="15" /><CircleAlert v-else-if="['failed', 'partial', 'blocked', 'cancelled', 'rejected', 'interrupted', 'dead_lettered'].includes(task.status)" :size="15" /><Timer v-else :size="15" /></span><div class="task-main"><div><strong>{{ task.label }}</strong><span>{{ kindLabel(task.kind) }}</span></div><small>{{ task.task_id }}</small></div><div class="task-progress"><span v-if="task.progress.completed !== undefined">{{ task.progress.completed }} / {{ task.progress.total }}</span><span v-else-if="task.progress.filled_quantity !== undefined">{{ task.progress.filled_quantity }} / {{ task.progress.quantity }}</span><span v-else>—</span></div><span class="task-status" :class="task.status">{{ statusLabel(task.status) }}</span><time>{{ formatDate(task.updated_at) }}</time><div class="task-actions"><button v-if="task.cancellable" class="icon-button" type="button" title="停止任务" aria-label="停止任务" :disabled="actionLoading === task.task_id" @click.stop="cancelTask(task)"><CircleStop :size="14" /></button><button v-if="task.retryable" class="icon-button" type="button" title="重试任务" aria-label="重试任务" :disabled="actionLoading === task.task_id" @click.stop="retryTask(task)"><RotateCcw :size="14" /></button></div></article></div></section>
  <section v-if="selectedTaskId" class="task-detail-panel" aria-labelledby="task-detail-title"><div class="section-heading"><div><p class="kicker">TASK DETAIL</p><h2 id="task-detail-title">任务详情</h2></div><button class="icon-button" type="button" title="关闭任务详情" aria-label="关闭任务详情" @click="closeDetail"><X :size="15" /></button></div><div v-if="detailLoading" class="detail-loading"><RefreshCw :size="17" class="spinning" /> 正在读取任务详情</div><template v-else-if="selectedTaskDetail"><div class="detail-summary"><article><span>类型</span><strong>{{ kindLabel(selectedTaskDetail.task.kind) }}</strong></article><article><span>状态</span><strong :class="selectedTaskDetail.task.status === 'completed' ? 'positive' : ['failed', 'blocked', 'partial', 'cancelled', 'interrupted', 'rejected', 'dead_lettered'].includes(selectedTaskDetail.task.status) ? 'negative' : 'warn'">{{ statusLabel(selectedTaskDetail.task.status) }}</strong></article><article><span>进度</span><strong>{{ selectedTaskDetail.task.progress.completed ?? '—' }} / {{ selectedTaskDetail.task.progress.total ?? '—' }}</strong></article><article><span>更新时间</span><strong>{{ formatDate(selectedTaskDetail.task.updated_at) }}</strong></article></div><div v-if="selectedTaskDetail.ledger?.error && Object.keys(selectedTaskDetail.ledger.error).length" class="detail-error" role="alert">{{ String(selectedTaskDetail.ledger.error.message || '任务执行失败') }}</div><div class="detail-columns"><section><div class="detail-subheading"><strong>结果</strong><span>{{ selectedTaskDetail.ledger?.finished_at ? formatDate(selectedTaskDetail.ledger.finished_at) : '尚未结束' }}</span></div><pre>{{ formatJson(selectedTaskDetail.detail) }}</pre></section><section><div class="detail-subheading"><strong>生命周期事件</strong><span>{{ selectedTaskDetail.events.length }}</span></div><div class="event-list"><div v-for="event in selectedTaskDetail.events" :key="event.event_id" class="event-row"><span>{{ event.event_type }}</span><time>{{ formatDate(event.created_at) }}</time><small>{{ event.message }}</small></div><div v-if="!selectedTaskDetail.events.length" class="event-empty">暂无持久化事件</div></div></section></div></template></section>
  </section>
</template>

<style scoped>
.task-center { display: grid; gap: 30px; }
.task-heading { margin-bottom: 0; }
.task-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.task-metrics article { display: grid; gap: 7px; min-height: 106px; padding: 18px 20px; border-right: 1px solid var(--line); }
.task-metrics article:last-child { border-right: 0; }
.task-metrics span, .task-metrics em { color: var(--dim); font-size: 10px; font-style: normal; }
.task-metrics strong { align-self: center; color: var(--ink); font-size: 25px; font-weight: 560; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.task-tabs { display: flex; border-bottom: 1px solid var(--line); }
.task-tabs button { border: 0; border-bottom: 2px solid transparent; padding: 10px 17px; color: var(--dim); background: transparent; font-size: 11px; }
.task-tabs button.active { border-bottom-color: var(--cyan); color: var(--cyan); }
.task-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 13px; background: var(--panel); }
.task-list { display: grid; border-top: 1px solid var(--line); }
.task-row { display: grid; grid-template-columns: 30px minmax(0, 1fr) 100px 82px 160px auto; align-items: center; gap: 12px; min-height: 64px; border-bottom: 1px solid var(--line); cursor: pointer; outline: none; }
.task-row:hover, .task-row:focus-visible, .task-row.selected { background: var(--panel-soft); }
.task-icon { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid var(--line-bright); color: var(--amber); }
.task-icon.completed, .task-icon.filled { color: var(--cyan); border-color: #42645d; }
.task-icon.failed, .task-icon.partial, .task-icon.blocked, .task-icon.cancelled, .task-icon.rejected, .task-icon.interrupted, .task-icon.dead_lettered { color: var(--red); border-color: #734943; }
.task-main { display: grid; gap: 5px; min-width: 0; }
.task-main div { display: flex; align-items: baseline; gap: 9px; min-width: 0; }
.task-main strong { overflow: hidden; color: var(--ink); font-size: 12px; font-weight: 570; text-overflow: ellipsis; white-space: nowrap; }
.task-main span, .task-main small, .task-row time { overflow: hidden; color: var(--dim); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.task-progress { color: var(--muted); font: 10px Consolas, monospace; text-align: right; }
.task-status { justify-self: start; padding: 5px 7px; color: var(--muted); background: #222a2a; font-size: 9px; }
.task-status.completed, .task-status.filled { color: var(--cyan); background: rgba(108, 229, 208, .1); }
.task-status.failed, .task-status.partial, .task-status.blocked, .task-status.cancelled, .task-status.rejected, .task-status.interrupted, .task-status.dead_lettered { color: var(--red); background: rgba(238, 129, 120, .1); }
.task-status.running, .task-status.queued, .task-status.cancelling, .task-status.open, .task-status.partially_filled { color: var(--amber); background: rgba(228, 179, 109, .1); }
.task-row time { color: var(--dim); font: 9px Consolas, monospace; text-align: right; }
.task-actions { display: flex; align-items: center; gap: 5px; }
.task-actions .icon-button { width: 27px; height: 27px; color: var(--dim); }
.task-actions .icon-button:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.task-detail-panel { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 20px 18px; background: var(--panel); }
.detail-loading { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 120px; color: var(--dim); font-size: 11px; }
.detail-summary { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.detail-summary article { display: grid; gap: 7px; min-height: 76px; padding: 12px; border-right: 1px solid var(--line); }
.detail-summary article:last-child { border-right: 0; }
.detail-summary span, .detail-subheading span { color: var(--dim); font-size: 9px; }
.detail-summary strong { color: var(--ink); font-size: 12px; font-weight: 560; }
.detail-error { margin-top: 12px; border-left: 2px solid var(--red); padding: 8px 12px; color: #ffaaa2; background: rgba(238, 129, 120, .08); font-size: 11px; }
.detail-columns { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(260px, .8fr); gap: 14px; margin-top: 14px; }
.detail-columns section { min-width: 0; border-top: 1px solid var(--line); }
.detail-subheading { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 0 9px; }
.detail-subheading strong { color: var(--ink); font-size: 11px; font-weight: 570; }
.detail-columns pre { max-height: 260px; margin: 0; overflow: auto; border: 1px solid var(--line); padding: 12px; color: var(--muted); background: #111719; font: 10px/1.55 Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.event-list { display: grid; border-top: 1px solid var(--line); }
.event-row { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; padding: 9px 0; border-bottom: 1px solid var(--line); }
.event-row span { color: var(--cyan); font: 10px Consolas, monospace; }
.event-row time { color: var(--dim); font: 9px Consolas, monospace; text-align: right; }
.event-row small { grid-column: 1 / -1; color: var(--muted); font-size: 10px; }
.event-empty { padding: 22px 0; color: var(--dim); font-size: 10px; text-align: center; }
.empty-state { display: grid; justify-items: center; gap: 8px; min-height: 180px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); }
.empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
@media (max-width: 900px) { .detail-columns { grid-template-columns: 1fr; } }
@media (max-width: 800px) { .task-row { grid-template-columns: 30px minmax(0, 1fr) auto auto; } .task-progress { display: none; } .task-row time { grid-column: 2 / -1; justify-self: start; text-align: left; padding-bottom: 9px; } .task-status { grid-column: 3; grid-row: 1; } .task-actions { grid-column: 4; grid-row: 1; } }
@media (max-width: 600px) { .task-metrics { grid-template-columns: 1fr 1fr; } .task-metrics article:nth-child(2) { border-right: 0; } .task-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } .task-tabs { overflow-x: auto; } .task-tabs button { flex: 0 0 auto; padding-inline: 13px; } }
</style>
