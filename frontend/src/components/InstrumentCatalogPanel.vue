<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { CircleCheck, Database, RefreshCw, Search, ShieldAlert, WifiOff } from 'lucide-vue-next';
import { api } from '../api';
import type { MarketInstrumentCatalog } from '../types';

const venues = [
  { id: 'binance', label: 'Binance' },
  { id: 'okx', label: 'OKX' },
  { id: 'bybit', label: 'Bybit' },
];
const quoteAssets = ['USDT', 'USDC', 'BTC', 'ETH'];
const venueId = ref('binance');
const quoteAsset = ref('USDT');
const search = ref('');
const catalog = ref<MarketInstrumentCatalog | null>(null);
const loading = ref(false);
const error = ref('');

const loadCatalog = async (refresh = false) => {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await api.get<MarketInstrumentCatalog>('/market/instruments', {
      params: {
        venue_id: venueId.value,
        quote_asset: quoteAsset.value,
        search: search.value.trim(),
        limit: 80,
        refresh,
      },
    });
    catalog.value = data;
  } catch (cause: any) {
    catalog.value = null;
    error.value = cause.response?.data?.detail || '公开品种目录读取失败';
  } finally {
    loading.value = false;
  }
};

const switchVenue = () => loadCatalog();
const switchQuote = () => loadCatalog();
const statusLabel = (status: string) => ({ LIVE: '公开接口', CACHED: '本地缓存', BLOCKED: '网络阻断' }[status] || status);
const statusNote = (status: string) => ({ LIVE: '刚刚从交易所公开品种接口读取', CACHED: '当前端点未刷新，显示最近一次成功缓存', BLOCKED: '当前端点不可达，未生成占位品种' }[status] || '等待公开接口返回');
const formatNumber = (value: string) => {
  const [integer, fraction] = value.split('.');
  if (!fraction) return value;
  const trimmed = fraction.replace(/0+$/, '');
  return `${integer}.${trimmed || '0'}`;
};
const formatNotional = (value: string, asset: string) => value === '0' ? '未提供' : `${formatNumber(value)} ${asset}`;
const formatDate = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false }) : '—';

onMounted(loadCatalog);
</script>

