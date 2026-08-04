import client from './client';

export const submitBacktest = (req: Record<string, any>) =>
  client.post('/backtest', req).then((r) => r.data);

export const getBacktest = (runId: string) =>
  client.get(`/backtest/${runId}`).then((r) => r.data);

// V2 — drawdown + position curves for a finished run.
export interface DrawdownPoint {
  date: string;
  drawdown: number;
}
export interface PositionPoint {
  date: string;
  position: number;
}

export const getDrawdownCurve = (runId: string) =>
  client
    .get<DrawdownPoint[] | null>(`/backtest/${runId}/drawdown`)
    .then((r) => r.data);

export const getPositionCurve = (runId: string) =>
  client
    .get<PositionPoint[] | null>(`/backtest/${runId}/positions`)
    .then((r) => r.data);
