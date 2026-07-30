import { Card, Descriptions, Skeleton, Tag } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { ChipDistribution as Chip } from '../api/types';
import { UP_COLOR, DOWN_COLOR, FLAT_COLOR, fmtPrice } from '../utils/format';
import EmptyState from './EmptyState';

// Chip-distribution (筹码峰) component (V1.5 BP-V1.5-007).
//
// Renders the price/weight histogram as a horizontal bar chart plus a small
// summary of the cost concentration (profit ratio, avg cost, 70%/90%
// concentration ranges). When `data` is null we show the empty state.

type Props = { data?: Chip | null; loading: boolean };

export default function ChipDistribution({ data, loading }: Props) {
  if (loading && !data) {
    return (
      <Card title="筹码分布">
        <Skeleton active paragraph={{ rows: 4 }} />
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="筹码分布">
        <EmptyState description="暂无筹码数据" />
      </Card>
    );
  }

  const dist = data.distribution ?? [];
  // distribution is [price, weight]; ECharts horizontal bar wants [value, name].
  const prices = dist.map((d) => d[0]);
  const weights = dist.map((d) => d[1]);
  const profit = data.profit_ratio;

  const option = {
    animation: false,
    grid: { left: 56, right: 24, top: 16, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any[]) =>
        params.length
          ? `价格 ${fmtPrice(Number(params[0].name))}<br/>权重 ${Number(params[0].value).toFixed(2)}%`
          : '',
    },
    xAxis: {
      type: 'value',
      name: '权重%',
      splitLine: { lineStyle: { color: '#f0f0f0' } },
    },
    yAxis: {
      type: 'category',
      data: prices.map((p) => String(p)),
      axisLabel: {
        fontSize: 10,
        formatter: (v: string) => fmtPrice(Number(v)),
      },
      inverse: true, // low prices at the bottom
    },
    series: [
      {
        type: 'bar',
        data: weights.map((w, i) => ({
          value: w,
          // Highlight the bar nearest the average cost.
          itemStyle: {
            color: nearAvg(prices[i], data.avg_cost) ? UP_COLOR : '#1677ff',
          },
        })),
        barMaxWidth: 14,
      },
    ],
  };

  return (
    <Card title="筹码分布">
      <Descriptions column={2} size="small" style={{ marginBottom: 12 }}>
        <Descriptions.Item label="获利比例">
          {profit == null ? (
            '--'
          ) : (
            <span style={{ color: colorForProfit(profit) }}>
              {(Number(profit) * 100).toFixed(2)}%
            </span>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="平均成本">
          {fmtPrice(data.avg_cost)}
        </Descriptions.Item>
        <Descriptions.Item label="90%集中度">
          {data.concentration_90 == null ? '--' : `${fmtPrice(data.cost_90_low)} ~ ${fmtPrice(data.cost_90_high)}`}
        </Descriptions.Item>
        <Descriptions.Item label="70%集中度">
          {data.concentration_70 == null ? '--' : `${(Number(data.concentration_70) * 100).toFixed(2)}%`}
        </Descriptions.Item>
      </Descriptions>
      {data.trade_date && (
        <Tag color="default" style={{ marginBottom: 8 }}>
          {data.trade_date}
        </Tag>
      )}
      {dist.length === 0 ? (
        <EmptyState description="暂无分布数据" />
      ) : (
        <ReactECharts option={option} notMerge lazyUpdate style={{ height: 260 }} />
      )}
    </Card>
  );
}

function nearAvg(price: number, avg: number | null): boolean {
  if (avg == null) return false;
  return Math.abs(price - avg) < 0.01;
}

function colorForProfit(ratio: number): string {
  const pct = Number(ratio) * 100;
  if (pct >= 50) return UP_COLOR; // mostly in profit — bullish tint
  if (pct <= 20) return DOWN_COLOR;
  return FLAT_COLOR;
}
