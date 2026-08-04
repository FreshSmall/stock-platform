import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import { getBacktest, submitBacktest } from '../api/backtest';
import { listStrategies } from '../api/strategy';
import {
  getDrawdownCurve,
  getPositionCurve,
  type DrawdownPoint,
  type PositionPoint,
} from '../api/backtest';
import { colorForChange, fmtMoney, fmtPct } from '../utils/format';
import EmptyState from '../components/EmptyState';
import DrawdownChart from '../components/DrawdownChart';
import PositionChart from '../components/PositionChart';
import EquityVsBenchmark from '../components/EquityVsBenchmark';

const { RangePicker } = DatePicker;
const { Text } = Typography;

// Benchmark options for the V2 equity-vs-benchmark comparison.
const BENCHMARK_OPTIONS = [
  { label: '上证指数 (sh000001)', value: 'sh000001' },
  { label: '深证成指 (sz399001)', value: 'sz399001' },
  { label: '沪深300 ETF (510300)', value: '510300' },
  { label: '创业板指 (sz399006)', value: 'sz399006' },
];

// Strategy metadata (mirrors /strategy response + backend registry).
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

interface EquityPoint {
  date: string;
  equity: number;
}
interface TradeRow {
  entry_date?: string | null;
  exit_date?: string | null;
  price?: number | null;
  size?: number | null;
  pnl?: number | null;
  bars?: number | null;
}
interface BacktestMetrics {
  return_rate?: number | null;
  max_drawdown?: number | null;
  sharpe?: number | null;
  win_rate?: number | null;
  // V2 advanced metrics
  calmar?: number | null;
  information_ratio?: number | null;
  profit_loss_ratio?: number | null;
  benchmark_return?: number | null;
}
interface BacktestResult {
  run_id: string;
  status: string;
  strategy?: string;
  error?: string;
  metrics?: BacktestMetrics;
  equity_curve?: EquityPoint[];
  benchmark_curve?: EquityPoint[];
  trades?: TradeRow[];
}

