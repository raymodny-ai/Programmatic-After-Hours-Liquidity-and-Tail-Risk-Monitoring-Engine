/**
 * V1.3 API 客户端配置层
 *
 * - 优先使用 NEXT_PUBLIC_API_BASE（构建时注入）
 * - 默认 http://localhost:8000
 * - SSE / WS 路径自动按 base 协议派生
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080';

export const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE ?? 'ws://localhost:8080';

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