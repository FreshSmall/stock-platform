import client from './client';

export const submitBacktest = (req: Record<string, any>) =>
  client.post('/backtest', req).then((r) => r.data);

export const getBacktest = (runId: string) =>
  client.get(`/backtest/${runId}`).then((r) => r.data);
