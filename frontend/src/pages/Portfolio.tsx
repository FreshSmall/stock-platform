import { useMemo, useState } from 'react';
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
  Row,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import dayjs from 'dayjs';
import ReactECharts from 'echarts-for-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  backtestPortfolio,
  createPortfolio,
  deletePortfolio,
  getPortfolio,
  listPortfolios,
} from '../api/portfolio';
import type { Portfolio, PortfolioHolding } from '../api/types';
import { colorForChange, fmtPct } from '../utils/format';
import EmptyState from '../components/EmptyState';

const { RangePicker } = DatePicker;
const { Text } = Typography;

// N3 — 组合管理页.
//
// Three regions: a portfolio list (cards) on top, a create-portfolio modal,
// and a detail drawer/section with NAV curve, drawdown stat, and holdings.
export default function Portfolio() {
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState<number | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const listQ = useQuery<Portfolio[]>({
    queryKey: ['portfolios'],
    queryFn: listPortfolios,
  });

  const removeMut = useMutation({
    mutationFn: (id: number) => deletePortfolio(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolios'] });
      setActiveId(null);
    },
  });

  const portfolios = listQ.data ?? [];

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card
          title="我的组合"
          extra={
            <Button type="primary" onClick={() => setCreateOpen(true)}>
              + 创建组合
            </Button>
          }
        >
          {listQ.isLoading ? (
            <Skeleton active paragraph={{ rows: 3 }} />
          ) : portfolios.length === 0 ? (
            <EmptyState description="还没有组合，点击右上角创建" />
          ) : (
            <Row gutter={[16, 16]}>
              {portfolios.map((p) => (
                <Col key={p.id} xs={24} sm={12} lg={8}>
                  <PortfolioCard
                    portfolio={p}
                    active={p.id === activeId}
                    onSelect={() => setActiveId(p.id)}
                    onDelete={() => removeMut.mutate(p.id)}
                  />
                </Col>
              ))}
            </Row>
          )}
        </Card>
      </Col>

      {activeId && (
        <Col span={24}>
          <PortfolioDetail id={activeId} />
        </Col>
      )}

      <CreatePortfolioModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(p) => {
          setCreateOpen(false);
          qc.invalidateQueries({ queryKey: ['portfolios'] });
          setActiveId(p.id);
        }}
      />
    </Row>
  );
}

function PortfolioCard({
  portfolio,
  active,
  onSelect,
  onDelete,
}: {
  portfolio: Portfolio;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <Card
      size="small"
      hoverable
      onClick={onSelect}
      style={{
        cursor: 'pointer',
        borderColor: active ? '#1677ff' : undefined,
        boxShadow: active ? '0 0 0 2px rgba(22,119,255,0.15)' : undefined,
      }}
      title={
        <Space>
          <span>{portfolio.name}</span>
          {portfolio.benchmark && <Tag>{portfolio.benchmark}</Tag>}
        </Space>
      }
    >
      <Statistic
        title="持仓数"
        value={portfolio.holdings?.length ?? 0}
        valueStyle={{ fontSize: 20 }}
      />
      {portfolio.description && (
        <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
          {portfolio.description}
        </Text>
      )}
      <div style={{ marginTop: 8, textAlign: 'right' }}>
        <Button
          size="small"
          danger
          type="link"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          删除
        </Button>
      </div>
    </Card>
  );
}