<template>
  <section class="instrument-panel market-panel" aria-labelledby="instrument-title">
    <div class="section-heading">
      <div>
        <p class="kicker">PUBLIC INSTRUMENT CATALOG</p>
        <h2 id="instrument-title">可交易品种</h2>
      </div>
      <div class="catalog-heading-tools">
        <span v-if="catalog" class="catalog-count">{{ catalog.total_count }} PAIRS</span>
        <button class="icon-button" type="button" title="刷新公开品种目录" aria-label="刷新公开品种目录" :disabled="loading" @click="loadCatalog(true)">
          <RefreshCw :size="17" :class="{ spinning: loading }" />
        </button>
      </div>
    </div>

    <form class="catalog-controls" @submit.prevent="loadCatalog()">
      <label><span>交易所</span><select v-model="venueId" @change="switchVenue"><option v-for="venue in venues" :key="venue.id" :value="venue.id">{{ venue.label }}</option></select></label>
      <label><span>计价资产</span><select v-model="quoteAsset" @change="switchQuote"><option v-for="asset in quoteAssets" :key="asset" :value="asset">{{ asset }}</option></select></label>
      <label class="catalog-search"><span>搜索</span><div><Search :size="14" /><input v-model="search" type="search" placeholder="BTC 或 BTCUSDT" /></div></label>
      <button class="catalog-submit" type="submit" :disabled="loading"><Search :size="15" /><span>查询</span></button>
    </form>

    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="catalog" class="catalog-status" :class="catalog.status.toLowerCase()">
      <CircleCheck v-if="catalog.status === 'LIVE'" :size="16" />
      <Database v-else-if="catalog.status === 'CACHED'" :size="16" />
      <WifiOff v-else :size="16" />
      <strong>{{ statusLabel(catalog.status) }}</strong>
      <span>{{ catalog.returned_count }} / {{ catalog.total_count }} 个 {{ catalog.quote_asset }} 现货</span>
      <small>{{ statusNote(catalog.status) }} · {{ formatDate(catalog.fetched_at) }}</small>
    </div>

    <div v-if="loading" class="catalog-empty"><RefreshCw :size="20" class="spinning" /><p>正在读取公开品种</p></div>
    <div v-else-if="catalog && catalog.items.length" class="catalog-table-wrap">
      <table class="catalog-table">
        <thead><tr><th>标准交易对</th><th>交易所符号</th><th>价格最小变动</th><th>数量步长</th><th>最小数量</th><th>最小名义</th></tr></thead>
        <tbody>
          <tr v-for="item in catalog.items" :key="item.instrument_key">
            <td><strong>{{ item.canonical_symbol }}</strong><small>{{ item.base_asset }} / {{ item.quote_asset }}</small></td>
            <td>{{ item.native_symbol }}</td>
            <td>{{ formatNumber(item.price_tick) }}</td>
            <td>{{ formatNumber(item.quantity_step) }}</td>
            <td>{{ formatNumber(item.min_quantity) }}</td>
            <td>{{ formatNotional(item.min_notional, item.quote_asset) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="catalog-empty">
      <ShieldAlert :size="21" />
      <p>{{ catalog?.status === 'BLOCKED' ? '该交易所当前没有可用目录' : '没有符合条件的公开品种' }}</p>
      <small>{{ catalog?.last_error?.kind || '调整交易所、计价资产或搜索条件后重试' }}</small>
    </div>
    <p v-if="catalog?.truncated" class="catalog-note">当前只展示前 {{ catalog.returned_count }} 条，继续输入搜索词以缩小目录。</p>
    <p class="catalog-footnote">品种目录来自公开现货接口；交易状态和下单约束会变化，不能替代账户权限、余额与真实执行风控。</p>
  </section>
</template>

<style scoped>
.instrument-panel { display: grid; gap: 18px; min-width: 0; }
.catalog-heading-tools { display: flex; align-items: center; gap: 12px; }
.catalog-count { color: var(--dim); font-size: 9px; letter-spacing: .12em; }
.catalog-controls { display: flex; align-items: end; gap: 12px; padding: 0 0 18px; border-bottom: 1px solid var(--line); }
.catalog-controls label { display: grid; gap: 7px; min-width: 145px; color: var(--muted); font-size: 11px; }
.catalog-controls select, .catalog-controls input { min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; color: var(--ink); background: #13191b; outline: none; }
.catalog-controls select:focus, .catalog-controls input:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.catalog-search { flex: 1 1 230px; }
.catalog-search div { display: flex; align-items: center; gap: 8px; min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 0 10px; color: var(--dim); background: #13191b; }
.catalog-search input { width: 100%; min-height: 35px; border: 0; padding: 0; background: transparent; box-shadow: none !important; }
.catalog-submit { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 37px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 13px; color: var(--muted); background: transparent; font-size: 11px; }
.catalog-submit:hover:not(:disabled) { border-color: var(--cyan); color: var(--cyan); }
.catalog-submit:disabled { cursor: wait; opacity: .55; }
.catalog-status { display: flex; align-items: center; gap: 8px; min-height: 34px; border-left: 2px solid var(--cyan); padding: 7px 10px; color: var(--cyan); background: rgba(108, 229, 208, .06); font-size: 11px; }
.catalog-status.cached { border-color: var(--amber); color: var(--amber); background: rgba(228, 179, 109, .06); }
.catalog-status.blocked { border-color: var(--red); color: var(--red); background: rgba(238, 129, 120, .06); }
.catalog-status strong { font-size: 11px; font-weight: 600; }
.catalog-status span { color: var(--muted); }
.catalog-status small { margin-left: auto; color: var(--dim); font-size: 9px; }
.catalog-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.catalog-table { width: 100%; min-width: 760px; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.catalog-table th, .catalog-table td { padding: 10px 11px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 10px; text-align: right; white-space: nowrap; }
.catalog-table th { color: var(--dim); background: var(--panel-soft); font-size: 9px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
.catalog-table th:first-child, .catalog-table td:first-child { text-align: left; }
.catalog-table tr:last-child td { border-bottom: 0; }
.catalog-table td:first-child { display: grid; gap: 4px; }
.catalog-table td strong { color: var(--ink); font-size: 11px; font-weight: 570; }
.catalog-table td small { color: var(--dim); font-size: 9px; }
.catalog-empty { display: grid; justify-items: center; gap: 8px; min-height: 106px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }
.catalog-empty p { margin: 0; color: var(--muted); font-size: 12px; }
.catalog-empty small { color: var(--dim); font-size: 10px; }
.catalog-note, .catalog-footnote { margin: -7px 0 0; color: var(--dim); font-size: 10px; line-height: 1.6; }
.catalog-footnote { margin-top: -2px; }
@media (max-width: 700px) {
  .catalog-controls { align-items: stretch; flex-wrap: wrap; }
  .catalog-controls label { flex: 1 1 135px; }
  .catalog-search { flex-basis: 100% !important; }
  .catalog-submit { flex: 1 1 100%; }
  .catalog-status { align-items: start; flex-wrap: wrap; }
  .catalog-status small { width: 100%; margin-left: 24px; }
}
</style>
