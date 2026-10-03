<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { Activity, ArrowDown, ArrowLeftRight, ArrowUp, CandlestickChart, CircleAlert, LineChart, RefreshCw, ScanSearch, WifiOff } from 'lucide-vue-next';
import { api } from '../api';
import type { HistoricalSpreadResearch, HistoryCandleResponse, HistoryCoverage, HistoryDataset, MarketOverview, MarketSnapshot, OpportunityRecord, VenueStatus } from '../types';
import InstrumentCatalogPanel from './InstrumentCatalogPanel.vue';
import PublicTickerBoard from './PublicTickerBoard.vue';

type MarketRow = {
  venue_id: string;
  label: string;
  state: 'available' | 'missing' | 'error';
  close: string | null;
  change_pct: string | null;
  timestamp: string | null;
  row_count: number;
  detail: string;
};

const venueLabels: Record<string, string> = { binance: 'Binance', okx: 'OKX', bybit: 'Bybit' };
const symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'];
const intervals = [
  { value: '1h', label: '1 小时' },
  { value: '1d', label: '1 日' },
  { value: '5m', label: '5 分钟' },
];
const symbol = ref('BTC/USDT');
const interval = ref('1h');
const rows = ref<MarketRow[]>([]);
const comparison = ref<{ buy_venue_id: string; sell_venue_id: string; gross_spread_pct: string; estimated_cost_pct: string; net_spread_pct: string; research_positive: boolean } | null>(null);
const opportunity = ref<any>(null);
const opportunityHistory = ref<OpportunityRecord[]>([]);
const opportunityLoading = ref(false);
const loading = ref(false);
const error = ref('');
const spreadResult = ref<HistoricalSpreadResearch | null>(null);
const spreadLoading = ref(false);
const spreadError = ref('');
const spreadBuyVenue = ref('binance');
const spreadSellVenue = ref('bybit');
const spreadFeeBps = ref('10');
const spreadSlippageBps = ref('5');
const spreadThresholdBps = ref('0');
const spreadLookbackDays = ref('30');
const liveVenues = ['binance', 'okx', 'bybit'] as const;
const liveSymbol = 'BTC/USDT';
const liveChannelMeta = liveVenues.map((venue) => venue.toUpperCase()).join(' · ');
type LiveMarketRow = {
  venue_id: string;
  venue_label: string;
  symbol: string;
  native_symbol: string;
  state: 'live' | 'stale' | 'missing';
  best_bid: string | null;
  bid_quantity: string | null;
  best_ask: string | null;
  ask_quantity: string | null;
  spread_bps: number | null;
  received_at: string | null;
  sequence: number | null;
  detail: string;
};
const liveRows = ref<LiveMarketRow[]>([]);
const liveStatuses = ref<Record<string, VenueStatus>>({});
const liveLoading = ref(false);
const liveError = ref('');
let livePoll: ReturnType<typeof setInterval> | null = null;
const LIVE_DISPLAY_MAX_AGE_SECONDS = 10;

const liveFreshCount = computed(() => liveRows.value.filter((row) => row.state === 'live').length);
const liveChannelClass = computed(() => liveFreshCount.value === liveVenues.length ? 'connected' : liveFreshCount.value ? 'partial' : 'offline');
const liveChannelLabel = computed(() => {
  if (liveFreshCount.value === liveVenues.length && liveVenues.every((venue) => liveStatuses.value[venue]?.state === 'CONNECTED')) return '三家公开通道已连接';
  if (liveFreshCount.value === liveVenues.length) return '三家公开快照可用';
  if (liveFreshCount.value) return `公开通道部分可用（${liveFreshCount.value}/${liveVenues.length}）`;
  if (liveRows.value.some((row) => row.state === 'stale')) return '盘口已过期';
  return Object.keys(liveStatuses.value).length ? '公开通道未连接' : '正在读取通道';
});
const liveLastReceivedAt = computed(() => {
  const timestamps = liveVenues
    .map((venue) => liveStatuses.value[venue]?.last_received_at)
    .filter((timestamp): timestamp is string => Boolean(timestamp))
    .sort();
  return timestamps.length ? timestamps[timestamps.length - 1] : null;
});

const availableRows = computed(() => rows.value.filter((row) => row.state === 'available' && row.close));
const lowRow = computed(() => availableRows.value.reduce<MarketRow | null>((lowest, row) => (!lowest || Number(row.close) < Number(lowest.close) ? row : lowest), null));
const highRow = computed(() => availableRows.value.reduce<MarketRow | null>((highest, row) => (!highest || Number(row.close) > Number(highest.close) ? row : highest), null));
const spreadPct = computed(() => {
  if (!lowRow.value || !highRow.value || Number(lowRow.value.close) <= 0) return null;
  return ((Number(highRow.value.close) / Number(lowRow.value.close) - 1) * 100).toFixed(3);
});

