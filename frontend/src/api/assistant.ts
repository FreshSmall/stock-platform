import client from './client';
import { streamSSE } from '../utils/sse';

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
