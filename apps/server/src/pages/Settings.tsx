/**
 * 排程設定、匯入／匯出授權、登入頁設定（第 10 章）。三頁都寫入 server_config，變更寫入使用記錄。
 */
import React, { useEffect, useState } from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { Btn, Field, LoadState, Msg, PageHead, useApp, useLoad } from '../../../../packages/ui/src/core';
import { AsrLengthLine } from './AsrLimits';

export function useConfig() {
  return useLoad(() => api.get<any>('/api/admin/config'), []);
}

// ---------- 排程設定 ----------

export function ScheduleForm(props: { compact?: boolean; onSaved?: () => void }): any {
  const cfg = useConfig();
  const [s, setS] = useState<any | null>(null);
  const [clip, setClip] = useState<string>('1.9');
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  useEffect(() => {
    if (cfg.data) {
      setS({ ...cfg.data.schedule });
      setClip(String(cfg.data.clip_threshold_gib));
    }
  }, [cfg.data]);
  const save = async () => {
    setMsg(null);
    try {
      await api.put('/api/admin/config', {
        schedule: { start: s.start, end: s.end, backup_time: s.backup_time, auto_convert: !!s.auto_convert,
          auto_whisper: !!s.auto_whisper, whisper_after_convert: !!s.whisper_after_convert },
        ...(props.compact ? {} : { clip_threshold_gib: Number(clip) }),
      });
      setMsg({ kind: 'ok', text: '已儲存；下一次排程立即套用' });
      props.onSaved?.();
    } catch (e) {
      setMsg({ kind: 'error', text: errText(e) });
    }
  };
  if (!s) return <LoadState loading={cfg.loading} error={cfg.error} reload={cfg.reload} />;
  const set = (k: string, v: any) => setS({ ...s, [k]: v });
  const crosses = s.start > s.end;
  return (
    <div className="stack">
      <div className="form-grid">
        <Field label="系統工作開始" hint="Asia/Taipei"><input type="time" value={s.start} onChange={(e: any) => set('start', e.target.value)} /></Field>
        <Field label="系統工作結束" hint={crosses ? '跨午夜（到隔天）' : ''}><input type="time" value={s.end} onChange={(e: any) => set('end', e.target.value)} /></Field>
        <Field label="每日備份時間"><input type="time" value={s.backup_time} onChange={(e: any) => set('backup_time', e.target.value)} /></Field>
        {!props.compact ? <Field label="AVCHD 合集判定門檻（GiB）" hint="前一支檔案大於等於此大小才視為被攝影機切段（預設 1.9）">
          <input type="number" step="0.1" min={0.1} max={64} value={clip} onChange={(e: any) => setClip(e.target.value)} /></Field> : null}
      </div>
      <div className="checks">
        <label className="check"><input type="checkbox" checked={!!s.auto_convert} onChange={() => set('auto_convert', !s.auto_convert)} /> {s.auto_convert ? '☑' : '☐'} 時段內自動轉檔</label>
        <label className="check"><input type="checkbox" checked={!!s.auto_whisper} onChange={() => set('auto_whisper', !s.auto_whisper)} /> {s.auto_whisper ? '☑' : '☐'} 時段內自動產生字幕</label>
        <label className="check"><input type="checkbox" checked={!!s.whisper_after_convert} onChange={() => set('whisper_after_convert', !s.whisper_after_convert)} /> {s.whisper_after_convert ? '☑' : '☐'} 字幕在轉檔佇列空了之後才開始（建議）</label>
      </div>
      <p className="dim">系統工作（轉檔、字幕）只在 {s.start}～{s.end}{crosses ? '（跨午夜）' : ''} 執行；Super 按「立即執行」的單支影片不受時段限制。加密備份在 {s.backup_time} 建立；保留天數（10～15 天）與遠端上傳在「備份」頁設定。</p>
      {s.auto_whisper ? <AsrLengthLine /> : null}
      {msg ? <Msg kind={msg.kind}>{msg.text}</Msg> : null}
      <div><Btn kind="primary" onClick={save}>儲存排程</Btn></div>
    </div>
  );
}

