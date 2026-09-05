import { useState } from 'react';
import {
  Button,
  Card,
  Col,
  Drawer,
  Popconfirm,
  Progress,
  Result,
  Row,
  Segmented,
  Skeleton,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchDataSources,
  fetchTaskLogs,
  fetchTasks,
  fetchUsers,
  getRun,
  runTask,
  testDatasource,
  updateUser,
} from '../api/admin';
import type {
  AdminUserRow,
  DataSourceRow,
  TaskRow,
  TaskRunRow,
} from '../api/types';
import { useAuthStore } from '../store/authStore';
import EmptyState from '../components/EmptyState';
import QualityPanel from '../components/QualityPanel';
import FactorHealthPanel from '../components/FactorHealthPanel';

type Tab = 'sources' | 'tasks' | 'quality' | 'health' | 'users';

// 管理后台. 仅 admin 可见 (路由层守卫之外再做一次确认).
// V2.1: 任务页对齐后端 name-based 契约，长任务（重灌/修复/回补）异步提交并
// 轮询进度；新增"数据质量"页签（BP-V2.1-007）。
export default function Admin() {
  const role = useAuthStore((s) => s.role);
  const [tab, setTab] = useState<Tab>('tasks');

  if (role !== 'admin') {
    return (
      <Result status="403" title="403" subTitle="抱歉，您没有权限访问该页面。" />
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
                { label: '任务', value: 'tasks' },
                { label: '数据质量', value: 'quality' },
                { label: '因子健康', value: 'health' },
                { label: '数据源', value: 'sources' },
                { label: '用户', value: 'users' },
              ]}
            />
          }
        >
          {tab === 'sources' && <DataSourcesTable />}
          {tab === 'tasks' && <TasksTable />}
          {tab === 'quality' && <QualityPanel />}
          {tab === 'health' && <FactorHealthPanel />}
          {tab === 'users' && <UsersTable />}
        </Card>
      </Col>
    </Row>
  );
}

// ---- data sources ----
function DataSourcesTable() {
  const { data, isLoading } = useQuery<DataSourceRow[]>({
    queryKey: ['admin', 'datasources'],
    queryFn: fetchDataSources,
  });

  const testMut = useMutation({
    mutationFn: (name: string) => testDatasource(name),
    onSuccess: (r) =>
      r.ok ? message.success(r.detail) : message.error(r.detail || '不可用'),
  });

  const columns: ColumnsType<DataSourceRow> = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'type', render: (v) => v ?? '--' },
    { title: '说明', dataIndex: 'note', render: (v) => v ?? '--' },
    {
      title: '操作',
      width: 140,
      render: (_v, r) => (
        <Button
          size="small"
          type="link"
          loading={testMut.isPending && testMut.variables === r.name}
          onClick={() => testMut.mutate(r.name)}
        >
          测试连通性
        </Button>
      ),
    },
  ];

  if (isLoading) return <Skeleton active paragraph={{ rows: 4 }} />;
  if (!data || data.length === 0) return <EmptyState description="暂无数据源" />;
  return (
    <Table rowKey="name" dataSource={data} columns={columns} pagination={false} size="small" />
  );
}

