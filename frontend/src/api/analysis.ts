import client from './client';
import { streamSSE } from '../utils/sse';

export const triggerAnalysis = (code: string) =>
  client.post(`/analysis/${code}`).then((r) => r.data);

export const getLatestAnalysis = (code: string) =>
  client.get(`/analysis/${code}/latest`).then((r) => r.data);

// Note: streamSSE bypasses the axios baseURL, so the path starts with /api/v1.
export function streamAnalysis(
  code: string,
  requestId: string,
  onEvent: (o: any) => void,
  onError?: (e: any) => void,
) {
  return streamSSE(
    `/api/v1/analysis/${code}/stream?request_id=${encodeURIComponent(requestId)}`,
    onEvent,
    onError,
  );
}