export function SchedulePage(): any {
  return (
    <div className="page narrow">
      <PageHead title="排程設定" />
      <section className="panel"><ScheduleForm /></section>
    </div>
  );
}

// ---------- 匯入／匯出授權 ----------

export function ImportExportPage(): any {
  const { toast } = useApp();
  const cfg = useConfig();
  const [ids, setIds] = useState<number[]>([]);
  useEffect(() => { if (cfg.data) setIds(cfg.data.import_export_admin_ids); }, [cfg.data]);
  const save = async () => {
    try {
      await api.put('/api/admin/config', { import_export_admin_ids: ids });
      toast('已儲存匯入／匯出授權', 'ok');
      cfg.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="page narrow">
      <PageHead title="匯入／匯出授權" />
      <section className="panel">
        <p>所有資料的匯入與匯出（字幕 SRT／VTT、使用記錄 CSV）只有 Super 可以使用；Super 可以授權個別 Admin。其他角色一律不能使用。</p>
        <LoadState loading={cfg.loading} error={cfg.error} reload={cfg.reload} />
        {cfg.data && !cfg.data.admins.length ? <Msg kind="info">目前沒有 Admin 帳號（在「帳號管理」建立）</Msg> : null}
        {cfg.data && cfg.data.admins.length ? (
          <table className="grid">
            <thead><tr><th>Admin</th><th>帳號狀態</th><th>可使用匯入／匯出</th></tr></thead>
            <tbody>
              {cfg.data.admins.map((a: any) => (
                <tr key={a.id}>
                  <td>{a.full_name || a.username}（{a.username}）</td>
                  <td>{a.active ? '✓ 啟用' : '✗ 停用'}</td>
                  <td><label className="check"><input type="checkbox" checked={ids.includes(a.id)}
                    onChange={() => setIds(ids.includes(a.id) ? ids.filter((x) => x !== a.id) : [...ids, a.id])} />
                    {ids.includes(a.id) ? '☑ 已授權' : '☐ 未授權'}</label></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <Btn kind="primary" onClick={save} disabled={!cfg.data}>儲存授權</Btn>
      </section>
    </div>
  );
}

// ---------- 登入頁設定 ----------

export function LoginConfigForm(props: { onSaved?: () => void }): any {
  const cfg = useConfig();
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  useEffect(() => {
    if (cfg.data) {
      setU(cfg.data.login_placeholders.username || '');
      setP(cfg.data.login_placeholders.password || '');
    }
  }, [cfg.data]);
  const save = async () => {
    setMsg(null);
    try {
      await api.put('/api/admin/config', { login_placeholders: { username: u, password: p } });
      setMsg({ kind: 'ok', text: '已儲存；Client 下次開啟登入頁即套用' });
      props.onSaved?.();
    } catch (e) {
      setMsg({ kind: 'error', text: errText(e) });
    }
  };
  return (
    <div className="stack">
      <LoadState loading={cfg.loading} error={cfg.error} reload={cfg.reload} />
      <div className="form-grid">
        <Field label="帳號欄提示文字" hint={`${u.length}／40 字`}><input value={u} maxLength={40} onChange={(e: any) => setU(e.target.value)} /></Field>
        <Field label="密碼欄提示文字" hint={`${p.length}／40 字`}><input value={p} maxLength={40} onChange={(e: any) => setP(e.target.value)} /></Field>
      </div>
      <div className="login-preview" aria-label="預覽">
        <span className="dim">預覽（Client 登入頁）</span>
        <label className="field"><span>帳號</span><input placeholder={u} readOnly tabIndex={-1} /></label>
        <label className="field"><span>密碼</span><input type="password" placeholder={p} readOnly tabIndex={-1} /></label>
      </div>
      {msg ? <Msg kind={msg.kind}>{msg.text}</Msg> : null}
      <div><Btn kind="primary" onClick={save}>儲存</Btn></div>
    </div>
  );
}

export function LoginConfigPage(): any {
  return (
    <div className="page narrow">
      <PageHead title="登入頁設定" />
      <section className="panel">
        <p>Client App 登入頁的帳號、密碼欄空白時顯示的提示文字（各最多 40 字）。</p>
        <LoginConfigForm />
      </section>
    </div>
  );
}
