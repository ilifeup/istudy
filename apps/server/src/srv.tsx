/**
 * Server App 共用：Server 管理子導覽、首次設定未完成提示、主機維護（pkexec istudyctl）執行與進度顯示、檢查結果清單。
 */
import React, { useEffect, useRef, useState } from 'react';
import { api } from '../../../packages/ui/src/api';
import { bridge, HostEvent } from '../../../packages/ui/src/bridge';
import { Btn, Msg, useApp, useLoad } from '../../../packages/ui/src/core';

export const SRV_NAV: { id: string; label: string }[] = [
  { id: 'srv-overview', label: '總覽' },
  { id: 'srv-folders', label: '教材目錄' },
  { id: 'srv-media', label: '教材清單' },
  { id: 'srv-convert', label: '轉檔佇列' },
  { id: 'srv-schedule', label: '排程設定' },
  { id: 'srv-originals', label: '原檔刪除確認' },
  { id: 'srv-subtitles', label: '字幕' },
  { id: 'srv-docs', label: '文件轉換' },
  { id: 'srv-outgo', label: 'Outgo（錄音轉文字）' },
  { id: 'srv-reports', label: '統計與報告' },
  { id: 'srv-clients', label: 'Client 版本與更新' },
  { id: 'srv-ie', label: '匯入／匯出授權' },
  { id: 'srv-login', label: '登入頁設定' },
  { id: 'srv-check', label: '系統檢查與管理' },
  { id: 'srv-backup', label: '備份' },
  { id: 'srv-logs', label: 'Log' },
  { id: 'srv-uem', label: '主機盤點（UEM）' },
  { id: 'srv-host', label: '主機維護' },
];

/** 首次設定狀態：精靈完成前，Server 管理頁頂端顯示「⚠ 首次設定未完成（第 N 步）」。 */
let setupCache: { completed: boolean; step: number } | null = null;
export function setSetupCache(s: { completed: boolean; step: number } | null): void {
  setupCache = s;
}

export function SrvLayout(props: { page: string; children: any }): any {
  const { route, go } = useApp();
  const setup = useLoad(async () => (setupCache ? { setup: setupCache } : api.get<{ setup: any }>('/api/admin/setup')), [route.page]);
  const s = setup.data ? setup.data.setup : null;
  if (s && !setupCache) setupCache = s;
  return (
    <div className="srv">
      <nav className="srvnav" aria-label="Server 管理">
        {SRV_NAV.map((n) => (
          <button key={n.id} type="button" className={'srvtab' + (props.page === n.id ? ' on' : '')}
            aria-current={props.page === n.id ? 'page' : undefined} onClick={() => go(n.id)}>
            {props.page === n.id ? '▶ ' : ''}{n.label}
          </button>
        ))}
      </nav>
      <div className="srv-main">
        {s && !s.completed && props.page !== 'srv-setup' ? (
          <div className="banner warn">⚠ 首次設定未完成（第 {s.step || 1} 步）
            <Btn small onClick={() => go('srv-setup')}>繼續首次設定</Btn></div>
        ) : null}
        <div className="srv-body">{props.children}</div>
      </div>
    </div>
  );
}

// ---------- 檢查結果 ----------

export const CHECK_MARK: Record<string, string> = { pass: '✓', ok: '✓', warn: '⚠', fail: '✗', skip: '－', running: '…', pending: '○' };
export const CHECK_TEXT: Record<string, string> = { pass: '通過', ok: '完成', warn: '警告', fail: '失敗', skip: '略過', running: '進行中', pending: '等待' };

export interface CheckItem { id: string; name: string; status: string; message?: string; value?: any; detail?: string }

/** dev.55 HW-01：實際使用的轉檔編碼器（由 Agent 回報）。 */
export const ENCODER_TEXT: Record<string, string> = {
  qsv: 'Intel Quick Sync（h264_qsv）', vaapi: 'Intel VA-API（h264_vaapi）', cpu: '⚠ CPU（libx264，較慢）',
};

export function CheckList(props: { items: CheckItem[]; compact?: boolean }): any {
  return (
    <ul className={'checklist' + (props.compact ? ' compact' : '')}>
      {props.items.map((c, i) => (
        <li key={(c.id || '') + i} className={'chk ' + c.status}>
          <span className="mark" aria-hidden="true">{CHECK_MARK[c.status] || '·'}</span>
          <span className="name">{c.name}</span>
          <span className="state">{CHECK_TEXT[c.status] || c.status}</span>
          {c.message ? <span className="detail">{c.message}</span> : null}
          {c.detail ? <details className="chk-more"><summary>FFmpeg 錯誤輸出</summary><pre className="hostout">{c.detail}</pre></details> : null}
        </li>
      ))}
    </ul>
  );
}

// ---------- 主機維護：pkexec istudyctl ----------

export interface HostRun {
  runId: string;
  action: string;
  events: HostEvent[];
  exit: { code: number | null; error: string } | null;
}

