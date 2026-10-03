<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { FlaskConical, History, Play, RefreshCw, Save } from 'lucide-vue-next';
import { api } from '../api';
import { isQueuedTask, resolveTaskResponse, taskStatusLabel } from '../services/taskPolling';
import type { StrategyDefinition } from '../types';

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

interface TuneEntry {
  params: Record<string, any>;
  score: number | null;
  total_return_pct: number | null;
  max_drawdown_pct: number | null;
  win_rate_pct: number | null;
  trade_count: number;
  run_id?: string;
  validation_status?: string;
  validation_total_return_pct?: number | null;
  validation_max_drawdown_pct?: number | null;
  validation_win_rate_pct?: number | null;
  robust_score?: number | null;
  overfit_guard_passed?: boolean;
}

interface TuneResult {
  tune_id: string;
  strategy_id: string;
  total_combinations: number;
  completed: number;
  failed: number;
  ranked: TuneEntry[];
  best: TuneEntry | null;
  validation: {
    enabled: boolean;
    validation_ratio: number;
    train_start_at: string;
    train_end_at: string;
    validation_start_at: string;
    validation_end_at: string;
  } | null;
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
const validationRatio = ref('0.3');
const validationTopN = ref('10');
const tuning = ref(false);
const taskMessage = ref('');
const result = ref<TuneResult | null>(null);
const error = ref('');
const history = ref<TuneResult[]>([]);
const showHistory = ref(false);
const presetName = ref('');
const presetSaved = ref('');
const savingPreset = ref(false);

const selectedDef = computed(() =>
  props.strategies.find((s) => s.strategy_id === selectedStrategy.value)
);
const paramFields = computed(() => toParamFields(selectedDef.value?.parameter_schema));
const hasValidation = computed(() => !!result.value?.validation?.enabled);

function initGrids() {
  const fields = paramFields.value;
  if (!fields.length) return;
  const grids: Record<string, string> = {};
  for (const f of fields) {
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

async function loadHistory() {
  try {
    const { data } = await api.get<{ items: TuneResult[] }>('/backtests/tunes', { params: { limit: 20 } });
    history.value = data.items || [];
  } catch {
    // history is best-effort
  }
}

async function runTuning() {
  if (!selectedStrategy.value) {
    error.value = '请先选择策略';
    return;
  }
  error.value = '';
  taskMessage.value = '';
  tuning.value = true;
  result.value = null;
  try {
    const grids: Record<string, any[]> = {};
    for (const [k, v] of Object.entries(paramGrids.value)) {
      const vals = v.split(',').map((s) => s.trim()).filter(Boolean);
      if (!vals.length) throw new Error(`参数 ${k} 的网格不能为空`);
      const def = paramFields.value.find((f) => f.key === k);
      grids[k] = vals.map((s) => (def?.type === 'integer' ? parseInt(s, 10) : s));
    }
    const payload = {
      venue_id: props.venueId,
      symbol: props.symbol,
      interval: props.interval,
      strategy_id: selectedStrategy.value,
      param_grids: grids,
      metric: metric.value,
      max_combinations: 100,
      validation_ratio: Math.min(0.49, Math.max(0, Number(validationRatio.value) || 0)),
      validation_top_n: Math.min(50, Math.max(1, parseInt(validationTopN.value, 10) || 10)),
    };
    const res = await api.post('/backtests/tune', payload);
    if (isQueuedTask(res.data)) {
      taskMessage.value = `调参已排队 · ${res.data.task_id}`;
      const detail = await resolveTaskResponse<{ result?: TuneResult }>(res.data, {
        onUpdate: (task) => {
          taskMessage.value = `调参${taskStatusLabel(task.status)}`;
        },
      });
      void detail;
      // Worker persists the tune result; fetch the latest from history
      await loadHistory();
      result.value = history.value[0] || null;
      taskMessage.value = '调参已完成';
    } else {
      result.value = res.data as TuneResult;
    }
    await loadHistory();
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e.message || '调参失败';
  } finally {
    tuning.value = false;
  }
}

function viewHistory(item: TuneResult) {
  result.value = item;
  showHistory.value = false;
}

async function savePreset() {
  if (!result.value?.best || !selectedStrategy.value) return;
  if (!presetName.value.trim()) {
    error.value = '请先给预设起个名字';
    return;
  }
  savingPreset.value = true;
  presetSaved.value = '';
  try {
    const best = result.value.best;
    await api.post('/backtests/presets', {
      strategy_id: selectedStrategy.value,
      name: presetName.value.trim(),
      parameters: best.params,
      venue_id: props.venueId,
      symbol: props.symbol,
      interval: props.interval,
      tune_id: result.value.tune_id,
      metrics: {
        total_return_pct: best.total_return_pct,
        validation_total_return_pct: best.validation_total_return_pct,
        max_drawdown_pct: best.max_drawdown_pct,
        win_rate_pct: best.win_rate_pct,
        overfit_guard_passed: best.overfit_guard_passed,
      },
      note: hasValidation.value ? '训练/验证双窗口调参' : '单窗口调参',
    });
    presetSaved.value = `预设「${presetName.value.trim()}」已保存，可在回测页加载使用`;
    presetName.value = '';
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e.message || '保存预设失败';
  } finally {
    savingPreset.value = false;
  }
}

const fmtPct = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;

const guardBadge = (entry: TuneEntry) => {
  if (!hasValidation.value) return null;
  if (entry.validation_status === 'skipped') return { text: '未验证', cls: 'guard-skip' };
  if (entry.validation_status === 'failed') return { text: '验证失败', cls: 'guard-fail' };
  return entry.overfit_guard_passed
    ? { text: '双窗口盈利', cls: 'guard-pass' }
    : { text: '疑似过拟合', cls: 'guard-fail' };
};

onMounted(loadHistory);
</script>

<template>
  <section class="tune-panel" aria-labelledby="tune-title">
    <div class="section-heading">
      <div><p class="kicker">PARAMETER TUNING</p><h2 id="tune-title">自动调参</h2></div>
      <div class="heading-actions">
        <button class="icon-button" type="button" title="调参历史" @click="showHistory = !showHistory">
          <History :size="15" />
        </button>
        <FlaskConical :size="18" class="section-icon" />
      </div>
    </div>
    <p class="muted">
      网格搜索策略参数，默认按时间切分训练/验证窗口——只在两个窗口都盈利的参数才算过关，防止过拟合。
      当前数据集：{{ props.venueId.toUpperCase() }} · {{ props.symbol }} · {{ props.interval }}
    </p>

    <div v-if="showHistory" class="history-box">
      <h4>调参历史</h4>
      <div v-if="!history.length" class="muted">暂无历史记录</div>
      <div v-else class="history-list">
        <button
          v-for="h in history"
          :key="h.tune_id"
          type="button"
          class="history-row"
          @click="viewHistory(h)"
        >
          <span>{{ h.strategy_id }}</span>
          <span class="muted">{{ h.completed }}/{{ h.total_combinations }}</span>
          <b :class="Number(h.best?.robust_score ?? h.best?.score) >= 0 ? 'positive' : 'negative'">
            {{ fmtPct(h.best?.robust_score ?? h.best?.score) }}
          </b>
        </button>
      </div>
    </div>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="taskMessage" class="task-message">{{ taskMessage }}</div>

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

    <div class="tune-options">
      <label class="backtest-field">
        <span>优化目标</span>
        <select v-model="metric">
          <option value="total_return_pct">总收益率</option>
          <option value="max_drawdown_pct">最小回撤</option>
          <option value="win_rate_pct">胜率</option>
        </select>
      </label>
      <label class="backtest-field">
        <span>验证集比例（0=关闭）</span>
        <input v-model="validationRatio" placeholder="0.3" />
      </label>
      <label class="backtest-field">
        <span>验证 Top-N</span>
        <input v-model="validationTopN" placeholder="10" />
      </label>
    </div>

    <button class="backtest-submit" :disabled="tuning || !selectedStrategy" @click="runTuning">
      <Play v-if="!tuning" :size="15" />
      <RefreshCw v-else :size="15" class="spinning" />
      <span>{{ tuning ? '调参中…' : '开始调参' }}</span>
    </button>

    <div v-if="result" class="tune-results">
      <h3>调参结果 <small>{{ result.completed }}/{{ result.total_combinations }} 完成</small></h3>
      <div v-if="hasValidation" class="validation-info">
        训练窗口 {{ result.validation!.train_start_at.slice(0, 10) }} ~ {{ result.validation!.train_end_at.slice(0, 10) }}
        ｜ 验证窗口 {{ result.validation!.validation_start_at.slice(0, 10) }} ~ {{ result.validation!.validation_end_at.slice(0, 10) }}
      </div>
      <div v-if="result.best" class="best-box">
        <strong>最优参数</strong>
        <code>{{ JSON.stringify(result.best.params) }}</code>
        <span v-if="hasValidation && result.best.validation_status === 'completed'">
          训练 <b :class="Number(result.best.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ fmtPct(result.best.total_return_pct) }}</b>
          验证 <b :class="Number(result.best.validation_total_return_pct) >= 0 ? 'positive' : 'negative'">{{ fmtPct(result.best.validation_total_return_pct) }}</b>
        </span>
        <span v-else :class="Number(result.best.score) >= 0 ? 'positive' : 'negative'">
          {{ fmtPct(result.best.score) }}
        </span>
        <span v-if="guardBadge(result.best)" :class="['guard-badge', guardBadge(result.best)!.cls]">
          {{ guardBadge(result.best)!.text }}
        </span>
      </div>
      <div v-if="result.best" class="preset-save">
        <input v-model="presetName" placeholder="预设名称，如：macd-btc-双窗口" />
        <button class="preset-button" :disabled="savingPreset" @click="savePreset">
          <Save :size="13" />
          <span>{{ savingPreset ? '保存中…' : '保存为参数预设' }}</span>
        </button>
        <span v-if="presetSaved" class="preset-ok">{{ presetSaved }}</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>#</th><th>参数</th>
            <th v-if="hasValidation">训练收益</th><th v-if="hasValidation">验证收益</th>
            <th v-if="!hasValidation">收益</th>
            <th>回撤</th><th>胜率</th><th>交易</th><th v-if="hasValidation">过拟合检查</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in result.ranked.slice(0, 10)" :key="i" :class="{ 'best-row': i === 0 }">
            <td>{{ i + 1 }}</td>
            <td><code>{{ JSON.stringify(r.params) }}</code></td>
            <td v-if="hasValidation" :class="Number(r.total_return_pct) >= 0 ? 'positive' : 'negative'">{{ fmtPct(r.total_return_pct) }}</td>
            <td v-if="hasValidation" :class="Number(r.validation_total_return_pct) >= 0 ? 'positive' : 'negative'">{{ fmtPct(r.validation_total_return_pct) }}</td>
            <td v-if="!hasValidation" :class="Number(r.score) >= 0 ? 'positive' : 'negative'">{{ fmtPct(r.score) }}</td>
            <td>{{ fmtPct(r.max_drawdown_pct) }}</td>
            <td>{{ r.win_rate_pct === null ? '—' : `${Number(r.win_rate_pct).toFixed(1)}%` }}</td>
            <td>{{ r.trade_count }}</td>
            <td v-if="hasValidation">
              <span v-if="guardBadge(r)" :class="['guard-badge', guardBadge(r)!.cls]">{{ guardBadge(r)!.text }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="muted">「双窗口盈利」= 训练集和验证集都赚钱的参数，最不容易过拟合。「疑似过拟合」= 只在训练集赚钱，验证集亏钱，实盘慎用。</p>
    </div>
  </section>
</template>

<style scoped>
.tune-panel { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); margin-top: 16px; }
.section-heading { display: flex; justify-content: space-between; align-items: center; }
.heading-actions { display: flex; gap: 8px; align-items: center; }
.icon-button { background: none; border: 1px solid var(--line); border-radius: 4px; padding: 5px 7px; color: var(--dim); cursor: pointer; }
.kicker { font-size: 10px; color: var(--dim); letter-spacing: .1em; margin: 0; }
.section-heading h2 { margin: 4px 0 0; font-size: 15px; }
.muted { color: var(--dim); font-size: 12px; margin: 0; }
.backtest-field { display: grid; gap: 6px; font-size: 12px; color: var(--muted); }
.backtest-field select, .backtest-field input { min-height: 36px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 8px 10px; background: var(--input-bg); color: var(--ink); font-size: 12px; }
.grid-inputs { display: grid; gap: 10px; }
.tune-options { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.backtest-submit { display: inline-flex; align-items: center; gap: 8px; padding: 10px 18px; border: 1px solid var(--cyan); border-radius: 6px; background: rgba(108,229,208,.1); color: var(--cyan); cursor: pointer; font-size: 13px; }
.backtest-submit:disabled { opacity: .5; cursor: not-allowed; }
.inline-error { color: var(--red); font-size: 12px; padding: 8px; background: rgba(255,90,90,.08); border-radius: 4px; }
.task-message { color: var(--cyan); font-size: 12px; }
.tune-results { display: grid; gap: 10px; }
.tune-results h3 small { color: var(--dim); font-weight: normal; }
.validation-info { font-size: 11px; color: var(--dim); }
.best-box { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 10px 12px; background: rgba(108,229,208,.06); border: 1px solid rgba(108,229,208,.25); border-radius: 6px; font-size: 12px; }
.best-box code { font-size: 11px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.data-table th, .data-table td { padding: 7px 8px; border-bottom: 1px solid var(--line); text-align: left; }
.data-table th { color: var(--dim); font-size: 10px; }
.data-table code { font-size: 10px; }
.best-row { background: rgba(108,229,208,.04); }
.positive { color: var(--cyan); }
.negative { color: var(--red); }
.guard-badge { font-size: 10px; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
.guard-pass { background: rgba(108,229,208,.12); color: var(--cyan); border: 1px solid rgba(108,229,208,.3); }
.guard-fail { background: rgba(255,90,90,.1); color: var(--red); border: 1px solid rgba(255,90,90,.3); }
.guard-skip { background: rgba(128,128,128,.1); color: var(--dim); border: 1px solid var(--line); }
.history-box { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; display: grid; gap: 8px; }
.history-box h4 { margin: 0; font-size: 12px; }
.history-list { display: grid; gap: 6px; max-height: 180px; overflow-y: auto; }
.history-row { display: flex; gap: 10px; align-items: center; background: none; border: 1px solid var(--line); border-radius: 4px; padding: 6px 10px; cursor: pointer; color: var(--ink); font-size: 11px; text-align: left; }
.history-row:hover { border-color: var(--cyan); }
.preset-save { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.preset-save input { min-height: 34px; border: 1px solid var(--line-bright); border-radius: 5px; padding: 6px 10px; background: var(--input-bg); color: var(--ink); font-size: 12px; flex: 1; min-width: 180px; }
.preset-button { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border: 1px solid var(--cyan); border-radius: 6px; background: rgba(108,229,208,.1); color: var(--cyan); cursor: pointer; font-size: 12px; }
.preset-button:disabled { opacity: .5; cursor: not-allowed; }
.preset-ok { color: var(--cyan); font-size: 11px; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
