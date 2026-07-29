// Fetch-based SSE receiver (EventSource doesn't support custom headers like Authorization).
export async function streamSSE(
  url: string,
  onEvent: (obj: any) => void,
  onError?: (e: any) => void,
  options: { method?: 'GET' | 'POST'; body?: unknown } = {},
): Promise<void> {
  const token = localStorage.getItem('token');
  const method = options.method ?? 'GET';
  try {
    const init: RequestInit = {
      method,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    };
    if (method === 'POST' && options.body !== undefined) {
      (init.headers as Record<string, string>)['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    const resp = await fetch(url, init);
    if (!resp.ok || !resp.body) throw new Error(`SSE failed: ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';
      for (const part of parts) {
        const line = part.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        const json = line.slice(5).trim();
        try {
          onEvent(JSON.parse(json));
        } catch {
          // ignore non-JSON keepalives
        }
      }
    }
  } catch (e) {
    if (onError) onError(e);
    else throw e;
  }
}
