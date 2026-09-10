import { useMemo, useState } from 'react';
import {
  Button,
  DatePicker,
  Drawer,
  Popconfirm,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchPipelineDaily,
  fetchPipelineSummary,
  runTask,
  type PipelineLongTask,
  type PipelineStepRow,
} from '../api/admin';
import EmptyState from './EmptyState';

// V2.5 BP-V2.5-003 — 管线面板：当日 run 的步骤时间线 + 失败步重跑 +
// 近 30 日状态色带 + 当日长任务聚合。步骤数据由 /admin/pipeline/daily 提供，
// 每步内嵌最近一次 task_log 记录（详情抽屉无需二次请求）。

const STEP_COLOR: Record<string, string> = {
  success: '#52c41a',
  running: '#1677ff',
  failed: '#cf1322',
  pending: '#bfbfbf',
  skipped: '#8c8c8c',
};

const RUN_COLOR: Record<string, string> = {
  success: '#52c41a',
  running: '#1677ff',
  partial: '#fa8c16',
  failed: '#cf1322',
  skipped: '#8c8c8c',
};

const STATUS_TEXT: Record<string, string> = {
  success: '成功',
  running: '运行中',
  failed: '失败',
  pending: '待运行',
  skipped: '跳过',
  partial: '部分失败',
};

function fmtTime(v: string | null): string {
  return v ? v.replace('T', ' ').slice(11, 19) : '--';
}

function fmtDur(ms: number | null): string {
  if (ms == null) return '--';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
}

