import {
  Button,
  Card,
  Col,
  Row,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listStrategies } from '../api/strategy';
import EmptyState from '../components/EmptyState';

const { Title, Paragraph, Text } = Typography;

// One parameter descriptor in the strategy metadata from /strategy.
interface StrategyParam {
  name: string;
  type: string;
  default: number | string;
  min?: number;
  max?: number;
  description?: string;
}

interface StrategyMeta {
  name: string;
  title: string;
  description: string;
  params: StrategyParam[];
  available: boolean;
}

// H4 + V2 — 策略列表.
//
// Renders all strategies from the registry as cards in a responsive grid.
// V2 makes all 8 strategies available (V1 ma/macd + V2 ema/trend/leader/board/
// lowbuy/breakout). Each card shows the strategy's params; clicking
// 「回测此策略」 carries the strategy key into /backtest?strategy=<name>.
export default function Strategy() {
  const nav = useNavigate();
  const q = useQuery<StrategyMeta[]>({
    queryKey: ['strategies'],
    queryFn: listStrategies,
  });

  if (q.isLoading) {
    return (
      <Row gutter={[16, 16]}>
        {[0, 1, 2, 3].map((i) => (
          <Col key={i} xs={24} sm={12} lg={8}>
            <Card>
              <Skeleton active paragraph={{ rows: 3 }} />
            </Card>
          </Col>
        ))}
      </Row>
    );
  }

  if (!q.data || q.data.length === 0) {
    return (
      <Card>
        <EmptyState description="暂无可用策略" />
      </Card>
    );
  }

  return (
    <>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          策略库
        </Title>
        <Text type="secondary">选择策略进入回测，验证历史表现</Text>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
          gap: 24,
          alignItems: 'start',
        }}
      >
        {q.data.map((s) => (
          <StrategyCard
            key={s.name}
            meta={s}
            onBacktest={() => nav(`/backtest?strategy=${s.name}`)}
          />
        ))}
      </div>
    </>
  );
}

function StrategyCard({
  meta,
  onBacktest,
}: {
  meta: StrategyMeta;
  onBacktest: () => void;
}) {
  const params = meta.params ?? [];
  // V2: all registered strategies are interactive. We keep the `available`
  // flag for display (a "可用" tag) but no longer gate the backtest button.
  const available = meta.available;
  return (
    <Card
      styles={{ body: { display: 'flex', flexDirection: 'column', padding: 20 } }}
      style={{ width: '100%' }}
      title={
        <Space size={8} align="center">
          <span>{meta.title}</span>
          <Tag color={available ? 'green' : 'blue'}>{available ? 'V1' : 'V2'}</Tag>
        </Space>
      }
      extra={<Tag>{meta.name}</Tag>}
    >
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        {meta.description || '暂无描述'}
      </Paragraph>

      <div style={{ minHeight: 32, marginBottom: 16 }}>
        {params.length > 0 ? (
          <Space size={[8, 8]} wrap>
            {params.map((p) => (
              <Tag key={p.name} style={{ margin: 0 }}>
                {p.description || p.name}{' '}
                <Text strong style={{ fontSize: 12 }}>
                  {String(p.default)}
                </Text>
              </Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">无参数</Text>
        )}
      </div>

      <Button type="primary" block onClick={onBacktest}>
        回测此策略
      </Button>
    </Card>
  );
}
