import { useMemo, useState } from 'react';
import {
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { useQuery } from '@tanstack/react-query';
import {
  fetchQualityDaily,
  fetchQualityDetail,
  fetchQualityTrend,
  runQualityCheck,
} from '../api/admin';
import type { QualityCheckRow } from '../api/types';
import EmptyState from './EmptyState';

// V2.1 BP-V2.1-007 — 数据质量日报面板：当日红绿灯 + 30 日趋势 + 异常明细。
// detail 字段后端可能是 JSON 字符串或已解析对象，这里统一解析。
function parseDetail(
  detail: QualityCheckRow['detail'],
): Record<string, unknown> {
  if (!detail) return {};
  if (typeof detail === 'string') {
    try {
      return JSON.parse(detail) as Record<string, unknown>;
    } catch {
      return { raw: detail };
    }
  }
  return detail;
}

const STATUS_COLOR: Record<string, string> = {
  pass: 'green',
  warn: 'orange',
  fail: 'red',
};

const CHECK_LABEL: Record<string, string> = {
  adjustment_break: '复权一致性',
  frozen: '价格冻结',
  row_baseline: '行数基线',
  field_missing: '字段缺失',
  coverage: '标注覆盖',
  amplitude_anomaly: '振幅异常',
};

export default function QualityPanel() {
  const [date, setDate] = useState<Dayjs>(dayjs());
  const dateStr = date.format('YYYY-MM-DD');
  const [detailDrawer, setDetailDrawer] = useState<QualityCheckRow | null>(null);

  const dailyQ = useQuery({
    queryKey: ['admin', 'quality', 'daily', dateStr],
    queryFn: () => fetchQualityDaily(dateStr),
  });
  const trendQ = useQuery({
    queryKey: ['admin', 'quality', 'trend'],
    queryFn: () => fetchQualityTrend(30),
    staleTime: 5 * 60 * 1000,
  });

  const rows = dailyQ.data ?? [];
  const counts = useMemo(() => {
    const c = { pass: 0, warn: 0, fail: 0 };
    rows.forEach((r) => {
      if (r.status in c) c[r.status as keyof typeof c] += 1;
    });
    return c;
  }, [rows]);

  // 趋势：每个 check/metric 取 30 日序列（倒序 → 升序），迷你文本趋势。
  const trendByMetric = useMemo(() => {
    const map = new Map<string, { date: string; value: number | null; status: string }[]>();
    (trendQ.data ?? []).forEach((r) => {
      const key = `${r.check_name}/${r.metric_name}`;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push({
        date: r.check_date,
        value: r.metric_value,
        status: r.status,
      });
    });
    map.forEach((list) => list.sort((a, b) => (a.date < b.date ? -1 : 1)));
    return map;
  }, [trendQ.data]);

  const runMut = {
    mutate: async () => {
      await runQualityCheck();
      message.success('巡检已触发，稍后刷新查看结果');
      setTimeout(() => dailyQ.refetch(), 3000);
    },
  };

  const columns: ColumnsType<QualityCheckRow> = [
    {
      title: '检查项',
      dataIndex: 'check_name',
      render: (v: string) => CHECK_LABEL[v] ?? v,
    },
    { title: '指标', dataIndex: 'metric_name' },
    {
      title: '数值',
      dataIndex: 'metric_value',
      width: 110,
      render: (v: number | null) => (v === null ? '--' : v.toFixed(4)),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (
        <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag>
      ),
    },
    {
      title: '近 30 日',
      width: 200,
      render: (_v, r) => {
        const list = trendByMetric.get(`${r.check_name}/${r.metric_name}`) ?? [];
        const latest = list.slice(-10);
        if (latest.length === 0) return '--';
        return (
          <Tooltip
            title={latest
              .map((p) => `${p.date}: ${p.value === null ? '--' : p.value.toFixed(2)} (${p.status})`)
              .join('\n')}
          >
            <Space size={2}>
              {latest.map((p) => (
                <span
                  key={p.date}
                  style={{
                    display: 'inline-block',
                    width: 14,
                    height: 14,
                    borderRadius: 3,
                    background:
                      p.status === 'fail'
                        ? '#ff4d4f'
                        : p.status === 'warn'
                          ? '#faad14'
                          : '#52c41a',
                  }}
                />
              ))}
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      width: 90,
      render: (_v, r) =>
        r.status !== 'pass' ? (
          <Button size="small" type="link" onClick={() => setDetailDrawer(r)}>
            明细
          </Button>
        ) : null,
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        <DatePicker value={date} onChange={(v) => v && setDate(v)} allowClear={false} />
        <Button onClick={() => runMut.mutate()}>手动巡检</Button>
        {rows.length > 0 && rows[0].check_date !== dateStr && (
          <Tag color="blue">{rows[0].check_date} 的最新报告</Tag>
        )}
        <Tag color="green">通过 {counts.pass}</Tag>
        <Tag color="orange">警告 {counts.warn}</Tag>
        <Tag color="red">失败 {counts.fail}</Tag>
      </Space>

      {dailyQ.isLoading ? (
        <Card loading style={{ minHeight: 160 }} />
      ) : rows.length === 0 ? (
        <EmptyState description={`${dateStr} 暂无巡检结果（可点"手动巡检"生成）`} />
      ) : (
        <Table<QualityCheckRow>
          rowKey={(r) => `${r.check_name}/${r.metric_name}`}
          dataSource={rows}
          columns={columns}
          pagination={false}
          size="small"
        />
      )}

      <Drawer
        title={
          detailDrawer
            ? `${CHECK_LABEL[detailDrawer.check_name] ?? detailDrawer.check_name} · 异常明细（${detailDrawer.check_date}）`
            : ''
        }
        width={520}
        open={!!detailDrawer}
        onClose={() => setDetailDrawer(null)}
      >
        {detailDrawer && (
          <DetailList
            date={detailDrawer.check_date}
            check={detailDrawer.check_name}
            rawDetail={parseDetail(detailDrawer.detail)}
          />
        )}
      </Drawer>
    </Space>
  );
}

function DetailList({
  date,
  check,
  rawDetail,
}: {
  date: string;
  check: string;
  rawDetail: Record<string, unknown>;
}) {
  const q = useQuery({
    queryKey: ['admin', 'quality', 'detail', date, check],
    queryFn: () => fetchQualityDetail(date, check),
    enabled: !!date && !!check,
  });
  const offenders = q.data?.rows ?? [];

  const summary = Object.entries(rawDetail).filter(([k]) => k !== 'offenders');

  return (
    <>
      {summary.length > 0 && (
        <Descriptions size="small" column={1} bordered style={{ marginBottom: 12 }}>
          {summary.map(([k, v]) => (
            <Descriptions.Item key={k} label={k}>
              {typeof v === 'object' ? JSON.stringify(v) : String(v)}
            </Descriptions.Item>
          ))}
        </Descriptions>
      )}
      {offenders.length === 0 ? (
        <EmptyState description="无股票级明细" />
      ) : (
        <Table
          rowKey={(r, i) => `${r.code}-${i}`}
          dataSource={offenders}
          columns={[
            { title: '股票', dataIndex: 'code' },
            { title: '检查', dataIndex: 'check_name' },
          ]}
          pagination={{ pageSize: 20, size: 'small' }}
          size="small"
        />
      )}
      <Row>
        <Col span={24}>
          <Button
            size="small"
            onClick={() => {
              const csv =
                'code\n' + offenders.map((o) => o.code).join('\n');
              const blob = new Blob([csv], { type: 'text/csv' });
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `quality_${check}_${date}.csv`;
              a.click();
            }}
          >
            导出 CSV
          </Button>
        </Col>
      </Row>
    </>
  );
}
