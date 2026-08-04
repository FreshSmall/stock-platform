import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Empty } from 'antd';
import type { FactorIC } from '../api/types';
import { UP_COLOR, DOWN_COLOR } from '../utils/format';

// IC bar chart + cumulative IC line (ECharts).
//
// The backend returns a single-date IC snapshot (one value), so we render it
// as a headline gauge bar plus the layered-returns decomposition is handled by
// LayeredReturns. When multiple IC values are available (an ic history array),
// we plot them as bars with a cumulative line. Here the snapshot is single, so
// we show the IC value as a prominent bar and reuse layered_returns only for
// context via the layeredReturns component.
type Props = { ic?: FactorIC | null; loading?: boolean };

export default function ICChart({ ic, loading }: Props) {
  const option = useMemo(() => buildOption(ic), [ic]);

  if (loading && !ic) {
    return (
      <Card size="small" title="IC 分析" loading>
        <div style={{ height: 260 }} />
      </Card>
    );
  }
  if (!ic) {
    return (
      <Card size="small" title="IC 分析">
        <Empty description="暂无 IC 数据" style={{ padding: 32 }} />
      </Card>
    );
  }
  return (
    <Card
      size="small"
      title={`IC 分析（${ic.trade_date}，horizon=${ic.horizon}）`}
      extra={<span style={{ fontSize: 12, color: '#8c8c8c' }}>universe {ic.universe_size}</span>}
    >
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 260 }} />
    </Card>
  );
}

function buildOption(ic: FactorIC | null | undefined) {
  if (!ic) return {};
  const icVal = ic.ic;
  const winRate = ic.win_rate;
  const color = icVal == null ? '#8c8c8c' : icVal >= 0 ? UP_COLOR : DOWN_COLOR;
  return {
    animation: false,
    grid: { left: 56, right: 56, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: number) => (v == null ? '--' : Number(v).toFixed(4)),
    },
    xAxis: {
      type: 'category',
      data: ['IC', '胜率'],
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        type: 'bar',
        barWidth: 48,
        data: [
          { value: icVal, itemStyle: { color } },
          {
            value: winRate,
            itemStyle: { color: '#1677ff' },
          },
        ],
        label: {
          show: true,
          position: 'top',
          formatter: (p: { value: number | null }) =>
            p.value == null ? '--' : Number(p.value).toFixed(4),
          fontSize: 11,
        },
        markLine: {
          symbol: 'none',
          data: [{ yAxis: 0 }],
          lineStyle: { color: '#8c8c8c', type: 'dashed' },
        },
      },
    ],
  };
}
