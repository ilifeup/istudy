/** 書籤與最愛（第 08 章）：同一筆資料的兩個欄位；取消最愛不刪書籤；移除書籤連最愛一起移除（先確認）。 */
import React, { useState } from 'react';
import { api, errText, qs } from '../api';
import { Btn, LoadState, Msg, PageHead, Tabs, useApp, useLoad } from '../core';
import { KIND_ICON } from '../format';
import { openMedia } from './Courses';

export function BookmarksPage(): any {
  const { go, toast } = useApp();
  const [filter, setFilter] = useState('all');
  const [tag, setTag] = useState('');
  const b = useLoad(() => api.get<{ items: any[]; tags: string[] }>('/api/bookmarks' + qs({
    favorite: filter === 'fav' ? 1 : undefined, type: filter === 'video' || filter === 'doc' ? filter : undefined, tag: tag || undefined,
  })), [filter, tag]);
  const update = async (it: any, body: Record<string, boolean>) => {
    if (body.is_bookmarked === false && !window.confirm(`移除「${it.title}」的書籤？${it.is_favorite ? '（最愛也會一起移除）' : ''}`)) return;
    try {
      await api.put(`/api/media/${it.id}/bookmark`, body);
      b.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="page">
      <PageHead title="我的書籤">
        <Tabs small value={filter} onChange={setFilter} tabs={[
          { id: 'all', label: '全部' }, { id: 'fav', label: '❤ 最愛' }, { id: 'video', label: '影片' }, { id: 'doc', label: '文件' }]} />
        <label className="inline-select">
          <span>Tag 篩選：</span>
          <select value={tag} onChange={(e: any) => setTag(e.target.value)}>
            <option value="">全部</option>
            {(b.data ? b.data.tags : []).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </PageHead>
      <LoadState loading={b.loading} error={b.error} reload={b.reload} />
      {b.data && !b.data.items.length ? <Msg kind="info">沒有符合的書籤。在播放器或文件檢視器按「🔖 加入書籤」或「♡ 加入最愛」。</Msg> : null}
      <ul className="cards-list">
        {b.data ? b.data.items.map((it) => (
          <li key={it.id} className={'item-card' + (!it.has_access ? ' unavailable' : '')}>
            <div className="item-main">
              <div className="item-title">{it.is_favorite ? '❤' : '♡'} {KIND_ICON[it.kind] || '📄'} {it.title}</div>
              <div className="dim">{it.has_access ? it.path : '🔒 目前無閱覽權限'}{it.tags.length ? ' · Tag：' + it.tags.join('、') : ''}</div>
            </div>
            <div className="item-actions">
              <button type="button" className="btn plain" disabled={!it.has_access || it.state !== 'ready'} onClick={() => openMedia(go, it)}>開啟</button>
              {it.has_access ? <Btn onClick={() => update(it, { is_favorite: !it.is_favorite })}>{it.is_favorite ? '取消最愛' : '加入最愛'}</Btn> : null}
              <Btn kind="danger" onClick={() => update(it, { is_bookmarked: false })}>移除書籤</Btn>
            </div>
          </li>
        )) : null}
      </ul>
    </div>
  );
}
