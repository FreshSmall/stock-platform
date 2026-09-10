import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Space, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  fetchFactorHealth,
  getRun,
  runFactorHealthCheck,
} from '../api/admin';
import type {
  FactorHealthFactor,
  FactorHealthMetric,
} from '../api/admin';
import EmptyState from './EmptyState';

// V2.2 BP-V2.2-007 — 因子健康度面板：预设因子的 ICIR / IC 衰减 / 近季 IC
// 红绿灯。数据来自每周六 09:30 的 factor_health_check 巡检（复用质量检查表），
// 此处只读展示 + 手动重跑。
const METRIC_LABELS: Record<string, string> = {
  ic_ir: 'ICIR(|值|≥阈值)',
  ic_decay: 'IC衰减(h10→h20)',
  recent_ic: '近季IC(±带)',
};

const STATUS_COLOR: Record<string, string> = {
  pass: 'green',
  warn: 'orange',
  fail: 'red',
};

const METRIC_HINTS: Record<string, string> = {
  ic_ir: '近一年 IC 信息比（绝对值），低于阈值 = 因子近乎噪声',
  ic_decay: 'h=20 与 h=10 均值IC之差，偏离 0 过多 = 持有期结构变化',
  recent_ic: '最近一季度均值IC，显著偏离 0 = 市场状态切换警报',
};

export default function FactorHealthPanel() {
  const qc = useQueryClient();
  const [patrolling, setPatrolling] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const q = useQuery({
    queryKey: ['factor-health'],
    queryFn: fetchFactorHealth,
  });

  // 巡检 ≈ 1 分钟（5 因子 × IC 序列），走长任务链路：提交 → 轮询 run 状态
  // → 完成后刷新报告。轮询挂在组件 ref 上，卸载时清理。
  const pollRun = (runId: number) => {
    pollTimer.current = setTimeout(async () => {
      try {
        const run = await getRun(runId);
        if (run.status === 'running') {
          pollRun(runId);
          return;
        }
        if (run.status === 'success') {
          message.success('因子健康度巡检完成');
          qc.invalidateQueries({ queryKey: ['factor-health'] });
        } else {
          message.error(`巡检失败：${run.error ?? '未知错误'}`);
        }
      } catch {
        message.error('巡检状态查询失败');
      } finally {
        setPatrolling(false);
      }
    }, 5000);
  };

  const runMut = useMutation({
    mutationFn: runFactorHealthCheck,
    onSuccess: (data) => {
      if (data && typeof data === 'object' && 'run_id' in data) {
        setPatrolling(true);
        message.info('巡检已提交，后台执行中（约 1 分钟）…');
        pollRun(data.run_id);
      } else {
        // 同步兜底（理论上不会走到）
        message.success('因子健康度巡检完成');
        qc.invalidateQueries({ queryKey: ['factor-health'] });
      }
    },
    onError: (e: unknown) =>
      message.error(e instanceof Error ? e.message : '巡检提交失败'),
  });

  const columns: ColumnsType<FactorHealthFactor> = [
    {
      title: '因子',
      dataIndex: 'factor_code',
      width: 100,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '综合',
      dataIndex: 'worst',
      width: 80,
      render: (v: string) => <Tag color={STATUS_COLOR[v]}>{v.toUpperCase()}</Tag>,
    },
    ...Object.keys(METRIC_LABELS).map((m) => ({
      title: (
        <Tooltip title={METRIC_HINTS[m]}>{METRIC_LABELS[m]}</Tooltip>
      ),
      key: m,
      align: 'right' as const,
      render: (_: unknown, record: FactorHealthFactor) => {
        const metric: FactorHealthMetric | undefined = record.metrics[m];
        if (!metric) return '--';
        return (
          <Tooltip title={`${METRIC_LABELS[m]}：${metric.status}`}>
            <Tag color={STATUS_COLOR[metric.status]} style={{ marginInlineEnd: 0 }}>
              {metric.value == null ? '--' : metric.value.toFixed(4)}
            </Tag>
          </Tooltip>
        );
      },
    })),
    { title: '检查日', dataIndex: 'check_date', width: 110 },
  ];

  return (
    <Card
      size="small"
      title="因子健康度（v2_reversal 预设因子）"
      extra={
        <Space>
          <Button
            size="small"
            loading={runMut.isPending || patrolling}
            onClick={() => runMut.mutate()}
          >
            立即巡检
          </Button>
        </Space>
      }
    >
      {q.isLoading ? (
        <Table loading columns={columns} dataSource={[]} rowKey="factor_code" />
      ) : !q.data || q.data.factors.length === 0 ? (
        <EmptyState description="尚无巡检结果，点击「立即巡检」生成首份健康度报告（约 1 分钟）" />
      ) : (
        <Table<FactorHealthFactor>
          rowKey="factor_code"
          size="small"
          pagination={false}
          columns={columns}
          dataSource={q.data.factors}
          footer={() => (
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>
              巡检日：{q.data!.as_of} ｜ 每周六 09:30 自动运行 ｜ 阈值可在
              sa_data_quality_rule（check_name=factor_health）调整
            </span>
          )}
        />
      )}
    </Card>
  );
}
