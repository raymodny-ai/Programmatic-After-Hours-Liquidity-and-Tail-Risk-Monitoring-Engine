'use client';

import { useEffect, useRef, useState } from 'react';
import {
  createChart,
  ColorType,
  LineSeries,
  AreaSeries,
  IChartApi,
  ISeriesApi,
  UTCTimestamp,
} from 'lightweight-charts';
import useSWR from 'swr';
import { fetchApi, MacroSeries, LeverageSnapshot, SkewSnapshot } from '../lib/api';

interface MacroApiResp {
  ok: boolean;
  data?: MacroSeries;
}
interface LeverageApiResp {
  ok: boolean;
  data?: LeverageSnapshot;
}
interface SkewApiResp {
  ok: boolean;
  data?: {
    snapshots?: Record<string, SkewSnapshot>;
  };
}

export default function ViewA() {
  const macroChartRef = useRef<HTMLDivElement>(null);
  const skewChartRef = useRef<HTMLDivElement>(null);
  const [macroChart, setMacroChart] = useState<IChartApi | null>(null);
  const [skewChart, setSkewChart] = useState<IChartApi | null>(null);

  const { data: macroResp } = useSWR<MacroApiResp>(
    '/api/v1/macro/series',
    (p) => fetchApi<MacroApiResp>(p),
  );
  const { data: leverageResp } = useSWR<LeverageApiResp>(
    '/api/v1/macro/leverage',
    (p) => fetchApi<LeverageApiResp>(p),
  );
  const { data: skewResp } = useSWR<SkewApiResp>(
    '/api/v1/options/skew',
    (p) => fetchApi<SkewApiResp>(p),
  );

  // 初始化 TradingView 图表
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
      crosshair: {
        mode: 1,
      },
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
      if (macroChartRef.current && c1) {
        c1.applyOptions({ width: macroChartRef.current.clientWidth });
      }
      if (skewChartRef.current && c2) {
        c2.applyOptions({ width: skewChartRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      c1.remove();
      c2.remove();
    };
  }, []);

  // 渲染宏观数据：M2 / Margin Debt / Ratio
  useEffect(() => {
    if (!macroChart || !macroResp?.data) return;
    const series = macroResp.data;

    const monthTs = (yyyyMm: string): UTCTimestamp => {
      const [y, m] = yyyyMm.split('-').map(Number);
      return Math.floor(Date.UTC(y, m - 1, 1) / 1000) as UTCTimestamp;
    };

    const ratioSeries: ISeriesApi<'Area'> = macroChart.addSeries(AreaSeries, {
      lineColor: '#22d3ee',
      topColor: 'rgba(34, 211, 238, 0.4)',
      bottomColor: 'rgba(34, 211, 238, 0.0)',
      priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(2) },
    });

    const data = series.months
      .map((m) => ({
        time: monthTs(m),
        value: series.ratio_by_month[m],
      }))
      .filter((p) => Number.isFinite(p.value));

    ratioSeries.setData(data);
  }, [macroChart, macroResp]);

  // 渲染微观 Skew 25d
  useEffect(() => {
    if (!skewChart || !skewResp?.data?.snapshots) return;

    const lineSeries: ISeriesApi<'Line'> = skewChart.addSeries(LineSeries, {
      color: '#8b5cf6',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(3) },
    });

    const data = Object.values(skewResp.data.snapshots)
      .map((s) => ({
        time: s.as_of as unknown as UTCTimestamp,
        value: s.skew_25d,
      }))
      .filter((p) => Number.isFinite(p.value))
      .sort((a, b) => (a.time as number) - (b.time as number));

    if (data.length > 0) {
      lineSeries.setData(data);
    }
  }, [skewChart, skewResp]);

  const leverage = leverageResp?.data;
  const yoyColor =
    leverage && leverage.yoy_pct > 0
      ? 'text-rose-400'
      : 'text-emerald-400';

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">视图 A · 宏观 ↔ 微观联动</h1>
          <p className="text-xs text-slate-500 mt-1">
            上图：宏观流动性比率 (M2 / Margin Debt) · 下图：Skew 25d (SPY/QQQ/IWM)
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
              <div className="text-slate-500 text-[10px] uppercase">
                3m Momentum
              </div>
              <div
                className={`font-mono text-lg ${
                  leverage.three_month_momentum > 0
                    ? 'text-rose-400'
                    : 'text-emerald-400'
                }`}
              >
                {(leverage.three_month_momentum * 100).toFixed(2)}%
              </div>
            </div>
            <div className="bg-bg-card border border-slate-800 rounded px-3 py-2">
              <div className="text-slate-500 text-[10px] uppercase">
                反转信号
              </div>
              <div
                className={`font-mono text-lg ${
                  leverage.momentum_reversal
                    ? 'text-accent-amber'
                    : 'text-slate-400'
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
        </div>

        <div className="bg-bg-card border border-slate-800 rounded-md p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-slate-400">
              Skew 25d (横截面)
            </span>
            <span className="text-[10px] text-slate-600">日度</span>
          </div>
          <div ref={skewChartRef} />
        </div>
      </div>
    </div>
  );
}