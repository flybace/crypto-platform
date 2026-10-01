<script setup lang="ts">
import { ref } from 'vue';
import { Activity, ArrowRight, Database, LockKeyhole, ShieldCheck } from 'lucide-vue-next';
import { useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const username = ref('');
const password = ref('');
const loading = ref(false);
const error = ref('');
const auth = useAuthStore();
const router = useRouter();

const submit = async () => {
  error.value = '';
  loading.value = true;
  try {
    await auth.login(username.value.trim(), password.value);
    await router.replace('/');
  } catch (cause: any) {
    error.value = cause.response?.data?.detail || '服务不可用，请检查后端是否启动。';
  } finally {
    loading.value = false;
  }
};
</script>

<template>
  <main class="auth-page">
    <section class="auth-rail" aria-labelledby="product-title">
      <div class="brand-lockup"><Activity :size="18" /> VECTOR / CRYPTO</div>
      <div class="auth-copy">
        <p class="kicker">MULTI-MARKET RESEARCH SYSTEM · 0.1</p>
        <h1 id="product-title">把每一个价差，放回真实的市场里。</h1>
        <p>独立运行的数字资产行情、策略研究与受控交易工作台。</p>
      </div>
      <div class="rail-signals">
        <span><Database :size="15" /> 数据边界独立</span>
        <span><ShieldCheck :size="15" /> 默认只读执行</span>
        <span><LockKeyhole :size="15" /> 认证后访问</span>
      </div>
    </section>

    <section class="auth-panel" aria-labelledby="login-title">
      <div class="panel-heading">
        <p class="kicker">SECURE ACCESS</p>
        <h2 id="login-title">进入研究工作台</h2>
        <p class="muted">使用本地开发账号登录。真实账户和交易权限尚未启用。</p>
      </div>
      <form @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="admin" required />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="primary-button" type="submit" :disabled="loading">
          <span>{{ loading ? '正在验证' : '进入工作台' }}</span>
          <ArrowRight :size="17" />
        </button>
      </form>
      <p class="panel-footnote">AUTH / LOCAL DEVELOPMENT · EXECUTION DISABLED</p>
    </section>
  </main>
</template>
