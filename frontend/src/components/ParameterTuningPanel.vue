<script setup lang="ts">
import { computed, ref } from 'vue';
import { FlaskConical, Play, RefreshCw } from 'lucide-vue-next';
import { api } from '../api';
import type { StrategyDefinition } from '../types';

interface ParamField {
  key: string;
  label: string;
  type: string;
  default: any;
  min: number;
  max: number;
}

interface ParamFieldView {
  key: string;
  label: string;
  type: string;
  default: any;
  min: number;
  max: number;
}

function toParamFields(schema: Record<string, unknown>[] | undefined): ParamFieldView[] {
  if (!schema) return [];
  return schema.map((f) => ({
    key: String(f['key'] ?? ''),
    label: String(f['label'] ?? ''),
    type: String(f['type'] ?? ''),
    default: f['default'],
    min: Number(f['min'] ?? 0),
    max: Number(f['max'] ?? 0),
  }));
}

interface TuneResult {
  tune_id: string;
  strategy_id: string;
  total_combinations: number;
  completed: number;
  failed: number;
  ranked: Array<{
    params: Record<string, any>;
    score: number | null;
    total_return_pct: number | null;
    max_drawdown_pct: number | null;
    win_rate_pct: number | null;
    trade_count: number;
  }>;
  best: any;
}

const props = defineProps<{
  strategies: StrategyDefinition[];
  venueId: string;
  symbol: string;
  interval: string;
}>();

const selectedStrategy = ref('');
const paramGrids = ref<Record<string, string>>({});
const metric = ref('total_return_pct');
const tuning = ref(false);
const result = ref<TuneResult | null>(null);
const error = ref('');

const selectedDef = computed(() =>
  props.strategies.find((s) => s.strategy_id === selectedStrategy.value)
);
const paramFields = computed(() => toParamFields(selectedDef.value?.parameter_schema));

function initGrids() {
  const fields = paramFields.value;
  if (!fields.length) return;
  const grids: Record<string, string> = {};
  for (const f of fields) {
    // Default grid: 3 values around default
    if (f.type === 'integer') {
      const d = Number(f.default);
      const vals = [Math.max(f.min, d - 4), d, Math.min(f.max, d + 4)];
      grids[f.key] = [...new Set(vals)].join(',');
    } else {
      const d = Number(f.default);
      const vals = [d * 0.7, d, d * 1.3].map((v) => v.toFixed(4));
      grids[f.key] = [...new Set(vals)].join(',');
    }
  }
  paramGrids.value = grids;
}

function onStrategyChange() {
  initGrids();
  result.value = null;
}

async function runTuning() {
  if (!selectedStrategy.value) {
    error.value = '请先选择策略';
    return;
  }
  error.value = '';
  tuning.value = true;
  result.value = null;
  try {
    const grids: Record<string, any[]> = {};
    for (const [k, v] of Object.entries(paramGrids.value)) {
      const vals = v.split(',').map((s) => s.trim()).filter(Boolean);
      if (!vals.length) throw new Error(`参数 ${k} 的网格不能为空`);
      const def = paramFields.value.find((f) => f.key === k);
      grids[k] = vals.map((s) =>
        def?.type === 'integer' ? parseInt(s, 10) : s
      );
    }
    const res = await api.post('/backtests/tune', {
      venue_id: props.venueId,
      symbol: props.symbol,
      interval: props.interval,
      strategy_id: selectedStrategy.value,
      param_grids: grids,
      metric: metric.value,
      max_combinations: 100,
    });
    result.value = res.data;
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e.message || '调参失败';
  } finally {
    tuning.value = false;
  }
}

const fmtPct = (v: number | null) =>
  v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
</script>

