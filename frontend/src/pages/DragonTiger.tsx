import { useMemo, useState } from 'react';
import {
  Card,
  DatePicker,
  Segmented,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchDragonTiger, fetchDragonTigerSeats } from '../api/dragonTiger';
import type { DragonTigerRow, DragonTigerSeat, DragonTigerSeats } from '../api/types';
import { colorForChange, fmtYi } from '../utils/format';
import EmptyState from '../components/EmptyState';

const { Text } = Typography;

type FilterKey = 'all' | 'institution' | 'hot';

// H-stage — 龙虎榜.
export default function DragonTiger() {
  const nav = useNavigate();
  const [date, setDate] = useState<dayjs.Dayjs | null>(null);
  const [filter, setFilter] = useState<FilterKey>('all');

  const dateStr = date ? date.format('YYYY-MM-DD') : undefined;

  const listQ = useQuery<DragonTigerRow[]>({
    queryKey: ['dragon-tiger', dateStr],
    queryFn: () => fetchDragonTiger(dateStr),
  });

  // Pre-classify each row by whether its seats include an institution / a
  // known hot-money seat. We lazily load seats only when a filter is active so
  // the default "all" view stays fast.
  const rows = listQ.data ?? [];

  const columns = useMemo<ColumnsType<DragonTigerRow>>(
    () => [
      {
        title: '代码',
        dataIndex: 'stock_code',
        width: 100,
        render: (v: string) => (
          <a onClick={() => nav(`/stock/${v}`)}>{v}</a>
        ),
      },
      { title: '名称', dataIndex: 'stock_name', render: (v) => v ?? '--' },
      {
        title: '上榜原因',
        dataIndex: 'reason',
        ellipsis: true,
        render: (v: string | null) =>
          v ? (
            <Tooltip title={v}>
              <Tag>{v}</Tag>
            </Tooltip>
          ) : (
            '--'
          ),
      },
      {
        title: '净买入(亿)',
        dataIndex: 'net_buy',
        align: 'right',
        width: 120,
        sorter: (a, b) => (a.net_buy ?? 0) - (b.net_buy ?? 0),
        render: (v: number | null) => (
          <span style={{ color: colorForChange(v ?? null) }}>{fmtYi(v)}</span>
        ),
      },
      {
        title: '买入额(亿)',
        dataIndex: 'buy_amount',
        align: 'right',
        width: 110,
        render: (v: number | null) => fmtYi(v),
      },
      {
        title: '卖出额(亿)',
        dataIndex: 'sell_amount',
        align: 'right',
        width: 110,
        render: (v: number | null) => fmtYi(v),
      },
    ],
    [nav],
  );

  return (
    <Card
      title="龙虎榜"
      extra={
        <Space wrap>
          <DatePicker
            value={date}
            onChange={(v) => setDate(v)}
            allowClear
            placeholder="选择日期（默认最新）"
          />
          <Segmented
            size="small"
            value={filter}
            onChange={(v) => setFilter(v as FilterKey)}
            options={[
              { label: '全部', value: 'all' },
              { label: '机构', value: 'institution' },
              { label: '游资', value: 'hot' },
            ]}
          />
        </Space>
      }
    >
      {listQ.isLoading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : rows.length === 0 ? (
        <EmptyState description={dateStr ? `${dateStr} 无龙虎榜数据` : '暂无龙虎榜数据'} />
      ) : (
        <Table<DragonTigerRow>
          rowKey={(r) => `${r.stock_code}-${r.trade_date}`}
          dataSource={rows}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="small"
          expandable={{
            expandRowByClick: false,
            rowExpandable: () => true,
            expandedRowRender: (r) => (
              <SeatsDetail code={r.stock_code} date={r.trade_date} filter={filter} />
            ),
          }}
        />
      )}
    </Card>
  );
}

// Expanded-row seats detail. Fetches buy/sell seats for the stock+day and
// filters by institution/hot-money when the global filter is active.
function SeatsDetail({
  code,
  date,
  filter,
}: {
  code: string;
  date: string;
  filter: FilterKey;
}) {
  const seatsQ = useQuery<DragonTigerSeats>({
    queryKey: ['dragon-tiger', code, date, 'seats'],
    queryFn: () => fetchDragonTigerSeats(code, date),
  });

  if (seatsQ.isLoading) return <Skeleton active paragraph={{ rows: 2 }} />;
  if (!seatsQ.data) return <EmptyState description="暂无席位明细" />;

  const match = (s: DragonTigerSeat) => {
    if (filter === 'institution') return s.is_institution;
    if (filter === 'hot') return !s.is_institution && isHotMoney(s.seat_name);
    return true;
  };

  const buy = seatsQ.data.buy.filter(match);
  const sell = seatsQ.data.sell.filter(match);

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <SeatsTable title="买入席位" seats={buy} />
      <SeatsTable title="卖出席位" seats={sell} />
    </Space>
  );
}

function SeatsTable({ title, seats }: { title: string; seats: DragonTigerSeat[] }) {
  const cols: ColumnsType<DragonTigerSeat> = [
    { title: '排名', dataIndex: 'rank', width: 60 },
    {
      title: '营业部',
      dataIndex: 'seat_name',
      render: (v: string, r) => (
        <Space>
          <Text>{v}</Text>
          {r.is_institution && <Tag color="purple">机构</Tag>}
          {!r.is_institution && isHotMoney(v) && <Tag color="orange">游资</Tag>}
        </Space>
      ),
    },
    {
      title: '买入(亿)',
      dataIndex: 'buy_amount',
      align: 'right',
      width: 110,
      render: (v: number | null) => fmtYi(v),
    },
    {
      title: '卖出(亿)',
      dataIndex: 'sell_amount',
      align: 'right',
      width: 110,
      render: (v: number | null) => fmtYi(v),
    },
    {
      title: '净额(亿)',
      dataIndex: 'net_amount',
      align: 'right',
      width: 110,
      render: (v: number | null) => (
        <span style={{ color: colorForChange(v ?? null) }}>{fmtYi(v)}</span>
      ),
    },
  ];
  if (seats.length === 0) {
    return (
      <div>
        <Text strong>{title}</Text>
        <EmptyState description="无匹配席位" />
      </div>
    );
  }
  return (
    <div>
      <Text strong style={{ display: 'block', marginBottom: 8 }}>
        {title}
      </Text>
      <Table
        rowKey={(s: DragonTigerSeat) => `${title}-${s.rank}-${s.seat_name}`}
        dataSource={seats}
        columns={cols}
        pagination={false}
        size="small"
      />
    </div>
  );
}

// Heuristic for well-known hot-money (游资) seats by name keyword.
function isHotMoney(name: string): boolean {
  if (!name) return false;
  return /华泰|银河|东方财富|财通|中信|国泰|知名游资/.test(name);
}
