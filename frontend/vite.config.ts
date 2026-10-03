import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8290';
  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: 4191,
      // 允许 ngrok 固定公网域名访问（开发期）。allowedHosts: true 过于宽松，
      // 这里只放行我们自己的静态域名。
      allowedHosts: ['lankiness-hamburger-untidy.ngrok-free.dev'],
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/health': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
