import client from './client';

// V3a — Paper trading (模拟盘) API (BP-V3a-005 / T3.5). All endpoints under
// /api/v1/paper. The backend lands in a later wave, so page code wraps every
// call in try/catch and degrades to empty states on 404 / empty payloads.

export interface PaperFactorConfig {
  code: string;
  weight: number;
  direction?: number;
}

export interface PaperAccountConfig {
  preset?: string;
  factors?: PaperFactorConfig[];
  top_n?: number;
  freq?: 'W' | 'M' | number;
  initial_cash?: number;
  benchmark?: string;
  liquidity_top_k?: number;
  neutralize?: 'none' | 'industry' | 'industry_mcap';
}

// Summary row from the accounts list. Return-like fields (cum_ret/drawdown)
// are fractions (0.05 = 5%), consistent with the backtest metrics elsewhere.
export interface PaperAccountBrief {
  id: number;
  name: string;
  status: string; // running / paused / stopped
  config?: PaperAccountConfig;
  latest_nav?: number | null;
  cum_ret?: number | null;
  drawdown?: number | null;
  cash?: number | null;
  open_alerts?: number | null;
}

export interface PaperPosition {
  stock_code: string;
  shares: number;
  close_price: number | null;
  market_value: number | null;
  weight: number | null;
  pnl_pct?: number | null;
}

export interface PaperAlert {
  metric: string;
  status: string; // pass / warn / fail
  value: number | null;
  check_date: string | null;
}

export interface PaperAccountDetail {
  account: PaperAccountBrief;
  positions: PaperPosition[];
  alerts: PaperAlert[];
}

export interface PaperNavPoint {
  trade_date: string;
  nav: number | null;
  daily_ret?: number | null;
  benchmark_nav?: number | null;
  drawdown?: number | null;
}

export interface PaperNavResponse {
  series: PaperNavPoint[];
  attribution: {
    excess?: number | null;
    cost_drag?: number | null;
    cash_drag?: number | null;
    selection?: number | null;
  } | null;
}

export interface PaperTrade {
  price: number | null;
  amount: number | null;
  commission: number | null;
  stamp_duty: number | null;
  transfer_fee: number | null;
}

export interface PaperOrder {
  signal_date: string | null;
  exec_date: string | null;
  stock_code: string;
  side: string; // buy / sell
  shares?: number | null;
  status: string; // filled / pending / skipped / deferred / cancelled
  fail_reason?: string | null;
  trade?: PaperTrade | null;
}

export interface PaperDriftResponse {
  weekly: {
    week: string;
    paper_ret: number | null;
    backtest_ret: number | null;
    diff: number | null;
  }[];
  cum_paper: number | null;
  cum_backtest: number | null;
  max_abs_weekly: number | null;
  holding_jaccard?: { date: string; value: number | null }[];
}

// List accounts (summary metrics included).
export const listPaperAccounts = () =>
  client
    .get<{ accounts: PaperAccountBrief[] }>('/paper/accounts')
    .then((r) => r.data);

// Create an account; returns the new id.
export const createPaperAccount = (body: {
  name: string;
  config: PaperAccountConfig;
}) =>
  client.post<{ id: number }>('/paper/accounts', body).then((r) => r.data);

// Account detail: config + current positions + unresolved alerts.
export const getPaperAccount = (id: number) =>
  client.get<PaperAccountDetail>(`/paper/accounts/${id}`).then((r) => r.data);

// Lifecycle: pause stops signal generation; stop is terminal.
export const setPaperAccountStatus = (
  id: number,
  action: 'pause' | 'resume' | 'stop',
) =>
  client
    .post<{ ok: boolean }>(`/paper/accounts/${id}/status`, { action })
    .then((r) => r.data);

// NAV series vs benchmark + return attribution over [start, end].
export const getPaperNav = (id: number, start?: string, end?: string) =>
  client
    .get<PaperNavResponse>(`/paper/accounts/${id}/nav`, {
      params: { start, end },
    })
    .then((r) => r.data);

// Rebalance order history with simulated trade details (fees).
export const getPaperOrders = (id: number, start?: string, end?: string) =>
  client
    .get<{ orders: PaperOrder[] }>(`/paper/accounts/${id}/orders`, {
      params: { start, end },
    })
    .then((r) => r.data);

// Paper vs backtest weekly-return drift over [start, end].
export const getPaperDrift = (id: number, start?: string, end?: string) =>
  client
    .get<PaperDriftResponse>(`/paper/accounts/${id}/drift`, {
      params: { start, end },
    })
    .then((r) => r.data);

// Manual catch-up run (paper_tick) for one account; omit dates to advance to
// the latest trade date.
export const tickPaperAccount = (id: number, dates?: string[]) =>
  client
    .post<{ run_id?: number; async?: boolean }>(`/paper/accounts/${id}/tick`, {
      dates,
    })
    .then((r) => r.data);

// Admin: 推进所有 running 账户的每日 paper_tick。与 factor-health run 同模式：
// 返回 {run_id, async: true}，调用方轮询 api/admin 的 getRun(runId) 直到结束。
// 后端后续波次按同模式挂载 POST /admin/paper/run。
export const runPaperTickAll = () =>
  client
    .post<{ run_id: number; async: boolean }>('/admin/paper/run')
    .then((r) => r.data);
