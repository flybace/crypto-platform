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

async function loadBalances() {
  const res = await api.get('/account/balances');
  balances.value = res.data.balances || [];
  fetchedAt.value = res.data.fetched_at || '';
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
  if (!confirm(`确定要${label}自动交易吗？${next ? '启用后策略才允许提交真实订单（M6 未实现前无实际下单能力）。' : ''}`)) return;
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

    <div v-if="status && !status.configured" class="alert warn">
      <KeyRound :size="16" />
      <div>
        <strong>尚未配置 API Key</strong>
        <p>在服务器环境变量中设置 <code>CRYPTO_BINANCE_API_KEY</code> / <code>CRYPTO_BINANCE_API_SECRET</code> 后重启后端即可接入只读账户。密钥不会经过前端。</p>
      </div>
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

    <div class="panel">
      <h3>自动交易开关</h3>
      <p class="muted">自动交易必须在这里显式打开才会生效，默认关闭。当前 M6 真实下单尚未实现，打开此开关不会产生任何真实订单。</p>
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
</style>
