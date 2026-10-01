import { defineStore } from 'pinia';
import { ACCESS_TOKEN_KEY, api } from '../api';
import type { User } from '../types';

type LoginResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(ACCESS_TOKEN_KEY) || '',
    user: null as User | null,
  }),
  actions: {
    async login(username: string, password: string) {
      const { data } = await api.post<LoginResponse>('/auth/login', { username, password });
      this.token = data.access_token;
      this.user = data.user;
      localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
    },
    async restore() {
      if (!this.token) return false;
      try {
        const { data } = await api.get<User>('/auth/me');
        this.user = data;
        return true;
      } catch {
        this.clear();
        return false;
      }
    },
    async logout() {
      try {
        if (this.token) await api.post('/auth/logout');
      } finally {
        this.clear();
      }
    },
    clear() {
      this.token = '';
      this.user = null;
      localStorage.removeItem(ACCESS_TOKEN_KEY);
    },
  },
});
