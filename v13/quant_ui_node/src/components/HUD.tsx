'use client';

import { useEffect, useState } from 'react';
import useSWR from 'swr';
import clsx from 'clsx';
import { fetchApi, HealthStatus, SkewSnapshot, VxnAlertSeverity } from '../lib/api';
import { useAlertsStream } from '../lib/useAlerts';

const SEVERITY_COLORS: Record<VxnAlertSeverity, string> = {
  normal: 'text-signal-normal',
  watch: 'text-signal-watch',
  elevated: 'text-signal-elevated',
  high: 'text-signal-high',
  critical: 'text-signal-critical',
};

const SEVERITY_BG: Record<VxnAlertSeverity, string> = {
  normal: 'bg-emerald-500/10',
  watch: 'bg-amber-500/10',
  elevated: 'bg-orange-500/10',
  high: 'bg-rose-500/10',
  critical: 'bg-rose-700/20',
};

interface DatePickerProps {
  value: string;
  onChange: (v: string) => void;
}

function DatePicker({ value, onChange }: DatePickerProps) {
  return (
    <input
      type="date"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-bg-card border border-slate-700 text-slate-200 text-xs px-2 py-1 rounded font-mono focus:outline-none focus:border-accent-cyan"
    />
  );
}

interface StatusLightsProps {
  health: HealthStatus | undefined;
}

function StatusLights({ health }: StatusLightsProps) {
  const apiOk = health?.components.api === 'up';
  const dbOk = health?.components.sqlite === 'up';
  const redisOk = health?.components.redis === 'up';
  const schedulerOn = health?.components.scheduler === 'enabled';

  const lights = [
    { label: 'API', ok: apiOk, hint: 'FastAPI process' },
    { label: 'SQLite', ok: dbOk, hint: '持久化存储' },
    { label: 'Redis', ok: redisOk, hint: '热缓存 + pub/sub' },
    { label: '21:00 调度', ok: schedulerOn, hint: '美东定时器' },
  ];

  return (
    <div className="flex items-center gap-4">
      {lights.map((l) => (
        <div
          key={l.label}
          className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider"
          title={l.hint}
        >
          <span
            className={clsx(
              'status-light pulse',
              l.ok ? 'bg-emerald-400 text-emerald-400' : 'bg-rose-500 text-rose-500',
            )}
          />
          <span className={l.ok ? 'text-slate-300' : 'text-slate-500'}>
            {l.label}
          </span>
        </div>
      ))}
    </div>
  );
}

interface SkewCardProps {
  snap: SkewSnapshot;
}

function SkewCard({ snap }: SkewCardProps) {
  const z = snap.z_score;
  const color =
    z >= 2.0
      ? 'text-rose-400'
      : z >= 1.0
        ? 'text-amber-400'
        : 'text-slate-300';
  return (
    <div className="bg-bg-card border border-slate-800 rounded-md p-3 hover:border-slate-600 transition-colors">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-mono text-slate-400">{snap.ticker}</span>
        <span
          className={clsx(
            'text-[9px] uppercase px-1.5 py-0.5 rounded font-mono',
            snap.data_quality === 'primary'
              ? 'bg-emerald-500/10 text-emerald-400'
              : snap.data_quality === 'fallback'
                ? 'bg-amber-500/10 text-amber-400'
                : 'bg-rose-500/10 text-rose-400',
          )}
        >
          {snap.data_quality}
        </span>
      </div>
      <div className={clsx('text-2xl font-semibold mt-1 font-mono', color)}>
        {snap.skew_25d?.toFixed(3) ?? '—'}
      </div>
      <div className="flex justify-between mt-1 text-[10px] text-slate-500 font-mono">
        <span>Z: {snap.z_score?.toFixed(2) ?? '—'}</span>
        <span>IV: {snap.iv_atm?.toFixed(3) ?? '—'}</span>
      </div>
    </div>
  );
}