/** 執行固定的主機維護動作，逐行接收 JSON 進度。 */
export function useHostRun(): {
  run: HostRun | null; running: boolean; start: (action: string, opts?: unknown) => Promise<HostRun | null>; cancel: () => void; reset: () => void;
} {
  const buf = useRef(new Map<string, HostEvent[]>());
  const current = useRef<string | null>(null);
  const waiters = useRef(new Map<string, (r: HostRun) => void>());
  const [, force] = useState(0);
  const [run, setRun] = useState<HostRun | null>(null);
  useEffect(() => bridge.host.onEvent((e) => {
    const list = buf.current.get(e.runId) || [];
    list.push(e);
    buf.current.set(e.runId, list);
    if (e.runId === current.current) {
      const exit = e.type === 'exit' ? { code: e.code ?? null, error: e.error || '' } : null;
      setRun((r: HostRun | null) => {
        if (!r || r.runId !== e.runId) return r;
        const next = { ...r, events: [...list], exit: exit || r.exit };
        if (exit) {
          const w = waiters.current.get(e.runId);
          if (w) {
            waiters.current.delete(e.runId);
            setTimeout(() => w(next), 0);
          }
        }
        return next;
      });
      force((n: number) => n + 1);
    }
  }), []);
  const start = async (action: string, opts?: unknown): Promise<HostRun | null> => {
    const r = await bridge.host.run(action, opts);
    if (r.error || !r.runId) throw new Error(r.error || '無法執行');
    current.current = r.runId;
    const early = buf.current.get(r.runId) || [];
    const ex = early.find((e) => e.type === 'exit');
    const init: HostRun = { runId: r.runId, action, events: [...early], exit: ex ? { code: ex.code ?? null, error: ex.error || '' } : null };
    setRun(init);
    if (init.exit) return init;
    return new Promise((resolve) => waiters.current.set(r.runId!, resolve));
  };
  const cancel = () => {
    if (current.current) void bridge.host.cancel(current.current);
  };
  const reset = () => {
    current.current = null;
    setRun(null);
  };
  return { run, running: !!run && !run.exit, start, cancel, reset };
}

export function lastDone(run: HostRun | null): any | null {
  if (!run) return null;
  for (let i = run.events.length - 1; i >= 0; i--) {
    const e = run.events[i];
    if (e.type === 'event' && e.data && e.data.event === 'done') return e.data;
  }
  return null;
}

export function eventsOf(run: HostRun | null, name: string): any[] {
  return run ? run.events.filter((e) => e.type === 'event' && e.data && e.data.event === name).map((e) => e.data) : [];
}

/** 步驟（最新狀態）＋檢查項目＋文字輸出＋結束訊息。 */
export function HostRunView(props: { run: HostRun | null; stepNames?: Record<number, string>; hideSteps?: boolean }): any {
  const { run } = props;
  if (!run) return null;
  const steps = new Map<number, any>();
  for (const s of eventsOf(run, 'step')) steps.set(s.step, s);
  if (props.stepNames) {
    for (const [n, name] of Object.entries(props.stepNames)) if (!steps.has(Number(n))) steps.set(Number(n), { step: Number(n), name, status: 'pending' });
  }
  const checks = eventsOf(run, 'check');
  const texts = run.events.filter((e) => e.type === 'text').map((e) => e.text);
  const infos = eventsOf(run, 'info').map((d) => d.message);
  const errors = eventsOf(run, 'error').map((d) => d.message);
  const done = lastDone(run);
  return (
    <div className="hostrun">
      {!props.hideSteps && steps.size ? (
        <table className="grid steps">
          <thead><tr><th>步驟</th><th>狀態</th><th>說明</th></tr></thead>
          <tbody>
            {Array.from(steps.values()).sort((a, b) => a.step - b.step).map((s) => (
              <tr key={s.step} className={'st-' + s.status}>
                <td>{s.step}. {s.name}</td>
                <td className="nowrap">{CHECK_MARK[s.status] || '·'} {CHECK_TEXT[s.status] || s.status}</td>
                <td>{s.message || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {checks.length ? <CheckList items={checks} /> : null}
      {infos.length || texts.length ? <pre className="hostout">{[...infos, ...texts].join('\n')}</pre> : null}
      {errors.map((m, i) => <Msg key={i} kind="error">{m.replace(/^✗\s*/, '')}</Msg>)}
      {!run.exit ? <p className="dim">… 執行中（需要 root 的步驟會跳出 Ubuntu 系統密碼視窗）</p> : null}
      {run.exit && run.exit.error ? <Msg kind="error">{run.exit.error}</Msg> : null}
      {run.exit && done && done.message ? <Msg kind={done.status === 'ok' || done.status === 'pass' ? 'ok' : done.status === 'warn' ? 'warn' : 'error'}>{done.message}</Msg> : null}
    </div>
  );
}
