/** 教材目錄（第 10、13 章）：目錄樹（dev.55）、深度掃描、試掃描、掃描紀錄、課程資料夾（顯示名稱、是否為課程、排序、啟用）。 */
import React, { useState } from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { Btn, LoadState, Modal, Msg, PageHead, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, size } from '../../../../packages/ui/src/format';

const RUN_STATUS: Record<string, string> = { running: '… 掃描中', done: '✓ 完成', failed: '✗ 失敗', rejected: '✗ 已拒絕' };

export function ScanSummary(props: { s: any }): any {
  const s = props.s || {};
  if (!s.files && s.files !== 0) return null;
  return (
    <table className="grid kv small">
      <tbody>
        <tr><th>資料夾／課程</th><td>{s.folders} 個資料夾，{s.courses} 個第一層課程資料夾</td></tr>
        <tr><th>檔案</th><td>{s.files} 個，共 {size(s.total_size)}</td></tr>
        <tr><th>需要轉檔的影片</th><td>{s.videos_need_convert} 支</td></tr>
        <tr><th>預估轉檔期間增加的空間</th><td>約 {size(s.estimated_extra_space)}（原檔保留 30 天，與 MP4 並存）
          {s.media_free !== undefined && s.media_free !== null ? `；/Media 目前剩餘 ${size(s.media_free)}` : ''}
          {s.media_free !== null && s.media_free !== undefined && s.estimated_extra_space > s.media_free ? '　⚠ 空間可能不足' : ''}</td></tr>
        <tr><th>略過（不支援或名稱有問題的檔案）</th><td>{s.skipped} 個</td></tr>
        {s.system_dirs ? <tr><th>系統資料夾（不掃描）</th><td>{s.system_dirs.length ? s.system_dirs.join('、') : '無'}</td></tr> : null}
        {s.by_ext ? <tr><th>副檔名分布</th><td>{Object.entries(s.by_ext).map(([k, v]: any) => `.${k} ${v.count} 個（${size(v.size)}）`).join('、')}</td></tr> : null}
      </tbody>
    </table>
  );
}

const KIND_TEXT: Record<string, string> = { video: '影片', pdf: 'PDF', office: 'Office 文件', text: '文字', image: '圖片' };
const CONVERT_TEXT: Record<string, string> = {
  none: '', pending: '… 等待檢測', probing: '… 檢測中', queued: '… 等待轉檔', converting: '… 轉檔中', verifying: '… 檢查中',
  done: '✓ 已轉 MP4', direct: '✓ 可直接播放', failed: '✗ 轉檔失敗', verify_failed: '✗ 檢查未通過',
};
const DOC_TEXT: Record<string, string> = { none: '○ 尚未轉 PDF', pending: '… 等待轉換', converting: '… 轉換中', done: '✓ 已轉 PDF', failed: '✗ 轉換失敗' };

function fileState(f: any): string {
  if (f.status === 'missing') return '⚠ 檔案遺失';
  if (f.status === 'original_deleted') return '原檔已刪除（保留 MP4）';
  if (f.kind === 'video') return CONVERT_TEXT[f.convert_status] || f.convert_status;
  if (f.kind === 'office') return DOC_TEXT[f.doc_status] || f.doc_status;
  return '✓ 已登錄';
}

/** dev.62 教材目錄樹的樣式（只在 Server App 這一頁，不改共用樣式，不影響 Client）。 */
const TREE_CSS = `
.mt { border: 1px solid var(--line); border-radius: var(--radius); background: var(--paper); }
.mt-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; padding: 10px 12px; border-bottom: 2px solid var(--line); background: var(--info-soft); }
.mt-head .mt-name { font-size: 17px; }
.mt-body { padding: 10px 12px; display: flex; flex-direction: column; gap: 10px; }
.mt-block { border: 1px solid var(--line); border-radius: var(--radius); }
.mt-block > .mt-row { padding: 8px 10px; }
.mt-block.open > .mt-row { border-bottom: 1px solid var(--line-soft); background: var(--pine-soft); }
.mt-block-end { font-size: 13px; color: var(--ink-3); padding: 4px 10px 6px; border-top: 1px dashed var(--line-soft); }
.mt-rootfiles { border-style: dashed; }
.mt-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; min-width: 0; }
.mt-name { font-weight: 600; overflow-wrap: anywhere; min-width: 0; }
.mt-tag { font-size: 14px; border: 1px solid var(--line); border-radius: var(--radius); padding: 0 6px; white-space: nowrap; }
.mt-path { font-size: 13px; overflow-wrap: anywhere; }
.mt-toggle { width: 1.8em; min-width: 1.8em; padding: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--paper); cursor: pointer; line-height: 1.4; }
span.mt-toggle { border-color: transparent; background: none; cursor: default; }
.mt-list { list-style: none; margin: 0; padding: 4px 10px 6px 26px; }
.mt-list .mt-list { padding: 2px 0 2px 26px; }
.mt-list > li { position: relative; padding: 3px 0 3px 14px; border-left: 1px solid var(--line); }
.mt-list > li:last-child { border-left-color: transparent; }
.mt-list > li::before { content: ""; position: absolute; left: 0; top: 0; width: 12px; height: 1.15em; border-left: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.mt-list > li > .mt-path { margin-left: 2.4em; }
.mt-note { color: var(--warn); font-size: 14px; }
`;

