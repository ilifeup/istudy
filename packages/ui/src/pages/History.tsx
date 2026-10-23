/** 學習歷程（瀏覽記錄，第 08 章）：依最後瀏覽時間排序；位置顯示約略值；狀態三選一由使用者手動選。 */
import React, { useState } from 'react';
import { api, errText } from '../api';
import { LoadState, Msg, PageHead, useApp, useLoad } from '../core';
import { approxPosition, dateTime, KIND_ICON, LEARNING } from '../format';
import { openMedia, stateText } from './Courses';

export function HistoryPage(): any {
  const { go, toast } = useApp();
  const h = useLoad(() => api.get<{ items: any[] }>('/api/history'), []);
  const [busy, setBusy] = useState<number | null>(null);
  const setStatus = async (item: any, status: string) => {
    setBusy(item.id);
    try {
      await api.put(`/api/media/${item.id}/status`, { status });
      h.setData({ items: h.data!.items.map((x) => (x.id === item.id ? { ...x, learning_status: status } : x)) });
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="page">
      <PageHead title="學習歷程" />
      <LoadState loading={h.loading} error={h.error} reload={h.reload} />
      {h.data && !h.data.items.length ? <Msg kind="info">還沒有瀏覽記錄。從「課程目錄」開啟教材後會出現在這裡。</Msg> : null}
      <ul className="cards-list">
        {h.data ? h.data.items.map((it) => {
          const unavailable = !it.has_access ? '🔒 目前無閱覽權限' : stateText(it) && it.state === 'missing'
            ? '⚠ 教材目前不可用（已移除或搬移，待管理者重新掃描）；紀錄仍保留' : null;
          const pos = it.kind === 'video' ? (it.last_position_sec ? '上次' + approxPosition(it.last_position_sec) : '')
            : it.last_page ? `上次約第 ${it.last_page} 頁` : '';
          return (
            <li key={it.id} className={'item-card' + (unavailable ? ' unavailable' : '')}>
              <div className="item-main">
                <div className="item-title">{KIND_ICON[it.kind] || '📄'} {it.title}</div>
                <div className="dim">{it.path ? it.path + ' · ' : ''}{dateTime(it.last_viewed_at)}{pos ? ' · ' + pos : ''}</div>
                {unavailable ? <div className="row-state">{unavailable}</div> : null}
              </div>
              <div className="item-actions">
                <label className="inline-select">
                  <span>狀態：</span>
                  <select value={it.learning_status || 'not_started'} disabled={busy === it.id || !it.has_access}
                    onChange={(e: any) => void setStatus(it, e.target.value)}>
                    {Object.entries(LEARNING).map(([k, v]) => <option key={k} value={k}>{v.mark} {v.text}</option>)}
                  </select>
                </label>
                <button type="button" className="btn plain" disabled={!!unavailable || it.state !== 'ready'}
                  onClick={() => openMedia(go, it, it.kind !== 'video' && it.last_page ? { page: it.last_page } : {})}>開啟教材</button>
              </div>
            </li>
          );
        }) : null}
      </ul>
    </div>
  );
}
