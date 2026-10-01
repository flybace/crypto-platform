<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { HistoryCandle, MarketHistorySeries } from '../types';

const props = defineProps<{
  symbol: string;
  interval: string;
  mode: 'candles' | 'comparison';
  venueId: string;
  series: MarketHistorySeries[];
}>();

const venueLabels: Record<string, string> = { binance: 'Binance', okx: 'OKX', bybit: 'Bybit' };
const venueColors: Record<string, string> = { binance: '#69d3bd', okx: '#e3b56c', bybit: '#85a6df' };
const container = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let resizeObserver: ResizeObserver | null = null;
let activeSeries: ISeriesApi<'Line'> | ISeriesApi<'Candlestick'> | null = null;

const selectedSeries = computed(() => props.series.find((item) => item.venue_id === props.venueId));
const chartLabel = computed(() => props.mode === 'candles'
  ? `${venueLabels[props.venueId] || props.venueId} ${props.symbol} ${props.interval} 蜡烛图`
  : `${props.symbol} 在 Binance、OKX、Bybit 的区间相对涨跌走势`);
const candleRows = computed(() => selectedSeries.value?.items.slice(-10).reverse() || []);
const alignedComparison = computed(() => {
  const available = props.series.filter((item) => item.status === 'ready' && item.items.length);
  if (!available.length) return [];
  const markets = available.map((item) => {
    const candles = item.items
      .map((candle) => {
        const point = candlePoint(candle);
        return point && Number(candle.close) > 0 ? { candle, point } : null;
      })
      .filter((entry): entry is NonNullable<typeof entry> => entry !== null)
      .sort((left, right) => Number(left.point.time) - Number(right.point.time));
    const byTime = new Map<number, (typeof candles)[number]>();
    candles.forEach((entry) => byTime.set(Number(entry.point.time), entry));
    return { venue_id: item.venue_id, candles, byTime };
  });
  const firstMarket = markets[0];
  if (!firstMarket) return [];
  const commonTimes = [...firstMarket.byTime.keys()]
    .filter((time) => markets.every((market) => market.byTime.has(time)))
    .sort((left, right) => left - right);
  const start = commonTimes[0];
  const end = commonTimes[commonTimes.length - 1];
  if (start === undefined || end === undefined) return [];

  return markets.flatMap((market) => {
    const baseline = market.byTime.get(start);
    const latest = market.byTime.get(end);
    if (!baseline || !latest) return [];
    const baselinePrice = Number(baseline.candle.close);
    const latestPrice = Number(latest.candle.close);
    if (!Number.isFinite(baselinePrice) || baselinePrice <= 0 || !Number.isFinite(latestPrice)) return [];
    const points = market.candles
      .filter(({ point }) => Number(point.time) >= start && Number(point.time) <= end)
      .map(({ candle, point }) => ({
        time: point.time,
        value: (Number(candle.close) / baselinePrice - 1) * 100,
      }))
      .filter((point) => Number.isFinite(point.value));
    return [{
      venue_id: market.venue_id,
      latest: latest.candle,
      change: (latestPrice / baselinePrice - 1) * 100,
      points,
    }];
  });
});
const comparisonRows = computed(() => alignedComparison.value);

const formatNumber = (value: string | number | null | undefined) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: parsed < 1 ? 8 : 4 }).format(parsed);
};
const formatTime = (value: string) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false });
};
const candlePoint = (item: HistoryCandle) => {
  const time = Math.floor(Date.parse(item.open_time) / 1000);
  const open = Number(item.open);
  const high = Number(item.high);
  const low = Number(item.low);
  const close = Number(item.close);
  if (!Number.isFinite(time) || ![open, high, low, close].every(Number.isFinite)) return null;
  return {
    time: time as UTCTimestamp,
    open,
    high,
    low,
    close,
  };
};

const renderChart = () => {
  if (!container.value) return;
  chart?.remove();
  chart = null;
  activeSeries = null;
  const width = Math.max(300, container.value.clientWidth);
  chart = createChart(container.value, {
    width,
    height: 300,
    layout: {
      background: { type: ColorType.Solid, color: '#12191b' },
      textColor: '#aab5b2',
      fontFamily: 'Inter, Noto Sans SC, Microsoft YaHei, sans-serif',
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: '#273231' },
      horzLines: { color: '#273231' },
    },
    rightPriceScale: { borderColor: '#3b4a48', minimumWidth: 72 },
    timeScale: {
      borderColor: '#3b4a48',
      timeVisible: props.interval !== '1d',
      secondsVisible: false,
      rightOffset: 3,
    },
    localization: { locale: 'zh-CN' },
    crosshair: { vertLine: { color: '#72817e', labelBackgroundColor: '#33413e' }, horzLine: { color: '#72817e', labelBackgroundColor: '#33413e' } },
  });

  if (props.mode === 'candles') {
    const data = (selectedSeries.value?.items || []).map(candlePoint).filter((item): item is NonNullable<typeof item> => item !== null);
    if (data.length) {
      const latestPoint = data[data.length - 1];
      if (!latestPoint) return;
      const firstPrice = latestPoint.close;
      const precision = firstPrice < 1 ? 8 : firstPrice < 100 ? 4 : 2;
      activeSeries = chart.addSeries(CandlestickSeries, {
        upColor: '#35b39e',
        downColor: '#e97872',
        borderVisible: false,
        wickUpColor: '#35b39e',
        wickDownColor: '#e97872',
        priceFormat: { type: 'price', precision, minMove: 10 ** -precision },
      });
      activeSeries.setData(data);
    }
  } else {
    alignedComparison.value.forEach((marketSeries) => {
      const line = chart?.addSeries(LineSeries, {
        color: venueColors[marketSeries.venue_id] || '#d0d7d4',
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        priceFormat: {
          type: 'custom',
          minMove: 0.01,
          formatter: (value: number) => `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`,
        },
      });
      if (!line) return;
      line.setData(marketSeries.points);
      activeSeries = line;
    });
  }
  chart.timeScale().fitContent();
};

