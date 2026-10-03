<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { CandlestickChart, CircleAlert, LineChart, RefreshCw, X } from 'lucide-vue-next';
import { api } from '../api';
import type { HistoryCandleResponse, HistoryJob, MarketHistorySeries, PublicTickerItem } from '../types';
import MarketHistoryChart from './MarketHistoryChart.vue';

const props = defineProps<{ item: PublicTickerItem }>();
const emit = defineEmits<{ close: [] }>();

const venueIds = ['binance', 'okx', 'bybit'];
const venueLabels: Record<string, string> = { binance: 'Binance', okx: 'OKX', bybit: 'Bybit' };
const intervals = [
  { value: '5m', label: '5 分钟' },
  { value: '1h', label: '1 小时' },
  { value: '1d', label: '1 日' },
] as const;
const dialog = ref<HTMLDialogElement | null>(null);
const interval = ref<(typeof intervals)[number]['value']>('1h');
const mode = ref<'comparison' | 'candles'>('comparison');
const selectedVenue = ref('binance');
const history = ref<MarketHistorySeries[]>([]);
const historyLoading = ref(false);
const historyError = ref('');
const backfillSubmitting = ref(false);
const backfillError = ref('');
const backfillJob = ref<HistoryJob | null>(null);
let requestVersion = 0;
let jobPoll: ReturnType<typeof setInterval> | null = null;

const missingVenues = computed(() => history.value.filter((item) => item.status === 'missing').map((item) => item.venue_id));
const historyReadyCount = computed(() => history.value.filter((item) => item.status === 'ready' && item.items.length).length);
const selectedMarket = computed(() => props.item.markets[selectedVenue.value] || null);
const jobFinished = computed(() => Boolean(backfillJob.value && !['queued', 'running', 'pending'].includes(backfillJob.value.status.toLowerCase())));

