import { useCallback, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Row,
  Space,
  Statistic,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import { getStockInfo } from '../api/stock';
import {
  getLatestAnalysis,
  streamAnalysis,
  triggerAnalysis,
} from '../api/analysis';
import type { AnalysisResult, StockInfo } from '../api/types';
import { colorForChange, fmtPct, fmtPrice } from '../utils/format';
import EmptyState from '../components/EmptyState';
import RiskNotice from '../components/RiskNotice';
import ScoreRadar from '../components/ScoreRadar';

const { Title, Text } = Typography;

type SectionKey = 'fundamentals' | 'technicals' | 'capital' | 'news' | 'risk';

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: 'fundamentals', label: '基本面' },
  { key: 'technicals', label: '技术面' },
  { key: 'capital', label: '资金面' },
  { key: 'news', label: '消息面' },
  { key: 'risk', label: '风险' },
];

function levelFor(score: number | null): { label: string; color: string } {
  if (score == null) return { label: '暂无评分', color: '#8c8c8c' };
  if (score >= 80) return { label: '强烈关注', color: '#f5222d' };
  if (score >= 60) return { label: '值得关注', color: '#fa8c16' };
  if (score >= 40) return { label: '中性观察', color: '#1677ff' };
  return { label: '谨慎', color: '#52c41a' };
}

// H3 — AI 分析页.
export default function Analysis() {
  const { code = '' } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  const infoQ = useQuery<StockInfo>({
    queryKey: ['stock', code],
    queryFn: () => getStockInfo(code),
    enabled: !!code,
  });
  const latestQ = useQuery<AnalysisResult | null>({
    queryKey: ['analysis', code, 'latest'],
    queryFn: () => getLatestAnalysis(code),
    enabled: !!code,
  });

  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [status, setStatus] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  // The latest result drives score/radar/sections; stream only adds a live
  // "thinking" transcript. After 'done' we refetch latest to pull the
  // persisted full text sections (the LLM may return the whole payload in
  // 'done' with empty chunks in dev — see task notes).
  const result = latestQ.data;
  const score = result?.score ?? null;
  const level = levelFor(score);

  const runAnalysis = useCallback(async () => {
    setError(null);
    setStreamText('');
    setStatus('正在触发分析…');
    setStreaming(true);
    try {
      const { request_id } = await triggerAnalysis(code);
      setStatus(`正在分析 ${infoQ.data?.stock_name ?? code}…`);
      await streamAnalysis(
        code,
        request_id,
        (evt: { type: string; data?: unknown }) => {
          const t = evt.type;
          const d = evt.data;
          if (t === 'context') {
            setStatus(`正在分析 ${infoQ.data?.stock_name ?? code}…`);
          } else if (t === 'chunk') {
            if (typeof d === 'string') {
              setStreamText((prev) => prev + d);
              setStatus('正在生成分析内容…');
            }
          } else if (t === 'done') {
            setStatus('分析完成，正在加载结果…');
            // Refetch the persisted full result (sections + scores).
            qc.invalidateQueries({ queryKey: ['analysis', code, 'latest'] });
          } else if (t === 'disclaimer') {
            // Footer disclaimer already rendered globally; nothing to do.
          } else if (t === 'error') {
            setError(typeof d === 'string' ? d : '分析失败');
          }
        },
        (e: unknown) => {
          const msg = e instanceof Error ? e.message : String(e);
          setError(`分析流式中断: ${msg}`);
        },
      );
    } catch (e: unknown) {
      // 429 / per-stock cooldown land here.
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg || '分析请求失败，请稍后重试');
    } finally {
      setStreaming(false);
      setStatus('');
    }
  }, [code, infoQ.data?.stock_name, qc]);

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
  const hasSections =
    !!result && SECTIONS.some((s) => result[s.key]);

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card loading={infoQ.isLoading && !info}>
          {info && (
            <Space align="center" size="large" wrap>
              <Title level={4} style={{ margin: 0 }}>
                {info.stock_name ?? '--'}{' '}
                <Text type="secondary" style={{ fontSize: 14 }}>
                  {info.stock_code}
                </Text>
              </Title>
              <Statistic
                title="现价"
                value={fmtPrice(info.close)}
                valueStyle={{ color: colorForChange(pct), fontSize: 22 }}
              />
              <Text style={{ color: colorForChange(pct) }}>{fmtPct(pct)}</Text>
            </Space>
          )}
        </Card>
      </Col>

      <Col xs={24} lg={10}>
        <Card title="综合评分" loading={latestQ.isLoading && !result}>
          <Row gutter={16}>
            <Col span={10}>
              <div style={{ textAlign: 'center' }}>
                <Title
                  level={1}
                  style={{ margin: 0, color: level.color }}
                >
                  {score != null ? Math.round(score) : '--'}
                </Title>
                <Tag color={level.color} style={{ fontSize: 14 }}>
                  {level.label}
                </Tag>
              </div>
            </Col>
            <Col span={14}>
              <ScoreRadar scores={result?.scores} />
            </Col>
          </Row>
        </Card>
      </Col>

      <Col xs={24} lg={14}>
        <Card
          title="AI 分析"
          extra={
            <Button
              type="primary"
              loading={streaming}
              onClick={runAnalysis}
            >
              {result ? '重新分析' : '开始分析'}
            </Button>
          }
        >
          {error && (
            <Alert
              type="warning"
              showIcon
              message={error}
              style={{ marginBottom: 12 }}
              closable
              onClose={() => setError(null)}
            />
          )}
          {streaming && (
            <Alert
              type="info"
              showIcon
              message={status || '分析中…'}
              style={{ marginBottom: 12 }}
            />
          )}
          {streamText && (
            <div
              className="assistant-md"
              style={{
                background: '#fafafa',
                padding: 12,
                borderRadius: 6,
              }}
            >
              <ReactMarkdown>{streamText}</ReactMarkdown>
              {streaming && <span className="blink">▍</span>}
            </div>
          )}
          {!result && !streaming && !streamText && (
            <EmptyState description="暂无分析结果，点击「开始分析」生成" />
          )}
        </Card>
      </Col>

      {hasSections && (
        <Col span={24}>
          <Card title="详细分析">
            <Tabs
              items={SECTIONS.map((s) => ({
                key: s.key,
                label: s.label,
                children: (
                  <>
                    {result?.[s.key] ? (
                      <div className="assistant-md">
                        <ReactMarkdown>{result[s.key]}</ReactMarkdown>
                      </div>
                    ) : (
                      <EmptyState description="暂无内容" />
                    )}
                    <Text type="warning" style={{ display: 'block', marginTop: 16 }}>
                      <RiskNotice />
                    </Text>
                  </>
                ),
              }))}
            />
          </Card>
        </Col>
      )}

      <style>{`
        @keyframes blink { 0%, 50% { opacity: 1 } 51%, 100% { opacity: 0 } }
        .blink { animation: blink 1s step-end infinite; }
      `}</style>
    </Row>
  );
}
