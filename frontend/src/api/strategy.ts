import client from './client';

export const listStrategies = () => client.get('/strategy').then((r) => r.data);