function FolderRow(props: { f: any; open: boolean; onToggle: () => void }): any {
  const f = props.f;
  return (
    <div className="mt-row">
      <button type="button" className="mt-toggle" onClick={props.onToggle} aria-expanded={props.open}
        aria-label={`${props.open ? '收合' : '展開'} ${f.name}`}>{props.open ? '▾' : '▸'}</button>
      <span className="mt-name">📁 {f.name}</span>
      {f.is_course ? <span className="mt-tag">{f.is_course_flag ? '☑ 課程（第一層）' : '第一層資料夾（未設為課程）'}</span> : null}
      {f.status === 'missing' ? <span className="mt-tag warn-text">⚠ 資料夾已不存在</span> : null}
      {!f.enabled ? <span className="mt-tag">✗ 停用</span> : null}
      <span className="dim">{f.subfolders} 個子資料夾、{f.files} 個檔案</span>
    </div>
  );
}

function FileItem(props: { f: any }): any {
  const f = props.f;
  return (
    <>
      <div className="mt-row">
        <span className="mt-toggle" aria-hidden="true" />
        <span className="mt-name">📄 {f.name}</span>
        <span className="mt-tag">{KIND_TEXT[f.kind] || f.kind}</span>
        <span className="dim">{size(f.size)}</span>
        <span>{fileState(f)}</span>
      </div>
      <div className="dim mt-path">{f.full_path}</div>
    </>
  );
}

/** 第二層以下：縮排＋樹狀引導線；資料夾在前、檔案在後。逐層載入。 */
function SubLevel(props: { folder: number }): any {
  const r = useLoad(() => api.get<any>(`/api/admin/tree?folder=${props.folder}`), [props.folder]);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (!r.data) return <div className="mt-list"><LoadState loading={r.loading} error={r.error} reload={r.reload} /></div>;
  const d = r.data;
  return (
    <ul className="mt-list" role="group">
      {d.folders.map((f: any) => (
        <li key={'d' + f.id} role="treeitem" aria-expanded={!!open[f.id]}>
          <FolderRow f={f} open={!!open[f.id]} onToggle={() => setOpen({ ...open, [f.id]: !open[f.id] })} />
          <div className="dim mt-path">{f.full_path}</div>
          {open[f.id] ? <SubLevel folder={f.id} /> : null}
        </li>
      ))}
      {d.files.map((f: any) => <li key={'f' + f.id} role="treeitem"><FileItem f={f} /></li>)}
      {!d.folders.length && !d.files.length ? <li className="dim">（沒有已掃描的內容）</li> : null}
      {d.truncated ? <li className="dim">只列出前 500 個檔案</li> : null}
    </ul>
  );
}

/** dev.55 FILE-01／dev.62：教材目錄樹（唯讀，資料來自掃描索引）。
 *  /Media 標頭 → 每個第一層資料夾一個有邊框的區塊（開始與結束都看得出來）→ 子層縮排＋引導線；
 *  直接放在 /Media 的檔案集中在最後的「根目錄檔案」區塊，不會看起來像屬於上面展開的資料夾。 */
