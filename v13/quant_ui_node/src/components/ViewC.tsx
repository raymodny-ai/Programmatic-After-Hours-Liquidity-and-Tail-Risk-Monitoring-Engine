'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { fetchApi } from '../lib/api';

interface ConfigItem {
  name: string;
  yaml: string;
  updated_at: string;
}

interface ConfigsResp {
  ok: boolean;
  data?: ConfigItem[];
}

interface ConfigDetailResp {
  ok: boolean;
  data?: { yaml: string; value: Record<string, unknown> };
}

export default function ViewC() {
  const [selected, setSelected] = useState<string>('vxn_thresholds');
  const [editedYaml, setEditedYaml] = useState<string>('');
  const [saveStatus, setSaveStatus] = useState<string>('');

  const { data: listResp, mutate: reloadList } = useSWR<ConfigsResp>(
    '/api/v1/config',
    (p: string) => fetchApi<ConfigsResp>(p),
  );

  const { data: detail } = useSWR<ConfigDetailResp>(
    `/api/v1/config/${selected}`,
    (p: string) => fetchApi<ConfigDetailResp>(p),
    {
      onSuccess: (d) => {
        if (d?.data?.yaml) setEditedYaml(d.data.yaml);
      },
    },
  );

  const handleSave = async () => {
    setSaveStatus('保存中…');
    try {
      const res = await fetchApi<{ ok: boolean }>(`/api/v1/config/${selected}`, {
        method: 'PUT',
        body: JSON.stringify({ yaml: editedYaml }),
      });
      if (res.ok) {
        setSaveStatus('✓ 已保存');
        reloadList();
        setTimeout(() => setSaveStatus(''), 2000);
      } else {
        setSaveStatus('✗ 失败');
      }
    } catch (err) {
      setSaveStatus('✗ ' + (err instanceof Error ? err.message : 'error'));
    }
  };

  const handleReload = () => {
    if (detail?.data?.yaml) setEditedYaml(detail.data.yaml);
  };

  // 阈值滑块（演示）
  const value = detail?.data?.value ?? {};
  const zAlert = typeof value.z_alert === 'number' ? value.z_alert : 1.5;

  return (
    <div className="p-6 h-full flex flex-col">
      <header className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-lg font-semibold">视图 C · 风控配置台</h1>
          <p className="text-xs text-slate-500 mt-1">
            YAML 实时编辑 · 阈值滑块联动 · 保存后下次 21:00 任务生效
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
            disabled={!editedYaml}
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
            配置文件
          </div>
          <ul>
            {(listResp?.data ?? []).map((c) => (
              <li key={c.name}>
                <button
                  onClick={() => setSelected(c.name)}
                  className={`w-full text-left px-3 py-2 text-xs font-mono ${
                    selected === c.name
                      ? 'bg-accent-cyan/10 text-accent-cyan border-l-2 border-accent-cyan'
                      : 'text-slate-400 hover:bg-slate-800/50'
                  }`}
                >
                  <div>{c.name}</div>
                  <div className="text-[10px] text-slate-600 mt-0.5">
                    {c.updated_at}
                  </div>
                </button>
              </li>
            ))}
            {(!listResp?.data || listResp.data.length === 0) && (
              <li className="px-3 py-4 text-xs text-slate-600">
                （无配置）
              </li>
            )}
          </ul>
        </aside>

        {/* 中间 YAML 编辑器 */}
        <main className="col-span-6 bg-bg-card border border-slate-800 rounded-md flex flex-col overflow-hidden">
          <div className="px-4 py-2 border-b border-slate-800 flex items-center justify-between text-xs">
            <span className="text-slate-400 font-mono">{selected}.yaml</span>
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
                  {String(value.as_of ?? '—')}
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