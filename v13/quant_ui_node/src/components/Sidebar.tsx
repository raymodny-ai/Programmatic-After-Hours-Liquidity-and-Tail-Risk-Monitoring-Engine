'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import clsx from 'clsx';

const NAV_ITEMS = [
  { href: '/', label: '概览', icon: '◎', key: 'hud' },
  { href: '/view-a', label: '视图A', icon: '◐', key: 'macro' },
  { href: '/view-b', label: '视图B', icon: '◑', key: 'surface' },
  { href: '/view-c', label: '视图C', icon: '◓', key: 'config' },
  { href: '/logs', label: '终端', icon: '◔', key: 'logs' },
] as const;

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 shrink-0 bg-bg-panel border-r border-slate-800 flex flex-col">
      <div className="px-4 py-5 border-b border-slate-800">
        <div className="text-xs uppercase tracking-widest text-slate-500">
          V1.3 Console
        </div>
        <div className="text-sm font-semibold text-accent-cyan mt-1 hud-gradient">
          Tail Risk Monitor
        </div>
      </div>

      <nav className="flex-1 px-2 py-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const active =
            pathname === item.href ||
            (item.href !== '/' && pathname?.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              prefetch={item.href === '/logs' ? false : undefined}
              className={clsx(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                active
                  ? 'bg-accent-cyan/10 text-accent-cyan border-l-2 border-accent-cyan'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50',
              )}
            >
              <span className="text-base">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="px-4 py-3 border-t border-slate-800 text-[10px] text-slate-600">
        © 2026 Quant Project · MIT
      </div>
    </aside>
  );
}