/**
 * 系統檢查與管理（第 10 章，8 個子分頁）。所有清單共用 checklist_items 一張表，以分類區分；
 * 每個項目只有名稱、狀態（待確認／已確認／異常／不適用）、備註、最後更新時間。
 */
import React, { useState } from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { Btn, LoadState, Msg, PageHead, Tabs, useApp, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, size } from '../../../../packages/ui/src/format';
import { CheckList } from '../srv';
import { boardText } from './Uem';

export const PRINCIPLES: [string, string][] = [
  ['匯入／匯出', '所有資料匯入匯出僅 Super 可用；Super 可授權 Admin；其他角色禁止。'],
  ['教材 ID 與 Tag', '每份教材有永久教材 ID；有閱覽權限的人可新增共用 Tag。'],
  ['權限只在 Server 判斷', '前端隱藏按鈕不是安全界線；權限變更立即生效。'],
  ['簡單可用', '優先沿用現有功能，不新增非必要服務。'],
  ['影片', 'Server 批次轉 MP4；檢查通過滿 30 天、Super 確認後才刪原檔。'],
  ['學習歷程', '狀態由使用者手動選；每 30 分鐘及登出時儲存；只顯示約略位置。'],
  ['系統工作交給 Server Agent', 'PHP 不執行系統指令；Client 不裝 Python。'],
  ['驗收', '程式完成不等於已驗收；未實測項目列入「App 開發待確認清單」。'],
];

const STATUS: Record<string, string> = { pending: '○ 待確認', confirmed: '✓ 已確認', abnormal: '✗ 異常', na: '－ 不適用' };

const TABS = [
  { id: 'principles', label: '1. 開發指導原則' },
  { id: 'pending_dev', label: '2. App 開發待確認清單' },
  { id: 'os_packages', label: '3. OS 環境套件清單' },
  { id: 'server_hw', label: '4. Server 硬體檢查' },
  { id: 'client_hw', label: '5. Client 硬體通用檢查' },
  { id: 'client_os', label: '6. Client OS 軟體通用檢查' },
  { id: 'custom', label: '7. 檢查清單' },
  { id: 'diagnose', label: '8. 系統檢測' },
];

export function SystemCheckPage(): any {
  const [tab, setTab] = useState('principles');
  return (
    <div className="page">
      <PageHead title="系統檢查與管理" />
      <Tabs small value={tab} onChange={setTab} tabs={TABS} />
      {tab === 'principles' ? (
        <section className="panel">
          <ol className="principles">
            {PRINCIPLES.map(([k, v]) => <li key={k}><strong>{k}：</strong>{v}</li>)}
          </ol>
        </section>
      ) : null}
      {['pending_dev', 'os_packages', 'server_hw', 'client_hw', 'client_os', 'custom'].includes(tab) ? <Checklist key={tab} category={tab} /> : null}
      {tab === 'server_hw' ? <LatestDiagnosis /> : null}
      {tab === 'client_hw' || tab === 'client_os' ? <ClientInfos /> : null}
      {tab === 'diagnose' ? <DiagnoseHistory /> : null}
    </div>
  );
}

