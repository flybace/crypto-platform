<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { KeyRound, RefreshCw, ShieldCheck, Wallet } from 'lucide-vue-next';
import { api } from '../api';

interface BalanceRow {
  asset: string;
  available: string;
  total: string;
}

const status = ref<{ configured: boolean; venue_id: string; read_only: boolean } | null>(null);
const balances = ref<BalanceRow[]>([]);
const openOrderIds = ref<string[]>([]);
const openOrderCount = ref(0);
const fetchedAt = ref('');
const trading = ref<{ auto_trading_enabled: boolean } | null>(null);
const recon = ref<any>(null);
const pause = ref<any>(null);
const clearing = ref(false);
const credKey = ref('');
const credSecret = ref('');
const credPrefix = ref('');
const credSaving = ref(false);
const credError = ref('');
const credOk = ref('');
const toggling = ref(false);
const loading = ref(false);
const error = ref('');

async function loadStatus() {
  const res = await api.get('/account/status');
  status.value = res.data;
}

async function loadTrading() {
  const res = await api.get('/settings/trading');
  trading.value = res.data;
}

async function loadCredStatus() {
  try {
    const res = await api.get('/account/credentials');
    credPrefix.value = res.data.key_prefix || '';
  } catch { /* ignore */ }
}

async function saveCredentials() {
  credSaving.value = true;
  credError.value = '';
  credOk.value = '';
  try {
    const res = await api.post('/account/credentials', { api_key: credKey.value, api_secret: credSecret.value });
    credOk.value = `验证通过：读取到 ${res.data.balance_count} 种资产`;
    credKey.value = '';
    credSecret.value = '';
    await loadStatus();
    await loadCredStatus();
    await loadBalances();
  } catch (e: any) {
    credError.value = e?.response?.data?.detail || '保存失败';
  } finally {
    credSaving.value = false;
  }
}

async function deleteCredentials() {
  if (!confirm('确定删除已保存的 API 密钥吗？')) return;
  credSaving.value = true;
  try {
    await api.delete('/account/credentials');
    await loadStatus();
    credPrefix.value = '';
  } catch (e: any) {
    credError.value = e?.response?.data?.detail || '删除失败';
  } finally {
    credSaving.value = false;
  }
}

async function loadBalances() {
  const res = await api.get('/account/balances');
  balances.value = res.data.balances || [];
  fetchedAt.value = res.data.fetched_at || '';
}

async function loadRecon() {
  const res = await api.get('/account/reconciliation');
  recon.value = res.data;
  pause.value = res.data.pause;
}

async function loadOrders() {
  const res = await api.get('/account/orders');
  openOrderIds.value = res.data.open_order_ids || [];
  openOrderCount.value = res.data.open_order_count || 0;
}

async function refresh() {
  loading.value = true;
  error.value = '';
  try {
    await loadStatus();
    await loadTrading();
    await loadCredStatus();
    await loadRecon();
    if (status.value?.configured) {
      await loadBalances();
      await loadOrders();
    }
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '读取账户失败';
  } finally {
    loading.value = false;
  }
}

async function toggleTrading() {
  if (!trading.value) return;
  const next = !trading.value.auto_trading_enabled;
  const label = next ? '启用' : '关闭';
  if (!confirm(`确定要${label}自动交易吗？${next ? '启用后，策略实例的自动交易开关也打开时，信号会自动下模拟单（真实执行仍关闭）。' : '关闭后，所有策略立即停止自动下单，只记录信号。'}`)) return;
  toggling.value = true;
  try {
    const res = await api.put('/settings/trading', { enabled: next });
    trading.value = res.data;
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '切换失败';
  } finally {
    toggling.value = false;
  }
}

