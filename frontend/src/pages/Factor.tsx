import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Empty,
  Input,
  InputNumber,
  Row,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tree,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  computeFactorIC,
  computeFactorICSeries,
  computeFactorSeries,
  layeredBacktest,
  listFactorPresets,
  listFactors,
  multiFactorScore,
} from '../api/factor';
import type {
  FactorIcSeries,
  FactorLayeredBacktest,
  FactorPreset,
  SampleFilters,
} from '../api/factor';
import type {
  FactorBrief,
  FactorCategory,
  FactorIC,
  FactorScoreRow,
  FactorSeriesPoint,
} from '../api/types';
import ICChart from '../components/ICChart';
import LayeredReturns from '../components/LayeredReturns';
import EmptyState from '../components/EmptyState';
import ReactECharts from 'echarts-for-react';

const { RangePicker } = DatePicker;
const { Text } = Typography;

// V2.1 样本过滤条（BP-V2.1-004/005）：IC 与打分共用；默认全关 = V2 原行为。
function SampleFilterBar({
  value,
  onChange,
}: {
  value: SampleFilters;
  onChange: (v: SampleFilters) => void;
}) {
  return (
    <Space wrap size={12}>
      <Space size={4}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          股票池
        </Text>
        <Select
          size="small"
          value={value.pool ?? 'current'}
          style={{ width: 110 }}
          onChange={(v) => onChange({ ...value, pool: v })}
          options={[
            { value: 'current', label: '当前快照' },
            { value: 'pit', label: '历史时点(PIT)' },
          ]}
        />
      </Space>
      <Checkbox
        checked={value.exclude_st ?? false}
        onChange={(e) => onChange({ ...value, exclude_st: e.target.checked })}
      >
        剔除 ST
      </Checkbox>
      <Checkbox
        checked={value.exclude_suspended ?? false}
        onChange={(e) => onChange({ ...value, exclude_suspended: e.target.checked })}
      >
        剔除停牌
      </Checkbox>
      <Checkbox
        checked={value.only_tradable ?? false}
        onChange={(e) => onChange({ ...value, only_tradable: e.target.checked })}
      >
        仅可成交
      </Checkbox>
      <Space size={4}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          中性化
        </Text>
        <Select
          size="small"
          value={value.neutralize ?? 'none'}
          style={{ width: 110 }}
          onChange={(v) => onChange({ ...value, neutralize: v })}
          options={[
            { value: 'none', label: '关闭' },
            { value: 'industry', label: '行业' },
            { value: 'industry_mcap', label: '行业+市值' },
          ]}
        />
      </Space>
    </Space>
  );
}

// Factor category tree groups.
const CATEGORY_TREE: { key: FactorCategory; title: string }[] = [
  { key: 'trend', title: '趋势' },
  { key: 'momentum', title: '动量' },
  { key: 'volatility', title: '波动率' },
  { key: 'volume', title: '成交量' },
  { key: 'fundamental', title: '基本面' },
  { key: 'sentiment', title: '情绪' },
];

