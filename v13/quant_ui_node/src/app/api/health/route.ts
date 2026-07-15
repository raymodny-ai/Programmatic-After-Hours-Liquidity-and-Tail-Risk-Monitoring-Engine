/**
 * Docker healthcheck endpoint for quant-ui-node.
 *
 * 返回 service / version / redis 状态 (ping 后端 /api/health 拼装).
 * docker-compose.yml 的 healthcheck 期待此路径返回 HTTP 200。
 */
import { NextResponse } from 'next/server';
import { API_BASE_SSR_ONLY } from '../../../lib/api';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

// SSR-only: 服务器路由内部 fetch 后端容器用 quant-api-node:8080 docker network DNS
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || API_BASE_SSR_ONLY;

export async function GET() {
  let apiOk = false;
  let apiVersion: string | null = null;
  try {
    const r = await fetch(`${API_BASE}/api/health`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(2000),
    });
    apiOk = r.ok;
    if (apiOk) {
      const body = await r.json();
      apiVersion = body.version ?? null;
    }
  } catch {
    // API 不可达时仍返 200 (UI 自己能跑就 ok)
    apiOk = false;
  }

  return NextResponse.json({
    service: 'quant-ui-node',
    version: '1.3.0',
    api_reachable: apiOk,
    api_version: apiVersion,
    timestamp: new Date().toISOString(),
  });
}