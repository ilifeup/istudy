/**
 * dev.61「統計與報告」（Super 專用）：ASR 處理記錄——每次字幕辨識與 Outgo 錄音轉文字都記一筆（成功、失敗都記）：
 * 什麼檔案、多長、用哪個引擎、中文音訊品質（高／中／低的粗略估計與原因）、結果。可匯出 CSV（Excel 可直接開啟）與 HTML。
 */
import React, { useState } from 'react';
import { api, errText, qs } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, Field, LoadState, Msg, PageHead, useApp, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, duration, size } from '../../../../packages/ui/src/format';
import { QUALITY_HELP, QUALITY_TEXT, QualityTag, minutesPerHour } from './AsrLimits';
import { subtitleEngine } from './Queues';

const KIND: Record<string, string> = { subtitle: '字幕（影片）', outgo: 'Outgo（錄音）' };
const RESULT: Record<string, string> = { done: '✓ 成功', failed: '✗ 失敗' };
const PERIODS: [string, string][] = [['7', '最近 7 天'], ['30', '最近 30 天'], ['90', '最近 90 天'], ['365', '最近 1 年'], ['0', '全部']];

const engineName = (x: any): string => (x.engine ? subtitleEngine({ engine: x.engine }) : x.engine_requested ? subtitleEngine({ engine: x.engine_requested }) : '—');

function csvCell(v: any): string {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function asrLogCsv(items: any[]): string {
  const head = ['時間', '類型', '檔案', '檔案大小（位元組）', '長度（秒）', '長度', '引擎', '音訊品質', '品質說明', '結果', '錯誤', 'RTF', '字幕句數', '工作編號'];
  const rows = items.map((x) => [dateTime(x.created_at, true), KIND[x.kind] || x.kind, x.path, x.file_size ?? '', x.audio_sec ?? '', x.audio_sec ? duration(x.audio_sec) : '',
    engineName(x), (QUALITY_TEXT[x.quality] || x.quality).replace(/^\S+ /, ''), (x.quality_reasons || []).join('；'), (RESULT[x.status] || x.status).replace(/^\S+ /, ''),
    x.error || '', x.rtf ?? '', x.cues ?? '', x.job_id ?? '']);
  return '﻿' + [head, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';     // BOM：Excel 以 UTF-8 開啟
}

const esc = (s: any): string => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));

export function asrLogHtml(items: any[], summary: any, filterText: string): string {
  const rows = items.map((x) => `<tr><td>${esc(dateTime(x.created_at, true))}</td><td>${esc(KIND[x.kind] || x.kind)}</td><td>${esc(x.path)}</td>`
    + `<td>${esc(x.audio_sec ? duration(x.audio_sec) : '—')}</td><td>${esc(engineName(x))}</td>`
    + `<td>${esc(QUALITY_TEXT[x.quality] || x.quality)}${(x.quality_reasons || []).map((r: string) => `<div class="d">· ${esc(r)}</div>`).join('')}</td>`
    + `<td>${esc(RESULT[x.status] || x.status)}${x.error ? `<div class="d">${esc(x.error)}</div>` : ''}</td><td>${esc(x.rtf ?? '—')}</td></tr>`).join('\n');
  const q = summary.quality;
  return `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>iStudy ASR 處理記錄</title><style>
body{font-family:"Noto Sans CJK TC","Microsoft JhengHei",sans-serif;margin:24px;color:#1d2a27}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border:1px solid #b8c5c0;padding:4px 8px;text-align:left;vertical-align:top}th{background:#eef3f1}.d{color:#5b6b67;font-size:12px}
</style></head><body><h1>iStudy ASR 處理記錄</h1>
<p>匯出時間：${esc(dateTime(new Date().toISOString(), true))}｜條件：${esc(filterText)}</p>
<p>共 ${summary.total} 筆：成功 ${summary.done}、失敗 ${summary.failed}；成功辨識的總長 ${summary.audio_hours} 小時；音訊品質 高 ${q.high}／中 ${q.medium}／低 ${q.low}／未知 ${q.unknown}</p>
<p class="d">${esc(QUALITY_HELP)}</p>
<table><thead><tr><th>時間</th><th>類型</th><th>檔案</th><th>長度</th><th>引擎</th><th>音訊品質</th><th>結果</th><th>RTF</th></tr></thead><tbody>
${rows}
</tbody></table></body></html>
`;
}

