/**
 * V1.3 API 客户端配置层
 *
 * - 浏览器运行时: 默认走相对路径 (浏览器访问 nginx :8880 → 同源反代到 api_backend)
 *   Owner 用浏览器开 http://NAS_IP:8880 时,fetch 走同源,避免跨域/CORS/端口直挂
 * - 构建时 NEXT_PUBLIC_API_BASE (构建期注入),SSR / API 路由 (本项目只有一个 /api/health) 仍用绝对
 * - WS 同样走 nginx 反代 ws://host/ws/alerts,无需 NEXT_PUBLIC_WS_BASE
 */

// API_BASE 仅在 SSR / 服务器路由里使用 (build-time 注入为 http://quant-api-node:8080)。
// 浏览器运行时 fetch / fetchApi / WS *应该*走同源 (`'' '' / window.location.host`),
// 以走 Nginx 反代.  这是 Next.js 14 standalone build + docker-network 场景的唯一可靠姿势.
// 设个明显名字提醒使用者不要直接 fetch API_BASE。
export const API_BASE_SSR_ONLY = process.env.NEXT_PUBLIC_API_BASE ?? 'http://quant-api-node:8080';

/** 浏览器/客户端运行时统一用空弦,API 请求会走当前 origin (e.g. http://nas-ip:8880/api/...). */
export const API_BASE = '';

export const WS_BASE =
  process.env.NEXT_PUBLIC_WS_BASE ?? '';   // 空串 → 客户端用 window.location.host 动态拼

/** 浏览器运行时,拼装完整的 ws://host/ws/alerts URL。
 *  用法: new WebSocket(wsUrl('/ws/alerts'))
 *  SSR 时退回到 NEXT_PUBLIC_WS_BASE + path (老用法)。
 */
