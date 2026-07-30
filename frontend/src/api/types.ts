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
  ema?: EMARow[];
  rsi?: RSIRow[];
  boll?: BOLLRow[];
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

// ---- V1.5 Stage H additions ------------------------------------------------

// Stocks list (GET /stocks). The response is a paginated page object.
export interface StockRow {
  stock_code: string;
  stock_name: string | null;
  industry: string | null;
  close: number | null;
  pct_change: number | null;
  amount: number | null;
  total_mv: number | null;
  circ_mv: number | null;
  pe: number | null;
  pb: number | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

// Sectors (板块): an industry or a concept.
export interface SectorRow {
  sector_code: string;
  sector_name: string;
  sector_type: string; // 'industry' | 'concept'
  trade_date: string | null;
  pct_change: number | null;
  amount: number | null;
  limit_up_count: number | null;
  main_net_inflow: number | null;
  leader_code: string | null;
  leader_name?: string | null;
}

export interface SectorStock {
  stock_code: string;
  stock_name: string | null;
  pct_change: number | null;
  close: number | null;
  amount: number | null;
  total_mv: number | null;
  is_leader?: boolean;
}

// Dragon-tiger (龙虎榜).
export interface DragonTigerRow {
  stock_code: string;
  stock_name: string | null;
  trade_date: string;
  reason: string | null;
  net_buy: number | null;
  buy_amount: number | null;
  sell_amount: number | null;
}

export interface DragonTigerSeat {
  rank: number;
  seat_name: string;
  buy_amount: number | null;
  sell_amount: number | null;
  net_amount: number | null;
  is_institution: boolean;
}

export interface DragonTigerSeats {
  buy: DragonTigerSeat[];
  sell: DragonTigerSeat[];
}

// Market sentiment (情绪).
export interface Sentiment {
  trade_date: string | null;
  limit_up_count: number | null;
  limit_down_count: number | null;
  failed_limit_count: number | null;
  seal_rate: number | null;
  max_streak: number | null;
  up_count: number | null;
  down_count: number | null;
  // streak_days -> count, e.g. {"1": 23, "2": 5, "3": 1}
  streak_ladder: Record<string, number> | null;
}

// Northbound flow (北向资金): one row per day.
export interface NorthFlowRow {
  trade_date: string;
  sh_net: number | null;
  sz_net: number | null;
}

// Chip distribution (筹码峰).
export interface ChipDistribution {
  trade_date: string | null;
  profit_ratio: number | null;
  avg_cost: number | null;
  concentration_90: number | null;
  concentration_70: number | null;
  cost_90_low: number | null;
  cost_90_high: number | null;
  // list of [price, weight] pairs
  distribution: [number, number][] | null;
}

// Money-flow detail (个股资金流向): one row per day.
export interface MoneyFlowDetailRow {
  trade_date: string;
  super_net: number | null;
  big_net: number | null;
  medium_net: number | null;
  small_net: number | null;
}

// Minute bars (分时): same shape as a K-line bar, period in {1,5,15,30,60}.
export type MinutePeriod = '1' | '5' | '15' | '30' | '60';

// Extended indicator rows (EMA / RSI / BOLL) used by the new indicator tabs.
export interface EMARow {
  trade_date: string;
  ema12?: number | null;
  ema26?: number | null;
}
export interface RSIRow {
  trade_date: string;
  rsi6?: number | null;
  rsi12?: number | null;
  rsi24?: number | null;
}
export interface BOLLRow {
  trade_date: string;
  up?: number | null;
  mid?: number | null;
  low?: number | null;
}

// Admin: data sources / tasks / users (V1.5 management).
export interface DataSourceRow {
  id: number;
  name: string;
  type: string | null;
  status: number | null; // 1=enabled, 0=disabled
  last_sync_at: string | null;
  last_status: string | null;
  last_message: string | null;
}

export interface TaskRow {
  id: number;
  name: string;
  cron: string | null;
  status: number | null; // 1=enabled, 0=disabled
  last_run_at: string | null;
  last_status: string | null;
  last_message: string | null;
}

export interface AdminUserRow {
  id: number;
  username: string;
  role: string; // 'user' | 'admin'
  status: number; // 1=enabled, 0=disabled
  created_at: string | null;
}

// Authenticated user profile (role added in V1.5).
export interface UserProfile {
  id: number;
  username: string;
  role?: string;
}
