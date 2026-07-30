import client from './client';
import type { UserProfile } from './types';

export const register = (username: string, password: string) =>
  client.post('/auth/register', { username, password }).then((r) => r.data);

export const login = (username: string, password: string) =>
  client.post('/auth/login', { username, password }).then((r) => r.data);

export const me = () =>
  client.get<UserProfile>('/auth/me').then((r) => r.data);