export function wsUrl(path: string): string {
  if (typeof window !== 'undefined') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}${path}`;
  }
  return `${WS_BASE}${path}`;
}

/** 统一 REST 抓取封装 */
export async function fetchApi<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

/** V1.3 后端实际返回的 /api/v1/options/skew 每条 item 的 shape */
export interface SkewApiItem {
  ticker: string;
  as_of_date?: string;
  as_of?: string;
  skew_25d: number | null;
  skew_spread: number | null;
  iv_put_25d: number | null;
  iv_call_25d: number | null;
  put_count: number | null;
  call_count: number | null;
  status: string | null;
  data_source: string | null;
  signal_quality: SignalQuality;
}

export interface SkewApiResp {
  as_of_date: string;
  count: number;
  items: SkewApiItem[];
}

/** V1.3 后端 /health 返的字段 */
export interface HealthApiResp {
  service: string;
  version: string;
  redis: boolean;
  sqlite: boolean;
  last_pipeline_run: string | null;
  uptime_seconds: number | null;
}

/** V1.3 后端 /api/latest 返的字段 */
export interface LatestApiResp {
  date: string;
  updated_at: string;
  source: string;
  snapshots: Record<
    string,
    {
      ticker: string;
      skew_spread: number | null;
      iv_put_25d: number | null;
      iv_call_25d: number | null;
      data_source: string | null;
      signal_quality: string | null;
      status: string | null;
      as_of?: string;
    }
  >;
}

/** 把 V1.3 SkewApiItem 转成 UI 用的 SkewSnapshot。
 *  UI 代码要求 skew_25d / z_score / iv_atm 等字段,V1.3 给的是 skew_spread / iv_put_25d 等。
 */
export function adaptSkewItem(item: SkewApiItem): SkewSnapshot {
  return {
    ticker: item.ticker,
    as_of: item.as_of_date ?? item.as_of ?? '',
    skew_25d: item.skew_25d ?? item.skew_spread ?? 0,
    z_score: 0, // V1.3 没提供,UI 拿到 0 走默认
    iv_atm: item.iv_call_25d ?? 0,
    iv_25d_call: item.iv_call_25d ?? undefined,
    iv_25d_put: item.iv_put_25d ?? undefined,
    data_quality: item.signal_quality === 'fallback_estimated' ? 'fallback' : 'primary',
    signal_quality: item.signal_quality,
  };
}

export function adaptHealth(r: HealthApiResp): HealthStatus {
  return {
    ok: r.redis && r.sqlite,
    version: r.version,
    components: {
      api: 'up',
      sqlite: r.sqlite ? 'up' : 'down',
      redis: r.redis ? 'up' : 'down',
      scheduler: 'enabled',
    },
    as_of: r.last_pipeline_run ?? new Date().toISOString(),
  };
}

// V1.3 /api/v1/macro/series/M2 返回 {name, count, values: [{date, value}]}
export interface MacroApiResp {
  name: string;
  count: number;
  values: { date: string; value: number }[];
}

/** 把 V1.3 macro values 转成 UI 用 schema (months + ratio_by_month + as_of)。
 *  V1.3 API 返回的结构是 { name, count, values: [{date: 'YYYY-MM-DD', value: number}] }.
 *  UI 在 ViewA 里读 series.months / series.ratio_by_month——我们让其二者都从 values 块派生。
 */
export function adaptMacroSeries(r: MacroApiResp): MacroSeries {
  const months: string[] = [];
  const ratio_by_month: Record<string, number> = {};
  for (const v of r.values ?? []) {
    // FRED 月度序列可能是月末日(YYYY-MM-DD)或月初(YYYY-MM-01);取前 7 字符作为月份键
    const monthKey = v.date?.slice(0, 7);
    if (!monthKey || !Number.isFinite(v.value)) continue;
    if (!ratio_by_month[monthKey]) months.push(monthKey);
    ratio_by_month[monthKey] = v.value;
  }
  months.sort();
  return {
    as_of: r.values?.[r.values.length - 1]?.date ?? new Date().toISOString().slice(0, 10),
    months,
    m2_by_month: ratio_by_month,
    margin_by_month: {},
    ratio_by_month,
  };
}

// V1.3 /api/v1/macro/leverage 返回 { as_of_date, ratio, ratio_yoy, ratio_3m_momentum, momentum_reversal, m2, margin_debt, signal_quality }
export interface LeverageApiResp {
  as_of_date: string;
  ratio: number | null;
  ratio_yoy: number | null;
  ratio_3m_momentum: number | null;
  momentum_reversal: boolean;
  m2: number | null;
  margin_debt: number | null;
  signal_quality: string;
}

export function adaptLeverage(r: LeverageApiResp): LeverageSnapshot {
  return {
    as_of: r.as_of_date ?? '',
    yoy_pct: r.ratio_yoy ?? 0,
    three_month_momentum: r.ratio_3m_momentum ?? 0,
    momentum_reversal: r.momentum_reversal ?? false,
  };
}

/** 守 YYYY-MM-DD / YYYY-MM 转 UTCTimestamp (秒), 不合法返 null */
export function ymdToUtcTs(s: string | undefined | null): number | null {
  if (!s || typeof s !== 'string') return null;
  // 接受 YYYY-MM-DD 或 YYYY-MM
  const m = s.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?$/);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = m[3] ? Number(m[3]) : 1;
  if (!Number.isFinite(y) || !Number.isFinite(mo) || mo < 1 || mo > 12) return null;
  return Math.floor(Date.UTC(y, mo - 1, d) / 1000);
}

export function adaptLatest(r: LatestApiResp): { data: { snapshots: Record<string, SkewSnapshot> } } {
  const snapshots: Record<string, SkewSnapshot> = {};
  for (const [k, v] of Object.entries(r.snapshots || {})) {
    snapshots[k] = {
      ticker: v.ticker,
      as_of: v.as_of ?? r.date,
      skew_25d: v.skew_spread ?? 0,
      z_score: 0,
      iv_atm: v.iv_call_25d ?? 0,
      iv_25d_call: v.iv_call_25d ?? undefined,
      iv_25d_put: v.iv_put_25d ?? undefined,
      data_quality: v.signal_quality === 'fallback_estimated' ? 'fallback' : 'primary',
      signal_quality: (v.signal_quality as SignalQuality) ?? 'unavailable',
    };
  }
  return { data: { snapshots } };
}

/** 共享类型契约（与 v13/shared/schemas 保持同步） */
export type DataQualityTier = 'primary' | 'fallback' | 'unavailable';
export type SignalQuality =
  | 'primary'
  | 'fallback_estimated'
  | 'degraded'
  | 'unavailable';
export type VxnAlertSeverity =
  | 'normal'
  | 'watch'
  | 'elevated'
  | 'high'
  | 'critical';

export interface SkewSnapshot {
  ticker: string;
  as_of: string;
  skew_25d: number;
  z_score: number;
  z_score_5d?: number;
  z_score_20d?: number;
  iv_atm: number;
  iv_25d_call?: number;
  iv_25d_put?: number;
  data_quality: DataQualityTier;
  signal_quality: SignalQuality;
}

export interface MacroSeries {
  as_of: string;
  months: string[];
  m2_by_month: Record<string, number>;
  margin_by_month: Record<string, number>;
  ratio_by_month: Record<string, number>;
}

export interface LeverageSnapshot {
  as_of: string;
  yoy_pct: number;
  three_month_momentum: number;
  momentum_reversal: boolean;
}

export interface AlertRecord {
  id?: string;
  ticker: string;
  as_of_date: string;
  severity: VxnAlertSeverity;
  is_alert: boolean;
  z_score?: number;
  reasons: string[];
  timestamp?: string;
}

export interface ApiResponse<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string };
  as_of?: string;
  data_quality?: DataQualityTier;
}

/** 健康状态 */
export interface HealthStatus {
  ok: boolean;
  version: string;
  components: {
    api: 'up' | 'down' | 'degraded';
    sqlite: 'up' | 'down' | 'unavailable';
    redis: 'up' | 'down' | 'unavailable';
    scheduler: 'enabled' | 'disabled';
  };
  as_of: string;
}