/**
 * 課程目錄（第 06 章）：左側多層目錄樹＋右側檔案清單，操作接近 Windows 檔案總管。
 * - 目錄樹獨立捲動、寬度可拖曳並記住；展開狀態存在本機（同一使用者下次登入保留）。
 * - 資料夾在前，其餘依名稱自然排序；麵包屑、↑ 上一層、⟳ 重新整理。
 * - 狀態一律有符號與文字（⏳ 轉檔中、⚠ 教材目前不可用、🔒 無權限）。
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, errText } from '../api';
import { atLeast, Btn, LoadState, Msg, useApp, useLoad, useLocalState } from '../core';
import { duration, KIND_ICON, KIND_TEXT, learningText, size } from '../format';

export interface TreeFolder { id: number; parent_id: number | null; name: string; is_course: boolean; sort_order: number; has_children: boolean }
export interface MediaItem {
  id: number; title: string; file_name: string; kind: string; ext: string; size: number; duration: number | null; status: string;
  state: string; folder_id: number | null; course_id: number | null; tags: string[]; has_subtitle: boolean;
  clip: { group_id: number; name: string; seq: number; count: number; source: string } | null;
  learning_status?: string; is_favorite?: boolean; is_bookmarked?: boolean; convert_status?: string; doc_status?: string;
  last_position_sec?: number | null; last_page?: number | null; last_viewed_at?: string | null;
}

/** 從其他分頁回來時保留上次位置（同一次登入內）。 */
const memory: { folderId: number | null } = { folderId: null };

export function stateText(m: MediaItem): string | null {
  switch (m.state) {
    case 'converting': return '⏳ 轉檔中，暫不可播放';
    case 'doc_converting': return '⏳ 文件轉換中';
    case 'missing': return '⚠ 教材目前不可用（待管理者重新掃描）';
    case 'failed': return m.kind === 'video' ? '⚠ 轉檔失敗（已通知管理者）' : '⚠ 文件轉換失敗';
    default: return null;
  }
}

export function openMedia(go: (p: string, params?: Record<string, any>) => void, m: { id: number; kind: string }, extra: Record<string, any> = {}): void {
  go(m.kind === 'video' ? 'player' : 'doc', { id: m.id, ...extra });
}

export function NoAccess(props: { course?: string }): any {
  return (
    <div className="no-access" role="alert">
      🔒 尚未取得閱覽權限：管理員尚未開放『{props.course || '此課程'}』，請聯絡管理員
    </div>
  );
}

export function ClipBadge(props: { clip: MediaItem['clip']; onOpen?: () => void }): any {
  if (!props.clip) return null;
  return (
    <button type="button" className="clip-badge" title={props.clip.name} onClick={(e: any) => { e.stopPropagation(); props.onOpen?.(); }}>
      ［合集 {props.clip.seq}/{props.clip.count}］
    </button>
  );
}

function Tree(props: { folders: TreeFolder[]; selected: number | null; expanded: number[]; onToggle: (id: number) => void; onSelect: (id: number) => void }): any {
  const children = useMemo(() => {
    const m = new Map<number | null, TreeFolder[]>();
    for (const f of props.folders) {
      const k = f.parent_id;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(f);
    }
    return m;
  }, [props.folders]);
  const render = (parent: number | null, depth: number): any => (children.get(parent) || []).map((f) => {
    const open = props.expanded.includes(f.id);
    const sel = props.selected === f.id;
    return (
      <li key={f.id} role="treeitem" aria-expanded={f.has_children ? open : undefined} aria-selected={sel}>
        <div className={'tree-row' + (sel ? ' sel' : '')} style={{ paddingLeft: 6 + depth * 18 }}>
          {f.has_children
            ? <button type="button" className="twisty" aria-label={open ? '收合' : '展開'} onClick={() => props.onToggle(f.id)}>{open ? '▼' : '▶'}</button>
            : <span className="twisty-space" />}
          <button type="button" className="tree-name" onClick={() => props.onSelect(f.id)}>
            {f.name}{sel ? ' ◀' : ''}
          </button>
        </div>
        {open && f.has_children ? <ul role="group">{render(f.id, depth + 1)}</ul> : null}
      </li>
    );
  });
  return <ul className="tree" role="tree">{render(null, 0)}</ul>;
}