function PortfolioDetail({ id }: { id: number }) {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(1, 'year'),
    dayjs(),
  ]);
  const start = range[0].format('YYYY-MM-DD');
  const end = range[1].format('YYYY-MM-DD');

  const portQ = useQuery<Portfolio | null>({
    queryKey: ['portfolio', id],
    queryFn: () => getPortfolio(id),
  });

  // Backtest (NAV) is run on demand via the button.
  const [btKey, setBtKey] = useState(0);
  const btQ = useQuery({
    queryKey: ['portfolio', id, 'backtest', start, end, btKey],
    queryFn: () => backtestPortfolio(id, { start, end }),
    enabled: btKey > 0,
  });

  const navCurve = btQ.data?.nav_curve ?? [];
  const hasNav = navCurve.length > 0;
  const ret = btQ.data?.return_rate ?? null;
  const maxDd = btQ.data?.max_drawdown ?? null;

  // Derive the option from the stable query result reference, not the derived
  // `navCurve` array (rebuilt every render).
  const navOption = useMemo(() => buildNavOption(btQ.data?.nav_curve ?? []), [btQ.data]);

  const portfolio = portQ.data;

  return (
    <Card
      title={
        <Space>
          <span>组合详情</span>
          {portfolio?.name && <Tag color="blue">{portfolio.name}</Tag>}
        </Space>
      }
      extra={
        <Space wrap>
          <RangePicker
            size="small"
            value={range}
            onChange={(v) => {
              if (v && v[0] && v[1]) setRange([v[0], v[1]]);
            }}
            allowClear={false}
          />
          <Button
            type="primary"
            size="small"
            loading={btQ.isFetching}
            onClick={() => setBtKey((k) => k + 1)}
          >
            回测
          </Button>
        </Space>
      }
    >
      {portQ.isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : !portfolio ? (
        <EmptyState description="组合不存在或已删除" />
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic
                title="区间收益"
                value={fmtPct(ret)}
                valueStyle={{ color: colorForChange(ret) }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic
                title="最大回撤"
                value={fmtPct(maxDd)}
                valueStyle={{ color: '#52c41a' }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic title="持仓数" value={portfolio.holdings?.length ?? 0} />
            </Card>
          </Col>

          <Col span={24}>
            <Card size="small" title="净值曲线">
              {!hasNav ? (
                <EmptyState description="点击「回测」生成净值曲线" />
              ) : (
                <ReactECharts
                  option={navOption}
                  notMerge
                  lazyUpdate
                  style={{ height: 300 }}
                />
              )}
            </Card>
          </Col>

          <Col span={24}>
            <Card size="small" title="持仓">
              <Table<PortfolioHolding>
                rowKey="stock_code"
                dataSource={portfolio.holdings ?? []}
                size="small"
                pagination={false}
                columns={holdingColumns}
              />
            </Card>
          </Col>
        </Row>
      )}
    </Card>
  );
}

function buildNavOption(curve: { date: string; nav: number }[]) {
  if (!curve.length) return {};
  return {
    animation: false,
    grid: { left: 56, right: 16, top: 16, bottom: 32 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: curve.map((p) => p.date),
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
        name: '净值',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: curve.map((p) => p.nav),
        lineStyle: { width: 2, color: '#1677ff' },
        areaStyle: { opacity: 0.08 },
      },
    ],
  };
}

const holdingColumns: ColumnsType<PortfolioHolding> = [
  { title: '代码', dataIndex: 'stock_code', width: 120 },
  {
    title: '权重',
    dataIndex: 'weight',
    align: 'right',
    render: (v: number | null) => fmtPct((v ?? 0) * 100),
  },
];

// Create-portfolio modal: name + holdings (stock_code + weight) rows.
function CreatePortfolioModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (p: Portfolio) => void;
}) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    try {
      const vals = await form.validateFields();
      const holdings: PortfolioHolding[] = (vals.holdings ?? [])
        .filter((h: { stock_code?: string; weight?: number }) => h?.stock_code?.trim())
        .map((h: { stock_code: string; weight: number }) => ({
          stock_code: h.stock_code.trim(),
          weight: Number(h.weight ?? 1),
        }));
      if (holdings.length === 0) {
        setError('请至少添加一只持仓');
        return;
      }
      setSubmitting(true);
      const created = await createPortfolio({
        name: vals.name,
        description: vals.description,
        benchmark: vals.benchmark || 'sh000001',
        holdings,
      });
      onCreated(created);
      form.resetFields();
    } catch (e: unknown) {
      if (e instanceof Error) setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="创建组合"
      open={open}
      onCancel={onClose}
      onOk={submit}
      okButtonProps={{ loading: submitting }}
      destroyOnHidden
      width={560}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          name: '',
          benchmark: 'sh000001',
          holdings: [{ stock_code: '', weight: 1 }],
        }}
      >
        <Form.Item label="组合名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="如：核心资产" />
        </Form.Item>
        <Form.Item label="基准" name="benchmark">
          <Input placeholder="sh000001" />
        </Form.Item>
        <Form.Item label="描述" name="description">
          <Input.TextArea rows={2} placeholder="可选" />
        </Form.Item>
        <Form.Item label="持仓（股票代码 + 权重，权重自动归一）">
          <Form.List name="holdings">
            {(fields, { add, remove }) => (
              <>
                {fields.map((f) => (
                  <Space key={f.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item name={[f.name, 'stock_code']} noStyle>
                      <Input placeholder="股票代码" style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item name={[f.name, 'weight']} noStyle>
                      <InputNumber placeholder="权重" min={0} step={0.1} style={{ width: 100 }} />
                    </Form.Item>
                    <Button onClick={() => remove(f.name)}>删除</Button>
                  </Space>
                ))}
                <Button type="dashed" block onClick={() => add({ stock_code: '', weight: 1 })}>
                  + 添加持仓
                </Button>
              </>
            )}
          </Form.List>
        </Form.Item>
      </Form>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} />}
    </Modal>
  );
}
