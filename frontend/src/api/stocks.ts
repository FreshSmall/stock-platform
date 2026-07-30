import client from './client';
import type { Page, StockRow } from './types';

// V1.5 Stage H — the stocks (股票) list endpoint.
//
// The backend reads the latest daily snapshot joined to the stock pool, with
// optional industry / tag filters, sortable columns and standard pagination.
// We surface the params as plain optional keys so callers omit what they don't
// need (axios drops undefined query params automatically).

export type StockSortKey =
  | 'pct_change'
  | 'amount'
  | 'total_mv'
  | 'pe'
  | 'price'
  | 'turnover';

// Quick-tag preset keys; must match the backend tag enum exactly.
export type StockTagKey =
  | 'limit_up'
  | 'limit_down'
  | 'top_gainers'
  | 'low_price'
  | 'high_turnover';

export interface FetchStocksParams {
  industry?: string;
  tag?: string;
  sort?: StockSortKey;
  order?: 'asc' | 'desc';
  page?: number;
  size?: number;
}

export const fetchStocks = (params: FetchStocksParams = {}) =>
  client
    .get<Page<StockRow>>('/stocks', { params })
    .then((r) => r.data);

// Distinct industries for the filter dropdown. The stocks endpoint supports an
// optional industry list helper; if absent we fall back to deriving it client
// side — but exposing it here keeps the page decoupled from that detail.
export const fetchIndustries = () =>
  client.get<string[]>('/stocks/industries').then((r) => r.data);
