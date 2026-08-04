import client from './client';
import { streamSSE } from '../utils/sse';
import type { KnowledgeDoc } from './types';

export const createSession = (title?: string) =>
  client.post('/assistant/sessions', { title }).then((r) => r.data);

export const listSessions = () =>
  client.get('/assistant/sessions').then((r) => r.data);

export const getMessages = (sessionId: string) =>
  client.get(`/assistant/sessions/${sessionId}/messages`).then((r) => r.data);

// Note: streamSSE bypasses the axios baseURL, so the path starts with /api/v1.
// The backend route is POST with a JSON body {content}; streamSSE issues the POST.
export function sendMessageStream(
  sessionId: string,
  content: string,
  onEvent: (o: any) => void,
  onError?: (e: any) => void,
) {
  return streamSSE(
    `/api/v1/assistant/sessions/${sessionId}/messages`,
    onEvent,
    onError,
    { method: 'POST', body: { content } },
  );
}

// V2 Stage L — RAG knowledge base. Endpoints under /assistant/knowledge.

export interface UploadDocReq {
  title: string;
  content: string;
  source?: string;
  stock_code?: string;
  doc_date?: string; // ISO YYYY-MM-DD
}

export const uploadKnowledgeDoc = (req: UploadDocReq) =>
  client
    .post<{ id: number; title: string; status: string }>('/assistant/knowledge', req)
    .then((r) => r.data);

export const listKnowledgeDocs = (
  params: { stock_code?: string; status?: string; limit?: number } = {},
) =>
  client
    .get<KnowledgeDoc[]>('/assistant/knowledge', {
      params: { limit: 100, ...params },
    })
    .then((r) => r.data);

export const deleteKnowledgeDoc = (docId: number) =>
  client.delete(`/assistant/knowledge/${docId}`).then((r) => r.data);

