import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Empty } from 'antd';
import type { DrawdownPoint } from '../api/backtest';

// Drawdown curve (回撤曲线): line with red fill below zero.
//
// drawdown values are positive percentages representing the drop from peak,
// so we negate them to plot below the zero line (conventional drawdown look).
type Props = { data?: DrawdownPoint[] | null; loading?: boolean };

export default function DrawdownChart({ data, loading }: Props) {
  const option = useMemo(() => buildOption(data), [data]);

  if (loading && !data) {
    return (
      <Card size="small" title="回撤曲线" loading>
        <div style={{ height: 240 }} />
      </Card>
    );
  }
  if (!data || data.length === 0) {
    return (
      <Card size="small" title="回撤曲线">
        <Empty description="暂无回撤数据" style={{ padding: 32 }} />
      </Card>
    );
  }
  return (
    <Card size="small" title="回撤曲线">
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 240 }} />
    </Card>
  );
}

function buildOption(data: DrawdownPoint[] | null | undefined) {
  if (!data || data.length === 0) return {};
  type TooltipParam = { axisValue: string; value: number };
  const dates = data.map((p) => p.date);
  const values = data.map((p) => -Math.abs(p.drawdown)); // plot below zero
  return {
    animation: false,
    grid: { left: 56, right: 16, top: 16, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: TooltipParam[]) => {
        if (!ps.length) return '';
        return `${ps[0].axisValue}<br/>回撤 ${Number(ps[0].value).toFixed(2)}%`;
      },
    },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'value',
      name: '%',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: values,
        lineStyle: { width: 1.5, color: '#f5222d' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(245,34,45,0.02)' },
              { offset: 1, color: 'rgba(245,34,45,0.35)' },
            ],
          },
        },
      },
    ],
  };
}