export function CoursesPage(props: { params?: Record<string, any> }): any {
  const { account, go } = useApp();
  const isAdmin = atLeast(account, 'admin');
  const tree = useLoad(() => api.get<{ folders: TreeFolder[]; indexed: boolean }>('/api/tree'), []);
  const [expanded, setExpanded] = useLocalState<number[]>(`istudy.tree.${account.id}`, []);
  const [treeWidth, setTreeWidth] = useLocalState<number>('istudy.treeWidth', 300);
  const [showTags, setShowTags] = useLocalState<boolean>('istudy.showTags', false);
  const [folderId, setFolderId] = useState<number | null>(props.params && props.params.folder ? Number(props.params.folder) : memory.folderId);
  const [clipOpen, setClipOpen] = useState<MediaItem | null>(null);
  const [rowMsg, setRowMsg] = useState<string | null>(null);
  const items = useLoad(() => folderId === null ? Promise.resolve(null)
    : api.get<{ folder: { id: number; name: string; parent_id: number | null }; breadcrumbs: { id: number; name: string }[]; folders: { id: number; name: string }[]; media: MediaItem[] }>(`/api/folders/${folderId}/items`),
  [folderId]);
  const dragging = useRef(false);

  useEffect(() => { memory.folderId = folderId; }, [folderId]);
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (dragging.current) setTreeWidth(Math.max(200, Math.min(600, e.clientX - 12)));
    };
    const up = () => { dragging.current = false; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, []);

  const folders = tree.data ? tree.data.folders : [];
  const toggle = (id: number) => setExpanded(expanded.includes(id) ? expanded.filter((x) => x !== id) : [...expanded, id]);
  const select = (id: number) => {
    setRowMsg(null);
    setFolderId(id);
    // 選取時自動展開到該資料夾
    const chain: number[] = [];
    let cur = folders.find((f) => f.id === id);
    while (cur && cur.parent_id !== null) {
      chain.push(cur.parent_id);
      cur = folders.find((f) => f.id === cur!.parent_id);
    }
    const next = Array.from(new Set([...expanded, ...chain, id]));
    if (next.length !== expanded.length) setExpanded(next);
  };

  const toggleFav = async (m: MediaItem) => {
    try {
      const r = await api.put(`/api/media/${m.id}/bookmark`, { is_favorite: !m.is_favorite });
      items.setData(items.data ? { ...items.data, media: items.data.media.map((x) => (x.id === m.id ? { ...x, ...r } : x)) } : null);
    } catch (e) {
      setRowMsg(errText(e));
    }
  };

  const open = (m: MediaItem) => {
    const t = stateText(m);
    if (t) {
      setRowMsg(t);
      return;
    }
    openMedia(go, m);
  };

  const noAccessCourse = useNoAccess(items.raw);
  const noAccess = noAccessCourse !== null;
  const err = items.error && !noAccess ? items.error : null;
  const crumbs = items.data ? items.data.breadcrumbs : [];
  const parent = items.data ? items.data.folder.parent_id : null;
  const courses = folders.filter((f) => f.parent_id === null);

  return (
    <div className="explorer" style={{ gridTemplateColumns: `${treeWidth}px 6px 1fr` }}>
      <aside className="tree-pane" aria-label="課程目錄樹">
        <div className="pane-title">▼ 複習根目錄</div>
        <LoadState loading={tree.loading} error={tree.error} reload={tree.reload} />
        {tree.data && !tree.data.indexed ? <Msg kind="info">教材索引尚未建立，請洽管理者執行掃描</Msg> : null}
        {tree.data && tree.data.indexed && !courses.length ? <Msg kind="info">目前沒有可閱覽的課程，請洽管理員開放</Msg> : null}
        <Tree folders={folders} selected={folderId} expanded={expanded} onToggle={toggle} onSelect={select} />
      </aside>
      <div className="splitter" role="separator" aria-orientation="vertical" onMouseDown={() => { dragging.current = true; }} />
      <section className="list-pane" aria-label="檔案清單">
        <div className="crumbbar">
          <span className="dim">目前位置：</span>
          {folderId === null ? <span>複習根目錄</span> : crumbs.map((c, i) => (
            <span key={c.id}>
              {i > 0 ? ' / ' : ''}
              <button type="button" className="crumb" onClick={() => select(c.id)}>{c.name}</button>
            </span>
          ))}
          <span className="grow" />
          <Btn small disabled={folderId === null} onClick={() => { setFolderId(parent); }}>↑ 上一層</Btn>
          <Btn small onClick={() => { tree.reload(); items.reload(); }}>⟳ 重新整理</Btn>
          <label className="check"><input type="checkbox" checked={showTags} onChange={(e: any) => setShowTags(e.target.checked)} /> 顯示 Tag</label>
        </div>
        {rowMsg ? <Msg kind="warn" action={<Btn small kind="quiet" onClick={() => setRowMsg(null)}>✕</Btn>}>{rowMsg}</Msg> : null}
        {folderId === null ? (
          <table className="grid files">
            <thead><tr><th>名稱</th><th>類型</th></tr></thead>
            <tbody>
              {courses.map((c) => (
                <tr key={c.id} className="clickable" onDoubleClick={() => select(c.id)}>
                  <td><button type="button" className="rowlink" onClick={() => select(c.id)}>📁 {c.name}</button></td>
                  <td>課程</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : noAccess ? <NoAccess course={noAccessCourse || undefined} /> : (
          <>
            {err ? <LoadState loading={false} error={err} reload={items.reload} /> : null}
            {items.loading ? <div className="loading">… 讀取中</div> : null}
            {items.data ? (
              <table className="grid files">
                <thead>
                  <tr>
                    <th>名稱</th><th>類型</th><th className="num">長度</th><th className="num">大小</th><th>學習狀態</th><th>最愛</th>
                    {showTags ? <th>Tag</th> : null}
                    {isAdmin ? <th className="num">教材 ID</th> : null}
                    {isAdmin ? <th>轉檔狀態</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {items.data.folders.map((f) => (
                    <tr key={'f' + f.id} className="clickable" onDoubleClick={() => select(f.id)}>
                      <td><button type="button" className="rowlink" onClick={() => select(f.id)}>📁 {f.name}</button></td>
                      <td>資料夾</td><td /><td /><td /><td />
                      {showTags ? <td /> : null}{isAdmin ? <td /> : null}{isAdmin ? <td /> : null}
                    </tr>
                  ))}
                  {items.data.media.map((m) => {
                    const st = stateText(m);
                    return (
                      <tr key={m.id} className={'clickable' + (st ? ' unavailable' : '')} onDoubleClick={() => open(m)}>
                        <td>
                          <button type="button" className="rowlink" onClick={() => open(m)}>{KIND_ICON[m.kind] || '📄'} {m.title}</button>
                          {' '}<ClipBadge clip={m.clip} onOpen={() => setClipOpen(m)} />
                          {st ? <div className="row-state">{st}</div> : null}
                        </td>
                        <td>{KIND_TEXT[m.kind] || m.kind}</td>
                        <td className="num">{duration(m.duration)}</td>
                        <td className="num">{size(m.size)}</td>
                        <td>{learningText(m.learning_status)}</td>
                        <td>
                          <button type="button" className="btn small quiet" aria-pressed={!!m.is_favorite} onClick={() => void toggleFav(m)}>
                            {m.is_favorite ? '❤ 已加入' : '♡'}
                          </button>
                        </td>
                        {showTags ? <td className="tags-cell">{m.tags.join('、')}</td> : null}
                        {isAdmin ? <td className="num">{m.id}</td> : null}
                        {isAdmin ? <td>{convertText(m)}</td> : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : null}
            {items.data && !items.data.folders.length && !items.data.media.length ? <Msg kind="info">這個資料夾是空的</Msg> : null}
          </>
        )}
        {clipOpen && clipOpen.clip ? <ClipPopover m={clipOpen} onClose={() => setClipOpen(null)} /> : null}
      </section>
    </div>
  );
}

export function convertText(m: { kind: string; convert_status?: string; doc_status?: string }): string {
  if (m.kind === 'office') return ({ pending: '⏳ 等待轉換', converting: '⏳ 轉換中', done: '✓ 已轉 PDF', failed: '✗ 轉換失敗', none: '—' } as any)[m.doc_status || 'none'] || '—';
  if (m.kind !== 'video') return '—';
  return ({
    none: '—', pending: '⏳ 等待檢測', probing: '⏳ 檢測中', queued: '⏳ 等待轉檔', converting: '⏳ 轉檔中', verifying: '⏳ 驗證中',
    done: '✓ 完成', direct: '✓ 直接播放', failed: '✗ 轉檔失敗', verify_failed: '✗ 驗證失敗',
  } as any)[m.convert_status || 'none'] || m.convert_status || '—';
}

function ClipPopover(props: { m: MediaItem; onClose: () => void }): any {
  const { go } = useApp();
  const d = useLoad(() => api.get(`/api/media/${props.m.id}`), [props.m.id]);
  return (
    <div className="popover" role="dialog" aria-label="合集">
      <div className="row between">
        <strong>合集：{props.m.clip!.name}</strong>
        <Btn small kind="quiet" onClick={props.onClose}>✕ 關閉</Btn>
      </div>
      <p className="dim">同一次錄影被攝影機切成 {props.m.clip!.count} 段；每段仍是獨立的一集。</p>
      <LoadState loading={d.loading} error={d.error} />
      <ol className="clip-list">
        {d.data ? d.data.clip_members.map((c: any) => (
          <li key={c.id}>
            <button type="button" className={'rowlink' + (c.id === props.m.id ? ' current' : '')} onClick={() => go('player', { id: c.id })}>
              第 {c.seq} 段：{c.title}{c.id === props.m.id ? '（目前）' : ''}
            </button>
          </li>
        )) : null}
      </ol>
    </div>
  );
}

export function useNoAccess(e: unknown): string | null {
  return e instanceof ApiError && e.code === 'no_access' ? (e.extra.course_name || '') : null;
}
