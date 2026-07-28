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
import { getIndicators, getKline, getStockInfo } from '../api/stock';
import type {
  IndicatorMap,
  KDJRow,
  KLineItem,
  MACDRow,
  MARow,
  StockInfo,
} from '../api/types';
import { colorForChange, fmtMoney, fmtPct, fmtPrice } from '../utils/format';
import EmptyState from '../components/EmptyState';
import KLineChart from '../components/KLineChart';

const { RangePicker } = DatePicker;
const { Title, Text } = Typography;

const DEFAULT_DAYS = 365;

// H2 — 股票详情 + KLine chart.
export default function StockDetail() {
  const { code = '' } = useParams();
  const nav = useNavigate();
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(DEFAULT_DAYS, 'day'),
    dayjs(),
  ]);
  const [activeIndicator, setActiveIndicator] = useState<'macd' | 'kdj'>('macd');

  const start = range[0].format('YYYY-MM-DD');
  const end = range[1].format('YYYY-MM-DD');

  const infoQ = useQuery<StockInfo>({
    queryKey: ['stock', code],
    queryFn: () => getStockInfo(code),
    enabled: !!code,
  });

  const klineQ = useQuery<KLineItem[]>({
    queryKey: ['stock', code, 'kline', start, end],
    queryFn: () => getKline(code, start, end),
    enabled: !!code,
  });

  // Three indicator series share the kline window.
  const maQ = useQuery<MARow[]>({
    queryKey: ['stock', code, 'indicators', 'ma', start, end],
    queryFn: () => getIndicators(code, 'ma', start, end),
    enabled: !!code,
  });
  const macdQ = useQuery<MACDRow[]>({
    queryKey: ['stock', code, 'indicators', 'macd', start, end],
    queryFn: () => getIndicators(code, 'macd', start, end),
    enabled: !!code,
  });
  const kdjQ = useQuery<KDJRow[]>({
    queryKey: ['stock', code, 'indicators', 'kdj', start, end],
    queryFn: () => getIndicators(code, 'kdj', start, end),
    enabled: !!code,
  });

  const indicators: IndicatorMap = useMemo(
    () => ({
      ma: maQ.data,
      macd: macdQ.data,
      kdj: kdjQ.data,
    }),
    [maQ.data, macdQ.data, kdjQ.data],
  );

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
          title="日K / 成交量 / 技术指标"
          extra={
            <Space wrap>
              <RangePicker
                value={range}
                onChange={(v) => {
                  if (v && v[0] && v[1]) setRange([v[0], v[1]]);
                }}
                allowClear={false}
              />
              <Segmented
                size="small"
                value={activeIndicator}
                onChange={(v) => setActiveIndicator(v as 'macd' | 'kdj')}
                options={[
                  { label: 'MACD', value: 'macd' },
                  { label: 'KDJ', value: 'kdj' },
                ]}
              />
            </Space>
          }
        >
          {klineQ.isLoading ? (
            <Skeleton active paragraph={{ rows: 8 }} />
          ) : !klineQ.data || klineQ.data.length === 0 ? (
            <EmptyState description="该区间暂无K线数据" />
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
    </Row>
  );
}
