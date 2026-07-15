'use client';

import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import useSWR from 'swr';
import '@xterm/xterm/css/xterm.css';
import { fetchApi, wsUrl } from '../lib/api';

interface LogEntry {
  ts: string;
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG';
  source: string;
  message: string;
}

export default function TerminalLogs() {
  const termRef = useRef<HTMLDivElement>(null);
  const termInstanceRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [paused, setPaused] = useState(false);

  // 初始历史日志 (V1.3 后端未装 audit logger, 返 entries=[]; 走 WS 实时 streaming)
  const { data: historyResp } = useSWR<{ count: number; entries: LogEntry[] }>(
    '/api/v1/audit?limit=200',
    (p: string) => fetchApi(p),
    { refreshInterval: 0, revalidateOnFocus: false },
  );

  useEffect(() => {
    if (!termRef.current) return;

    const term = new Terminal({
      theme: {
        background: '#0a0e1a',
        foreground: '#cbd5e1',
        cursor: '#22d3ee',
        black: '#1e293b',
        green: '#10b981',
        yellow: '#f59e0b',
        red: '#f43f5e',
        cyan: '#22d3ee',
        blue: '#3b82f6',
        magenta: '#a855f7',
      },
      fontFamily: '"JetBrains Mono", "Fira Code", monospace',
      fontSize: 12,
      lineHeight: 1.3,
      convertEol: true,
      cursorBlink: false,
      disableStdin: true,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(termRef.current);

    try {
      fit.fit();
    } catch {
      // ignore if container size is 0
    }

    termInstanceRef.current = term;
    fitRef.current = fit;

    // 欢迎横幅
    term.writeln('\x1b[36m╔════════════════════════════════════════════════════════════╗\x1b[0m');
    term.writeln('\x1b[36m║\x1b[0m   V1.3 Tail Risk Console · Live Pipeline Logs             \x1b[36m║\x1b[0m');
    term.writeln('\x1b[36m║\x1b[0m   美东 21:00 自动触发 · WebSocket 实时推送                  \x1b[36m║\x1b[0m');
    term.writeln('\x1b[36m╚════════════════════════════════════════════════════════════╝\x1b[0m');
    term.writeln('');

    const handleResize = () => {
      try {
        fit.fit();
      } catch {
        // ignore
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      term.dispose();
      termInstanceRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // 写入历史日志 (空 entries 跳过,避免循环依赖)
  useEffect(() => {
    const term = termInstanceRef.current;
    if (!term || !historyResp?.entries?.length) return;
    if (paused) return;

    for (const entry of historyResp.entries.slice(-50)) {
      writeLog(term, entry);
    }
  }, [historyResp, paused]);

  // WebSocket 实时日志
  useEffect(() => {
    if (paused) return;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(wsUrl('/ws/alerts'));

      ws.onmessage = (ev) => {
        if (paused) return;
        const term = termInstanceRef.current;
        if (!term) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'alert') {
            const a = msg.payload;
            const text = `[${a.as_of_date}] ${a.ticker} 严重度=${a.severity} Z=${a.z_score?.toFixed?.(2) ?? '—'} 原因=${(a.reasons ?? []).join(' | ')}`;
            term.writeln(`\x1b[${getColor(a.severity)}m${text}\x1b[0m`);
          } else if (msg.type === 'log') {
            writeLog(term, msg.payload);
          }
        } catch {
          // ignore
        }
      };

      ws.onclose = () => {
        if (cancelled) return;
        retryTimer = setTimeout(connect, 5000);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [paused]);

  const clear = () => {
    termInstanceRef.current?.clear();
  };

  return (
    <div className="p-6 h-full flex flex-col">
      <header className="flex items-center justify-between mb-3">
        <div>
          <h1 className="text-lg font-semibold">终端 · Pipeline Logs</h1>
          <p className="text-xs text-slate-500 mt-1">
            实时滚动告警 + 历史审计日志
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoScroll((v) => !v)}
            className={`text-xs px-3 py-1.5 rounded border ${
              autoScroll
                ? 'bg-accent-cyan/10 border-accent-cyan/40 text-accent-cyan'
                : 'bg-bg-card border-slate-700 text-slate-400'
            }`}
          >
            自动滚动 {autoScroll ? '✓' : '—'}
          </button>
          <button
            onClick={() => setPaused((v) => !v)}
            className={`text-xs px-3 py-1.5 rounded border ${
              paused
                ? 'bg-amber-500/10 border-amber-500/40 text-amber-400'
                : 'bg-bg-card border-slate-700 text-slate-400'
            }`}
          >
            {paused ? '▶ 已暂停' : '⏸ 暂停'}
          </button>
          <button
            onClick={clear}
            className="text-xs px-3 py-1.5 bg-bg-card border border-slate-700 hover:border-slate-500 rounded text-slate-300"
          >
            清屏
          </button>
        </div>
      </header>
      <div
        ref={termRef}
        className="flex-1 bg-bg-primary border border-slate-800 rounded-md overflow-hidden"
      />
    </div>
  );
}

function writeLog(term: Terminal, entry: LogEntry) {
  const ts = entry.ts || new Date().toISOString();
  const levelColor =
    entry.level === 'ERROR'
      ? 31
      : entry.level === 'WARN'
        ? 33
        : entry.level === 'DEBUG'
          ? 90
          : 36;
  term.writeln(
    `\x1b[90m${ts.slice(11, 19)}\x1b[0m \x1b[${levelColor}m[${entry.level.padEnd(5)}]\x1b[0m \x1b[35m${entry.source.padEnd(12)}\x1b[0m ${entry.message}`,
  );
}

function getColor(severity: string): number {
  switch (severity) {
    case 'critical':
      return 91;
    case 'high':
      return 31;
    case 'elevated':
      return 33;
    case 'watch':
      return 93;
    default:
      return 36;
  }
}