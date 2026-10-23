/**
 * 共用：App 狀態（登入者、導覽）、資料載入（忽略過期回應）、按鈕（處理中…）、訊息、對話框、分頁、提醒。
 */
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { errText } from './api';
import type { Account, AppInfo, HealthSnapshot } from './bridge';

// ---------- 導覽 ----------

export interface Route {
  page: string;
  params?: Record<string, any>;
}

export interface AppCtx {
  info: AppInfo;
  account: Account;
  health: HealthSnapshot;
  route: Route;
  go: (page: string, params?: Record<string, any>) => void;
  back: () => void;
  setAccount: (a: Account) => void;
  logout: () => Promise<void>;
  onBeforeLogout: (fn: () => Promise<void> | void) => () => void;
  toast: (text: string, kind?: MsgKind, ms?: number) => void;
}

export const AppContext = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const c = useContext<AppCtx | null>(AppContext);
  if (!c) throw new Error('AppContext missing');
  return c;
}

export const ROLE_LEVEL: Record<string, number> = { user: 1, admin: 2, super: 3 };

/** 稱謂與權限一起顯示：助教、助教・admin、super。稱謂只是名稱，權限才決定能做什麼。 */
export function roleBadge(a: { role: string; title?: string | null }): string {
  return [a.title || '', a.role !== 'user' ? a.role : ''].filter(Boolean).join('・') || a.role;
}
export const atLeast = (a: Account, role: string) => (ROLE_LEVEL[a.role] || 0) >= ROLE_LEVEL[role];

// ---------- 資料載入：切換畫面後忽略過期回應（沿用 dev.41～dev.44 的保護） ----------

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): {
  data: T | null; error: string | null; raw: unknown; loading: boolean; reload: () => void; setData: (d: T | null) => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    setRaw(null);
    fn().then((d) => {
      if (my === seq.current) {
        setData(d);
        setLoading(false);
      }
    }, (e) => {
      if (my === seq.current) {
        setError(errText(e));
        setRaw(e);
        setLoading(false);
      }
    });
    return () => {
      seq.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t: number) => t + 1), []);
  return { data, error, raw, loading, reload, setData };
}

/** 定期重新讀取（例如設備清單每 15 秒）。 */
export function useInterval(fn: () => void, ms: number | null): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms]);
}

// ---------- 按鈕：要求中顯示「處理中…」並停用 ----------

export function Btn(props: {
  children: any; onClick?: () => any; kind?: 'primary' | 'plain' | 'danger' | 'quiet'; disabled?: boolean;
  title?: string; type?: 'button' | 'submit'; on?: boolean; small?: boolean; busyText?: string; className?: string;
}): any {
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);
  const click = async () => {
    if (!props.onClick || busy) return;
    const r = props.onClick();
    if (r && typeof r.then === 'function') {
      setBusy(true);
      try {
        await r;
      } finally {
        if (mounted.current) setBusy(false);
      }
    }
  };
  const cls = ['btn', props.kind || 'plain', props.on ? 'on' : '', props.small ? 'small' : '', props.className || ''].join(' ');
  return (
    <button type={props.type || 'button'} className={cls} disabled={props.disabled || busy} title={props.title}
      aria-pressed={props.on === undefined ? undefined : props.on} onClick={props.type === 'submit' ? undefined : click}>
      {busy ? (props.busyText || '處理中…') : props.children}
    </button>
  );
}

// ---------- 訊息（狀態不只靠顏色：一律有符號與文字） ----------

export type MsgKind = 'error' | 'warn' | 'ok' | 'info';
const MARK: Record<MsgKind, string> = { error: '✗', warn: '⚠', ok: '✓', info: 'ⓘ' };

export function Msg(props: { kind: MsgKind; children: any; action?: any }): any {
  return (
    <div className={'msg ' + props.kind} role={props.kind === 'error' ? 'alert' : 'status'}>
      <span className="mark" aria-hidden="true">{MARK[props.kind]}</span>
      <span className="text">{props.children}</span>
      {props.action}
    </div>
  );
}

export function Loading(props: { text?: string }): any {
  return <div className="loading">… {props.text || '讀取中'}</div>;
}