function MaterialTree(): any {
  const r = useLoad(() => api.get<any>('/api/admin/tree'), []);
  const [key, setKey] = useState(0);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const root = r.data ? r.data.root : '/Media';
  const d = r.data;
  return (
    <section className="panel">
      <style>{TREE_CSS}</style>
      <div className="row between">
        <h2>目錄樹</h2>
        <Btn small onClick={() => { setKey(key + 1); r.reload(); }}>重新整理</Btn>
      </div>
      <p className="dim">顯示已掃描的內容（唯讀）。新放進資料夾的檔案，按「深度掃描教材」後才會出現。
        {d && d.last_scan_at ? `上次正式掃描：${dateTime(d.last_scan_at)}` : '尚未正式掃描'}</p>
      <div className="mt" role="tree" aria-label="教材目錄樹" key={key}>
        <div className="mt-head"><span className="mt-name">🗄 {root}（教材根目錄）</span>
          {d ? <span className="dim">{d.folders.length} 個第一層資料夾、{d.files.length} 個根目錄檔案</span> : null}</div>
        <div className="mt-body">
          {!d ? <LoadState loading={r.loading} error={r.error} reload={r.reload} /> : null}
          {d ? d.folders.map((f: any) => {
            const k = 'd' + f.id;
            return (
              <div key={k} className={'mt-block' + (open[k] ? ' open' : '')} role="treeitem" aria-expanded={!!open[k]}>
                <div className="mt-row"><FolderRow f={f} open={!!open[k]} onToggle={() => setOpen({ ...open, [k]: !open[k] })} /></div>
                <div className="dim mt-path" style={{ padding: '0 10px 6px 3.2em' }}>{f.full_path}</div>
                {open[k] ? <><SubLevel folder={f.id} /><div className="mt-block-end">— {f.name} 結束 —</div></> : null}
              </div>
            );
          }) : null}
          {d && d.files.length ? (
            <div className={'mt-block mt-rootfiles' + (open.root ? ' open' : '')} role="treeitem" aria-expanded={!!open.root}>
              <div className="mt-row">
                <button type="button" className="mt-toggle" onClick={() => setOpen({ ...open, root: !open.root })} aria-expanded={!!open.root}
                  aria-label={`${open.root ? '收合' : '展開'} 根目錄檔案`}>{open.root ? '▾' : '▸'}</button>
                <span className="mt-name">📄 根目錄檔案（直接放在 {root}）</span>
                <span className="dim">{d.files.length} 個檔案{d.truncated ? '（只列出前 500 個）' : ''}</span>
              </div>
              {d.root_files_note ? <div className="mt-note" style={{ padding: '0 10px 6px 3.2em' }}>⚠ {d.root_files_note}</div> : null}
              {open.root ? <><ul className="mt-list" role="group">
                {d.files.map((f: any) => <li key={'f' + f.id} role="treeitem"><FileItem f={f} /></li>)}
              </ul><div className="mt-block-end">— 根目錄檔案結束 —</div></> : null}
            </div>
          ) : null}
          {d && !d.folders.length && !d.files.length ? <div className="dim">（沒有已掃描的內容）</div> : null}
        </div>
      </div>
    </section>
  );
}

