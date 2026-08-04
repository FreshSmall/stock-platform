import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  List,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import ReactMarkdown from 'react-markdown';
import {
  createSession,
  deleteKnowledgeDoc,
  getMessages,
  listKnowledgeDocs,
  listSessions,
  sendMessageStream,
  uploadKnowledgeDoc,
} from '../api/assistant';
import type { KnowledgeDoc, KnowledgeSource } from '../api/types';
import RiskNotice from '../components/RiskNotice';

const { TextArea } = Input;
const { Title, Text } = Typography;

interface SessionBrief {
  session_id: string;
  title?: string | null;
  created_at?: string | null;
}

// One tool invocation surfaced inline in an assistant reply.
interface ToolStep {
  name: string;
  args: unknown;
  result?: unknown;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  toolSteps: ToolStep[];
  // V2: RAG source citations attached to the assistant reply (if any).
  sources: KnowledgeSource[];
  error?: string;
}

const EXAMPLE_CHIPS = ['分析 600519', 'MACD 金叉股票', '回测 MA 策略'];

// H6 — AI 助手对话.
//
// Single-column chat layout: a sessions drawer toggled on the left, the
// scrolling message stream in the middle, and a TextArea + 发送 at the bottom.
//
// Assistant replies accumulate over the SSE stream: 'tool_call' adds a step
// ("正在调用工具"), 'tool_result' fills that step's result (collapsible JSON),
// 'chunk' appends to the in-progress text, 'done' finalizes it. Each assistant
// message is rendered as markdown and ends with the risk disclaimer.
export default function Assistant() {
  const [sessions, setSessions] = useState<SessionBrief[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, dispatch] = useReducer(messagesReducer, []);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [, setError] = useState<string | null>(null);
  const [loadingSessions, setLoadingSessions] = useState(true);

  const idRef = useRef(0);
  const nextId = useCallback(() => `m${++idRef.current}`, []);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const streamTokenRef = useRef(0); // bumped each send to ignore stale events

  // --- session bootstrap: load the list, auto-select the newest on first mount.
  const refreshSessions = useCallback(async (selectNewest = false) => {
    setLoadingSessions(true);
    try {
      const list: SessionBrief[] = await listSessions();
      setSessions(list);
      if (selectNewest && list.length > 0) {
        await selectSession(list[0].session_id);
      }
    } catch {
      // ignore — user can still start a new session manually
    } finally {
      setLoadingSessions(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    refreshSessions(true);
  }, [refreshSessions]);

  // --- load a session's history into the message list.
  const selectSession = useCallback(
    async (sid: string) => {
      setError(null);
      setSessionId(sid);
      dispatch({ type: 'reset' });
      try {
        const rows: any[] = await getMessages(sid);
        // Assistant tool messages aren't separately surfaced; map persisted
        // rows into bubbles, deferring tool_calls JSON to the assistant bubble.
        const mapped: ChatMessage[] = rows
          .filter((r) => r.role !== 'tool')
          .map((r) => ({
            id: nextId(),
            role: r.role,
            content: r.content ?? '',
            toolSteps: [],
            sources: [],
          }));
        dispatch({ type: 'set', messages: mapped });
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载历史失败');
      }
    },
    [nextId],
  );

  const newSession = useCallback(async () => {
    setError(null);
    try {
      const s: { session_id: string; title?: string | null } = await createSession(
        '新对话',
      );
      setSessions((prev) => [
        { session_id: s.session_id, title: s.title, created_at: null },
        ...prev,
      ]);
      setSessionId(s.session_id);
      dispatch({ type: 'reset' });
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建会话失败');
    }
  }, []);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || sending) return;

      // Lazy session creation: if the user starts typing before creating one,
      // make a session on the fly, then proceed.
      let sid = sessionId;
      if (!sid) {
        try {
          const s: { session_id: string; title?: string | null } =
            await createSession(content.slice(0, 20));
          sid = s.session_id;
          setSessionId(sid);
          setSessions((prev) => [
            { session_id: sid!, title: s.title, created_at: null },
            ...prev,
          ]);
        } catch (e) {
          setError(e instanceof Error ? e.message : '创建会话失败');
          return;
        }
      }

      setError(null);
      setInput('');
      setSending(true);
      const token = ++streamTokenRef.current;

      const userId = nextId();
      const assistantId = nextId();
      dispatch({
        type: 'add',
        message: { id: userId, role: 'user', content, toolSteps: [], sources: [] },
      });
      dispatch({
        type: 'add',
        message: {
          id: assistantId,
          role: 'assistant',
          content: '',
          toolSteps: [],
          sources: [],
        },
      });

      try {
        await sendMessageStream(
          sid,
          content,
          (evt: { type: string; data?: unknown }) => {
            if (token !== streamTokenRef.current) return; // stale stream
            const { type, data } = evt;
            if (type === 'chunk' && typeof data === 'string') {
              dispatch({ type: 'appendChunk', id: assistantId, chunk: data });
            } else if (type === 'sources') {
              // V2 RAG: attach retrieved source citations to the reply.
              const srcs = Array.isArray(data) ? (data as KnowledgeSource[]) : [];
              dispatch({ type: 'setSources', id: assistantId, sources: srcs });
            } else if (type === 'tool_call') {
              const d = data as { name?: string; args?: unknown };
              dispatch({
                type: 'addToolStep',
                id: assistantId,
                step: { name: d?.name ?? '工具', args: d?.args },
              });
            } else if (type === 'tool_result') {
              const d = data as { name?: string; result?: unknown };
              dispatch({
                type: 'fillToolResult',
                id: assistantId,
                name: d?.name,
                result: d?.result,
              });
            } else if (type === 'error') {
              const msg = typeof data === 'string' ? data : '回答失败';
              dispatch({ type: 'setError', id: assistantId, error: msg });
            } else if (type === 'done') {
              // The stream emits chunks already; 'done' carries the final full
              // text. Backfill it only if no chunks were captured (some models
              // return the whole answer in 'done' with no chunks).
              if (typeof data === 'string' && data.length > 0) {
                dispatch({ type: 'finalize', id: assistantId, content: data });
              }
            }
            // 'user_saved' and 'disclaimer' need no UI action (disclaimer is
            // rendered globally and per-message).
          },
          (e: unknown) => {
            if (token !== streamTokenRef.current) return;
            const msg = e instanceof Error ? e.message : '流式连接中断';
            dispatch({ type: 'setError', id: assistantId, error: msg });
          },
        );
      } catch (e: unknown) {
        if (token !== streamTokenRef.current) return;
        dispatch({
          type: 'setError',
          id: assistantId,
          error: e instanceof Error ? e.message : '发送失败',
        });
      } finally {
        if (token === streamTokenRef.current) setSending(false);
      }
    },
    [sessionId, sending, nextId],
  );

  // Auto-scroll to the newest message as it streams in.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // V2: knowledge base panel toggle.
  const [kbOpen, setKbOpen] = useState(false);

  return (
    <div
      style={{
        display: 'flex',
        gap: 16,
        height: '100%',
        minHeight: 0,
      }}
    >
      {/* Sessions list (fixed-width rail; V1 simplicity — no drawer needed). */}
      <Card
        size="small"
        style={{ width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { padding: 0, flex: 1, overflow: 'auto' } }}
        title="会话"
        extra={
          <Button size="small" type="primary" onClick={newSession}>
            新对话
          </Button>
        }
      >
        {loadingSessions ? (
          <div style={{ padding: 16, textAlign: 'center' }}>
            <Spin />
          </div>
        ) : sessions.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无会话"
            style={{ padding: 16 }}
          />
        ) : (
          <List
            size="small"
            dataSource={sessions}
            renderItem={(s) => (
              <List.Item
                onClick={() => selectSession(s.session_id)}
                style={{
                  cursor: 'pointer',
                  padding: '8px 12px',
                  background:
                    s.session_id === sessionId ? '#e6f4ff' : undefined,
                }}
              >
                <Text
                  ellipsis
                  style={{ width: '100%' }}
                  strong={s.session_id === sessionId}
                >
                  {s.title || '新对话'}
                </Text>
              </List.Item>
            )}
          />
        )}
      </Card>

      {/* Main chat column. */}
      <Card
        size="small"
        style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' } }}
        title={
          <Space>
            <Title level={5} style={{ margin: 0 }}>AI 助手</Title>
          </Space>
        }
        extra={
          <Button size="small" onClick={() => setKbOpen((v) => !v)}>
            知识库
          </Button>
        }
      >
        {/* Scrollable message area. */}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            padding: 16,
            background: '#fafafa',
          }}
        >
          {messages.length === 0 ? (
            <EmptyState onStart={() => newSession()} />
          ) : (
            <MessageList messages={messages} sending={sending} />
          )}
          <div ref={bottomRef} />
        </div>

        {/* Composer (fixed at bottom of the card). */}
        <div style={{ flexShrink: 0, borderTop: '1px solid #f0f0f0', padding: 12, background: '#fff' }}>
          <Space size={[8, 8]} wrap style={{ marginBottom: 8 }}>
            {EXAMPLE_CHIPS.map((c) => (
              <Tag
                key={c}
                style={{ cursor: 'pointer' }}
                onClick={() => send(c)}
              >
                {c}
              </Tag>
            ))}
          </Space>
          <Space.Compact style={{ width: '100%' }}>
            <TextArea
              autoSize={{ minRows: 1, maxRows: 4 }}
              placeholder="输入问题，如「分析 600519」"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
            />
            <Button
              type="primary"
              loading={sending}
              onClick={() => send(input)}
              style={{ height: 'auto' }}
            >
              发送
            </Button>
          </Space.Compact>
        </div>
      </Card>

      {/* V2: RAG knowledge base management panel (toggleable). */}
      {kbOpen && <KnowledgePanel />}
    </div>
  );
}

