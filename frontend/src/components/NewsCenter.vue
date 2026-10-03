<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { AlertTriangle, ArrowDown, ArrowUp, Bot, Check, ExternalLink, Newspaper, RefreshCw, ShieldCheck, Sparkles, TrendingUp } from 'lucide-vue-next';
import { api } from '../api';
import type { NewsAdvice, NewsEvent, NewsRankingItem, NewsResonance, NewsResonanceItem, NewsSummary } from '../types';
import { interpretNews, loadProvider, providerReady } from '../ai/provider';

const interval = ref<'1d' | '1h' | '5m'>('1h');
const sentiment = ref<'all' | 'positive' | 'neutral' | 'risk'>('all');
const summary = ref<NewsSummary | null>(null);
const events = ref<NewsEvent[]>([]);
const resonance = ref<NewsResonance | null>(null);
const ranking = ref<NewsRankingItem[]>([]);
const advice = ref<NewsAdvice[]>([]);
const loading = ref(false);
const saving = ref(false);
const ingesting = ref(false);
const error = ref('');
const notice = ref('');

// AI 解读(前端直连用户自己的网关)
const interpreting = ref<string | null>(null);
const interpretResult = ref('');
const interpretEvent = ref<NewsEvent | null>(null);
const showInterpret = ref(false);

const form = reactive({
  title: '',
  summary: '',
  source: 'manual-research',
  source_url: '',
  published_at: localDateTime(),
  symbols: 'BTC/USDT, ETH/USDT',
  topics: 'market',
  sentiment: 'neutral' as 'positive' | 'neutral' | 'risk',
  risk_level: 'low' as 'low' | 'medium' | 'high',
  impact_score: '50',
});

const riskCount = computed(() => summary.value?.sentiment_counts?.risk || 0);
const resonanceItems = computed(() => resonance.value?.items || []);

function localDateTime() {
  const date = new Date();
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [summaryResponse, eventsResponse, resonanceResponse, rankingResponse, adviceResponse] = await Promise.all([
      api.get<NewsSummary>('/news/summary'),
      api.get<{ items: NewsEvent[] }>('/news/events', { params: { sentiment: sentiment.value, limit: 80 } }),
      api.get<NewsResonance>('/news/resonance', { params: { interval: interval.value, limit: 80 } }),
      api.get<{ items: NewsRankingItem[] }>('/news/ranking'),
      api.get<{ items: NewsAdvice[] }>('/news/advice'),
    ]);
    summary.value = summaryResponse.data;
    events.value = eventsResponse.data.items || [];
    resonance.value = resonanceResponse.data;
    ranking.value = rankingResponse.data.items || [];
    advice.value = adviceResponse.data.items || [];
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '新闻研究数据读取失败';
  } finally {
    loading.value = false;
  }
};

const runIngest = async () => {
  ingesting.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.post('/news/ingest/run');
    const majors = (data.major_events || []).length;
    notice.value = `抓取完成:新增 ${data.ingested || 0} 条,去重 ${data.skipped || 0} 条,重大事件 ${majors} 个。`;
    await load();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '新闻抓取失败';
  } finally {
    ingesting.value = false;
  }
};

const interpret = async (event: NewsEvent) => {
  const provider = loadProvider();
  if (!providerReady(provider)) {
    error.value = '请先在「AI 能力」页配置模型网关(地址 / Key / 模型名),再使用 AI 解读';
    return;
  }
  interpreting.value = event.event_id;
  interpretResult.value = '';
  interpretEvent.value = event;
  showInterpret.value = true;
  try {
    interpretResult.value = await interpretNews(provider, {
      title: event.title,
      summary: event.summary,
      source: event.source,
      published_at: event.published_at,
      symbols: event.symbols || [],
      topics: event.topics || [],
      sentiment: event.sentiment,
      risk_level: event.risk_level,
      impact_score: event.impact_score,
    });
  } catch (cause: any) {
    interpretResult.value = `解读失败:${cause?.message || '未知错误'}`;
  } finally {
    interpreting.value = null;
  }
};

