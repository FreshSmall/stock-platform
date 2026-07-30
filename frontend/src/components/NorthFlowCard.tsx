import { Card, Skeleton, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { NorthFlowRow } from '../api/types';
import { UP_COLOR, DOWN_COLOR, fmtMoney } from '../utils/format';
import EmptyState from './EmptyState';

const { Text } = Typography;

// Northbound (北向资金) flow card (V1.5).
//
// Stacked columns (sh_net + sz_net) per day, with a line for the combined net.
// Values arrive in 元 from the backend; we convert to 亿 for display.

type Props = { rows?: NorthFlowRow[]; loading: boolean };

const YI = 1e8;

export default function NorthFlowCard({ rows, loading }: Props) {
  if (loading && !rows) {
    return (
      <Card title="北向资金">
        <Skeleton active paragraph={{ rows: 4 }} />
      </Card>
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <Card title="北向资金">
        <EmptyState description="暂无北向资金数据" />
      </Card>
    );
  }

  const dates = rows.map((r) => r.trade_date);
  const sh = rows.map((r) => toYi(r.sh_net));
  const sz = rows.map((r) => toYi(r.sz_net));
  const total = rows.map((r) => toYi(sum(r.sh_net, r.sz_net)));

  const option = {
    animation: false,
    legend: { top: 0, data: ['沪股通', '深股通', '合计净流入'], textStyle: { fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v: number) => `${Number(v).toFixed(2)}亿`,
    },
    grid: { left: 48, right: 48, top: 32, bottom: 24 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: [
      { type: 'value', name: '亿', splitLine: { lineStyle: { color: '#f0f0f0' } } },
      { type: 'value', name: '合计', splitLine: { show: false } },
    ],
    series: [
      {
        name: '沪股通',
        type: 'bar',
        stack: 'north',
        data: sh,
        itemStyle: { color: UP_COLOR },
      },
      {
        name: '深股通',
        type: 'bar',
        stack: 'north',
        data: sz,
        itemStyle: { color: '#fa8c16' },
      },
      {
        name: '合计净流入',
        type: 'line',
        yAxisIndex: 1,
        data: total,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { width: 2, color: '#1677ff' },
        itemStyle: { color: '#1677ff' },
      },
    ],
  };

  // Latest-day summary line.
  const last = rows[rows.length - 1];
  const lastTotal = sum(last.sh_net, last.sz_net);

  return (
    <Card
      title={
        <span>
          北向资金
          {lastTotal != null && (
            <Text
              style={{
                marginLeft: 12,
                fontSize: 14,
                color: colorForNet(lastTotal),
                fontWeight: 600,
              }}
            >
              {lastTotal >= 0 ? '+' : ''}
              {fmtMoney(lastTotal)}
            </Text>
          )}
        </span>
      }
    >
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 240 }} />
    </Card>
  );
}

function toYi(v: number | null): number | string {
  if (v == null) return '-';
  return Number((v / YI).toFixed(2));
}

function sum(a: number | null, b: number | null): number | null {
  if (a == null && b == null) return null;
  return (a ?? 0) + (b ?? 0);
}

function colorForNet(v: number | null): string {
  if (v == null) return '#8c8c8c';
  return v >= 0 ? UP_COLOR : DOWN_COLOR;
}
