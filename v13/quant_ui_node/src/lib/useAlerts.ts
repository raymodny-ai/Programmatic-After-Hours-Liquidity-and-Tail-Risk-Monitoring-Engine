'use client';

import { useEffect, useRef, useState } from 'react';
import { AlertRecord, wsUrl } from './api';

interface AlertsStreamState {
  alerts: AlertRecord[];
  connected: boolean;
  lastError: string | null;
}

export function useAlertsStream(): AlertsStreamState {
  const [state, setState] = useState<AlertsStreamState>({
    alerts: [],
    connected: false,
    lastError: null,
  });
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      try {
        const ws = new WebSocket(wsUrl('/ws/alerts'));
        wsRef.current = ws;

        ws.onopen = () => {
          retryRef.current = 0;
          setState((s) => ({ ...s, connected: true, lastError: null }));
          ws.send(JSON.stringify({ type: 'hello', client: 'quant-ui' }));
        };

        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'alert' && msg.payload) {
              setState((s) => ({
                ...s,
                alerts: [msg.payload, ...s.alerts].slice(0, 200),
              }));
            }
          } catch {
            // ignore malformed message
          }
        };

        ws.onerror = () => {
          setState((s) => ({ ...s, lastError: 'WebSocket error' }));
        };

        ws.onclose = () => {
          setState((s) => ({ ...s, connected: false }));
          // 指数退避：1, 2, 4, 8, 16 秒，封顶 30 秒
          const delay = Math.min(1000 * 2 ** retryRef.current, 30000);
          retryRef.current += 1;
          timerRef.current = setTimeout(connect, delay);
        };
      } catch (err) {
        setState((s) => ({
          ...s,
          connected: false,
          lastError: err instanceof Error ? err.message : String(err),
        }));
        timerRef.current = setTimeout(connect, 5000);
      }
    };

    connect();

    // 心跳保活
    const heartbeat = setInterval(() => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
        } catch {
          // ignore
        }
      }
    }, 25000);

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      clearInterval(heartbeat);
      wsRef.current?.close();
    };
  }, []);

  return state;
}