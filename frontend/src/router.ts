import { createRouter, createWebHistory } from 'vue-router';
import LoginView from './views/LoginView.vue';
import WorkspaceView from './views/WorkspaceView.vue';
import { useAuthStore } from './stores/auth';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    { path: '/', name: 'workspace', component: WorkspaceView, meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (to.meta.requiresAuth && !auth.user) {
    if (!(await auth.restore())) return { name: 'login' };
  }
  if (to.name === 'login' && auth.user) return { name: 'workspace' };
  return true;
});

export default router;