// H5 — 回测配置与结果.
//
// Left column is the config form; the right column shows the run result.
// Submit calls POST /backtest (synchronous in V1) then polls GET /backtest/:id
// via React Query refetchInterval while the status is pending/running.
// When the run is done we render metric cards + an equity-curve line chart
// (ECharts) + a trades table. A run that produces zero trades surfaces a hint
// to adjust parameters.
export default function Backtest() {
  const [params] = useSearchParams();
  const initialStrategy = params.get('strategy') || undefined;

  const strategiesQ = useQuery<StrategyMeta[]>({
    queryKey: ['strategies'],
    queryFn: listStrategies,
  });
  const strategies = useMemo(
    () => (strategiesQ.data ?? []).filter((s) => s.available),
    [strategiesQ.data],
  );

  const [form] = Form.useForm();
  const [selectedStrategy, setSelectedStrategy] = useState<string | undefined>(
    initialStrategy,
  );
  const [stockPool, setStockPool] = useState<string[]>([]);
  const [stockInput, setStockInput] = useState('');

  // Active run — set on submit, drives the polling query below.
  const [runId, setRunId] = useState<string | null>(null);

  // When strategies load, default the selector to ?strategy= (validated) or the
  // first available strategy.
  useEffect(() => {
    if (strategies.length === 0 || selectedStrategy) return;
    const valid =
      initialStrategy &&
      strategies.some((s) => s.name === initialStrategy);
    const next = valid ? initialStrategy! : strategies[0].name;
    setSelectedStrategy(next);
    form.setFieldValue('strategy', next);
  }, [strategies, selectedStrategy, initialStrategy, form]);

  const currentMeta = useMemo(
    () => strategies.find((s) => s.name === selectedStrategy),
    [strategies, selectedStrategy],
  );

  // Defensive poll: V1 submit is synchronous, but re-fetch once after the POST
  // returns and keep polling while the status is pending/running.
  const resultQ = useQuery<BacktestResult>({
    queryKey: ['backtest', runId],
    queryFn: () => getBacktest(runId!),
    enabled: !!runId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s && s !== 'pending' && s !== 'running' ? false : 1500;
    },
  });

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const lastParamsRef = useRef<Record<string, number>>({});

  const addStock = () => {
    const code = stockInput.trim();
    if (!code) return;
    if (stockPool.includes(code)) {
      setStockInput('');
      return;
    }
    setStockPool((prev) => [...prev, code]);
    setStockInput('');
  };

  const onFinish = async (vals: Record<string, any>) => {
    setSubmitError(null);
    if (!selectedStrategy) {
      setSubmitError('请选择策略');
      return;
    }
    if (stockPool.length === 0) {
      setSubmitError('请至少添加一只股票');
      return;
    }
    if (!vals.range || !vals.range[0] || !vals.range[1]) {
      setSubmitError('请选择回测区间');
      return;
    }

    // Collect only the strategy param fields (named after param.name), skip the
    // fixed config keys.
    const paramFieldNames = new Set(
      (currentMeta?.params ?? []).map((p) => p.name),
    );
    const strategyParams: Record<string, number> = {};
    for (const [k, v] of Object.entries(vals)) {
      if (paramFieldNames.has(k)) strategyParams[k] = Number(v);
    }
    lastParamsRef.current = strategyParams;

    // V2: benchmark is a run-level param. The backend pops it out of `params`
    // before handing them to the backtrader Strategy (see backtest_service).
    if (vals.benchmark) strategyParams.benchmark = vals.benchmark;

    const req = {
      strategy: selectedStrategy,
      params: strategyParams,
      stock_pool: stockPool,
      start_date: vals.range[0].format('YYYY-MM-DD'),
      end_date: vals.range[1].format('YYYY-MM-DD'),
      initial_cash: Number(vals.initial_cash),
      commission: Number(vals.commission),
      slippage: Number(vals.slippage),
    };

    setSubmitting(true);
    try {
      const { run_id } = await submitBacktest(req);
      setRunId(run_id);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : '提交回测失败');
    } finally {
      setSubmitting(false);
    }
  };


  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={10}>
        <Card title="回测配置">
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              initial_cash: 100000,
              commission: 0.0003,
              slippage: 0.0001,
              benchmark: 'sh000001',
              range: [dayjs().subtract(1, 'year'), dayjs()] as [Dayjs, Dayjs],
            }}
            onValuesChange={(changed) => {
              if ('strategy' in changed && changed.strategy) {
                setSelectedStrategy(changed.strategy);
              }
            }}
            onFinish={onFinish}
          >
            <Form.Item label="策略" name="strategy" rules={[{ required: true }]}>
              <Select
                placeholder="选择策略"
                loading={strategiesQ.isLoading}
                options={strategies.map((s) => ({
                  label: s.title,
                  value: s.name,
                }))}
              />
            </Form.Item>

            {/* Dynamic per-strategy param fields, driven by the strategy's
                params metadata (e.g. ma → fast/slow, macd → 3 periods). */}
            {currentMeta &&
              currentMeta.params.map((p) => (
                <Form.Item
                  key={p.name}
                  label={`${p.description || p.name}`}
                  name={p.name}
                  initialValue={Number(p.default)}
                  rules={[{ required: true }]}
                >
                  <InputNumber
                    style={{ width: '100%' }}
                    min={p.min}
                    max={p.max}
                  />
                </Form.Item>
              ))}

            <Form.Item label="基准" name="benchmark">
              <Select
                placeholder="选择基准"
                allowClear
                options={BENCHMARK_OPTIONS}
              />
            </Form.Item>

            <Form.Item label="股票池" required>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="输入股票代码，如 600519"
                  value={stockInput}
                  onChange={(e) => setStockInput(e.target.value)}
                  onPressEnter={addStock}
                />
                <Button onClick={addStock}>添加</Button>
              </Space.Compact>
              {stockPool.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  {stockPool.map((code) => (
                    <Tag
                      key={code}
                      closable
                      onClose={() =>
                        setStockPool((prev) => prev.filter((c) => c !== code))
                      }
                    >
                      {code}
                    </Tag>
                  ))}
                </div>
              )}
            </Form.Item>

            <Form.Item
              label="回测区间"
              name="range"
              rules={[{ required: true, message: '请选择回测区间' }]}
            >
              <RangePicker style={{ width: '100%' }} allowClear={false} />
            </Form.Item>

            <Row gutter={8}>
              <Col span={12}>
                <Form.Item
                  label="初始资金"
                  name="initial_cash"
                  rules={[{ required: true }]}
                >
                  <InputNumber style={{ width: '100%' }} min={1000} step={10000} />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="佣金率" name="commission">
                  <InputNumber style={{ width: '100%' }} min={0} step={0.0001} />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="滑点" name="slippage">
                  <InputNumber style={{ width: '100%' }} min={0} step={0.0001} />
                </Form.Item>
              </Col>
            </Row>

            {submitError && (
              <Alert
                type="error"
                showIcon
                message={submitError}
                style={{ marginBottom: 12 }}
                closable
                onClose={() => setSubmitError(null)}
              />
            )}

            <Button
              type="primary"
              htmlType="submit"
              block
              loading={submitting}
              disabled={!selectedStrategy || stockPool.length === 0}
            >
              开始回测
            </Button>
          </Form>
        </Card>
      </Col>

      <Col xs={24} lg={14}>
        <Card title="回测结果">
          {!runId && !submitting && (
            <EmptyState description="配置参数后点击「开始回测」查看结果" />
          )}
          {(submitting || (resultQ.isFetching && isPending(resultQ.data))) && (
            <div style={{ textAlign: 'center', padding: 48 }}>
              <Spin size="large" />
              <div style={{ marginTop: 16 }}>
                <Text type="secondary">正在运行回测…</Text>
              </div>
            </div>
          )}
          {resultQ.data && !isPending(resultQ.data) && (
            <ResultView result={resultQ.data} />
          )}
        </Card>
      </Col>
    </Row>
  );
}

