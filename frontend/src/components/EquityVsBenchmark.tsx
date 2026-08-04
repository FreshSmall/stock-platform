import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Empty } from 'antd';

// Equity vs benchmark comparison (净值对比): strategy vs benchmark double line.
type EquityPoint = { date: string; equity: number };

type Props = {
  equity?: EquityPoint[];
  benchmark?: EquityPoint[];
  loading?: boolean;
  benchmarkName?: string;
};

export default function EquityVsBenchmark({
  equity,
  benchmark,
  loading,
  benchmarkName = '基准',
}: Props) {
  const option = useMemo(
    () => buildOption(equity, benchmark, benchmarkName),
    [equity, benchmark, benchmarkName],
  );

  if (loading && !equity) {
    return (
      <Card size="small" title="净值对比" loading>
        <div style={{ height: 280 }} />
      </Card>
    );
  }
  if (!equity || equity.length === 0) {
    return (
      <Card size="small" title="净值对比">
        <Empty description="暂无净值数据" style={{ padding: 32 }} />
      </Card>
    );
  }
  return (
    <Card size="small" title="净值对比">
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 280 }} />
    </Card>
  );
}

function buildOption(
  equity: EquityPoint[] | undefined,
  benchmark: EquityPoint[] | undefined,
  benchmarkName: string,
) {
  if (!equity || equity.length === 0) return {};
  const eqDates = equity.map((p) => p.date);
  const eqVals = equity.map((p) => p.equity);
  // Benchmark dates may differ in length; align to the strategy axis when
  // available, otherwise use its own.
  const bmDates = benchmark ? benchmark.map((p) => p.date) : [];
  const xData = bmDates.length > eqDates.length ? bmDates : eqDates;
  const bmVals = benchmark ? benchmark.map((p) => p.equity) : [];
  return {
    animation: false,
    legend: {
      top: 0,
      data: ['策略', benchmarkName],
      textStyle: { fontSize: 11 },
    },
    grid: { left: 56, right: 16, top: 32, bottom: 32 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: xData, axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        name: '策略',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: eqVals,
        lineStyle: { width: 2, color: '#1677ff' },
        areaStyle: { opacity: 0.06 },
      },
      {
        name: benchmarkName,
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: bmVals,
        lineStyle: { width: 1.5, color: '#8c8c8c', type: 'dashed' },
      },
    ],
  };
}
