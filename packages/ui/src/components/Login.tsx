/**
 * 登入頁（第 06 章）：狀態一行、帳號、密碼（提示文字由 Super 設定）、登入、喚醒電腦、頁尾版別與版本。
 * Server 位址不在任何畫面顯示。
 */
import React, { useEffect, useState } from 'react';
import { api, errText } from '../api';
import { Account, AppInfo, bridge, HealthSnapshot } from '../bridge';
import { Btn, Msg, useInterval } from '../core';
import { ago, shortDateTime } from '../format';

export function HealthLine(props: { health: HealthSnapshot; onRetry?: () => void }): any {
  const s = props.health.state;
  if (s === 'checking') return <div className="health checking">狀態：… 正在檢查伺服器</div>;
  if (s === 'ok') return <div className="health ok">狀態：✓ 伺服器連線正常</div>;
  if (s === 'update_required') return <div className="health fail">狀態：✗ App 需要更新</div>;
  return (
    <div className="health fail">
      狀態：✗ 伺服器無法連線 {props.onRetry ? <Btn small onClick={props.onRetry}>重試</Btn> : null}
    </div>
  );
}

interface PublicDevice {
  id: number; name: string; os: string; state: string; online: boolean; last_seen_at: string | null; last_wake_at: string | null;
}

export function deviceStateText(d: { state: string; last_seen_at: string | null }): string {
  switch (d.state) {
    case 'online': return `● 在線（最後回報 ${ago(d.last_seen_at)}）`;
    case 'booted': return `✓ 已開機（${ago(d.last_seen_at)}回報）`;
    case 'waiting': return '… 已送出，等待開機回報';
    case 'no_report': return '⚠ 5 分鐘內未回報';
    default: return d.last_seen_at ? `○ 未回報（最後回報 ${shortDateTime(d.last_seen_at)}）` : '○ 未回報';
  }
}