<template>
  <section class="tune-panel" aria-labelledby="tune-title">
    <div class="section-heading">
      <div><p class="kicker">PARAMETER TUNING</p><h2 id="tune-title">自动调参</h2></div>
      <FlaskConical :size="18" class="section-icon" />
    </div>
    <p class="muted">网格搜索策略参数，按目标指标自动找出最优组合。</p>

    <div v-if="error" class="inline-error">{{ error }}</div>

    <label class="backtest-field">
      <span>策略</span>
      <select v-model="selectedStrategy" @change="onStrategyChange">
        <option value="">选择策略</option>
        <option v-for="s in props.strategies" :key="s.strategy_id" :value="s.strategy_id">
          {{ s.name }}
        </option>
      </select>
    </label>

    <div v-if="paramFields.length" class="grid-inputs">
      <label v-for="f in paramFields" :key="f.key" class="backtest-field">
        <span>{{ f.label }}（逗号分隔候选值）</span>
        <input v-model="paramGrids[f.key]" placeholder="如: 8,12,16" />
      </label>
    </div>

    <label class="backtest-field">
      <span>优化目标</span>
      <select v-model="metric">
        <option value="total_return_pct">总收益率</option>
        <option value="max_drawdown_pct">最小回撤</option>
        <option value="win_rate_pct">胜率</option>
      </select>
    </label>

    <button class="backtest-submit" :disabled="tuning || !selectedStrategy" @click="runTuning">
      <Play v-if="!tuning" :size="15" />
      <RefreshCw v-else :size="15" class="spinning" />
      <span>{{ tuning ? '调参中…' : '开始调参' }}</span>
    </button>

    <div v-if="result" class="tune-results">
      <h3>调参结果 <small>{{ result.completed }}/{{ result.total_combinations }} 完成</small></h3>
      <div v-if="result.best" class="best-box">
        <strong>最优参数</strong>
        <code>{{ JSON.stringify(result.best.params) }}</code>
        <span :class="Number(result.best.score) >= 0 ? 'positive' : 'negative'">
          {{ fmtPct(result.best.score) }}
        </span>
      </div>
      <table class="data-table">
        <thead><tr><th>#</th><th>参数</th><th>收益</th><th>回撤</th><th>胜率</th><th>交易</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in result.ranked.slice(0, 10)" :key="i" :class="{ 'best-row': i === 0 }">
            <td>{{ i + 1 }}</td>
            <td><code>{{ JSON.stringify(r.params) }}</code></td>
            <td :class="Number(r.score) >= 0 ? 'positive' : 'negative'">{{ fmtPct(r.score) }}</td>
            <td>{{ fmtPct(r.max_drawdown_pct) }}</td>
            <td>{{ r.win_rate_pct === null ? '—' : `${Number(r.win_rate_pct).toFixed(1)}%` }}</td>
            <td>{{ r.trade_count }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.tune-panel { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); margin-top: 16px; }
.section-heading { display: flex; justify-content: space-between; align-items: center; }
.kicker { font-size: 10px; color: var(--dim); letter-spacing: .1em; margin: 0; }
.section-heading h2 { margin: 4px 0 0; font-size: 15px; }
.muted { color: var(--dim); font-size: 12px; margin: 0; }
.backtest-field { display: grid; gap: 6px; font-size: 12px; color: var(--muted); }
.backtest-field select, .backtest-field input { min-height: 36px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; background: var(--input-bg); color: var(--ink); font-size: 12px; }
.grid-inputs { display: grid; gap: 10px; }
.backtest-submit { display: inline-flex; align-items: center; gap: 8px; padding: 10px 18px; border: 1px solid var(--cyan); border-radius: 6px; background: rgba(108,229,208,.1); color: var(--cyan); cursor: pointer; font-size: 13px; }
.backtest-submit:disabled { opacity: .5; cursor: not-allowed; }
.inline-error { color: var(--red); font-size: 12px; padding: 8px; background: rgba(255,90,90,.08); border-radius: 4px; }
.tune-results { display: grid; gap: 10px; }
.tune-results h3 small { color: var(--dim); font-weight: normal; }
.best-box { display: flex; gap: 10px; align-items: center; padding: 10px 12px; background: rgba(108,229,208,.06); border: 1px solid rgba(108,229,208,.25); border-radius: 6px; font-size: 12px; }
.best-box code { font-size: 11px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.data-table th, .data-table td { padding: 7px 8px; border-bottom: 1px solid var(--line); text-align: left; }
.data-table th { color: var(--dim); font-size: 10px; }
.data-table code { font-size: 10px; }
.best-row { background: rgba(108,229,208,.04); }
.positive { color: var(--cyan); }
.negative { color: var(--red); }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