const loadMarket = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [coverageResponse, compareResponse] = await Promise.all([
      api.get<HistoryCoverage>('/history/coverage'),
      api.get<{ comparison: typeof comparison.value }>('/market/compare', { params: { symbol: symbol.value, interval: interval.value } }),
    ]);
    comparison.value = compareResponse.data.comparison;
    rows.value = await Promise.all(['binance', 'okx', 'bybit'].map((venue) => loadVenue(coverageResponse.data.datasets, venue)));
    await loadOpportunityHistory();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '历史行情读取失败';
    rows.value = [];
    comparison.value = null;
  } finally {
    loading.value = false;
  }
};

const loadVenue = async (datasets: HistoryDataset[], venue: string): Promise<MarketRow> => {
  const dataset = datasets.find((item) => item.venue_id === venue && item.instrument_key === `${venue}:spot:${symbol.value}` && item.interval === interval.value);
  if (!dataset) {
    return { venue_id: venue, label: venueLabels[venue] || venue, state: 'missing', close: null, change_pct: null, timestamp: null, row_count: 0, detail: '没有该周期的已校验数据' };
  }
  try {
    const { data } = await api.get<HistoryCandleResponse>('/history/candles', {
      params: { venue_id: venue, symbol: symbol.value, interval: interval.value, limit: 2, tail: true },
    });
    const latest = data.items[data.items.length - 1];
    const previous = data.items[0];
    const change = previous && latest ? ((Number(latest.close) / Number(previous.close) - 1) * 100).toFixed(3) : null;
    return {
      venue_id: venue,
      label: venueLabels[venue] || venue,
      state: latest ? 'available' : 'error',
      close: latest?.close || null,
      change_pct: change,
      timestamp: latest?.close_time || null,
      row_count: dataset.row_count,
      detail: latest ? '尾部 K 线已读取' : '数据集为空',
    };
  } catch {
    return { venue_id: venue, label: venueLabels[venue] || venue, state: 'error', close: null, change_pct: null, timestamp: null, row_count: dataset.row_count, detail: '读取接口不可用' };
  }
};

const scanOpportunity = async () => {
  opportunityLoading.value = true;
  try {
    const { data } = await api.post('/market/opportunities/scan', { symbol: symbol.value, buy_venue_id: 'binance', sell_venue_id: 'okx' });
    opportunity.value = data;
    await loadOpportunityHistory();
  } catch (cause: any) {
    opportunity.value = { state: 'BLOCKED', blocking_reason: cause.response?.data?.detail || 'L2_SNAPSHOT_UNAVAILABLE', note: '当前没有可用的同步盘口快照。' };
  } finally {
    opportunityLoading.value = false;
  }
};

const loadOpportunityHistory = async () => {
  try {
    const { data } = await api.get<{ items: OpportunityRecord[] }>('/market/opportunities/history', { params: { limit: 8 } });
    opportunityHistory.value = data.items;
  } catch {
    opportunityHistory.value = [];
  }
};

const loadLiveMarket = async () => {
  if (liveLoading.value) return;
  liveLoading.value = true;
  liveError.value = '';
  try {
    const { data: overview } = await api.get<MarketOverview>('/market/overview');
    liveStatuses.value = Object.fromEntries(overview.venues.map((status) => [status.venue_id, status]));
    const snapshotResults = await Promise.all(liveVenues.map(async (venue) => {
        try {
          const { data } = await api.get<MarketSnapshot>('/market/snapshot', {
            params: { instrument_key: `${venue}:spot:${liveSymbol}` },
          });
          return { venue, snapshot: data, error: '' };
        } catch (cause: any) {
          return { venue, snapshot: null, error: cause.response?.data?.detail || liveStatuses.value[venue]?.reason || '盘口快照不可用' };
        }
      }));
    liveRows.value = snapshotResults.map(({ venue, snapshot, error }) => {
      const venueStatus = liveStatuses.value[venue];
      if (!snapshot) {
        return {
          venue_id: venue,
          venue_label: venueLabels[venue] || venue,
          symbol: liveSymbol,
          native_symbol: venue === 'okx' ? 'BTC-USDT' : 'BTCUSDT',
          state: 'missing',
          best_bid: null,
          bid_quantity: null,
          best_ask: null,
          ask_quantity: null,
          spread_bps: null,
          received_at: null,
          sequence: null,
          detail: error,
        };
      }
      const bid = snapshot.bids[0] || null;
      const ask = snapshot.asks[0] || null;
      const ageSeconds = Math.max(0, (Date.now() - new Date(snapshot.received_timestamp).getTime()) / 1000);
      const isLive = venueStatus?.state === 'CONNECTED' && Number.isFinite(ageSeconds) && ageSeconds <= LIVE_DISPLAY_MAX_AGE_SECONDS;
      const bidPrice = bid ? Number(bid.price) : NaN;
      const askPrice = ask ? Number(ask.price) : NaN;
      return {
        venue_id: venue,
        venue_label: venueLabels[venue] || venue,
        symbol: liveSymbol,
        native_symbol: snapshot.native_symbol,
        state: isLive ? 'live' : 'stale',
        best_bid: bid?.price || null,
        bid_quantity: bid?.quantity || null,
        best_ask: ask?.price || null,
        ask_quantity: ask?.quantity || null,
        spread_bps: bidPrice > 0 && askPrice >= bidPrice ? Number(((askPrice / bidPrice - 1) * 10000).toFixed(2)) : null,
        received_at: snapshot.received_timestamp,
        sequence: snapshot.sequence,
        detail: isLive ? '公开 WebSocket 已更新' : venueStatus?.reason || '已有快照但已过期',
      };
    });
    if (!liveRows.value.some((row) => row.state === 'live')) {
      const reasons = liveVenues.map((venue) => liveStatuses.value[venue]?.reason).filter(Boolean);
      liveError.value = reasons.length ? reasons.join('；') : '当前没有新鲜盘口快照';
    }
  } catch (cause: any) {
    liveError.value = cause.response?.data?.detail || '实时盘口状态读取失败';
  } finally {
    liveLoading.value = false;
  }
};

