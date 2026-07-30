import client from './client';
import type {
  ChipDistribution,
  KLineItem,
  MinutePeriod,
  MoneyFlowDetailRow,
} from './types';

export const searchStocks = (q: string, limit?: number) =>
  client.get('/stock/search', { params: { q, limit } }).then((r) => r.data);

export const getStockInfo = (code: string) =>
  client.get(`/stock/${code}`).then((r) => r.data);

// K-line now accepts a `period` ('d' | 'w' | 'm'); the V1 daily default still
// works when `period` is omitted. The signature stays backward compatible.
export const getKline = (
  code: string,
  start?: string,
  end?: string,
  period?: 'd' | 'w' | 'm',
) =>
  client
    .get<KLineItem[]>(`/stock/${code}/kline`, {
      params: { start, end, period },
    })
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

// V1.5 Stage H additions.

// Chip-distribution (筹码峰) snapshot; returns null when no data exists.
export const fetchChipDistribution = (code: string, tradeDate?: string) =>
  client
    .get<ChipDistribution | null>(`/stock/${code}/chip-distribution`, {
      params: { trade_date: tradeDate },
    })
    .then((r) => r.data);

// Four-tier money-flow detail for the recent `days` rows (ascending).
export const fetchMoneyFlowDetail = (code: string, days = 30) =>
  client
    .get<MoneyFlowDetailRow[]>(`/stock/${code}/money-flow-detail`, {
      params: { days },
    })
    .then((r) => r.data);

// Intraday minute bars for a stock; period in {1,5,15,30,60} minutes.
export const fetchMinute = (code: string, period: MinutePeriod) =>
  client
    .get<KLineItem[]>(`/stock/${code}/minute`, { params: { period } })
    .then((r) => r.data);
