import { useMemo } from 'react';
import { Button, Card, Skeleton, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchSector, fetchSectorStocks } from '../api/sector';
import type { SectorStock } from '../api/types';
import { colorForChange, fmtPct, fmtPrice, fmtYi } from '../utils/format';
import EmptyState from '../components/EmptyState';

const { Title, Text } = Typography;

// H-stage — 板块详情: 成分股列表, 龙头高亮.
export default function SectorDetail() {
  const { code = '' } = useParams();
  const nav = useNavigate();

  const sectorQ = useQuery({
    queryKey: ['sector', code],
    queryFn: () => fetchSector(code),
    enabled: !!code,
  });

  const stocksQ = useQuery<SectorStock[]>({
    queryKey: ['sector', code, 'stocks'],
    queryFn: () => fetchSectorStocks(code),
    enabled: !!code,
  });

  const columns = useMemo<ColumnsType<SectorStock>>(
    () => [
      {
        title: '代码',
        dataIndex: 'stock_code',
        width: 100,
        render: (v: string, r) =>
          r.is_leader ? (
            <Space>
              <Text copyable strong>
                {v}
              </Text>
              <Tag color="red">龙头</Tag>
            </Space>
          ) : (
            <Text copyable>{v}</Text>
          ),
      },
      {
        title: '名称',
        dataIndex: 'stock_name',
        render: (v, r) => (r.is_leader ? <Text strong>{v ?? '--'}</Text> : v ?? '--'),
      },
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
        title: '成交额(亿)',
        dataIndex: 'amount',
        align: 'right',
        width: 110,
        render: (v: number | null) => fmtYi(v),
      },
      {
        title: '总市值(亿)',
        dataIndex: 'total_mv',
        align: 'right',
        width: 120,
        render: (v: number | null) => fmtYi(v),
      },
    ],
    [],
  );

  const sector = sectorQ.data;

  return (
    <Card
      title={
        <Space>
          <Button size="small" onClick={() => nav('/sector')}>
            返回
          </Button>
          <Title level={5} style={{ margin: 0 }}>
            {sector?.sector_name ?? code}
          </Title>
          {sector?.sector_type && <Tag>{sector.sector_type === 'industry' ? '行业' : '概念'}</Tag>}
          {sector?.pct_change != null && (
            <span style={{ color: colorForChange(sector.pct_change) }}>
              {fmtPct(sector.pct_change)}
            </span>
          )}
        </Space>
      }
    >
      {stocksQ.isLoading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : !stocksQ.data || stocksQ.data.length === 0 ? (
        <EmptyState description="该板块暂无成分股数据" />
      ) : (
        <Table<SectorStock>
          rowKey="stock_code"
          dataSource={stocksQ.data}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="small"
          rowClassName={(r) => (r.is_leader ? 'sector-leader-row' : '')}
          onRow={(r) => ({
            onClick: () => nav(`/stock/${r.stock_code}`),
            style: { cursor: 'pointer', background: r.is_leader ? '#fff1f0' : undefined },
          })}
        />
      )}
    </Card>
  );
}