// N3 — 因子分析页.
//
// Left rail: a factor category tree (trend/momentum/...). Selecting a leaf
// loads that category's factors. Right pane: pick a factor -> IC bar chart +
// layered returns bar + a factor-value table for one stock.
export default function Factor() {
  const nav = useNavigate();
  const [selectedFactor, setSelectedFactor] = useState<string | null>(null);
  const [stock, setStock] = useState('600519');
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(1, 'year'),
    dayjs(),
  ]);
  const [horizon, setHorizon] = useState(5);
  const [sample, setSample] = useState<SampleFilters>({});

  // Load all factors once (no category filter) so the tree can group them.
  const factorsQ = useQuery<FactorBrief[]>({
    queryKey: ['factors', 'all'],
    queryFn: () => listFactors(),
  });

  // Auto-select the first factor when the factors load, and reset when the
  // selected factor is no longer present in the loaded list.
  const factors = factorsQ.data ?? [];
  const firstCode = factors[0]?.code;
  useEffect(() => {
    if (!firstCode) return;
    const data = factorsQ.data ?? [];
    if (!selectedFactor || !data.some((f) => f.code === selectedFactor)) {
      setSelectedFactor(firstCode);
    }
    // Depend only on primitives / the query result reference, not the derived
    // `factors` array (which is rebuilt every render).
  }, [firstCode, factorsQ.data, selectedFactor]);
  const effectiveFactor = selectedFactor ?? firstCode ?? null;

  const start = range[0].format('YYYY-MM-DD');
  const end = range[1].format('YYYY-MM-DD');

  const seriesQ = useQuery<FactorSeriesPoint[]>({
    queryKey: ['factor', effectiveFactor, 'series', stock, start, end],
    queryFn: () => computeFactorSeries(effectiveFactor!, stock, start, end),
    enabled: !!effectiveFactor && !!stock,
  });

  const icQ = useQuery<FactorIC | null>({
    queryKey: ['factor', effectiveFactor, 'ic', horizon, end, sample],
    queryFn: () => computeFactorIC(effectiveFactor!, horizon, end, sample),
    enabled: !!effectiveFactor,
  });

  const treeData = useMemo(
    () =>
      CATEGORY_TREE.map((c) => {
        const children = (factorsQ.data ?? [])
          .filter((f) => f.category === c.key)
          .map((f) => ({
            key: f.code,
            title: (
              <span>
                {f.name} <Text type="secondary" style={{ fontSize: 11 }}>{f.code}</Text>
              </span>
            ),
          }));
        return {
          key: c.key,
          title: (
            <span>
              {c.title}
              {children.length > 0 && (
                <Tag style={{ marginLeft: 6, fontSize: 10 }}>{children.length}</Tag>
              )}
            </span>
          ),
          children,
        };
      }),
    [factorsQ.data],
  );

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={6}>
        <Card
          title="因子分类"
          size="small"
          styles={{ body: { padding: 8, maxHeight: '70vh', overflow: 'auto' } }}
        >
          {factorsQ.isLoading ? (
            <Skeleton active paragraph={{ rows: 8 }} />
          ) : (
            <Tree
              treeData={treeData}
              defaultExpandedKeys={CATEGORY_TREE.map((c) => c.key)}
              selectedKeys={effectiveFactor ? [effectiveFactor] : []}
              onSelect={(keys) => {
                const k = keys[0] as string | undefined;
                // only leaf (factor code) selects change the factor
                if (k && !CATEGORY_TREE.some((c) => c.key === k)) {
                  setSelectedFactor(k);
                }
              }}
            />
          )}
        </Card>
      </Col>

      <Col xs={24} lg={18}>
        <Card
          size="small"
          title={
            <Space>
              <span>因子分析</span>
              {effectiveFactor && <Tag color="blue">{effectiveFactor}</Tag>}
            </Space>
          }
          extra={
            <Space wrap>
              <SampleFilterBar value={sample} onChange={setSample} />
              <Input
                placeholder="股票代码"
                value={stock}
                onChange={(e) => setStock(e.target.value)}
                style={{ width: 120 }}
              />
              <RangePicker
                size="small"
                value={range}
                onChange={(v) => {
                  if (v && v[0] && v[1]) setRange([v[0], v[1]]);
                }}
                allowClear={false}
              />
              <Text type="secondary">horizon</Text>
              <InputNumber
                size="small"
                min={1}
                max={60}
                value={horizon}
                onChange={(v) => v && setHorizon(v)}
                style={{ width: 64 }}
              />
              <Button
                size="small"
                type="primary"
                disabled={!stock}
                onClick={() => nav(`/stock/${stock}`)}
              >
                加入K线
              </Button>
            </Space>
          }
        >
          {!effectiveFactor ? (
            <EmptyState description="请在左侧选择一个因子" />
          ) : (
            <Row gutter={[12, 12]}>
              <Col xs={24} md={12}>
                <ICChart ic={icQ.data} loading={icQ.isLoading} />
              </Col>
              <Col xs={24} md={12}>
                <LayeredReturns
                  layers={icQ.data?.layered_returns ?? null}
                  loading={icQ.isLoading}
                />
              </Col>
              <Col xs={24} md={12}>
                <ICDecayCard
                  factor={effectiveFactor}
                  start={start}
                  end={end}
                  sample={sample}
                />
              </Col>
              <Col xs={24} md={12}>
                <LayerNavCard
                  factor={effectiveFactor}
                  start={start}
                  end={end}
                  sample={sample}
                />
              </Col>
              <Col span={24}>
                <Card size="small" title={`因子值序列（${stock}）`}>
                  {seriesQ.isLoading ? (
                    <Skeleton active paragraph={{ rows: 6 }} />
                  ) : !seriesQ.data || seriesQ.data.length === 0 ? (
                    <Empty description="该区间暂无因子值" style={{ padding: 24 }} />
                  ) : (
                    <Table<FactorSeriesPoint>
                      rowKey="trade_date"
                      dataSource={seriesQ.data}
                      size="small"
                      pagination={{ pageSize: 10, showSizeChanger: false }}
                      columns={seriesColumns}
                    />
                  )}
                </Card>
              </Col>
            </Row>
          )}
        </Card>
      </Col>

      <Col span={24}>
        <MultiFactorScoreCard factors={factorsQ.data ?? []} />
      </Col>
    </Row>
  );
}

