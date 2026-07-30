import client from './client';
import type { NorthFlowRow, Sentiment } from './types';

export const getIndices = () => client.get('/market/indices').then((r) => r.data);

export const getMarketSummary = () => client.get('/market/summary').then((r) => r.data);

export const getHotStocks = (sort?: string, limit?: number) =>
  client
    .get('/market/hot-stocks', { params: { sort, limit } })
    .then((r) => r.data);

// V1.5 Stage H additions.

// Market sentiment rollup (limit-up/down, seal rate, streaks, ladder).
export const fetchSentiment = () =>
  client.get<Sentiment>('/market/sentiment').then((r) => r.data);

// Northbound net inflow (both channels) for the recent `days`.
export const fetchNorthFlow = (days = 30) =>
  client
    .get<NorthFlowRow[]>('/market/north-flow', { params: { days } })
    .then((r) => r.data);
