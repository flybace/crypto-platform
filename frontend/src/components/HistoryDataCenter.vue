<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { AlertTriangle, Archive, Check, Database, Download, Eye, LineChart, LoaderCircle, RefreshCw, RotateCcw, StopCircle, XCircle } from 'lucide-vue-next';
import { api } from '../api';
import type { HistoryArchiveStatus, HistoryCandleResponse, HistoryCoverage, HistoryCoverageEntry, HistoryDataset, HistoryJob, HistorySchedulerStatus, HistorySyncPlan, HistorySyncResponse, HistoryTaskResponse, PlatformTask } from '../types';

const venueOptions = [
  { id: 'binance', label: 'Binance', native: 'data-api.binance.vision' },
  { id: 'okx', label: 'OKX', native: 'www.okx.com' },
  { id: 'bybit', label: 'Bybit', native: 'api.bybit-tr.com' },
];

const selectedVenues = ref(['binance', 'okx', 'bybit']);
const symbols = ref('BTC/USDT, ETH/USDT, BNB/USDT');
const interval = ref('1d');
const startAt = ref(defaultDate(-365));
const endAt = ref(defaultDate(0));
const coverage = ref<HistoryCoverage | null>(null);
const archive = ref<HistoryArchiveStatus | null>(null);
const jobs = ref<HistoryJob[]>([]);
const syncPlan = ref<HistorySyncPlan | null>(null);
const schedulerStatus = ref<HistorySchedulerStatus | null>(null);
const loading = ref(false);
const submitting = ref(false);
const syncSubmitting = ref(false);
const error = ref('');
const selectedDataset = ref<HistoryDataset | null>(null);
const preview = ref<HistoryCandleResponse | null>(null);
const previewLoading = ref(false);
const previewError = ref('');
const message = ref('');
const expandedJobId = ref('');
const jobDetail = ref<HistoryJob | null>(null);
const jobDetailTask = ref<PlatformTask | null>(null);
const detailLoading = ref(false);
const actionJobId = ref('');
const actionType = ref<'' | 'cancel' | 'retry'>('');
const archiveLoading = ref(false);
let pollTimer: number | undefined;

function defaultDate(offsetDays: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

const venueName = (venueId: string) => venueOptions.find((venue) => venue.id === venueId)?.label || venueId.toUpperCase();
const statusText = (status: string) => ({ queued: '排队中', running: '下载中', cancelling: '停止中', completed: '已完成', blocked: '已阻塞', failed: '失败', partial: '部分完成', cancelled: '已停止', interrupted: '进程中断' }[status] || status);
const statusClass = (status: string) => `history-status-${status}`;
const coverageStatusText = (status: string) => ({ VERIFIED: '已验证', BLOCKED: '已阻断', FAILED: '下载失败', IN_PROGRESS: '下载中', INTERRUPTED: '进程中断', MISSING: '未拉取' }[status] || status);
const coverageStatusClass = (status: string) => `coverage-status-${status.toLowerCase()}`;
const activeJobs = computed(() => jobs.value.filter((job) => ['queued', 'running', 'cancelling'].includes(job.status)));
const latestJob = computed(() => jobs.value[0] || null);
const incompleteDatasetCount = computed(() => {
  const expected = coverage.value?.expected_dataset_count || 0;
  const verified = coverage.value?.verified_dataset_count || 0;
  return Math.max(expected - verified, 0);
});
const syncStatusLabel = computed(() => {
  const plan = syncPlan.value;
  if (!plan) return '计划生成中';
  if (plan.status === 'UP_TO_DATE' && plan.verified_dataset_count > 0) return '已有历史数据，无新增任务';
  if (plan.verified_dataset_count > 0) return `已有数据，待同步 ${plan.download_count} 个序列`;
  return `待同步 ${plan.download_count} 个序列`;
});
const syncStatusDetail = computed(() => {
  const plan = syncPlan.value;
  if (!plan) return '服务端任务账本';
  const latest = plan.last_job_id ? `最近任务 ${plan.last_job_id.slice(0, 12)}` : '尚未提交任务';
  return `${plan.verified_dataset_count} 个已验证 · ${formatRows(plan.verified_row_count)} 根 K 线 · ${latest}`;
});
const schedulerStatusLabel = computed(() => ({
  not_started: '等待首次运行',
  checking: '检查中',
  active: '任务执行中',
  submitted: '已自动提交',
  up_to_date: '已是最新',
  backoff: '退避重试中',
  error: '本轮失败',
}[schedulerStatus.value?.status || ''] || '状态未知'));
const schedulerStatusDetail = computed(() => {
  const scheduler = schedulerStatus.value;
  if (!scheduler) return '状态未读取';
  if (scheduler.next_attempt_at) return `下次检查 ${formatDateTime(scheduler.next_attempt_at)}`;
  if (scheduler.last_run_at) return `每 ${formatSchedulerInterval(scheduler.interval_seconds)} · 最近 ${formatDateTime(scheduler.last_run_at)}`;
  return `每 ${formatSchedulerInterval(scheduler.interval_seconds)} 自动检查`;
});
const selectedLabel = computed(() => {
  if (!selectedDataset.value) return '';
  return `${venueName(selectedDataset.value.venue_id)} · ${selectedDataset.value.instrument_key.split(':').pop() || selectedDataset.value.native_symbol}`;
});

const loadData = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [coverageResponse, jobsResponse, planResponse, archiveResponse, schedulerResponse] = await Promise.all([
      api.get<HistoryCoverage>('/history/coverage'),
      api.get<{ items: HistoryJob[] }>('/history/jobs'),
      api.get<HistorySyncPlan>('/history/sync/plan', { params: { intervals: '1d,1h,5m' } }),
      api.get<HistoryArchiveStatus>('/history/archive'),
      api.get<HistorySchedulerStatus>('/history/sync/status'),
    ]);
    coverage.value = coverageResponse.data;
    jobs.value = jobsResponse.data.items;
    syncPlan.value = planResponse.data;
    archive.value = archiveResponse.data;
    schedulerStatus.value = schedulerResponse.data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '历史数据状态读取失败';
  } finally {
    loading.value = false;
  }
};