export function WakePanel(props: { onClose: () => void }): any {
  const [devices, setDevices] = useState<PublicDevice[] | null>(null);
  const [counts, setCounts] = useState<{ registered: number; disabled: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = async () => {
    try {
      const r = await api.get<{ devices: PublicDevice[]; registered_total?: number; disabled_total?: number }>('/api/wol/public/devices');
      setDevices(r.devices);
      setCounts(typeof r.registered_total === 'number' ? { registered: r.registered_total, disabled: r.disabled_total || 0 } : null);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  };
  useEffect(() => { void load(); }, []);
  useInterval(() => void load(), 15000); // 展開時每 15 秒重新讀取；收合後元件卸載即停止
  const wake = async (body: Record<string, unknown>) => {
    try {
      const r = await api.post<{ sent: unknown[]; cooling: { retry_after: number }[] }>('/api/wol/public/wake', body);
      setNotice(`已送出 ${r.sent.length} 台` + (r.cooling.length ? `；${r.cooling.length} 台剛喚醒過，稍後再試` : ''));
      await load();
    } catch (e) {
      setNotice(errText(e));
    }
  };
  return (
    <section className="wake-panel" aria-label="Client 電腦">
      <div className="row between">
        <h2>Client 電腦</h2>
        <div className="row">
          <Btn onClick={() => wake({ all: true })} disabled={!devices || !devices.length}>全部喚醒</Btn>
          <Btn kind="quiet" onClick={props.onClose}>✕ 收合</Btn>
        </div>
      </div>
      {error ? <Msg kind="error">{error}</Msg> : null}
      {notice ? <Msg kind="info">{notice}</Msg> : null}
      {devices === null && !error ? <div className="loading">… 讀取中</div> : null}
      {/* dev.62：清單只列「已登記並啟用喚醒」的電腦；已登記但未啟用的要說清楚，不然看起來和 WOL 管理不一致 */}
      {devices && !devices.length ? (counts && counts.disabled
        ? <Msg kind="warn">已登記 {counts.registered} 台電腦，但都還沒有啟用喚醒，所以這裡沒有可以喚醒的電腦。請 Super 在「WOL 管理」把要喚醒的電腦設為「✓ 啟用」。</Msg>
        : <Msg kind="info">尚未登記任何電腦（由 Super 在「WOL 管理」設定）</Msg>) : null}
      {devices && devices.length > 0 && counts && counts.disabled > 0 ? <p className="dim">另有 {counts.disabled} 台已登記但未啟用喚醒，不會顯示在這裡。</p> : null}
      {devices && devices.length ? (
        <table className="grid">
          <thead><tr><th>名稱</th><th>作業系統</th><th>狀態</th><th>操作</th></tr></thead>
          <tbody>
            {devices.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td>
                <td>{d.os || '—'}</td>
                <td className={'dev-' + d.state}>{deviceStateText(d)}</td>
                <td>{d.state === 'offline' || d.state === 'no_report' ? <Btn small onClick={() => wake({ device_id: d.id })}>喚醒</Btn> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="dim">狀態每 15 秒更新一次；這裡只能喚醒。電腦的名稱、排序與啟用由 Super 在「WOL 管理」設定</p>
    </section>
  );
}

/** dev.62：密碼欄右側的小按鈕。按一下常態顯示密碼，再按一下隱藏；圖示（眼睛／加斜線）＋文字，不只靠顏色區分狀態。
 *  圖示用內嵌 SVG，不依賴 Server 桌面是否裝了 emoji 字型。 */
function PasswordEye(props: { shown: boolean; onToggle: () => void }): any {
  const label = props.shown ? '隱藏密碼' : '顯示密碼';
  return (
    <button type="button" className="pw-eye" aria-label={label} aria-pressed={props.shown}
      title={props.shown ? '隱藏密碼' : '顯示密碼（確認輸入是否正確）'} onClick={props.onToggle}
      style={{ position: 'absolute', right: 6, bottom: 8, height: 28, display: 'inline-flex', alignItems: 'center', gap: 3,
        padding: '0 6px', fontSize: 12, lineHeight: 1, border: props.shown ? '1px solid currentColor' : '1px dashed currentColor',
        borderRadius: 4, background: 'transparent', color: 'inherit', cursor: 'pointer' }}>
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
        <circle cx="12" cy="12" r="3" />
        {props.shown ? <line x1="3" y1="21" x2="21" y2="3" /> : null}
      </svg>
      <span>{props.shown ? '隱藏' : '顯示'}</span>
    </button>
  );
}

export function LoginPage(props:{ info: AppInfo; health: HealthSnapshot; onLogin: (a: Account) => void; serverApp?: boolean; notice?: string | null }): any {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wakeOpen, setWakeOpen] = useState(false);
  const [showPw, setShowPw] = useState(false);   // dev.62：「顯示密碼」
  const [ph, setPh] = useState({ username_placeholder: '請輸入帳號', password_placeholder: '請輸入密碼' });
  useEffect(() => {
    bridge.loginConfig().then(setPh, () => undefined);
  }, [props.health.state === 'ok']);
  const canLogin = props.health.state === 'ok' && !busy;
  const submit = async (e: any) => {
    e.preventDefault();
    if (!canLogin) return;
    if (!username.trim() || !password) {
      setError('請輸入帳號與密碼');
      return;
    }
    setBusy(true);
    setError(null);
    const r = await bridge.login(username.trim(), password);
    setBusy(false);
    if (r.ok && r.account) {
      setPassword('');
      setShowPw(false);
      props.onLogin(r.account);
    } else {
      setError(r.error || '登入失敗');
    }
  };
  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-brand">
          <span className="seal" aria-hidden="true">iStudy</span>
          <div>
            <div className="brand-name">iStudy</div>
            <div className="brand-sub">iStudy 學習系統{props.serverApp ? '　Server App' : ''}</div>
          </div>
        </div>
        <HealthLine health={props.health} onRetry={() => void bridge.retryHealth()} />
        {props.notice ? <Msg kind="info">{props.notice}</Msg> : null}
        <form className="login-form" onSubmit={submit}>
          <label className="field">
            <span>帳號</span>
            <input value={username} onChange={(e: any) => setUsername(e.target.value)} placeholder={ph.username_placeholder}
              autoComplete="username" autoFocus />
          </label>
          {/* dev.62：「顯示密碼」小按鈕（Client 與 Server App）；按鈕放在 label 外（欄位名稱仍是「密碼」），疊在輸入框右側 */}
          <div style={{ position: 'relative' }}>
            <label className="field">
              <span>密碼</span>
              <input type={showPw ? 'text' : 'password'} value={password} onChange={(e: any) => setPassword(e.target.value)}
                placeholder={ph.password_placeholder} autoComplete={showPw ? 'off' : 'current-password'} spellCheck={false}
                autoCapitalize="off" style={{ paddingRight: 78 }} />
            </label>
            <PasswordEye shown={showPw} onToggle={() => setShowPw(!showPw)} />
          </div>
          {error ? <Msg kind="error">{error}</Msg> : null}
          <div className="row">
            <button type="submit" className="btn primary" disabled={!canLogin}>{busy ? '處理中…' : '登入'}</button>
            {!props.serverApp ? (
              <button type="button" className={'btn plain' + (wakeOpen ? ' on' : '')} aria-expanded={wakeOpen}
                onClick={() => setWakeOpen(!wakeOpen)}>喚醒電腦</button>
            ) : null}
            <span className="dim">忘記密碼？請洽管理員重設</span>
          </div>
        </form>
        {wakeOpen ? <WakePanel onClose={() => setWakeOpen(false)} /> : null}
        <footer className="login-foot dim">{props.info.versionLabel} · © 2026 iStudy</footer>
      </div>
    </div>
  );
}
