import { create } from 'zustand';

interface AuthState {
  token: string | null;
  username: string | null;
  role: string | null; // 'user' | 'admin' (V1.5); null until /me resolves
  setAuth: (token: string, username: string) => void;
  setRole: (role: string) => void;
  isAdmin: () => boolean;
  logout: () => void;
  hydrate: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: localStorage.getItem('token'),
  username: localStorage.getItem('username'),
  role: localStorage.getItem('role'),
  setAuth: (token, username) => {
    localStorage.setItem('token', token);
    localStorage.setItem('username', username);
    set({ token, username });
  },
  setRole: (role) => {
    localStorage.setItem('role', role);
    set({ role });
  },
  isAdmin: () => get().role === 'admin',
  logout: () => {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    localStorage.removeItem('role');
    set({ token: null, username: null, role: null });
  },
  hydrate: () =>
    set({
      token: localStorage.getItem('token'),
      username: localStorage.getItem('username'),
      role: localStorage.getItem('role'),
    }),
}));
