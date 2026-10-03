<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Bot, CircleAlert, LockKeyhole, Play, RefreshCw, ShieldCheck } from 'lucide-vue-next';
import { api } from '../api';
import type { AssistantAction, AssistantSummary } from '../types';

const summary = ref<AssistantSummary | null>(null);
const actions = ref<AssistantAction[]>([]);
const invocations = ref<Array<Record<string, any>>>([]);
const selectedAction = ref('runtime.plan.read');
const interval = ref<'1d' | '1h' | '5m'>('1h');
const loading = ref(false);
const invoking = ref(false);
const error = ref('');
const result = ref<Record<string, any> | null>(null);

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const [summaryResponse, actionsResponse, invocationsResponse] = await Promise.all([
      api.get<AssistantSummary>('/assistant/summary'),
      api.get<{ items: AssistantAction[] }>('/assistant/actions'),
      api.get<{ items: Array<Record<string, any>> }>('/assistant/invocations'),
    ]);
    summary.value = summaryResponse.data;
    actions.value = actionsResponse.data.items || [];
    invocations.value = invocationsResponse.data.items || [];
    if (!actions.value.some((item) => item.id === selectedAction.value) && actions.value[0]) selectedAction.value = actions.value[0].id;
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || 'AI 能力目录读取失败';
  } finally {
    loading.value = false;
  }
};

const invoke = async () => {
  invoking.value = true;
  error.value = '';
  result.value = null;
  try {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const { data } = await api.post('/assistant/invoke', {
      request_id: `ui-${id}`,
      action: selectedAction.value,
      actor: 'standalone-ui',
      risk_level: 'READ_ONLY',
      payload: { interval: interval.value, limit: 20 },
      authorization_id: 'ui-read-only-session',
      idempotency_key: `ui-${selectedAction.value}-${id}`,
      expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
    });
    result.value = data;
    await load();
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || 'AI Action 调用失败';
  } finally {
    invoking.value = false;
  }
};

onMounted(load);
</script>

<template>
  <section class="assistant-center" aria-labelledby="assistant-title">
    <section class="page-heading assistant-heading"><div><p class="kicker">GACE AI BRIDGE / 12</p><h1 id="assistant-title">AI 能力</h1><p class="muted">先提供可发现、可审计的只读 Action；研究任务和真实交易写入能力保持关闭。</p></div><button class="icon-button" type="button" title="刷新 AI 能力" aria-label="刷新 AI 能力" :disabled="loading" @click="load"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <section class="assistant-banner"><Bot :size="18" /><div><strong>GACE AI 只读桥接已就绪</strong><span>每次调用都必须携带过期时间、授权标识和幂等键；真实下单、提币和任意写入均未开放。</span></div><b>{{ summary?.actions || 0 }} ACTIONS</b></section>
    <section class="assistant-layout">
      <section class="assistant-panel action-panel" aria-labelledby="action-title"><header class="section-heading"><div><p class="kicker">ALLOWLISTED ACTIONS</p><h2 id="action-title">能力目录</h2></div><ShieldCheck :size="18" class="section-icon" /></header><div class="action-list"><button v-for="action in actions" :key="action.id" type="button" :class="{ selected: selectedAction === action.id }" @click="selectedAction = action.id"><span><strong>{{ action.id }}</strong><small>{{ action.description }}</small></span><b>{{ action.risk_level }}</b></button></div></section>
      <section class="assistant-panel invoke-panel" aria-labelledby="invoke-title"><header class="section-heading"><div><p class="kicker">READ-ONLY INVOCATION</p><h2 id="invoke-title">调用预览</h2></div><LockKeyhole :size="18" class="section-icon" /></header><label class="assistant-field"><span>当前 Action</span><select v-model="selectedAction"><option v-for="action in actions" :key="action.id" :value="action.id">{{ action.id }}</option></select></label><label class="assistant-field"><span>查询周期</span><select v-model="interval"><option value="1h">1 小时</option><option value="1d">1 日</option><option value="5m">5 分钟</option></select></label><button class="invoke-button" type="button" :disabled="invoking || !selectedAction" @click="invoke"><RefreshCw v-if="invoking" :size="15" class="spinning" /><Play v-else :size="15" /><span>{{ invoking ? '调用中' : '执行只读 Action' }}</span></button><div v-if="result" class="invoke-result"><header><strong>返回结果</strong><span>{{ result.status }} · {{ result.action }}</span></header><pre>{{ JSON.stringify(result.result, null, 2) }}</pre></div><div v-else class="invoke-empty"><CircleAlert :size="20" /><span>选择能力后执行一次只读查询</span></div></section>
    </section>
    <section class="assistant-panel invocation-panel"><header class="section-heading"><div><p class="kicker">AUDIT TRAIL</p><h2>最近调用</h2></div><span class="section-meta">{{ invocations.length }} RECORDS</span></header><div v-if="!invocations.length" class="invoke-empty">暂无 AI 调用记录</div><div v-else class="invocation-list"><div v-for="item in invocations" :key="`${item.request_id}-${item.created_at}`"><strong>{{ item.action }}</strong><span>{{ item.status }} · {{ item.actor }}</span><time>{{ item.created_at }}</time></div></div></section>
  </section>
