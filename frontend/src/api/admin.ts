import client from './client';
import type {
  AdminUserRow,
  DataSourceRow,
  QualityCheckRow,
  TaskRow,
  TaskRunRow,
} from './types';

// Admin (管理) endpoints. All require a JWT (auto-injected by client.ts) AND
// an admin role enforced server-side via require_admin_user.
//
// V2.1: run returns either the finished log (sync tasks) or
// ``{async: true, run_id}`` for long tasks — poll getRun(runId) for progress.

// ---- data sources ----
export const fetchDataSources = () =>
  client.get<DataSourceRow[]>('/admin/datasources').then((r) => r.data);

export const testDatasource = (name: string) =>
  client
    .post<{ name: string; ok: boolean; detail: string }>(
      `/admin/datasources/${name}/test`,
    )
    .then((r) => r.data);

// ---- tasks ----
export const fetchTasks = () =>
  client.get<TaskRow[]>('/admin/tasks').then((r) => r.data);

export const runTask = (name: string) =>
  client
    .post<TaskRunRow | { async: true; run_id: number }>(`/admin/tasks/${name}/run`)
    .then((r) => r.data);

export const fetchTaskLogs = (name: string, limit = 20) =>
  client
    .get<TaskRunRow[]>(`/admin/tasks/${name}/logs`, { params: { limit } })
    .then((r) => r.data);

export const getRun = (runId: number) =>
  client.get<TaskRunRow>(`/admin/tasks/runs/${runId}`).then((r) => r.data);

export const fetchRunFailures = (runId: number) =>
  client
    .get<{ run_id: number; status: string; failures: { code: string }[] }>(
      `/admin/tasks/runs/${runId}/failures`,
    )
    .then((r) => r.data);

// ---- data quality (V2.1 BP-V2.1-007) ----
export const fetchQualityDaily = (date?: string) =>
  client
    .get<QualityCheckRow[]>('/admin/quality/daily', { params: { date } })
    .then((r) => r.data);

export const fetchQualityTrend = (days = 30) =>
  client
    .get<QualityCheckRow[]>('/admin/quality/trend', { params: { days } })
    .then((r) => r.data);

export const fetchQualityDetail = (
  date: string,
  check?: string,
  status?: string,
) =>
  client
    .get<{ date: string; rows: { check_name: string; code: string }[] }>(
      '/admin/quality/detail',
      { params: { date, check, status } },
    )
    .then((r) => r.data);

export const runQualityCheck = () =>
  client.post<TaskRunRow>('/admin/quality/check/run').then((r) => r.data);

// ---- users ----
export const fetchUsers = () =>
  client.get<AdminUserRow[]>('/admin/users').then((r) => r.data);

export const updateUser = (
  id: number,
  patch: { role?: string; status?: number },
) =>
  client
    .patch<AdminUserRow>(`/admin/users/${id}`, null, { params: patch })
    .then((r) => r.data);

// --- V2.2 T2.7 factor health ------------------------------------------------

export interface FactorHealthMetric {
  value: number | null;
  status: 'pass' | 'warn' | 'fail';
}

export interface FactorHealthFactor {
  factor_code: string;
  check_date: string;
  metrics: Record<string, FactorHealthMetric>;
  worst: 'pass' | 'warn' | 'fail';
}

export interface FactorHealthReport {
  as_of: string | null;
  factors: FactorHealthFactor[];
}

export const fetchFactorHealth = () =>
  client.get<FactorHealthReport>('/admin/factor-health').then((r) => r.data);

export const runFactorHealthCheck = () =>
  client
    .post<{
      checked: number;
      rows: number;
      failed: number;
      as_of: string;
      factors: {
        factor_code: string;
        icir: number | null;
        mean_ic_10: number | null;
        mean_ic_20: number | null;
        ic_decay: number | null;
        recent_ic: number | null;
        statuses: Record<string, 'pass' | 'warn' | 'fail'>;
      }[];
    }>('/admin/factor-health/run')
    .then((r) => r.data);
