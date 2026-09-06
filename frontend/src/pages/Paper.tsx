import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import ReactECharts from 'echarts-for-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createPaperAccount,
  getPaperAccount,
  getPaperDrift,
  getPaperNav,
  getPaperOrders,
  listPaperAccounts,
  setPaperAccountStatus,
  tickPaperAccount,
} from '../api/paper';
import type {
  PaperAccountBrief,
  PaperAccountDetail,
  PaperDriftResponse,
  PaperNavResponse,
  PaperOrder,
  PaperPosition,
} from '../api/paper';
import {
  DOWN_COLOR,
  UP_COLOR,
  colorForChange,
  fmtMoney,
  fmtPct,
  fmtPrice,
} from '../utils/format';
import EmptyState from '../components/EmptyState';

const { RangePicker } = DatePicker;
const { Text } = Typography;

// V3a T3.5 (BP-V3a-005) — 模拟盘页面：账户生命周期（新建/暂停/恢复/终止）、
// 净值 vs 基准、持仓与调仓历史、模拟盘 vs 回测周收益漂移、手动补跑（paper_tick）。
// 后端接口由后续波次落地：所有请求 try/catch 降级为空态，404 / 空数据不崩溃。

const STATUS_LABEL: Record<string, { color: string; label: string }> = {
  running: { color: 'green', label: '运行中' },
  paused: { color: 'orange', label: '已暂停' },
  stopped: { color: 'default', label: '已终止' },
};

function statusInfo(s?: string | null) {
  return STATUS_LABEL[s ?? ''] ?? { color: 'default', label: s ?? '--' };
}

// 调仓单状态：filled 绿 / pending 灰 / skipped 橙 / deferred 红 / cancelled 灰。
const ORDER_STATUS_COLOR: Record<string, string> = {
  filled: 'green',
  pending: 'default',
  skipped: 'orange',
  deferred: 'red',
  cancelled: 'default',
};

const fmtNum = (v: number | null | undefined, digits = 2) =>
  v == null || isNaN(Number(v)) ? '--' : Number(v).toFixed(digits);

// 列表降级用的稳定空引用（避免每次渲染新建 [] 触发 effect 重跑）。
const NO_ACCOUNTS: PaperAccountBrief[] = [];

// 契约中的收益率/权重均为小数（0.05 = 5%），展示时 ×100。
const fmtPctFrac = (v: number | null | undefined) =>
  fmtPct(v == null ? null : v * 100);

