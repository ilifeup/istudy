/**
 * 問題回報（dev.57，只在 Client App）：任何登入的使用者都可以用。
 * 實際流程：內容送到 iStudy Server，由 Server 代寄給 Super（Email 報告的收件人），Super 也能在 Server App「Email 報告」的寄送紀錄看到。
 * 畫面上不提寄信：送出後只顯示下面這段安撫訊息（依需求固定文字）。
 */
import React, { useState } from 'react';
import { api, errText } from '../api';
import { Btn, Field, LoadState, Modal, Msg, PageHead, useApp, useLoad } from '../core';
import { dateTime } from '../format';

export const FEEDBACK_THANKS = [
  '本系統不連線網際網路~',
  '陳鴻文將於下次到教室時候獲知此訊息，再為您查看問題，感謝您，祝您身心康泰',
  '~無相氣學 萬馬奔騰!',
];

export function FeedbackPage(): any {
  const { account, info, toast } = useApp();
  const mine = useLoad(() => api.get<{ items: any[] }>('/api/feedback/mine'), []);
  const [summary, setSummary] = useState('');
  const [sending, setSending] = useState(false);
  const [thanks, setThanks] = useState(false);
  const submit = async () => {
    if (summary.trim().length < 2) return toast('請寫下問題概述', 'error');
    setSending(true);
    try {
      await api.post('/api/feedback', { summary: summary.trim(), app: `Client ${info.versionLabel}`, pc: info.hostname, page: '問題回報' });
      setSummary('');
      setThanks(true);
      mine.reload();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="page">
      <PageHead title="問題回報" />
      <Msg kind="info">寫下遇到的問題後按「送出回報」。會一起附上你的帳號（{account.username}{account.full_name ? `，${account.full_name}` : ''}）、App 版本與電腦名稱。</Msg>
      <section className="panel">
        <Field label="問題概述（必填，最多 1000 字）" wide hint="例如：第三堂課的影片播到一半沒有聲音；哪一支影片、什麼時候發生">
          <textarea rows={5} value={summary} maxLength={1000} onChange={(e: any) => setSummary(e.target.value)} />
        </Field>
        <div className="row">
          <Btn kind="primary" onClick={submit} disabled={sending || summary.trim().length < 2}>{sending ? '送出中…' : '送出回報'}</Btn>
          <span className="dim">{summary.length}／1000</span>
        </div>
      </section>
      <section className="panel">
        <h2>我送出的回報（最近 10 筆）</h2>
        <LoadState loading={mine.loading && !mine.data} error={mine.error} reload={mine.reload} />
        {mine.data && !mine.data.items.length ? <p className="dim">還沒有回報過</p> : null}
        {mine.data && mine.data.items.length ? (
          <table className="grid small">
            <thead><tr><th>送出時間</th><th>內容</th><th>狀態</th></tr></thead>
            <tbody>{mine.data.items.map((x) => (
              <tr key={x.id}><td className="nowrap">{dateTime(x.created_at)}</td><td className="pre-wrap">{x.summary}</td><td className="nowrap">✓ 已送出</td></tr>
            ))}</tbody>
          </table>
        ) : null}
      </section>
      {thanks ? (
        <Modal title="問題回報已送出" onClose={() => setThanks(false)} footer={<Btn kind="primary" onClick={() => setThanks(false)}>好</Btn>}>
          <div className="feedback-thanks">{FEEDBACK_THANKS.map((l) => <p key={l}>{l}</p>)}</div>
        </Modal>
      ) : null}
    </div>
  );
}
