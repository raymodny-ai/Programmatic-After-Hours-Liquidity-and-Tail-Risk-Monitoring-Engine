'use client';

import Sidebar from '../../components/Sidebar';
import ViewC from '../../components/ViewC';

export default function ViewCPage() {
  return (
    <div className="flex h-screen bg-bg-primary text-slate-200">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <ViewC />
      </main>
    </div>
  );
}