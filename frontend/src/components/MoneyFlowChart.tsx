import { Card, Skeleton } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { MoneyFlowDetailRow } from '../api/types';
import { UP_COLOR, DOWN_COLOR } from '../utils/format';
import EmptyState from './EmptyState';

// Money-flow detail (个股资金流向) chart (V1.5).
//
// Stacked columns of the four order tiers (super / big / medium / small net),
// plus a line for the daily net total. Positive/negative values use the
// A-share red-up/green-down convention. Values arrive in 元; we display 亿元.

type Props = { rows?: MoneyFlowDetailRow[]; loading: boolean };

const YI = 1e8;

const TIERS: { key: keyof Pick<MoneyFlowDetailRow, 'super_net' | 'big_net' | 'medium_net' | 'small_net'> ; name: string; color: string }[] = [
  { key: 'super_net', name: '超大单', color: '#f5222d' },
  { key: 'big_net', name: '大单', color: '#fa8c16' },
  { key: 'medium_net', name: '中单', color: '#13c2c2' },
  { key: 'small_net', name: '小单', color: '#8c8c8c' },
];

export default function MoneyFlowChart({ rows, loading }: Props) {
  if (loading && !rows) {
    return (
      <Card title="资金流向">
        <Skeleton active paragraph={{ rows: 5 }} />
      </Card>
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <Card title="资金流向">
        <EmptyState description="暂无资金流向数据" />
      </Card>
    );
  }

  const dates = rows.map((r) => r.trade_date);
  const series = TIERS.map((t) => ({
    name: t.name,
    type: 'bar',
    stack: 'flow',
    data: rows.map((r) => toYi(r[t.key])),
    itemStyle: { color: t.color },
    barMaxWidth: 24,
  }));
  // Daily net total as a line over the stacked bars.
  const total = rows.map((r) => toYi(netTotal(r)));

  const option = {
    animation: false,
    legend: { top: 0, data: [...TIERS.map((t) => t.name), '净流入'], textStyle: { fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v: number) => `${Number(v).toFixed(2)}亿`,
    },
    grid: { left: 48, right: 48, top: 32, bottom: 24 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: [
      { type: 'value', name: '亿', splitLine: { lineStyle: { color: '#f0f0f0' } } },
      { type: 'value', name: '净流入', splitLine: { show: false } },
    ],
    series: [
      ...series,
      {
        name: '净流入',
        type: 'line',
        yAxisIndex: 1,
        data: total,
        smooth: true,
        symbol: 'none',
        lineStyle: {
          width: 2,
          color: UP_COLOR,
        },
        itemStyle: { color: UP_COLOR },
        markLine: {
          symbol: 'none',
          data: [{ yAxis: 0 }],
          lineStyle: { color: DOWN_COLOR, type: 'dashed' },
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(245,34,45,0.15)' },
              { offset: 1, color: 'rgba(82,196,26,0.15)' },
            ],
          },
        },
      },
    ],
  };

  return (
    <Card title="资金流向">
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 280 }} />
    </Card>
  );
}

function toYi(v: number | null): number | string {
  if (v == null) return '-';
  return Number((v / YI).toFixed(2));
}

function netTotal(r: MoneyFlowDetailRow): number {
  return (
    (r.super_net ?? 0) +
    (r.big_net ?? 0) +
    (r.medium_net ?? 0) +
    (r.small_net ?? 0)
  );
}
