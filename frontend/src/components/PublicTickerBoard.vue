<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, CandlestickChart, CircleAlert, RefreshCw, Search, WifiOff } from 'lucide-vue-next';
import { api } from '../api';
import type { PublicTickerItem, PublicTickerMarket, PublicTickerResponse } from '../types';

const CoinMarketDetail = defineAsyncComponent(() => import('./CoinMarketDetail.vue'));

const venueIds = ['binance', 'okx', 'bybit'];
const venueLabels: Record<string, string> = { binance: 'Binance', okx: 'OKX', bybit: 'Bybit' };
const quoteAssets = ['USDT', 'USDC', 'BTC', 'ETH'];
const sortOptions = [
  { value: 'volume', label: '成交量' },
  { value: 'change', label: '涨幅' },
  { value: 'spread', label: '跨市场价差' },
  { value: 'symbol', label: '币种' },
];

const quoteAsset = ref('USDT');
const search = ref('');
const submittedSearch = ref('');
const sortBy = ref<'volume' | 'change' | 'spread' | 'symbol'>('volume');
const offset = ref(0);
const limit = 60;
const data = ref<PublicTickerResponse | null>(null);
const selectedCoin = ref<PublicTickerItem | null>(null);
const loading = ref(false);
const error = ref('');
let poll: ReturnType<typeof setInterval> | null = null;

const pageLabel = computed(() => {
  if (!data.value || !data.value.total_count) return '暂无币种';
  return `${offset.value + 1}-${offset.value + (data.value.items.length || 0)} / ${data.value.total_count}`;
});
const availableVenueCount = computed(() => data.value?.venues.filter((venue) => venue.status !== 'BLOCKED').length || 0);

const load = async (refresh = false) => {
  if (loading.value) return;
  loading.value = true;
  error.value = '';
  try {
    const response = await api.get<PublicTickerResponse>('/market/tickers', {
      params: {
        quote_asset: quoteAsset.value,
        search: submittedSearch.value,
        sort_by: sortBy.value,
        limit,
        offset: offset.value,
        refresh,
      },
    });
    data.value = response.data;
    if (selectedCoin.value) {
      selectedCoin.value = response.data.items.find((item) => item.symbol === selectedCoin.value?.symbol) || selectedCoin.value;
    }
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '全市场行情读取失败';
  } finally {
    loading.value = false;
  }
};

const submitSearch = () => {
  submittedSearch.value = search.value.trim().toUpperCase();
  offset.value = 0;
  load(true);
};

const changeFilter = () => {
  offset.value = 0;
  load(true);
};

const goPage = (direction: number) => {
  const next = Math.max(0, offset.value + direction * limit);
  if (next === offset.value) return;
  offset.value = next;
  load(false);
};

const market = (item: PublicTickerItem, venueId: string): PublicTickerMarket | null => item.markets[venueId] || null;
const formatPrice = (value: string | null | undefined) => {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: numeric < 1 ? 8 : 4 }).format(numeric);
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
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleTimeString('zh-CN', { hour12: false });
};
const changeClass = (value: string | null | undefined) => Number(value) >= 0 ? 'positive' : 'negative';
const trendLabel = (trend: string) => trend === 'up' ? '上涨' : trend === 'down' ? '下跌' : '横盘';
const trendIcon = (trend: string) => trend === 'down' ? ArrowDown : trend === 'up' ? ArrowUp : null;
const rangePosition = (item: PublicTickerItem) => {
  const values = venueIds.map((venue) => Number(market(item, venue)?.range_position_pct)).filter(Number.isFinite);
  return values.length ? Math.max(0, Math.min(100, values.reduce((sum, value) => sum + value, 0) / values.length)) : 0;
};
const openCoin = (item: PublicTickerItem) => { selectedCoin.value = item; };

onMounted(async () => {
  await load(true);
  poll = setInterval(() => load(true), 10000);
});

onUnmounted(() => {
  if (poll !== null) clearInterval(poll);
});
</script>