// ---- tasks ----
function TasksTable() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<TaskRow[]>({
    queryKey: ['admin', 'tasks'],
    queryFn: fetchTasks,
    refetchInterval: 30_000,
  });

  // 长任务：run 返回 {async: true, run_id} → 打开进度抽屉轮询。
  const [runDrawer, setRunDrawer] = useState<TaskRunRow | null>(null);
  const [logsTask, setLogsTask] = useState<string | null>(null);

  const { data: watchedRun } = useQuery({
    queryKey: ['admin', 'task-run', runDrawer?.id],
    queryFn: () => getRun(runDrawer!.id),
    enabled: !!runDrawer && runDrawer.status === 'running',
    refetchInterval: (q) =>
      q.state.data?.status === 'running' ? 3000 : false,
  });
  const current: TaskRunRow | null = watchedRun ?? runDrawer;

  const { data: logs } = useQuery({
    queryKey: ['admin', 'task-logs', logsTask],
    queryFn: () => fetchTaskLogs(logsTask!, 20),
    enabled: !!logsTask,
  });

  const runMut = useMutation({
    mutationFn: (name: string) => runTask(name),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['admin', 'tasks'] });
      if ('run_id' in r) {
        message.success('长任务已提交，后台执行中');
        setRunDrawer({ id: r.run_id, task_name: '', started_at: null, finished_at: null, status: 'running', rows_affected: null, progress_done: null, progress_total: null, result: null, error: null, triggered_by: null });
        // 立即拉一次真实状态
        getRun(r.run_id).then(setRunDrawer).catch(() => undefined);
      } else {
        message.success(
          `执行完成：${r.status}${r.rows_affected != null ? `（${r.rows_affected} 行）` : ''}`,
        );
      }
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '执行失败'),
  });

  const columns: ColumnsType<TaskRow> = [
    {
      title: '任务名称',
      dataIndex: 'title',
      render: (v: string, r) => (
        <Space size={6}>
          <span>{v || r.task_name}</span>
          {r.is_long && (
            <Tag color="geekblue" style={{ fontSize: 11 }}>
              长任务
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '任务',
      dataIndex: 'task_name',
      render: (v: string) => <span style={{ fontSize: 12, color: '#888' }}>{v}</span>,
    },
    {
      title: '最近执行',
      dataIndex: 'last_started_at',
      render: (v: string | null) => v?.replace('T', ' ').slice(0, 19) ?? '--',
    },
    {
      title: '结果',
      dataIndex: 'last_status',
      width: 100,
      render: (v: string | null) =>
        v ? (
          <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'blue'}>
            {v === 'running' ? '运行中' : v}
          </Tag>
        ) : (
          '--'
        ),
    },
    {
      title: '行数',
      dataIndex: 'last_rows',
      width: 90,
      render: (v: number | null) => v ?? '--',
    },
    {
      title: '操作',
      width: 170,
      render: (_v, r) => (
        <Space>
          <Popconfirm
            title="确认立即执行该任务？"
            onConfirm={() => runMut.mutate(r.task_name)}
          >
            <Button size="small" type="link" loading={runMut.isPending}>
              立即执行
            </Button>
          </Popconfirm>
          <Button size="small" type="link" onClick={() => setLogsTask(r.task_name)}>
            历史
          </Button>
        </Space>
      ),
    },
  ];

  if (isLoading) return <Skeleton active paragraph={{ rows: 4 }} />;
  if (!data || data.length === 0) return <EmptyState description="暂无任务" />;
  return (
    <>
      <Table<TaskRow>
        rowKey="task_name"
        dataSource={data}
        columns={columns}
        pagination={false}
        size="small"
      />
      <Drawer
        title={`任务进度${current?.task_name ? ` · ${current.task_name}` : ''}`}
        width={460}
        open={!!runDrawer}
        onClose={() => {
          setRunDrawer(null);
          qc.invalidateQueries({ queryKey: ['admin', 'tasks'] });
        }}
      >
        {current && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Tag
              color={
                current.status === 'success'
                  ? 'green'
                  : current.status === 'failed'
                    ? 'red'
                    : 'blue'
              }
            >
              {current.status}
            </Tag>
            {current.progress_total != null && (
              <Progress
                percent={Math.round(
                  ((current.progress_done ?? 0) / current.progress_total) * 100,
                )}
                size="small"
                format={() =>
                  `${current.progress_done ?? 0} / ${current.progress_total}`
                }
              />
            )}
            {current.rows_affected != null && <div>影响行数：{current.rows_affected}</div>}
            {current.error && <div style={{ color: '#cf1322' }}>{current.error}</div>}
            {current.result != null && (
              <pre style={{ fontSize: 12, maxHeight: 300, overflow: 'auto' }}>
                {typeof current.result === 'string'
                  ? current.result
                  : JSON.stringify(current.result, null, 2)}
              </pre>
            )}
          </Space>
        )}
      </Drawer>
      <Drawer
        title={`执行历史${logsTask ? ` · ${logsTask}` : ''}`}
        width={560}
        open={!!logsTask}
        onClose={() => setLogsTask(null)}
      >
        <Table<TaskRunRow>
          rowKey="id"
          dataSource={logs ?? []}
          size="small"
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '开始',
              dataIndex: 'started_at',
              render: (v: string | null) => v?.replace('T', ' ').slice(5, 19) ?? '--',
            },
            {
              title: '状态',
              dataIndex: 'status',
              render: (v: string) => (
                <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'blue'}>
                  {v}
                </Tag>
              ),
            },
            { title: '行数', dataIndex: 'rows_affected', render: (v) => v ?? '--' },
            {
              title: '进度',
              render: (_v, r) =>
                r.progress_total != null
                  ? `${r.progress_done ?? 0}/${r.progress_total}`
                  : '--',
            },
            {
              title: '触发',
              dataIndex: 'triggered_by',
              render: (v: string | null) => v ?? '--',
            },
          ]}
        />
      </Drawer>
    </>
  );
}

// ---- users ----
function UsersTable() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<AdminUserRow[]>({
    queryKey: ['admin', 'users'],
    queryFn: fetchUsers,
  });

  const updateMut = useMutation({
    mutationFn: (p: { id: number; patch: { role?: string; status?: number } }) =>
      updateUser(p.id, p.patch),
    onSuccess: () => {
      message.success('已更新');
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
      width: 110,
      render: (v: string) => (
        <Tag color={v === 'admin' ? 'purple' : 'default'}>
          {v === 'admin' ? '管理员' : '普通用户'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: (v: number) =>
        v === 1 ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>,
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      render: (v: string | null) => v?.replace('T', ' ').slice(0, 19) ?? '--',
    },
    {
      title: '操作',
      width: 200,
      render: (_v, r) => (
        <Space>
          <Popconfirm
            title="确认切换该用户角色？"
            onConfirm={() =>
              updateMut.mutate({
                id: r.id,
                patch: { role: r.role === 'admin' ? 'user' : 'admin' },
              })
            }
          >
            <Button size="small" type="link">
              {r.role === 'admin' ? '降为普通' : '升为管理员'}
            </Button>
          </Popconfirm>
          <Popconfirm
            title={r.status === 1 ? '确认禁用该用户？' : '确认启用该用户？'}
            onConfirm={() =>
              updateMut.mutate({ id: r.id, patch: { status: r.status === 1 ? 0 : 1 } })
            }
          >
            <Button size="small" type="link" danger={r.status === 1}>
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
