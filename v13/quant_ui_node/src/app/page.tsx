'use client';

import Sidebar from '../components/Sidebar';
import HUD from '../components/HUD';

export default function HomePage() {
  return (
    <div className="flex h-screen bg-bg-primary text-slate-200">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <HUD />
      </main>
    </div>
  );
}