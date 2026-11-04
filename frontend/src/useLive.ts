import { useEffect, useRef, useState } from "react";
import { auth } from "./api";

export interface LiveEvent {
  id: number; task_id: string | null; batch_id: string | null; type: string; payload: Record<string, unknown>; at: string;
}

/** Subscribes to the API's event stream (Postgres LISTEN/NOTIFY behind it). Reconnects with backoff. */
export function useLive(filter: { task_id?: string; batch_id?: string }, onEvent: (e: LiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let ping: number | undefined;

    const connect = () => {
      const q = new URLSearchParams({ token: auth.token });
      if (filter.task_id) q.set("task_id", filter.task_id);
      if (filter.batch_id) q.set("batch_id", filter.batch_id);
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/ws?${q}`);
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        ping = window.setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 20000);
      };
      ws.onmessage = (m) => {
        try { cb.current(JSON.parse(m.data)); } catch { /* ignore malformed */ }
      };
      ws.onclose = () => {
        setConnected(false);
        window.clearInterval(ping);
        if (!closed) window.setTimeout(connect, Math.min(10000, 500 * 2 ** retry++));
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearInterval(ping);
      ws?.close();
    };
  }, [filter.task_id, filter.batch_id]);

  return connected;
}