// V2 — fetch the drawdown + position curves for a finished run. Lifted into a
// dedicated component so we can hook useQuery on the run_id.
function AdvancedCharts({ runId }: { runId: string }) {
  const ddQ = useQuery<DrawdownPoint[] | null>({
    queryKey: ['backtest', runId, 'drawdown'],
    queryFn: () => getDrawdownCurve(runId),
  });
  const posQ = useQuery<PositionPoint[] | null>({
    queryKey: ['backtest', runId, 'positions'],
    queryFn: () => getPositionCurve(runId),
  });
  return (
    <Row gutter={[12, 12]}>
      <Col xs={24} lg={12}>
        <DrawdownChart data={ddQ.data ?? null} loading={ddQ.isLoading} />
      </Col>
      <Col xs={24} lg={12}>
        <PositionChart data={posQ.data ?? null} loading={posQ.isLoading} />
      </Col>
    </Row>
  );
}

function ResultView({ result }: { result: BacktestResult }) {
  if (result.status === 'failed' || result.error) {
    return (
      <Alert
        type="error"
        showIcon
        message="回测失败"
        description={result.error || '执行过程中发生错误'}
      />
    );
  }
  if (result.status !== 'done') {
    return (
      <div style={{ textAlign: 'center', padding: 32 }}>
        <Spin />
        <div style={{ marginTop: 12 }}>
          <Text type="secondary">状态：{result.status}</Text>
        </div>
      </div>
    );
  }

  const metrics = result.metrics ?? {};
  const trades = result.trades ?? [];
  const hasTrades = trades.length > 0;
  const rr = metrics.return_rate ?? null;
  const benchmarkCurve = result.benchmark_curve ?? [];
  const hasBenchmark = benchmarkCurve.length > 0;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {/* Headline metrics */}
      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="收益率"
              value={fmtPct(rr)}
              valueStyle={{ color: colorForChange(rr) }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="最大回撤"
              value={fmtPct(metrics.max_drawdown)}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="夏普比率"
              value={metrics.sharpe != null ? Number(metrics.sharpe).toFixed(2) : '--'}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="胜率" value={fmtPct(metrics.win_rate)} />
          </Card>
        </Col>
      </Row>

      {/* V2 advanced metrics */}
      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="卡玛比率"
              value={metrics.calmar != null ? Number(metrics.calmar).toFixed(2) : '--'}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="信息比率"
              value={
                metrics.information_ratio != null
                  ? Number(metrics.information_ratio).toFixed(2)
                  : '--'
              }
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="盈亏比"
              value={
                metrics.profit_loss_ratio != null
                  ? Number(metrics.profit_loss_ratio).toFixed(2)
                  : '--'
              }
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="基准收益"
              value={fmtPct(metrics.benchmark_return)}
              valueStyle={{ color: colorForChange(metrics.benchmark_return) }}
            />
          </Card>
        </Col>
      </Row>

      {!hasTrades && (
        <Alert
          type="warning"
          showIcon
          message="当前参数下未产生交易信号，请调整参数"
        />
      )}

      {/* V2: strategy vs benchmark (replaces the standalone equity chart when a
          benchmark curve is present; otherwise show the strategy-only curve). */}
      {result.equity_curve && result.equity_curve.length > 0 ? (
        hasBenchmark ? (
          <EquityVsBenchmark
            equity={result.equity_curve}
            benchmark={benchmarkCurve}
            benchmarkName="基准"
          />
        ) : (
          <Card size="small" title="净值曲线">
            <EquityChart curve={result.equity_curve} />
          </Card>
        )
      ) : (
        <Card size="small" title="净值曲线">
          <EmptyState description="无净值数据" />
        </Card>
      )}

      {/* V2: drawdown + position charts */}
      <AdvancedCharts runId={result.run_id} />

      <Card size="small" title="交易明细">
        {hasTrades ? (
          <Table<TradeRow>
            rowKey={(_, i) => String(i)}
            dataSource={trades}
            size="small"
            pagination={{ pageSize: 8, showSizeChanger: false }}
            columns={tradeColumns}
          />
        ) : (
          <EmptyState description="无交易明细" />
        )}
      </Card>
    </Space>
  );
}

