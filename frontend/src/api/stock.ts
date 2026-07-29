import client from './client';

export const searchStocks = (q: string, limit?: number) =>
  client.get('/stock/search', { params: { q, limit } }).then((r) => r.data);

export const getStockInfo = (code: string) =>
  client.get(`/stock/${code}`).then((r) => r.data);

export const getKline = (code: string, start?: string, end?: string) =>
  client
    .get(`/stock/${code}/kline`, { params: { start, end } })
    .then((r) => r.data);

export const getIndicators = (
  code: string,
  type: string,
  start?: string,
  end?: string,
) =>
  client
    .get(`/stock/${code}/indicators`, { params: { type, start, end } })
    .then((r) => r.data);
