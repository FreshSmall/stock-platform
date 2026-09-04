import client from './client';
import type {
  FactorBrief,
  FactorCategory,
  FactorIC,
  FactorScoreRow,
  FactorSeriesPoint,
} from './types';

// V2 — Factor API (BP-V2-001/004/013). All endpoints under /api/v1/factor.

// List all factors, optionally filtered by category.
export const listFactors = (category?: FactorCategory | string) =>
  client
    .get<FactorBrief[]>('/factor', { params: { category } })
    .then((r) => r.data);

// Factor value series for one stock over a date range.
export const computeFactorSeries = (
  code: string,
  stock: string,
  start?: string,
  end?: string,
) =>
  client
    .get<FactorSeriesPoint[]>(`/factor/${code}/compute`, {
      params: { stock, start, end },
    })
    .then((r) => r.data);

// V2.1 sample-governance options shared by IC and scoring (defaults keep the
// unchanged V2 behaviour; pool="pit" swaps in the point-in-time universe).
export interface SampleFilters {
  pool?: 'current' | 'pit';
  exclude_st?: boolean;
  exclude_suspended?: boolean;
  only_tradable?: boolean;
}

// IC analysis for a factor on one rebalance date (latest if omitted).
export const computeFactorIC = (
  code: string,
  horizon = 5,
  tradeDate?: string,
  filters?: SampleFilters,
) =>
  client
    .get<FactorIC | null>(`/factor/${code}/ic`, {
      params: { horizon, trade_date: tradeDate, ...filters },
    })
    .then((r) => r.data);

// Multi-factor weighted scoring -> ranked stock list.
export const multiFactorScore = (
  factors: { code: string; weight: number }[],
  tradeDate?: string,
  filters?: SampleFilters,
) =>
  client
    .post<FactorScoreRow[] | null>('/factor/score', {
      factors,
      trade_date: tradeDate,
      ...filters,
    })
    .then((r) => r.data);
