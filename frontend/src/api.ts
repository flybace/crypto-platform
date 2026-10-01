import axios from 'axios';

export const ACCESS_TOKEN_KEY = 'crypto_access_token';

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 12000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(ACCESS_TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && location.pathname !== '/login') {
      localStorage.removeItem(ACCESS_TOKEN_KEY);
      location.assign('/login');
    }
    return Promise.reject(error);
  },
);
