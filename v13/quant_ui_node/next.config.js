/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/backend/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080'}/api/:path*`,
      },
      {
        source: '/api/v1/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080'}/api/v1/:path*`,
      },
      // /ws/:path* rewrite 禁用 — Next.js 14.2.5 不接受 ws:// destination
      // 前端直接走 NEXT_PUBLIC_WS_BASE 连后端 (绕过 Next.js, nginx 升级 Upgrade 头)
    ];
  },
  experimental: {
    optimizePackageImports: ['lightweight-charts', '@xterm/xterm'],
  },
};

module.exports = nextConfig;