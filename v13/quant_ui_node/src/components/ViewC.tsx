'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { fetchApi } from '../lib/api';

// V1.3 /api/v1/config → {configs:[{key, yaml, updated_at}]}
// V1.3 /api/v1/config/{key} → {key, value: {…}, yaml: '...', updated_at}
// V1.3 PUT  /api/v1/config/{key}  body: {yaml_text, value}
interface ConfigListItem {
  key: string;
  yaml: string | null;
  updated_at: string;
}
interface ConfigListResp {
  configs: ConfigListItem[];
}
interface ConfigDetailResp {
  key: string;
  value: Record<string, unknown>;
  yaml: string | null;
  updated_at: string;
}

export default function ViewC() {
  const [selected, setSelected] = useState<string>('');  // 空串 = 还没选;打开后从列表里选第一个
  const [editedYaml, setEditedYaml] = useState<string>('');
  const [saveStatus, setSaveStatus] = useState<string>('');

  const { data: listResp, mutate: reloadList } = useSWR<ConfigListResp>(
    '/api/v1/config',
    (p: string) => fetchApi<ConfigListResp>(p),
    { revalidateOnFocus: false },
  );

  // 列表为空 → 选第一个有 yaml 的
  const configs = listResp?.configs ?? [];
  const effectiveKey = selected || configs[0]?.key || '';

  const { data: detail } = useSWR<ConfigDetailResp | null>(
    effectiveKey ? `/api/v1/config/${effectiveKey}` : null,
    (p: string) => fetchApi<ConfigDetailResp>(p),
    {
      revalidateOnFocus: false,
      onSuccess: (d) => {
        if (d?.yaml != null) setEditedYaml(d.yaml);
      },
    },
  );

  const handleSave = async () => {
    if (!effectiveKey) return;
    setSaveStatus('保存中…');
    try {
      // V1.3 PUT body 走 {yaml_text, value}; value 用 detail.value 或 空 dict
      await fetchApi<{ key: string; ok: boolean }>(`/api/v1/config/${effectiveKey}`, {
        method: 'PUT',
        body: JSON.stringify({
          yaml_text: editedYaml,
          value: detail?.value ?? {},
        }),
      });
      setSaveStatus('✓ 已保存');
      reloadList();
      setTimeout(() => setSaveStatus(''), 2000);
    } catch (err) {
      setSaveStatus('✗ ' + (err instanceof Error ? err.message : 'error'));
    }
  };

  const handleReload = () => {
    if (detail?.yaml != null) setEditedYaml(detail.yaml);
  };

  const value = detail?.value ?? {};
  const zAlert = typeof value.z_alert === 'number' ? value.z_alert : 1.5;

  return (
    <div className="p-6 h-full flex flex-col">
      <header className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-lg font-semibold">视图 C · 风控配置台</h1>
          <p className="text-xs text-slate-500 mt-1">
            YAML 实时编辑 · 保存后下次 21:00 任务生效
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleReload}
            className="text-xs px-3 py-1.5 bg-bg-card border border-slate-700 hover:border-slate-500 rounded text-slate-300"
          >
            重新加载
          </button>
          <button
            onClick={handleSave}
            disabled={!editedYaml || !effectiveKey}
            className="text-xs px-3 py-1.5 bg-accent-cyan/20 border border-accent-cyan/40 hover:bg-accent-cyan/30 disabled:opacity-50 rounded text-accent-cyan"
          >
            保存
          </button>
        </div>
      </header>

      <div className="grid grid-cols-12 gap-4 flex-1 overflow-hidden">
        {/* 左侧列表 */}
        <aside className="col-span-3 bg-bg-card border border-slate-800 rounded-md overflow-y-auto">
          <div className="p-3 border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
            配置文件 ({configs.length})
          </div>
          <ul>
            {configs.map((c) => (
              <li key={c.key}>
                <button
                  onClick={() => { setSelected(c.key); setEditedYaml(c.yaml ?? ''); }}
                  className={`w-full text-left px-3 py-2 text-xs font-mono ${
                    effectiveKey === c.key
                      ? 'bg-accent-cyan/10 text-accent-cyan border-l-2 border-accent-cyan'
                      : 'text-slate-400 hover:bg-slate-800/50'
                  }`}
                >
                  <div>{c.key}</div>
                  <div className="text-[10px] text-slate-600 mt-0.5">
                    {c.updated_at ? new Date(c.updated_at).toLocaleString() : '—'}
                  </div>
                </button>
              </li>
            ))}
            {configs.length === 0 && (
              <li className="px-3 py-4 text-xs text-slate-600">
                （无配置 — 数据库为空,等待 seed 或手动 PUT）
              </li>
            )}
          </ul>
        </aside>

        {/* 中间 YAML 编辑器 */}
        <main className="col-span-6 bg-bg-card border border-slate-800 rounded-md flex flex-col overflow-hidden">
          <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between text-xs">
            <span className="text-slate-400 font-mono">
              {effectiveKey ? `${effectiveKey}.yaml` : '(no config selected)'}
            </span>
            <span
              className={`text-[10px] ${
                saveStatus.startsWith('✓')
                  ? 'text-emerald-400'
                  : saveStatus.startsWith('✗')
                    ? 'text-rose-400'
                    : 'text-slate-500'
              }`}
            >
              {saveStatus}
            </span>
          </div>
          <textarea
            value={editedYaml}
            onChange={(e) => setEditedYaml(e.target.value)}
            spellCheck={false}
            className="flex-1 bg-transparent p-4 font-mono text-xs text-slate-200 resize-none focus:outline-none"
            placeholder="# 在此编辑 YAML 配置…"
          />
        </main>

        {/* 右侧阈值面板 */}
        <aside className="col-span-3 bg-bg-card border border-slate-800 rounded-md overflow-y-auto">
          <div className="p-3 border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
            阈值滑块
          </div>
          <div className="p-4 space-y-5">
            <div>
              <div className="flex justify-between text-xs mb-2">
                <span className="text-slate-400">z_alert 阈值</span>
                <span className="font-mono text-accent-cyan">
                  {zAlert.toFixed(2)}
                </span>
              </div>
              <input
                type="range"
                min={0.5}
                max={4.0}
                step={0.1}
                value={zAlert}
                readOnly
                className="w-full accent-accent-cyan"
              />
              <p className="text-[10px] text-slate-600 mt-1">
                Z-Score ≥ 此值触发告警
              </p>
            </div>

            <div>
              <div className="flex justify-between text-xs mb-2">
                <span className="text-slate-400">数据日期</span>
                <span className="font-mono text-slate-300">
                  {String(value.as_of ?? detail?.updated_at ?? '—')}
                </span>
              </div>
            </div>

            <div className="pt-4 border-t border-slate-800">
              <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-2">
                说明
              </h4>
              <ul className="text-[11px] text-slate-500 space-y-1 leading-relaxed">
                <li>• 修改 YAML 后点击「保存」</li>
                <li>• 配置写入 SQLite risk_config 表</li>
                <li>• 下次 21:00 美东任务自动加载</li>
                <li>• 支持 Git-style audit log</li>
              </ul>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}