export default function HUD() {
  const [asOf, setAsOf] = useState<string>('');
  const stream = useAlertsStream();

  // 初始化默认日期为今天
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    setAsOf(today);
  }, []);

  const { data: health } = useSWR<HealthStatus>(
    '/health',
    (p: string) => fetchApi<HealthStatus>(p),
    { refreshInterval: 10000 },
  );

  const { data: skewData } = useSWR<{ data?: { snapshots?: Record<string, SkewSnapshot> } }>(
    asOf ? `/api/v1/options/skew?as_of=${asOf}` : null,
    (p: string) => fetchApi(p),
    { refreshInterval: 30000 },
  );

  const snapshots = skewData?.data?.snapshots
    ? Object.values(skewData.data.snapshots)
    : [];

  const recentAlerts = stream.alerts.slice(0, 8);

  return (
    <div className="flex flex-col h-full">
      {/* 顶部 HUD Bar */}
      <header className="h-14 bg-bg-panel border-b border-slate-800 flex items-center px-6 gap-6">
        <div className="text-sm font-semibold tracking-wider">
          <span className="text-accent-cyan">V1.3</span>
          <span className="text-slate-500 ml-2">/</span>
          <span className="text-slate-300 ml-2">Tail Risk Console</span>
        </div>

        <div className="flex-1" />

        <StatusLights health={health} />

        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span>数据日期</span>
          <DatePicker value={asOf} onChange={setAsOf} />
        </div>

        <div
          className={clsx(
            'text-[10px] uppercase font-mono px-2 py-1 rounded',
            stream.connected
              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
              : 'bg-rose-500/10 text-rose-400 border border-rose-500/30',
          )}
        >
          {stream.connected ? '● WS LIVE' : '○ WS OFFLINE'}
        </div>
      </header>

      {/* 主区域 */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* 风险快照矩阵 */}
        <section>
          <h2 className="text-xs uppercase tracking-widest text-slate-500 mb-3">
            Skew 25d Matrix · {snapshots.length || '—'} Tickers
          </h2>
          {snapshots.length === 0 ? (
            <div className="bg-bg-card border border-dashed border-slate-700 rounded-md p-8 text-center text-slate-500 text-sm">
              {health === undefined
                ? '等待 API 健康检查响应…'
                : '尚未获取到 Skew 数据（请等待 21:00 美东定时任务执行或手动触发）'}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              {snapshots.map((s) => (
                <SkewCard key={s.ticker} snap={s} />
              ))}
            </div>
          )}
        </section>

        {/* 最近告警流 */}
        <section>
          <h2 className="text-xs uppercase tracking-widest text-slate-500 mb-3">
            Live Alerts · {stream.alerts.length} Total
          </h2>
          <div className="bg-bg-card border border-slate-800 rounded-md">
            {recentAlerts.length === 0 ? (
              <div className="p-6 text-center text-slate-500 text-sm">
                {stream.connected
                  ? '实时连接中，等待告警事件…'
                  : 'WebSocket 未连接（检查 API 服务）'}
              </div>
            ) : (
              <ul className="divide-y divide-slate-800">
                {recentAlerts.map((a, i) => (
                  <li
                    key={i}
                    className={clsx(
                      'px-4 py-3 flex items-center gap-4 animate-fade-in',
                      SEVERITY_BG[a.severity],
                    )}
                  >
                    <span
                      className={clsx(
                        'font-mono text-xs uppercase px-2 py-0.5 rounded',
                        SEVERITY_COLORS[a.severity],
                      )}
                    >
                      {a.severity}
                    </span>
                    <span className="font-mono text-sm text-slate-200">
                      {a.ticker}
                    </span>
                    <span className="text-xs text-slate-500 font-mono">
                      {a.as_of_date}
                    </span>
                    <span className="text-xs text-slate-400 flex-1 truncate">
                      {(a.reasons ?? []).join(' · ')}
                    </span>
                    {a.z_score !== undefined && (
                      <span className="font-mono text-xs text-slate-300">
                        Z={a.z_score.toFixed(2)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}