function parseResult(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export default function PipelinePanel() {
  const qc = useQueryClient();
  const [date, setDate] = useState<Dayjs>(dayjs());
  const dateStr = date.format('YYYY-MM-DD');
  const [detail, setDetail] = useState<PipelineStepRow | null>(null);

  const dailyQ = useQuery({
    queryKey: ['admin', 'pipeline', 'daily', dateStr],
    queryFn: () => fetchPipelineDaily(dateStr),
    refetchInterval: 30_000,
  });
  const summaryQ = useQuery({
    queryKey: ['admin', 'pipeline', 'summary'],
    queryFn: () => fetchPipelineSummary(30),
    staleTime: 5 * 60 * 1000,
  });

  const rerunMut = useMutation({
    mutationFn: (name: string) => runTask(name),
    onSuccess: (r) => {
      message.success(
        'run_id' in r ? '长任务已提交，后台执行中' : `重跑完成：${r.status}`,
      );
      qc.invalidateQueries({ queryKey: ['admin', 'pipeline'] });
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '重跑失败'),
  });

  const run = dailyQ.data?.run ?? null;
  const steps = useMemo(
    () => [...(dailyQ.data?.steps ?? [])].sort((a, b) => a.seq - b.seq),
    [dailyQ.data],
  );
  const longTasks = dailyQ.data?.long_tasks ?? [];
  const ok = steps.filter((s) => s.status === 'success').length;
  const failed = steps.filter((s) => s.status === 'failed').length;

  const stepColumns: ColumnsType<PipelineStepRow> = [
    { title: '#', dataIndex: 'seq', width: 44 },
    {
      title: '步骤',
      dataIndex: 'title',
      render: (v: string, r) => (
        <Space size={6}>
          <span
            style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              borderRadius: 4,
              background: STEP_COLOR[r.status] ?? '#bfbfbf',
            }}
          />
          <span>{v || r.step_key}</span>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 92,
      render: (v: string) => (
        <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : v === 'running' ? 'blue' : 'default'}>
          {STATUS_TEXT[v] ?? v}
        </Tag>
      ),
    },
    { title: '次数', dataIndex: 'attempts', width: 64 },
    {
      title: '开始',
      dataIndex: 'started_at',
      width: 84,
      render: (v: string | null) => fmtTime(v),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 84,
      render: (v: number | null) => fmtDur(v),
    },
    {
      title: '操作',
      width: 130,
      render: (_v, r) => (
        <Space>
          <Button size="small" type="link" onClick={() => setDetail(r)}>
            详情
          </Button>
          {r.status === 'failed' && (
            <Popconfirm
              title="确认重跑该步骤？"
              onConfirm={() => rerunMut.mutate(r.step_key)}
            >
              <Button size="small" type="link" loading={rerunMut.isPending}>
                重跑
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const longColumns: ColumnsType<PipelineLongTask> = [
    {
      title: '任务',
      dataIndex: 'title',
      render: (v: string, r) => v || r.task_name,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 92,
      render: (v: string) => (
        <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'blue'}>
          {v === 'running' ? '运行中' : v}
        </Tag>
      ),
    },
    {
      title: '开始',
      dataIndex: 'started_at',
      render: (v: string | null) => (v ? v.replace('T', ' ').slice(5, 19) : '--'),
    },
    { title: '行数', dataIndex: 'rows_affected', width: 90, render: (v) => v ?? '--' },
  ];

  if (dailyQ.isLoading) return <Skeleton active paragraph={{ rows: 6 }} />;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <DatePicker
          value={date}
          onChange={(d) => d && setDate(d)}
          allowClear={false}
        />
        {run ? (
          <>
            <Tag color={run.status === 'success' ? 'green' : run.status === 'failed' ? 'red' : run.status === 'partial' ? 'orange' : 'blue'}>
              {run.run_date} · {run.pipeline_type === 'daily' ? '日管线' : '周末管线'} ·{' '}
              {STATUS_TEXT[run.status] ?? run.status}
            </Tag>
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              {fmtTime(run.started_at)} ~ {fmtTime(run.finished_at)}
            </span>
          </>
        ) : (
          <span style={{ fontSize: 12, color: '#8c8c8c' }}>
            该日无管线记录（非交易日 / 功能上线前）
          </span>
        )}
      </Space>

      {run && (
        <Space size="large" wrap>
          <Statistic title="步骤成功" value={`${ok}/${steps.length}`} />
          <Statistic
            title="失败"
            value={failed}
            valueStyle={failed > 0 ? { color: '#cf1322' } : undefined}
          />
          <Statistic
            title="总耗时"
            value={fmtDur(
              steps.reduce((acc, s) => acc + (s.duration_ms ?? 0), 0),
            )}
          />
        </Space>
      )}

      {steps.length === 0 ? (
        <EmptyState description="该日无步骤记录" />
      ) : (
        <Table<PipelineStepRow>
          rowKey="step_key"
          dataSource={steps}
          columns={stepColumns}
          pagination={false}
          size="small"
        />
      )}

      {longTasks.length > 0 && (
        <>
          <div style={{ fontWeight: 600 }}>当日长任务 / 非管线任务</div>
          <Table<PipelineLongTask>
            rowKey={(r) => `${r.task_name}-${r.started_at}`}
            dataSource={longTasks}
            columns={longColumns}
            pagination={false}
            size="small"
          />
        </>
      )}

      {(summaryQ.data?.length ?? 0) > 0 && (
        <>
          <div style={{ fontWeight: 600 }}>近 30 日</div>
          <Space size={4} wrap>
            {[...(summaryQ.data ?? [])]
              .sort((a, b) => (a.run_date < b.run_date ? -1 : 1))
              .map((s) => (
                <Tooltip
                  key={`${s.run_date}-${s.pipeline_type}`}
                  title={`${s.run_date} ${s.pipeline_type}：${STATUS_TEXT[s.status] ?? s.status}（${s.ok}/${s.total}${
                    s.failed ? `，失败 ${s.failed}` : ''
                  }）`}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: 18,
                      height: 18,
                      borderRadius: 3,
                      background: RUN_COLOR[s.status] ?? '#bfbfbf',
                      fontSize: 9,
                      color: '#fff',
                      textAlign: 'center',
                      lineHeight: '18px',
                      cursor: 'pointer',
                    }}
                    onClick={() => setDate(dayjs(s.run_date))}
                  >
                    {s.run_date.slice(8)}
                  </span>
                </Tooltip>
              ))}
          </Space>
        </>
      )}

      <Drawer
        title={`步骤详情 · ${detail?.title ?? ''}`}
        width={520}
        open={!!detail}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <div>
              <Tag color={detail.status === 'success' ? 'green' : detail.status === 'failed' ? 'red' : 'blue'}>
                {STATUS_TEXT[detail.status] ?? detail.status}
              </Tag>
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                尝试 {detail.attempts} 次 · 耗时 {fmtDur(detail.duration_ms)}
              </span>
            </div>
            {detail.error && (
              <pre style={{ color: '#cf1322', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                {detail.error}
              </pre>
            )}
            {detail.task_log ? (
              <>
                <div style={{ fontWeight: 600 }}>
                  任务日志 #{detail.task_log.id}
                </div>
                <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                  触发：{detail.task_log.triggered_by ?? '--'} · 行数：
                  {detail.task_log.rows_affected ?? '--'}
                  {detail.task_log.progress_total != null &&
                    ` · 进度 ${detail.task_log.progress_done ?? 0}/${detail.task_log.progress_total}`}
                </div>
                {detail.task_log.error && (
                  <pre style={{ color: '#cf1322', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                    {detail.task_log.error}
                  </pre>
                )}
                {detail.task_log.result != null && (
                  <pre style={{ fontSize: 12, maxHeight: 320, overflow: 'auto' }}>
                    {typeof detail.task_log.result === 'string'
                      ? (parseResult(detail.task_log.result) as string)
                      : JSON.stringify(detail.task_log.result, null, 2)}
                  </pre>
                )}
              </>
            ) : (
              <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                无关联任务日志（该步骤可能由旧版本执行）
              </div>
            )}
          </Space>
        )}
      </Drawer>
    </Space>
  );
}
