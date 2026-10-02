import { ref } from 'vue';

export type ThemeName = 'dark' | 'light';

const STORAGE_KEY = 'crypto-platform-theme';

const theme = ref<ThemeName>('dark');

function applyTheme(name: ThemeName) {
  theme.value = name;
  document.documentElement.setAttribute('data-theme', name);
  try {
    localStorage.setItem(STORAGE_KEY, name);
  } catch {
    /* storage unavailable: keep in-memory only */
  }
}

function initTheme() {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  applyTheme(saved === 'light' ? 'light' : 'dark');
}

export function useTheme() {
  return { theme, applyTheme, initTheme };
}
