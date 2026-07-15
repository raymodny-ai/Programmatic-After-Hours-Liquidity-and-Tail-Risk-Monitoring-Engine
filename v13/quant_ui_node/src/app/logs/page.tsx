'use client';

import { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import Sidebar from '../../components/Sidebar';

// xterm 顶层 import 触发 self.* 在 Node SSR 抛 ReferenceError
// ssr: false 让 TerminalLogs 完全跳过 server prerender
const TerminalLogs = dynamic(
  () => import('../../components/TerminalLogs'),
  { ssr: false, loading: () => <div className="p-6 text-slate-500 text-sm">加载终端…</div> }
);

export default function LogsPage() {
  return (
    <div className="flex h-screen bg-bg-primary text-slate-200">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <TerminalLogs />
      </main>
    </div>
  );
}