import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
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
import ReactMarkdown from 'react-markdown';
import { useQuery } from '@tanstack/react-query';
import { generateReport, getReport, listReports } from '../api/reports';
import type { AgentReport, ReportAgent } from '../api/types';
import EmptyState from '../components/EmptyState';
import RiskNotice from '../components/RiskNotice';

const { Text } = Typography;

// N3 — AI 报告页.
//
// 4 tabs (sector / market / review / recommend). Each tab lists reports of
// that agent; clicking a row opens the report body (markdown). A manual
// generate button triggers POST /reports/{agent}/generate.
const TABS: { key: ReportAgent; label: string }[] = [
  { key: 'sector', label: '板块分析' },
  { key: 'market', label: '大盘研判' },
  { key: 'review', label: '每日复盘' },
  { key: 'recommend', label: '股票推荐' },
];

export default function Reports() {
  const [activeTab, setActiveTab] = useState<ReportAgent>('sector');
  const [activeReportId, setActiveReportId] = useState<number | null>(null);
  const [filterDate, setFilterDate] = useState<Dayjs | null>(null);

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
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
                  filterDate={filterDate}
                  setFilterDate={setFilterDate}
                  onOpen={(id) => setActiveReportId(id)}
                />
              ),
            }))}
          />
        </Card>
      </Col>

      <Col span={24}>
        {activeReportId && (
          <ReportDetail id={activeReportId} onClose={() => setActiveReportId(null)} />
        )}
      </Col>
    </Row>
  );
}

function ReportListPane({
  agent,
  filterDate,
  setFilterDate,
  onOpen,
}: {
  agent: ReportAgent;
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
          renderItem={(r) => (
            <List.Item
              onClick={() => onOpen(r.id)}
              style={{ cursor: 'pointer' }}
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
                  <Text type="secondary" ellipsis style={{ maxWidth: 480 }}>
                    {r.summary || '（无摘要）'}
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

function ReportDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery<AgentReport | null>({
    queryKey: ['report', id],
    queryFn: () => getReport(id),
  });

  return (
    <Card
      title={
        <Space>
          <span>{q.data?.title ?? '报告正文'}</span>
          {q.data?.agent && <Tag color="blue">{q.data.agent}</Tag>}
          {q.data?.trade_date && <Tag>{q.data.trade_date}</Tag>}
        </Space>
      }
      extra={<Button onClick={onClose}>关闭</Button>}
    >
      {q.isLoading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : !q.data ? (
        <EmptyState description="报告不存在" />
      ) : q.data.content ? (
        <div className="assistant-md">
          <ReactMarkdown>{q.data.content}</ReactMarkdown>
        </div>
      ) : (
        <Empty description="该报告无正文（可能生成失败）" />
      )}
      <Text type="secondary" style={{ display: 'block', marginTop: 16, fontSize: 12 }}>
        <RiskNotice />
      </Text>
    </Card>
  );
}