export default function Paper() {
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState<number | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  // 列表接口未就绪（404）时降级为空列表，页面渲染引导空态。
  const accountsQ = useQuery({
    queryKey: ['paper', 'accounts'],
    queryFn: async (): Promise<PaperAccountBrief[]> => {
      try {
        const r = await listPaperAccounts();
        return r?.accounts ?? [];
      } catch {
        return [];
      }
    },
  });

  const detailQ = useQuery({
    queryKey: ['paper', 'account', activeId],
    queryFn: async (): Promise<PaperAccountDetail | null> => {
      try {
        return await getPaperAccount(activeId!);
      } catch {
        return null;
      }
    },
    enabled: activeId != null,
  });

  const accounts = accountsQ.data ?? NO_ACCOUNTS;
  const account: PaperAccountBrief | null =
    accounts.find((a) => a.id === activeId) ?? detailQ.data?.account ?? null;
  const positions = detailQ.data?.positions ?? [];
  const alerts = detailQ.data?.alerts ?? [];

  // 默认选中第一个账户。
  useEffect(() => {
    if (activeId == null && accounts.length > 0) setActiveId(accounts[0].id);
  }, [accounts, activeId]);

  const statusMut = useMutation({
    mutationFn: (p: { id: number; action: 'pause' | 'resume' | 'stop' }) =>
      setPaperAccountStatus(p.id, p.action),
    onSuccess: (_r, v) => {
      message.success(
        v.action === 'pause' ? '已暂停' : v.action === 'resume' ? '已恢复' : '已终止',
      );
      qc.invalidateQueries({ queryKey: ['paper'] });
    },
    onError: (e: unknown) =>
      message.error(e instanceof Error ? e.message : '操作失败'),
  });

  const st = statusInfo(account?.status);
  const openAlerts =
    account?.open_alerts ??
    alerts.filter((a) => a.status === 'fail' || a.status === 'warn').length;

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card
          title="模拟盘账户"
          extra={
            <Space wrap>
              <Select
                style={{ minWidth: 220 }}
                placeholder="选择账户"
                value={activeId ?? undefined}
                showSearch
                optionFilterProp="label"
                options={accounts.map((a) => ({
                  value: a.id,
                  label: `${a.name}（${statusInfo(a.status).label}）`,
                }))}
                onChange={(v) => setActiveId(v)}
              />
              <Button type="primary" onClick={() => setCreateOpen(true)}>
                + 新建账户
              </Button>
              {account && (
                <Space size={6}>
                  <Tag color={st.color}>{st.label}</Tag>
                  {account.status !== 'stopped' && (
                    <>
                      <Popconfirm
                        title={
                          account.status === 'running'
                            ? '确认暂停该账户？暂停期间不再生成新信号'
                            : '确认恢复该账户？'
                        }
                        onConfirm={() =>
                          statusMut.mutate({
                            id: account.id,
                            action: account.status === 'running' ? 'pause' : 'resume',
                          })
                        }
                      >
                        <Button size="small" loading={statusMut.isPending}>
                          {account.status === 'running' ? '暂停' : '恢复'}
                        </Button>
                      </Popconfirm>
                      <Popconfirm
                        title="确认终止该账户？终止后不可恢复"
                        onConfirm={() =>
                          statusMut.mutate({ id: account.id, action: 'stop' })
                        }
                      >
                        <Button size="small" danger loading={statusMut.isPending}>
                          终止
                        </Button>
                      </Popconfirm>
                    </>
                  )}
                </Space>
              )}
            </Space>
          }
        >
          {accountsQ.isLoading ? (
            <Skeleton active paragraph={{ rows: 3 }} />
          ) : !account ? (
            <EmptyState description="尚无模拟盘账户（或接口未就绪），点击右上角「新建账户」创建第一个账户" />
          ) : (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Row gutter={[12, 12]}>
                <Col xs={12} sm={8} md={4}>
                  <Card size="small">
                    <Statistic
                      title="最新净值"
                      value={fmtNum(account.latest_nav, 4)}
                      valueStyle={{ fontSize: 18 }}
                    />
                  </Card>
                </Col>
                <Col xs={12} sm={8} md={4}>
                  <Card size="small">
                    <Statistic
                      title="累计收益"
                      value={fmtPctFrac(account.cum_ret)}
                      valueStyle={{
                        fontSize: 18,
                        color: colorForChange(account.cum_ret ?? null),
                      }}
                    />
                  </Card>
                </Col>
                <Col xs={12} sm={8} md={4}>
                  <Card size="small">
                    <Statistic
                      title="最大回撤"
                      value={fmtPctFrac(account.drawdown)}
                      valueStyle={{ fontSize: 18 }}
                    />
                  </Card>
                </Col>
                <Col xs={12} sm={8} md={4}>
                  <Card size="small">
                    <Statistic
                      title="现金余额"
                      value={fmtMoney(account.cash ?? null)}
                      valueStyle={{ fontSize: 18 }}
                    />
                  </Card>
                </Col>
                <Col xs={12} sm={8} md={4}>
                  <Card size="small">
                    <Statistic
                      title="持仓数"
                      value={positions.length}
                      valueStyle={{ fontSize: 18 }}
                    />
                  </Card>
                </Col>
                <Col xs={12} sm={8} md={4}>
                  <Card size="small">
                    <Statistic
                      title={
                        <span>
                          未恢复告警
                          {openAlerts > 0 && (
                            <span
                              style={{
                                display: 'inline-block',
                                width: 8,
                                height: 8,
                                borderRadius: 4,
                                background: '#f5222d',
                                marginLeft: 6,
                                verticalAlign: 'middle',
                              }}
                            />
                          )}
                        </span>
                      }
                      value={openAlerts}
                      valueStyle={{
                        fontSize: 18,
                        color: openAlerts > 0 ? '#cf1322' : undefined,
                      }}
                    />
                  </Card>
                </Col>
              </Row>
              {alerts.length > 0 && (
                <Space wrap size={6}>
                  {alerts.map((a, i) => (
                    <Tag
                      key={i}
                      color={
                        a.status === 'fail'
                          ? 'red'
                          : a.status === 'warn'
                            ? 'orange'
                            : 'green'
                      }
                    >
                      {a.metric}：{fmtNum(a.value, 4)}（{a.check_date ?? '--'}）
                    </Tag>
                  ))}
                </Space>
              )}
            </Space>
          )}
        </Card>
      </Col>

      {activeId != null && (
        <>
          <Col span={24}>
            <NavCard id={activeId} />
          </Col>
          <Col span={24}>
            <Card size="small" title="当前持仓">
              {detailQ.isLoading ? (
                <Skeleton active paragraph={{ rows: 3 }} />
              ) : positions.length === 0 ? (
                <EmptyState description="暂无持仓（首次调仓前为空仓，或接口未就绪）" />
              ) : (
                <Table<PaperPosition>
                  rowKey="stock_code"
                  size="small"
                  pagination={false}
                  columns={positionColumns}
                  dataSource={positions}
                />
              )}
            </Card>
          </Col>
          <Col span={24}>
            <OrdersCard id={activeId} />
          </Col>
          <Col span={24}>
            <DriftCard id={activeId} />
          </Col>
          <Col span={24}>
            <TickCard id={activeId} />
          </Col>
        </>
      )}

      <CreateAccountModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => {
          setCreateOpen(false);
          setActiveId(id);
          qc.invalidateQueries({ queryKey: ['paper'] });
        }}
      />
    </Row>
  );
}

