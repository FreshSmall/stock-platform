// Fetch-based SSE receiver (EventSource doesn't support custom headers like Authorization).
export async function streamSSE(
  url: string,
  onEvent: (obj: any) => void,
  onError?: (e: any) => void,
): Promise<void> {
  const token = localStorage.getItem('token');
  try {
    const resp = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
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
