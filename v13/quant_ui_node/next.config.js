/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  async rewrites() {
    return [
      // V1.3 后端根 /api/* 端点: /api/latest, /api/health, /api/stats, /api/skipped
      // 必须排在 /api/v1 之前 (rewrite 按顺序匹配,虽然这里其实不会撞,但防御性)
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080'}/api/:path*`,
      },
      // 兼容老用法: /api/backend/:path* → 后端 /api/:path*
      {
        source: '/api/backend/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080'}/api/:path*`,
      },
      // /api/v1/:path* rewrite 由上面那条已覆盖 (Next.js 匹配是 source glob,不是 exact),
      // 这里保留空 rewrite 注释提醒开发者
      // /ws/:path* rewrite 禁用 — Next.js 14.2.5 不接受 ws:// destination
      // 前端直接走 NEXT_PUBLIC_WS_BASE 连后端 (绕过 Next.js, nginx 升级 Upgrade 头)
    ];
  },
  experimental: {
    optimizePackageImports: ['lightweight-charts', '@xterm/xterm'],
  },
};

module.exports = nextConfig;