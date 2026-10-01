<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { Bell, Check, CircleAlert, RefreshCw, Volume2 } from 'lucide-vue-next';
import { api } from '../api';
import type { NotificationSummary } from '../types';

const summary = ref<NotificationSummary | null>(null);
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const notice = ref('');
const form = reactive({ enabled: false, in_app_enabled: true, system_sound_enabled: true });

const syncForm = () => {
  if (!summary.value) return;
  form.enabled = summary.value.config.enabled;
  form.in_app_enabled = summary.value.config.in_app_enabled;
  form.system_sound_enabled = summary.value.config.system_sound_enabled;
};
const load = async () => {
  loading.value = true;
  error.value = '';
  try { const { data } = await api.get<NotificationSummary>('/notifications/summary'); summary.value = data; syncForm(); } catch (cause: any) { error.value = cause.response?.data?.detail || '通知中心读取失败'; } finally { loading.value = false; }
};
const save = async () => {
  saving.value = true;
  error.value = '';
  try { await api.put('/notifications/config', { ...form, event_preferences: {} }); notice.value = '通知配置已保存'; await load(); } catch (cause: any) { error.value = cause.response?.data?.detail || '通知配置保存失败'; } finally { saving.value = false; }
};
const test = async () => {
  try { await api.post('/notifications/test'); notice.value = '测试通知已写入站内收件箱'; await load(); } catch (cause: any) { error.value = cause.response?.data?.detail || '测试通知失败'; }
};
const read = async (id: string) => {
  try { await api.post(`/notifications/read/${id}`); await load(); } catch (cause: any) { error.value = cause.response?.data?.detail || '通知状态更新失败'; }
};
const readAll = async () => {
  try { await api.post('/notifications/read', { all: true }); await load(); } catch (cause: any) { error.value = cause.response?.data?.detail || '通知状态更新失败'; }
};
const time = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', hour12: false });

onMounted(load);
</script>

<template>
  <section class="notification-center" aria-labelledby="notification-title">
    <section class="page-heading notification-heading"><div><p class="kicker">EVENT OUTBOX / 13</p><h1 id="notification-title">通知中心</h1><p class="muted">保留研究、历史任务和风控事件的站内留痕；外部机器人通道暂不接入。</p></div><button class="icon-button" type="button" title="刷新通知" aria-label="刷新通知" :disabled="loading" @click="load"><RefreshCw :size="17" :class="{ spinning: loading }" /></button></section>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div><div v-if="notice" class="inline-notice" role="status">{{ notice }}</div>
    <section class="notification-banner"><Bell :size="18" /><div><strong>{{ summary?.unread_count || 0 }} 条未读通知</strong><span>{{ summary?.readiness.message || '正在读取通知状态' }}</span></div><span>{{ summary?.status || 'standby' }}</span></section>
    <section class="notification-layout"><section class="notification-panel settings-panel"><header class="section-heading"><div><p class="kicker">IN-APP CHANNEL</p><h2>站内通道</h2></div><Volume2 :size="18" class="section-icon" /></header><label class="toggle-field"><input v-model="form.enabled" type="checkbox" /><span><strong>启用通知中心</strong><small>只影响独立应用内通知</small></span></label><label class="toggle-field"><input v-model="form.in_app_enabled" type="checkbox" /><span><strong>接收站内事件</strong><small>历史、研究、任务和风控事件</small></span></label><label class="toggle-field"><input v-model="form.system_sound_enabled" type="checkbox" /><span><strong>系统提示音</strong><small>当前由浏览器决定是否允许播放</small></span></label><button class="save-button" type="button" :disabled="saving" @click="save"><RefreshCw v-if="saving" :size="15" class="spinning" /><Check v-else :size="15" />保存设置</button><button class="test-button" type="button" @click="test"><Bell :size="14" />写入测试通知</button></section><section class="notification-panel inbox-panel"><header class="section-heading"><div><p class="kicker">IN-APP INBOX</p><h2>收件箱</h2></div><div class="inbox-actions"><span>{{ summary?.items.length || 0 }} ITEMS</span><button type="button" title="全部标记已读" aria-label="全部标记已读" :disabled="!summary?.unread_count" @click="readAll"><Check :size="14" /></button></div></header><div v-if="!summary?.items.length" class="notification-empty"><CircleAlert :size="22" /><span>暂无站内通知</span></div><div v-else class="notification-list"><article v-for="item in summary.items" :key="item.id" :class="{ unread: !item.read }"><div class="notification-item-icon"><CircleAlert :size="15" /></div><div><strong>{{ item.title }}</strong><p>{{ item.message }}</p><small>{{ item.event_type }} · {{ time(item.created_at) }}</small></div><button v-if="!item.read" type="button" title="标记已读" aria-label="标记已读" @click="read(item.id)"><Check :size="14" /></button><span v-else class="read-mark">已读</span></article></div></section></section>
  </section>