async function clearPause() {
  if (!confirm('确定要解除安全暂停吗？请先确认对账异常原因已排查。')) return;
  clearing.value = true;
  try {
    const res = await api.post('/account/pause/clear');
    pause.value = res.data;
    await loadRecon();
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '解除失败';
  } finally {
    clearing.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="account-center">
    <div class="panel-head">
      <div>
        <h2>账户</h2>
        <p class="muted">Binance 只读账户：余额与挂单查询，不涉及下单。</p>
      </div>
      <button class="icon-button" :disabled="loading" @click="refresh" title="刷新">
        <RefreshCw :size="16" :class="{ spinning: loading }" />
      </button>
    </div>

    <div v-if="error" class="alert error">{{ error }}</div>

    <div v-if="status && !status.configured" class="panel">
      <h3><KeyRound :size="16" /> 接入 Binance 只读账户</h3>
      <p class="muted">填写只读 API Key（请勿给交易/提现权限）。密钥保存在服务器 0600 文件中，不进 git；保存后自动验证连通性，无需重启。</p>
      <div class="cred-form">
        <label><span>API Key</span><input v-model="credKey" placeholder="Binance API Key" autocomplete="off" /></label>
        <label><span>API Secret</span><input v-model="credSecret" type="password" placeholder="Binance API Secret" autocomplete="off" /></label>
        <div class="cred-actions">
          <button class="primary-btn" type="button" :disabled="credSaving || !credKey || !credSecret" @click="saveCredentials">
            {{ credSaving ? '验证中…' : '保存并验证' }}
          </button>
        </div>
        <p v-if="credError" class="error">{{ credError }}</p>
        <p v-if="credOk" class="ok">{{ credOk }}</p>
      </div>
    </div>

    <div v-if="status?.configured" class="panel">
      <h3><KeyRound :size="16" /> API 密钥</h3>
      <p class="muted">已配置只读密钥 <code>{{ credPrefix || '****' }}</code>。如需更换，先删除再重新填写。</p>
      <button class="danger-btn" type="button" :disabled="credSaving" @click="deleteCredentials">删除密钥</button>
    </div>

    <div v-if="status?.configured" class="cards">
      <div class="stat-card">
        <Wallet :size="18" />
        <div>
          <small>资产种类</small>
          <strong>{{ balances.length }}</strong>
        </div>
      </div>
      <div class="stat-card">
        <ShieldCheck :size="18" />
        <div>
          <small>挂单数量</small>
          <strong>{{ openOrderCount }}</strong>
        </div>
      </div>
      <div class="stat-card">
        <span class="dot live"></span>
        <div>
          <small>模式</small>
          <strong>只读</strong>
        </div>
      </div>
    </div>

    <div v-if="status?.configured" class="panel">
      <h3>余额</h3>
      <table class="data-table">
        <thead>
          <tr><th>资产</th><th>可用</th><th>总额</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in balances" :key="b.asset">
            <td><strong>{{ b.asset }}</strong></td>
            <td class="num">{{ b.available }}</td>
            <td class="num">{{ b.total }}</td>
          </tr>
          <tr v-if="!balances.length"><td colspan="3" class="muted">无余额数据</td></tr>
        </tbody>
      </table>
      <p v-if="fetchedAt" class="muted small">更新于 {{ fetchedAt }}</p>
    </div>

    <div v-if="status?.configured && openOrderIds.length" class="panel">
      <h3>挂单</h3>
      <ul class="order-list">
        <li v-for="id in openOrderIds" :key="id"><code>{{ id }}</code></li>
      </ul>
    </div>

    <div v-if="pause?.paused" class="alert error">
      <ShieldCheck :size="16" />
      <div>
        <strong>安全暂停已触发</strong>
        <p>{{ pause.reason }}</p>
        <button class="btn" :disabled="clearing" @click="clearPause">解除暂停</button>
      </div>
    </div>

    <div v-if="recon" class="panel">
      <h3>对账状态</h3>
      <p class="muted small">
        调度器：{{ recon.running ? '运行中' : '已停止' }} ·
        间隔 {{ recon.interval_seconds }} 秒 ·
        网关：{{ recon.gateway_configured ? '已配置' : '未配置' }} ·
        上次：{{ recon.last_run?.status || '未运行' }}
      </p>
      <div v-if="recon.recent_runs?.length">
        <h4>最近对账</h4>
        <table class="data-table">
          <thead><tr><th>时间</th><th>结果</th><th>差异数</th></tr></thead>
          <tbody>
            <tr v-for="r in recon.recent_runs.slice(0, 5)" :key="r.run_id">
              <td>{{ r.checked_at }}</td>
              <td>{{ r.balanced ? '✓ 平衡' : '✗ 差异' }}</td>
              <td>{{ r.differences?.length || 0 }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <h3>自动交易开关</h3>
      <p class="muted">自动交易总开关，默认关闭。打开后，还需在模拟盘为每个策略单独开启自动交易，两者同时打开，策略信号才会自动下模拟单。真实执行保持关闭。</p>
      <label class="switch-row">
        <span>自动交易</span>
        <button
          class="switch"
          :class="{ on: trading?.auto_trading_enabled }"
          :disabled="toggling || !trading"
          @click="toggleTrading"
          role="switch"
          :aria-checked="!!trading?.auto_trading_enabled"
        >
          <span class="knob"></span>
        </button>
        <strong>{{ trading?.auto_trading_enabled ? '已启用' : '已关闭' }}</strong>
      </label>
    </div>
  </div>
</template>

<style scoped>
.account-center { display: grid; gap: 16px; }
.panel-head { display: flex; align-items: center; justify-content: space-between; }
.panel-head h2 { margin: 0; }
.muted { color: var(--dim); }
.small { font-size: 11px; }
.alert { display: flex; gap: 10px; padding: 12px 14px; border-radius: 6px; font-size: 12px; }
.alert.error { background: rgba(255,90,90,.08); border: 1px solid rgba(255,90,90,.3); }
.alert.warn { background: rgba(255,180,60,.08); border: 1px solid rgba(255,180,60,.3); }
.alert code { background: rgba(0,0,0,.3); padding: 1px 5px; border-radius: 3px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; }
.stat-card { display: flex; gap: 10px; align-items: center; padding: 12px 16px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel-soft); }
.stat-card small { display: block; color: var(--dim); font-size: 10px; }
.stat-card strong { font-size: 16px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.live { background: var(--cyan); }
.panel { border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; background: var(--panel); }
.panel h3 { margin: 0 0 10px; font-size: 13px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.data-table th, .data-table td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; }
.data-table .num { font-family: Consolas, monospace; text-align: right; }
.data-table th { color: var(--dim); font-size: 10px; }
.order-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.order-list code { font-size: 11px; }
.switch-row { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
.switch { width: 44px; height: 24px; border-radius: 99px; border: 1px solid var(--line-bright); background: #222; position: relative; cursor: pointer; }
.switch .knob { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #888; transition: all .15s; }
.switch.on { background: rgba(108,229,208,.25); border-color: var(--cyan); }
.switch.on .knob { left: 22px; background: var(--cyan); }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.cred-form { display: flex; flex-direction: column; gap: 10px; max-width: 420px; }
.cred-form label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.cred-form input { padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--input-bg); color: inherit; }
.cred-actions { display: flex; gap: 8px; }
.primary-btn { padding: 8px 16px; border-radius: 6px; border: none; background: var(--accent, #1faa53); color: #fff; cursor: pointer; }
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.danger-btn { padding: 8px 16px; border-radius: 6px; border: 1px solid #f0433a; background: transparent; color: #f0433a; cursor: pointer; }
.error { color: #f0433a; font-size: 13px; }
.ok { color: #1faa53; font-size: 13px; }
</style>
