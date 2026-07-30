import { useMemo, useState } from 'react';
import {
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Row,
  Segmented,
  Skeleton,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd';
import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  fetchChipDistribution,
  fetchMinute,
  fetchMoneyFlowDetail,
  getIndicators,
  getKline,
  getStockInfo,
} from '../api/stock';
import type {
  BOLLRow,
  ChipDistribution as Chip,
  EMARow,
  IndicatorMap,
  KDJRow,
  KLineItem,
  MACDRow,
  MARow,
  MinutePeriod,
  MoneyFlowDetailRow,
  RSIRow,
  StockInfo,
} from '../api/types';
import { colorForChange, fmtMoney, fmtPct, fmtPrice } from '../utils/format';
import EmptyState from '../components/EmptyState';
import KLineChart from '../components/KLineChart';
import type { IndicatorKey } from '../components/KLineChart';
import ChipDistribution from '../components/ChipDistribution';
import MoneyFlowChart from '../components/MoneyFlowChart';

const { RangePicker } = DatePicker;
const { Title, Text } = Typography;

const DEFAULT_DAYS = 365;

type Period = 'd' | 'w' | 'm' | MinutePeriod;

const PERIOD_OPTIONS: { label: string; value: Period }[] = [
  { label: '日K', value: 'd' },
  { label: '周K', value: 'w' },
  { label: '月K', value: 'm' },
  { label: '1分', value: '1' },
  { label: '5分', value: '5' },
  { label: '15分', value: '15' },
  { label: '30分', value: '30' },
  { label: '60分', value: '60' },
];

const INDICATOR_OPTIONS: { label: string; value: IndicatorKey }[] = [
  { label: 'MACD', value: 'macd' },
  { label: 'KDJ', value: 'kdj' },
  { label: 'EMA', value: 'ema' },
  { label: 'RSI', value: 'rsi' },
  { label: 'BOLL', value: 'boll' },
];

