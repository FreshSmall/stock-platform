import client from './client';
import type {
  AdminUserRow,
  DataSourceRow,
  TaskRow,
} from './types';

// V1.5 Stage H — admin (管理) endpoints. All require a JWT (auto-injected by
// client.ts) AND an admin role enforced server-side via require_admin_user.

// ---- data sources ----
export const fetchDataSources = () =>
  client.get<DataSourceRow[]>('/admin/data-sources').then((r) => r.data);

export const toggleDataSource = (id: number, status: number) =>
  client
    .post<DataSourceRow>(`/admin/data-sources/${id}/status`, { status })
    .then((r) => r.data);

// ---- tasks ----
export const fetchTasks = () =>
  client.get<TaskRow[]>('/admin/tasks').then((r) => r.data);

export const runTask = (id: number) =>
  client.post<{ status: string; message?: string }>(`/admin/tasks/${id}/run`).then((r) => r.data);

export const toggleTask = (id: number, status: number) =>
  client.post<TaskRow>(`/admin/tasks/${id}/status`, { status }).then((r) => r.data);

// ---- users ----
export const fetchUsers = () =>
  client.get<AdminUserRow[]>('/admin/users').then((r) => r.data);

export const updateUserRole = (id: number, role: string) =>
  client.post<AdminUserRow>(`/admin/users/${id}/role`, { role }).then((r) => r.data);

export const setUserStatus = (id: number, status: number) =>
  client.post<AdminUserRow>(`/admin/users/${id}/status`, { status }).then((r) => r.data);