const archiveHistory = async () => {
  archiveLoading.value = true;
  error.value = '';
  message.value = '';
  try {
    const { data } = await api.post<HistoryArchiveStatus>('/history/archive');
    archive.value = data;
    message.value = data.failed_count ? `Parquet 归档部分完成：${data.archived_count}/${data.dataset_count} 个数据集。` : `Parquet 归档完成：${data.archived_count} 个数据集。`;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || 'Parquet 归档不可用';
  } finally {
    archiveLoading.value = false;
  }
};

const runSync = async (mode: 'incremental' | 'backfill') => {
  syncSubmitting.value = true;
  error.value = '';
  message.value = '';
  try {
    const { data } = await api.post<HistorySyncResponse>('/history/sync', {
      mode,
      venue_ids: selectedVenues.value,
      symbols: symbols.value.split(',').map((item) => item.trim()).filter(Boolean),
      intervals: ['1d', '1h', '5m'],
    });
    message.value = data.job
      ? `${mode === 'backfill' ? '历史回补' : '增量同步'}已提交：${data.plan.download_count} 个序列，任务 ${data.job.job_id.slice(0, 12)}。`
      : '当前选择已经覆盖到完整周期，无需重复下载。';
    await loadData();
    if (data.job) startPolling();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || `${mode === 'backfill' ? '历史回补' : '增量同步'}提交失败`;
  } finally {
    syncSubmitting.value = false;
  }
};

const startPolling = () => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
  pollTimer = window.setInterval(async () => {
    await loadData();
    if (!activeJobs.value.length && pollTimer !== undefined) {
      window.clearInterval(pollTimer);
      pollTimer = undefined;
    }
  }, 2000);
};

const submitJob = async () => {
  const requestedSymbols = symbols.value.split(',').map((item) => item.trim()).filter(Boolean);
  if (!selectedVenues.value.length || !requestedSymbols.length) {
    error.value = '至少选择一个市场和一个交易对';
    return;
  }
  submitting.value = true;
  error.value = '';
  message.value = '';
  try {
    const { data } = await api.post<HistoryJob>('/history/jobs', {
      venue_ids: selectedVenues.value,
      symbols: requestedSymbols,
      interval: interval.value,
      start_at: `${startAt.value}T00:00:00Z`,
      end_at: `${endAt.value}T00:00:00Z`,
    });
    message.value = data.deduplicated ? '已复用相同的进行中历史任务。' : '历史下载任务已提交，正在后台处理。';
    await loadData();
    startPolling();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '历史下载任务提交失败';
  } finally {
    submitting.value = false;
  }
};

const taskPath = (jobId: string) => `/tasks/${encodeURIComponent(`history:${jobId}`)}`;
const isJobCancellable = (job: HistoryJob) => ['queued', 'running'].includes(job.status);
const isJobRetryable = (job: HistoryJob) => ['blocked', 'failed', 'partial', 'cancelled', 'interrupted'].includes(job.status);

const openJobDetails = async (job: HistoryJob) => {
  if (expandedJobId.value === job.job_id && !detailLoading.value) {
    expandedJobId.value = '';
    return;
  }
  expandedJobId.value = job.job_id;
  detailLoading.value = true;
  error.value = '';
  try {
    const { data } = await api.get<HistoryTaskResponse>(taskPath(job.job_id));
    jobDetail.value = data.detail;
    jobDetailTask.value = data.task;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '历史任务详情读取失败';
    expandedJobId.value = '';
  } finally {
    detailLoading.value = false;
  }
};

const cancelJob = async (job: HistoryJob) => {
  actionJobId.value = job.job_id;
  actionType.value = 'cancel';
  error.value = '';
  message.value = '';
  try {
    const { data } = await api.post<HistoryTaskResponse>(`${taskPath(job.job_id)}/cancel`);
    jobDetail.value = data.detail;
    jobDetailTask.value = data.task;
    expandedJobId.value = job.job_id;
    message.value = data.detail.status === 'cancelling' ? '已发出停止请求，当前网络页完成后会停止剩余任务。' : '历史任务已停止。';
    await loadData();
    if (activeJobs.value.length) startPolling();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '停止历史任务失败';
  } finally {
    actionJobId.value = '';
    actionType.value = '';
  }
};

const retryJob = async (job: HistoryJob) => {
  actionJobId.value = job.job_id;
  actionType.value = 'retry';
  error.value = '';
  message.value = '';
  try {
    const { data } = await api.post<HistoryTaskResponse>(`${taskPath(job.job_id)}/retry`);
    jobDetail.value = data.detail;
    jobDetailTask.value = data.task;
    expandedJobId.value = data.detail.job_id;
    message.value = `已创建重试任务 ${data.detail.job_id.slice(0, 12)}。`;
    await loadData();
    startPolling();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '重试历史任务失败';
  } finally {
    actionJobId.value = '';
    actionType.value = '';
  }
};

