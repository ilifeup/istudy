/** 個人資料與修改密碼（第 08 章）。登入帳號、角色、群組不可自行修改；改密碼後所有 Session 失效。 */
import React, { useState } from 'react';
import { api, errText } from '../api';
import { bridge } from '../bridge';
import { Btn, Field, Msg, PageHead, useApp } from '../core';

const FIELDS: [string, string, string?][] = [
  ['full_name', '姓名'], ['phone', '電話'], ['birth_date', '生日', 'date'], ['start_year', '開始年份', 'number'],
  ['address', '地址'], ['class_time', '上課時間'],
];

export function ProfilePage(): any {
  const { account, setAccount } = useApp();
  const [form, setForm] = useState<Record<string, any>>({ ...account });
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [pw, setPw] = useState({ current: '', next: '', again: '' });
  const [pwMsg, setPwMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const save = async () => {
    setMsg(null);
    try {
      const body: Record<string, any> = {};
      for (const [k] of FIELDS) body[k] = form[k] === '' && (k === 'start_year' || k === 'birth_date') ? null : form[k];
      body.bio = form.bio;
      if (body.start_year !== null && body.start_year !== undefined) body.start_year = Number(body.start_year);
      const a = await api.patch('/api/me', body);
      setAccount(a);
      setMsg({ kind: 'ok', text: '已儲存' });
    } catch (e) {
      setMsg({ kind: 'error', text: errText(e) });
    }
  };
  const changePw = async () => {
    setPwMsg(null);
    if (pw.next.length < 8) return setPwMsg({ kind: 'error', text: '新密碼至少 8 碼' });
    if (pw.next !== pw.again) return setPwMsg({ kind: 'error', text: '兩次輸入的新密碼不同' });
    try {
      await api.post('/api/me/password', { current_password: pw.current, new_password: pw.next });
      setPwMsg({ kind: 'ok', text: '密碼已更新，請重新登入' });
      setTimeout(() => { void bridge.clearSession().then(() => window.location.reload()); }, 1500);
    } catch (e) {
      setPwMsg({ kind: 'error', text: errText(e) });
    }
  };
  return (
    <div className="page narrow">
      <PageHead title="個人資料" />
      <section className="panel">
        <div className="form-grid">
          <Field label="登入帳號（不可修改）"><input value={account.username} disabled /></Field>
          <Field label="稱謂（不可修改）"><input value={account.title || '—'} disabled /></Field>
          <Field label="權限（不可修改）"><input value={account.role} disabled /></Field>
          <Field label="課程群組（不可修改）"><input value={account.groups.map((g) => g.name).join('、') || '—'} disabled /></Field>
          {FIELDS.map(([k, label, type]) => (
            <Field key={k} label={label}>
              <input type={type || 'text'} value={form[k] ?? ''} onChange={(e: any) => setForm({ ...form, [k]: e.target.value })} />
            </Field>
          ))}
          <Field label="自我介紹" wide>
            <textarea rows={4} value={form.bio || ''} onChange={(e: any) => setForm({ ...form, bio: e.target.value })} />
          </Field>
        </div>
        {msg ? <Msg kind={msg.kind}>{msg.text}</Msg> : null}
        <Btn kind="primary" onClick={save}>儲存個人資料</Btn>
      </section>
      <section className="panel">
        <h2>修改密碼</h2>
        <div className="form-grid">
          <Field label="目前密碼"><input type="password" value={pw.current} onChange={(e: any) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" /></Field>
          <Field label="新密碼（至少 8 碼）"><input type="password" value={pw.next} onChange={(e: any) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" /></Field>
          <Field label="再輸入一次新密碼"><input type="password" value={pw.again} onChange={(e: any) => setPw({ ...pw, again: e.target.value })} autoComplete="new-password" /></Field>
        </div>
        {pwMsg ? <Msg kind={pwMsg.kind}>{pwMsg.text}</Msg> : null}
        <Btn kind="primary" onClick={changePw}>更新密碼</Btn>
      </section>
    </div>
  );
}
