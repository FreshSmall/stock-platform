import client from './client';
import type {
  Portfolio,
  PortfolioBacktestResult,
  PortfolioHolding,
} from './types';

// V2 — Portfolio API (BP-V2-005). All endpoints under /api/v1/portfolio.

export const listPortfolios = () =>
  client.get<Portfolio[]>('/portfolio').then((r) => r.data);

export interface CreatePortfolioReq {
  name: string;
  description?: string;
  benchmark?: string;
  holdings: PortfolioHolding[];
}

export const createPortfolio = (req: CreatePortfolioReq) =>
  client.post<Portfolio>('/portfolio', req).then((r) => r.data);

export const getPortfolio = (id: number) =>
  client.get<Portfolio | null>(`/portfolio/${id}`).then((r) => r.data);

export interface UpdatePortfolioReq {
  name?: string;
  description?: string;
  benchmark?: string;
  holdings?: PortfolioHolding[];
}

export const updatePortfolio = (id: number, req: UpdatePortfolioReq) =>
  client.put<Portfolio | null>(`/portfolio/${id}`, req).then((r) => r.data);

export const deletePortfolio = (id: number) =>
  client.delete<{ deleted: boolean }>(`/portfolio/${id}`).then((r) => r.data);

// Weighted buy-and-hold NAV over a date range (start/end ISO dates, optional).
export const backtestPortfolio = (
  id: number,
  range?: { start?: string; end?: string },
) =>
  client
    .post<PortfolioBacktestResult | null>(`/portfolio/${id}/backtest`, range ?? {})
    .then((r) => r.data);
