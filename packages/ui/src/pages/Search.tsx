/** 搜尋（第 08 章）：名稱、Tag、字幕；權限在 Server 端過濾，最多 200 筆。 */
import React, { useState } from 'react';
import { api, errText, qs } from '../api';
import { Btn, Msg, PageHead, Tabs, useApp } from '../core';
import { clock, KIND_ICON } from '../format';
import { openMedia, stateText } from './Courses';

export function SearchPage(): any {
  const { go } = useApp();
  const [q, setQ] = useState('');
  const [type, setType] = useState('all');
  const [scope, setScope] = useState<string[]>(['name', 'tag']);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    if (!q.trim()) return setErr('請輸入關鍵字');
    setErr(null);
    try {
      setRes(await api.get('/api/search' + qs({ q: q.trim(), type, scope: scope.join(',') })));
    } catch (e) {
      setErr(errText(e));
    }
  };
  const toggleScope = (s: string) => setScope(scope.includes(s) ? scope.filter((x) => x !== s) : [...scope, s]);
  const fav = async (r: any) => {
    try {
      const x = await api.put(`/api/media/${r.id}/bookmark`, { is_favorite: true });
      setRes({ ...res, results: res.results.map((y: any) => (y === r ? { ...y, ...x } : y)) });
    } catch (e) {
      setErr(errText(e));
    }
  };
  return (
    <div className="page">
      <PageHead title="搜尋" />
      <div className="search-bar">
        <input className="big" value={q} onChange={(e: any) => setQ(e.target.value)} onKeyDown={(e: any) => { if (e.key === 'Enter') void run(); }}
          placeholder="輸入教材名稱、資料夾名稱、Tag 或字幕文字" aria-label="搜尋關鍵字" autoFocus />
        <Btn kind="primary" onClick={run}>搜尋</Btn>
      </div>
      <div className="row">
        <span>類型：</span>
        <Tabs small value={type} onChange={setType} tabs={[{ id: 'all', label: '全部' }, { id: 'video', label: '影片' }, { id: 'doc', label: '文件' }, { id: 'folder', label: '資料夾' }]} />
        <span>範圍：</span>
        {[['name', '名稱'], ['tag', 'Tag'], ['subtitle', '字幕']].map(([k, label]) => (
          <label key={k} className="check"><input type="checkbox" checked={scope.includes(k)} onChange={() => toggleScope(k)} /> {label}</label>
        ))}
      </div>
      {err ? <Msg kind="error">{err}</Msg> : null}
      {res && !res.indexed ? <Msg kind="info">教材索引尚未建立，請洽管理者執行掃描</Msg> : null}
      {res && res.truncated ? <Msg kind="info">結果超過 200 筆，只顯示前 200 筆，請輸入更精確的關鍵字</Msg> : null}
      {res && !res.results.length ? <Msg kind="info">找不到「{q}」</Msg> : null}
      {res && res.results.length ? (
        <table className="grid">
          <thead><tr><th>名稱</th><th>路徑</th><th>Tag</th><th>操作</th></tr></thead>
          <tbody>
            {res.results.map((r: any, i: number) => (
              <tr key={i}>
                <td>
                  {r.type === 'folder' ? `📁 ${r.name}` : r.type === 'subtitle' ? <>💬 字幕 {clock(r.start)}「{r.text}」<div className="dim">{r.title}</div></>
                    : <>{KIND_ICON[r.kind] || '📄'} {r.title}{stateText(r) ? <div className="row-state">{stateText(r)}</div> : null}</>}
                </td>
                <td>{r.path || (r.type === 'folder' ? '課程資料夾' : '')}</td>
                <td>{r.tags ? r.tags.join('、') : ''}</td>
                <td className="actions">
                  {r.type === 'folder' ? <Btn small onClick={() => go('courses', { folder: r.id })}>前往</Btn> : null}
                  {r.type === 'subtitle' ? <Btn small onClick={() => go('player', { id: r.id, start: r.start })}>從此播放</Btn> : null}
                  {r.type === 'media' ? (
                    <>
                      <Btn small disabled={r.state !== 'ready'} onClick={() => openMedia(go, r)}>{r.kind === 'video' ? '播放' : '開啟'}</Btn>
                      <Btn small onClick={() => fav(r)} disabled={r.is_favorite}>{r.is_favorite ? '❤' : '♡'}</Btn>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
