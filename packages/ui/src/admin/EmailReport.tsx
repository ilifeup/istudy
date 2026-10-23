/**
 * Email 報告（dev.57，Super 專用）：Server App 與 Client App 都有。
 * Client 電腦不能上網：內容只送到 iStudy Server，由 Server 的 Agent 代寄（網站代寄或 SMTP）。
 * 寄送設定只能在 Server App 修改；金鑰與密碼不會回傳到畫面。
 */
import React, { useEffect, useState } from 'react';
import { api, errText } from '../api';
import { bridge } from '../bridge';
import { Btn, Field, LoadState, Msg, PageHead, useApp, useInterval, useLoad } from '../core';
import { dateTime } from '../format';

const REPORT_TEXT: Record<string, string> = { summary: '系統狀態摘要', asr: '字幕辨識結果', message: '自訂訊息', test: '測試信', feedback: '問題回報' };
// done＝寄送主機（網站 mail() 或 SMTP 伺服器）已接受；沒有收件回執，所以不寫「已寄達」
const STATUS: Record<string, string> = { queued: '… 等待寄送', running: '… 寄送中', done: '✓ 已提交寄送', failed: '✗ 失敗', cancelled: '－ 已取消' };

function Settings(props: { onSaved: () => void }): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<any>('/api/admin/email'), []);
  const [f, setF] = useState<any>(null);
  useEffect(() => {
    if (r.data) setF({ ...r.data, to: (r.data.to || []).join('\n'), password: '', relay_key: '' });
  }, [r.data]);
  if (!f) return <LoadState loading={r.loading} error={r.error} reload={r.reload} />;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const save = async () => {
    try {
      const body: any = { method: f.method, to: String(f.to).split(/[\n,]/).map((x: string) => x.trim()).filter(Boolean) };
      if (f.method === 'relay') {
        body.relay_url = f.relay_url;
        if (f.relay_key) body.relay_key = f.relay_key;
      } else {
        Object.assign(body, { host: f.host, port: Number(f.port) || 587, security: f.security, username: f.username, from: f.from });
        if (f.password) body.password = f.password;
      }
      await api.put('/api/admin/email', body);
      toast('寄送設定已儲存', 'ok');
      r.reload();
      props.onSaved();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const test = async () => {
    try {
      await api.post('/api/admin/email/test', {});
      toast('已排入測試信，請看下方寄送紀錄', 'ok');
      props.onSaved();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <section className="panel">
      <h2>寄送設定（只能在 Server App 修改）</h2>
      <div className="form-grid">
        <Field label="寄送方式">
          <select value={f.method} onChange={(e: any) => set('method', e.target.value)}>
            <option value="relay">網站代寄（建議：不需要信箱密碼）</option>
            <option value="smtp">SMTP 寄件信箱</option>
          </select>
        </Field>
        {f.method === 'relay' ? (
          <>
            <Field label="代寄網址" wide hint="把 deploy/email/istudy-mail-relay.php 上傳到可以寄信的網站主機，填它的 https 網址">
              <input value={f.relay_url} placeholder="https://work.eachcore.com/istudy-mail-relay.php" onChange={(e: any) => set('relay_url', e.target.value)} />
            </Field>
            <Field label="代寄金鑰" hint={f.relay_key_set ? '已設定（不顯示）；要更換才需要填' : '與代寄程式裡的 RELAY_KEY 相同，至少 24 個字'}>
              <input type="password" value={f.relay_key} autoComplete="new-password" onChange={(e: any) => set('relay_key', e.target.value)} />
            </Field>
          </>
        ) : (
          <>
            <Field label="SMTP 主機"><input value={f.host} placeholder="smtp.gmail.com" onChange={(e: any) => set('host', e.target.value)} /></Field>
            <Field label="連接埠"><input value={f.port} inputMode="numeric" onChange={(e: any) => set('port', e.target.value)} /></Field>
            <Field label="加密">
              <select value={f.security} onChange={(e: any) => set('security', e.target.value)}>
                <option value="starttls">STARTTLS（587）</option><option value="ssl">SSL（465）</option><option value="none">不加密</option>
              </select>
            </Field>
            <Field label="帳號"><input value={f.username} onChange={(e: any) => set('username', e.target.value)} /></Field>
            <Field label="密碼" hint={f.password_set ? '已設定（不顯示）；要更換才需要填' : '網站主機寄件信箱的密碼，或 Gmail 的「應用程式密碼」'}>
              <input type="password" value={f.password} autoComplete="new-password" onChange={(e: any) => set('password', e.target.value)} />
            </Field>
            <Field label="寄件人"><input value={f.from} placeholder="noreply@eachcore.com" onChange={(e: any) => set('from', e.target.value)} /></Field>
          </>
        )}
        <Field label="收件人（一行一位，最多 5 位）" wide hint={f.method === 'relay' ? '網站代寄只會寄給代寄程式 RELAY_TO 也允許的地址' : ''}>
          <textarea rows={2} value={f.to} onChange={(e: any) => set('to', e.target.value)} />
        </Field>
      </div>
      <div className="row">
        <Btn kind="primary" onClick={save}>儲存設定</Btn>
        <Btn onClick={test} disabled={!r.data || !r.data.configured}>寄測試信</Btn>
        <span className="dim">{r.data && r.data.configured ? '✓ 已設定' : '○ 尚未設定'}</span>
      </div>
    </section>
  );
}

export function EmailReportPage(): any {
  const { toast, info } = useApp();
  const r = useLoad(() => api.get<any>('/api/email'), []);
  const [report, setReport] = useState('summary');
  const [subject, setSubject] = useState('');
  const [note, setNote] = useState('');
  const [attachUem, setAttachUem] = useState(false);
  const isServerApp = info.kind === 'server';
  const busy = r.data && r.data.history.some((h: any) => h.status === 'queued' || h.status === 'running');
  useInterval(() => r.reload(), busy ? 3000 : null);
  const send = async () => {
    try {
      const body: any = { report, subject, note };
      if (isServerApp && attachUem) {
        const u = await bridge.host.uemLatest();
        if (!u || !u.html) return toast('還沒有主機盤點報告：請先到「主機盤點（UEM）」按「快速盤點」', 'error');
        body.uem_html = u.html;
      }
      await api.post('/api/email/send', body);
      toast('已送到 Server，由 Server 代寄', 'ok');
      setNote('');
      setSubject('');
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const d = r.data;
  return (
    <div className="page">
      <PageHead title="Email 報告" />
      <Msg kind="info">內容先送到 iStudy Server，再由 Server 代寄（這台電腦不需要連上網際網路）。只會寄給設定好的收件人。</Msg>
      <LoadState loading={r.loading && !d} error={r.error} reload={r.reload} />
      {d && d.can_edit ? <Settings onSaved={r.reload} /> : null}
      {d ? (
        <>
          {!d.configured ? (
            <Msg kind="warn">{d.can_edit ? '請先完成上方的寄送設定' : 'Email 尚未設定：請在 Server App 的「Email 報告」完成寄送設定'}</Msg>
          ) : null}
          <section className="panel">
            <h2>問題回報（Client 使用者送出，最近 30 筆）</h2>
            <p className="dim">使用者在 Client App 按「問題回報」送出的內容；Server 會寄到上面的收件人。沒寄出時（例如 Email 尚未設定）也會留在這裡。</p>
            {d.feedback && d.feedback.length ? (
              <table className="grid small">
                <thead><tr><th>時間</th><th>回報者</th><th>電腦／App</th><th>問題概述</th><th>寄信</th></tr></thead>
                <tbody>{d.feedback.map((x: any) => (
                  <tr key={x.id}>
                    <td className="nowrap">{dateTime(x.created_at)}</td>
                    <td className="nowrap">{x.username}{x.full_name ? `（${x.full_name}）` : ''}</td>
                    <td>{x.pc || '—'}{x.app ? <div className="dim">{x.app}</div> : null}</td>
                    <td className="pre-wrap">{x.summary}</td>
                    <td>{x.status === 'failed' ? <><span className="nowrap">✗ 沒寄出</span><div className="err-text">{x.error}</div></> : <span className="nowrap">{STATUS[x.status] || x.status}</span>}</td>
                  </tr>
                ))}</tbody>
              </table>
            ) : <p className="dim">目前沒有問題回報</p>}
          </section>
          <section className="panel">
            <h2>寄出報告</h2>
            <p className="dim">收件人：{(d.to || []).join('、')}　寄送方式：{d.method === 'relay' ? '網站代寄' : 'SMTP'}</p>
            <div className="radio-row" role="radiogroup" aria-label="報告內容">
              {['summary', 'asr', 'message'].map((k) => (
                <label key={k} className="check">
                  <input type="radio" name="report" checked={report === k} onChange={() => setReport(k)} /> {REPORT_TEXT[k]}
                </label>
              ))}
            </div>
            <div className="form-grid">
              <Field label="主旨（可空白，自動產生）" wide><input value={subject} maxLength={120} onChange={(e: any) => setSubject(e.target.value)} /></Field>
              <Field label={report === 'message' ? '訊息內容（必填）' : '補充說明（可空白）'} wide>
                <textarea rows={4} value={note} maxLength={2000} onChange={(e: any) => setNote(e.target.value)} />
              </Field>
            </div>
            {isServerApp ? (
              <label className="check"><input type="checkbox" checked={attachUem} onChange={() => setAttachUem(!attachUem)} /> {attachUem ? '☑' : '☐'} 附上主機盤點（UEM）最新報告（HTML）</label>
            ) : null}
            <div className="row">
              <Btn kind="primary" disabled={!d.configured || busy} onClick={send}>寄出</Btn>
              {busy ? <span className="dim">… 上一封寄送中</span> : <span className="dim">一次一封；24 小時最多 {d.daily_limit} 封</span>}
            </div>
          </section>
          <section className="panel">
            <h2>寄送紀錄（最近 20 封）</h2>
            <p className="dim">「✓ 已提交寄送」＝寄送主機已接受，不代表已送達；請到收件匣（含垃圾郵件）確認。</p>
            {d.history.length ? (
              <table className="grid small">
                <thead><tr><th>時間</th><th>內容</th><th>寄件者</th><th>狀態</th><th>說明</th></tr></thead>
                <tbody>{d.history.map((h: any) => (
                  <tr key={h.id}>
                    <td className="nowrap">{dateTime(h.created_at)}</td>
                    <td>{REPORT_TEXT[h.report] || h.report}{h.attachment ? '（附主機盤點）' : ''}</td>
                    <td>{h.by || '—'}</td>
                    <td className="nowrap">{STATUS[h.status] || h.status}</td>
                    <td>{h.status === 'failed' ? <span className="err-text">{h.error}</span> : (h.subject ? `${h.subject} → ${(h.to || []).join('、')}` : '')}</td>
                  </tr>
                ))}</tbody>
              </table>
            ) : <p className="dim">還沒有寄過</p>}
          </section>
        </>
      ) : null}
    </div>
  );
}
