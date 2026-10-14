/** Subscribe to IHAgent's local event bridge. Connection loss is not an agent error. */

const DEFAULT_URL = 'http://127.0.0.1:8765';

export function bridgeOrigin(explicit) {
  if (explicit) return String(explicit).replace(/\/$/, '');
  if (typeof window !== 'undefined' && window.__IHAGENT_ROBOT_URL) {
    return String(window.__IHAGENT_ROBOT_URL).replace(/\/$/, '');
  }
  return DEFAULT_URL;
}

/**
 * `onEvent` receives parsed SSE payloads.
 * `onStatus` receives `{ connected: boolean }`.
 * Returns a disconnect function.
 *
 * A missing bridge is normal. Probe /health and back off so a closed port
 * does not leave EventSource retrying on every tick.
 */
export function connectBridge(origin, onEvent, onStatus) {
  if (typeof fetch === 'undefined') {
    if (onStatus) onStatus({ connected: false });
    return () => {};
  }
  let closed = false;
  let source = null;
  let timer = null;
  let delay = 4000;

  function schedule() {
    if (closed) return;
    timer = setTimeout(probe, delay);
    delay = Math.min(Math.round(delay * 1.6), 20000);
  }

  function openStream() {
    if (closed || typeof EventSource === 'undefined') return;
    source = new EventSource(`${origin}/events`);
    source.onopen = () => {
      delay = 4000;
      if (!closed && onStatus) onStatus({ connected: true });
    };
    source.onmessage = (ev) => {
      if (closed) return;
      try {
        const data = JSON.parse(ev.data);
        onEvent(data);
      } catch (err) {
        // Ignore a malformed frame. The next one may be fine.
      }
    };
    source.onerror = () => {
      if (onStatus) onStatus({ connected: false });
      if (source) source.close();
      source = null;
      schedule();
    };
  }

  async function probe() {
    if (closed) return;
    try {
      const res = await fetch(`${origin}/health`, { cache: 'no-store' });
      if (!res.ok) throw new Error('down');
      openStream();
    } catch (err) {
      if (onStatus) onStatus({ connected: false });
      schedule();
    }
  }

  probe();
  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    if (source) source.close();
  };
}

export async function sendBridgeMessage(origin, text) {
  const res = await fetch(`${origin}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  let body = {};
  try {
    body = await res.json();
  } catch (err) {
    body = {};
  }
  if (!res.ok || body.ok === false) {
    const reason = body.error || `The bridge returned HTTP ${res.status}.`;
    return { ok: false, reason };
  }
  return { ok: true };
}