const previewDataset = async (dataset: HistoryDataset) => {
  selectedDataset.value = dataset;
  preview.value = null;
  previewError.value = '';
  previewLoading.value = true;
  try {
    const { data } = await api.get<HistoryCandleResponse>('/history/candles', {
      params: {
        venue_id: dataset.venue_id,
        symbol: dataset.instrument_key.split(':').pop(),
        interval: dataset.interval,
        limit: 12,
        tail: true,
      },
    });
    preview.value = data;
  } catch (cause: any) {
    previewError.value = cause.response?.data?.detail || 'K 线读取失败';
  } finally {
    previewLoading.value = false;
  }
};

const formatDate = (value: string | null) => value ? new Date(value).toLocaleDateString('zh-CN', { timeZone: 'UTC' }) : '—';
const formatRows = (value: number) => new Intl.NumberFormat('zh-CN').format(value);
const formatPrice = (value: string) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 8 }).format(Number(value));
const formatDateTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const formatSchedulerInterval = (seconds: number) => seconds >= 3600 && seconds % 3600 === 0 ? `${seconds / 3600} 小时` : `${Math.max(1, Math.round(seconds / 60))} 分钟`;
const coverageWindow = (entry: HistoryCoverageEntry) => entry.start_at && entry.end_at ? `${formatDate(entry.start_at)} 至 ${formatDate(entry.end_at)}` : entry.reason;

onMounted(async () => {
  await loadData();
  if (activeJobs.value.length) startPolling();
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
});
</script>

