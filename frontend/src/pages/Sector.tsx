import { useMemo, useState } from 'react';
import { Card, Segmented, Skeleton, Space, Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchSectors } from '../api/sector';
import type { SectorSortKey, SectorType } from '../api/sector';
import type { SectorRow } from '../api/types';
import { colorForChange, fmtPct, fmtYi } from '../utils/format';
import EmptyState from '../components/EmptyState';

const { Text } = Typography;

const SORT_OPTIONS: { label: string; value: SectorSortKey }[] = [
  { label: '涨跌幅', value: 'pct_change' },
  { label: '成交额', value: 'amount' },
  { label: '主力净流入', value: 'main_net_inflow' },
];

// H-stage — 板块排行 (行业 / 概念).
export default function Sector() {
  const nav = useNavigate();
  const [type, setType] = useState<SectorType>('industry');
  const [sort, setSort] = useState<SectorSortKey>('pct_change');

  const sectorsQ = useQuery<SectorRow[]>({
    queryKey: ['sector', type, sort],
    queryFn: () => fetchSectors(type, sort, 100),
  });

  const columns = useMemo<ColumnsType<SectorRow>>(
    () => [
      {
        title: '板块',
        dataIndex: 'sector_name',
        render: (v: string, r) => (
          <Space>
            <a onClick={() => nav(`/sector/${r.sector_code}`)}>{v}</a>
          </Space>
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
        render: (v: number | null) => fmtYi(v),
      },
      {
        title: '涨停数',
        dataIndex: 'limit_up_count',
        align: 'right',
        width: 90,
        render: (v: number | null) => v ?? '--',
      },
      {
        title: '主力净流入(亿)',
        dataIndex: 'main_net_inflow',
        align: 'right',
        width: 130,
        render: (v: number | null) => (
          <span style={{ color: colorForChange(v ?? null) }}>{fmtYi(v)}</span>
        ),
      },
      {
        title: '龙头',
        dataIndex: 'leader_code',
        width: 140,
        render: (v: string | null, r) =>
          v ? (
            <Space>
              <a onClick={() => nav(`/stock/${v}`)}>{v}</a>
              {r.leader_name && <Text type="secondary">{r.leader_name}</Text>}
            </Space>
          ) : (
            '--'
          ),
      },
    ],
    [nav],
  );

  return (
    <Card
      title="板块"
      extra={
        <Segmented
          size="small"
          value={type}
          onChange={(v) => setType(v as SectorType)}
          options={[
            { label: '行业', value: 'industry' },
            { label: '概念', value: 'concept' },
          ]}
        />
      }
    >
      <div style={{ marginBottom: 12 }}>
        <Segmented
          size="small"
          value={sort}
          onChange={(v) => setSort(v as SectorSortKey)}
          options={SORT_OPTIONS}
        />
      </div>
      {sectorsQ.isLoading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : !sectorsQ.data || sectorsQ.data.length === 0 ? (
        <EmptyState description="暂无板块数据" />
      ) : (
        <Table<SectorRow>
          rowKey={(r) => `${r.sector_code}-${r.sector_type}`}
          dataSource={sectorsQ.data}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="small"
        />
      )}
    </Card>
  );
}
