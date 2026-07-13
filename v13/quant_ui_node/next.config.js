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
      {
        source: '/ws/:path*',
        destination: `${process.env.NEXT_PUBLIC_WS_BASE ?? 'ws://localhost:8080'}/ws/:path*`,
      },
    ];
  },
  experimental: {
    optimizePackageImports: ['lightweight-charts', '@xterm/xterm'],
  },
};

module.exports = nextConfig;