// V2 — RAG knowledge base panel: list docs, upload new ones, delete.
function KnowledgePanel() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listKnowledgeDocs();
      setDocs(list);
    } catch {
      // ignore — non-fatal
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const remove = async (id: number) => {
    try {
      await deleteKnowledgeDoc(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
    } catch {
      // ignore
    }
  };

  return (
    <Card
      size="small"
      style={{ width: 280, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
      styles={{ body: { padding: 0, flex: 1, overflow: 'auto' } }}
      title="知识库"
      extra={
        <Button size="small" type="primary" onClick={() => setUploadOpen(true)}>
          上传
        </Button>
      }
    >
      {loading ? (
        <div style={{ padding: 16, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : docs.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无文档"
          style={{ padding: 16 }}
        />
      ) : (
        <List
          size="small"
          dataSource={docs}
          renderItem={(d) => (
            <List.Item
              style={{ padding: '8px 12px', alignItems: 'flex-start' }}
              actions={[
                <a
                  key="del"
                  onClick={() => remove(d.id)}
                  style={{ color: '#ff4d4f', fontSize: 12 }}
                >
                  删除
                </a>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space size={4} wrap>
                    <Text ellipsis style={{ maxWidth: 140 }}>
                      {d.title}
                    </Text>
                    <Tag
                      color={
                        d.status === 'embedded'
                          ? 'green'
                          : d.status === 'failed'
                            ? 'red'
                            : 'default'
                      }
                      style={{ fontSize: 10, margin: 0 }}
                    >
                      {d.status}
                    </Tag>
                  </Space>
                }
                description={
                  <Space size={4} wrap>
                    {d.stock_code && <Tag style={{ fontSize: 10 }}>{d.stock_code}</Tag>}
                    {d.source && (
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {d.source}
                      </Text>
                    )}
                    {d.doc_date && (
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {d.doc_date}
                      </Text>
                    )}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      )}

      <UploadDocModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          setUploadOpen(false);
          refresh();
        }}
      />
    </Card>
  );
}

function UploadDocModal({
  open,
  onClose,
  onUploaded,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [source, setSource] = useState('');
  const [stockCode, setStockCode] = useState('');
  const [docDate, setDocDate] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    if (!title.trim() || !content.trim()) {
      setError('请填写标题与正文');
      return;
    }
    setSubmitting(true);
    try {
      await uploadKnowledgeDoc({
        title: title.trim(),
        content: content.trim(),
        source: source.trim() || undefined,
        stock_code: stockCode.trim() || undefined,
        doc_date: docDate.trim() || undefined,
      });
      setTitle('');
      setContent('');
      setSource('');
      setStockCode('');
      setDocDate('');
      onUploaded();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '上传失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="上传知识库文档"
      open={open}
      onCancel={onClose}
      onOk={submit}
      okButtonProps={{ loading: submitting }}
      destroyOnHidden
      width={560}
    >
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Input
          placeholder="标题"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <TextArea
          placeholder="正文（支持研报、公告、笔记等任意文本）"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          autoSize={{ minRows: 6, maxRows: 16 }}
        />
        <Space wrap>
          <Input
            placeholder="来源（可选）"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            style={{ width: 160 }}
          />
          <Input
            placeholder="股票代码（可选）"
            value={stockCode}
            onChange={(e) => setStockCode(e.target.value)}
            style={{ width: 140 }}
          />
          <Input
            placeholder="日期 YYYY-MM-DD（可选）"
            value={docDate}
            onChange={(e) => setDocDate(e.target.value)}
            style={{ width: 180 }}
          />
        </Space>
        {error && <Alert type="error" showIcon message={error} />}
      </Space>
    </Modal>
  );
}

function MessageList({
  messages,
  sending,
}: {
  messages: ChatMessage[];
  sending: boolean;
}) {
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {messages.map((m) => (
        <div
          key={m.id}
          style={{
            display: 'flex',
            justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
          }}
        >
          <MessageBubble message={m} sending={sending} />
        </div>
      ))}
    </Space>
  );
}

function MessageBubble({ message, sending }: { message: ChatMessage; sending: boolean }) {
  const isUser = message.role === 'user';

  return (
    <Card
      size="small"
      style={{
        maxWidth: '85%',
        borderColor: isUser ? '#91caff' : '#f0f0f0',
        background: isUser ? '#f0f7ff' : '#fff',
      }}
      styles={{ body: { padding: '10px 14px' } }}
      title={
        <Space size={6}>
          <Tag color={isUser ? 'blue' : 'green'} style={{ margin: 0 }}>
            {isUser ? '我' : 'AI 助手'}
          </Tag>
        </Space>
      }
    >
      {isUser ? (
        <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
      ) : (
        <>
          {/* Tool calls render inline above the streamed text. */}
          {message.toolSteps.map((step, i) => (
            <ToolStepView key={i} step={step} />
          ))}

          {/* V2: RAG source citations render above the streamed text. */}
          {message.sources.length > 0 && <SourcesView sources={message.sources} />}

          {message.content ? (
            <div className="assistant-md">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          ) : (
            !message.error &&
            sending && (
              <Text type="secondary">
                正在思考…
                <span className="blink">▍</span>
              </Text>
            )
          )}

          {message.error && (
            <Alert
              type="error"
              showIcon
              message={message.error}
              style={{ margin: '4px 0' }}
            />
          )}

          {!isUser && (message.content || message.error) && (
            <Text
              type="secondary"
              style={{ display: 'block', marginTop: 8, fontSize: 12 }}
            >
              <RiskNotice />
            </Text>
          )}
        </>
      )}
    </Card>
  );
}

// V2 — render RAG source citations as a collapsible list above the answer.
function SourcesView({ sources }: { sources: KnowledgeSource[] }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <Collapse
        size="small"
        defaultActiveKey={['1']}
        items={[
          {
            key: '1',
            label: (
              <Space size={6}>
                <Tag color="purple" style={{ margin: 0 }}>
                  来源
                </Tag>
                <Text strong>知识库引用 ({sources.length})</Text>
              </Space>
            ),
            children: (
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {sources.map((s, i) => (
                  <Card
                    key={i}
                    size="small"
                    style={{ background: '#fafaff' }}
                    title={
                      <Space size={6}>
                        <Text strong>{s.title}</Text>
                        {s.stock_code && <Tag>{s.stock_code}</Tag>}
                        {s.source && <Tag color="blue">{s.source}</Tag>}
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          相似度 {Number(s.score).toFixed(3)}
                        </Text>
                      </Space>
                    }
                  >
                    <Text style={{ whiteSpace: 'pre-wrap' }}>{s.text}</Text>
                  </Card>
                ))}
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}

function ToolStepView({ step }: { step: ToolStep }) {
  const header = (
    <Space size={6}>
      <Tag color="blue">工具</Tag>
      <Text strong>{step.name}</Text>
      {step.result === undefined && <Text type="secondary">调用中…</Text>}
    </Space>
  );
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ marginBottom: 4 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          正在调用工具：{step.name}
        </Text>
      </div>
      <Collapse
        size="small"
        items={[
          {
            key: '1',
            label: header,
            children: (
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    参数
                  </Text>
                  <pre style={preStyle}>{safeJson(step.args)}</pre>
                </div>
                {step.result !== undefined && (
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      工具结果
                    </Text>
                    <pre style={preStyle}>{safeJson(step.result)}</pre>
                  </div>
                )}
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}

function EmptyState({ onStart }: { onStart: () => void }) {
  return (
    <Empty description="开始一段新的对话" style={{ padding: 40 }}>
      <Button type="primary" onClick={onStart}>
        新对话
      </Button>
    </Empty>
  );
}

const preStyle: React.CSSProperties = {
  margin: 0,
  padding: 8,
  background: '#f5f5f5',
  borderRadius: 4,
  fontSize: 12,
  maxHeight: 200,
  overflow: 'auto',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

function safeJson(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// --- message state ----------------------------------------------------------

type Action =
  | { type: 'add'; message: ChatMessage }
  | { type: 'appendChunk'; id: string; chunk: string }
  | { type: 'finalize'; id: string; content: string }
  | { type: 'addToolStep'; id: string; step: ToolStep }
  | { type: 'fillToolResult'; id: string; name?: string; result: unknown }
  | { type: 'setSources'; id: string; sources: KnowledgeSource[] }
  | { type: 'setError'; id: string; error: string }
  | { type: 'set'; messages: ChatMessage[] }
  | { type: 'reset' };

function messagesReducer(state: ChatMessage[], action: Action): ChatMessage[] {
  switch (action.type) {
    case 'add':
      return [...state, action.message];
    case 'appendChunk':
      return patch(state, action.id, (m) => ({ ...m, content: m.content + action.chunk }));
    case 'setSources':
      return patch(state, action.id, (m) => ({ ...m, sources: action.sources }));
    case 'finalize':
      // Only backfill if nothing streamed in; never overwrite chunks.
      return patch(state, action.id, (m) =>
        m.content.length > 0 ? m : { ...m, content: action.content },
      );
    case 'addToolStep':
      return patch(state, action.id, (m) => ({
        ...m,
        toolSteps: [...m.toolSteps, action.step],
      }));
    case 'fillToolResult': {
      return patch(state, action.id, (m) => {
        // Fill the LAST matching step that has no result yet (a tool may be
        // called more than once in one turn).
        const steps = [...m.toolSteps];
        for (let i = steps.length - 1; i >= 0; i--) {
          if (
            steps[i].result === undefined &&
            (!action.name || steps[i].name === action.name)
          ) {
            steps[i] = { ...steps[i], result: action.result };
            break;
          }
        }
        return { ...m, toolSteps: steps };
      });
    }
    case 'setError':
      return patch(state, action.id, (m) => ({ ...m, error: action.error }));
    case 'set':
      return action.messages;
    case 'reset':
      return [];
    default:
      return state;
  }
}

function patch(
  state: ChatMessage[],
  id: string,
  fn: (m: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return state.map((m) => (m.id === id ? fn(m) : m));
}