// H2 + V1.5 — 股票详情 + KLine chart (multi-period, more indicators, chip/flow).
export default function StockDetail() {
  const { code = '' } = useParams();
  const nav = useNavigate();
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(DEFAULT_DAYS, 'day'),
    dayjs(),
  ]);
  const [period, setPeriod] = useState<Period>('d');
  const [activeIndicator, setActiveIndicator] = useState<IndicatorKey>('macd');

  const start = range[0].format('YYYY-MM-DD');
  const end = range[1].format('YYYY-MM-DD');
  const isMinute = ['1', '5', '15', '30', '60'].includes(period);

  const infoQ = useQuery<StockInfo>({
    queryKey: ['stock', code],
    queryFn: () => getStockInfo(code),
    enabled: !!code,
  });

  // K-line: daily/weekly/monthly use the date range; minute ignores it.
  const klineQ = useQuery<KLineItem[]>({
    queryKey: ['stock', code, 'kline', isMinute ? 'minute' : period, isMinute ? period : start, isMinute ? undefined : end],
    queryFn: () =>
      isMinute
        ? fetchMinute(code, period as MinutePeriod)
        : getKline(code, start, end, period as 'd' | 'w' | 'm'),
    enabled: !!code,
  });

  // Indicators share the daily kline window (only meaningful for d/w/m).
  const indicatorEnabled = !!code && !isMinute;
  const maQ = useQuery<MARow[]>({
    queryKey: ['stock', code, 'indicators', 'ma', start, end],
    queryFn: () => getIndicators(code, 'ma', start, end),
    enabled: indicatorEnabled,
  });
  const macdQ = useQuery<MACDRow[]>({
    queryKey: ['stock', code, 'indicators', 'macd', start, end],
    queryFn: () => getIndicators(code, 'macd', start, end),
    enabled: indicatorEnabled,
  });
  const kdjQ = useQuery<KDJRow[]>({
    queryKey: ['stock', code, 'indicators', 'kdj', start, end],
    queryFn: () => getIndicators(code, 'kdj', start, end),
    enabled: indicatorEnabled,
  });
  const emaQ = useQuery<EMARow[]>({
    queryKey: ['stock', code, 'indicators', 'ema', start, end],
    queryFn: () => getIndicators(code, 'ema', start, end),
    enabled: indicatorEnabled,
  });
  const rsiQ = useQuery<RSIRow[]>({
    queryKey: ['stock', code, 'indicators', 'rsi', start, end],
    queryFn: () => getIndicators(code, 'rsi', start, end),
    enabled: indicatorEnabled,
  });
  const bollQ = useQuery<BOLLRow[]>({
    queryKey: ['stock', code, 'indicators', 'boll', start, end],
    queryFn: () => getIndicators(code, 'boll', start, end),
    enabled: indicatorEnabled,
  });

  const indicators: IndicatorMap = useMemo(
    () => ({
      ma: maQ.data,
      macd: macdQ.data,
      kdj: kdjQ.data,
      ema: emaQ.data,
      rsi: rsiQ.data,
      boll: bollQ.data,
    }),
    [maQ.data, macdQ.data, kdjQ.data, emaQ.data, rsiQ.data, bollQ.data],
  );

  // V1.5 — chip distribution + money flow.
  const chipQ = useQuery<Chip | null>({
    queryKey: ['stock', code, 'chip'],
    queryFn: () => fetchChipDistribution(code),
    enabled: !!code,
  });
  const flowQ = useQuery<MoneyFlowDetailRow[]>({
    queryKey: ['stock', code, 'money-flow'],
    queryFn: () => fetchMoneyFlowDetail(code, 30),
    enabled: !!code,
  });

  // Unknown stock: backend returns data: null with a "stock not found" msg.
  if (!infoQ.isLoading && infoQ.data == null) {
    return (
      <Card>
        <EmptyState description={`未找到股票 ${code}`} />
        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <Button onClick={() => nav('/market')}>返回行情</Button>
        </div>
      </Card>
    );
  }

  const info = infoQ.data;
  const pct = info?.pct_change ?? null;
  const close = info?.close ?? null;
  const changeAmt =
    close != null && pct != null ? close - close / (1 + pct / 100) : null;

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card loading={infoQ.isLoading && !info}>
          {info && (
            <Space align="start" size="large" wrap>
              <div>
                <Title level={3} style={{ margin: 0 }}>
                  {info.stock_name ?? '--'}{' '}
                  <Text type="secondary" copyable style={{ fontSize: 14 }}>
                    {info.stock_code}
                  </Text>
                </Title>
                {info.exchange && <Tag>{info.exchange}</Tag>}
                {info.industry && <Tag color="blue">{info.industry}</Tag>}
              </div>
              <Statistic
                title="现价"
                value={fmtPrice(close)}
                valueStyle={{ color: colorForChange(pct), fontSize: 28 }}
              />
              <div>
                <div style={{ color: colorForChange(pct) }}>
                  {fmtPct(pct)}{' '}
                  {changeAmt != null && (
                    <span style={{ fontSize: 12 }}>
                      ({changeAmt >= 0 ? '+' : ''}
                      {changeAmt.toFixed(2)})
                    </span>
                  )}
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  涨跌幅
                </Text>
              </div>
              <Button type="primary" onClick={() => nav(`/analysis/${code}`)}>
                AI 分析
              </Button>
            </Space>
          )}
        </Card>
      </Col>

      <Col xs={24} lg={18}>
        <Card
          title="K线 / 成交量 / 技术指标"
          extra={
            <Space wrap>
              <Segmented
                size="small"
                value={period}
                onChange={(v) => setPeriod(v as Period)}
                options={PERIOD_OPTIONS}
              />
              {!isMinute && (
                <RangePicker
                  value={range}
                  onChange={(v) => {
                    if (v && v[0] && v[1]) setRange([v[0], v[1]]);
                  }}
                  allowClear={false}
                />
              )}
              <Segmented
                size="small"
                value={activeIndicator}
                onChange={(v) => setActiveIndicator(v as IndicatorKey)}
                options={INDICATOR_OPTIONS}
              />
            </Space>
          }
        >
          {klineQ.isLoading ? (
            <Skeleton active paragraph={{ rows: 8 }} />
          ) : !klineQ.data || klineQ.data.length === 0 ? (
            <EmptyState description={isMinute ? '暂无分时数据' : '该区间暂无K线数据'} />
          ) : (
            <KLineChart
              kline={klineQ.data}
              indicators={indicators}
              activeIndicator={activeIndicator}
            />
          )}
        </Card>
      </Col>

      <Col xs={24} lg={6}>
        <Card title="基本面">
          <Skeleton loading={infoQ.isLoading && !info} active paragraph={{ rows: 3 }}>
            {info && (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="市盈率 PE">
                  {info.pe != null ? Number(info.pe).toFixed(2) : '--'}
                </Descriptions.Item>
                <Descriptions.Item label="市净率 PB">
                  {info.pb != null ? Number(info.pb).toFixed(2) : '--'}
                </Descriptions.Item>
                <Descriptions.Item label="总市值">
                  {fmtMoney(info.total_mv)}
                </Descriptions.Item>
                <Descriptions.Item label="流通市值">
                  {fmtMoney(info.circ_mv)}
                </Descriptions.Item>
                <Descriptions.Item label="上市日期">
                  {info.list_date ?? '--'}
                </Descriptions.Item>
              </Descriptions>
            )}
          </Skeleton>
        </Card>
      </Col>

      <Col xs={24} lg={12}>
        <ChipDistribution data={chipQ.data} loading={chipQ.isLoading} />
      </Col>

      <Col xs={24} lg={12}>
        <MoneyFlowChart rows={flowQ.data} loading={flowQ.isLoading} />
      </Col>
    </Row>
  );
}
