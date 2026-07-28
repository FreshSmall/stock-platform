// Shared response types for the market/stock/analysis APIs.
//
// All fields are nullable because the backend Pydantic schemas (see
// backend/app/schemas/*.py) intentionally mark most numeric columns Optional —
// the read-only daily_prices/stock_pool tables legitimately contain NULLs and,
// for indices, the data is not yet populated. Keeping these loose avoids noisy
// runtime guards for every cell.

export interface IndexQuote {
  code: string;
  name: string;
  close: number | null;
  pct_change: number | null;
}

export interface MarketSummary {
  trade_date: string | null;
  advance_count: number;
  decline_count: number;
  flat_count: number;
  total_amount: number | null;
}

export interface HotStock {
  stock_code: string;
  stock_name: string | null;
  close: number | null;
  pct_change: number | null;
  amount: number | null;
}

export interface StockBrief {
  stock_code: string;
  stock_name: string | null;
  exchange: string | null;
  industry: string | null;
}

export interface StockInfo extends StockBrief {
  total_mv: number | null;
  circ_mv: number | null;
  pe: number | null;
  pb: number | null;
  list_date: string | null;
  close: number | null;
  pct_change: number | null;
}

export interface KLineItem {
  trade_date: string; // YYYY-MM-DD
  open: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  pct_change: number | null;
  turnover?: number | null;
}

// Indicator rows are a trade_date + a sparse subset of indicator values.
export interface MARow {
  trade_date: string;
  ma5?: number | null;
  ma10?: number | null;
  ma20?: number | null;
}
export interface MACDRow {
  trade_date: string;
  dif?: number | null;
  dea?: number | null;
  macd?: number | null;
}
export interface KDJRow {
  trade_date: string;
  k?: number | null;
  d?: number | null;
  j?: number | null;
}

export type IndicatorMap = {
  ma?: MARow[];
  macd?: MACDRow[];
  kdj?: KDJRow[];
};

export interface AnalysisScores {
  fundamental: number | null;
  technical: number | null;
  capital: number | null;
  news: number | null;
  risk: number | null;
}

export interface AnalysisResult {
  request_id: string;
  stock_code: string;
  score: number | null;
  scores: AnalysisScores;
  fundamentals: string | null;
  technicals: string | null;
  capital: string | null;
  news: string | null;
  risk: string | null;
  created_at: string | null;
}

export interface TriggerResult {
  request_id: string;
  stock_code: string;
}