</template>

<style scoped>
.notification-center{display:grid;gap:24px}.notification-heading{margin-bottom:0}.inline-notice{border-left:2px solid var(--cyan);padding:8px 12px;color:var(--cyan);background:rgba(108,229,208,.07);font-size:12px}.notification-banner{display:flex;align-items:start;gap:10px;border-left:2px solid var(--amber);padding:13px 14px;color:var(--amber);background:rgba(228,179,109,.07)}.notification-banner div{display:grid;gap:4px;flex:1}.notification-banner strong{color:var(--ink);font-size:12px}.notification-banner span{color:var(--muted);font-size:11px}.notification-banner>span{font:700 9px Consolas,monospace}.notification-layout{display:grid;grid-template-columns:minmax(260px,.7fr) minmax(0,1.3fr);gap:13px;align-items:start}.notification-panel{min-width:0;border:1px solid var(--line);background:var(--panel);padding:21px 18px 14px}.notification-panel .section-heading{margin-bottom:15px}.toggle-field{display:flex;align-items:center;gap:9px;padding:12px 0;border-top:1px solid var(--line);color:var(--muted);font-size:11px}.toggle-field input{accent-color:var(--cyan)}.toggle-field span{display:grid;gap:3px}.toggle-field strong{color:var(--ink);font-size:11px}.toggle-field small{color:var(--dim);font-size:9px}.save-button,.test-button{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;min-height:35px;margin-top:13px;border-radius:5px;padding:8px;font-size:10px}.save-button{border:1px solid var(--cyan);color:#11201e;background:var(--cyan);font-weight:700}.save-button:disabled{opacity:.6}.test-button{border:1px solid var(--line-bright);color:var(--muted);background:transparent}.test-button:hover{color:var(--cyan);border-color:var(--cyan)}.inbox-actions{display:flex;align-items:center;gap:8px;color:var(--dim);font:9px Consolas,monospace}.inbox-actions button{display:grid;place-items:center;width:26px;height:26px;border:1px solid var(--line-bright);color:var(--muted);background:transparent}.inbox-actions button:hover:not(:disabled){color:var(--cyan);border-color:var(--cyan)}.inbox-actions button:disabled{opacity:.4}.notification-list{display:grid;border-top:1px solid var(--line)}.notification-list article{display:grid;grid-template-columns:27px minmax(0,1fr) 27px;align-items:start;gap:9px;padding:12px 4px;border-bottom:1px solid var(--line);opacity:.7}.notification-list article.unread{opacity:1}.notification-item-icon{display:grid;place-items:center;width:25px;height:25px;border:1px solid var(--line-bright);color:var(--muted)}.unread .notification-item-icon{border-color:#665637;color:var(--amber)}.notification-list article>div:nth-child(2){display:grid;gap:4px;min-width:0}.notification-list strong{color:var(--ink);font-size:11px}.notification-list p{margin:0;color:var(--muted);font-size:10px;line-height:1.5}.notification-list small{color:var(--dim);font:9px Consolas,monospace}.notification-list article>button{display:grid;place-items:center;width:25px;height:25px;border:1px solid var(--line-bright);color:var(--cyan);background:transparent}.read-mark{color:var(--dim);font-size:9px}.notification-empty{display:grid;justify-items:center;gap:7px;min-height:170px;place-content:center;border:1px dashed var(--line-bright);color:var(--dim);font-size:11px}@media(max-width:760px){.notification-layout{grid-template-columns:1fr}.notification-banner{flex-wrap:wrap}.notification-banner>span{width:100%;margin-left:28px}}
</style>