const seriesColumns: ColumnsType<FactorSeriesPoint> = [
  { title: '交易日期', dataIndex: 'trade_date', width: 140 },
  {
    title: '因子值',
    dataIndex: 'value',
    align: 'right',
    render: (v: number | null) => (v == null ? '--' : Number(v).toFixed(4)),
  },
];

// Multi-factor weighted scoring panel (POST /factor/score).
function MultiFactorScoreCard({ factors: allFactors }: { factors: FactorBrief[] }) {
  const [factors, setFactors] = useState<{ code: string; weight: number }[]>([
    { code: 'pe', weight: 1 },
  ]);
  const factorOptions = useMemo(
    () =>
      CATEGORY_TREE.map((c) => ({
        label: c.title,
        options: allFactors
          .filter((f) => f.category === c.key)
          .map((f) => ({ value: f.code, label: `${f.name}（${f.code}）` })),
      })).filter((g) => g.options.length > 0),
    [allFactors],
  );
  const [results, setResults] = useState<FactorScoreRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tradeDate, setTradeDate] = useState<Dayjs>(dayjs());
  const [sample, setSample] = useState<SampleFilters>({});
  // V2.2: direction per factor (1 = 值高者优先, -1 = 值低者优先), preset, top_n.
  const [directions, setDirections] = useState<Record<string, number>>({});
  const [preset, setPreset] = useState<string | null>(null);
  const [topN, setTopN] = useState(20);
  const presetsQ = useQuery<FactorPreset[]>({
    queryKey: ['factor', 'presets'],
    queryFn: listFactorPresets,
    staleTime: 60 * 60_000,
  });

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await multiFactorScore(
        factors
          .filter((f) => f.code.trim())
          .map((f) => ({ ...f, direction: directions[f.code] ?? 1 })),
        tradeDate.format('YYYY-MM-DD'),
        sample,
        { top_n: topN, preset: preset ?? undefined },
      );
      setResults(data ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '打分失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card size="small" title="多因子打分（选股）">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap>
          <DatePicker
            value={tradeDate}
            onChange={(v) => v && setTradeDate(v)}
            allowClear={false}
          />
          <Select
            allowClear
            placeholder="研究预设（V2 反转组合）"
            value={preset ?? undefined}
            style={{ minWidth: 180 }}
            options={(presetsQ.data ?? []).map((p) => ({ value: p.name, label: p.title }))}
            onChange={(v) => setPreset(v ?? null)}
          />
          <Text type="secondary">Top</Text>
          <InputNumber
            min={5}
            max={100}
            value={topN}
            onChange={(v) => v && setTopN(v)}
            style={{ width: 64 }}
          />
          <SampleFilterBar value={sample} onChange={setSample} />
          {factors.map((f, i) => (
            <Space.Compact key={i}>
              <Select
                showSearch
                allowClear
                placeholder="选择因子"
                value={f.code || undefined}
                style={{ minWidth: 200 }}
                options={factorOptions}
                optionFilterProp="label"
                onChange={(v) =>
                  setFactors((prev) =>
                    prev.map((x, j) => (j === i ? { ...x, code: v ?? '' } : x)),
                  )
                }
              />
              <InputNumber
                placeholder="权重"
                value={f.weight}
                style={{ width: 80 }}
                onChange={(v) =>
                  setFactors((prev) =>
                    prev.map((x, j) => (j === i ? { ...x, weight: Number(v ?? 1) } : x)),
                  )
                }
              />
              <Select
                value={directions[f.code] ?? 1}
                style={{ width: 76 }}
                options={[
                  { value: 1, label: '↑ 高优' },
                  { value: -1, label: '↓ 低优' },
                ]}
                onChange={(v) =>
                  setDirections((prev) => ({ ...prev, [f.code]: Number(v) }))
                }
              />
              <Button
                onClick={() =>
                  setFactors((prev) => prev.filter((_, j) => j !== i))
                }
              >
                删除
              </Button>
            </Space.Compact>
          ))}
          <Button onClick={() => setFactors((prev) => [...prev, { code: '', weight: 1 }])}>
            + 添加因子
          </Button>
          <Button type="primary" loading={loading} onClick={run}>
            打分
          </Button>
        </Space>

        {error && (
          <Alert type="error" showIcon message={error} closable onClose={() => setError(null)} />
        )}

        {!results ? (
          <EmptyState description="配置因子与权重后点击「打分」获取选股结果" />
        ) : results.length === 0 ? (
          <EmptyState description="未匹配到股票（检查因子代码 / 日期）" />
        ) : (
          <Table<FactorScoreRow>
            rowKey="stock_code"
            dataSource={results}
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: false }}
            columns={[
              {
                title: '排名',
                width: 70,
                render: (_, __, i) => i + 1,
              },
              { title: '代码', dataIndex: 'stock_code', width: 120 },
              {
                title: '综合得分',
                dataIndex: 'score',
                align: 'right',
                sorter: (a, b) => b.score - a.score,
                render: (v: number) => Number(v).toFixed(4),
              },
            ]}
          />
        )}
      </Space>
    </Card>
  );
}

