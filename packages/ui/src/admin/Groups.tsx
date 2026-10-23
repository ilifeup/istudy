/**
 * 課程群組與個人覆寫（第 09 章）：群組可看的課程、成員；個人覆寫表同時列出「群組結果」「個人覆寫」「最後結果」。
 */
import React, { useEffect, useRef, useState } from 'react';
import { api, errText } from '../api';
import { Btn, LoadState, Msg, PageHead, useApp, useLoad } from '../core';

export function GroupsPage(): any {
  const { toast } = useApp();
  const g = useLoad(() => api.get<{ groups: any[]; courses: any[] }>('/api/groups'), []);
  const accounts = useLoad(() => api.get<{ accounts: any[] }>('/api/accounts'), []);
  const [gid, setGid] = useState<number | null>(null);
  const [courseIds, setCourseIds] = useState<number[]>([]);
  const [newName, setNewName] = useState('');
  const [addId, setAddId] = useState('');
  const group = g.data && gid ? g.data.groups.find((x) => x.id === gid) : null;
  useEffect(() => {
    if (group) setCourseIds(group.course_ids);
  }, [gid, g.data]);
  useEffect(() => {
    if (g.data && g.data.groups.length && !gid) setGid(g.data.groups[0].id);
  }, [g.data]);

  const create = async () => {
    if (!newName.trim()) return;
    try {
      const r = await api.post('/api/groups', { name: newName.trim() });
      setNewName('');
      setGid(r.group.id);
      g.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const rename = async () => {
    const v = window.prompt('新的群組名稱', group.name);
    if (!v || !v.trim()) return;
    try {
      await api.patch(`/api/groups/${gid}`, { name: v.trim() });
      g.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const remove = async () => {
    try {
      await api.del(`/api/groups/${gid}`, {});
    } catch (e: any) {
      if (e.code === 'confirm_required') {
        if (!window.confirm(e.message + '。確定刪除？')) return;
        try {
          await api.del(`/api/groups/${gid}`, { confirm: true });
        } catch (e2) {
          return toast(errText(e2), 'error');
        }
      } else {
        return toast(errText(e), 'error');
      }
    }
    setGid(null);
    g.reload();
  };
  const saveCourses = async () => {
    try {
      await api.put(`/api/groups/${gid}/courses`, { course_ids: courseIds });
      toast('已儲存群組權限', 'ok');
      g.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const addMember = async () => {
    if (!addId) return;
    try {
      await api.post(`/api/groups/${gid}/members`, { account_ids: [Number(addId)] });
      setAddId('');
      g.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const removeMember = async (aid: number) => {
    try {
      await api.del(`/api/groups/${gid}/members/${aid}`, {});
      g.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const courses = g.data ? g.data.courses : [];
  const memberIds = group ? group.members.map((m: any) => m.id) : [];

  return (
    <div className="page">
      <PageHead title="課程群組與個人覆寫" />
      <LoadState loading={g.loading} error={g.error} reload={g.reload} />
      <section className="panel">
        <div className="row">
          <label className="search-inline">新群組名稱：<input value={newName} onChange={(e: any) => setNewName(e.target.value)} /></label>
          <Btn onClick={create} disabled={!newName.trim()}>新增群組</Btn>
          <span className="grow" />
          <label className="inline-select">
            <span>選擇群組：</span>
            <select value={gid ?? ''} onChange={(e: any) => setGid(e.target.value ? Number(e.target.value) : null)}>
              {(g.data ? g.data.groups : []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          </label>
          {group ? <span className="dim">成員 {group.members.length} 人</span> : null}
          {group ? <Btn small onClick={rename}>改名</Btn> : null}
          {group ? <Btn small kind="danger" onClick={remove}>刪除群組</Btn> : null}
        </div>
        {g.data && !g.data.groups.length ? <Msg kind="info">尚未建立課程群組</Msg> : null}
        {!courses.length ? <Msg kind="info">尚未掃描到課程資料夾</Msg> : null}
        {group ? (
          <div className="cols-2">
            <div>
              <h3>群組可看的課程</h3>
              <div className="check-grid">
                {courses.map((c) => (
                  <label key={c.id} className="check">
                    <input type="checkbox" checked={courseIds.includes(c.id)}
                      onChange={() => setCourseIds(courseIds.includes(c.id) ? courseIds.filter((x) => x !== c.id) : [...courseIds, c.id])} />
                    {courseIds.includes(c.id) ? '☑' : '☐'} {c.name}{c.enabled ? '' : '（已停用）'}
                  </label>
                ))}
              </div>
              <Btn kind="primary" onClick={saveCourses}>儲存群組權限</Btn>
            </div>
            <div>
              <h3>成員</h3>
              <ul className="member-list">
                {group.members.map((m: any) => (
                  <li key={m.id}>{m.full_name || m.username}（{m.username}） <Btn small onClick={() => removeMember(m.id)}>移出</Btn></li>
                ))}
              </ul>
              <div className="row">
                <select value={addId} onChange={(e: any) => setAddId(e.target.value)} aria-label="加入成員">
                  <option value="">＋ 加入成員…</option>
                  {(accounts.data ? accounts.data.accounts : []).filter((a) => !memberIds.includes(a.id)).map((a) => (
                    <option key={a.id} value={a.id}>{a.full_name || a.username}（{a.username}）</option>
                  ))}
                </select>
                <Btn onClick={addMember} disabled={!addId}>加入</Btn>
              </div>
            </div>
          </div>
        ) : null}
      </section>
      <Overrides accounts={accounts.data ? accounts.data.accounts : []} />
    </div>
  );
}

function Overrides(props: { accounts: any[] }): any {
  const { toast } = useApp();
  const [aid, setAid] = useState<string>('');
  const [rows, setRows] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    if (!aid) return setRows(null);
    const my = ++seq.current;
    setErr(null);
    api.get(`/api/accounts/${aid}/overrides`).then((r) => { if (my === seq.current) setRows(r.rows); }, (e) => { if (my === seq.current) setErr(errText(e)); });
  }, [aid]);
  const setMode = async (courseId: number, mode: string) => {
    setSaving(true);
    try {
      const r = await api.put(`/api/accounts/${aid}/overrides`, { overrides: [{ folder_id: courseId, mode: mode || null }] });
      setRows(r.rows);
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setSaving(false);
    }
  };
  const clear = async () => {
    if (!window.confirm('清除此人所有個人覆寫，改為只依群組權限？')) return;
    try {
      const r = await api.put(`/api/accounts/${aid}/overrides`, { clear: true });
      setRows(r.rows);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const finalText = (r: any) => r.reason === 'role' ? '✓ 可看（管理員）' : r.final
    ? (r.reason === 'personal_allow' ? '✓ 可看（個人加開）' : '✓ 可看') : (r.reason === 'personal_deny' ? '✗ 不可（個人關閉）' : '✗ 不可');
  return (
    <section className="panel">
      <div className="row">
        <h2>個人覆寫</h2>
        <label className="inline-select">
          <span>選擇帳號：</span>
          <select value={aid} onChange={(e: any) => setAid(e.target.value)}>
            <option value="">請選擇</option>
            {props.accounts.map((a) => <option key={a.id} value={a.id}>{a.full_name || a.username}（{a.username}）</option>)}
          </select>
        </label>
        {aid ? <Btn onClick={clear}>套用群組權限（清除個人覆寫）</Btn> : null}
      </div>
      {err ? <Msg kind="error">{err}</Msg> : null}
      {rows ? (
        <table className="grid">
          <thead><tr><th>課程</th><th>群組結果</th><th>個人覆寫</th><th>最後結果</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.course_id}>
                <td>{r.course_name}</td>
                <td>{r.group_result ? '✓ 可看' : '✗ 不可'}</td>
                <td>
                  <select value={r.override || ''} disabled={saving || r.reason === 'role'} onChange={(e: any) => void setMode(r.course_id, e.target.value)}>
                    <option value="">（無）</option><option value="allow">允許</option><option value="deny">禁止</option>
                  </select>
                </td>
                <td><strong>{finalText(r)}</strong></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
