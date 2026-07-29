import client from './client';

export const register = (username: string, password: string) =>
  client.post('/auth/register', { username, password }).then((r) => r.data);

export const login = (username: string, password: string) =>
  client.post('/auth/login', { username, password }).then((r) => r.data);

export const me = () => client.get('/auth/me').then((r) => r.data);