export function ReportsPage(): any {
  const { toast } = useApp();
  const blank = { days: '30', kind: '', quality: '', status: '', q: '' };
  const [f, setF] = useState(blank);
  const [applied, setApplied] = useState(blank);
  const r = useLoad(() => api.get<any>('/api/admin/asr-log' + qs({ ...applied, limit: 5000 })), [JSON.stringify(applied)]);
  const items: any[] = r.data ? r.data.items : [];
  const s = r.data ? r.data.summary : null;
  const filterText = [PERIODS.find((p) => p[0] === applied.days)?.[1] || '', applied.kind ? KIND[applied.kind] : '全部類型',
    applied.quality ? `音訊品質 ${QUALITY_TEXT[applied.quality]}` : '全部品質', applied.status ? RESULT[applied.status] : '全部結果',
    applied.q ? `檔案含「${applied.q}」` : ''].filter(Boolean).join('、');
  const save = async (fmt: 'csv' | 'html') => {
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      const content = fmt === 'csv' ? asrLogCsv(items) : asrLogHtml(items, s, filterText);
      const res = await bridge.saveFile(`istudy-asr-log-${stamp}.${fmt}`, content);
      if (res.ok) toast(`已匯出 ${items.length} 筆：${res.path}`, 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const mph = s ? minutesPerHour(s.rtf_median) : null;
  return (
    <div className="page">
      <PageHead title="統計與報告">
        <Btn disabled={!items.length} onClick={() => void save('csv')}>匯出 CSV（Excel）</Btn>
        <Btn disabled={!items.length} onClick={() => void save('html')}>匯出 HTML</Btn>
      </PageHead>
      <h2>ASR 處理記錄</h2>
      <p className="dim">每次字幕辨識（影片）與 Outgo 錄音轉文字都記一筆，成功與失敗都記：什麼檔案、多長、用哪個引擎、音訊品質、結果。
        教材永久刪除後，記錄仍保留（路徑與結果），只是不再連到教材。</p>
      <section className="panel">
        <div className="form-row">
          <Field label="期間"><select value={f.days} onChange={(e: any) => setF({ ...f, days: e.target.value })}>
            {PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="類型"><select value={f.kind} onChange={(e: any) => setF({ ...f, kind: e.target.value })}>
            <option value="">全部</option><option value="subtitle">字幕（影片）</option><option value="outgo">Outgo（錄音）</option></select></Field>
          <Field label="音訊品質"><select value={f.quality} onChange={(e: any) => setF({ ...f, quality: e.target.value })}>
            <option value="">全部</option>{['high', 'medium', 'low', 'unknown'].map((k) => <option key={k} value={k}>{QUALITY_TEXT[k]}</option>)}</select></Field>
          <Field label="結果"><select value={f.status} onChange={(e: any) => setF({ ...f, status: e.target.value })}>
            <option value="">全部</option><option value="done">✓ 成功</option><option value="failed">✗ 失敗</option></select></Field>
          <Field label="檔案"><input value={f.q} placeholder="路徑或檔名" onChange={(e: any) => setF({ ...f, q: e.target.value })}
            onKeyDown={(e: any) => { if (e.key === 'Enter') setApplied({ ...f }); }} /></Field>
          <Btn kind="primary" onClick={() => setApplied({ ...f })}>篩選</Btn>
        </div>
      </section>
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {s ? (
        <table className="grid small summary">
          <tbody>
            <tr><th>處理次數</th><td>共 {s.total} 筆：✓ 成功 {s.done}、✗ 失敗 {s.failed}（字幕 {s.kind.subtitle}、Outgo {s.kind.outgo}）</td></tr>
            <tr><th>辨識總長</th><td>{s.audio_hours} 小時（只算成功的）{mph !== null ? `；速度中位數 RTF ${s.rtf_median}，1 小時約 ${mph} 分鐘` : ''}</td></tr>
            <tr><th>音訊品質</th><td>{QUALITY_TEXT.high} {s.quality.high}　{QUALITY_TEXT.medium} {s.quality.medium}　{QUALITY_TEXT.low} {s.quality.low}　{QUALITY_TEXT.unknown} {s.quality.unknown}</td></tr>
          </tbody>
        </table>
      ) : null}
      {r.data && r.data.truncated ? <Msg kind="warn">只列出最新的 {items.length} 筆（匯出也是這些）；請縮小期間或條件。</Msg> : null}
      {r.data && !items.length ? <Msg kind="info">這個條件沒有 ASR 處理記錄。字幕辨識或 Outgo 轉文字完成（或失敗）後會出現在這裡。</Msg> : null}
      {items.length ? (
        <table className="grid small">
          <thead><tr><th>時間</th><th>類型</th><th>檔案</th><th>長度</th><th>引擎</th><th>音訊品質</th><th>結果</th><th>RTF</th></tr></thead>
          <tbody>{items.map((x) => (
            <tr key={x.id}>
              <td className="nowrap">{dateTime(x.created_at)}</td>
              <td className="nowrap">{KIND[x.kind] || x.kind}</td>
              <td className="path">{x.path}{x.file_size ? <div className="dim">{size(x.file_size)}</div> : null}</td>
              <td className="nowrap">{x.audio_sec ? duration(x.audio_sec) : '—'}</td>
              <td>{engineName(x)}</td>
              <td><QualityTag level={x.quality} reasons={x.quality_reasons} /></td>
              <td><span className="nowrap">{RESULT[x.status] || x.status}</span>{x.error ? <div className="err-text">{x.error}</div> : null}</td>
              <td className="nowrap">{x.rtf ?? '—'}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
      <p className="dim">{QUALITY_HELP}</p>
    </div>
  );
}