// ---- 净值曲线（nav vs benchmark_nav） ----
function NavCard({ id }: { id: number }) {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(1, 'year'),
    dayjs(),
  ]);
  const start = range[0].format('YYYY-MM-DD');
  const end = range[1].format('YYYY-MM-DD');

  const q = useQuery({
    queryKey: ['paper', 'account', id, 'nav', start, end],
    queryFn: async (): Promise<PaperNavResponse | null> => {
      try {
        return await getPaperNav(id, start, end);
      } catch {
        return null;
      }
    },
  });

  const series = q.data?.series ?? [];
  const hasNav = series.length > 0 && series.some((p) => p.nav != null);
  const option = useMemo(() => buildNavOption(q.data), [q.data]);

  const attr = q.data?.attribution;
  const attrParts: string[] = [];
  if (attr?.excess != null) attrParts.push(`超额 ${fmtPctFrac(attr.excess)}`);
  if (attr?.cost_drag != null) attrParts.push(`成本拖累 ${fmtPctFrac(attr.cost_drag)}`);
  if (attr?.cash_drag != null) attrParts.push(`现金拖累 ${fmtPctFrac(attr.cash_drag)}`);
  if (attr?.selection != null) attrParts.push(`选股贡献 ${fmtPctFrac(attr.selection)}`);

  return (
    <Card
      size="small"
      title="净值曲线"
      extra={
        <RangePicker
          size="small"
          value={range}
          onChange={(v) => {
            if (v && v[0] && v[1]) setRange([v[0], v[1]]);
          }}
          allowClear={false}
        />
      }
    >
      {q.isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : !hasNav ? (
        <EmptyState description="暂无净值数据（账户尚未推进或接口未就绪）" />
      ) : (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <ReactECharts
            option={option}
            notMerge
            lazyUpdate
            style={{ height: 300 }}
          />
          {attrParts.length > 0 && <Text type="secondary">{attrParts.join(' · ')}</Text>}
        </Space>
      )}
    </Card>
  );
}

function buildNavOption(res: PaperNavResponse | null | undefined) {
  const series = res?.series ?? [];
  if (!series.length) return {};
  return {
    animation: false,
    legend: { top: 0, fontSize: 10 },
    grid: { left: 64, right: 24, top: 32, bottom: 40 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: series.map((p) => p.trade_date),
      axisLabel: { fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        name: '模拟盘净值',
        type: 'line',
        showSymbol: false,
        data: series.map((p) => p.nav),
      },
      {
        name: '基准',
        type: 'line',
        showSymbol: false,
        lineStyle: { type: 'dashed' },
        data: series.map((p) => p.benchmark_nav),
      },
    ],
  };
}

// ---- 持仓 ----
const positionColumns: ColumnsType<PaperPosition> = [
  {
    title: '股票',
    dataIndex: 'stock_code',
    width: 110,
    render: (v: string) => <Tag color="blue">{v}</Tag>,
  },
  {
    title: '股数',
    dataIndex: 'shares',
    width: 100,
    align: 'right',
    render: (v: number | null) => fmtNum(v, 0),
  },
  {
    title: '现价',
    dataIndex: 'close_price',
    width: 90,
    align: 'right',
    render: (v: number | null) => fmtPrice(v),
  },
  {
    title: '市值',
    dataIndex: 'market_value',
    width: 110,
    align: 'right',
    render: (v: number | null) => fmtMoney(v),
  },
  {
    title: '权重',
    dataIndex: 'weight',
    width: 90,
    align: 'right',
    render: (v: number | null) => fmtPctFrac(v),
  },
  {
    title: '浮动盈亏',
    dataIndex: 'pnl_pct',
    width: 100,
    align: 'right',
    render: (v: number | null | undefined) =>
      v == null ? (
        '--'
      ) : (
        <span style={{ color: colorForChange(v) }}>{fmtPctFrac(v)}</span>
      ),
  },
];

