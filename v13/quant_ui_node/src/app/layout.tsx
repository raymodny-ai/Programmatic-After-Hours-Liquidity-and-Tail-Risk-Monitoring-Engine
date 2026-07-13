import type { Metadata, Viewport } from 'next';
import '../styles/globals.css';

export const metadata: Metadata = {
  title: 'V1.3 宏观流动性与尾部风险监控控制台',
  description:
    'Programmatic After-Hours Liquidity & Tail Risk Monitoring Console',
};

export const viewport: Viewport = {
  themeColor: '#0a0e1a',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="bg-bg-primary text-slate-200 antialiased">
        {children}
      </body>
    </html>
  );
}