export function LoadState(props: { loading: boolean; error: string | null; reload?: () => void }): any {
  if (props.error) return <Msg kind="error" action={props.reload ? <Btn small onClick={props.reload}>重試</Btn> : null}>{props.error}</Msg>;
  if (props.loading) return <Loading />;
  return null;
}

// ---------- 對話框 ----------

export function Modal(props: { title: string; children: any; onClose: () => void; footer?: any; wide?: boolean }): any {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') props.onClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [props.onClose]);
  return (
    <div className="modal-back" onMouseDown={(e: any) => { if (e.target === e.currentTarget) props.onClose(); }}>
      <div className={'modal' + (props.wide ? ' wide' : '')} role="dialog" aria-modal="true" aria-label={props.title}>
        <div className="modal-head">
          <h2>{props.title}</h2>
          <button type="button" className="btn quiet small" onClick={props.onClose}>✕ 關閉</button>
        </div>
        <div className="modal-body">{props.children}</div>
        {props.footer ? <div className="modal-foot">{props.footer}</div> : null}
      </div>
    </div>
  );
}

/** 需要輸入確認字樣的危險操作（刪除帳號、永久刪除原檔、清除使用記錄）。 */
export function TypedConfirm(props: { title: string; message: any; expect: string; confirmText: string;
  onConfirm: () => Promise<void>; onClose: () => void }): any {
  const [v, setV] = useState('');
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title={props.title} onClose={props.onClose} footer={<>
      <Btn kind="danger" disabled={v !== props.expect} onClick={async () => {
        try {
          setErr(null);
          await props.onConfirm();
          props.onClose();
        } catch (e) {
          setErr(errText(e));
        }
      }}>{props.confirmText}</Btn>
      <Btn onClick={props.onClose}>取消</Btn>
    </>}>
      <div className="stack">
        <div>{props.message}</div>
        <label className="field">
          <span>請輸入「{props.expect}」確認</span>
          <input value={v} onChange={(e: any) => setV(e.target.value)} autoFocus />
        </label>
        {err ? <Msg kind="error">{err}</Msg> : null}
      </div>
    </Modal>
  );
}

// ---------- 分頁 ----------

export function Tabs(props: { tabs: { id: string; label: string }[]; value: string; onChange: (id: string) => void; small?: boolean }): any {
  return (
    <div className={'tabs' + (props.small ? ' small' : '')} role="tablist">
      {props.tabs.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={t.id === props.value}
          className={'tab' + (t.id === props.value ? ' on' : '')} onClick={() => props.onChange(t.id)}>
          {t.id === props.value ? '▶ ' : ''}{t.label}
        </button>
      ))}
    </div>
  );
}

// ---------- 右下角提醒 ----------

export interface ToastItem { id: number; text: string; kind: MsgKind; until: number }

export function Toasts(props: { items: ToastItem[] }): any {
  const [, force] = useState(0);
  useInterval(() => force((n: number) => n + 1), props.items.length ? 500 : null);
  return (
    <div className="toasts" aria-live="polite">
      {props.items.map((t) => {
        const left = Math.max(0, Math.ceil((t.until - Date.now()) / 1000));
        return (
          <div key={t.id} className={'toast ' + t.kind}>
            <span className="mark">{MARK[t.kind]}</span> {t.text.replace('{s}', String(left))}
          </div>
        );
      })}
    </div>
  );
}

// ---------- 小工具 ----------

export function Field(props: { label: string; children: any; hint?: string; wide?: boolean }): any {
  return (
    <label className={'field' + (props.wide ? ' wide' : '')}>
      <span>{props.label}</span>
      {props.children}
      {props.hint ? <small className="hint">{props.hint}</small> : null}
    </label>
  );
}

export function PageHead(props: { title: string; children?: any }): any {
  return (
    <div className="page-head">
      <h1>{props.title}</h1>
      <div className="page-actions">{props.children}</div>
    </div>
  );
}

export function useLocalState<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = (nv: T) => {
    setV(nv);
    try {
      localStorage.setItem(key, JSON.stringify(nv));
    } catch {
      /* 忽略 */
    }
  };
  return [v, set];
}
