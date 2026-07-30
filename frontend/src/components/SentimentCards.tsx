import { Card, Col, Row, Skeleton, Statistic, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsInstance } from 'echarts-for-react/lib/types';
import { useRef } from 'react';
import type { Sentiment } from '../api/types';
import { UP_COLOR, DOWN_COLOR, FLAT_COLOR } from '../utils/format';

const { Text } = Typography;

// Market sentiment card group (V1.5 BP-V1.5-006/011).
//
// Six headline metrics (limit-up / limit-down / failed-limit / seal rate / max
// streak / up-down) plus a small "ladder" step chart showing how many stocks
// sit on each consecutive limit-up streak (1板 / 2板 / 3板 ...).

type Props = { sentiment?: Sentiment; loading: boolean };

export default function SentimentCards({ sentiment, loading }: Props) {
  if (loading && !sentiment) {
    return (
      <Card title="市场情绪">
        <Skeleton active paragraph={{ rows: 3 }} />
      </Card>
    );
  }

  const seal = sentiment?.seal_rate;
  const ladder = sentiment?.streak_ladder ?? null;

  return (
    <Card
      title={
        <span>
          市场情绪
          {sentiment?.trade_date && (
            <Tag color="default" style={{ marginLeft: 8, fontWeight: 400 }}>
              {sentiment.trade_date}
            </Tag>
          )}
        </span>
      }
    >
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="涨停"
            value={sentiment?.limit_up_count ?? 0}
            valueStyle={{ color: UP_COLOR }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="跌停"
            value={sentiment?.limit_down_count ?? 0}
            valueStyle={{ color: DOWN_COLOR }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="炸板"
            value={sentiment?.failed_limit_count ?? 0}
            valueStyle={{ color: FLAT_COLOR }}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="封板率"
            value={seal == null ? '--' : (Number(seal) * 100).toFixed(1)}
            suffix={seal == null ? '' : '%'}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Statistic
            title="连板高度"
            value={sentiment?.max_streak ?? 0}
            suffix={sentiment?.max_streak ? '板' : ''}
          />
        </Col>
        <Col xs={12} sm={8} md={4}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              涨 / 跌
            </Text>
            <div style={{ marginTop: 4 }}>
              <span style={{ color: UP_COLOR, fontWeight: 600 }}>
                {sentiment?.up_count ?? 0}
              </span>
              <span style={{ color: FLAT_COLOR, margin: '0 6px' }}>/</span>
              <span style={{ color: DOWN_COLOR, fontWeight: 600 }}>
                {sentiment?.down_count ?? 0}
              </span>
            </div>
          </div>
        </Col>
      </Row>

      {ladder && Object.keys(ladder).length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            连板梯队
          </Text>
          <LadderChart ladder={ladder} />
        </div>
      )}
    </Card>
  );
}

function LadderChart({ ladder }: { ladder: Record<string, number> }) {
  const ref = useRef<ReactECharts | null>(null);
  const entries = Object.entries(ladder)
    .map(([k, v]) => [Number(k), v] as [number, number])
    .sort((a, b) => a[0] - b[0]);
  const option = {
    animation: false,
    grid: { left: 32, right: 16, top: 16, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any[]) =>
        params.length ? `${params[0].axisValue}板: ${params[0].value}只` : '',
    },
    xAxis: {
      type: 'category',
      data: entries.map(([k]) => `${k}`),
      axisLabel: { fontSize: 10 },
    },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f0f0' } } },
    series: [
      {
        type: 'bar',
        data: entries.map(([k, v]) => ({
          value: v,
          itemStyle: { color: k >= 3 ? '#722ed1' : UP_COLOR },
        })),
        barMaxWidth: 28,
      },
    ],
  };
  return (
    <ReactECharts
      ref={(r) => {
        ref.current = r;
      }}
      option={option}
      notMerge
      lazyUpdate
      style={{ height: 140 }}
      onEvents={{
        dblclick: () => {
          const inst: EChartsInstance | undefined = ref.current?.getEchartsInstance();
          inst?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
        },
      }}
    />
  );
}
