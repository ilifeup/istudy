/** 使用記錄（第 09 章）：Admin 只看最近 14 天、看不到 Super；Super 看全部並可手動清除；匯出 CSV 需授權。 */
import React, { useState } from 'react';
import { api, errText, fetchText, qs } from '../api';
import { bridge } from '../bridge';
import { Btn, Field, LoadState, Msg, PageHead, TypedConfirm, useApp, useLoad } from '../core';
import { dateTime } from '../format';

const ACTION_TEXT: Record<string, string> = {
  login: '登入', logout: '登出', 'login.failed': '登入失敗', 'media.open_video': '開啟影片', 'media.open_document': '開啟文件',
  'tag.added': '新增 Tag', 'tag.edited': '修改 Tag', 'tag.removed': '移除 Tag', 'wol.wake': 'WOL 喚醒',
  'account.created': '新增帳號', 'account.updated': '帳號變更', 'account.deleted': '刪除帳號', 'account.password_reset': '重設密碼',
  'account.overrides_updated': '權限變更（個人覆寫）', 'group.courses_updated': '權限變更（群組）', 'subtitle.imported': '匯入字幕', 'subtitle.asr_requested': '產生字幕（自動辨識）',
  'subtitle.exported': '匯出字幕', 'audit.exported': '匯出使用記錄', 'audit.cleared': '清除使用記錄', 'password.changed': '修改密碼',
};

export function AuditPage(): any {
  const { account, toast } = useApp();
  const [f, setF] = useState({ account: '', action: '', from: '', to: '' });
  const [applied, setApplied] = useState(f);
  const accounts = useLoad(() => api.get<{ accounts: any[] }>('/api/accounts'), []);
  const r = useLoad(() => api.get<{ items: any[]; limited_to_days: number | null }>('/api/audit' + qs(applied)), [JSON.stringify(applied)]);
  const [clearOpen, setClearOpen] = useState(false);
  const exportCsv = async () => {
    try {
      const t = await fetchText('/api/audit/export' + qs(applied));
      const s = await bridge.saveFile(t.filename || '使用記錄.csv', t.text);
      if (s.ok) toast('已匯出：' + s.path, 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="page">
      <PageHead title="使用記錄">
        {account.can_import_export ? <Btn onClick={exportCsv}>匯出 CSV</Btn> : null}
        {account.role === 'super' ? <Btn kind="danger" onClick={() => setClearOpen(true)}>清除記錄…</Btn> : null}
      </PageHead>
      <section className="panel">
        <div className="form-row">
          <Field label="帳號">
            <select value={f.account} onChange={(e: any) => setF({ ...f, account: e.target.value })}>
              <option value="">全部</option>
              {(accounts.data ? accounts.data.accounts : []).map((a) => <option key={a.id} value={a.id}>{a.full_name || a.username}</option>)}
            </select>
          </Field>
          <Field label="動作">
            <select value={f.action} onChange={(e: any) => setF({ ...f, action: e.target.value })}>
              <option value="">全部</option>
              {Object.entries(ACTION_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="從"><input type="date" value={f.from} onChange={(e: any) => setF({ ...f, from: e.target.value })} /></Field>
          <Field label="到"><input type="date" value={f.to} onChange={(e: any) => setF({ ...f, to: e.target.value })} /></Field>
          <Btn kind="primary" onClick={() => setApplied({ ...f })}>篩選</Btn>
        </div>
        {r.data && r.data.limited_to_days ? <p className="dim">管理員只顯示最近 {r.data.limited_to_days} 天；每個帳號最多 300 筆。</p> : null}
      </section>
      <LoadState loading={r.loading} error={r.error} reload={r.reload} />
      {r.data && !r.data.items.length ? <Msg kind="info">沒有符合的記錄</Msg> : null}
      {r.data && r.data.items.length ? (
        <table className="grid small">
          <thead><tr><th>時間</th><th>帳號</th><th>角色／群組</th><th>動作</th><th>路徑／教材</th><th>來源電腦</th></tr></thead>
          <tbody>
            {r.data.items.map((it) => (
              <tr key={it.id}>
                <td className="nowrap">{dateTime(it.created_at, true)}</td>
                <td>{it.actor_name || '（未登入）'}{it.actor_id ? <span className="dim"> #{it.actor_id}</span> : null}</td>
                <td>{it.actor_role}{it.actor_groups ? ' · ' + it.actor_groups : ''}</td>
                <td>{ACTION_TEXT[it.action] || it.action}{it.details && it.details.tag ? `「${it.details.tag}」` : ''}</td>
                <td>{it.path}{it.media_id ? <span className="dim">（教材 {it.media_id}）</span> : null}</td>
                <td>{it.client_name || ''} {it.client_ip}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {clearOpen ? (
        <ClearAudit onClose={() => setClearOpen(false)} onDone={() => r.reload()} />
      ) : null}
    </div>
  );
}

function ClearAudit(props: { onClose: () => void; onDone: () => void }): any {
  const { toast } = useApp();
  const [before, setBefore] = useState('');
  return (
    <TypedConfirm title="清除使用記錄" expect="清除" confirmText="清除記錄"
      message={<div className="stack">
        <p>使用記錄預設永久保留。清除後無法復原，清除本身也會留下一筆紀錄。</p>
        <Field label="只清除這一天之前的記錄（空白＝全部）"><input type="date" value={before} onChange={(e: any) => setBefore(e.target.value)} /></Field>
      </div>}
      onConfirm={async () => {
        const r = await api.del('/api/audit', { confirm: '清除', before: before || null });
        toast(`已清除 ${r.deleted} 筆`, 'ok');
        props.onDone();
      }} onClose={props.onClose} />
  );
}
