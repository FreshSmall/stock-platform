import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Drawer,
  Empty,
  Grid,
  Input,
  List,
  Row,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { Dayjs } from 'dayjs';
import { useQuery } from '@tanstack/react-query';
import { generateReport, getReport, listReports } from '../api/reports';
import type { AgentReport, ReportAgent } from '../api/types';
import EmptyState from '../components/EmptyState';
import Markdown from '../components/Markdown';
import RiskNotice from '../components/RiskNotice';

const { Text } = Typography;

// N3 — AI 报告页.
//
// 4 tabs (sector / market / review / recommend). Wide screens keep the list on
// the left and render the selected report beside it, so the detail is visible
// without scrolling to the bottom of the page; narrow screens open the report
// in a Drawer. Clicking a row opens the report body (markdown with GFM
// tables). A manual generate button triggers POST /reports/{agent}/generate.
const TABS: { key: ReportAgent; label: string }[] = [
  { key: 'sector', label: '板块分析' },
  { key: 'market', label: '大盘研判' },
  { key: 'review', label: '每日复盘' },
  { key: 'recommend', label: '股票推荐' },
];

// Strip common markdown markers (headings, bold, table pipes) so the list
// preview reads as plain text instead of raw syntax.
function previewText(text: string): string {
  return text
    .replace(/\|/g, ' ')
    .replace(/[#*`>]/g, '')
    .replace(/-{2,}/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function useReport(id: number) {
  return useQuery<AgentReport | null>({
    queryKey: ['report', id],
    queryFn: () => getReport(id),
  });
}

export default function Reports() {
  const [activeTab, setActiveTab] = useState<ReportAgent>('sector');
  const [activeReportId, setActiveReportId] = useState<number | null>(null);
  const [filterDate, setFilterDate] = useState<Dayjs | null>(null);
  const isWide = Grid.useBreakpoint().lg ?? false;

  const listCard = (
    <Card>
      <Tabs
        activeKey={activeTab}
        onChange={(k) => {
          setActiveTab(k as ReportAgent);
          setActiveReportId(null);
        }}
        items={TABS.map((t) => ({
          key: t.key,
          label: t.label,
          children: (
            <ReportListPane
              agent={t.key}
              activeId={activeReportId}
              filterDate={filterDate}
              setFilterDate={setFilterDate}
              onOpen={setActiveReportId}
            />
          ),
        }))}
      />
    </Card>
  );

  if (isWide) {
    return (
      <Row gutter={[16, 16]}>
        <Col span={10} xl={8}>
          {listCard}
        </Col>
        <Col span={14} xl={16}>
          {activeReportId ? (
            <ReportDetailCard id={activeReportId} onClose={() => setActiveReportId(null)} />
          ) : (
            <Card>
              <EmptyState description="在左侧选择报告，正文将在这里展示" />
            </Card>
          )}
        </Col>
      </Row>
    );
  }

  return (
    <>
      {listCard}
      <Drawer
        open={activeReportId !== null}
        onClose={() => setActiveReportId(null)}
        width="min(92vw, 720px)"
        title={activeReportId ? <ReportDetailTitle id={activeReportId} /> : '报告正文'}
      >
        {activeReportId && <ReportDetailBody id={activeReportId} />}
      </Drawer>
    </>
  );
}

function ReportListPane({
  agent,
  activeId,
  filterDate,
  setFilterDate,
  onOpen,
}: {
  agent: ReportAgent;
  activeId: number | null;
  filterDate: Dayjs | null;
  setFilterDate: (d: Dayjs | null) => void;
  onOpen: (id: number) => void;
}) {
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [target, setTarget] = useState('');

  const dateStr = filterDate ? filterDate.format('YYYY-MM-DD') : undefined;
  const q = useQuery<AgentReport[]>({
    queryKey: ['reports', agent, dateStr],
    queryFn: () => listReports(agent, dateStr),
  });

  const generate = async () => {
    setGenError(null);
    setGenerating(true);
    try {
      await generateReport(agent, target.trim() || undefined);
      q.refetch();
    } catch (e: unknown) {
      setGenError(e instanceof Error ? e.message : '生成失败');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div>
      <Space wrap style={{ marginBottom: 16 }}>
        <DatePicker
          placeholder="按日期过滤"
          value={filterDate}
          onChange={setFilterDate}
          allowClear
        />
        {agent === 'recommend' || agent === 'sector' ? (
          <Input
            placeholder={agent === 'sector' ? '板块名称（可选）' : '股票代码（可选）'}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            style={{ width: 180 }}
          />
        ) : null}
        <Button type="primary" loading={generating} onClick={generate}>
          手动生成
        </Button>
      </Space>

      {genError && (
        <Alert
          type="error"
          showIcon
          message={genError}
          closable
          onClose={() => setGenError(null)}
          style={{ marginBottom: 12 }}
        />
      )}

      {q.isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : !q.data || q.data.length === 0 ? (
        <EmptyState description={`暂无${TABS.find((t) => t.key === agent)?.label}报告`} />
      ) : (
        <List
          dataSource={q.data}
          rowKey={(r) => r.id}
          renderItem={(r) => (
            <List.Item
              onClick={() => onOpen(r.id)}
              style={{
                cursor: 'pointer',
                borderRadius: 8,
                padding: '12px',
                transition: 'background 0.2s',
                ...(r.id === activeId
                  ? { background: '#e6f4ff', boxShadow: 'inset 3px 0 0 #1677ff' }
                  : {}),
              }}
              extra={
                <Space direction="vertical" align="end" size={0}>
                  {r.trade_date && <Tag>{r.trade_date}</Tag>}
                  {r.target && <Text type="secondary" style={{ fontSize: 12 }}>{r.target}</Text>}
                </Space>
              }
            >
              <List.Item.Meta
                title={r.title || `${agent} 报告 #${r.id}`}
                description={
                  <Text type="secondary" ellipsis style={{ display: 'block', maxWidth: '100%' }}>
                    {r.summary ? previewText(r.summary) : '（无摘要）'}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
}

function ReportDetailTitle({ id }: { id: number }) {
  const { data } = useReport(id);

  return (
    <Space wrap>
      <span>{data?.title ?? '报告正文'}</span>
      {data?.agent && <Tag color="blue">{data.agent}</Tag>}
      {data?.trade_date && <Tag>{data.trade_date}</Tag>}
    </Space>
  );
}

function ReportDetailBody({ id }: { id: number }) {
  const q = useReport(id);

  if (q.isLoading) {
    return <Skeleton active paragraph={{ rows: 8 }} />;
  }
  if (!q.data) {
    return <EmptyState description="报告不存在" />;
  }
  if (!q.data.content) {
    return <Empty description="该报告无正文（可能生成失败）" />;
  }
  return (
    <>
      <div className="assistant-md">
        <Markdown>{q.data.content}</Markdown>
      </div>
      <Text type="secondary" style={{ display: 'block', marginTop: 16, fontSize: 12 }}>
        <RiskNotice />
      </Text>
    </>
  );
}

function ReportDetailCard({ id, onClose }: { id: number; onClose: () => void }) {
  return (
    <Card
      title={<ReportDetailTitle id={id} />}
      extra={<Button onClick={onClose}>关闭</Button>}
      styles={{ body: { maxHeight: 'calc(100vh - 220px)', overflowY: 'auto' } }}
    >
      <ReportDetailBody id={id} />
    </Card>
  );
}