function EquityChart({ curve }: { curve: EquityPoint[] }) {
  const option = useMemo(
    () => ({
      animation: false,
      grid: { left: 56, right: 16, top: 24, bottom: 32 },
      tooltip: {
        trigger: 'axis',
        formatter: (ps: any[]) => {
          if (!ps.length) return '';
          const p = ps[0];
          return `${p.axisValue}<br/>净值 ${fmtMoney(p.value)}`;
        },
      },
      xAxis: {
        type: 'category',
        data: curve.map((p) => p.date),
        axisLabel: { fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 10 },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      series: [
        {
          name: '净值',
          type: 'line',
          smooth: true,
          symbol: 'none',
          data: curve.map((p) => p.equity),
          lineStyle: { width: 2, color: '#1677ff' },
          areaStyle: { opacity: 0.08 },
        },
      ],
    }),
    [curve],
  );
  return <ReactECharts option={option} notMerge lazyUpdate style={{ height: 280 }} />;
}

const tradeColumns: ColumnsType<TradeRow> = [
  {
    title: '开仓日期',
    dataIndex: 'entry_date',
    width: 110,
    render: (v) => v ?? '--',
  },
  {
    title: '平仓日期',
    dataIndex: 'exit_date',
    width: 110,
    render: (v) => v ?? '--',
  },
  {
    title: '方向',
    dataIndex: 'size',
    width: 70,
    render: (v: number | null) =>
      v == null ? (
        '--'
      ) : (
        <Tag color={v >= 0 ? 'red' : 'green'}>{v >= 0 ? '多' : '空'}</Tag>
      ),
  },
  {
    title: '成交价',
    dataIndex: 'price',
    align: 'right',
    render: (v: number | null) => (v != null ? Number(v).toFixed(2) : '--'),
  },
  {
    title: '数量',
    dataIndex: 'size',
    align: 'right',
    render: (v: number | null) => (v != null ? Math.abs(v) : '--'),
  },
  {
    title: '持仓天数',
    dataIndex: 'bars',
    align: 'right',
    render: (v: number | null) => (v != null ? v : '--'),
  },
  {
    title: '盈亏',
    dataIndex: 'pnl',
    align: 'right',
    render: (v: number | null) =>
      v == null ? (
        '--'
      ) : (
        <span style={{ color: colorForChange(v) }}>{v.toFixed(2)}</span>
      ),
  },
];

function isPending(result?: BacktestResult | null): boolean {
  return !!result && (result.status === 'pending' || result.status === 'running');
}