// V2.2 — IC decay across horizons (GET /factor/{code}/ic-series summary).
function ICDecayCard({
  factor,
  start,
  end,
  sample,
}: {
  factor: string | null;
  start: string;
  end: string;
  sample: SampleFilters;
}) {
  const q = useQuery<FactorIcSeries | null>({
    queryKey: ['factor', factor, 'ic-series', start, end, sample],
    queryFn: () =>
      computeFactorICSeries(factor!, start, end, {
        ...sample,
        horizons: '1,5,10,20',
        step: 5,
        universe_size: 500,
      }),
    enabled: !!factor,
    staleTime: 5 * 60_000,
  });

  const summary = q.data?.summary ?? {};
  const horizons = q.data ? q.data.horizons : [];
  const option = useMemo(() => {
    if (!horizons.length) return {};
    return {
      animation: false,
      grid: { left: 56, right: 24, top: 24, bottom: 32 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: number) => (v == null ? '--' : Number(v).toFixed(4)),
      },
      xAxis: { type: 'category', data: horizons.map((h) => `${h}日`) },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#f0f0f0' } },
        axisLabel: { fontSize: 10 },
      },
      series: [
        {
          type: 'bar',
          barWidth: 36,
          data: horizons.map((h) => summary[String(h)]?.mean_ic ?? null),
          label: {
            show: true,
            position: 'top',
            formatter: (p: { value: number | null }) =>
              p.value == null ? '--' : Number(p.value).toFixed(4),
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
  }, [horizons, summary]);

  if (q.isLoading) {
    return (
      <Card size="small" title="IC 衰减（多持有期）" loading>
        <div style={{ height: 260 }} />
      </Card>
    );
  }
  if (!q.data) {
    return (
      <Card size="small" title="IC 衰减（多持有期）">
        <EmptyState description="该因子暂不支持时序 IC（需面板化因子）" />
      </Card>
    );
  }
  const years = Object.keys(q.data.by_year).sort();
  return (
    <Card
      size="small"
      title="IC 衰减（多持有期）"
      extra={
        <span style={{ fontSize: 12, color: '#8c8c8c' }}>
          {q.data.pool} / {q.data.neutralized}
        </span>
      }
    >
      <ReactECharts option={option} notMerge lazyUpdate style={{ height: 220 }} />
      <Table
        rowKey="h"
        size="small"
        pagination={false}
        dataSource={horizons.map((h) => ({ h, ...summary[String(h)] }))}
        columns={[
          { title: '持有期', dataIndex: 'h', width: 70, render: (v: number) => `${v}日` },
          {
            title: '均值IC',
            dataIndex: 'mean_ic',
            align: 'right',
            render: (v: number | null) => (v == null ? '--' : Number(v).toFixed(4)),
          },
          {
            title: 'ICIR',
            dataIndex: 'icir',
            align: 'right',
            render: (v: number | null) => (v == null ? '--' : Number(v).toFixed(3)),
          },
          {
            title: '胜率',
            dataIndex: 'win_rate',
            align: 'right',
            render: (v: number | null) => (v == null ? '--' : `${(Number(v) * 100).toFixed(1)}%`),
          },
          { title: '样本日', dataIndex: 'n_dates', align: 'right' },
        ]}
      />
      {years.length > 1 && (
        <Table
          rowKey="y"
          size="small"
          pagination={false}
          dataSource={years.map((y) => ({ y, ...q.data!.by_year[y] }))}
          columns={[
            { title: '分年IC', dataIndex: 'y', width: 70 },
            ...horizons.map((h) => ({
              title: `${h}日`,
              dataIndex: String(h),
              align: 'right' as const,
              render: (v: number | null) => (v == null ? '--' : Number(v).toFixed(4)),
            })),
          ]}
        />
      )}
    </Card>
  );
}

// V2.2 — quantile NAV backtest (POST /factor/{code}/layered-backtest).
function LayerNavCard({
  factor,
  start,
  end,
  sample,
}: {
  factor: string | null;
  start: string;
  end: string;
  sample: SampleFilters;
}) {
  const [data, setData] = useState<FactorLayeredBacktest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!factor) return;
    setLoading(true);
    setError(null);
    try {
      const res = await layeredBacktest(factor, { start, end, step: 5, ...sample });
      setData(res);
      if (!res) setError('该因子暂不支持分层回测（需面板化因子）');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '分层回测失败');
    } finally {
      setLoading(false);
    }
  };

  const option = useMemo(() => {
    if (!data) return {};
    const dates = data.rebalance_dates;
    const series: {
      name: string;
      type: 'line';
      showSymbol: boolean;
      data: number[];
      lineStyle?: { type: 'dashed' };
    }[] = data.layers.map((L) => ({
      name: `Q${L.layer}`,
      type: 'line' as const,
      showSymbol: false,
      data: L.nav,
    }));
    series.push({
      name: '多空(Q顶-Q底)',
      type: 'line',
      showSymbol: false,
      lineStyle: { type: 'dashed' },
      data: data.long_short.nav,
    });
    return {
      animation: false,
      legend: { top: 0, fontSize: 10 },
      grid: { left: 56, right: 24, top: 32, bottom: 32 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: '#f0f0f0' } },
        axisLabel: { fontSize: 10 },
      },
      series,
    };
  }, [data]);

  return (
    <Card
      size="small"
      title="分层回测（五分位净值）"
      extra={
        <Button size="small" type="primary" loading={loading} onClick={run}>
          运行
        </Button>
      }
    >
      {error && <Alert type="warning" showIcon message={error} style={{ marginBottom: 8 }} />}
      {!data ? (
        <EmptyState description="点击「运行」对该因子做五分位分层回测（等权持有5日轮动）" />
      ) : (
        <>
          <ReactECharts option={option} notMerge lazyUpdate style={{ height: 240 }} />
          <Table
            rowKey="layer"
            size="small"
            pagination={false}
            dataSource={data.layers}
            columns={[
              { title: '分层', dataIndex: 'layer', width: 60, render: (v: number) => `Q${v}` },
              {
                title: '累计收益',
                dataIndex: 'total_return',
                align: 'right',
                render: (v: number | null) =>
                  v == null ? '--' : `${(Number(v) * 100).toFixed(1)}%`,
              },
              {
                title: '年化',
                dataIndex: 'ann_return',
                align: 'right',
                render: (v: number | null) =>
                  v == null ? '--' : `${(Number(v) * 100).toFixed(1)}%`,
              },
              {
                title: '最大回撤',
                dataIndex: 'max_drawdown',
                align: 'right',
                render: (v: number | null) =>
                  v == null ? '--' : `${(Number(v) * 100).toFixed(1)}%`,
              },
              {
                title: '均持股数',
                dataIndex: 'avg_count',
                align: 'right',
                render: (v: number | null) => (v == null ? '--' : Number(v).toFixed(0)),
              },
            ]}
          />
        </>
      )}
    </Card>
  );
}