<template>
  <section class="history-center" aria-labelledby="history-title">
    <section class="page-heading history-heading">
      <div>
        <p class="kicker">DATA CENTER / 02</p>
        <h1 id="history-title">历史数据</h1>
        <p class="muted">公开 K 线、可复现覆盖范围与下载任务。</p>
      </div>
      <button class="icon-button" type="button" title="刷新历史数据状态" aria-label="刷新历史数据状态" :disabled="loading" @click="loadData">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
      </button>
    </section>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="message" class="inline-success" role="status">{{ message }}</div>

    <section class="history-metrics" aria-label="历史数据摘要">
      <article><span>已验证序列</span><strong>{{ coverage?.verified_dataset_count || coverage?.dataset_count || 0 }}<small>/ {{ coverage?.expected_dataset_count || 27 }}</small></strong><small>{{ coverage?.coverage_ratio_pct || '0.00' }}% 目标覆盖</small></article>
      <article><span>可用 K 线</span><strong>{{ formatRows(coverage?.row_count || 0) }}</strong><small>CSV rows</small></article>
      <article><span>未完成序列</span><strong class="metric-warn">{{ incompleteDatasetCount }}</strong><small>未拉取 {{ coverage?.missing_dataset_count || 0 }} · 阻断 {{ coverage?.blocked_dataset_count || 0 }} · 失败 {{ coverage?.failed_dataset_count || 0 }}</small></article>
      <article><span>当前任务</span><strong>{{ latestJob ? statusText(latestJob.status) : '无' }}</strong><small>{{ activeJobs.length ? '后台运行中' : '等待提交' }}</small></article>
      <article><span>元数据镜像</span><strong>{{ coverage?.metadata?.dataset_count || 0 }}/{{ coverage?.dataset_count || 0 }}</strong><small>{{ coverage?.metadata?.ok ? 'PostgreSQL 已同步' : '等待关系库同步' }}</small></article>
    </section>

    <section class="history-panel sync-panel" aria-labelledby="sync-title">
      <div class="section-heading"><div><p class="kicker">SYNC CONTROL</p><h2 id="sync-title">历史同步计划</h2></div><div class="sync-meta"><span v-if="syncPlan">{{ syncPlan.download_count }} 个序列待处理 · {{ syncPlan.up_to_date_count }} 个已覆盖</span><button class="icon-button" type="button" title="刷新同步计划" aria-label="刷新同步计划" :disabled="loading || syncSubmitting" @click="loadData"><RefreshCw :size="15" :class="{ spinning: loading }" /></button></div></div>
      <p class="sync-intro">计划会读取当前 Manifest 尾端，只提交缺失、过期或质量阻断的数据；已有文件与新响应按开盘时间幂等合并，完成后重新计算缺口、重复和 SHA-256。</p>
      <div v-if="syncPlan" class="sync-strip">
         <div><span>目标范围</span><strong>{{ syncPlan.target_count }} 个序列</strong><small>Binance / OKX / Bybit · BTC ETH BNB · 1d 1h 5m</small></div>
         <div><span>归档边界</span><strong>{{ formatDateTime(syncPlan.end_at) }}</strong><small>UTC 完整周期</small></div>
         <div><span>当前状态</span><strong>{{ syncStatusLabel }}</strong><small>{{ syncStatusDetail }}</small></div>
         <div v-if="schedulerStatus" class="scheduler-status"><span>自动同步</span><strong>{{ schedulerStatusLabel }}</strong><small>{{ schedulerStatusDetail }}</small></div>
         <div class="sync-actions"><button class="sync-button" type="button" :disabled="syncSubmitting || !selectedVenues.length" @click="runSync('incremental')"><RefreshCw :size="14" :class="{ spinning: syncSubmitting }" />增量同步</button><button class="sync-button secondary" type="button" :disabled="syncSubmitting || !selectedVenues.length" @click="runSync('backfill')"><Database :size="14" />按回看窗口回补</button></div>
      </div>
      <div v-else class="history-empty compact"><RefreshCw :size="18" class="spinning" /><p>正在生成同步计划</p></div>
      <div v-if="archive" class="archive-strip">
        <div class="archive-status-mark" :class="`archive-${archive.status.toLowerCase()}`"><Archive :size="16" /></div>
        <div><span>Parquet 研究归档</span><strong>{{ archive.archived_count }}/{{ archive.dataset_count }} 个数据集</strong><small>{{ archive.available ? `${archive.archive_root} · ${archive.status}` : (archive.error || '运行环境未安装 pyarrow') }}</small></div>
        <div class="archive-meta"><span v-if="archive.stale_count">过期 {{ archive.stale_count }}</span><span v-if="archive.missing_count">缺失 {{ archive.missing_count }}</span><span v-if="!archive.stale_count && !archive.missing_count">Manifest 摘要已绑定</span></div>
        <button class="sync-button secondary" type="button" :disabled="archiveLoading || !archive.available" title="生成或刷新 Parquet 归档" @click="archiveHistory"><Archive v-if="!archiveLoading" :size="14" /><RefreshCw v-else :size="14" class="spinning" />{{ archiveLoading ? '归档中' : '生成归档' }}</button>
      </div>
    </section>

    <section class="history-grid">
      <section class="history-panel request-panel" aria-labelledby="request-title">
        <div class="section-heading">
          <div><p class="kicker">PUBLIC DOWNLOAD</p><h2 id="request-title">拉取历史行情</h2></div>
          <Download :size="18" class="section-icon" />
        </div>
        <form class="history-form" @submit.prevent="submitJob">
          <fieldset>
            <legend>市场</legend>
            <label v-for="venue in venueOptions" :key="venue.id" class="venue-choice">
              <input v-model="selectedVenues" type="checkbox" :value="venue.id" />
              <span class="check-box"><Check :size="13" /></span>
              <span><strong>{{ venue.label }}</strong><small>{{ venue.native }}</small></span>
            </label>
          </fieldset>
          <label class="history-field"><span>交易对</span><input v-model="symbols" type="text" placeholder="BTC/USDT, ETH/USDT" /></label>
          <label class="history-field"><span>周期</span><select v-model="interval"><option value="1d">1 日</option><option value="1h">1 小时</option><option value="5m">5 分钟</option></select></label>
          <div class="date-grid">
            <label class="history-field"><span>开始日</span><input v-model="startAt" type="date" /></label>
            <label class="history-field"><span>结束日（不含）</span><input v-model="endAt" type="date" /></label>
          </div>
          <button class="history-submit" type="submit" :disabled="submitting || !selectedVenues.length">
            <LoaderCircle v-if="submitting" :size="16" class="spinning" /><Download v-else :size="16" />
            <span>{{ submitting ? '提交中' : '开始公开下载' }}</span>
          </button>
        </form>
      </section>

      <section class="history-panel coverage-panel" aria-labelledby="coverage-title">
        <div class="section-heading">
          <div><p class="kicker">ARCHIVE COVERAGE</p><h2 id="coverage-title">数据覆盖</h2></div>
          <Database :size="18" class="section-icon" />
        </div>
        <div v-if="!coverage?.datasets.length" class="history-empty"><Database :size="22" /><p>暂无已归档数据</p><small>完成一次公开下载后，Manifest 会出现在这里。</small></div>
        <div v-else class="dataset-list">
            <article v-for="dataset in coverage.datasets" :key="dataset.dataset_id" class="dataset-row">
              <div class="dataset-main"><strong>{{ venueName(dataset.venue_id) }} · {{ dataset.instrument_key.split(':').pop() }}</strong><span>{{ dataset.interval }} · {{ formatDate(dataset.start_at) }} 至 {{ formatDate(dataset.end_at) }}</span></div>
              <div class="dataset-stat"><strong>{{ formatRows(dataset.row_count) }}</strong><span>行</span></div>
              <div class="dataset-quality" :class="{ warning: dataset.gap_count > 0 || dataset.duplicate_count > 0 }"><Check v-if="dataset.gap_count === 0 && dataset.duplicate_count === 0" :size="14" /><AlertTriangle v-else :size="14" /><span>{{ dataset.gap_count ? `${dataset.gap_count} 缺口` : dataset.duplicate_count ? `${dataset.duplicate_count} 重复` : '质量通过' }}</span></div>
              <button class="dataset-open" type="button" :title="`查看 ${dataset.native_symbol} K 线`" :aria-label="`查看 ${dataset.native_symbol} K 线`" @click="previewDataset(dataset)"><Eye :size="15" /></button>
            </article>
          </div>
      </section>
    </section>

    <section class="history-panel matrix-panel" aria-labelledby="matrix-title">
      <div class="section-heading"><div><p class="kicker">TARGET COVERAGE MATRIX</p><h2 id="matrix-title">历史覆盖矩阵</h2></div><span class="section-meta">{{ coverage?.verified_dataset_count || 0 }} / {{ coverage?.expected_dataset_count || 27 }} VERIFIED</span></div>
      <div class="matrix-note">“已验证”只表示文件、Manifest 和 SHA-256 通过；覆盖区间仍以每行显示为准。网络阻断、上游拒绝和进程中断会保留在矩阵中，不会伪造数据。</div>
      <div v-if="coverage?.matrix?.length" class="matrix-table-wrap">
        <table class="matrix-table">
          <thead><tr><th>市场</th><th>交易对</th><th>周期</th><th>状态</th><th>行数</th><th>覆盖区间 / 原因</th></tr></thead>
          <tbody><tr v-for="entry in coverage.matrix" :key="`${entry.venue_id}-${entry.symbol}-${entry.interval}`"><td>{{ venueName(entry.venue_id) }}</td><td>{{ entry.symbol }}</td><td>{{ entry.interval }}</td><td><span class="matrix-status" :class="coverageStatusClass(entry.status)">{{ coverageStatusText(entry.status) }}</span></td><td>{{ entry.row_count ? formatRows(entry.row_count) : '—' }}</td><td class="matrix-detail">{{ coverageWindow(entry) }}</td></tr></tbody>
        </table>
      </div>
      <div v-else class="history-empty compact"><Database :size="20" /><p>覆盖矩阵暂不可用</p></div>
    </section>

    <section v-if="selectedDataset" class="history-panel preview-panel" aria-labelledby="preview-title">
      <div class="section-heading"><div><p class="kicker">VERIFIED CANDLE READ</p><h2 id="preview-title">K 线预览</h2></div><LineChart :size="18" class="section-icon" /></div>
      <div class="preview-meta"><strong>{{ selectedLabel }}</strong><span>{{ selectedDataset.interval }} · 数据集 {{ selectedDataset.row_count }} 行 · 尾部 12 根</span></div>
      <div v-if="previewLoading" class="history-empty compact"><RefreshCw :size="20" class="spinning" /><p>正在读取已校验 K 线</p></div>
      <div v-else-if="previewError" class="inline-error" role="alert">{{ previewError }}</div>
      <div v-else-if="preview?.items.length" class="candle-table-wrap">
        <table class="candle-table">
          <thead><tr><th>时间（UTC）</th><th>开</th><th>高</th><th>低</th><th>收</th><th>成交量</th></tr></thead>
          <tbody><tr v-for="candle in preview.items" :key="candle.open_time"><td>{{ formatDateTime(candle.open_time) }}</td><td>{{ formatPrice(candle.open) }}</td><td>{{ formatPrice(candle.high) }}</td><td>{{ formatPrice(candle.low) }}</td><td class="candle-close">{{ formatPrice(candle.close) }}</td><td>{{ formatPrice(candle.volume) }}</td></tr></tbody>
        </table>
      </div>
      <div v-else class="history-empty compact"><Database :size="20" /><p>该数据集没有可读取的 K 线</p></div>
    </section>

    <section class="history-panel jobs-panel" aria-labelledby="jobs-title">
      <div class="section-heading"><div><p class="kicker">DOWNLOAD LOG</p><h2 id="jobs-title">任务记录</h2></div><span class="section-meta">{{ jobs.length }} JOBS</span></div>
      <div v-if="!jobs.length" class="history-empty compact"><RefreshCw :size="20" /><p>还没有下载任务</p></div>
      <div v-else class="job-list">
        <template v-for="job in jobs" :key="job.job_id">
          <article class="job-row">
            <div class="job-icon" :class="statusClass(job.status)"><LoaderCircle v-if="['queued', 'running', 'cancelling'].includes(job.status)" :size="16" class="spinning" /><Check v-else-if="job.status === 'completed'" :size="16" /><XCircle v-else :size="16" /></div>
            <div class="job-main"><strong>{{ statusText(job.status) }}</strong><span>{{ formatDate(job.created_at) }} · {{ job.completed }}/{{ job.total }} 完成</span></div>
            <div class="job-summary"><span v-if="job.blocked">{{ job.blocked }} 个阻塞</span><span v-if="job.failed">{{ job.failed }} 个失败</span><span v-if="job.interrupted">{{ job.interrupted }} 个中断</span><span v-if="job.cancelled">{{ job.cancelled }} 个停止</span><span v-if="!job.blocked && !job.failed && !job.interrupted && !job.cancelled">公开数据源</span></div>
            <span class="state-pill" :class="statusClass(job.status)">{{ statusText(job.status) }}</span>
            <div class="job-actions">
              <button class="job-action" type="button" :title="expandedJobId === job.job_id ? '收起任务详情' : '查看任务详情'" @click="openJobDetails(job)"><Eye :size="13" />{{ expandedJobId === job.job_id ? '收起' : '详情' }}</button>
              <button v-if="isJobCancellable(job)" class="job-action warning-action" type="button" title="停止历史下载" :disabled="actionJobId === job.job_id" @click="cancelJob(job)"><StopCircle v-if="actionJobId !== job.job_id || actionType !== 'cancel'" :size="13" /><RefreshCw v-else :size="13" class="spinning" />{{ actionJobId === job.job_id && actionType === 'cancel' ? '停止中' : '停止' }}</button>
              <button v-if="isJobRetryable(job)" class="job-action" type="button" title="重试未完成数据" :disabled="!!actionJobId" @click="retryJob(job)"><RotateCcw v-if="actionJobId !== job.job_id || actionType !== 'retry'" :size="13" /><RefreshCw v-else :size="13" class="spinning" />{{ actionJobId === job.job_id && actionType === 'retry' ? '重试中' : '重试' }}</button>
            </div>
          </article>
          <section v-if="expandedJobId === job.job_id" class="job-detail-card" aria-label="历史任务详情">
            <header class="job-detail-head"><strong>任务详情</strong><span v-if="jobDetailTask" class="detail-status" :class="statusClass(jobDetailTask.status)">{{ statusText(jobDetailTask.status) }} · {{ jobDetailTask.task_id }}</span></header>
            <div v-if="detailLoading" class="job-detail-loading"><RefreshCw :size="15" class="spinning" /> 正在读取任务状态</div>
            <div v-else class="job-detail-list">
              <div v-for="item in (jobDetail?.items || job.items)" :key="`${job.job_id}-${item.venue_id}-${item.native_symbol}-${item.interval}`" class="job-detail"><span>{{ venueName(item.venue_id) }} · {{ item.native_symbol }} · {{ item.interval }}</span><span class="detail-status" :class="statusClass(item.status)">{{ statusText(item.status) }}<template v-if="item.error"> · {{ item.error.message || item.error.kind }}</template><template v-if="item.dataset"> · {{ item.dataset.row_count }} 行</template></span></div>
            </div>
            <p v-if="jobDetail?.request_fingerprint" class="job-fingerprint">请求指纹：{{ jobDetail.request_fingerprint }}</p>
          </section>
        </template>
      </div>
    </section>

    <p class="history-footnote"><AlertTriangle :size="13" /> 仅使用公开行情接口；不读取 API Key，不创建订单。OKX 与 Bybit 的连接状态取决于运行端网络和地区端点。</p>
  </section>
