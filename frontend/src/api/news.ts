import client from './client';
import type { NewsItem } from './types';

// V2 — News sentiment API (BP-V2-008). All endpoints under /api/v1/news.

// List news with sentiment, optionally filtered by stock/sector/date.
export const listNews = (
  params: { stock?: string; sector?: string; date?: string; limit?: number } = {},
) =>
  client
    .get<NewsItem[]>('/news', {
      params: { limit: 50, ...params },
    })
    .then((r) => r.data);

// Manually trigger a news sync (collection). Returns number of rows written.
export const syncNews = (limit = 50, score = false) =>
  client
    .post<{ written: number }>('/news/sync', null, {
      params: { limit, score },
    })
    .then((r) => r.data);
