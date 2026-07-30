import { useState } from 'react';
import { Button, Card, Col, Row, Segmented, Skeleton, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  fetchNorthFlow,
  fetchSentiment,
  getHotStocks,
  getIndices,
  getMarketSummary,
} from '../api/market';
import type {
  HotStock,
  IndexQuote,
  MarketSummary,
  NorthFlowRow,
  Sentiment,
} from '../api/types';
import { colorForChange, fmtMoney, fmtPct, fmtPrice } from '../utils/format';
import EmptyState from '../components/EmptyState';
import SentimentCards from '../components/SentimentCards';
import NorthFlowCard from '../components/NorthFlowCard';

const { Title, Text } = Typography;

const REFETCH_MS = 30_000;
const HOT_LIMIT = 20;
type SortKey = 'amount' | 'pct_change';

// H1 — 行情总览.
export default function Market() {
  const nav = useNavigate();
  const [sort, setSort] = useState<SortKey>('amount');

  const indicesQ = useQuery<IndexQuote[]>({
    queryKey: ['market', 'indices'],
    queryFn: getIndices,
    refetchInterval: REFETCH_MS,
  });
  const summaryQ = useQuery<MarketSummary>({
    queryKey: ['market', 'summary'],
    queryFn: getMarketSummary,
    refetchInterval: REFETCH_MS,
  });
  const hotQ = useQuery<HotStock[]>({
    queryKey: ['market', 'hot', sort],
    queryFn: () => getHotStocks(sort, HOT_LIMIT),
    refetchInterval: REFETCH_MS,
  });

  // V1.5 — market sentiment + northbound flow.
  const sentimentQ = useQuery<Sentiment>({
    queryKey: ['market', 'sentiment'],
    queryFn: fetchSentiment,
    refetchInterval: REFETCH_MS,
  });
  const northQ = useQuery<NorthFlowRow[]>({
    queryKey: ['market', 'north-flow'],
    queryFn: () => fetchNorthFlow(30),
    refetchInterval: REFETCH_MS,
  });

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Row gutter={16}>
          {(indicesQ.data ?? [undefined, undefined, undefined]).map((idx, i) => (
            <Col key={i} xs={24} sm={8}>
              <IndexCard loading={indicesQ.isLoading} index={idx} />
            </Col>
          ))}
        </Row>
      </Col>

      <Col span={24}>
        <SummaryCard loading={summaryQ.isLoading} summary={summaryQ.data} />
      </Col>

      <Col span={24}>
        <SentimentCards sentiment={sentimentQ.data} loading={sentimentQ.isLoading} />
      </Col>

      <Col xs={24} lg={12}>
        <NorthFlowCard rows={northQ.data} loading={northQ.isLoading} />
      </Col>
      <Col xs={24} lg={12}>
        <Card
          title="龙虎榜"
          extra={
            <Button size="small" type="link" onClick={() => nav('/dragon-tiger')}>
              查看全部
            </Button>
          }
        >
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Button type="primary" onClick={() => nav('/dragon-tiger')}>
              进入龙虎榜
            </Button>
          </div>
        </Card>
      </Col>

      <Col span={24}>
        <Card
          title="热门股票"
          extra={
            <Segmented
              size="small"
              value={sort}
              onChange={(v) => setSort(v as SortKey)}
              options={[
                { label: '成交额', value: 'amount' },
                { label: '涨幅', value: 'pct_change' },
              ]}
            />
          }
        >
          {hotQ.isLoading ? (
            <Skeleton active paragraph={{ rows: 6 }} />
          ) : !hotQ.data || hotQ.data.length === 0 ? (
            <EmptyState description="当前为非交易时段，展示最近交易日数据" />
          ) : (
            <Table<HotStock>
              rowKey="stock_code"
              dataSource={hotQ.data}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              size="small"
              onRow={(r) => ({ onClick: () => nav(`/stock/${r.stock_code}`) })}
              columns={hotColumns}
            />
          )}
        </Card>
      </Col>
    </Row>
  );
}

const hotColumns: ColumnsType<HotStock> = [
  {
    title: '代码',
    dataIndex: 'stock_code',
    width: 100,
    render: (v: string) => <Text copyable>{v}</Text>,
  },
  { title: '名称', dataIndex: 'stock_name', render: (v) => v ?? '--' },
  {
    title: '现价',
    dataIndex: 'close',
    align: 'right',
    width: 90,
    render: (v: number | null, r) => (
      <span style={{ color: colorForChange(r.pct_change) }}>{fmtPrice(v)}</span>
    ),
  },
  {
    title: '涨跌幅',
    dataIndex: 'pct_change',
    align: 'right',
    width: 100,
    sorter: (a, b) => (a.pct_change ?? 0) - (b.pct_change ?? 0),
    render: (v: number | null) => (
      <span style={{ color: colorForChange(v) }}>{fmtPct(v)}</span>
    ),
  },
  {
    title: '成交额',
    dataIndex: 'amount',
    align: 'right',
    width: 110,
    render: (v: number | null) => fmtMoney(v),
  },
];

function IndexCard({ index, loading }: { index?: IndexQuote; loading: boolean }) {
  if (loading && !index) {
    return (
      <Card>
        <Skeleton active paragraph={{ rows: 2 }} />
      </Card>
    );
  }
  const hasData = !!index && index.close != null;
  const color = hasData ? colorForChange(index!.pct_change) : undefined;
  return (
    <Card>
      <Text type="secondary">{index?.name ?? '—'}</Text>
      {hasData ? (
        <>
          <Title level={3} style={{ margin: '4px 0 0', color }}>
            {fmtPrice(index!.close)}
            <span style={{ fontSize: 14, marginLeft: 8 }}>
              {fmtPct(index!.pct_change)}
            </span>
          </Title>
        </>
      ) : (
        <Title level={4} type="secondary" style={{ margin: '8px 0 0' }}>
          数据待接入
        </Title>
      )}
    </Card>
  );
}

function SummaryCard({ summary, loading }: { summary?: MarketSummary; loading: boolean }) {
  if (loading && !summary) {
    return (
      <Card>
        <Skeleton active paragraph={{ rows: 1 }} />
      </Card>
    );
  }
  const adv = summary?.advance_count ?? 0;
  const dec = summary?.decline_count ?? 0;
  const flat = summary?.flat_count ?? 0;
  const total = Math.max(adv + dec + flat, 1); // avoid divide-by-zero
  const bar = (count: number, color: string, label: string, val: string) => (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <Text type="secondary">{label}</Text>
        <strong style={{ color }}>{val}</strong>
      </div>
      <div
        style={{
          height: 10,
          borderRadius: 5,
          background: '#f0f0f0',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${(count / total) * 100}%`,
            height: '100%',
            background: color,
          }}
        />
      </div>
    </div>
  );
  return (
    <Card
      title={
        <span>
          市场概况
          {summary?.trade_date && (
            <Tag color="default" style={{ marginLeft: 8, fontWeight: 400 }}>
              {summary.trade_date}
            </Tag>
          )}
        </span>
      }
    >
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {bar(adv, '#f5222d', '上涨', String(adv))}
        {bar(dec, '#52c41a', '下跌', String(dec))}
        {bar(flat, '#8c8c8c', '平盘', String(flat))}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <Text type="secondary">总成交额</Text>
          </div>
          <strong style={{ fontSize: 16 }}>{fmtMoney(summary?.total_amount)}</strong>
        </div>
      </div>
    </Card>
  );
}
