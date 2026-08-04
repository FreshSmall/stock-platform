import client from './client';
import type { AgentReport, ReportAgent } from './types';

// V2 — Agent reports API (BP-V2-009~012). All endpoints under /api/v1/reports.

// List agent reports, optionally filtered by agent/date.
export const listReports = (
  agent?: ReportAgent | string,
  tradeDate?: string,
  limit = 20,
) =>
  client
    .get<AgentReport[]>('/reports', {
      params: { agent, trade_date: tradeDate, limit },
    })
    .then((r) => r.data);

export const getReport = (id: number) =>
  client.get<AgentReport | null>(`/reports/${id}`).then((r) => r.data);

// Manually trigger an agent run (synchronous). target optional (e.g. a stock
// code for the recommend agent, a sector name for the sector agent).
export const generateReport = (agent: ReportAgent | string, target?: string) =>
  client
    .post<AgentReport>(`/reports/${agent}/generate`, null, {
      params: { target },
    })
    .then((r) => r.data);
