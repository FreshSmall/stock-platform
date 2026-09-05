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
  neutralize?: 'none' | 'industry' | 'industry_mcap';
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

// Multi-factor weighted scoring -> ranked stock list (V2.2: direction/top_n/preset).
export const multiFactorScore = (
  factors: { code: string; weight: number; direction?: number }[],
  tradeDate?: string,
  filters?: SampleFilters,
  opts?: { top_n?: number; preset?: string },
) =>
  client
    .post<FactorScoreRow[] | null>('/factor/score', {
      factors: opts?.preset ? [] : factors,
      preset: opts?.preset,
      top_n: opts?.top_n,
      trade_date: tradeDate,
      ...filters,
    })
    .then((r) => r.data);

// --- V2.2 research loop (BP-V2.2-001/002/005) -----------------------------

export interface FactorPreset {
  name: string;
  title: string;
  factors: { code: string; weight: number; direction: number }[];
}

export const listFactorPresets = () =>
  client.get<FactorPreset[]>('/factor/presets').then((r) => r.data);

export interface IcHorizonSummary {
  mean_ic: number | null;
  ic_std: number | null;
  icir: number | null;
  win_rate: number | null;
  n_dates: number;
}

export interface FactorIcSeries {
  factor_code: string;
  start: string;
  end: string;
  horizons: number[];
  step: number;
  pool: string;
  neutralized: string;
  series: { trade_date: string; horizon: number; ic: number | null; n: number }[];
  summary: Record<string, IcHorizonSummary>;
  by_year: Record<string, Record<string, number | null>>;
  persisted_rows: number;
}

// RankIC across rebalance dates, per horizon (mean IC decay by holding period).
export const computeFactorICSeries = (
  code: string,
  start: string,
  end: string,
  filters?: SampleFilters & { horizons?: string; step?: number; universe_size?: number },
) =>
  client
    .get<FactorIcSeries | null>(`/factor/${code}/ic-series`, {
      params: { start, end, ...filters },
    })
    .then((r) => r.data);

export interface LayerNav {
  layer: number;
  nav: number[];
  total_return: number | null;
  ann_return: number | null;
  vol: number | null;
  max_drawdown: number | null;
  avg_count: number | null;
}

export interface FactorLayeredBacktest {
  factor_code: string;
  start: string;
  end: string;
  step: number;
  n_layers: number;
  pool: string;
  neutralized: string;
  rebalance_dates: string[];
  layers: LayerNav[];
  long_short: {
    nav: number[];
    total_return: number | null;
    ann_return: number | null;
    vol: number | null;
    max_drawdown: number | null;
  };
}

// N-quantile equal-weight portfolio NAVs + long-short spread.
export const layeredBacktest = (code: string, body: Record<string, unknown>) =>
  client
    .post<FactorLayeredBacktest | null>(`/factor/${code}/layered-backtest`, body)
    .then((r) => r.data);

export interface MfBacktestMetrics {
  total_return: number | null;
  ann_return: number | null;
  vol: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  calmar: number | null;
  benchmark_return: number | null;
  avg_turnover: number | null;
  total_cost: number;
  n_rebalances: number;
}

export interface MfRebalance {
  rebalance_date: string;
  exec_date: string;
  target: string[];
  buys: { code: string; shares: number; price: number; fee: number }[];
  sells: { code: string; shares: number; price: number; fee: number }[];
  cost: number;
}

export interface MfPortfolioBacktest {
  run_id: string;
  metrics: MfBacktestMetrics;
  nav: { date: string; value: number }[];
  drawdown_curve: { date: string; drawdown: number }[];
  benchmark_curve: { date: string; value: number }[];
  turnover_series: { date: string; turnover: number }[];
  holdings_timeline: { date: string; holdings: { code: string; shares: number }[] }[];
  rebalances: MfRebalance[];
}

// Multi-factor portfolio backtest (periodic rebalance, cost-aware).
export const portfolioBacktest = (body: Record<string, unknown>) =>
  client
    .post<MfPortfolioBacktest | null>('/factor/portfolio-backtest', body)
    .then((r) => r.data);