</template>

<style scoped>
.assistant-center{display:grid;gap:24px}.assistant-heading{margin-bottom:0}.assistant-banner{display:flex;align-items:start;gap:10px;border-left:2px solid var(--cyan);padding:13px 14px;color:var(--cyan);background:rgba(108,229,208,.07)}.assistant-banner div{display:grid;gap:4px;flex:1}.assistant-banner strong{color:var(--ink);font-size:12px}.assistant-banner span{color:var(--muted);font-size:11px;line-height:1.5}.assistant-banner b{font:700 9px Consolas,monospace;white-space:nowrap}.assistant-layout{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:13px;align-items:start}.assistant-panel{min-width:0;border:1px solid var(--line);background:var(--panel);padding:21px 18px 14px}.assistant-panel .section-heading{margin-bottom:14px}.action-list{display:grid;border-top:1px solid var(--line)}.action-list button{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:10px;border:0;border-bottom:1px solid var(--line);padding:11px 8px;background:transparent;color:var(--muted);text-align:left}.action-list button:hover,.action-list button.selected{background:rgba(108,229,208,.06)}.action-list button.selected{box-shadow:inset 2px 0 var(--cyan)}.action-list span{display:grid;gap:4px;min-width:0}.action-list strong{overflow:hidden;color:var(--ink);font:600 11px Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.action-list small{overflow:hidden;color:var(--dim);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.action-list b{border:1px solid #42645d;padding:3px 5px;color:var(--cyan);font:700 8px Consolas,monospace}.assistant-field{display:grid;gap:6px;margin-bottom:12px;color:var(--muted);font-size:10px}.assistant-field select{min-height:36px;border:1px solid var(--line-bright);border-radius:5px;padding:8px;color:var(--ink);background: var(--input-bg)}.invoke-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;width:100%;min-height:38px;border:1px solid var(--cyan);border-radius:5px;color:#11201e;background:var(--cyan);font-size:11px;font-weight:700}.invoke-button:disabled{opacity:.55}.invoke-result{margin-top:15px;border-top:1px solid var(--line)}.invoke-result header{display:flex;justify-content:space-between;gap:8px;padding:10px 0;color:var(--ink);font-size:10px}.invoke-result header span{color:var(--dim);font:9px Consolas,monospace}.invoke-result pre{max-height:270px;overflow:auto;margin:0;border:1px solid var(--line);padding:10px;color:var(--cyan);background:#101617;font:9px/1.5 Consolas,monospace;white-space:pre-wrap}.invoke-empty{display:grid;justify-items:center;gap:7px;min-height:130px;place-content:center;color:var(--dim);font-size:10px}.invocation-panel{padding-bottom:10px}.invocation-list{display:grid;border-top:1px solid var(--line)}.invocation-list>div{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:12px;min-height:42px;border-bottom:1px solid var(--line);font-size:10px}.invocation-list strong{color:var(--ink);font:600 10px Consolas,monospace}.invocation-list span{color:var(--muted)}.invocation-list time{color:var(--dim);font:9px Consolas,monospace}@media(max-width:800px){.assistant-layout{grid-template-columns:1fr}.assistant-banner{flex-wrap:wrap}.assistant-banner b{width:100%;margin-left:28px}.invocation-list>div{grid-template-columns:1fr auto}.invocation-list time{grid-column:1/-1;padding-bottom:7px}}
</style>
