/**
 * 教材清單（第 10 章）：搜尋、依課程篩選；改標題、指定字幕；遺失檔案重新指定路徑（保留教材 ID）；
 * 合集加入、移出、改名、取消（手動結果重新掃描不覆蓋）；「解除關聯」只刪資料庫紀錄，不刪實體檔案。
 * dev.61：「永久刪除」＝刪除原檔、衍生檔（播放用 MP4、字幕、文件 PDF）與資料庫紀錄；先顯示影響範圍，輸入「永久刪除」才執行。
 */
import React, { useState } from 'react';
import { api, errText, qs } from '../../../../packages/ui/src/api';
import { Btn, Field, LoadState, Modal, Msg, PageHead, useApp, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, duration, KIND_ICON, KIND_TEXT, size } from '../../../../packages/ui/src/format';
import { convertText, openMedia } from '../../../../packages/ui/src/pages/Courses';

const STATUS_TEXT: Record<string, string> = { present: '✓ 存在', missing: '⚠ 檔案遺失', original_deleted: '✓ 原檔已刪（播放 MP4）' };

export function MediaListPage(): any {
  const { go, toast } = useApp();
  const [f, setF] = useState({ q: '', course: '', status: '', kind: '' });
  const [applied, setApplied] = useState(f);
  const [page, setPage] = useState(1);
  const courses = useLoad(() => api.get<{ folders: any[] }>('/api/admin/folders'), []);
  const r = useLoad(() => api.get<any>('/api/admin/media' + qs({ ...applied, page, per: 100 })), [JSON.stringify(applied), page]);
  const [sel, setSel] = useState<number[]>([]);
  const [edit, setEdit] = useState<any | null>(null);
  const [clip, setClip] = useState<{ groupId: number; folderId: number } | null>(null);
  const [purge, setPurge] = useState<any | null>(null);
  const items = r.data ? r.data.items : [];
  const pages = r.data ? Math.max(1, Math.ceil(r.data.total / r.data.per)) : 1;
  const toggle = (id: number) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);
  const makeGroup = async () => {
    const name = window.prompt('合集名稱', '手動合集');
    if (name === null) return;
    try {
      await api.post('/api/admin/clip-groups', { media_ids: sel, name: name.trim() || '手動合集' });
      toast('已建立合集', 'ok');
      setSel([]);
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="page">
      <PageHead title="教材清單">
        {sel.length >= 2 ? <Btn onClick={makeGroup}>以勾選的 {sel.length} 支影片建立合集</Btn> : null}
      </PageHead>
      <section className="panel">
        <div className="form-row">
          <Field label="搜尋"><input value={f.q} onChange={(e: any) => setF({ ...f, q: e.target.value })} placeholder="標題、路徑或教材 ID"
            onKeyDown={(e: any) => { if (e.key === 'Enter') { setPage(1); setApplied({ ...f }); } }} /></Field>
          <Field label="課程">
            <select value={f.course} onChange={(e: any) => setF({ ...f, course: e.target.value })}>
              <option value="">全部</option>
              {(courses.data ? courses.data.folders : []).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </Field>
          <Field label="狀態">
            <select value={f.status} onChange={(e: any) => setF({ ...f, status: e.target.value })}>
              <option value="">全部</option><option value="present">存在</option><option value="missing">檔案遺失</option><option value="original_deleted">原檔已刪</option>
            </select>
          </Field>
          <Field label="類型">
            <select value={f.kind} onChange={(e: any) => setF({ ...f, kind: e.target.value })}>
              <option value="">全部</option>
              {['video', 'pdf', 'office', 'text', 'image'].map((k) => <option key={k} value={k}>{KIND_TEXT[k]}{k === 'office' ? '（Office）' : ''}</option>)}
            </select>
          </Field>
          <Btn kind="primary" onClick={() => { setPage(1); setApplied({ ...f }); }}>篩選</Btn>
        </div>
      </section>
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data ? <p className="dim">共 {r.data.total} 筆{pages > 1 ? `，第 ${page}／${pages} 頁` : ''}；勾選同一資料夾的 2 支以上影片可建立合集</p> : null}
      {r.data && !items.length ? <Msg kind="info">沒有符合的教材</Msg> : null}
      {items.length ? (
        <table className="grid small">
          <thead><tr><th></th><th>ID</th><th>教材</th><th>路徑</th><th>狀態</th><th>處理</th><th>合集</th><th>操作</th></tr></thead>
          <tbody>
            {items.map((m: any) => (
              <tr key={m.id}>
                <td>{m.kind === 'video' ? <input type="checkbox" checked={sel.includes(m.id)} onChange={() => toggle(m.id)} aria-label={`選取 ${m.title}`} /> : null}</td>
                <td>{m.id}</td>
                <td>{KIND_ICON[m.kind] || '📄'} {m.title}<div className="dim">{m.file_name}・{size(m.size)}{m.duration ? '・' + duration(m.duration) : ''}{m.has_subtitle ? '・💬 有字幕' : ''}</div></td>
                <td className="path">{m.rel_path}</td>
                <td>{STATUS_TEXT[m.status] || m.status}</td>
                <td>{convertText(m) || '✓ 可使用'}{m.convert_error || m.doc_error ? <div className="err-text">{m.convert_error || m.doc_error}</div> : null}</td>
                <td>{m.clip ? <Btn small kind="quiet" onClick={() => setClip({ groupId: m.clip.group_id, folderId: m.folder_id })}>
                  🎞 {m.clip.seq}/{m.clip.count}{m.clip.source === 'manual' ? '（手動）' : ''}</Btn> : '—'}</td>
                <td className="actions">
                  {m.state === 'ready' ? <Btn small onClick={() => openMedia(go, m)}>{m.kind === 'video' ? '播放抽查' : '開啟'}</Btn> : null}
                  <Btn small onClick={() => setEdit(m)}>編輯</Btn>
                  <Btn small kind="danger" onClick={() => setPurge(m)}>永久刪除…</Btn>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {pages > 1 ? (
        <div className="row">
          <Btn small disabled={page <= 1} onClick={() => setPage(page - 1)}>上一頁</Btn>
          <span>第 {page}／{pages} 頁</span>
          <Btn small disabled={page >= pages} onClick={() => setPage(page + 1)}>下一頁</Btn>
        </div>
      ) : null}
      {edit ? <EditMedia m={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); r.reload(); }} /> : null}
      {purge ? <PurgeDialog m={purge} onClose={() => setPurge(null)} onDone={() => { setPurge(null); r.reload(); }} /> : null}
      {clip ? <ClipGroupEditor groupId={clip.groupId} folderId={clip.folderId} selected={sel} onClose={() => setClip(null)} onDone={() => r.reload()} /> : null}
    </div>
  );
}

/** dev.61 永久刪除教材：先列出會刪除的檔案與資料，輸入「永久刪除」後由 Agent 執行；等工作結束才顯示結果（不會刪一半就說成功）。 */
export function PurgeDialog(props: { m: any; onClose: () => void; onDone: () => void }): any {
  const { toast } = useApp();
  const pv = useLoad(() => api.get<any>(`/api/admin/media/${props.m.id}/purge-preview`), [props.m.id]);
  const [typed, setTyped] = useState('');
  const [phase, setPhase] = useState<'confirm' | 'running' | 'failed' | 'done'>('confirm');
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);        // dev.62：工作結果（每個檔案實際的狀態）
  const d = pv.data;
  const blocked = !d || d.blockers.length > 0;
  const go = async () => {
    setErr(null);
    setPhase('running');
    try {
      const x = await api.post(`/api/admin/media/${props.m.id}/purge`, { confirm: typed, rel_path: d.media.rel_path });
      for (let i = 0; i < 180; i++) {      // 最多等 3 分鐘；Agent 正在做別的長工作時會排隊
        await new Promise((res) => setTimeout(res, 1000));
        const j = (await api.get<any>(`/api/admin/jobs/${x.job_id}`)).job;
        if (j.status === 'done') {
          const res = j.result || {};
          setResult(res);
          toast(`已永久刪除「${d.media.title}」：${(res.deleted || []).join('、') || '資料庫紀錄'}`
            + (res.left && res.left.length ? `；⚠ ${res.left.length} 個暫存檔待清理` : ''), res.left && res.left.length ? 'error' : 'ok', 6000);
          return setPhase('done');
        }
        if (j.status === 'failed' || j.status === 'cancelled') {
          setResult(j.result || null);
          setErr(j.error || '刪除失敗');
          return setPhase('failed');
        }
      }
      toast('刪除工作已排入，Agent 忙碌中；完成後清單會更新（結果寫入使用記錄）', 'info', 6000);
      props.onDone();
    } catch (e) {
      setErr(errText(e));
      setPhase('failed');
    }
  };
  return (
    <Modal title={`永久刪除教材 ${props.m.id}`} wide onClose={phase === 'done' ? props.onDone : props.onClose} footer={phase === 'done'
      ? <Btn kind="primary" onClick={props.onDone}>關閉</Btn> : <>
      <Btn kind="danger" disabled={blocked || typed !== '永久刪除' || phase === 'running'} onClick={go}>
        {phase === 'running' ? '… 刪除中' : phase === 'failed' ? '再試一次（接著處理）' : '永久刪除'}</Btn>
      <Btn onClick={props.onClose} disabled={phase === 'running'}>取消</Btn>
    </>}>
      {phase === 'done' || (phase === 'failed' && result && result.items) ? <PurgeResult res={result} ok={phase === 'done'} /> : null}
      <LoadState loading={pv.loading} error={pv.error} reload={pv.reload} />
      {d && phase !== 'done' ? (
        <div className="stack">
          <Msg kind="warn">永久刪除後無法復原：/Media 上的原檔、播放用 MP4、字幕、文件 PDF 與資料庫紀錄都會刪除，重新掃描也不會再出現。
            只刪除這一個教材，不刪資料夾。</Msg>
          <table className="grid kv small"><tbody>
            <tr><th>教材</th><td>{d.media.title}（ID {d.media.id}）</td></tr>
            <tr><th>檔名</th><td>{d.media.file_name}</td></tr>
            <tr><th>路徑</th><td className="path">/Media/{d.media.rel_path}</td></tr>
          </tbody></table>
          <h3>會刪除的檔案</h3>
          <table className="grid small">
            <thead><tr><th>項目</th><th>路徑</th><th>狀態</th></tr></thead>
            <tbody>{d.files.map((f: any) => (
              <tr key={f.label}><td>{f.label}</td><td className="path">{f.path}</td>
                <td>{!f.exists ? '－ 不存在' : f.owned === false ? '－ 同 ID 的舊檔（不屬於這個教材）：不處理' : f.symlink ? '✗ 符號連結：不刪除'
                  : f.shared ? '－ 其他教材也在使用：保留' : `✓ 刪除（${size(f.size || 0)}）`}</td></tr>
            ))}</tbody>
          </table>
          <h3>會刪除的資料</h3>
          <ul className="plain small">
            <li>教材紀錄、Tag {d.db.tags} 個</li>
            <li>字幕：{d.db.subtitle ? `有（${d.db.subtitle_cues} 段）` : '沒有'}</li>
            <li>書籤 {d.db.bookmarks} 筆、學習歷程 {d.db.view_history} 筆</li>
            <li>這個教材的工作紀錄 {d.db.jobs_history} 筆；排隊中的工作 {d.db.jobs_queued} 筆會取消</li>
            <li>使用記錄 {d.db.audit_refs} 筆：保留內容，只移除教材 ID 關聯；另記一筆「永久刪除」</li>
            {d.db.asr_log ? <li>ASR 處理記錄 {d.db.asr_log} 筆：保留（路徑、品質、結果），只移除教材 ID 關聯</li> : null}
            {d.db.clip_group ? <li>合集「{d.db.clip_group.name}」：移出（剩下沒有成員時刪除合集）</li> : null}
          </ul>
          {d.blockers.length ? <Msg kind="error">不能刪除：{d.blockers.join('；')}</Msg> : (
            <label className="field"><span>輸入「永久刪除」確認</span>
              <input value={typed} onChange={(e: any) => setTyped(e.target.value)} disabled={phase === 'running'} aria-label="輸入永久刪除確認" /></label>
          )}
          {err ? <Msg kind="error">✗ {err}</Msg> : null}
        </div>
      ) : null}
    </Modal>
  );
}

const PURGE_STATE: Record<string, string> = {
  gone: '✓ 已刪除', original: '－ 在原位置（沒有刪除）', staged: '⚠ 在暫存名稱（隱藏檔）', both: '⚠ 原位置與暫存名稱都有檔案',
};

/** dev.62：逐項顯示每個檔案實際的狀態（Agent 確認過的，不是推測）。 */
function PurgeResult(props: { res: any; ok: boolean }): any {
  const r = props.res || {};
  const items: any[] = r.items || [];
  return (
    <div className="stack">
      {props.ok ? <Msg kind={r.left && r.left.length ? 'warn' : 'ok'}>{r.left && r.left.length
        ? `教材已永久刪除（資料庫紀錄已刪）；${r.left.length} 個暫存檔暫時刪不掉，已列為待清理，Agent 重新啟動時會再刪`
        : '✓ 教材已永久刪除：檔案與資料庫紀錄都已刪除'}</Msg>
        : <Msg kind="info">教材資料庫紀錄{r.committed ? '已刪除' : '沒有刪除'}。各檔案目前的實際位置如下：</Msg>}
      {items.length ? (
        <table className="grid small">
          <thead><tr><th>項目</th><th>路徑</th><th>結果</th></tr></thead>
          <tbody>{items.map((x: any) => (
            <tr key={x.path}><td>{x.label}</td><td className="path">{x.path}{x.state === 'staged' ? <div className="dim">目前：{x.staged}</div> : null}</td>
              <td>{PURGE_STATE[x.state] || x.state}</td></tr>
          ))}</tbody>
        </table>
      ) : null}
      {(r.notes || []).length ? <ul className="plain small">{r.notes.map((n: string, i: number) => <li key={i}>· {n}</li>)}</ul> : null}
    </div>
  );
}

function EditMedia(props: { m: any; onClose: () => void; onDone: () => void }): any {
  const { toast } = useApp();
  const { m } = props;
  const [title, setTitle] = useState(m.title);
  const [rel, setRel] = useState(m.rel_path);
  const [sub, setSub] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const save = async () => {
    setErr(null);
    const body: Record<string, any> = {};
    if (title.trim() !== m.title) body.title = title.trim();
    if (m.status === 'missing' && rel.trim() !== m.rel_path) body.rel_path = rel.trim();
    if (sub.trim()) body.subtitle_rel_path = sub.trim();
    if (!Object.keys(body).length) return props.onClose();
    try {
      const r = await api.patch(`/api/media/${m.id}`, body);
      toast(r.job_ids && r.job_ids.length ? '已儲存，背景工作已排入' : '已儲存', 'ok');
      props.onDone();
    } catch (e) {
      setErr(errText(e));
    }
  };
  const unlink = async () => {
    try {
      await api.del(`/api/media/${m.id}`, { confirm: true });
      toast('已解除關聯（實體檔案未刪除）', 'ok');
      props.onDone();
    } catch (e) {
      setErr(errText(e));
    }
  };
  return (
    <Modal title={`編輯教材 ${m.id}`} onClose={props.onClose} wide footer={<>
      <Btn kind="primary" onClick={save}>儲存</Btn>
      <Btn onClick={props.onClose}>取消</Btn>
      <span className="grow" />
      {!confirmUnlink ? <Btn kind="danger" onClick={() => setConfirmUnlink(true)}>解除關聯…</Btn>
        : <Btn kind="danger" onClick={unlink}>確定解除關聯（不刪檔案）</Btn>}
    </>}>
      <div className="stack">
        <Field label="標題"><input value={title} onChange={(e: any) => setTitle(e.target.value)} /></Field>
        <Field label="檔案路徑（/Media 底下）" hint={m.status === 'missing' ? '檔案遺失時可重新指定路徑，教材 ID 不變' : '檔案存在時不可修改；移動檔案後重新掃描會自動對應'}>
          <input value={rel} disabled={m.status !== 'missing'} onChange={(e: any) => setRel(e.target.value)} />
        </Field>
        {m.kind === 'video' ? (
          <Field label="指定字幕（/Media 底下的 .srt 或 .vtt）" hint={m.has_subtitle ? '已有字幕；指定後會取代' : '尚無字幕'}>
            <input value={sub} onChange={(e: any) => setSub(e.target.value)} placeholder="例如 課程A/第01講.srt" />
          </Field>
        ) : null}
        <table className="grid kv small">
          <tbody>
            <tr><th>處理方式</th><td>{m.play_mode === 'direct' ? '直接播放' : m.play_mode === 'remux' ? '換封裝' : m.play_mode === 'transcode' ? '重新編碼' : '—'}{m.encoder ? `（${m.encoder}）` : ''}</td></tr>
            <tr><th>MP4 檢查通過</th><td>{m.verified_at ? dateTime(m.verified_at) : '—'}</td></tr>
            <tr><th>原檔可刪除日</th><td>{m.original_deleted_at ? `已刪除（${dateTime(m.original_deleted_at)}）` : m.delete_due_at ? dateTime(m.delete_due_at) : '—'}</td></tr>
          </tbody>
        </table>
        {confirmUnlink ? <Msg kind="warn">「解除關聯」只刪除資料庫中的教材紀錄（含 Tag、書籤、學習歷程的關聯），不刪除 /Media 上的實體檔案。重新掃描時會以新的教材 ID 再加入。</Msg> : null}
        {err ? <Msg kind="error">{err}</Msg> : null}
      </div>
    </Modal>
  );
}

function ClipGroupEditor(props: { groupId: number; folderId: number; selected: number[]; onClose: () => void; onDone: () => void }): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<{ groups: any[] }>(`/api/admin/clip-groups?folder=${props.folderId}`), [props.groupId]);
  const g = r.data ? r.data.groups.find((x) => x.id === props.groupId) : null;
  const [name, setName] = useState<string | null>(null);
  const patch = async (body: Record<string, any>) => {
    try {
      const x = await api.patch(`/api/admin/clip-groups/${props.groupId}`, body);
      props.onDone();
      if (x.cancelled) {
        toast('合集只剩 1 支，已自動取消', 'info');
        props.onClose();
      } else r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const cancel = async () => {
    if (!window.confirm('取消這個合集？成員影片會改為獨立影片，重新掃描也不會再自動合併。')) return;
    try {
      await api.del(`/api/admin/clip-groups/${props.groupId}`);
      props.onDone();
      props.onClose();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const addable = g ? props.selected.filter((id) => !g.members.some((m: any) => m.id === id)) : [];
  return (
    <Modal title="合集" onClose={props.onClose} footer={<><Btn kind="danger" onClick={cancel}>取消合集</Btn><Btn onClick={props.onClose}>關閉</Btn></>}>
      <LoadState loading={r.loading} error={r.error} reload={r.reload} />
      {r.data && !g ? <Msg kind="info">這個合集已不存在</Msg> : null}
      {g ? (
        <div className="stack">
          <div className="row">
            <input className="grow" value={name ?? g.name} onChange={(e: any) => setName(e.target.value)} aria-label="合集名稱" />
            <Btn onClick={() => patch({ name: (name ?? g.name).trim() })} disabled={!name || name.trim() === g.name}>改名</Btn>
          </div>
          <p className="dim">{g.source === 'manual' ? '手動調整過：重新掃描不覆蓋' : '自動判定（連續編號、前一支接近 2GB、相同規格、錄影時間連續）'}{g.recorded_at ? `；錄影 ${dateTime(g.recorded_at)}` : ''}</p>
          <ol className="clip-members">
            {g.members.map((m: any) => (
              <li key={m.id}>{m.seq}. {m.title}<span className="dim">（{m.file_name}{m.duration ? '・' + duration(m.duration) : ''}）</span>
                <Btn small kind="quiet" onClick={() => patch({ remove: [m.id] })}>移出</Btn></li>
            ))}
          </ol>
          {addable.length ? <Btn onClick={() => patch({ add: addable })}>加入勾選的 {addable.length} 支影片</Btn>
            : <p className="dim">要加入影片：先在清單勾選同一資料夾的影片，再開啟這個視窗</p>}
        </div>
      ) : null}
    </Modal>
  );
}