const resizeChart = () => {
  if (chart && container.value) chart.applyOptions({ width: Math.max(300, container.value.clientWidth) });
};

onMounted(async () => {
  await nextTick();
  renderChart();
  if (container.value) {
    resizeObserver = new ResizeObserver(resizeChart);
    resizeObserver.observe(container.value);
  }
});

watch(
  () => [props.mode, props.venueId, props.interval, props.series],
  async () => {
    await nextTick();
    renderChart();
  },
  { deep: true },
);

onUnmounted(() => {
  resizeObserver?.disconnect();
  chart?.remove();
});
</script>

<template>
  <div class="market-chart-frame">
    <div ref="container" class="market-chart-canvas" role="img" :aria-label="chartLabel" tabindex="0" />
    <p class="chart-a11y-note">
      {{ mode === 'candles' ? '下表列出最近十根 OHLCV 数据。' : comparisonRows.length ? '曲线和摘要表使用所有已载入市场共同存在的 K 线时间点；共同起点归一为 0%。' : '已载入市场没有共同 K 线时间点，暂时无法进行时间对齐比较。' }}
    </p>
    <div v-if="mode === 'candles'" class="chart-data-table-wrap">
      <table class="chart-data-table">
        <caption>{{ venueLabels[venueId] || venueId }} 最近 K 线 OHLCV</caption>
        <thead><tr><th scope="col">时间</th><th scope="col">开盘</th><th scope="col">最高</th><th scope="col">最低</th><th scope="col">收盘</th><th scope="col">成交量</th></tr></thead>
        <tbody>
          <tr v-for="item in candleRows" :key="item.open_time">
            <th scope="row">{{ formatTime(item.open_time) }}</th>
            <td>{{ formatNumber(item.open) }}</td><td>{{ formatNumber(item.high) }}</td><td>{{ formatNumber(item.low) }}</td><td>{{ formatNumber(item.close) }}</td><td>{{ formatNumber(item.volume) }}</td>
          </tr>
          <tr v-if="!candleRows.length"><td colspan="6">该市场当前没有可展示的 K 线。</td></tr>
        </tbody>
      </table>
    </div>
    <div v-else class="chart-data-table-wrap">
      <table class="chart-data-table">
        <caption>{{ symbol }} 各市场相对走势数据</caption>
        <thead><tr><th scope="col">市场</th><th scope="col">最新收盘</th><th scope="col">区间变化</th><th scope="col">时间</th></tr></thead>
        <tbody>
          <tr v-for="row in comparisonRows" :key="row.venue_id">
            <th scope="row"><span class="chart-legend-dot" :style="{ background: venueColors[row.venue_id] }" />{{ venueLabels[row.venue_id] || row.venue_id }}</th>
            <td>{{ formatNumber(row.latest.close) }}</td>
            <td :class="row.change >= 0 ? 'chart-positive' : 'chart-negative'">{{ Number.isFinite(row.change) ? `${row.change >= 0 ? '+' : ''}${row.change.toFixed(2)}%` : '—' }}</td>
            <td>{{ formatTime(row.latest.close_time) }}</td>
          </tr>
          <tr v-if="!comparisonRows.length"><td colspan="4">已载入市场没有共同 K 线时间点。</td></tr>
        </tbody>
      </table>
    </div>
    <p class="chart-credit"><a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView Lightweight Charts™</a> · Copyright (c) 2025 TradingView, Inc.</p>
  </div>
</template>

<style scoped>
.market-chart-frame{min-width:0;display:grid;gap:10px}.market-chart-canvas{width:100%;height:300px;min-width:0;outline:none}.market-chart-canvas:focus-visible{box-shadow:0 0 0 2px var(--cyan)}.chart-a11y-note{margin:0;color:var(--dim);font-size:11px;line-height:1.5}.chart-data-table-wrap{max-width:100%;overflow-x:auto;border-top:1px solid var(--line)}.chart-data-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.chart-data-table caption{padding:9px 0;color:var(--muted);font-size:11px;text-align:left}.chart-data-table th,.chart-data-table td{padding:8px 9px;border-bottom:1px solid var(--line);color:var(--muted);font-size:10px;text-align:right;white-space:nowrap}.chart-data-table thead th{color:var(--dim);font-size:9px;font-weight:600}.chart-data-table th:first-child,.chart-data-table td:first-child{text-align:left}.chart-data-table tbody th{font-weight:550}.chart-legend-dot{display:inline-block;width:8px;height:8px;margin-right:7px;border-radius:50%;vertical-align:middle}.chart-positive{color:var(--cyan)!important}.chart-negative{color:var(--red)!important}
.chart-credit{margin:0;color:var(--dim);font-size:9px;line-height:1.5}.chart-credit a{color:inherit;text-decoration:underline;text-underline-offset:2px}
@media(max-width:600px){.market-chart-canvas{height:260px}.chart-data-table th,.chart-data-table td{padding:8px 7px;font-size:10px}}
</style>
