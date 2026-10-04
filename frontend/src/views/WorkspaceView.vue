<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Bell,
  Bot,
  Boxes,
  BriefcaseBusiness,
  CalendarClock,
  CandlestickChart,
  CircleUserRound,
  Database,
  FlaskConical,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Newspaper,
  RefreshCw,
  SearchCode,
  ScanSearch,
  ServerCog,
  Settings2,
  ShieldCheck,
  Sprout,
  Target,
  WifiOff,
  Wallet,
} from 'lucide-vue-next';
import { useRouter } from 'vue-router';
import { api } from '../api';
import { useAuthStore } from '../stores/auth';
import type { MarketOverview, MarketSummary, MarketSummaryQuote, VenueStatus } from '../types';
import HistoryDataCenter from '../components/HistoryDataCenter.vue';
import MarketCenter from '../components/MarketCenter.vue';
import AccountCenter from '../components/AccountCenter.vue';
import StrategyCenter from '../components/StrategyCenter.vue';
import NewsCenter from '../components/NewsCenter.vue';
import BacktestCenter from '../components/BacktestCenter.vue';
import PoolCenter from '../components/PoolCenter.vue';
import CoinPoolCenter from '../components/CoinPoolCenter.vue';
import ScreeningCenter from '../components/ScreeningCenter.vue';
import PaperTradingCenter from '../components/PaperTradingCenter.vue';
import RiskCenter from '../components/RiskCenter.vue';
import TaskCenter from '../components/TaskCenter.vue';
import ResearchCenter from '../components/ResearchCenter.vue';
import RuntimePlanCenter from '../components/RuntimePlanCenter.vue';
import StrategyIncubatorCenter from '../components/StrategyIncubatorCenter.vue';
import AdviceCenter from '../components/AdviceCenter.vue';
import AssistantCenter from '../components/AssistantCenter.vue';
import NotificationCenter from '../components/NotificationCenter.vue';
import SystemCenter from '../components/SystemCenter.vue';
import NetworkSettingsCenter from '../components/NetworkSettingsCenter.vue';
import OverviewCockpit from '../components/OverviewCockpit.vue';

const auth = useAuthStore();
const router = useRouter();
const overview = ref<MarketOverview | null>(null);
const marketSummary = ref<MarketSummary | null>(null);
type WorkspaceSection = 'overview' | 'plan' | 'history' | 'market' | 'account' | 'strategies' | 'research' | 'news' | 'advice' | 'pools' | 'screening' | 'incubator' | 'backtests' | 'paper' | 'risk' | 'tasks' | 'assistant' | 'notifications' | 'system' | 'network';
const activeSection = ref<WorkspaceSection>('overview');
const loading = ref(false);
const error = ref('');

const navItems: { key: WorkspaceSection; label: string; icon: typeof LayoutDashboard; enabled: boolean }[] = [
  { key: 'overview', label: '总览', icon: LayoutDashboard, enabled: true },
  { key: 'plan', label: '运行计划', icon: CalendarClock, enabled: true },
  { key: 'history', label: '历史数据', icon: Database, enabled: true },
  { key: 'market', label: '行情', icon: CandlestickChart, enabled: true },
  { key: 'account', label: '账户', icon: Wallet, enabled: true },
  { key: 'strategies', label: '策略', icon: FlaskConical, enabled: true },
  { key: 'research', label: '研究', icon: SearchCode, enabled: true },
  { key: 'news', label: '新闻', icon: Newspaper, enabled: true },
  { key: 'advice', label: '建议', icon: ScanSearch, enabled: true },
  { key: 'backtests', label: '回测', icon: BarChart3, enabled: true },
  { key: 'pools', label: '币池', icon: Boxes, enabled: true },
  { key: 'screening', label: '筛选', icon: Target, enabled: true },
  { key: 'incubator', label: '孵化池', icon: Sprout, enabled: true },
  { key: 'paper', label: '模拟盘', icon: BriefcaseBusiness, enabled: true },
  { key: 'risk', label: '风控', icon: ShieldCheck, enabled: true },
  { key: 'tasks', label: '任务', icon: ListChecks, enabled: true },
  { key: 'assistant', label: 'AI 能力', icon: Bot, enabled: true },
  { key: 'notifications', label: '通知', icon: Bell, enabled: true },
  { key: 'system', label: '系统', icon: ServerCog, enabled: true },
  { key: 'network', label: '网络设置', icon: Settings2, enabled: true },
];

