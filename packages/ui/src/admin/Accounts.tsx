/**
 * 帳號管理（第 09 章）。dev.55：權限只有 user／admin／super；稱謂（助教、講師）另外一欄，兩者分開設定、互不影響。
 * admin 新增與編輯 user；super 另可編輯 admin。唯一的 Super 在清單中唯讀顯示，不能在這裡修改。
 * 重設密碼、停用會立即使 Session 失效；刪除需輸入帳號名稱確認。
 */
import React, { useEffect, useState } from 'react';
import { api, errText } from '../api';
import { atLeast, Btn, Field, LoadState, Modal, Msg, PageHead, TypedConfirm, useApp, useLoad } from '../core';
import { dateTime } from '../format';

const EMPTY: Record<string, any> = { id: null, username: '', password: '', full_name: '', role: 'user', title_id: 'assistant', active: true, phone: '', birth_date: '',
  start_year: '', address: '', class_time: '', bio: '', group_ids: [] as number[] };

export function AccountsPage(): any {
  const { account, toast } = useApp();
  const isAdmin = atLeast(account, 'admin');
  const [q, setQ] = useState('');
  const list = useLoad(() => api.get<{ accounts: any[]; assignable_roles: string[]; titles: any[] }>(
    '/api/accounts' + (q ? '?q=' + encodeURIComponent(q) : '')), [q]);
  const groups = useLoad(() => (isAdmin ? api.get<{ groups: any[] }>('/api/groups') : Promise.resolve({ groups: [] })), []);
  const [form, setForm] = useState<Record<string, any>>(EMPTY);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [notes, setNotes] = useState<any[]>([]);
  const [canEditNotes, setCanEditNotes] = useState(false);
  const [noteText, setNoteText] = useState('');
  const [resetOpen, setResetOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const selectedSeq = React.useRef(0);

  const selected = form.id ? (list.data?.accounts || []).find((a) => a.id === form.id) : null;
  const editable = !form.id || (selected && selected.editable);
  const isSuperRow = !!(selected && selected.role === 'super');

  const pick = async (a: any) => {
    const my = ++selectedSeq.current; // 切換帳號時只顯示最新選取者的資料（沿用 dev.44 保護）
    setMsg(null);
    setForm({ ...EMPTY, ...a, password: '', birth_date: a.birth_date || '', start_year: a.start_year ?? '', group_ids: a.groups.map((g: any) => g.id) });
    if (a.role === 'super') { setNotes([]); setCanEditNotes(false); return; }
    try {
      const r = await api.get(`/api/accounts/${a.id}/notes`);
      if (my !== selectedSeq.current) return;
      setNotes(r.notes);
      setCanEditNotes(r.can_edit);
    } catch {
      if (my === selectedSeq.current) setNotes([]);
    }
  };
  const clear = () => {
    selectedSeq.current++;
    setForm(EMPTY);
    setNotes([]);
    setMsg(null);
  };

  const body = () => {
    const b: Record<string, any> = {
      full_name: form.full_name, phone: form.phone, birth_date: form.birth_date || null, address: form.address, class_time: form.class_time,
      bio: form.bio, start_year: form.start_year === '' ? null : Number(form.start_year),
    };
    // 稱謂與權限分開送出；權限只送可指派的值（Super 永遠不在選項內）
    if (form.title_id && titles.some((t) => t.title_id === form.title_id)) b.title_id = form.title_id;
    if (roles.includes(form.role)) b.role = form.role;
    if (isAdmin) b.group_ids = form.group_ids;
    return b;
  };
  const save = async () => {
    setMsg(null);
    try {
      if (form.id) {
        const r = await api.patch(`/api/accounts/${form.id}`, { ...body(), active: form.active });
        setMsg({ kind: 'ok', text: '已儲存' });
        await pick(r.account);
      } else {
        const r = await api.post('/api/accounts', { ...body(), username: form.username, password: form.password });
        setMsg({ kind: 'ok', text: `已建立帳號 ${r.account.username}` });
        await pick({ ...r.account, editable: true });
      }
      list.reload();
    } catch (e) {
      setMsg({ kind: 'error', text: errText(e) });
    }
  };
  const addNote = async () => {
    if (!noteText.trim()) return;
    try {
      const r = await api.post(`/api/accounts/${form.id}/notes`, { body: noteText.trim() });
      setNotes([...notes, r.note]);
      setNoteText('');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const editNote = async (n: any) => {
    const v = window.prompt('修改備註', n.body);
    if (v === null || !v.trim()) return;
    try {
      const r = await api.patch(`/api/accounts/${form.id}/notes/${n.id}`, { body: v.trim() });
      setNotes(notes.map((x) => (x.id === n.id ? r.note : x)));
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  useEffect(() => { if (list.data && form.id && !selected) clear(); }, [list.data]);

  const titles: any[] = (list.data && list.data.titles) || [];
  const roles: string[] = (list.data && list.data.assignable_roles) || [];
  const toggleGroup = (id: number) => setForm({ ...form, group_ids: form.group_ids.includes(id) ? form.group_ids.filter((x: number) => x !== id) : [...form.group_ids, id] });

  return (
    <div className="page">
      <PageHead title="帳號管理" />
      <section className="panel">
        <h2>{form.id ? `編輯：${form.full_name || form.username}` : '新增帳號'}</h2>
        {isSuperRow ? <Msg kind="info">super 是唯一的最高管理者，不能在帳號管理修改；super 自己的資料請到「個人資料」</Msg>
          : !editable ? <Msg kind="info">您只能為此帳號追加備註，不能修改資料</Msg> : null}
        <div className="form-grid">
          <Field label="姓名"><input value={form.full_name} disabled={!editable} onChange={(e: any) => setForm({ ...form, full_name: e.target.value })} /></Field>
          <Field label="帳號" hint={form.id ? undefined : '2～80 字：中文、半形英文字母、數字（可含 _ . -）'}>
            <input value={form.username} disabled={!!form.id} onChange={(e: any) => setForm({ ...form, username: e.target.value })} />
          </Field>
          {!form.id ? <Field label="初始密碼" hint="至少 8 碼"><input type="password" value={form.password} onChange={(e: any) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></Field> : null}
          <Field label="稱謂" hint="只是名稱，不影響權限">
            <select value={form.title_id || ''} disabled={!editable || isSuperRow} onChange={(e: any) => setForm({ ...form, title_id: e.target.value })}>
              {!form.title_id ? <option value="">—</option> : null}
              {titles.map((t) => <option key={t.title_id} value={t.title_id}>{t.zh_title}</option>)}
            </select>
          </Field>
          <Field label="權限" hint="user：看所屬群組的教材；admin：另可管理帳號、群組、WOL">
            <select value={form.role} disabled={!editable || roles.length < 2} onChange={(e: any) => setForm({ ...form, role: e.target.value })}>
              {!roles.includes(form.role) ? <option value={form.role}>{form.role}</option> : null}
              {roles.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </Field>
          {form.id ? (
            <Field label="狀態">
              <select value={form.active ? '1' : '0'} disabled={!editable || !isAdmin} onChange={(e: any) => setForm({ ...form, active: e.target.value === '1' })}>
                <option value="1">✓ 啟用</option><option value="0">✗ 停用</option>
              </select>
            </Field>
          ) : null}
          <Field label="電話"><input value={form.phone} disabled={!editable} onChange={(e: any) => setForm({ ...form, phone: e.target.value })} /></Field>
          <Field label="生日"><input type="date" value={form.birth_date} disabled={!editable} onChange={(e: any) => setForm({ ...form, birth_date: e.target.value })} /></Field>
          <Field label="開始年份"><input type="number" value={form.start_year} disabled={!editable} onChange={(e: any) => setForm({ ...form, start_year: e.target.value })} /></Field>
          <Field label="上課時間"><input value={form.class_time} disabled={!editable} onChange={(e: any) => setForm({ ...form, class_time: e.target.value })} /></Field>
          <Field label="地址" wide><input value={form.address} disabled={!editable} onChange={(e: any) => setForm({ ...form, address: e.target.value })} /></Field>
        </div>
        {isAdmin ? (
          <div className="checks">
            <span>課程群組：</span>
            {(groups.data ? groups.data.groups : []).map((g: any) => (
              <label key={g.id} className="check">
                <input type="checkbox" checked={form.group_ids.includes(g.id)} disabled={!editable} onChange={() => toggleGroup(g.id)} />
                {form.group_ids.includes(g.id) ? '☑' : '☐'} {g.name}
              </label>
            ))}
          </div>
        ) : null}
        {msg ? <Msg kind={msg.kind}>{msg.text}</Msg> : null}
        <div className="row">
          {editable ? <Btn kind="primary" onClick={save}>{form.id ? '儲存' : '建立帳號'}</Btn> : null}
          {form.id && editable && isAdmin ? <Btn onClick={() => setResetOpen(true)}>重設密碼</Btn> : null}
          {form.id && editable && isAdmin ? <Btn kind="danger" onClick={() => setDelOpen(true)}>刪除</Btn> : null}
          <Btn onClick={clear}>清空／新增</Btn>
        </div>
        {form.id && !isSuperRow ? (
          <div className="notes">
            <h3>備註（追加）</h3>
            {!notes.length ? <p className="dim">尚無備註</p> : null}
            <ul>
              {notes.map((n) => (
                <li key={n.id}>
                  <span className="dim">{dateTime(n.created_at)}・{n.created_by_name}{n.updated_at ? '（已編輯）' : ''}：</span> {n.body}
                  {canEditNotes ? <Btn small kind="quiet" onClick={() => editNote(n)}>編輯</Btn> : null}
                </li>
              ))}
            </ul>
            <div className="row">
              <input className="grow" value={noteText} onChange={(e: any) => setNoteText(e.target.value)} placeholder="追加一則備註" aria-label="追加備註" />
              <Btn onClick={addNote} disabled={!noteText.trim()}>追加</Btn>
            </div>
          </div>
        ) : null}
      </section>
      <section className="panel">
        <div className="row between">
          <h2>帳號清單</h2>
          <label className="search-inline">搜尋：<input value={q} onChange={(e: any) => setQ(e.target.value)} placeholder="姓名或帳號" /></label>
        </div>
        <LoadState loading={list.loading} error={list.error} reload={list.reload} />
        {list.data ? (
          <table className="grid">
            <thead><tr><th>姓名</th><th>帳號</th><th>稱謂</th><th>權限</th><th>群組</th><th>狀態</th></tr></thead>
            <tbody>
              {list.data.accounts.map((a) => (
                <tr key={a.id} className={'clickable' + (a.id === form.id ? ' sel' : '')} onClick={() => void pick(a)}>
                  <td>{a.id === form.id ? '▶ ' : ''}{a.full_name || '—'}</td><td>{a.username}</td><td>{a.title || '—'}</td><td>{a.role}</td>
                  <td>{a.groups.map((g: any) => g.name).join('、') || '—'}</td><td>{a.active ? '✓ 啟用' : '✗ 停用'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
      {resetOpen ? <ResetPassword id={form.id} name={form.full_name || form.username} onClose={() => setResetOpen(false)} /> : null}
      {delOpen ? (
        <TypedConfirm title="刪除帳號" expect={form.username} confirmText="永久刪除帳號"
          message={<>刪除後此帳號的學習歷程與書籤會一併刪除；使用記錄保留帳號 ID 與姓名。</>}
          onConfirm={async () => {
            await api.del(`/api/accounts/${form.id}`, { confirm_username: form.username });
            toast(`已刪除 ${form.username}`, 'ok');
            clear();
            list.reload();
          }} onClose={() => setDelOpen(false)} />
      ) : null}
    </div>
  );
}

function ResetPassword(props: { id: number; name: string; onClose: () => void }): any {
  const { toast } = useApp();
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title={`重設密碼：${props.name}`} onClose={props.onClose} footer={<>
      <Btn kind="primary" onClick={async () => {
        if (a.length < 8) return setErr('新密碼至少 8 碼');
        if (a !== b) return setErr('兩次輸入的密碼不同');
        try {
          await api.post(`/api/accounts/${props.id}/password`, { password: a });
          toast('已重設密碼，此帳號需重新登入', 'ok');
          props.onClose();
        } catch (e) {
          setErr(errText(e));
        }
      }}>重設密碼</Btn>
      <Btn onClick={props.onClose}>取消</Btn>
    </>}>
      <div className="stack">
        <Field label="新密碼（至少 8 碼）"><input type="password" value={a} onChange={(e: any) => setA(e.target.value)} autoFocus /></Field>
        <Field label="再輸入一次"><input type="password" value={b} onChange={(e: any) => setB(e.target.value)} /></Field>
        {err ? <Msg kind="error">{err}</Msg> : null}
      </div>
    </Modal>
  );
}
