import { useMemo, useState } from 'react';
import {
  Card,
  Col,
  Row,
  Segmented,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchIndustries, fetchStocks } from '../api/stocks';
import type { StockSortKey } from '../api/stocks';
import type { Page, StockRow } from '../api/types';
import { colorForChange, fmtPct, fmtPrice, fmtYi } from '../utils/format';
import EmptyState from '../components/EmptyState';

const { Text } = Typography;

const DEFAULT_SIZE = 20;
const SORT_OPTIONS: { label: string; value: StockSortKey }[] = [
  { label: '涨跌幅', value: 'pct_change' },
  { label: '成交额', value: 'amount' },
  { label: '总市值', value: 'total_mv' },
  { label: '市盈率', value: 'pe' },
];

// Quick tag filters map to common screen shortcuts.
const QUICK_TAGS = ['涨停', '领涨', '低价', '高换手'] as const;

// H-stage — 全市场股票列表.
export default function Stocks() {
  const nav = useNavigate();
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_SIZE);
  const [industry, setIndustry] = useState<string>();
  const [tag, setTag] = useState<string>();
  const [sort, setSort] = useState<StockSortKey>('amount');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');

  const industryQ = useQuery<string[]>({
    queryKey: ['stocks', 'industries'],
    queryFn: fetchIndustries,
    staleTime: 5 * 60_000,
  });

  const stocksQ = useQuery<Page<StockRow>>({
    queryKey: ['stocks', { industry, tag, sort, order, page, size }],
    queryFn: () =>
      fetchStocks({ industry, tag, sort, order, page, size }).catch((e) => {
        message.error(e instanceof Error ? e.message : '加载股票列表失败');
        throw e;
      }),
    placeholderData: (prev) => prev,
  });

  const columns = useMemo<ColumnsType<StockRow>>(
    () => [
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
        render: (v: number | null) => (
          <span style={{ color: colorForChange(v) }}>{fmtPct(v)}</span>
        ),
      },
      {
        title: '成交额(亿)',
        dataIndex: 'amount',
        align: 'right',
        width: 110,
        sorter: true,
        render: (v: number | null) => fmtYi(v),
      },
      {
        title: '总市值(亿)',
        dataIndex: 'total_mv',
        align: 'right',
        width: 120,
        sorter: true,
        render: (v: number | null) => fmtYi(v),
      },
      {
        title: '市盈率',
        dataIndex: 'pe',
        align: 'right',
        width: 90,
        sorter: true,
        render: (v: number | null) => (v == null ? '--' : Number(v).toFixed(2)),
      },
      {
        title: '行业',
        dataIndex: 'industry',
        width: 110,
        render: (v) => (v ? <Tag color="blue">{v}</Tag> : '--'),
      },
    ],
    [],
  );

  const pagination: TablePaginationConfig = {
    current: page,
    pageSize: size,
    total: stocksQ.data?.total ?? 0,
    showSizeChanger: true,
    pageSizeOptions: [10, 20, 50],
    onChange: (p, s) => {
      setPage(p);
      setSize(s);
    },
    showTotal: (t) => `共 ${t} 只`,
  };

  const onSortChange = (key: StockSortKey) => {
    // Clicking the active sort header again toggles order.
    if (key === sort) {
      setOrder((o) => (o === 'desc' ? 'asc' : 'desc'));
    } else {
      setSort(key);
      setOrder('desc');
    }
    setPage(1);
  };

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card
          title="股票"
          extra={
            <Space wrap>
              <Select
                allowClear
                placeholder="行业筛选"
                style={{ width: 160 }}
                value={industry}
                onChange={(v) => {
                  setIndustry(v);
                  setPage(1);
                }}
                options={(industryQ.data ?? []).map((i) => ({
                  label: i,
                  value: i,
                }))}
                loading={industryQ.isLoading}
                showSearch
              />
              <Segmented
                size="small"
                value={tag}
                onChange={(v) => {
                  setTag(v === tag ? undefined : (v as string));
                  setPage(1);
                }}
                options={QUICK_TAGS.map((t) => ({ label: t, value: t }))}
              />
              <Segmented
                size="small"
                value={sort}
                onChange={(v) => onSortChange(v as StockSortKey)}
                options={SORT_OPTIONS}
              />
              <Tag color={order === 'desc' ? 'red' : 'green'}>
                {order === 'desc' ? '降序' : '升序'}
              </Tag>
            </Space>
          }
        >
          {stocksQ.isLoading && !stocksQ.data ? (
            <Skeleton active paragraph={{ rows: 8 }} />
          ) : !stocksQ.data || stocksQ.data.items.length === 0 ? (
            <EmptyState description="未找到符合条件的股票" />
          ) : (
            <Table<StockRow>
              rowKey="stock_code"
              dataSource={stocksQ.data.items}
              columns={columns}
              pagination={pagination}
              size="small"
              loading={stocksQ.isFetching && !!stocksQ.data}
              onRow={(r) => ({
                onClick: () => nav(`/stock/${r.stock_code}`),
                style: { cursor: 'pointer' },
              })}
            />
          )}
        </Card>
      </Col>
    </Row>
  );
}