const formatPrice = (value: string | null | undefined) => {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: numeric < 1 ? 8 : 4 }).format(numeric) : '—';
};
const formatCompact = (value: string | null | undefined) => {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(numeric) : '—';
};
const formatPct = (value: string | null | undefined) => {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric >= 0 ? '+' : ''}${numeric.toFixed(2)}%` : '—';
};
const formatTime = (value: string | null | undefined) => {
  if (!value) return '时间不可用';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '时间不可用' : parsed.toLocaleString('zh-CN', { hour12: false });
};
const changeClass = (value: string | null | undefined) => Number(value) >= 0 ? 'positive' : 'negative';
const marketState = (venueId: string) => {
  const market = props.item.markets[venueId];
  return market ? '报价可用' : '当前无报价';
};

const loadHistory = async () => {
  const version = ++requestVersion;
  historyLoading.value = true;
  historyError.value = '';
  history.value = venueIds.map((venue_id) => ({ venue_id, status: 'loading', items: [] }));
  const results = await Promise.all(venueIds.map(async (venue_id): Promise<MarketHistorySeries> => {
    try {
      const { data } = await api.get<HistoryCandleResponse>('/history/candles', {
        params: { venue_id, symbol: props.item.symbol, interval: interval.value, limit: 500, tail: true },
      });
      return { venue_id, status: 'ready', items: data.items };
    } catch (cause: any) {
      if (cause.response?.status === 404) return { venue_id, status: 'missing', items: [], message: '尚无该市场的已校验历史数据' };
      return { venue_id, status: 'error', items: [], message: cause.response?.data?.detail || '历史数据读取失败' };
    }
  }));
  if (version !== requestVersion) return;
  history.value = results;
  historyLoading.value = false;
  if (results.every((item) => item.status === 'error')) historyError.value = '三个市场的 K 线均无法读取，请检查登录和后端状态。';
};

const pollHistoryJob = async () => {
  if (!backfillJob.value || jobFinished.value) return;
  try {
    const { data } = await api.get<HistoryJob>(`/history/jobs/${encodeURIComponent(backfillJob.value.job_id)}`);
    backfillJob.value = data;
    if (jobFinished.value) {
      if (jobPoll !== null) window.clearInterval(jobPoll);
      jobPoll = null;
      if (['completed', 'partial'].includes(data.status.toLowerCase())) await loadHistory();
    }
  } catch (cause: any) {
    backfillError.value = cause.response?.data?.detail || '历史任务状态读取失败';
    if (jobPoll !== null) window.clearInterval(jobPoll);
    jobPoll = null;
  }
};

const submitBackfill = async () => {
  if (!missingVenues.value.length || backfillSubmitting.value) return;
  backfillSubmitting.value = true;
  backfillError.value = '';
  const lookbackDays = interval.value === '5m' ? 7 : interval.value === '1h' ? 30 : 365;
  const end = new Date();
  const start = new Date(end.getTime() - lookbackDays * 86_400_000);
  try {
    const { data } = await api.post<HistoryJob>('/history/jobs', {
      venue_ids: missingVenues.value,
      symbols: [props.item.symbol],
      interval: interval.value,
      start_at: start.toISOString(),
      end_at: end.toISOString(),
    });
    backfillJob.value = data;
    await pollHistoryJob();
    if (!jobFinished.value) {
      if (jobPoll !== null) window.clearInterval(jobPoll);
      jobPoll = window.setInterval(() => void pollHistoryJob(), 2500);
    }
  } catch (cause: any) {
    backfillError.value = cause.response?.data?.detail || '补数任务提交失败';
  } finally {
    backfillSubmitting.value = false;
  }
};

const retryHistory = () => void loadHistory();
const handleDialogClose = () => emit('close');

watch(interval, () => {
  backfillJob.value = null;
  backfillError.value = '';
  if (jobPoll !== null) window.clearInterval(jobPoll);
  jobPoll = null;
  void loadHistory();
});

onMounted(() => {
  dialog.value?.showModal();
  void loadHistory();
});

onUnmounted(() => {
  requestVersion += 1;
  if (jobPoll !== null) window.clearInterval(jobPoll);
});
</script>

<template>
  <dialog ref="dialog" class="market-detail-dialog" aria-labelledby="market-detail-title" @close="handleDialogClose">
    <section class="market-detail-content">
      <header class="detail-header">
        <div>
          <p class="kicker">PUBLIC SPOT MARKET</p>
          <h2 id="market-detail-title">{{ item.symbol }} <span>币种行情详情</span></h2>
          <p class="detail-as-of">24 小时公开 ticker · {{ formatTime(item.timestamp) }}</p>
        </div>
        <button class="icon-button detail-close" type="button" aria-label="关闭行情详情" title="关闭" @click="dialog?.close()"><X :size="19" /></button>
      </header>

      <section class="detail-quotes" aria-label="三个市场当前价格">
        <article v-for="venue in venueIds" :key="venue" class="detail-quote-row">
          <div class="detail-venue-name"><strong>{{ venueLabels[venue] }}</strong><small>{{ marketState(venue) }}</small></div>
          <div class="detail-price-block">
            <strong class="detail-last-price">{{ formatPrice(item.markets[venue]?.last_price) }}</strong>
            <span :class="changeClass(item.markets[venue]?.change_pct)">{{ formatPct(item.markets[venue]?.change_pct) }} / 24H</span>
          </div>
          <div class="detail-range-block">
            <span>高 {{ formatPrice(item.markets[venue]?.high_24h) }}</span>
            <span>低 {{ formatPrice(item.markets[venue]?.low_24h) }}</span>
            <small>成交额 {{ formatCompact(item.markets[venue]?.quote_volume_24h) }} {{ item.quote_asset }}</small>
          </div>
          <small class="detail-market-time">{{ formatTime(item.markets[venue]?.timestamp) }}</small>
        </article>
      </section>

      <section class="detail-chart-section" aria-labelledby="detail-chart-title">
        <div class="detail-chart-heading">
          <div><h3 id="detail-chart-title">价格走势</h3><p>{{ historyReadyCount }}/3 市场有已校验 K 线 · 最多读取最近 500 根</p></div>
          <button class="icon-button" type="button" aria-label="重新读取 K 线" title="重新读取 K 线" :disabled="historyLoading" @click="retryHistory"><RefreshCw :size="16" :class="{ spinning: historyLoading }" /></button>
        </div>

        <div class="detail-chart-toolbar">
          <div class="segmented-control" role="group" aria-label="走势类型">
            <button type="button" :aria-pressed="mode === 'comparison'" @click="mode = 'comparison'"><LineChart :size="15" /><span>跨市场走势</span></button>
            <button type="button" :aria-pressed="mode === 'candles'" @click="mode = 'candles'"><CandlestickChart :size="15" /><span>K 线</span></button>
          </div>
          <div class="segmented-control interval-control" role="group" aria-label="K 线周期">
            <button v-for="option in intervals" :key="option.value" type="button" :aria-pressed="interval === option.value" @click="interval = option.value">{{ option.label }}</button>
          </div>
          <label v-if="mode === 'candles'" class="detail-market-select"><span>K 线市场</span><select v-model="selectedVenue"><option v-for="venue in venueIds" :key="venue" :value="venue">{{ venueLabels[venue] }}</option></select></label>
        </div>

        <div class="detail-series-legend" aria-label="图例">
          <span v-for="venue in venueIds" :key="venue" :class="{ unavailable: !history.find((series) => series.venue_id === venue && series.items.length) }">
            <i :class="`legend-${venue}`" />{{ venueLabels[venue] }}<small>{{ history.find((series) => series.venue_id === venue)?.status === 'missing' ? '无历史' : history.find((series) => series.venue_id === venue)?.status === 'error' ? '读取失败' : '' }}</small>
          </span>
        </div>

        <div v-if="historyLoading && !historyReadyCount" class="detail-chart-state" role="status"><RefreshCw :size="18" class="spinning" /><span>正在读取三个市场的 K 线</span></div>
        <div v-else-if="historyError" class="inline-error detail-error" role="alert"><CircleAlert :size="15" />{{ historyError }}<button type="button" @click="retryHistory">重试</button></div>
        <MarketHistoryChart v-else-if="historyReadyCount" :symbol="item.symbol" :interval="interval" :mode="mode" :venue-id="selectedVenue" :series="history" />
        <div v-else class="detail-chart-state detail-chart-empty">
          <CircleAlert :size="19" />
          <strong>当前周期没有可用的历史 K 线</strong>
          <span>可以为缺失市场提交公开历史补数任务；不会访问账户或下单。</span>
        </div>

        <div v-if="history.some((series) => series.status === 'missing')" class="detail-backfill-row">
          <div><strong>缺少 {{ missingVenues.map((venue) => venueLabels[venue]).join('、') }} 的 {{ interval }} 数据</strong><small>按周期回补近 {{ interval === '5m' ? 7 : interval === '1h' ? 30 : 365 }} 天，后台任务可在历史数据页查看。</small></div>
          <button class="detail-backfill-button" type="button" :disabled="backfillSubmitting || (backfillJob !== null && !jobFinished)" @click="submitBackfill"><RefreshCw v-if="backfillSubmitting || (backfillJob !== null && !jobFinished)" :size="15" class="spinning" /><span>{{ backfillJob !== null && !jobFinished ? `任务 ${backfillJob.status}` : jobFinished ? '再次补数' : '补充缺失数据' }}</span></button>
        </div>
        <div v-if="backfillJob" class="detail-job-state" role="status">历史任务 {{ backfillJob.status }}：完成 {{ backfillJob.completed }}/{{ backfillJob.total }}<span v-if="backfillJob.failed">，失败 {{ backfillJob.failed }}</span></div>
        <div v-if="backfillError" class="inline-error detail-error" role="alert"><CircleAlert :size="14" />{{ backfillError }}</div>
        <p class="detail-research-note">数据来自交易所公开行情与本地已校验历史数据，仅用于研究；不代表同时刻可成交价格，不连接私有账户，也不会生成订单。</p>
      </section>
    </section>
  </dialog>
</template>

<style scoped>
.market-detail-dialog{width:min(1080px,calc(100vw - 32px));max-width:none;max-height:calc(100dvh - 32px);margin:auto;border:1px solid var(--line-bright);border-radius:7px;padding:0;color:var(--ink);background:#141b1d;box-shadow:0 22px 80px rgba(0,0,0,.55);overflow:auto}.market-detail-dialog::backdrop{background:rgba(4,8,9,.78)}.market-detail-content{display:grid;gap:20px;padding:22px}.detail-header{display:flex;justify-content:space-between;align-items:start;gap:14px;border-bottom:1px solid var(--line);padding-bottom:16px}.detail-header .kicker{margin-bottom:6px}.detail-header h2{margin:0;font-size:21px;font-weight:620;line-height:1.35;overflow-wrap:anywhere}.detail-header h2 span{color:var(--muted);font-size:14px;font-weight:500}.detail-as-of{margin:6px 0 0;color:var(--dim);font-size:11px}.detail-close{flex:0 0 44px;width:44px;height:44px;border-color:var(--line-bright)}.detail-quotes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.detail-quote-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-content:start;gap:9px 14px;min-width:0;padding:14px;border-right:1px solid var(--line)}.detail-quote-row:last-child{border-right:0}.detail-venue-name,.detail-price-block,.detail-range-block{display:grid;gap:5px;min-width:0}.detail-venue-name strong{font-size:12px}.detail-venue-name small,.detail-range-block small,.detail-market-time{color:var(--dim);font-size:10px}.detail-price-block{text-align:right}.detail-last-price{font:650 17px Consolas,monospace;overflow-wrap:anywhere}.detail-price-block span{font-size:11px}.detail-price-block .positive{color:var(--cyan)}.detail-price-block .negative{color:var(--red)}.detail-range-block{grid-column:1 / -1;grid-template-columns:1fr 1fr;gap:6px 10px;color:var(--muted);font-size:10px;font-variant-numeric:tabular-nums}.detail-range-block small{grid-column:1 / -1}.detail-market-time{grid-column:1 / -1;overflow-wrap:anywhere}.detail-chart-section{display:grid;gap:13px;min-width:0}.detail-chart-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.detail-chart-heading h3{margin:0;font-size:15px;font-weight:600}.detail-chart-heading p{margin:4px 0 0;color:var(--dim);font-size:10px}.detail-chart-heading .icon-button{width:40px;height:40px;border:1px solid var(--line)}.detail-chart-toolbar{display:flex;align-items:end;flex-wrap:wrap;gap:10px}.segmented-control{display:flex;align-items:center;gap:3px;min-height:42px;border:1px solid var(--line);padding:3px;background:#101617}.segmented-control button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:34px;border:1px solid transparent;padding:6px 10px;color:var(--muted);background:transparent;font-size:11px}.segmented-control button[aria-pressed="true"]{border-color:#45625d;color:var(--cyan);background:#1b2927}.detail-market-select{display:grid;gap:5px;min-width:150px;color:var(--dim);font-size:10px}.detail-market-select select{min-height:40px;border:1px solid var(--line-bright);border-radius:4px;padding:7px 9px;color:var(--ink);background: var(--input-bg);font:inherit}.detail-series-legend{display:flex;flex-wrap:wrap;gap:9px 20px;color:var(--muted);font-size:11px}.detail-series-legend span{display:inline-flex;align-items:center;gap:6px}.detail-series-legend i{width:12px;height:3px;background:var(--line-bright)}.detail-series-legend .legend-binance{background:#69d3bd}.detail-series-legend .legend-okx{background:#e3b56c}.detail-series-legend .legend-bybit{background:#85a6df}.detail-series-legend .unavailable{opacity:.55}.detail-series-legend small{color:var(--dim);font-size:9px}.detail-chart-state{display:flex;align-items:center;justify-content:center;gap:10px;min-height:190px;border:1px dashed var(--line-bright);color:var(--muted);font-size:12px}.detail-chart-empty{flex-direction:column;text-align:center;padding:22px}.detail-chart-empty strong{color:var(--ink);font-size:13px}.detail-chart-empty span{max-width:440px;color:var(--dim);font-size:11px;line-height:1.6}.detail-error{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.detail-error button{border:0;padding:5px;color:var(--cyan);background:transparent;text-decoration:underline}.detail-backfill-row{display:flex;align-items:center;justify-content:space-between;gap:15px;border-top:1px solid var(--line);padding-top:13px}.detail-backfill-row>div{display:grid;gap:5px;min-width:0}.detail-backfill-row strong{font-size:11px;overflow-wrap:anywhere}.detail-backfill-row small,.detail-job-state{color:var(--dim);font-size:10px;line-height:1.5}.detail-backfill-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;flex:0 0 auto;border:1px solid var(--cyan);border-radius:4px;padding:8px 12px;color:#11201e;background:var(--cyan);font-size:11px;font-weight:650}.detail-backfill-button:disabled{opacity:.55}.detail-research-note{margin:0;border-left:2px solid var(--amber);padding:6px 10px;color:var(--dim);font-size:10px;line-height:1.6}.spinning{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:720px){.market-detail-dialog{inset:0;width:100%;height:100%;max-height:none;margin:0;border:0;border-radius:0}.market-detail-content{gap:15px;min-height:100%;padding:16px}.detail-quotes{grid-template-columns:1fr}.detail-quote-row{grid-template-columns:minmax(70px,.7fr) minmax(0,1fr);border-right:0;border-bottom:1px solid var(--line);padding:11px 5px}.detail-quote-row:last-child{border-bottom:0}.detail-price-block{text-align:right}.detail-range-block{grid-column:1 / -1}.detail-market-time{grid-column:1 / -1}.detail-chart-toolbar{align-items:stretch}.segmented-control{flex:1 1 100%;justify-content:stretch}.segmented-control button{flex:1 1 0;padding-inline:7px}.interval-control button{min-width:0}.detail-market-select{flex:1 1 100%}.detail-backfill-row{align-items:stretch;flex-direction:column}.detail-backfill-button{width:100%}}
@media(prefers-reduced-motion:reduce){.spinning{animation:none}}
</style>