const venueNames: Record<string, string> = {
  binance: 'Binance',
  okx: 'OKX',
  bybit: 'Bybit',
};

const connectedCount = computed(() => overview.value?.venues.filter((venue) => venue.state === 'CONNECTED').length || 0);
const lastUpdate = computed(() => {
  const values = overview.value?.venues.map((venue) => venue.last_received_at).filter(Boolean) || [];
  if (!values.length) return '暂无行情写入';
  const latest = values.sort()[values.length - 1];
  return new Date(latest as string).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
});
const sectionLabel = computed(() => navItems.find((item) => item.key === activeSection.value)?.label || '总览');
const summaryQuotes = computed(() => marketSummary.value?.quotes.slice(0, 9) || []);
const summarySpreads = computed(() => marketSummary.value?.spreads.slice(0, 6) || []);
const summaryAsOf = computed(() => marketSummary.value?.as_of ? formatTime(marketSummary.value.as_of) : '暂无数据');

const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  timeZone: 'UTC',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});
const formatNumber = (value: string | number, digits = 2) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
};
const formatPct = (value: string | number) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return `${parsed >= 0 ? '+' : ''}${parsed.toFixed(2)}%`;
};
const quoteTone = (row: MarketSummaryQuote) => Number(row.change_pct) >= 0 ? 'positive' : 'negative';

const stateLabel = (state: VenueStatus['state']) => {
  if (state === 'CONNECTED') return '已连接';
  if (state === 'DEGRADED') return '降级';
  if (state === 'STALE') return '数据过期';
  return '未连接';
};

const loadOverview = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [overviewResponse, summaryResponse] = await Promise.all([
      api.get<MarketOverview>('/market/overview'),
      api.get<MarketSummary>('/market/summary', { params: { interval: '1h' } }),
    ]);
    overview.value = overviewResponse.data;
    marketSummary.value = summaryResponse.data;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '市场状态读取失败';
  } finally {
    loading.value = false;
  }
};

const signOut = async () => {
  await auth.logout();
  await router.replace('/login');
};

onMounted(loadOverview);
</script>