// ---- 调仓历史 ----
function OrdersCard({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ['paper', 'account', id, 'orders'],
    queryFn: async (): Promise<PaperOrder[]> => {
      try {
        const r = await getPaperOrders(id);
        return r?.orders ?? [];
      } catch {
        return [];
      }
    },
  });

  const orders = q.data ?? [];

  const columns: ColumnsType<PaperOrder> = [
    {
      title: '信号日',
      dataIndex: 'signal_date',
      width: 100,
      render: (v: string | null) => v ?? '--',
    },
    {
      title: '执行日',
      dataIndex: 'exec_date',
      width: 100,
      render: (v: string | null) => v ?? '--',
    },
    {
      title: '股票',
      dataIndex: 'stock_code',
      width: 100,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '方向',
      dataIndex: 'side',
      width: 70,
      render: (v: string) =>
        v === 'buy' ? (
          <Tag color="red">买入</Tag>
        ) : v === 'sell' ? (
          <Tag color="green">卖出</Tag>
        ) : (
          v
        ),
    },
    {
      title: '股数',
      dataIndex: 'shares',
      width: 90,
      align: 'right',
      render: (v: number | null | undefined) => fmtNum(v, 0),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (
        <Tag color={ORDER_STATUS_COLOR[v] ?? 'default'}>{v}</Tag>
      ),
    },
    {
      title: '失败原因',
      dataIndex: 'fail_reason',
      render: (v: string | null | undefined) => v ?? '--',
    },
  ];

  return (
    <Card size="small" title="调仓历史">
      {q.isLoading ? (
        <Table
          loading
          columns={columns}
          dataSource={[]}
          rowKey={(r) => `${r.signal_date}-${r.stock_code}-${r.side}`}
        />
      ) : orders.length === 0 ? (
        <EmptyState description="暂无调仓记录（账户尚未触发首次调仓，或接口未就绪）" />
      ) : (
        <Table<PaperOrder>
          rowKey={(r, i) => `${r.signal_date ?? ''}-${r.exec_date ?? ''}-${r.stock_code}-${r.side}-${i}`}
          size="small"
          dataSource={orders}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          columns={columns}
          expandable={{
            expandedRowRender: (rec) =>
              rec.trade ? (
                <Space split="·" size={4} wrap>
                  <span>价格 {fmtPrice(rec.trade.price)}</span>
                  <span>金额 {fmtMoney(rec.trade.amount)}</span>
                  <span>佣金 ¥{fmtNum(rec.trade.commission)}</span>
                  <span>印花税 ¥{fmtNum(rec.trade.stamp_duty)}</span>
                  <span>过户费 ¥{fmtNum(rec.trade.transfer_fee)}</span>
                </Space>
              ) : (
                <Text type="secondary">无成交明细</Text>
              ),
          }}
        />
      )}
    </Card>
  );
}

// ---- 模拟盘 vs 回测漂移 ----
function DriftCard({ id }: { id: number }) {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(90, 'day'),
    dayjs(),
  ]);
  const [res, setRes] = useState<PaperDriftResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await getPaperDrift(
        id,
        range[0].format('YYYY-MM-DD'),
        range[1].format('YYYY-MM-DD'),
      );
      setRes(r);
    } catch {
      setRes(null);
      setError('漂移对比接口未就绪，或该区间无数据');
    } finally {
      setLoading(false);
    }
  };

  const option = useMemo(() => {
    const weekly = res?.weekly ?? [];
    if (!weekly.length) return {};
    return {
      animation: false,
      grid: { left: 64, right: 16, top: 24, bottom: 40 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: weekly.map((w) => w.week),
        axisLabel: { fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          fontSize: 10,
          formatter: (v: number) => `${(v * 100).toFixed(1)}%`,
        },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      series: [
        {
          name: '周收益差（模拟-回测）',
          type: 'bar',
          data: weekly.map((w) => ({
            value: w.diff,
            itemStyle: { color: (w.diff ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR },
          })),
        },
      ],
    };
  }, [res]);

  return (
    <Card
      size="small"
      title="模拟盘 vs 回测漂移"
      extra={
        <Space>
          <RangePicker
            size="small"
            value={range}
            onChange={(v) => {
              if (v && v[0] && v[1]) setRange([v[0], v[1]]);
            }}
            allowClear={false}
          />
          <Button size="small" type="primary" loading={loading} onClick={run}>
            计算漂移
          </Button>
        </Space>
      }
    >
      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 12 }}
        />
      )}
      {!res ? (
        <EmptyState description="选择区间（默认近 90 天）点击「计算漂移」，对比模拟盘与同期回测的周收益差" />
      ) : (res.weekly ?? []).length === 0 ? (
        <EmptyState description="该区间暂无漂移数据" />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            <Col xs={12} sm={8}>
              <Statistic
                title="模拟盘累计"
                value={fmtPctFrac(res.cum_paper)}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col xs={12} sm={8}>
              <Statistic
                title="回测累计"
                value={fmtPctFrac(res.cum_backtest)}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
            <Col xs={12} sm={8}>
              <Statistic
                title="最大周偏差"
                value={fmtPctFrac(res.max_abs_weekly)}
                valueStyle={{ fontSize: 16 }}
              />
            </Col>
          </Row>
          <ReactECharts option={option} notMerge lazyUpdate style={{ height: 260 }} />
        </Space>
      )}
    </Card>
  );
}