</template>

<style scoped>
.history-center { display: grid; gap: 30px; }
.history-heading { margin-bottom: 0; }
.inline-success { margin: 0; border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .08); font-size: 12px; }
.history-heading .icon-button { flex: 0 0 auto; }
.history-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.history-metrics article { display: grid; gap: 8px; min-height: 126px; padding: 21px 24px; border-right: 1px solid var(--line); }
.history-metrics article:last-child { border-right: 0; }
.history-metrics span, .history-metrics small { color: var(--dim); font-size: 11px; }
.history-metrics strong { align-self: center; color: var(--ink); font-size: 27px; font-weight: 560; }
.history-metrics strong small { margin-left: 4px; color: var(--dim); font-size: 14px; font-weight: 450; }
.history-metrics .metric-warn { color: var(--amber); }
.history-metrics small { letter-spacing: .08em; text-transform: uppercase; }
.sync-panel { padding: 22px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); }
.sync-panel .section-heading { margin-bottom: 10px; }
.sync-meta { display: flex; align-items: center; gap: 8px; color: var(--dim); font-size: 10px; }
.sync-meta .icon-button { width: 28px; height: 28px; }
.sync-intro { max-width: 820px; margin: 0 0 17px; color: var(--muted); font-size: 10px; line-height: 1.6; }
.sync-strip { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(150px, 1fr) minmax(170px, 1.05fr) minmax(170px, .95fr) auto; gap: 0; border: 1px solid var(--line); background: var(--panel-soft); }
.sync-strip > div { display: grid; gap: 5px; min-width: 0; padding: 12px 13px; border-right: 1px solid var(--line); }
.sync-strip > div:last-child { border-right: 0; }
.sync-strip span, .sync-strip small { color: var(--dim); font-size: 9px; }
.sync-strip strong { overflow: hidden; color: var(--ink); font: 600 12px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.scheduler-status strong { color: var(--cyan); }
.sync-actions { display: flex !important; align-items: center; gap: 7px; min-width: 226px !important; }
.sync-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 33px; border: 1px solid var(--cyan); border-radius: 5px; padding: 7px 9px; color: #11201e; background: var(--cyan); font-size: 10px; font-weight: 700; white-space: nowrap; }
.sync-button:hover:not(:disabled) { background: #94f0df; }
.sync-button.secondary { border-color: var(--line-bright); color: var(--muted); background: transparent; }
.sync-button.secondary:hover:not(:disabled) { border-color: var(--amber); color: var(--amber); background: transparent; }
.sync-button:disabled { cursor: not-allowed; opacity: .55; }
.archive-strip { display: grid; grid-template-columns: 30px minmax(0, 1.2fr) minmax(110px, .8fr) auto; align-items: center; gap: 10px; margin-top: 12px; border-top: 1px solid var(--line); padding-top: 12px; }
.archive-status-mark { display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid var(--line-bright); color: var(--muted); }
.archive-ready { color: var(--cyan); border-color: rgba(108, 229, 208, .5); }
.archive-partial, .archive-missing { color: var(--amber); border-color: rgba(228, 179, 109, .5); }
.archive-unavailable { color: var(--red); border-color: rgba(238, 129, 120, .5); }
.archive-strip > div:nth-child(2) { display: grid; gap: 4px; min-width: 0; }
.archive-strip span, .archive-strip small { color: var(--dim); font-size: 9px; }
.archive-strip strong { overflow: hidden; color: var(--ink); font-size: 11px; font-weight: 560; text-overflow: ellipsis; white-space: nowrap; }
.archive-meta { display: flex; gap: 8px; color: var(--muted); font-size: 10px; white-space: nowrap; }
.history-grid { display: grid; grid-template-columns: minmax(300px, .86fr) minmax(0, 1.4fr); gap: 14px; }
.history-grid > *, .history-form, .history-field { min-width: 0; }
.matrix-panel { border: 1px solid var(--line); border-radius: var(--radius); padding: 27px 22px 20px; background: var(--panel); }
.matrix-note { margin: -4px 0 16px; color: var(--dim); font-size: 10px; line-height: 1.6; }
.matrix-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.matrix-table { width: 100%; min-width: 760px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.matrix-table th, .matrix-table td { padding: 9px 11px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: left; white-space: nowrap; }
.matrix-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
.matrix-table tr:last-child td { border-bottom: 0; }
.matrix-status { display: inline-flex; padding: 4px 6px; font-size: 9px; }
.coverage-status-verified { color: var(--cyan); background: rgba(108, 229, 208, .1); }
.coverage-status-blocked { color: var(--red); background: rgba(238, 129, 120, .1); }
.coverage-status-failed, .coverage-status-interrupted { color: var(--red); background: rgba(238, 129, 120, .1); }
.coverage-status-in_progress { color: var(--amber); background: rgba(228, 179, 109, .1); }
.coverage-status-missing { color: var(--amber); background: rgba(228, 179, 109, .1); }
.matrix-detail { color: var(--dim) !important; }
.history-panel { min-width: 0; border-top: 1px solid var(--line); padding-top: 27px; }
.request-panel, .coverage-panel { padding: 27px 22px 23px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); }
.history-panel .section-heading { margin-bottom: 24px; }
.history-form { display: grid; gap: 17px; }
fieldset { display: grid; gap: 10px; margin: 0; padding: 0; border: 0; }
legend, .history-field > span { margin-bottom: 7px; color: var(--muted); font-size: 11px; }
.venue-choice { display: grid; grid-template-columns: 16px 17px 1fr; align-items: center; gap: 8px; min-height: 37px; color: var(--muted); cursor: pointer; }
.venue-choice input { position: absolute; opacity: 0; pointer-events: none; }
.check-box { display: grid; place-items: center; width: 17px; height: 17px; border: 1px solid var(--line-bright); color: transparent; background: #13191b; }
.venue-choice input:checked + .check-box { border-color: var(--cyan); color: #12201e; background: var(--cyan); }
.venue-choice input:focus-visible + .check-box { outline: 2px solid var(--cyan); outline-offset: 2px; }
.venue-choice strong, .venue-choice small { display: block; }
.venue-choice strong { color: var(--ink); font-size: 12px; font-weight: 550; }
.venue-choice small { margin-top: 3px; color: var(--dim); font-size: 10px; }
.history-field { display: grid; gap: 0; }
.history-field input, .history-field select { width: 100%; min-width: 0; max-width: 100%; min-height: 39px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 9px 11px; color: var(--ink); background: #13191b; outline: none; }
.history-field input:focus, .history-field select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.date-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }
.history-submit { display: inline-flex; align-items: center; justify-content: center; gap: 9px; min-height: 41px; margin-top: 2px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 12px; font-weight: 720; }
.history-submit:hover:not(:disabled) { background: #94f0df; }
.history-submit:disabled { opacity: .55; cursor: not-allowed; }
.dataset-list, .job-list { display: grid; }
.dataset-row { display: grid; grid-template-columns: minmax(0, 1fr) 66px 78px 34px; align-items: center; gap: 12px; min-height: 64px; border-bottom: 1px solid var(--line); }
.dataset-row:last-child { border-bottom: 0; }
.dataset-main { min-width: 0; display: grid; gap: 6px; }
.dataset-main strong { overflow: hidden; color: var(--ink); font-size: 12px; font-weight: 560; text-overflow: ellipsis; white-space: nowrap; }
.dataset-main span { color: var(--muted); font-size: 10px; }
.dataset-stat { display: grid; gap: 3px; text-align: right; }
.dataset-stat strong { color: var(--ink); font-size: 13px; font-weight: 560; }
.dataset-stat span { color: var(--dim); font-size: 9px; }
.dataset-quality { display: inline-flex; align-items: center; justify-content: end; gap: 4px; color: var(--cyan); font-size: 9px; white-space: nowrap; }
.dataset-quality.warning { color: var(--amber); }
.dataset-open { display: grid; place-items: center; width: 30px; height: 30px; border: 1px solid transparent; border-radius: 5px; color: var(--muted); background: transparent; }
.dataset-open:hover { border-color: var(--line-bright); color: var(--cyan); background: var(--panel-soft); }
.history-empty { display: grid; justify-items: center; gap: 8px; min-height: 190px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.history-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.history-empty small { max-width: 220px; color: var(--dim); font-size: 10px; line-height: 1.6; }
.history-empty.compact { min-height: 86px; }
.job-row { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto auto auto; align-items: center; gap: 12px; min-height: 62px; border-bottom: 1px solid var(--line); }
.job-icon { display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid var(--line-bright); color: var(--muted); }
.job-icon.history-status-completed { color: var(--cyan); border-color: rgba(108, 229, 208, .5); }
.job-icon.history-status-running, .job-icon.history-status-queued, .job-icon.history-status-cancelling { color: var(--amber); border-color: rgba(228, 179, 109, .5); }
.job-icon.history-status-blocked, .job-icon.history-status-failed, .job-icon.history-status-interrupted { color: var(--amber); border-color: rgba(228, 179, 109, .5); }
.job-main { display: grid; gap: 5px; min-width: 0; }
.job-main strong { color: var(--ink); font-size: 12px; font-weight: 550; }
.job-main span, .job-summary { color: var(--muted); font-size: 10px; }
.job-summary { display: flex; gap: 8px; color: var(--amber); }
.state-pill.history-status-running, .state-pill.history-status-queued, .state-pill.history-status-cancelling { color: var(--amber); background: rgba(228, 179, 109, .1); }
.state-pill.history-status-completed { color: var(--cyan); background: rgba(108, 229, 208, .1); }
.state-pill.history-status-blocked, .state-pill.history-status-failed, .state-pill.history-status-interrupted { color: var(--red); background: rgba(238, 129, 120, .1); }
.state-pill.history-status-cancelled { color: var(--muted); background: rgba(139, 157, 157, .1); }
.job-actions { display: flex; align-items: center; gap: 6px; }
.job-action { display: inline-flex; align-items: center; gap: 5px; min-height: 28px; border: 1px solid var(--line-bright); border-radius: 4px; padding: 5px 7px; color: var(--muted); background: transparent; font-size: 9px; white-space: nowrap; }
.job-action:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.job-action.warning-action:hover:not(:disabled) { border-color: var(--amber); color: var(--amber); }
.job-action:disabled { cursor: not-allowed; opacity: .55; }
.job-detail-card { grid-column: 1 / -1; margin: 0 0 10px 42px; border-left: 2px solid var(--line-bright); padding: 9px 12px; background: var(--panel-soft); }
.job-detail-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 7px; color: var(--muted); font-size: 10px; }
.job-detail-head strong { color: var(--ink); font-weight: 560; }
.job-detail-loading { display: flex; align-items: center; gap: 7px; color: var(--dim); font-size: 10px; }
.job-detail-list { display: grid; gap: 5px; padding: 9px 0 4px 42px; }
.job-detail { display: flex; justify-content: space-between; gap: 12px; color: var(--dim); font-size: 10px; }
.detail-status { color: var(--muted); }
.detail-status.history-status-completed { color: var(--cyan); }
.detail-status.history-status-running, .detail-status.history-status-queued, .detail-status.history-status-cancelling { color: var(--amber); }
.detail-status.history-status-blocked, .detail-status.history-status-failed, .detail-status.history-status-interrupted { color: var(--amber); }
.job-fingerprint { margin: 5px 0 0; overflow: hidden; color: var(--dim); font: 9px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }
.history-footnote { display: inline-flex; align-items: center; gap: 7px; margin: -6px 0 0; color: var(--dim); font-size: 10px; }
.history-footnote svg { color: var(--amber); flex: 0 0 auto; }
.preview-panel { border: 1px solid var(--line); border-radius: var(--radius); padding: 27px 22px 23px; background: var(--panel); }
.preview-meta { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.preview-meta strong { color: var(--ink); font-size: 12px; font-weight: 560; }
.preview-meta span { color: var(--muted); font-size: 10px; }
.candle-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.candle-table { width: 100%; min-width: 660px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.candle-table th, .candle-table td { padding: 10px 12px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.candle-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
.candle-table th:first-child, .candle-table td:first-child { text-align: left; }
.candle-table tr:last-child td { border-bottom: 0; }
.candle-table .candle-close { color: var(--cyan); }
@media (max-width: 1200px) { .history-grid { grid-template-columns: 1fr; } }
@media (max-width: 900px) {
  .sync-strip { grid-template-columns: 1fr 1fr; }
  .sync-strip > div:nth-child(2) { border-right: 0; }
  .sync-strip > div:nth-child(-n + 2) { border-bottom: 1px solid var(--line); }
  .sync-actions { grid-column: 1 / -1; border-right: 0 !important; }
}
@media (max-width: 620px) {
  .history-metrics { grid-template-columns: 1fr 1fr; }
  .history-metrics article { min-height: 84px; border-right: 0; border-bottom: 1px solid var(--line); }
  .history-metrics article:nth-child(odd) { border-right: 1px solid var(--line); }
  .history-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); }
  .history-metrics article:nth-child(3), .history-metrics article:nth-child(4) { border-bottom: 0; }
  .dataset-row { grid-template-columns: minmax(0, 1fr) 66px 34px; padding: 10px 0; }
  .dataset-quality { grid-column: 1 / -1; justify-content: start; }
  .job-row { grid-template-columns: 30px minmax(0, 1fr) auto; }
  .job-summary { display: none; }
  .job-row .state-pill { grid-column: 2 / -1; justify-self: start; }
  .job-actions { grid-column: 2 / -1; justify-self: start; }
  .job-detail-card { margin-left: 0; }
  .job-detail-list { padding-left: 0; }
  .job-detail { align-items: start; flex-direction: column; gap: 3px; }
  .date-grid { grid-template-columns: 1fr; }
  .preview-meta { align-items: start; flex-direction: column; gap: 5px; }
  .sync-panel { padding: 17px 14px; }
  .sync-meta > span { display: none; }
  .sync-strip { grid-template-columns: 1fr; }
  .sync-strip > div { border-right: 0 !important; border-bottom: 1px solid var(--line); }
  .sync-strip > div:last-child { border-bottom: 0; }
  .sync-actions { display: grid !important; grid-template-columns: 1fr 1fr; }
  .sync-button { width: 100%; }
  .archive-strip { grid-template-columns: 30px minmax(0, 1fr); }
  .archive-meta, .archive-strip .sync-button { grid-column: 2; justify-self: start; }
}
</style>