<template>
  <main class="workspace-shell">
    <aside class="sidebar">
      <div class="sidebar-brand"><span class="brand-dot" /><span>VECTOR / CRYPTO</span></div>
      <div class="sidebar-caption">CONTROL PLANE</div>
      <nav aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.label"
          class="nav-item"
          :class="{ active: activeSection === item.key }"
          :disabled="!item.enabled"
          :title="item.enabled ? item.label : `${item.label}模块筹建中`"
          @click="item.enabled && (activeSection = item.key)"
        >
          <component :is="item.icon" :size="17" />
          <span>{{ item.label }}</span>
          <small v-if="!item.enabled">SOON</small>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <div class="runtime-note"><span class="pulse-dot" /> STANDALONE RUNTIME</div>
        <div class="sidebar-version">CORE 0.1.0 · READ-ONLY</div>
      </div>
    </aside>

    <section class="workspace-main">
      <header class="topbar">
         <div class="breadcrumb"><span>WORKSPACE</span><strong>/</strong><span>{{ sectionLabel }}</span></div>
        <div class="topbar-actions">
          <span class="mode-chip"><ShieldCheck :size="14" /> 执行已禁用</span>
          <button v-if="activeSection === 'overview'" class="icon-button" type="button" title="刷新市场状态" aria-label="刷新市场状态" :disabled="loading" @click="loadOverview">
            <RefreshCw :size="17" :class="{ spinning: loading }" />
          </button>
          <div class="user-menu"><CircleUserRound :size="17" /><span>{{ auth.user?.display_name || 'admin' }}</span></div>
          <button class="icon-button" type="button" title="退出登录" aria-label="退出登录" @click="signOut">
            <LogOut :size="17" />
          </button>
        </div>
      </header>

      <div class="content-wrap">
         <HistoryDataCenter v-if="activeSection === 'history'" />
         <RuntimePlanCenter v-else-if="activeSection === 'plan'" />
         <MarketCenter v-else-if="activeSection === 'market'" />
         <AccountCenter v-else-if="activeSection === 'account'" />
         <StrategyCenter v-else-if="activeSection === 'strategies'" @open-backtest="activeSection = 'backtests'" />
         <ResearchCenter v-else-if="activeSection === 'research'" />
         <NewsCenter v-else-if="activeSection === 'news'" @go-assistant="activeSection = 'assistant'" />
         <AdviceCenter v-else-if="activeSection === 'advice'" />
         <BacktestCenter v-else-if="activeSection === 'backtests'" />
         <div v-else-if="activeSection === 'pools'" class="pools-stack">
          <CoinPoolCenter />
          <PoolCenter />
        </div>
         <ScreeningCenter v-else-if="activeSection === 'screening'" @open-backtest="activeSection = 'backtests'" @open-incubator="activeSection = 'incubator'" />
         <StrategyIncubatorCenter v-else-if="activeSection === 'incubator'" @open-research="activeSection = 'research'" />
         <PaperTradingCenter v-else-if="activeSection === 'paper'" />
         <RiskCenter v-else-if="activeSection === 'risk'" />
         <TaskCenter v-else-if="activeSection === 'tasks'" />
         <AssistantCenter v-else-if="activeSection === 'assistant'" />
         <NotificationCenter v-else-if="activeSection === 'notifications'" />
          <SystemCenter v-else-if="activeSection === 'system'" />
          <NetworkSettingsCenter v-else-if="activeSection === 'network'" />

        <div v-else>
        <section class="page-heading">
          <div>
            <p class="kicker">MARKET PULSE / 01</p>
            <h1>市场总览</h1>
            <p class="muted">统一查看交易所连接状态与行情数据新鲜度。</p>
          </div>
          <div class="heading-stamp"><Activity :size="15" /> DEVELOPMENT SURFACE</div>
        </section>

        <OverviewCockpit />

        <section class="metric-grid overview-metrics" aria-label="系统摘要">
          <article class="metric-cell">
            <span class="metric-label">已连接市场</span>
            <strong>{{ connectedCount }}<small>/ {{ overview?.venues.length || 3 }}</small></strong>
            <span class="metric-note">公开行情通道</span>
          </article>
          <article class="metric-cell">
            <span class="metric-label">已校验数据集</span>
            <strong>{{ marketSummary?.coverage.verified_dataset_count || 0 }}<small>/ {{ marketSummary?.coverage.expected_dataset_count || 27 }}</small></strong>
            <span class="metric-note">{{ marketSummary?.coverage.coverage_ratio_pct || '0.00' }}% 目标覆盖</span>
          </article>
          <article class="metric-cell">
            <span class="metric-label">历史 K 线</span>
            <strong>{{ formatNumber(marketSummary?.coverage.row_count || 0, 0) }}</strong>
            <span class="metric-note">{{ marketSummary?.interval || '1h' }} · 已验收数据</span>
          </article>
          <article class="metric-cell">
            <span class="metric-label">最新数据</span>
            <strong class="metric-text">{{ summaryAsOf }}</strong>
            <span class="metric-note">UTC · {{ lastUpdate }}</span>
          </article>
          <article class="metric-cell">
            <span class="metric-label">执行模式</span>
            <strong class="metric-text">{{ overview?.execution_mode || 'DISABLED' }}</strong>
            <span class="metric-note">服务端强制关闭</span>
          </article>
        </section>

        <section class="section-block" aria-labelledby="venue-title">
          <div class="section-heading">
            <div>
              <p class="kicker">VENUE STATUS</p>
              <h2 id="venue-title">交易所连接</h2>
            </div>
            <span class="section-meta">{{ overview?.venues.length || 3 }} VENUES</span>
          </div>
          <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
          <div v-else class="venue-grid">
            <article v-for="venue in overview?.venues || []" :key="venue.venue_id" class="venue-card">
              <div class="venue-card-top">
                <div class="venue-icon"><WifiOff v-if="venue.state !== 'CONNECTED'" :size="17" /><Activity v-else :size="17" /></div>
                <span class="state-pill" :class="venue.state.toLowerCase()">{{ stateLabel(venue.state) }}</span>
              </div>
              <h3>{{ venueNames[venue.venue_id] || venue.venue_id }}</h3>
              <p>{{ venue.reason || '等待行情数据' }}</p>
              <footer><span>SEQ</span><strong>{{ venue.last_sequence ?? '—' }}</strong></footer>
            </article>
          </div>
          <div v-if="!overview && loading" class="loading-block"><RefreshCw :size="17" class="spinning" /> 正在读取市场状态</div>
        </section>

        <section class="section-block overview-data-block" aria-labelledby="market-data-title">
          <div class="section-heading">
            <div>
              <p class="kicker">VERIFIED MARKET SNAPSHOT</p>
              <h2 id="market-data-title">市场数据</h2>
            </div>
            <span class="section-meta">{{ marketSummary?.interval || '1h' }} · RESEARCH ONLY</span>
          </div>
          <div v-if="marketSummary" class="overview-data-grid">
            <section class="overview-table-panel" aria-labelledby="quote-table-title">
              <div class="overview-subheading"><h3 id="quote-table-title">最新报价</h3><span>{{ summaryQuotes.length }} ROWS</span></div>
              <div v-if="summaryQuotes.length" class="overview-table-wrap">
                <table class="overview-table">
                  <thead><tr><th>品种</th><th>市场</th><th>收盘</th><th>24H</th><th>数据时间</th></tr></thead>
                  <tbody>
                    <tr v-for="row in summaryQuotes" :key="`${row.venue_id}-${row.symbol}`">
                      <td><strong>{{ row.symbol }}</strong><small>{{ row.interval }} · {{ formatNumber(row.row_count, 0) }} 根</small></td>
                      <td>{{ row.venue_name }}</td>
                      <td class="numeric">{{ formatNumber(row.close, 4) }}</td>
                      <td class="numeric" :class="quoteTone(row)"><ArrowUp v-if="Number(row.change_pct) >= 0" :size="13" /><ArrowDown v-else :size="13" />{{ formatPct(row.change_pct) }}</td>
                      <td>{{ formatTime(row.close_time) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="loading-block">暂无质量通过的小时线</div>
            </section>
            <section class="overview-table-panel" aria-labelledby="spread-table-title">
              <div class="overview-subheading"><h3 id="spread-table-title">跨市场价差</h3><span>{{ summarySpreads.length }} SYMBOLS</span></div>
              <div v-if="summarySpreads.length" class="overview-table-wrap">
                <table class="overview-table spread-table">
                  <thead><tr><th>品种</th><th>低价市场</th><th>高价市场</th><th>收盘价差</th></tr></thead>
                  <tbody>
                    <tr v-for="row in summarySpreads" :key="row.symbol">
                      <td><strong>{{ row.symbol }}</strong><small>{{ row.venue_count }} 个市场</small></td>
                      <td>{{ row.buy_venue_name }}<small>{{ formatNumber(row.buy_close, 4) }}</small></td>
                      <td>{{ row.sell_venue_name }}<small>{{ formatNumber(row.sell_close, 4) }}</small></td>
                      <td class="numeric positive">{{ formatPct(row.spread_pct) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="loading-block">至少需要两个市场的同周期数据</div>
            </section>
          </div>
          <div v-else class="loading-block"><RefreshCw :size="17" class="spinning" /> 正在读取已校验行情</div>
          <p class="overview-warning"><ShieldCheck :size="13" /> 报价来自已校验 K 线收盘价，跨市场价差只用于研究，不代表同步盘口可成交价。</p>
        </section>

        <section class="section-block next-block" aria-labelledby="next-title">
          <div class="section-heading">
            <div>
              <p class="kicker">NEXT LAYER</p>
              <h2 id="next-title">研究模块</h2>
            </div>
            <Database :size="18" class="section-icon" />
          </div>
          <div class="module-line">
            <span class="module-index">02</span>
            <div><strong>研究控制面与受控运行时</strong><p>建议队列、AI 只读能力、站内通知和系统就绪度已经接入。</p></div>
            <span class="status-label">IMPLEMENTED</span>
          </div>
        </section>
        </div>
      </div>
    </section>
  </main>
</template>
