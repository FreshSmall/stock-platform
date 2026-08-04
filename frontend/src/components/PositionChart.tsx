import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Empty } from 'antd';
import type { PositionPoint } from '../api/backtest';

// Position change chart (持仓变化): bar chart of held size per bar.
type Props = { data?: PositionPoint[] | null; loading?: boolean };

export default function PositionChart({ data, loading }: Props) {
  const option = useMemo(() => buildOption(data), [data]);

  if (loading && !data) {
    return (
      <Card size="small" title="持仓变化" loading>
        <div style={{ height: 220 }} />
      </Card>
    );
  }
  if (!data || data.length === 0) {
    return (
      <Card size="small" title="持仓变化">
        <Empty description="暂无持仓数据" style={{ padding: 32 }} />
      </Card>
    );
  }
  return (
    <Card size="small" title="持仓变化">
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 220 }} />
    </Card>
  );
}

function buildOption(data: PositionPoint[] | null | undefined) {
  if (!data || data.length === 0) return {};
  type TooltipParam = { axisValue: string; value: number };
  return {
    animation: false,
    grid: { left: 48, right: 16, top: 16, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: TooltipParam[]) => {
        if (!ps.length) return '';
        return `${ps[0].axisValue}<br/>持仓 ${Number(ps[0].value)}`;
      },
    },
    xAxis: {
      type: 'category',
      data: data.map((p) => p.date),
      axisLabel: { fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 8,
        data: data.map((p) => p.position),
        itemStyle: { color: '#1677ff' },
      },
    ],
  };
}