// ---- 手动补跑（paper_tick） ----
function TickCard({ id }: { id: number }) {
  const [datesText, setDatesText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const raw = datesText.trim();
    const dates = raw
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter((s) => /^\d{4}-\d{2}-\d{2}$/.test(s));
    if (raw && dates.length === 0) {
      message.warning('日期格式应为 yyyy-mm-dd，多个用逗号分隔');
      return;
    }
    setSubmitting(true);
    try {
      await tickPaperAccount(id, dates.length ? dates : undefined);
      message.success('补跑已提交，稍后刷新查看净值与调仓结果');
      setDatesText('');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '补跑提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card size="small" title="手动补跑（paper_tick）">
      <Space wrap>
        <Input
          style={{ width: 360 }}
          placeholder="可选：指定日期，逗号分隔，如 2026-08-31,2026-09-01"
          value={datesText}
          onChange={(e) => setDatesText(e.target.value)}
          onPressEnter={submit}
        />
        <Popconfirm
          title={datesText.trim() ? '确认补跑所选日期？' : '确认推进到最新交易日？'}
          onConfirm={submit}
        >
          <Button size="small" type="primary" loading={submitting}>
            提交补跑
          </Button>
        </Popconfirm>
        <Text type="secondary">
          留空则按最新交易日推进；用于停牌顺延 / 数据修复后的补数据
        </Text>
      </Space>
    </Card>
  );
}

// ---- 新建账户 ----
function CreateAccountModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    try {
      const vals = await form.validateFields();
      setSubmitting(true);
      const created = await createPaperAccount({
        name: vals.name,
        config: {
          preset: vals.preset,
          top_n: vals.top_n,
          freq:
            vals.freq === 'W' || vals.freq === 'M' ? vals.freq : Number(vals.freq),
          initial_cash: vals.initial_cash,
        },
      });
      form.resetFields();
      onCreated(created.id);
    } catch (e: unknown) {
      if (e instanceof Error) {
        setError(
          /404/.test(e.message)
            ? '模拟盘接口未就绪（404），请稍后重试'
            : e.message,
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="新建模拟盘账户"
      open={open}
      onCancel={onClose}
      onOk={submit}
      okButtonProps={{ loading: submitting }}
      destroyOnHidden
      width={520}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          name: '',
          preset: 'v2_reversal',
          top_n: 10,
          freq: 'W',
          initial_cash: 1000000,
        }}
      >
        <Form.Item
          label="账户名称"
          name="name"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="如：反转周频一号" />
        </Form.Item>
        <Form.Item label="因子预设" name="preset">
          <Select options={[{ value: 'v2_reversal', label: 'v2_reversal' }]} />
        </Form.Item>
        <Space wrap size={12}>
          <Form.Item label="Top N 持仓" name="top_n">
            <InputNumber min={5} max={50} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item label="调仓频率" name="freq">
            <Select
              style={{ width: 140 }}
              options={[
                { value: 'W', label: '每周调仓' },
                { value: 'M', label: '每月调仓' },
                { value: '5', label: '每5日' },
                { value: '10', label: '每10日' },
              ]}
            />
          </Form.Item>
          <Form.Item label="初始资金" name="initial_cash">
            <InputNumber min={10000} step={100000} style={{ width: 160 }} />
          </Form.Item>
        </Space>
      </Form>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} />}
    </Modal>
  );
}