const submitSpreadResearch = async () => {
  spreadLoading.value = true;
  spreadError.value = '';
  spreadResult.value = null;
  try {
    const lookback = Math.max(1, Math.min(365, Number(spreadLookbackDays.value) || 30));
    const end = new Date();
    const start = new Date(end.getTime() - lookback * 24 * 60 * 60 * 1000);
    const { data } = await api.get<HistoricalSpreadResearch>('/market/spread-history', {
      params: {
        symbol: symbol.value,
        buy_venue_id: spreadBuyVenue.value,
        sell_venue_id: spreadSellVenue.value,
        interval: interval.value,
        fee_bps: spreadFeeBps.value,
        slippage_bps: spreadSlippageBps.value,
        min_net_spread_bps: spreadThresholdBps.value,
        start_at: start.toISOString(),
        end_at: end.toISOString(),
        limit: 20,
      },
    });
    spreadResult.value = data;
  } catch (cause: any) {
    spreadError.value = cause.response?.data?.detail || '历史价差研究被阻断';
  } finally {
    spreadLoading.value = false;
  }
};

const formatPrice = (value: string | null) => value === null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 8 }).format(Number(value));
const formatPct = (value: string | null) => {
  if (value === null) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric >= 0 ? '+' : ''}${numeric.toFixed(3)}%` : '—';
};
const formatDate = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false }) : '—';
const statusText = (state: MarketRow['state']) => ({ available: '可读取', missing: '暂无覆盖', error: '读取异常' }[state]);
const venueLabel = (venue: string) => venueLabels[venue] || venue.toUpperCase();

onMounted(async () => {
  await Promise.all([loadMarket(), loadLiveMarket()]);
  livePoll = setInterval(loadLiveMarket, 5000);
});

onUnmounted(() => {
  if (livePoll !== null) clearInterval(livePoll);
});
</script>

<template>
  <section class="market-center" aria-labelledby="market-title">
    <section class="page-heading market-heading">
      <div><p class="kicker">MARKET DATA / 03</p><h1 id="market-title">行情中心</h1><p class="muted">从已校验历史 K 线读取多市场价格，作为后续筛选、回测和价差研究的统一入口。</p></div>
      <button class="icon-button" type="button" title="刷新行情数据" aria-label="刷新行情数据" :disabled="loading" @click="loadMarket"><RefreshCw :size="17" :class="{ spinning: loading }" /></button>
    </section>

    <div class="market-controls">
      <label class="market-control"><span>交易对</span><select v-model="symbol" @change="loadMarket"><option v-for="item in symbols" :key="item" :value="item">{{ item }}</option></select></label>
      <label class="market-control"><span>周期</span><select v-model="interval" @change="loadMarket"><option v-for="item in intervals" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
      <div class="market-read-note"><CandlestickChart :size="15" /><span>读取端：本地 Manifest 数据集</span><button class="opportunity-button" type="button" title="扫描同步盘口机会" @click="scanOpportunity"><ScanSearch :size="14" /> 扫描盘口</button></div>
    </div>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>

    <PublicTickerBoard />

    <InstrumentCatalogPanel />

    <section class="market-metrics" aria-label="行情比较摘要">
      <article><span>已读取市场</span><strong>{{ availableRows.length }}<small>/ 3</small></strong><em>当前选择</em></article>
      <article><span>历史末价差</span><strong>{{ spreadPct === null ? '—' : `${spreadPct}%` }}</strong><em>仅为同周期末根对比</em></article>
      <article><span>成本后估算</span><strong :class="comparison && comparison.research_positive ? 'positive' : 'warn'">{{ comparison ? `${Number(comparison.net_spread_pct).toFixed(3)}%` : '—' }}</strong><em>{{ comparison ? `${comparison.buy_venue_id.toUpperCase()} → ${comparison.sell_venue_id.toUpperCase()}` : '至少两个市场' }}</em></article>
      <article><span>数据状态</span><strong class="metric-word">{{ loading ? '读取中' : availableRows.length ? '可研究' : '待补齐' }}</strong><em>不代表实时可成交</em></article>
    </section>

    <section class="market-panel live-market-panel" aria-labelledby="live-market-title">
      <div class="section-heading">
        <div><p class="kicker">PUBLIC L2 STREAM</p><h2 id="live-market-title">实时盘口</h2></div>
        <span class="section-meta">{{ liveChannelMeta }} · READ ONLY</span>
      </div>
      <div class="live-market-toolbar">
        <span class="live-channel-state" :class="liveChannelClass"><span class="live-channel-dot" />{{ liveChannelLabel }}</span>
        <span v-if="liveLastReceivedAt">最后接收 {{ formatDate(liveLastReceivedAt) }}</span>
        <span>数据源：Binance / OKX / Bybit Spot 公共 WebSocket</span>
      </div>
      <div v-if="liveError" class="inline-error live-error" role="alert"><CircleAlert :size="14" /><span>{{ liveError }}</span></div>
      <div v-if="liveLoading && !liveRows.length" class="opportunity-empty"><RefreshCw :size="20" class="spinning" /><p>正在读取公开盘口</p></div>
      <div v-else class="live-market-grid">
        <article v-for="row in liveRows" :key="`${row.venue_id}:${row.symbol}`" class="live-market-card" :class="row.state">
          <header class="live-market-card-head"><div><strong>{{ row.venue_label }}</strong><small>{{ row.symbol }} · {{ row.native_symbol }} · Spot</small></div><span class="live-state-pill" :class="row.state">{{ row.state === 'live' ? '实时' : row.state === 'stale' ? '过期' : '暂无' }}</span></header>
          <div class="live-quote-grid"><div><span>买一</span><strong>{{ formatPrice(row.best_bid) }}</strong><small>{{ row.bid_quantity || '—' }}</small></div><div><span>卖一</span><strong>{{ formatPrice(row.best_ask) }}</strong><small>{{ row.ask_quantity || '—' }}</small></div></div>
          <footer><span>{{ row.detail }}</span><strong>{{ row.spread_bps === null ? '—' : `${row.spread_bps} bps` }}</strong><small>{{ row.received_at ? formatDate(row.received_at) : '等待数据' }} · SEQ {{ row.sequence ?? '—' }}</small></footer>
        </article>
      </div>
      <p class="market-disclaimer">实时盘口只用于观察和研究，服务端不保存私有凭据、不创建订单；没有新鲜盘口时机会扫描会自动阻断。</p>
    </section>

    <section class="market-panel" aria-labelledby="market-table-title">
      <div class="section-heading"><div><p class="kicker">CROSS-VENUE SNAPSHOT</p><h2 id="market-table-title">市场末根报价</h2></div><Activity :size="18" class="section-icon" /></div>
      <div class="market-table-wrap">
        <table class="market-table">
          <thead><tr><th>市场</th><th>末根收盘</th><th>区间变化</th><th>数据行数</th><th>时间（UTC）</th><th>状态</th></tr></thead>
          <tbody><tr v-for="row in rows" :key="row.venue_id"><td class="market-name"><span class="market-mark" :class="row.state"><Activity v-if="row.state === 'available'" :size="14" /><WifiOff v-else :size="14" /></span><strong>{{ row.label }}</strong></td><td class="market-price">{{ formatPrice(row.close) }}</td><td :class="{ positive: row.change_pct !== null && Number(row.change_pct) >= 0, negative: row.change_pct !== null && Number(row.change_pct) < 0 }"><ArrowUp v-if="row.change_pct !== null && Number(row.change_pct) >= 0" :size="13" /><ArrowDown v-else-if="row.change_pct !== null" :size="13" />{{ formatPct(row.change_pct) }}</td><td>{{ row.row_count ? new Intl.NumberFormat('zh-CN').format(row.row_count) : '—' }}</td><td>{{ formatDate(row.timestamp) }}</td><td><span class="state-pill" :class="row.state">{{ statusText(row.state) }}</span><small class="market-detail">{{ row.detail }}</small></td></tr></tbody>
        </table>
      </div>
      <p class="market-disclaimer">价差指标使用各市场最后一根已归档 K 线，不校验同时刻盘口、手续费、转账、滑点或库存，因此不能直接作为套利下单信号。</p>
    </section>

    <section class="market-panel opportunity-panel" aria-labelledby="opportunity-title">
      <div class="section-heading"><div><p class="kicker">EXECUTABLE EDGE GATE</p><h2 id="opportunity-title">盘口机会扫描</h2></div><span class="section-meta">L2 ONLY</span></div>
      <div v-if="opportunityLoading" class="opportunity-empty"><RefreshCw :size="20" class="spinning" /><p>正在读取同步盘口</p></div>
      <div v-else-if="!opportunity" class="opportunity-empty"><ScanSearch :size="22" /><p>点击“扫描盘口”读取当前机会</p><small>没有盘口快照时会直接阻断。</small></div>
      <div v-else class="opportunity-result"><div class="opportunity-state" :class="opportunity.state === 'VALIDATED' ? 'positive' : 'warn'"><ScanSearch :size="16" /><strong>{{ opportunity.state === 'VALIDATED' ? '通过成本门禁' : '已阻断' }}</strong><span>{{ opportunity.blocking_reason || 'NET_EDGE_BELOW_ZERO' }}</span></div><div class="opportunity-metrics"><article><span>买入</span><strong>{{ opportunity.buy_price || '—' }}</strong><small>{{ opportunity.buy_venue_id || '—' }}</small></article><article><span>卖出</span><strong>{{ opportunity.sell_price || '—' }}</strong><small>{{ opportunity.sell_venue_id || '—' }}</small></article><article><span>可成交数量</span><strong>{{ opportunity.quantity || '—' }}</strong><small>基于最小一档深度</small></article><article><span>净边际</span><strong :class="Number(opportunity.net_edge_quote) > 0 ? 'positive' : 'warn'">{{ opportunity.net_edge_quote || '—' }}</strong><small>USDT</small></article></div><p class="opportunity-note">{{ opportunity.note }}</p><span v-if="opportunity.record_id" class="opportunity-record-id">已记录 {{ opportunity.record_id.slice(0, 16) }}</span></div>
      <div v-if="opportunityHistory.length" class="opportunity-history"><div class="opportunity-history-head"><span>最近扫描记录</span><small>{{ opportunityHistory.length }} 条</small></div><div class="opportunity-history-list"><article v-for="record in opportunityHistory" :key="record.record_id" :class="record.state === 'VALIDATED' ? 'positive' : 'warn'"><div><strong>{{ record.symbol }}</strong><small>{{ record.buy_venue_id }} → {{ record.sell_venue_id }} · {{ formatDate(record.observed_at) }}</small></div><span>{{ record.state === 'VALIDATED' ? `净边际 ${record.net_edge_quote}` : record.blocking_reason || '已阻断' }}</span><em v-if="record.alerted">已告警</em></article></div></div>
    </section>

    <section class="market-panel spread-research-panel" aria-labelledby="spread-research-title">
      <div class="section-heading">
        <div><p class="kicker">HISTORICAL SPREAD RESEARCH</p><h2 id="spread-research-title">历史价差研究</h2></div>
        <span class="section-meta">RESEARCH ONLY</span>
      </div>
      <form class="spread-form" @submit.prevent="submitSpreadResearch">
        <div class="spread-form-row spread-form-row-main">
          <label><span>买入市场</span><select v-model="spreadBuyVenue"><option v-for="venue in ['binance', 'okx', 'bybit']" :key="venue" :value="venue">{{ venueLabel(venue) }}</option></select></label>
          <ArrowLeftRight :size="16" class="spread-arrow" aria-hidden="true" />
          <label><span>卖出市场</span><select v-model="spreadSellVenue"><option v-for="venue in ['binance', 'okx', 'bybit']" :key="venue" :value="venue">{{ venueLabel(venue) }}</option></select></label>
          <label><span>回看天数</span><input v-model="spreadLookbackDays" type="number" min="1" max="365" step="1" /></label>
        </div>
        <div class="spread-form-row spread-form-row-costs">
          <label><span>单腿费率（bps）</span><input v-model="spreadFeeBps" type="number" min="0" max="1000" step="1" /></label>
          <label><span>单腿滑点（bps）</span><input v-model="spreadSlippageBps" type="number" min="0" max="1000" step="1" /></label>
          <label><span>最低净价差（bps）</span><input v-model="spreadThresholdBps" type="number" min="0" max="10000" step="1" /></label>
          <button class="spread-submit" type="submit" :disabled="spreadLoading || spreadBuyVenue === spreadSellVenue"><LineChart v-if="!spreadLoading" :size="15" /><RefreshCw v-else :size="15" class="spinning" /><span>{{ spreadLoading ? '计算中' : '运行历史研究' }}</span></button>
        </div>
      </form>
      <div v-if="spreadError" class="inline-error" role="alert"><CircleAlert :size="14" /> <span>{{ spreadError }}</span></div>
      <div v-if="spreadResult" class="spread-result">
        <div class="spread-result-status"><LineChart :size="15" /><strong>{{ venueLabel(spreadResult.buy_venue_id) }} → {{ venueLabel(spreadResult.sell_venue_id) }}</strong><span>{{ spreadResult.symbol }} · {{ spreadResult.interval }} · {{ spreadResult.aligned_candle_count.toLocaleString('zh-CN') }} 根对齐</span></div>
        <div class="spread-metrics">
          <article><span>平均毛价差</span><strong>{{ formatPct(spreadResult.average_gross_spread_pct) }}</strong><small>收盘价比值</small></article>
          <article><span>平均净价差</span><strong :class="Number(spreadResult.average_net_spread_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(spreadResult.average_net_spread_pct) }}</strong><small>已扣双边成本</small></article>
          <article><span>达到阈值</span><strong :class="spreadResult.opportunity_count ? 'positive' : 'warn'">{{ spreadResult.opportunity_count.toLocaleString('zh-CN') }}</strong><small>{{ spreadResult.opportunity_ratio_pct }}% 观察点</small></article>
          <article><span>峰值净价差</span><strong :class="Number(spreadResult.max_net_spread_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(spreadResult.max_net_spread_pct) }}</strong><small>最低 {{ formatPct(spreadResult.min_net_spread_pct) }}</small></article>
        </div>
        <div class="spread-datasets"><span>{{ venueLabel(spreadResult.buy_venue_id) }} {{ spreadResult.buy_dataset.row_count.toLocaleString('zh-CN') }} 根</span><span>{{ venueLabel(spreadResult.sell_venue_id) }} {{ spreadResult.sell_dataset.row_count.toLocaleString('zh-CN') }} 根</span><span>成本 {{ spreadResult.cost_model.total_cost_bps }} bps</span><span>对齐率 {{ spreadResult.alignment_ratio_pct }}%</span></div>
        <div class="spread-table-wrap">
          <table class="spread-table">
            <thead><tr><th>时间（UTC）</th><th>买入收盘</th><th>卖出收盘</th><th>毛价差</th><th>净价差</th></tr></thead>
            <tbody><tr v-for="item in spreadResult.recent_observations" :key="item.timestamp"><td>{{ formatDate(item.timestamp) }}</td><td>{{ formatPrice(item.buy_close) }}</td><td>{{ formatPrice(item.sell_close) }}</td><td>{{ formatPct(item.gross_spread_pct) }}</td><td :class="Number(item.net_spread_pct) >= 0 ? 'positive' : 'negative'">{{ formatPct(item.net_spread_pct) }}</td></tr></tbody>
          </table>
        </div>
        <p class="market-disclaimer">{{ spreadResult.note }}</p>
      </div>
      <div v-else class="spread-empty"><LineChart :size="22" /><p>选择当前交易对和周期后运行一次历史研究</p><small>缺少任一市场或质量不通过时不会生成结果。</small></div>
    </section>
  </section>
</template>

<style scoped>
.market-center { display: grid; gap: 30px; min-width: 0; }
.market-center > * { min-width: 0; }
.market-center .market-panel > *, .spread-research-panel > * { min-width: 0; }
.market-heading { margin-bottom: 0; }
.market-controls { display: flex; align-items: end; gap: 14px; padding: 18px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.market-control { display: grid; gap: 7px; min-width: 150px; color: var(--muted); font-size: 11px; }
.market-control select { min-height: 38px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; color: var(--ink); background: var(--input-bg); }
.market-read-note { display: inline-flex; align-items: center; gap: 7px; margin-left: auto; padding-bottom: 10px; color: var(--dim); font-size: 10px; }
.market-read-note svg { color: var(--amber); }
.opportunity-button { display: inline-flex; align-items: center; gap: 6px; min-height: 30px; margin-left: 10px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 6px 9px; color: var(--muted); background: transparent; font-size: 10px; }
.opportunity-button:hover { border-color: var(--cyan); color: var(--cyan); }
.market-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.market-metrics article { display: grid; gap: 8px; min-height: 120px; padding: 20px 24px; border-right: 1px solid var(--line); }
.market-metrics article:last-child { border-right: 0; }
.market-metrics span, .market-metrics em { color: var(--dim); font-size: 11px; }
.market-metrics em { font-style: normal; }
.market-metrics strong { align-self: center; color: var(--ink); font-size: 27px; font-weight: 560; }
.market-metrics small { margin-left: 5px; color: var(--dim); font-size: 14px; font-weight: 450; }
.market-metrics .metric-word { font-size: 20px; }
.market-panel { border: 1px solid var(--line); border-radius: var(--radius); padding: 27px 22px 20px; background: var(--panel); }
.market-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.market-table { width: 100%; min-width: 760px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.market-table th, .market-table td { padding: 13px 12px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.market-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
.market-table th:first-child, .market-table td:first-child { text-align: left; }
.market-table tr:last-child td { border-bottom: 0; }
.market-name { display: flex; align-items: center; gap: 9px; color: var(--ink) !important; }
.market-mark { display: grid; place-items: center; width: 27px; height: 27px; border: 1px solid var(--line-bright); color: var(--cyan); }
.market-mark.missing, .market-mark.error { color: var(--amber); }
.market-price { color: var(--ink) !important; font-weight: 560; }
.positive { color: var(--cyan) !important; }
.negative { color: var(--red) !important; }
.positive svg, .negative svg { vertical-align: -2px; margin-right: 3px; }
.market-table .state-pill.available { color: var(--cyan); background: rgba(108, 229, 208, .1); }
.market-table .state-pill.missing, .market-table .state-pill.error { color: var(--amber); background: rgba(228, 179, 109, .1); }
.market-detail { display: block; margin-top: 5px; color: var(--dim); font-size: 9px; }
.market-disclaimer { display: block; min-width: 0; margin: 16px 0 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.live-market-panel { display: grid; gap: 16px; }
.live-market-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 18px; color: var(--dim); font-size: 10px; }
.live-channel-state { display: inline-flex; align-items: center; gap: 7px; color: var(--amber); }
.live-channel-state.connected { color: var(--cyan); }
.live-channel-state.partial { color: var(--amber); }
.live-channel-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 4px color-mix(in srgb, currentColor 12%, transparent); }
.live-market-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.live-market-card { display: grid; gap: 16px; min-width: 0; border: 1px solid var(--line); border-top: 2px solid var(--line-bright); padding: 16px; background: var(--panel-soft); }
.live-market-card.live { border-top-color: var(--cyan); }
.live-market-card.stale { border-top-color: var(--amber); }
.live-market-card.missing { border-top-color: var(--red); }
.live-market-card-head { display: flex; align-items: start; justify-content: space-between; gap: 10px; min-width: 0; }
.live-market-card-head > div { display: grid; gap: 4px; min-width: 0; }
.live-market-card-head strong { color: var(--ink); font-size: 16px; font-weight: 600; }
.live-market-card-head small { color: var(--dim); font-size: 9px; }
.live-state-pill { flex: 0 0 auto; border: 1px solid var(--line-bright); padding: 4px 6px; color: var(--dim); font-size: 9px; }
.live-state-pill.live { border-color: rgba(108, 229, 208, .45); color: var(--cyan); }
.live-state-pill.stale { border-color: rgba(228, 179, 109, .45); color: var(--amber); }
.live-state-pill.missing { border-color: rgba(238, 129, 120, .45); color: var(--red); }
.live-quote-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.live-quote-grid > div { display: grid; gap: 5px; min-width: 0; border-left: 1px solid var(--line-bright); padding-left: 10px; }
.live-quote-grid span, .live-quote-grid small { color: var(--dim); font-size: 9px; }
.live-quote-grid strong { overflow: hidden; color: var(--ink); font: 600 16px Consolas, monospace; text-overflow: ellipsis; }
.live-market-card footer { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 5px 10px; border-top: 1px solid var(--line); padding-top: 11px; }
.live-market-card footer span, .live-market-card footer small { min-width: 0; overflow: hidden; color: var(--dim); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.live-market-card footer strong { color: var(--cyan); font: 600 11px Consolas, monospace; }
.live-market-card footer small { grid-column: 1 / -1; }
.live-error { display: flex; align-items: center; gap: 7px; }
.opportunity-empty { display: grid; justify-items: center; gap: 8px; min-height: 130px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.opportunity-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.opportunity-empty small { color: var(--dim); font-size: 10px; }
.opportunity-result { display: grid; gap: 14px; }
.opportunity-state { display: flex; align-items: center; gap: 8px; border-left: 2px solid currentColor; padding: 8px 10px; background: rgba(228, 179, 109, .06); font-size: 10px; }
.opportunity-state strong { color: inherit; font-size: 12px; font-weight: 570; }
.opportunity-state span { color: var(--dim); }
.opportunity-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.opportunity-metrics article { display: grid; gap: 5px; min-height: 79px; padding: 11px 12px; border-right: 1px solid var(--line); }
.opportunity-metrics article:last-child { border-right: 0; }
.opportunity-metrics span, .opportunity-metrics small { color: var(--dim); font-size: 10px; }
.opportunity-metrics strong { color: var(--ink); font: 570 15px Consolas, monospace; }
.opportunity-note { margin: 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.opportunity-record-id { color: var(--dim); font: 9px Consolas, monospace; }
.opportunity-history { display: grid; gap: 9px; border-top: 1px solid var(--line); padding-top: 12px; }
.opportunity-history-head { display: flex; justify-content: space-between; color: var(--muted); font-size: 10px; }
.opportunity-history-head small { color: var(--dim); font: 9px Consolas, monospace; }
.opportunity-history-list { display: grid; gap: 6px; }
.opportunity-history-list article { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 10px; border-left: 2px solid currentColor; padding: 8px 10px; background: var(--panel-soft); }
.opportunity-history-list article > div { display: grid; gap: 3px; min-width: 0; }
.opportunity-history-list strong { color: var(--ink); font-size: 10px; }
.opportunity-history-list small, .opportunity-history-list span, .opportunity-history-list em { color: var(--dim); font-size: 9px; font-style: normal; white-space: nowrap; }
.opportunity-history-list span { color: currentColor; }
.opportunity-history-list em { color: var(--amber); }
.spread-research-panel { display: grid; gap: 18px; }
.spread-form { display: grid; gap: 12px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.spread-form-row { display: grid; align-items: end; gap: 10px; }
.spread-form-row-main { grid-template-columns: minmax(130px, 1fr) 18px minmax(130px, 1fr) minmax(110px, .7fr); }
.spread-form-row-costs { grid-template-columns: repeat(3, minmax(110px, 1fr)) minmax(150px, 1.2fr); }
.spread-form label { display: grid; gap: 7px; color: var(--muted); font-size: 10px; }
.spread-form input, .spread-form select { width: 100%; min-height: 36px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 7px 9px; color: var(--ink); background: var(--input-bg); outline: none; }
.spread-form input:focus, .spread-form select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.spread-arrow { align-self: center; margin-top: 18px; color: var(--amber); }
.spread-submit { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 36px; border: 1px solid var(--cyan); border-radius: 5px; color: #11201e; background: var(--cyan); font-size: 11px; font-weight: 700; }
.spread-submit:hover:not(:disabled) { background: #94f0df; }
.spread-submit:disabled { cursor: not-allowed; opacity: .55; }
.spread-research-panel .inline-error { display: flex; align-items: center; gap: 7px; }
.spread-result { display: grid; gap: 14px; min-width: 0; }
.spread-result > *, .spread-metrics > * { min-width: 0; }
.spread-result-status { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; min-width: 0; border-left: 2px solid var(--cyan); padding: 8px 10px; color: var(--cyan); background: rgba(108, 229, 208, .06); font-size: 10px; }
.spread-result-status strong { color: var(--ink); font-size: 12px; font-weight: 570; }
.spread-result-status span { min-width: 0; color: var(--dim); }
.spread-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.spread-metrics article { display: grid; gap: 6px; min-height: 90px; padding: 12px; border-right: 1px solid var(--line); }
.spread-metrics article:last-child { border-right: 0; }
.spread-metrics span, .spread-metrics small { color: var(--dim); font-size: 10px; }
.spread-metrics strong { align-self: center; color: var(--ink); font-size: 19px; font-weight: 570; }
.spread-datasets { display: flex; flex-wrap: wrap; gap: 7px 16px; color: var(--dim); font-size: 10px; }
.spread-datasets span { display: inline-flex; align-items: center; gap: 5px; }
.spread-datasets span::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: var(--amber); }
.spread-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.spread-table { width: 100%; min-width: 620px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.spread-table th, .spread-table td { padding: 10px 9px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.spread-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
.spread-table th:first-child, .spread-table td:first-child { text-align: left; }
.spread-table tr:last-child td { border-bottom: 0; }
.spread-empty { display: grid; justify-items: center; gap: 8px; min-height: 140px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.spread-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.spread-empty small { color: var(--dim); font-size: 10px; }
@media (max-width: 980px) { .market-metrics { grid-template-columns: repeat(2, 1fr); } .market-metrics article:nth-child(even) { border-right: 0; } .market-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } }
@media (max-width: 760px) { .spread-form-row-main, .spread-form-row-costs { grid-template-columns: 1fr 1fr; } .spread-arrow { display: none; } .spread-submit { grid-column: 1 / -1; } }
@media (max-width: 700px) { .market-controls { align-items: stretch; flex-wrap: wrap; } .market-control { flex: 1 1 140px; } .market-read-note { width: 100%; margin-left: 0; padding-bottom: 0; } .market-metrics, .spread-metrics { grid-template-columns: 1fr; } .market-metrics article { min-height: 84px; border-right: 0; border-bottom: 1px solid var(--line); } .market-metrics article:last-child { border-bottom: 0; } .spread-metrics article { min-height: 74px; border-right: 0; border-bottom: 1px solid var(--line); } .spread-metrics article:last-child { border-bottom: 0; } }
@media (max-width: 760px) { .live-market-grid { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .opportunity-metrics { grid-template-columns: 1fr 1fr; } .opportunity-metrics article:nth-child(2), .opportunity-metrics article:nth-child(4) { border-right: 0; } .opportunity-metrics article:nth-child(-n + 2) { border-bottom: 1px solid var(--line); } }
</style>
