/**
 * 原檔刪除確認（第 11 章）：dev.60 起列出所有受管理的原檔（需要轉成 MP4 的影片），提前查看轉檔狀態、四項檢查、保留天數與剩餘天數。
 * 只有「MP4 四項檢查通過、而且已滿 30 天」的原檔可以勾選；勾選後輸入「永久刪除」才排入刪除工作。
 * Server 與 Agent 刪除前都會再確認一次（媒體標記、MP4 存在且大小與檢查時相同、仍在到期清單中），這一頁不放寬任何條件。
 */
import React, { useState } from 'react';
import { api } from '../../../../packages/ui/src/api';
import { Btn, LoadState, Msg, PageHead, Tabs, TypedConfirm, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, size } from '../../../../packages/ui/src/format';
import { convertText } from '../../../../packages/ui/src/pages/Courses';
import { PLAY_MODE, VerifyResult } from './Conversions';

export function OriginalsPage(): any {
  const { go, toast } = useApp();
  const r = useLoad(() => api.get<{ items: any[]; keep_days: number; eligible: number }>('/api/admin/originals'), []);
  const [sel, setSel] = useState<number[]>([]);
  const [confirm, setConfirm] = useState(false);
  const [tab, setTab] = useState('all');
  const all = r.data ? r.data.items : [];
  const keep = r.data ? r.data.keep_days : 30;
  const items = tab === 'eligible' ? all.filter((m) => m.eligible) : all;
  const deleting = all.some((m) => m.deleting);
  useInterval(() => r.reload(), deleting ? 5000 : null);
  const avail = all.filter((m) => m.eligible && !m.deleting);
  const total = all.filter((m) => sel.includes(m.id)).reduce((s, m) => s + m.original_size, 0);
  const toggle = (id: number) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);
  return (
    <div className="page">
      <PageHead title="原檔刪除確認">
        <Btn kind="danger" disabled={!sel.length} onClick={() => setConfirm(true)}>永久刪除原檔（{sel.length}）</Btn>
      </PageHead>
      <Msg kind="info">列出所有轉成 MP4 播放的影片原檔。只有 MP4 四項檢查全部通過、而且檢查通過後已滿 {keep} 天的原檔可以勾選刪除；
        其他的先列出來查看，不能刪除。刪除前建議先「播放抽查」。原檔刪除後無法復原，教材改為播放 MP4。</Msg>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'all', label: `全部（${all.length}）` },
        { id: 'eligible', label: `可以刪除（${r.data ? r.data.eligible : 0}）` },
      ]} />
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data && !items.length ? <Msg kind="info">{tab === 'eligible' ? '目前沒有到期的原檔' : '沒有需要管理的原檔（目前的影片都直接播放）'}</Msg> : null}
      {items.length ? (
        <table className="grid small">
          <thead>
            <tr>
              <th><input type="checkbox" aria-label="全選可以刪除的原檔" disabled={!avail.length} checked={avail.length > 0 && sel.length === avail.length}
                onChange={() => setSel(sel.length === avail.length ? [] : avail.map((m) => m.id))} /></th>
              <th>教材</th><th>原檔</th><th>MP4</th><th>轉檔狀態</th><th>四項檢查</th><th>保留天數</th><th>剩餘天數</th><th>可否刪除</th><th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.id}>
                <td>{m.deleting ? '…' : m.eligible
                  ? <input type="checkbox" checked={sel.includes(m.id)} onChange={() => toggle(m.id)} aria-label={`選取 ${m.title}`} />
                  : <span title="不能刪除" aria-label="不能刪除">－</span>}</td>
                <td>{m.title}<div className="dim">{m.path}</div></td>
                <td className="path">{m.rel_path}<div className="dim">{size(m.original_size)}</div></td>
                <td>{m.playback_size !== null ? size(m.playback_size) : '—'}<div className="dim">{PLAY_MODE[m.play_mode] || m.play_mode}{m.lossy ? '（畫質與原檔不同）' : ''}</div></td>
                <td>{convertText({ kind: 'video', convert_status: m.convert_status })}</td>
                <td><VerifyResult v={m.verify} />{m.verified_at ? <div className="dim">{dateTime(m.verified_at)}</div> : null}</td>
                <td className="nowrap">{m.kept_days !== null ? `${m.kept_days} 天／${keep} 天` : '—（檢查通過後開始計算）'}</td>
                <td className="nowrap">{m.remain_days !== null ? (m.remain_days > 0 ? `剩 ${m.remain_days} 天` : '已滿') : '—'}
                  {m.delete_due_at ? <div className="dim">{dateTime(m.delete_due_at)}</div> : null}</td>
                <td>{m.deleting ? '… 刪除中' : m.eligible ? '✓ 可以刪除' : <span>✗ {m.reason}</span>}</td>
                <td>{m.convert_status === 'done' && !m.deleting ? <Btn small onClick={() => go('player', { id: m.id })}>播放抽查</Btn> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {confirm ? (
        <TypedConfirm title="永久刪除原檔" expect="永久刪除" confirmText="永久刪除原檔"
          message={<div className="stack">
            <p>即將刪除 {sel.length} 個原檔，共 {size(total)}。刪除後無法復原。</p>
            <ul className="plain small">{all.filter((m) => sel.includes(m.id)).slice(0, 30).map((m) => <li key={m.id}>{m.rel_path}</li>)}</ul>
          </div>}
          onConfirm={async () => {
            const x = await api.post('/api/admin/originals/delete', { media_ids: sel, confirm: '永久刪除' });
            toast(`已排入 ${x.job_ids.length} 個刪除工作；結果寫入使用記錄`, 'ok');
            setSel([]);
            r.reload();
          }} onClose={() => setConfirm(false)} />
      ) : null}
    </div>
  );
}
