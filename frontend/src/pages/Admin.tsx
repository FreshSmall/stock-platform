import { useState } from 'react';
import {
  Button,
  Card,
  Col,
  Popconfirm,
  Result,
  Row,
  Segmented,
  Skeleton,
  Space,
  Switch,
  Table,
  Tag,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchDataSources,
  fetchTasks,
  fetchUsers,
  runTask,
  toggleDataSource,
  toggleTask,
  updateUserRole,
  setUserStatus,
} from '../api/admin';
import type { AdminUserRow, DataSourceRow, TaskRow } from '../api/types';
import { useAuthStore } from '../store/authStore';
import EmptyState from '../components/EmptyState';

type Tab = 'sources' | 'tasks' | 'users';

// H-stage — 管理后台. 仅 admin 可见 (路由层守卫之外再做一次确认).
export default function Admin() {
  const role = useAuthStore((s) => s.role);
  const [tab, setTab] = useState<Tab>('tasks');

  if (role !== 'admin') {
    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，您没有权限访问该页面。"
      />
    );
  }

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card
          title="管理后台"
          extra={
            <Segmented
              value={tab}
              onChange={(v) => setTab(v as Tab)}
              options={[
                { label: '数据源', value: 'sources' },
                { label: '任务', value: 'tasks' },
                { label: '用户', value: 'users' },
              ]}
            />
          }
        >
          {tab === 'sources' && <DataSourcesTable />}
          {tab === 'tasks' && <TasksTable />}
          {tab === 'users' && <UsersTable />}
        </Card>
      </Col>
    </Row>
  );
}

// ---- data sources ----
function DataSourcesTable() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<DataSourceRow[]>({
    queryKey: ['admin', 'data-sources'],
    queryFn: fetchDataSources,
  });

  const toggleMut = useMutation({
    mutationFn: (p: { id: number; status: number }) =>
      toggleDataSource(p.id, p.status),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['admin', 'data-sources'] });
    },
    onError: (e) =>
      message.error(e instanceof Error ? e.message : '操作失败'),
  });

  const columns: ColumnsType<DataSourceRow> = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'type', render: (v) => v ?? '--' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: number | null, r) => (
        <Switch
          size="small"
          checked={v === 1}
          loading={toggleMut.isPending}
          onChange={(checked) =>
            toggleMut.mutate({ id: r.id, status: checked ? 1 : 0 })
          }
        />
      ),
    },
    {
      title: '最近同步',
      dataIndex: 'last_sync_at',
      render: (v: string | null) => v ?? '--',
    },
    {
      title: '结果',
      dataIndex: 'last_status',
      render: (v: string | null, r) =>
        v ? (
          <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'default'}>
            {v}
            {r.last_message ? `: ${r.last_message}` : ''}
          </Tag>
        ) : (
          '--'
        ),
    },
  ];

  if (isLoading) return <Skeleton active paragraph={{ rows: 4 }} />;
  if (!data || data.length === 0) return <EmptyState description="暂无数据源" />;
  return (
    <Table
      rowKey="id"
      dataSource={data}
      columns={columns}
      pagination={false}
      size="small"
    />
  );
}

// ---- tasks ----
function TasksTable() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<TaskRow[]>({
    queryKey: ['admin', 'tasks'],
    queryFn: fetchTasks,
  });

  const runMut = useMutation({
    mutationFn: (id: number) => runTask(id),
    onSuccess: (r) => {
      message.success(`任务已触发${r?.message ? `: ${r.message}` : ''}`);
      qc.invalidateQueries({ queryKey: ['admin', 'tasks'] });
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '执行失败'),
  });

  const toggleMut = useMutation({
    mutationFn: (p: { id: number; status: number }) => toggleTask(p.id, p.status),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['admin', 'tasks'] });
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '操作失败'),
  });

  const columns: ColumnsType<TaskRow> = [
    { title: '任务', dataIndex: 'name' },
    { title: 'Cron', dataIndex: 'cron', render: (v) => v ?? '--' },
    {
      title: '启用',
      dataIndex: 'status',
      width: 80,
      render: (v: number | null, r) => (
        <Switch
          size="small"
          checked={v === 1}
          loading={toggleMut.isPending}
          onChange={(checked) =>
            toggleMut.mutate({ id: r.id, status: checked ? 1 : 0 })
          }
        />
      ),
    },
    {
      title: '最近执行',
      dataIndex: 'last_run_at',
      render: (v: string | null) => v ?? '--',
    },
    {
      title: '结果',
      dataIndex: 'last_status',
      render: (v: string | null) =>
        v ? (
          <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'default'}>
            {v}
          </Tag>
        ) : (
          '--'
        ),
    },
    {
      title: '操作',
      width: 110,
      render: (_v, r) => (
        <Popconfirm
          title="确认立即执行该任务？"
          onConfirm={() => runMut.mutate(r.id)}
        >
          <Button size="small" type="link" loading={runMut.isPending}>
            立即执行
          </Button>
        </Popconfirm>
      ),
    },
  ];

  if (isLoading) return <Skeleton active paragraph={{ rows: 4 }} />;
  if (!data || data.length === 0) return <EmptyState description="暂无任务" />;
  return (
    <Table<TaskRow>
      rowKey="id"
      dataSource={data}
      columns={columns}
      pagination={false}
      size="small"
    />
  );
}

// ---- users ----
function UsersTable() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<AdminUserRow[]>({
    queryKey: ['admin', 'users'],
    queryFn: fetchUsers,
  });

  const roleMut = useMutation({
    mutationFn: (p: { id: number; role: string }) => updateUserRole(p.id, p.role),
    onSuccess: () => {
      message.success('角色已更新');
      qc.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '操作失败'),
  });

  const statusMut = useMutation({
    mutationFn: (p: { id: number; status: number }) => setUserStatus(p.id, p.status),
    onSuccess: () => {
      message.success('状态已更新');
      qc.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '操作失败'),
  });

  const columns: ColumnsType<AdminUserRow> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username' },
    {
      title: '角色',
      dataIndex: 'role',
      width: 140,
      render: (v: string) => (
        <Space>
          <Tag color={v === 'admin' ? 'purple' : 'default'}>
            {v === 'admin' ? '管理员' : '普通用户'}
          </Tag>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: number) =>
        v === 1 ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>,
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      render: (v: string | null) => v ?? '--',
    },
    {
      title: '操作',
      width: 200,
      render: (_v, r) => (
        <Space>
          <Popconfirm
            title="确认切换该用户角色？"
            onConfirm={() =>
              roleMut.mutate({ id: r.id, role: r.role === 'admin' ? 'user' : 'admin' })
            }
          >
            <Button size="small" type="link" loading={roleMut.isPending}>
              {r.role === 'admin' ? '降为普通' : '升为管理员'}
            </Button>
          </Popconfirm>
          <Popconfirm
            title={r.status === 1 ? '确认禁用该用户？' : '确认启用该用户？'}
            onConfirm={() =>
              statusMut.mutate({ id: r.id, status: r.status === 1 ? 0 : 1 })
            }
          >
            <Button
              size="small"
              type="link"
              danger={r.status === 1}
              loading={statusMut.isPending}
            >
              {r.status === 1 ? '禁用' : '启用'}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (isLoading) return <Skeleton active paragraph={{ rows: 4 }} />;
  if (!data || data.length === 0) return <EmptyState description="暂无用户" />;
  return (
    <Table<AdminUserRow>
      rowKey="id"
      dataSource={data}
      columns={columns}
      pagination={false}
      size="small"
    />
  );
}
