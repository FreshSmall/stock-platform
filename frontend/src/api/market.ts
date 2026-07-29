import client from './client';

export const getIndices = () => client.get('/market/indices').then((r) => r.data);

export const getMarketSummary = () => client.get('/market/summary').then((r) => r.data);

export const getHotStocks = (sort?: string, limit?: number) =>
  client
    .get('/market/hot-stocks', { params: { sort, limit } })
    .then((r) => r.data);