function Checklist(props: { category: string }): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<{ items: any[] }>(`/api/admin/checklist?category=${props.category}`), [props.category]);
  const [drafts, setDrafts] = useState<Record<number, { status?: string; note?: string }>>({});
  const [newTitle, setNewTitle] = useState('');
  const save = async (it: any) => {
    const d = drafts[it.id] || {};
    try {
      const x = await api.patch(`/api/admin/checklist/${it.id}`, { status: d.status ?? it.status, note: d.note ?? it.note });
      r.setData({ items: r.data!.items.map((y) => (y.id === it.id ? x.item : y)) });
      const nd = { ...drafts };
      delete nd[it.id];
      setDrafts(nd);
      toast('已儲存', 'ok', 1500);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const add = async () => {
    try {
      await api.post('/api/admin/checklist', { category: props.category, title: newTitle.trim() });
      setNewTitle('');
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const remove = async (it: any) => {
    if (!window.confirm(`刪除「${it.title}」？`)) return;
    try {
      await api.del(`/api/admin/checklist/${it.id}`);
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const items = r.data ? r.data.items : [];
  const counts = items.reduce((m: Record<string, number>, it) => ({ ...m, [it.status]: (m[it.status] || 0) + 1 }), {});
  return (
    <section className="panel">
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data ? <p className="dim">{Object.entries(STATUS).map(([k, v]) => `${v} ${counts[k] || 0}`).join('　')}</p> : null}
      {r.data && !items.length ? <Msg kind="info">這個清單還沒有項目</Msg> : null}
      {items.length ? (
        <table className="grid">
          <thead><tr><th>項目</th><th>自動欄位</th><th>狀態</th><th>備註</th><th>最後更新</th><th></th></tr></thead>
          <tbody>
            {items.map((it) => {
              const d = drafts[it.id] || {};
              const dirty = d.status !== undefined || d.note !== undefined;
              return (
                <tr key={it.id} className={'chk-row ' + (d.status ?? it.status)}>
                  <td>{it.title}{it.device_name ? <div className="dim">電腦：{it.device_name}</div> : null}</td>
                  <td className="small">{it.auto_value || <span className="dim">—</span>}</td>
                  <td>
                    <select value={d.status ?? it.status} onChange={(e: any) => setDrafts({ ...drafts, [it.id]: { ...d, status: e.target.value } })} aria-label={`${it.title} 狀態`}>
                      {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </td>
                  <td><textarea rows={1} value={d.note ?? it.note ?? ''} onChange={(e: any) => setDrafts({ ...drafts, [it.id]: { ...d, note: e.target.value } })} aria-label={`${it.title} 備註`} /></td>
                  <td className="nowrap small">{dateTime(it.updated_at)}</td>
                  <td className="actions">
                    <Btn small kind={dirty ? 'primary' : 'plain'} disabled={!dirty} onClick={() => save(it)}>儲存</Btn>
                    {props.category === 'custom' ? <Btn small kind="quiet" onClick={() => remove(it)}>刪除</Btn> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      <div className="row">
        <input className="grow" value={newTitle} onChange={(e: any) => setNewTitle(e.target.value)} placeholder="新增一個檢查項目" aria-label="新增檢查項目" />
        <Btn onClick={add} disabled={!newTitle.trim()}>新增</Btn>
      </div>
    </section>
  );
}

function LatestDiagnosis(): any {
  const r = useLoad(() => api.get<{ items: any[] }>('/api/admin/diagnostics?kind=server'), []);
  const d = r.data && r.data.items[0];
  if (!d) return <LoadState loading={r.loading} error={r.error} />;
  const x = d.data;
  const host = x.host || {};
  return (
    <section className="panel">
      <h2>最近一次 Server 診斷（{dateTime(d.created_at)}）</h2>
      <CheckList items={x.checks || []} />
      <table className="grid kv small">
        <tbody>
          {x.cpu ? <tr><th>CPU</th><td>{x.cpu.model}（{x.cpu.cores} 執行緒）</td></tr> : null}
          {x.memory ? <tr><th>RAM</th><td>{size(x.memory.total)}</td></tr> : null}
          {x.media_disk ? <tr><th>/Media</th><td>{size(x.media_disk.total)}，剩餘 {x.media_disk.free_pct}%</td></tr> : null}
          {(host.disks || []).map((k: any) => <tr key={k.path}><th>{k.path}</th><td>{size(k.total)}，剩餘 {k.free_pct}%</td></tr>)}
          {host.system ? <tr><th>系統</th><td>{host.system.os}（核心 {host.system.kernel}）；IPv4：{(host.system.ipv4 || []).join('、')}</td></tr> : null}
        </tbody>
      </table>
    </section>
  );
}

/** Q-05：依每台電腦最新一筆 PC 資訊統計作業系統台數（Windows 組建號 22000 以上為 Win11）。 */
function osSummary(items: any[]): string {
  const latest = new Map<string, any>();
  for (const it of items) {
    const key = String(it.device_id || it.data.hostname || it.id);
    if (!latest.has(key)) latest.set(key, it);
  }
  const count: Record<string, number> = {};
  for (const it of latest.values()) {
    const osText = String(it.data.os || '');
    const m = /^Windows (\d+)\.(\d+)\.(\d+)/.exec(osText);
    const label = m ? (m[1] === '6' && m[2] === '1' ? 'Win7' : m[1] === '10' ? (Number(m[3]) >= 22000 ? 'Win11' : 'Win10') : osText)
      : /ubuntu/i.test(osText) ? 'Ubuntu' : osText || '未知';
    count[label] = (count[label] || 0) + 1;
  }
  return `已收到 ${latest.size} 台：` + Object.entries(count).map(([k, v]) => `${k} ${v} 台`).join('、');
}

function ClientInfos(): any {
  const r = useLoad(() => api.get<{ items: any[] }>('/api/admin/diagnostics?kind=client'), []);
  return (
    <section className="panel">
      <h2>Client 上傳的 PC 資訊</h2>
      <p className="dim">在各台 Client 以 Super 登入，開啟「PC 資訊」按「上傳到 Server」。</p>
      <LoadState loading={r.loading} error={r.error} reload={r.reload} />
      {r.data && !r.data.items.length ? <Msg kind="info">尚未收到任何 Client 的 PC 資訊</Msg> : null}
      {r.data && r.data.items.length ? <p>{osSummary(r.data.items)}</p> : null}
      {r.data && r.data.items.length ? (
        <table className="grid small">
          <thead><tr><th>時間</th><th>電腦</th><th>作業系統</th><th>CPU／RAM</th><th>主機板</th><th>顯示卡</th><th>螢幕</th><th>App</th></tr></thead>
          <tbody>{r.data.items.map((it) => (
            <tr key={it.id}>
              <td>{dateTime(it.created_at)}</td><td>{it.device_name || it.data.hostname}</td><td>{it.data.os}</td>
              <td>{it.data.cpu}（{it.data.cpu_cores}）／{it.data.memory_gb} GB</td>
              <td>{it.data.board ? boardText(it.data.board) : '未知（這台 Client 的版本沒有回報）'}</td>
              <td>{(it.data.gpu || []).map((g: any) => g.name || `${g.vendor_id}:${g.device_id}`).join('、') || '—'}</td>
              <td>{(it.data.screens || []).map((s: any) => `${s.width}×${s.height}`).join('、')}</td>
              <td>{it.data.app ? it.data.app.label : ''}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
    </section>
  );
}

function DiagnoseHistory(): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<{ items: any[] }>('/api/admin/diagnostics?kind=server'), []);
  const [open, setOpen] = useState<number | null>(null);
  const run = async () => {
    try {
      await api.post('/api/admin/diagnostics', {});
      toast('已排入 Server 診斷', 'ok');
      setTimeout(r.reload, 8000);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <section className="panel">
      <div className="row"><h2>歷次 Server 診斷</h2><span className="grow" /><Btn kind="primary" onClick={run}>重跑 Server 診斷</Btn><Btn onClick={r.reload}>重新整理</Btn></div>
      <LoadState loading={r.loading} error={r.error} reload={r.reload} />
      {r.data && !r.data.items.length ? <Msg kind="info">尚無診斷結果</Msg> : null}
      {r.data ? (
        <ul className="plain">
          {r.data.items.map((it) => (
            <li key={it.id}>
              <button type="button" className="rowlink" onClick={() => setOpen(open === it.id ? null : it.id)}>
                {open === it.id ? '▼' : '▶'} {dateTime(it.created_at)}　{it.data.status === 'pass' ? '✓ 全部通過' : it.data.status === 'warn' ? '⚠ 有警告' : '✗ 有失敗'}
              </button>
              {open === it.id ? <CheckList items={it.data.checks || []} /> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
