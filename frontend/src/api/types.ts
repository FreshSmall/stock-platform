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

// Admin: data sources / tasks / users (V1.5 management, aligned to the
// backend /api/v1/admin contract; V2.1 adds task runs + quality patrol).
export interface DataSourceRow {
  name: string;
  type: string | null;
  note: string | null;
}

export interface TaskRow {
  task_name: string;
  title: string; // human-readable name (V2.1)
  is_long?: boolean; // runs async via run_task_async
  last_status: string | null;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_rows: number | null;
}

// One sa_admin_task_log execution (V2.1: async long tasks + progress).
export interface TaskRunRow {
  id: number;
  task_name: string;
  started_at: string | null;
  finished_at: string | null;
  status: string; // running / success / failed
  rows_affected: number | null;
  progress_done: number | null;
  progress_total: number | null;
  result: Record<string, unknown> | string | null;
  error: string | null;
  triggered_by: string | null;
}

// One quality-patrol metric result for a check date (V2.1 BP-V2.1-007).
export interface QualityCheckRow {
  check_date: string;
  check_name: string;
  metric_name: string;
  metric_value: number | null;
  status: string; // pass / warn / fail
  detail: Record<string, unknown> | string | null;
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

// ---- V2 Stage N3 additions ------------------------------------------------

// Factors (因子). Categories: trend/momentum/volatility/volume/fundamental/sentiment.
export type FactorCategory =
  | 'trend'
  | 'momentum'
  | 'volatility'
  | 'volume'
  | 'fundamental'
  | 'sentiment';

export interface FactorBrief {
  code: string;
  name: string;
  category: FactorCategory;
}

// One factor value point for a stock on a trade day.
export interface FactorSeriesPoint {
  trade_date: string;
  value: number | null;
}

// IC analysis result for a single rebalance date.
export interface LayeredReturn {
  layer: number; // 1 = lowest factor value
  mean_return: number | null;
  count: number;
}

export interface FactorIC {
  factor_code: string;
  trade_date: string;
  horizon: number;
  ic: number | null;
  win_rate: number | null;
  layered_returns: LayeredReturn[] | null;
  universe_size: number;
}

// Multi-factor score row (ranked).
export interface FactorScoreRow {
  stock_code: string;
  score: number;
}

// Portfolios (组合).
export interface PortfolioHolding {
  stock_code: string;
  weight: number | null;
}

export interface Portfolio {
  id: number;
  name: string;
  description?: string | null;
  benchmark?: string | null;
  holdings: PortfolioHolding[];
  created_at?: string | null;
}

export interface PortfolioNavItem {
  id: number;
  name: string;
  description?: string | null;
  benchmark?: string | null;
  holdings_count?: number;
  return_rate?: number | null;
}

export interface PortfolioNavPoint {
  date: string;
  nav: number;
}

export interface PortfolioBacktestResult {
  nav_curve: PortfolioNavPoint[];
  return_rate?: number | null;
  max_drawdown?: number | null;
  holdings?: PortfolioHolding[];
}

// Agent reports (报告). Agents: sector/market/review/recommend.
export type ReportAgent = 'sector' | 'market' | 'review' | 'recommend';

export interface AgentReport {
  id: number;
  agent: ReportAgent;
  trade_date: string | null;
  title: string | null;
  target: string | null;
  summary: string | null;
  content: string | null;
  scores: Record<string, number> | null;
  created_at: string | null;
}

// News (新闻情绪).
export interface NewsItem {
  id: number;
  pub_time: string | null;
  title: string | null;
  content: string | null;
  source: string | null;
  stock_codes: string[];
  sector: string | null;
  sentiment: number | null; // -1 ~ +1
  summary: string | null;
}

// Knowledge base (RAG 知识库).
export interface KnowledgeDoc {
  id: number;
  title: string;
  source: string | null;
  stock_code: string | null;
  doc_date: string | null;
  status: string; // pending | embedded | failed
  created_at: string | null;
}

// One retrieved chunk surfaced as a citation in an assistant answer.
export interface KnowledgeSource {
  doc_id: number;
  title: string;
  source: string | null;
  stock_code: string | null;
  text: string;
  chunk_index: number;
  score: number;
}