<template>
  <section class="ticker-board market-panel" aria-labelledby="ticker-board-title">
    <div class="section-heading ticker-heading">
      <div>
        <p class="kicker">PUBLIC 24H TICKERS</p>
        <h2 id="ticker-board-title">全市场行情</h2>
        <p class="ticker-subtitle">公开接口实时刷新价格、24 小时走势和各交易所报价。</p>
      </div>
      <div class="ticker-heading-meta">
        <span>{{ availableVenueCount }} / 3 市场可用</span>
        <button class="icon-button" type="button" title="刷新全市场行情" aria-label="刷新全市场行情" :disabled="loading" @click="load(true)">
          <RefreshCw :size="17" :class="{ spinning: loading }" />
        </button>
      </div>
    </div>

    <form class="ticker-toolbar" @submit.prevent="submitSearch">
      <label><span>计价资产</span><select v-model="quoteAsset" @change="changeFilter"><option v-for="asset in quoteAssets" :key="asset" :value="asset">{{ asset }}</option></select></label>
      <label class="ticker-search"><span>搜索币种</span><div><Search :size="14" /><input v-model="search" type="search" placeholder="BTC、ETH 或 BTC/USDT" /></div></label>
      <label><span>排序</span><select v-model="sortBy" @change="changeFilter"><option v-for="option in sortOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
      <button class="ticker-search-button" type="submit" :disabled="loading"><Search :size="15" /><span>查询</span></button>
    </form>

    <div class="ticker-status-row">
      <span class="ticker-live-dot" :class="{ active: data && !loading }" />
      <span>{{ pageLabel }}</span>
      <span>每 10 秒刷新</span>
      <span v-if="data?.as_of">最近行情 {{ formatTime(data.as_of) }}</span>
      <span class="ticker-status-note">数据源：Binance / OKX / Bybit 公共 REST ticker</span>
    </div>

    <div v-if="error" class="inline-error" role="alert"><CircleAlert :size="14" />{{ error }}</div>
    <div v-if="loading && !data" class="ticker-empty"><RefreshCw :size="21" class="spinning" /><p>正在读取全市场行情</p></div>
    <div v-else-if="data && data.items.length" class="ticker-table-wrap">
      <table class="ticker-table">
        <thead>
          <tr>
            <th>币种</th>
            <th v-for="venue in venueIds" :key="venue">{{ venueLabels[venue] }}价格 / 24H</th>
            <th>24H走势</th>
            <th>24H区间</th>
            <th>跨市场价差</th>
            <th>成交额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in data.items" :key="item.symbol">
            <td class="ticker-symbol">
              <button class="ticker-symbol-trigger" type="button" :aria-label="`查看 ${item.symbol} 的市场详情和走势图`" @click="openCoin(item)">
                <strong>{{ item.base_asset }}</strong><small>{{ item.symbol }}</small>
                <span class="ticker-detail-action"><CandlestickChart :size="14" />查看走势</span>
              </button>
            </td>
            <td v-for="venue in venueIds" :key="`${item.symbol}-${venue}`" class="venue-quote" :class="{ best: item.best_venue_id === venue }">
              <template v-if="market(item, venue)">
                <strong>{{ formatPrice(market(item, venue)?.last_price) }}</strong>
                <small :class="changeClass(market(item, venue)?.change_pct)">{{ formatPct(market(item, venue)?.change_pct) }}</small>
              </template>
              <span v-else class="missing-quote">—</span>
            </td>
            <td class="trend-cell">
              <span class="trend-pill" :class="item.trend"><component v-if="trendIcon(item.trend)" :is="trendIcon(item.trend)" :size="13" />{{ trendLabel(item.trend) }}</span>
              <small>{{ formatPct(item.change_pct) }}</small>
            </td>
            <td class="range-cell">
              <div class="range-track" aria-hidden="true"><span :style="{ width: `${rangePosition(item)}%` }" /></div>
              <small>{{ rangePosition(item).toFixed(0) }}% 高位</small>
            </td>
            <td class="spread-cell"><strong>{{ formatPct(item.spread_pct) }}</strong><small>{{ item.best_venue_id ? `${venueLabels[item.best_venue_id]} 最低` : '单一市场' }}</small></td>
            <td class="volume-cell"><strong>{{ formatCompact(item.quote_volume_24h) }}</strong><small>{{ item.quote_asset }}</small></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="ticker-empty"><WifiOff :size="21" /><p>当前没有可用的公开 ticker</p><small>请检查网络设置里的交易所 REST 地址和 HTTP 代理。</small></div>

    <footer class="ticker-footer">
      <span>价格和涨跌为公开 24 小时 ticker；绿色标记是当前返回价格最低的市场，不是可执行套利信号。</span>
      <div class="ticker-pagination">
        <button class="icon-button" type="button" title="上一页" aria-label="上一页" :disabled="offset === 0 || loading" @click="goPage(-1)"><ArrowLeft :size="15" /></button>
        <span>{{ pageLabel }}</span>
        <button class="icon-button" type="button" title="下一页" aria-label="下一页" :disabled="!data?.truncated || loading" @click="goPage(1)"><ArrowRight :size="15" /></button>
      </div>
    </footer>
  </section>
  <CoinMarketDetail v-if="selectedCoin" :item="selectedCoin" @close="selectedCoin = null" />
</template>

