'use client';

import { useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import {
  createChart,
  ColorType,
  LineSeries,
  AreaSeries,
  IChartApi,
  ISeriesApi,
  UTCTimestamp,
} from 'lightweight-charts';
import {
  fetchApi,
  MacroApiResp,
  LeverageApiResp,
  SkewApiResp,
  adaptMacroSeries,
  adaptLeverage,
  adaptSkewItem,
  SkewApiItem,
  SkewSnapshot,
  ymdToUtcTs,
} from '../lib/api';

export default function ViewA() {
  const macroChartRef = useRef<HTMLDivElement>(null);
  const skewChartRef = useRef<HTMLDivElement>(null);
  const [macroChart, setMacroChart] = useState<IChartApi | null>(null);
  const [skewChart, setSkewChart] = useState<IChartApi | null>(null);

  // V1.3 /api/v1/macro/series/M2 → { name, count, values: [{date, value}] }
  const { data: macroRaw } = useSWR<MacroApiResp>(
    '/api/v1/macro/series/M2',
    (p: string) => fetchApi<MacroApiResp>(p),
  );
  // V1.3 /api/v1/macro/leverage → { as_of_date, ratio, ratio_yoy, ... }
  const { data: leverageRaw } = useSWR<LeverageApiResp>(
    '/api/v1/macro/leverage',
    (p: string) => fetchApi<LeverageApiResp>(p),
  );
  // V1.3 /api/v1/options/skew → { as_of_date, count, items: SkewApiItem[] }
  const { data: skewRaw } = useSWR<{ as_of_date: string; count: number; items: SkewApiItem[] }>(
    '/api/v1/options/skew',
    (p: string) => fetchApi<{ as_of_date: string; count: number; items: SkewApiItem[] }>(p),
  );

  const macro = macroRaw ? adaptMacroSeries(macroRaw) : undefined;
  const leverage = leverageRaw ? adaptLeverage(leverageRaw) : undefined;
  const skewSnapshots: Record<string, SkewSnapshot> = {};
  if (skewRaw?.items) {
    for (const item of skewRaw.items) {
      skewSnapshots[item.ticker] = adaptSkewItem(item);
    }
  }

  // 初始化图表
  useEffect(() => {
    if (!macroChartRef.current || !skewChartRef.current) return;

    const commonOptions = {
      layout: {
        background: { type: ColorType.Solid, color: '#0f1626' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: '#1e293b',
      },
      crosshair: { mode: 1 },
    } as const;

    const c1 = createChart(macroChartRef.current, {
      ...commonOptions,
      width: macroChartRef.current.clientWidth,
      height: 320,
    });
    const c2 = createChart(skewChartRef.current, {
      ...commonOptions,
      width: skewChartRef.current.clientWidth,
      height: 320,
    });

    setMacroChart(c1);
    setSkewChart(c2);

    const handleResize = () => {
      if (macroChartRef.current && c1) c1.applyOptions({ width: macroChartRef.current.clientWidth });
      if (skewChartRef.current && c2) c2.applyOptions({ width: skewChartRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      c1.remove();
      c2.remove();
    };
  }, []);

  // 渲染宏观流动性比率 (M2 / Margin Debt)
  useEffect(() => {
    if (!macroChart || !macro) return;

    const ratioSeries: ISeriesApi<'Area'> = macroChart.addSeries(AreaSeries, {
      lineColor: '#22d3ee',
      topColor: 'rgba(34, 211, 238, 0.4)',
      bottomColor: 'rgba(34, 211, 238, 0.0)',
      priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(2) },
    });

    // YYYY-MM → 月初 UTC 秒
    const data = (macro.months ?? [])
      .map((m: string) => {
        const ts = ymdToUtcTs(m + '-01');
        const v = macro.ratio_by_month[m];
        return ts != null && Number.isFinite(v) ? { time: ts as UTCTimestamp, value: v } : null;
      })
      .filter((p): p is { time: UTCTimestamp; value: number } => p !== null)
      .sort((a, b) => (a.time as number) - (b.time as number));

    if (data.length === 0) {
      // 空数据不调 setData,避免 lightweight-charts 内部抛错
      return;
    }
    ratioSeries.setData(data);
  }, [macroChart, macro]);

  // 渲染微观 Skew 25d (横截面快照,time = as_of_date;同 ticker 单点)
  useEffect(() => {
    if (!skewChart) return;

    const lineSeries: ISeriesApi<'Line'> = skewChart.addSeries(LineSeries, {
      color: '#8b5cf6',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(3) },
    });

    // 单日横截面: 一个 time 多个 ticker 不行 (同一 time 多 series OK, 但 LineSeries 是单 series)
    // 改成 bar chart 多 series,或单值显示
    const data = Object.values(skewSnapshots)
      .map((s) => {
        const ts = ymdToUtcTs(s.as_of);
        return ts != null && Number.isFinite(s.skew_25d) ? { time: ts as UTCTimestamp, value: s.skew_25d } : null;
      })
      .filter((p): p is { time: UTCTimestamp; value: number } => p !== null)
      .sort((a, b) => (a.time as number) - (b.time as number));

    if (data.length === 0) return;
    lineSeries.setData(data);
  }, [skewChart, skewSnapshots]);

  const yoyColor =
    leverage && leverage.yoy_pct > 0 ? 'text-rose-400' : 'text-emerald-400';

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">视图 A · 宏观 ↔ 微观联动</h1>
          <p className="text-xs text-slate-500 mt-1">
            上图：宏观流动性比率 (M2 / Margin Debt) · 下图：Skew 25d 横截面
          </p>
        </div>
        {leverage && (
          <div className="flex gap-4 text-xs">
            <div className="bg-bg-card border border-slate-800 rounded px-3 py-2">
              <div className="text-slate-500 text-[10px] uppercase">YoY</div>
              <div className={`font-mono text-lg ${yoyColor}`}>
                {(leverage.yoy_pct * 100).toFixed(2)}%
              </div>
            </div>
            <div className="bg-bg-card border border-slate-800 rounded px-3 py-2">
              <div className="text-slate-500 text-[10px] uppercase">3m Momentum</div>
              <div
                className={`font-mono text-lg ${
                  leverage.three_month_momentum > 0 ? 'text-rose-400' : 'text-emerald-400'
                }`}
              >
                {(leverage.three_month_momentum * 100).toFixed(2)}%
              </div>
            </div>
            <div className="bg-bg-card border border-slate-800 rounded px-3 py-2">
              <div className="text-slate-500 text-[10px] uppercase">反转信号</div>
              <div
                className={`font-mono text-lg ${
                  leverage.momentum_reversal ? 'text-accent-amber' : 'text-slate-400'
                }`}
              >
                {leverage.momentum_reversal ? '⚠ YES' : '—'}
              </div>
            </div>
          </div>
        )}
      </header>

      <div className="grid grid-cols-1 gap-4">
        <div className="bg-bg-card border border-slate-800 rounded-md p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-slate-400">
              宏观流动性比率 (Margin / M2)
            </span>
            <span className="text-[10px] text-slate-600">月度</span>
          </div>
          <div ref={macroChartRef} />
          {(!macro || macro.months.length === 0) && (
            <div className="text-center text-xs text-slate-500 py-8">
              无宏观数据 (M2 FRED 密钥未配置)
            </div>
          )}
        </div>

        <div className="bg-bg-card border border-slate-800 rounded-md p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-slate-400">
              Skew 25d (横截面 · 当日快照)
            </span>
            <span className="text-[10px] text-slate-600">日度</span>
          </div>
          <div ref={skewChartRef} />
          {Object.keys(skewSnapshots).length === 0 && (
            <div className="text-center text-xs text-slate-500 py-8">无 Skew 数据</div>
          )}
          {/* 表格 fallback — 横截面在 chart 单点显示信息少 */}
          <div className="mt-3 grid grid-cols-5 gap-2 text-xs">
            {Object.values(skewSnapshots).map((s) => (
              <div key={s.ticker} className="bg-bg-primary border border-slate-800 rounded p-2">
                <div className="text-slate-500 text-[10px]">{s.ticker}</div>
                <div className="font-mono text-slate-200">
                  {Number.isFinite(s.skew_25d) ? s.skew_25d.toFixed(3) : '—'}
                </div>
                <div className="text-[10px] text-slate-500">
                  IV ATM {Number.isFinite(s.iv_atm) ? (s.iv_atm * 100).toFixed(1) + '%' : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}