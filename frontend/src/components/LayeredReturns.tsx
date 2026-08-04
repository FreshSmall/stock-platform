import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Empty } from 'antd';
import type { LayeredReturn } from '../api/types';
import { colorForChange, fmtPct } from '../utils/format';

// Layered returns bar chart (分层收益).
//
// Stocks are split into N quantile groups by factor value (layer 1 = lowest
// factor). A monotonically increasing mean return across layers indicates a
// useful factor. Bars use red-up/green-down coloring.
type Props = { layers?: LayeredReturn[] | null; loading?: boolean };

export default function LayeredReturns({ layers, loading }: Props) {
  const option = useMemo(() => buildOption(layers), [layers]);

  if (loading && !layers) {
    return (
      <Card size="small" title="分层收益" loading>
        <div style={{ height: 260 }} />
      </Card>
    );
  }
  if (!layers || layers.length === 0) {
    return (
      <Card size="small" title="分层收益">
        <Empty description="暂无分层收益数据" style={{ padding: 32 }} />
      </Card>
    );
  }
  return (
    <Card size="small" title="分层收益（按因子值分位）">
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 260 }} />
    </Card>
  );
}

function buildOption(layers: LayeredReturn[] | null | undefined) {
  if (!layers || layers.length === 0) return {};
  type TooltipParam = { dataIndex: number; value: number | null };
  const cats = layers.map((l) => `第${l.layer}层`);
  const values = layers.map((l) => (l.mean_return == null ? null : l.mean_return * 100));
  return {
    animation: false,
    grid: { left: 56, right: 16, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: TooltipParam[]) => {
        if (!ps.length) return '';
        const p = ps[0];
        const layer = layers[p.dataIndex];
        const ret = layer.mean_return == null ? null : layer.mean_return * 100;
        return `${cats[p.dataIndex]}<br/>收益 ${fmtPct(ret)}<br/>样本 ${layer.count}`;
      },
    },
    xAxis: { type: 'category', data: cats, axisLabel: { fontSize: 11 } },
    yAxis: {
      type: 'value',
      name: '%',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        type: 'bar',
        barWidth: '55%',
        data: values.map((v) => ({
          value: v,
          itemStyle: { color: colorForChange(v) },
        })),
        label: {
          show: true,
          position: 'top',
          formatter: (p: { value: number | null }) =>
            p.value == null ? '--' : `${Number(p.value).toFixed(2)}%`,
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