<style scoped>
.ticker-board{display:grid;gap:17px}.ticker-heading{align-items:start;margin-bottom:0}.ticker-subtitle{margin:8px 0 0;color:var(--dim);font-size:11px}.ticker-heading-meta{display:flex;align-items:center;gap:12px;color:var(--dim);font:10px Consolas,monospace}.ticker-toolbar{display:flex;align-items:end;gap:12px;padding-bottom:16px;border-bottom:1px solid var(--line)}.ticker-toolbar label{display:grid;gap:7px;min-width:130px;color:var(--muted);font-size:10px}.ticker-toolbar select,.ticker-toolbar input{min-height:37px;border:1px solid var(--line-bright);border-radius:5px;padding:8px 10px;color:var(--ink);background:#13191b;outline:none;font-size:11px}.ticker-toolbar select:focus,.ticker-toolbar input:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(108,229,208,.1)}.ticker-search{flex:1 1 240px}.ticker-search div{display:flex;align-items:center;gap:8px;min-height:37px;border:1px solid var(--line-bright);border-radius:5px;padding:0 10px;color:var(--dim);background:#13191b}.ticker-search input{width:100%;min-height:35px;border:0;padding:0;background:transparent;box-shadow:none!important}.ticker-search-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:37px;border:1px solid var(--line-bright);border-radius:5px;padding:8px 13px;color:var(--muted);background:transparent;font-size:11px}.ticker-search-button:hover:not(:disabled){border-color:var(--cyan);color:var(--cyan)}.ticker-status-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--dim);font-size:10px}.ticker-live-dot{width:7px;height:7px;border-radius:50%;background:var(--dim)}.ticker-live-dot.active{background:var(--cyan);box-shadow:0 0 0 4px rgba(108,229,208,.1)}.ticker-status-note{margin-left:auto}.ticker-table-wrap{overflow-x:auto;border:1px solid var(--line)}.ticker-table{width:100%;min-width:1050px;border-collapse:collapse;font-variant-numeric:tabular-nums}.ticker-table th,.ticker-table td{padding:11px 12px;border-bottom:1px solid var(--line);color:var(--muted);font-size:10px;text-align:right;white-space:nowrap}.ticker-table th{color:var(--dim);background:var(--panel-soft);font-size:9px;font-weight:650;letter-spacing:.08em}.ticker-table th:first-child,.ticker-table td:first-child{text-align:left}.ticker-table tr:last-child td{border-bottom:0}.ticker-symbol{display:grid;gap:4px;min-width:100px}.ticker-symbol strong{color:var(--ink);font-size:12px;font-weight:650}.ticker-symbol small,.venue-quote small,.trend-cell small,.range-cell small,.spread-cell small,.volume-cell small{color:var(--dim);font-size:9px}.venue-quote{display:grid;gap:4px;min-width:124px}.venue-quote strong{color:var(--ink);font:600 11px Consolas,monospace}.venue-quote.best{background:rgba(108,229,208,.05)}.venue-quote .positive,.trend-cell.up,.trend-pill.up{color:var(--cyan)}.venue-quote .negative,.trend-cell.down,.trend-pill.down{color:var(--red)}.missing-quote{color:var(--dim)}.trend-cell,.spread-cell,.volume-cell{display:grid;gap:4px}.trend-pill{display:inline-flex;align-items:center;justify-content:flex-end;gap:4px;font-size:10px}.trend-pill.flat{color:var(--amber)}.range-cell{min-width:110px}.range-track{width:90px;height:4px;margin-left:auto;border-radius:99px;background:#293333;overflow:hidden}.range-track span{display:block;height:100%;background:var(--cyan)}.spread-cell strong{color:var(--amber);font:600 11px Consolas,monospace}.volume-cell strong{color:var(--ink);font:600 11px Consolas,monospace}.ticker-empty{display:grid;justify-items:center;gap:9px;min-height:150px;place-content:center;border:1px dashed var(--line-bright);color:var(--dim);text-align:center}.ticker-empty p{margin:0;color:var(--muted);font-size:12px}.ticker-empty small{font-size:10px}.ticker-footer{display:flex;align-items:center;justify-content:space-between;gap:14px;color:var(--dim);font-size:10px;line-height:1.5}.ticker-pagination{display:flex;align-items:center;gap:7px;white-space:nowrap}.ticker-pagination .icon-button{width:28px;height:28px;border-color:var(--line)}
.ticker-symbol-trigger{display:grid;justify-items:start;gap:4px;min-width:108px;min-height:54px;border:1px solid transparent;border-radius:4px;padding:3px 6px;color:inherit;background:transparent;text-align:left}.ticker-symbol-trigger:hover,.ticker-symbol-trigger:focus-visible{border-color:var(--line-bright);outline:none;background:#182120}.ticker-symbol-trigger:focus-visible{box-shadow:0 0 0 2px var(--cyan)}.ticker-symbol-trigger strong{color:var(--ink);font-size:12px;font-weight:650}.ticker-symbol-trigger small{color:var(--dim);font-size:9px}.ticker-detail-action{display:inline-flex;align-items:center;gap:4px;min-height:24px;color:var(--cyan);font-size:9px}
@media(max-width:800px){.ticker-toolbar{align-items:stretch;flex-wrap:wrap}.ticker-toolbar label{flex:1 1 135px}.ticker-search{flex-basis:100%!important}.ticker-search-button{flex:1 1 100%}.ticker-status-note{width:100%;margin-left:17px}.ticker-footer{align-items:start;flex-direction:column}.ticker-pagination{width:100%;justify-content:space-between}}
</style>