const closeInterpret = () => { showInterpret.value = false; interpretEvent.value = null; };

const createEvent = async () => {
  if (!form.title.trim() || !form.summary.trim() || !form.source.trim()) {
    error.value = '标题、摘要和来源不能为空';
    return;
  }
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const { data } = await api.post<NewsEvent>('/news/events', {
      ...form,
      published_at: `${form.published_at}:00+08:00`,
      source_url: form.source_url.trim() || null,
      symbols: form.symbols.split(',').map((item) => item.trim()).filter(Boolean),
      topics: form.topics.split(',').map((item) => item.trim()).filter(Boolean),
      impact_score: form.impact_score,
    });
    notice.value = data.event_id ? '研究新闻事件已登记，并已重新计算行情共振。' : '新闻事件已登记。';
    form.title = '';
    form.summary = '';
    await load();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '新闻事件登记失败';
  } finally {
    saving.value = false;
  }
};

const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });
const formatNumber = (value: unknown, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', { maximumFractionDigits: digits }) : '—';
const formatPct = (value: unknown) => Number.isFinite(Number(value)) ? `${Number(value) >= 0 ? '+' : ''}${formatNumber(value)}%` : '—';
const sentimentLabel = (value: string) => ({ positive: '正面', neutral: '中性', risk: '风险' }[value] || value);
const stateLabel = (value: string) => ({ RESONANCE: '共振候选', RISK_BLOCK: '风险阻断', OBSERVED: '继续观察' }[value] || value);
const stateClass = (value: string) => value === 'RESONANCE' ? 'state-positive' : value === 'RISK_BLOCK' ? 'state-risk' : 'state-neutral';
const venueLabel = (value: string) => value ? value.toUpperCase() : '—';
const resonanceTitle = (item: NewsResonanceItem) => item.event_titles?.slice(0, 2).join(' / ') || '无关联事件标题';

onMounted(load);
</script>

<template>
  <section class="news-center" aria-labelledby="news-title">
    <section class="page-heading news-heading"><div><p class="kicker">NEWS & MARKET CONTEXT / 07</p><h1 id="news-title">新闻研究</h1><p class="muted">把带来源的事件与已验证行情关联起来，结果只进入研究队列，不直接生成交易动作。</p></div><button class="icon-button" type="button" title="刷新新闻研究" aria-label="刷新新闻研究" :disabled="loading" @click="load"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div v-if="notice" class="inline-notice" role="status"><Check :size="14" />{{ notice }}</div>
    <section class="news-toolbar"><label><span>行情周期</span><select v-model="interval" @change="load"><option value="1h">1 小时</option><option value="1d">1 日</option><option value="5m">5 分钟</option></select></label><label><span>事件情绪</span><select v-model="sentiment" @change="load"><option value="all">全部</option><option value="positive">正面</option><option value="neutral">中性</option><option value="risk">风险</option></select></label><button class="primary-small ingest-btn" type="button" :disabled="ingesting" @click="runIngest"><RefreshCw v-if="ingesting" :size="13" class="spinning" /><Newspaper v-else :size="13" />{{ ingesting ? '抓取中' : '抓取新闻' }}</button><div class="news-toolbar-state"><ShieldCheck :size="15" /><span>研究模式 · {{ summary?.source_mode || 'rss_and_manual' }} · 每 15 分钟自动抓取</span></div></section>
    <section class="news-metrics" aria-label="新闻研究摘要"><article><span>登记事件</span><strong>{{ summary?.event_count || 0 }}</strong><em>必须保留来源</em></article><article><span>风险事件</span><strong class="risk-value">{{ riskCount }}</strong><em>不产生交易指令</em></article><article><span>共振候选</span><strong class="positive">{{ resonance?.count || 0 }}</strong><em>{{ interval }} 行情关联</em></article><article><span>执行资格</span><strong class="warn">关闭</strong><em>服务端安全门禁</em></article></section>
    <section class="news-layout">
      <section class="news-panel ranking-panel" aria-labelledby="ranking-title"><header class="section-heading"><div><p class="kicker">COIN SELECTION</p><h2 id="ranking-title">消息面选币榜</h2></div><TrendingUp :size="18" class="section-icon" /></header><div v-if="!ranking.length" class="news-empty compact"><Newspaper :size="20" /><span>暂无品种消息,抓取新闻后自动生成</span></div><div v-else class="ranking-list"><article v-for="item in ranking.slice(0, 12)" :key="item.symbol" class="ranking-row"><div class="ranking-top"><strong>{{ item.symbol }}</strong><b :class="item.direction === 'risk' ? 'state-risk' : item.direction === 'positive' ? 'state-positive' : 'state-neutral'">{{ item.direction === 'risk' ? '偏空' : item.direction === 'positive' ? '偏多' : '中性' }}</b></div><div class="ranking-stats"><span>消息分 {{ item.score }}</span><span>事件 {{ item.heat }}</span><span class="positive">利好 {{ item.positive }}</span><span class="negative">风险 {{ item.risk }}</span></div><p :title="item.top_title">{{ item.top_title }}</p></article></div><p class="panel-note"><ShieldCheck :size="13" /> 按 72 小时内新闻加权聚合,仅作选币参考,不构成交易指令。</p></section>
      <section class="news-panel advice-panel" aria-labelledby="advice-title"><header class="section-heading"><div><p class="kicker">STRATEGY ADVICE</p><h2 id="advice-title">策略建议</h2></div><span class="section-meta">{{ advice.length }} ITEMS</span></header><div v-if="!advice.length" class="news-empty compact"><Newspaper :size="20" /><span>暂无重大事件建议</span></div><div v-else class="advice-list"><article v-for="(item, index) in advice.slice(0, 12)" :key="`${item.symbol}-${index}`" class="advice-row"><div class="advice-top"><strong>{{ item.symbol }}</strong><b :class="item.action === 'avoid_new_entries' ? 'state-risk' : item.action === 'watch_long_signals' ? 'state-positive' : 'state-neutral'">{{ item.action_text }}</b></div><p>{{ item.reason }}</p><small>影响 {{ item.impact_score }} · {{ item.heat }} 家报道 · {{ item.sources.join('、') }}</small></article></div><p class="panel-note"><ShieldCheck :size="13" /> 重大事件(多家报道或高风险)自动生成;回放时可开启新闻门控拦截新开仓。</p></section>
    </section>
    <section class="news-layout">
      <section class="news-panel ingest-panel" aria-labelledby="ingest-title"><header class="section-heading"><div><p class="kicker">EVENT INGESTION</p><h2 id="ingest-title">登记研究事件</h2></div><Newspaper :size="18" class="section-icon" /></header><form class="news-form" @submit.prevent="createEvent"><label><span>标题</span><input v-model="form.title" maxlength="200" placeholder="例如：交易所公开规则变化" /></label><label><span>摘要</span><textarea v-model="form.summary" rows="4" maxlength="1000" placeholder="保留可核对的事件摘要" /></label><div class="form-grid"><label><span>来源</span><input v-model="form.source" maxlength="120" /></label><label><span>来源链接（可选）</span><input v-model="form.source_url" maxlength="500" /></label><label><span>发布时间（北京时间）</span><input v-model="form.published_at" type="datetime-local" /></label><label><span>影响分（0-100）</span><input v-model="form.impact_score" type="number" min="0" max="100" step="1" /></label></div><div class="form-grid"><label><span>关联币种</span><input v-model="form.symbols" placeholder="BTC/USDT, ETH/USDT" /></label><label><span>主题</span><input v-model="form.topics" placeholder="market, regulation" /></label><label><span>情绪</span><select v-model="form.sentiment"><option value="positive">正面</option><option value="neutral">中性</option><option value="risk">风险</option></select></label><label><span>风险等级</span><select v-model="form.risk_level"><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label></div><button class="primary-small" type="submit" :disabled="saving"><RefreshCw v-if="saving" :size="14" class="spinning" /><Check v-else :size="14" />登记事件</button></form><p class="panel-note"><AlertTriangle :size="13" /> RSS 自动抓取(CoinDesk / CoinTelegraph)+ 规则智能分析:品种映射、分类、情绪打分、影响分均为确定性规则,可审计;语义解读请用事件行的 AI 解读。</p></section>
      <section class="news-panel resonance-panel" aria-labelledby="resonance-title"><header class="section-heading"><div><p class="kicker">MARKET RESONANCE</p><h2 id="resonance-title">行情共振</h2></div><span class="section-meta">{{ resonanceItems.length }} ITEMS</span></header><div v-if="!resonanceItems.length" class="news-empty"><Newspaper :size="24" /><strong>暂无共振候选</strong><span>登记带币种的事件，或先确认对应周期有质量通过的历史数据。</span></div><div v-else class="resonance-list"><article v-for="item in resonanceItems" :key="`${item.venue_id}-${item.symbol}`"><div class="resonance-top"><strong>{{ item.symbol }}</strong><span>{{ venueLabel(item.venue_id) }} · {{ formatNumber(item.close, 4) }}</span><b :class="stateClass(item.state)">{{ stateLabel(item.state) }}</b></div><div class="resonance-stats"><span :class="Number(item.change_pct) >= 0 ? 'positive' : 'negative'"><ArrowUp v-if="Number(item.change_pct) >= 0" :size="12" /><ArrowDown v-else :size="12" />{{ formatPct(item.change_pct) }}</span><span>研究分 {{ formatNumber(item.news_score) }}</span><span>风险 {{ item.risk_level }}</span></div><p :title="resonanceTitle(item)">{{ item.action }} · {{ resonanceTitle(item) }}</p></article></div><p class="panel-note"><ShieldCheck :size="13" /> 共振只用于候选排序；必须再经过策略回测、模拟盘和风控。</p></section>
    </section>
    <section class="news-panel event-panel" aria-labelledby="event-list-title"><header class="section-heading"><div><p class="kicker">SOURCE REGISTER</p><h2 id="event-list-title">事件记录</h2></div><span class="section-meta">{{ events.length }} EVENTS</span></header><div v-if="!events.length" class="news-empty compact"><Newspaper :size="20" /><span>暂无符合筛选条件的事件</span></div><div v-else class="event-list"><article v-for="event in events" :key="event.event_id" class="event-row"><div class="event-date">{{ formatTime(event.published_at) }}</div><div class="event-main"><div><strong>{{ event.title }}</strong><span class="sentiment-pill" :class="`sentiment-${event.sentiment}`">{{ sentimentLabel(event.sentiment) }}</span></div><p>{{ event.summary }}</p><small>{{ event.source }} · {{ event.symbols.join('、') || '全市场' }} · 影响 {{ formatNumber(event.impact_score, 0) }}</small></div><div class="event-actions"><button class="event-link" type="button" title="AI 解读" aria-label="AI 解读" :disabled="interpreting === event.event_id" @click="interpret(event)"><Sparkles :size="14" :class="{ spinning: interpreting === event.event_id }" /></button><a v-if="event.source_url" class="event-link" :href="event.source_url" target="_blank" rel="noreferrer" title="打开来源" aria-label="打开来源"><ExternalLink :size="14" /></a><span v-else class="event-link-placeholder" aria-hidden="true" /></div></article></div></section>
    <div v-if="showInterpret" class="interpret-overlay" @click.self="closeInterpret"><section class="interpret-modal" role="dialog" aria-label="AI 解读"><header><div><p class="kicker">AI INTERPRETATION</p><h2>{{ interpretEvent?.title }}</h2></div><button class="icon-button" type="button" @click="closeInterpret" aria-label="关闭">✕</button></header><p class="interpret-meta">{{ interpretEvent?.source }} · {{ interpretEvent?.symbols.join('、') || '全市场' }} · 影响 {{ interpretEvent?.impact_score }}</p><div v-if="interpreting" class="interpret-loading"><RefreshCw :size="16" class="spinning" /><span>模型解读中…</span></div><pre v-else class="interpret-body">{{ interpretResult }}</pre><p class="panel-note"><Bot :size="13" /> 由你配置的模型生成,仅供研究参考,不构成投资建议。</p></section></div>
  </section>
</template>

<style scoped>
.news-center { display: grid; gap: 24px; }
.news-heading { margin-bottom: 0; }
.inline-notice { display: flex; align-items: center; gap: 7px; border-left: 2px solid var(--cyan); padding: 8px 12px; color: var(--cyan); background: rgba(108, 229, 208, .07); font-size: 12px; }
.news-toolbar { display: flex; align-items: end; gap: 14px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 13px 0; }
.news-toolbar label { display: grid; gap: 6px; color: var(--muted); font-size: 10px; }
.news-toolbar select { min-width: 120px; border: 1px solid var(--line-bright); border-radius: 4px; padding: 8px; color: var(--ink); background: var(--input-bg); }
.news-toolbar-state { display: flex; align-items: center; gap: 7px; margin-left: auto; color: var(--dim); font-size: 10px; }
.news-toolbar-state svg { color: var(--amber); }
.news-metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.news-metrics article { display: grid; gap: 7px; min-height: 104px; padding: 17px 20px; border-right: 1px solid var(--line); }
.news-metrics article:last-child { border-right: 0; }
.news-metrics span, .news-metrics em { color: var(--dim); font-size: 10px; font-style: normal; }
.news-metrics strong { align-self: center; color: var(--ink); font: 600 23px Consolas, monospace; }
.positive { color: var(--cyan) !important; }.negative { color: var(--red) !important; }.warn { color: var(--amber) !important; }.risk-value { color: var(--red) !important; }
.news-layout { display: grid; grid-template-columns: minmax(330px, .92fr) minmax(0, 1.08fr); gap: 13px; align-items: start; }
.news-panel { min-width: 0; border: 1px solid var(--line); padding: 21px 18px 14px; background: var(--panel); }
.news-panel .section-heading { margin-bottom: 15px; }
.news-form { display: grid; gap: 12px; }
.news-form label { display: grid; gap: 6px; min-width: 0; }
.news-form label > span { color: var(--muted); font-size: 10px; }
.news-form input, .news-form select, .news-form textarea { width: 100%; min-width: 0; border: 1px solid var(--line-bright); border-radius: 4px; padding: 8px 9px; color: var(--ink); background: var(--input-bg); outline: none; font-size: 11px; resize: vertical; }
.news-form input, .news-form select { min-height: 36px; }
.news-form input:focus, .news-form select:focus, .news-form textarea:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(108, 229, 208, .1); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.primary-small { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 35px; border: 1px solid var(--cyan); border-radius: 4px; padding: 8px 11px; color: #11201e; background: var(--cyan); font-size: 10px; font-weight: 700; }
.primary-small:hover:not(:disabled) { background: #94f0df; }.primary-small:disabled { cursor: not-allowed; opacity: .55; }
.panel-note { display: flex; align-items: start; gap: 7px; margin: 14px 0 0; color: var(--dim); font-size: 9px; line-height: 1.6; }.panel-note svg { flex: 0 0 auto; color: var(--amber); }
.resonance-list { display: grid; border-top: 1px solid var(--line); }
.resonance-list article { display: grid; gap: 7px; padding: 12px 4px; border-bottom: 1px solid var(--line); }
.resonance-top, .resonance-stats { display: flex; align-items: center; gap: 9px; }.resonance-top strong { color: var(--ink); font-size: 12px; }.resonance-top span, .resonance-stats span { color: var(--dim); font-size: 9px; }.resonance-top b { margin-left: auto; border: 1px solid var(--line-bright); padding: 4px 6px; font-size: 9px; font-weight: 550; }.state-positive { border-color: #42645d !important; color: var(--cyan); }.state-risk { border-color: #734943 !important; color: var(--red); }.state-neutral { color: var(--amber); }
.resonance-stats svg { vertical-align: -2px; }.resonance-list p { overflow: hidden; margin: 0; color: var(--muted); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.news-empty { display: grid; justify-items: center; gap: 7px; min-height: 200px; place-content: center; border: 1px dashed var(--line-bright); color: var(--dim); text-align: center; }.news-empty strong { color: var(--ink); font-size: 13px; }.news-empty span { max-width: 250px; font-size: 10px; line-height: 1.6; }.news-empty.compact { min-height: 90px; }
.event-list { display: grid; border-top: 1px solid var(--line); }.event-row { display: grid; grid-template-columns: 130px minmax(0, 1fr) 62px; align-items: start; gap: 14px; padding: 13px 4px; border-bottom: 1px solid var(--line); }.event-actions { display: flex; gap: 6px; }.event-actions .event-link { cursor: pointer; background: transparent; font-size: 12px; }.event-date { color: var(--dim); font: 9px Consolas, monospace; line-height: 1.5; }.event-main { display: grid; gap: 6px; min-width: 0; }.event-main > div { display: flex; align-items: center; gap: 8px; min-width: 0; }.event-main strong { overflow: hidden; color: var(--ink); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.event-main p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.6; }.event-main small { color: var(--dim); font-size: 9px; }.sentiment-pill { border: 1px solid var(--line-bright); padding: 3px 5px; color: var(--muted); font-size: 8px; white-space: nowrap; }.sentiment-positive { color: var(--cyan); border-color: #42645d; }.sentiment-risk { color: var(--red); border-color: #734943; }.event-link { display: grid; place-items: center; width: 26px; height: 26px; border: 1px solid var(--line-bright); color: var(--muted); }.event-link:hover { border-color: var(--cyan); color: var(--cyan); }.event-link:disabled { opacity: .5; cursor: wait; }.event-link-placeholder { width: 26px; height: 26px; }
.ingest-btn { margin-left: 4px; }
.ranking-list, .advice-list { display: grid; border-top: 1px solid var(--line); }
.ranking-row, .advice-row { display: grid; gap: 6px; padding: 11px 4px; border-bottom: 1px solid var(--line); }
.ranking-top, .advice-top { display: flex; align-items: center; gap: 9px; }
.ranking-top strong, .advice-top strong { color: var(--ink); font-size: 12px; }
.ranking-top b, .advice-top b { margin-left: auto; border: 1px solid var(--line-bright); padding: 4px 6px; font-size: 9px; font-weight: 550; }
.ranking-stats { display: flex; align-items: center; gap: 9px; }
.ranking-stats span { color: var(--dim); font-size: 9px; }
.ranking-row p, .advice-row p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.5; }
.advice-row small { color: var(--dim); font-size: 9px; }
.interpret-overlay { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: 20px; background: rgba(0,0,0,.55); }
.interpret-modal { width: min(640px, 100%); max-height: 82vh; overflow: auto; border: 1px solid var(--line-bright); background: var(--panel); padding: 20px; }
.interpret-modal header { display: flex; align-items: start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.interpret-modal h2 { margin: 4px 0 0; color: var(--ink); font-size: 14px; line-height: 1.5; }
.interpret-meta { color: var(--dim); font-size: 10px; margin: 0 0 12px; }
.interpret-loading { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 11px; padding: 20px 0; }
.interpret-body { margin: 0; color: var(--ink); font-size: 11px; line-height: 1.8; white-space: pre-wrap; font-family: inherit; }
@media (max-width: 800px) { .news-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .news-toolbar { align-items: start; flex-wrap: wrap; }.news-toolbar-state { width: 100%; margin-left: 0; }.news-metrics { grid-template-columns: 1fr 1fr; }.news-metrics article:nth-child(2n) { border-right: 0; }.news-metrics article:nth-child(-n+2) { border-bottom: 1px solid var(--line); }.form-grid { grid-template-columns: 1fr; }.news-panel { padding: 17px 13px 13px; }.event-row { grid-template-columns: 1fr 62px; gap: 6px 10px; }.event-date { grid-column: 1 / -1; }.event-main { grid-column: 1; }.event-actions { grid-column: 2; grid-row: 2; } }
</style>
