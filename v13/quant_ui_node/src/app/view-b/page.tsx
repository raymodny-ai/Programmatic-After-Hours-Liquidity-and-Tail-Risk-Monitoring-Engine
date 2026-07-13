'use client';

import Sidebar from '../../components/Sidebar';
import ViewB from '../../components/ViewB';

export default function ViewBPage() {
  return (
    <div className="flex h-screen bg-bg-primary text-slate-200">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <ViewB />
      </main>
    </div>
  );
}