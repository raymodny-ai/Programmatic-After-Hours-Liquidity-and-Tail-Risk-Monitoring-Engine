'use client';

import Sidebar from '../../components/Sidebar';
import ViewA from '../../components/ViewA';

export default function ViewAPage() {
  return (
    <div className="flex h-screen bg-bg-primary text-slate-200">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <ViewA />
      </main>
    </div>
  );
}