export function FoldersPage(): any {
  const { toast } = useApp();
  const runs = useLoad(() => api.get<{ runs: any[] }>('/api/admin/scan-runs'), []);
  const folders = useLoad(() => api.get<{ folders: any[] }>('/api/admin/folders'), []);
  const [detail, setDetail] = useState<number | null>(null);
  const busy = runs.data && runs.data.runs.some((r) => r.status === 'running');
  useInterval(() => { runs.reload(); if (busy) folders.reload(); }, busy ? 3000 : 20000);
  const scan = async (dry: boolean) => {
    try {
      const r = await api.post('/api/admin/scan', { dry_run: dry });
      toast(r.existing ? '已有相同的掃描在等待或執行中' : dry ? '已排入試掃描（不寫入教材資料）' : '已排入深度掃描', 'ok');
      setTimeout(runs.reload, 1500);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const patch = async (f: any, body: Record<string, any>) => {
    try {
      await api.patch(`/api/admin/folders/${f.id}`, body);
      folders.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="page">
      <PageHead title="教材目錄">
        <Btn onClick={() => scan(true)}>試掃描（只統計）</Btn>
        <Btn kind="primary" onClick={() => scan(false)}>深度掃描教材</Btn>
      </PageHead>
      <MaterialTree />
      <section className="panel">
        <h2>課程資料夾（/Media 第一層；playback、subtitles、doc-cache 等系統資料夾不列入）</h2>
        <LoadState loading={folders.loading && !folders.data} error={folders.error} reload={folders.reload} />
        {folders.data && !folders.data.folders.length ? <Msg kind="info">尚未掃描到資料夾；把教材放進 /Media 的課程資料夾後按「深度掃描教材」</Msg> : null}
        {folders.data && folders.data.folders.length ? (
          <table className="grid">
            <thead><tr><th>資料夾</th><th>顯示名稱</th><th>是否為課程</th><th>排序</th><th>啟用</th><th>教材數</th><th>狀態</th></tr></thead>
            <tbody>
              {folders.data.folders.map((f) => (
                <tr key={f.id}>
                  <td>📁 {f.name}</td>
                  <td>
                    <input defaultValue={f.display_name || ''} placeholder={f.name} aria-label={`${f.name} 顯示名稱`}
                      onBlur={(e: any) => { if (e.target.value !== (f.display_name || '')) void patch(f, { display_name: e.target.value }); }} />
                  </td>
                  <td>
                    <label className="check"><input type="checkbox" checked={f.is_course} onChange={() => void patch(f, { is_course: !f.is_course })} />
                      {f.is_course ? '☑ 課程' : '☐ 一般資料夾'}</label>
                  </td>
                  <td><input type="number" className="num" defaultValue={f.sort_order} aria-label={`${f.name} 排序`}
                    onBlur={(e: any) => { const v = Number(e.target.value); if (v !== f.sort_order) void patch(f, { sort_order: v }); }} /></td>
                  <td>
                    <label className="check"><input type="checkbox" checked={f.enabled} onChange={() => void patch(f, { enabled: !f.enabled })} />
                      {f.enabled ? '✓ 啟用' : '✗ 停用'}</label>
                  </td>
                  <td>{f.media_count}</td>
                  <td>{f.status === 'missing' ? '⚠ 資料夾已不存在' : '✓ 存在'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
      <section className="panel">
        <h2>掃描紀錄</h2>
        <LoadState loading={runs.loading && !runs.data} error={runs.error} reload={runs.reload} />
        {runs.data && !runs.data.runs.length ? <Msg kind="info">尚未掃描過</Msg> : null}
        {runs.data && runs.data.runs.length ? (
          <table className="grid">
            <thead><tr><th>開始</th><th>結束</th><th>種類</th><th>狀態</th><th>新增</th><th>變更</th><th>移動</th><th>遺失</th><th>略過</th><th></th></tr></thead>
            <tbody>
              {runs.data.runs.map((r) => (
                <tr key={r.id}>
                  <td>{dateTime(r.started_at)}</td><td>{r.finished_at ? dateTime(r.finished_at) : '—'}</td>
                  <td>{r.dry_run ? '試掃描' : '正式掃描'}</td><td>{RUN_STATUS[r.status] || r.status}{r.error ? <div className="err-text">{r.error}</div> : null}</td>
                  <td>{r.dry_run ? '—' : r.added}</td><td>{r.dry_run ? '—' : r.changed}</td><td>{r.dry_run ? '—' : r.moved}</td>
                  <td>{r.dry_run ? '—' : r.missing ? `⚠ ${r.missing}` : 0}</td><td>{r.skipped_count}</td>
                  <td><Btn small onClick={() => setDetail(r.id)}>報告</Btn></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
      {detail ? <ScanReport id={detail} onClose={() => setDetail(null)} /> : null}
    </div>
  );
}

function ScanReport(props: { id: number; onClose: () => void }): any {
  const r = useLoad(() => api.get<{ run: any }>(`/api/admin/scan-runs/${props.id}`), [props.id]);
  const run = r.data ? r.data.run : null;
  const det = run ? run.details || {} : {};
  const list = (title: string, items: any[] | undefined, fmt: (x: any) => string) => (items && items.length ? (
    <details open={items.length <= 20}>
      <summary>{title}（{items.length}）</summary>
      <ul className="plain small">{items.slice(0, 500).map((x, i) => <li key={i}>{fmt(x)}</li>)}</ul>
    </details>
  ) : null);
  return (
    <Modal title="掃描報告" onClose={props.onClose} wide>
      <LoadState loading={r.loading} error={r.error} reload={r.reload} />
      {run ? (
        <div className="stack">
          <p>{run.dry_run ? '試掃描（不寫入教材資料）' : '正式掃描'}・{dateTime(run.started_at)}・{RUN_STATUS[run.status] || run.status}</p>
          <ScanSummary s={run.summary} />
          {list('新增', det.added, (x) => x.rel_path || String(x))}
          {list('內容變更', det.changed, (x) => x.rel_path || String(x))}
          {list('移動或改名（保留教材 ID）', det.moved, (x) => `${x.from} → ${x.to}（教材 ${x.id}）`)}
          {list('遺失', det.missing, (x) => x.rel_path || String(x))}
          {list('略過', run.skipped, (x) => `${x.path}：${x.reason}${x.warn_only ? '（只提醒）' : ''}`)}
        </div>
      ) : null}
    </Modal>
  );
}
