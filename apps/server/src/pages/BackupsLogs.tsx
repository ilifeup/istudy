/** 備份紀錄與 Log（第 10、18 章）。dev.62：方案 B 加密備份（age）、恢復密碼、InterServer 遠端（FTPS）、保留清理。 */
import React, { useState } from 'react';
import { api, errText, qs } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, Field, LoadState, Msg, PageHead, Tabs, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, size } from '../../../../packages/ui/src/format';
import { HostRunView, useHostRun } from '../srv';

const BK_STATUS: Record<string, string> = { running: '… 備份中', success: '✓ 成功', failed: '✗ 失敗' };
const BK_KIND: Record<string, string> = { daily: '每日', manual: '手動' };
const ENC: Record<string, string> = { unverified: '○ 尚未解密驗證', verified: '✓ 解密驗證通過', failed: '✗ 解密驗證失敗' };
const REMOTE: Record<string, string> = {
  none: '－ 未上傳', uploading: '… 上傳中', verified: '✓ 遠端已驗證', failed: '✗ 上傳失敗', deleted: '－ 遠端副本已依保留規則刪除',
};
const AGENT_TYPES = ['backup', 'backup_check'];
const HOST_TYPES = ['backup_verify', 'backup_upload', 'backup_prune', 'backup_remote_test'];
const HOST_LABEL: Record<string, string> = { backup_verify: '解密驗證', backup_upload: '遠端上傳', backup_prune: '保留清理', backup_remote_test: '遠端連線測試' };

/** 檢查結果：新格式 {integrity:{…}, restore_test:{…}, decrypt:{…}}；dev.53 以前的「備份並測試還原」是頂層 {passed,…}（視為還原測試）。 */
function checkOf(v: any, mode: 'integrity' | 'restore_test' | 'decrypt'): any {
  if (!v) return null;
  if (v[mode]) return v[mode];
  return mode === 'restore_test' && v.passed !== undefined ? v : null;
}

function CheckCell(props: { r: any; okText: string; none?: string }): any {
  const r = props.r;
  if (!r) return <span className="dim">{props.none || '未檢查'}</span>;
  return <>{r.passed ? `✓ ${props.okText}` : `✗ ${r.message || '未通過'}`}{r.checked_at ? <div className="dim">{dateTime(r.checked_at)}</div> : null}</>;
}

/** 加密備份的「解密驗證」欄：狀態＋檔案數＋時間（不只靠顏色）。 */
function DecryptCell(props: { b: any }): any {
  const d = checkOf(props.b.verify, 'decrypt');
  const i = checkOf(props.b.verify, 'integrity');
  return (
    <>
      {ENC[props.b.enc_status] || props.b.enc_status}
      {d ? <div className="dim">{d.passed ? `${d.members} 個檔案的 SHA-256 相符` : d.message}　{dateTime(d.checked_at)}</div> : null}
      {i ? <div className="dim">檔案完整性：{i.passed ? '✓ SHA-256 相符' : '✗ ' + i.message}</div> : null}
    </>
  );
}

function RemoteCell(props: { b: any }): any {
  const b = props.b;
  return (
    <>
      {REMOTE[b.remote_status] || b.remote_status}
      {b.remote_verified_at && b.remote_status === 'verified' ? <div className="dim">{dateTime(b.remote_verified_at)}</div> : null}
      {b.remote_status === 'failed' && b.remote_error ? <div className="err-text">{b.remote_error}</div> : null}
    </>
  );
}

/** Agent 與主機的備份工作（主機的解密驗證、上傳、清理寫在 jobs，只做紀錄）。 */
function useBackupJobs() {
  const job = useLoad(() => api.get<{ items: any[] }>('/api/admin/jobs' + qs({ type: [...AGENT_TYPES, ...HOST_TYPES].join(','), limit: 20 })), []);
  const active = job.data ? job.data.items.filter((j: any) => j.status === 'queued' || j.status === 'running') : [];
  return { job, active };
}

/**
 * 備份清單。dev.55 BK-01：建立、檢查、隔離還原測試分開；正式還原不在 App 內。
 * dev.62：加密備份的解密驗證、還原測試、上傳由主機執行（pkexec istudyctl）；舊的未加密備份沿用 Agent 的檢查。
 * actions=false 時只列出（首次設定精靈用）。
 */
export function BackupList(props: { limit?: number; actions?: boolean; onChanged?: () => void }): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<any>('/api/admin/backups'), []);
  const { job, active } = useBackupJobs();
  const h = useHostRun();
  const backupRunning = active.some((j: any) => j.type === 'backup');
  const hostRunning = active.filter((j: any) => HOST_TYPES.includes(j.type));
  const checking = (id: number) => active.find((j: any) => j.payload && j.payload.backup_id === id);
  useInterval(() => { r.reload(); job.reload(); }, active.length || h.running ? 3000 : 30000);
  const items = r.data ? r.data.items.slice(0, props.limit || 60) : [];
  const remoteOk = !!(r.data && r.data.remote && r.data.remote.configured);
  const check = async (b: any, mode: 'integrity' | 'restore_test') => {
    if (mode === 'restore_test' && !window.confirm('隔離還原測試：把這份備份還原到測試資料庫 istudy_restore_test 並比對筆數。\n不會動到正式資料庫、教材或系統。要開始嗎？')) return;
    try {
      const x = await api.post(`/api/admin/backups/${b.id}/check`, { mode });
      toast(x.existing ? '這份備份已在檢查中' : mode === 'integrity' ? '已開始檢查壓縮檔完整性' : '已開始隔離還原測試（只用測試資料庫）', 'ok');
      job.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const host = async (action: string, opts: unknown) => {
    try {
      await h.start(action, opts);
    } catch (e) {
      toast(errText(e), 'error');
    }
    r.reload();
    job.reload();
    props.onChanged?.();
  };
  const restoreTest = (b: any) => {
    if (!window.confirm(`解密＋隔離還原測試（備份 #${b.id}）：主機以私鑰解密，只把資料庫還原到測試資料庫 istudy_restore_test 比對筆數，結束後清空測試資料庫。\n不會動到正式資料庫、教材或系統。要開始嗎？`)) return;
    void host('backup-verify', { id: b.id, restoreTest: true });
  };
  const upload = (b: any) => {
    if (!window.confirm(`上傳備份 #${b.id} 到遠端：先解密驗證＋隔離還原測試，通過後以暫存名稱上傳、改名，再下載回來比對 SHA-256。要開始嗎？`)) return;
    void host('backup-upload', { id: b.id });
  };
  const busyHost = h.running || hostRunning.length > 0;
  return (
    <>
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {backupRunning ? <Msg kind="info">… 備份進行中</Msg> : null}
      {hostRunning.map((j: any) => <Msg key={j.id} kind="info">… 主機正在{HOST_LABEL[j.type] || j.type}{j.payload && j.payload.backup_id ? `（備份 #${j.payload.backup_id}）` : ''}</Msg>)}
      {r.data ? (r.data.days_since_success === null ? <Msg kind="warn">尚無成功的備份</Msg>
        : <Msg kind={r.data.days_since_success > 2 ? 'warn' : 'ok'}>上次成功備份：{dateTime(r.data.last_success_at)}（{r.data.days_since_success === 0 ? '今天' : r.data.days_since_success + ' 天前'}）</Msg>) : null}
      {h.run ? <HostRunView run={h.run} hideSteps /> : null}
      {items.length ? (
        <div className="table-scroll">
          <table className="grid small backups">
            <thead><tr><th>時間</th><th>種類</th><th>格式</th><th>狀態</th><th>檔案</th><th>大小</th><th>完整性／解密驗證</th><th>隔離還原測試</th><th>遠端</th>
              {props.actions !== false ? <th>操作</th> : null}<th>錯誤</th></tr></thead>
            <tbody>{items.map((b: any) => {
              const enc = b.format === 'age';
              const gone = !!b.deleted_at;
              const busy = checking(b.id);
              const ok = b.status === 'success' && !gone;
              return (
                <tr key={b.id} className={gone ? 'dim' : ''}>
                  <td className="nowrap">{dateTime(b.created_at)}<div className="dim">#{b.id}</div></td>
                  <td>{BK_KIND[b.kind] || b.kind}</td>
                  <td className="nowrap">{enc ? '加密（age）' : b.status === 'success' ? '⚠ 未加密（舊格式）' : '—'}</td>
                  <td>{gone ? <>－ 已清理<div className="dim">{b.delete_reason || ''}　{dateTime(b.deleted_at)}</div></> : (BK_STATUS[b.status] || b.status)}</td>
                  <td className="path">{b.file_path || '—'}{b.sha256 ? <div className="dim" title={b.sha256}>SHA-256 {b.sha256.slice(0, 16)}…</div> : null}</td>
                  <td className="nowrap">{b.size ? size(b.size) : '—'}</td>
                  <td>{b.status !== 'success' ? '—' : enc ? <DecryptCell b={b} /> : <CheckCell r={checkOf(b.verify, 'integrity')} okText="完整" />}</td>
                  <td><CheckCell r={checkOf(b.verify, 'restore_test')} okText="筆數相符" /></td>
                  <td>{!enc ? (b.status === 'success' ? <span className="dim">－ 不上傳（未加密）</span> : '—') : <RemoteCell b={b} />}</td>
                  {props.actions !== false ? (
                    <td className="actions">{!ok ? null : busy ? <span className="dim">… 處理中</span> : enc ? <>
                      <Btn small disabled={busyHost} onClick={() => host('backup-verify', { id: b.id })}>解密驗證</Btn>
                      <Btn small disabled={busyHost} onClick={() => restoreTest(b)}>解密＋還原測試</Btn>
                      <Btn small disabled={busyHost || !remoteOk} title={remoteOk ? '' : '請先設定遠端'} onClick={() => upload(b)}>
                        {b.remote_status === 'verified' ? '重新上傳' : '上傳'}</Btn>
                    </> : <>
                      <Btn small onClick={() => check(b, 'integrity')}>檢查完整性</Btn>
                      <Btn small onClick={() => check(b, 'restore_test')}>隔離還原測試</Btn>
                    </>}</td>
                  ) : null}
                  <td className="err-text">{b.error || ''}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

/** dev.62：方案 B 的範圍（UI 必須明確告知不能恢復的內容）。 */
export function PlanBNotice(): any {
  return (
    <section className="panel">
      <h2>方案 B：這份備份能恢復什麼</h2>
      <table className="grid kv small">
        <tbody>
          <tr><th className="nowrap">✓ 包含</th><td>資料庫（帳號、教材清單與狀態、群組、書籤、觀看紀錄、字幕內容、使用記錄等，含結構與序列）、
            字幕檔 /Media/subtitles/*.vtt、系統設定（.env、compose.yaml、VERSION.json、安裝識別碼）、Manifest（版本、Migration、筆數、每個檔案的 SHA-256）、還原說明 RESTORE.md。</td></tr>
          <tr><th className="nowrap">✗ 不包含</th><td><strong>不能恢復教材實體檔案（影片、音訊、文件）、唯一的播放影片（/Media/playback/*.mp4）及 Outgo 錄音與逐字稿。</strong>
            這些在 /Media（ZFS 鏡像）上，要靠 ZFS 鏡像與另外的檔案備份保護。也不含快取、字幕辨識模型、Client 安裝檔、UEM 報告（可重新產生或下載）。</td></tr>
          <tr><th className="nowrap">⚠ 注意</th><td>在「原檔刪除確認」刪掉原始影片後，/Media/playback 的 MP4 就是那支影片唯一的一份，方案 B 不會備份它。</td></tr>
        </tbody>
      </table>
    </section>
  );
}

/** dev.62：恢復密碼（BAK-03／BAK-04）。密碼只經過 Server App → pkexec → istudyctl 的標準輸入。 */
export function KeyPanel(props: { data: any; reload: () => void }): any {
  const { toast } = useApp();
  const h = useHostRun();
  const [mode, setMode] = useState<'' | 'init' | 'change' | 'verify'>('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const k = (props.data && props.data.key) || {};
  const submit = async () => {
    const a = pw;
    const b = pw2;
    if (mode !== 'verify') {
      if (a.length < 12) return toast('恢復密碼至少 12 字', 'error');
      if (a !== b) return toast('兩次輸入的恢復密碼不同', 'error');
    } else if (!a) {
      return toast('請輸入恢復密碼', 'error');
    }
    setPw('');
    setPw2('');
    try {
      const action = mode === 'init' ? 'backup-key-init' : mode === 'change' ? 'backup-key-change' : 'backup-key-verify';
      const r = await h.start(action, mode === 'verify' ? { password: a } : { password: a, password2: b });
      if (r && r.exit && r.exit.code === 0) setMode('');
    } catch (e) {
      toast(errText(e), 'error');
    }
    props.reload();
  };
  const label = mode === 'init' ? '設定恢復密碼' : mode === 'change' ? '修改恢復密碼' : '測試恢復密碼';
  return (
    <section className="panel">
      <h2>1. 加密與恢復密碼</h2>
      <table className="grid kv small">
        <tbody>
          <tr><th>狀態</th><td>{k.initialized ? '✓ 已設定：備份以 age 公鑰加密；Agent 只有公鑰，解密私鑰只在主機 root 可讀的位置' : '✗ 尚未設定：不會產生任何備份'}</td></tr>
          {k.initialized ? <tr><th>加密公鑰</th><td className="path">{k.recipient}</td></tr> : null}
          {k.created_at ? <tr><th>設定時間</th><td>{dateTime(k.created_at)}{k.changed_at ? `；最後修改密碼 ${dateTime(k.changed_at)}` : ''}</td></tr> : null}
        </tbody>
      </table>
      <Msg kind="warn">私鑰與恢復密碼都遺失時，所有加密備份都無法解密，任何人（包括開發者）都救不回。
        恢復密碼與 Super 登入密碼分開，請不要用同一組，並另外妥善保管（例如寫下來放在保險箱）。</Msg>
      <div className="row">
        {!k.initialized ? <Btn kind="primary" onClick={() => setMode('init')} disabled={h.running}>設定恢復密碼</Btn> : <>
          <Btn onClick={() => setMode('change')} disabled={h.running}>修改恢復密碼</Btn>
          <Btn onClick={() => setMode('verify')} disabled={h.running}>測試恢復密碼</Btn>
        </>}
      </div>
      {mode ? (
        <div className="stack bk-form">
          <h3>{label}</h3>
          {mode === 'change' ? <p className="dim">只換密碼；加密金鑰不變，舊備份照樣可以還原。</p> : null}
          {mode === 'verify' ? <p className="dim">確認這組密碼能解開恢復金鑰（重灌或災後重建時要用它）。</p> : null}
          <div className="form-grid">
            <Field label={mode === 'verify' ? '恢復密碼' : '新的恢復密碼'} hint={mode === 'verify' ? '' : '至少 12 字；不要和 Super 登入密碼相同'}>
              <input type="password" value={pw} autoComplete="new-password" onChange={(e: any) => setPw(e.target.value)} />
            </Field>
            {mode !== 'verify' ? <Field label="再輸入一次"><input type="password" value={pw2} autoComplete="new-password" onChange={(e: any) => setPw2(e.target.value)} /></Field> : null}
          </div>
          <div className="row">
            <Btn kind="primary" onClick={submit} disabled={h.running}>{label}</Btn>
            <Btn kind="quiet" onClick={() => { setMode(''); setPw(''); setPw2(''); }}>取消</Btn>
          </div>
          <p className="dim">按下後會跳出 Ubuntu 系統密碼視窗（主機管理權限）。恢復密碼只經標準輸入交給主機程式，不會寫入資料庫、Log 或任何明文檔案。</p>
        </div>
      ) : null}
      {h.run ? <HostRunView run={h.run} hideSteps /> : null}
    </section>
  );
}

/** dev.62：InterServer 遠端備份（FTPS，明示 TLS）。主機、連接埠、帳號依 InterServer 控制台輸入，系統不猜測。 */
export function RemotePanel(props: { data: any; reload: () => void }): any {
  const { toast } = useApp();
  const h = useHostRun();
  const rm = (props.data && props.data.remote) || {};
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ host: '', port: '', username: '', password: '', dir: '/istudy-backup' });
  const open = () => {
    setF({ host: rm.host || '', port: rm.port ? String(rm.port) : '', username: rm.username || '', password: '', dir: rm.dir || '/istudy-backup' });
    setEdit(true);
  };
  const run = async (action: string, opts?: unknown) => {
    try {
      const r = await h.start(action, opts);
      if (action === 'backup-remote-set' && r && r.exit && r.exit.code === 0) setEdit(false);
    } catch (e) {
      toast(errText(e), 'error');
    }
    setF((x) => ({ ...x, password: '' }));
    props.reload();
  };
  const save = () => {
    if (!f.host.trim() || !f.port.trim() || !f.username.trim() || !f.dir.trim()) return toast('主機、連接埠、帳號、遠端資料夾都要填', 'error');
    if (!rm.configured && !f.password) return toast('請填 FTP 密碼', 'error');
    void run('backup-remote-set', { host: f.host.trim(), port: Number(f.port), username: f.username.trim(), password: f.password, dir: f.dir.trim() });
  };
  const set = (k: string, v: string) => setF({ ...f, [k]: v });
  const lt = rm.last_test;
  const lu = rm.last_upload;
  const every = props.data && props.data.settings ? props.data.settings.upload_every_days : 3;
  return (
    <section className="panel">
      <h2>2. 遠端備份（InterServer）</h2>
      <table className="grid kv small">
        <tbody>
          <tr><th>狀態</th><td>{rm.configured ? `✓ 已設定：FTPS（明示 TLS）${rm.host}:${rm.port}　帳號 ${rm.username}　資料夾 ${rm.dir}` : '⚠ 尚未設定'}</td></tr>
          <tr><th>排程</th><td>每 {every} 天上傳一份「解密驗證＋隔離還原測試都通過」的備份；以暫存名稱上傳後改名，再下載回來比對 SHA-256 才標示「遠端已驗證」。
            上傳失敗時本機備份完全保留，可按「立即上傳最新備份」重試。</td></tr>
          <tr><th>連線測試</th><td>{lt ? <>{lt.message}<div className="dim">{dateTime(lt.at)}</div></> : <span className="dim">未測試</span>}</td></tr>
          <tr><th>最近上傳</th><td>{lu ? <>{lu.ok ? `✓ 備份 #${lu.backup_id} 上傳並驗證成功` : `✗ 備份 #${lu.backup_id} 失敗：${lu.message}`}<div className="dim">{dateTime(lu.at)}</div></> : <span className="dim">尚未上傳</span>}</td></tr>
          <tr><th>最新遠端副本</th><td>{rm.last_ok_at ? dateTime(rm.last_ok_at) : <span className="dim">沒有</span>}</td></tr>
        </tbody>
      </table>
      <div className="row">
        <Btn onClick={open} disabled={h.running}>{rm.configured ? '修改遠端設定' : '設定遠端'}</Btn>
        <Btn onClick={() => run('backup-remote-test')} disabled={h.running || !rm.configured}>測試遠端連線</Btn>
        <Btn onClick={() => run('backup-upload')} disabled={h.running || !rm.configured}>立即上傳最新備份</Btn>
      </div>
      {edit ? (
        <div className="stack bk-form">
          <p className="dim">只支援 FTPS（明示 TLS，AUTH TLS），伺服器憑證一定驗證；不支援也不會改用明文 FTP。
            主機、連接埠、帳號請依 InterServer 控制台（cPanel → FTP Accounts）顯示的資料填寫。遠端資料夾必須在網站公開目錄（public_html、www）之外。</p>
          <div className="form-grid">
            <Field label="FTPS 主機"><input value={f.host} onChange={(e: any) => set('host', e.target.value)} placeholder="InterServer 控制台顯示的 FTP 主機" /></Field>
            <Field label="連接埠"><input value={f.port} inputMode="numeric" onChange={(e: any) => set('port', e.target.value.replace(/\D/g, ''))} placeholder="控制台顯示的 FTPS 連接埠" /></Field>
            <Field label="FTP 帳號"><input value={f.username} autoComplete="off" onChange={(e: any) => set('username', e.target.value)} /></Field>
            <Field label="FTP 密碼" hint={rm.configured ? '留空＝沿用原密碼（換主機或帳號時一定要重新輸入）' : ''}>
              <input type="password" value={f.password} autoComplete="new-password" onChange={(e: any) => set('password', e.target.value)} /></Field>
            <Field label="遠端資料夾" hint="例如 /istudy-backup；不能在 public_html 裡"><input value={f.dir} onChange={(e: any) => set('dir', e.target.value)} /></Field>
          </div>
          <div className="row">
            <Btn kind="primary" onClick={save} disabled={h.running}>儲存遠端設定</Btn>
            <Btn kind="quiet" onClick={() => setEdit(false)}>取消</Btn>
          </div>
          <p className="dim">FTP 密碼只存在主機 root 可讀的 /etc/istudy/backup/remote.json，不寫入資料庫或 Log。</p>
        </div>
      ) : null}
      {h.run ? <HostRunView run={h.run} hideSteps /> : null}
    </section>
  );
}

/** dev.62：保留 10～15 天；最後一份可用備份永遠保留；舊的未加密備份不處理（BAK-07／BAK-08）。 */
export function RetentionPanel(props: { data: any; reload: () => void }): any {
  const { toast } = useApp();
  const h = useHostRun();
  const st = (props.data && props.data.settings) || { keep_days: 15, keep_min: 10, keep_max: 15, backup_time: '03:30' };
  const [days, setDays] = useState<number | null>(null);
  const cur = days ?? st.keep_days;
  const save = async () => {
    try {
      await api.put('/api/admin/backups/settings', { keep_days: cur });
      toast(`已設定保留 ${cur} 天`, 'ok');
      setDays(null);
      props.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const prune = async () => {
    if (!window.confirm(`依目前規則清理：刪除超過 ${st.keep_days} 天的加密備份（最後一份可用備份一定保留；舊的未加密備份不處理）。要開始嗎？`)) return;
    try {
      await h.start('backup-prune');
    } catch (e) {
      toast(errText(e), 'error');
    }
    props.reload();
  };
  const opts: number[] = [];
  for (let d = st.keep_min; d <= st.keep_max; d++) opts.push(d);
  const host = props.data && props.data.host;
  return (
    <section className="panel">
      <h2>3. 保留與清理</h2>
      <div className="form-row">
        <Field label="保留天數"><select value={cur} onChange={(e: any) => setDays(Number(e.target.value))}>
          {opts.map((d) => <option key={d} value={d}>{d} 天</option>)}</select></Field>
        <Btn kind="primary" onClick={save} disabled={cur === st.keep_days}>儲存</Btn>
        <Btn onClick={prune} disabled={h.running}>立即清理</Btn>
      </div>
      <ul className="plain small">
        <li>每天 {st.backup_time} 建立加密備份（時間在「排程設定」改）。主機每小時檢查：新備份做解密驗證；到期時上傳；每天清理一次。</li>
        <li>只刪除超過保留天數的加密備份；<strong>最後一份可用（已通過解密驗證）的備份永遠保留</strong>，不論多舊、有沒有遠端副本。</li>
        <li>遠端：最新一份已驗證的遠端副本永遠保留，其餘超過保留天數才刪。</li>
        <li>磁碟空間不足時停止建立新備份並記錄錯誤，不會為了騰出空間刪除任何備份。</li>
        <li>dev.61 以前的未加密備份（.tar.gz）與部署時的 istudy-db-*.sql 不會自動刪除或上傳。每次清理都記在「Log → 主機維護」。</li>
      </ul>
      {host && host.last_prune ? <p className="dim">上次清理：{dateTime(host.last_prune.at)}　{host.last_prune.message}</p> : null}
      {host && host.last_run ? <p className="dim">主機排程上次執行：{dateTime(host.last_run.at)}　{host.last_run.ok ? '✓' : '⚠'} {host.last_run.message}</p> : null}
      {h.run ? <HostRunView run={h.run} hideSteps /> : null}
    </section>
  );
}

export function BackupsPage(): any {
  const { go, toast } = useApp();
  const [key, setKey] = useState(0);
  const d = useLoad(() => api.get<any>('/api/admin/backups'), [key]);
  const reload = () => setKey((k: number) => k + 1);
  const run = async () => {
    try {
      const r = await api.post('/api/admin/backups', { verify: false });
      toast(r.existing ? '已有備份在等待或執行中' : '已開始建立備份', 'ok');
      setTimeout(reload, 1500);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const data = d.data;
  return (
    <div className="page">
      <PageHead title="備份">
        <Btn kind="primary" onClick={run} disabled={!!data && !data.key.initialized} title={data && !data.key.initialized ? '請先設定恢復密碼' : ''}>建立備份</Btn>
      </PageHead>
      <p className="dim">加密備份（age）儲存於 SSD /srv/istudy/backup，每份附 .sha256；本機只保存加密版本。
        <Btn small kind="quiet" onClick={() => go('srv-host')}>主機維護</Btn></p>
      <LoadState loading={d.loading && !data} error={d.error} reload={d.reload} />
      {data ? data.warnings.map((w: any, i: number) => <Msg key={i} kind={w.level}>{w.text.replace(/^[✗⚠ⓘ]\s*/, '')}</Msg>) : null}
      <PlanBNotice />
      {data ? <KeyPanel data={data} reload={reload} /> : null}
      {data ? <RemotePanel data={data} reload={reload} /> : null}
      {data ? <RetentionPanel data={data} reload={reload} /> : null}
      <section className="panel">
        <h2>備份清單</h2>
        <BackupList key={key} onChanged={reload} />
      </section>
      <section className="panel">
        <h2>檢查與還原的差別</h2>
        <table className="grid kv small">
          <tbody>
            <tr><th>解密驗證（加密備份）</th><td>主機以私鑰完整解密，核對 Manifest 中每個檔案的 SHA-256；不寫入任何資料庫。新備份由主機排程自動驗證。</td></tr>
            <tr><th>解密＋還原測試</th><td>解密後只把資料庫還原到測試資料庫 istudy_restore_test 比對筆數，結束後清空；不動正式資料庫、教材與系統。</td></tr>
            <tr><th>檢查完整性（舊格式）</th><td>只讀未加密的壓縮檔，確認沒有損壞、db.sql 與 manifest 都在。通過不等於可以還原。</td></tr>
            <tr><th>隔離還原測試（舊格式）</th><td>還原到測試資料庫 istudy_restore_test 並比對筆數；不動正式資料庫、教材與系統。</td></tr>
            <tr><th>正式還原</th><td>App 內沒有這個按鈕。需要時在 Server 終端機執行
              <code>sudo istudyctl restore --file 備份檔 --target prod</code>：會先還原到測試資料庫檢查，再要求輸入「覆蓋」才會動正式資料庫。
              主機沒有私鑰時（重灌）先用恢復密碼匯入：<code>sudo istudyctl backup-key --import --file istudy-recovery-key.age</code>。</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}

const LEVEL: Record<string, string> = { info: 'ⓘ 資訊', warn: '⚠ 警告', error: '✗ 錯誤' };

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function LogsPage(): any {
  const { toast } = useApp();
  const [source, setSource] = useState('server');
  const [f, setF] = useState({ from: '', to: '', level: '' });
  const [applied, setApplied] = useState(f);
  const r = useLoad(() => api.get<{ items: any[] }>('/api/admin/logs' + qs({ source, from: applied.from, to: applied.to, limit: 1000 })), [source, JSON.stringify(applied)]);
  const items = r.data ? r.data.items.filter((x) => !applied.level || x.level === applied.level) : [];
  const exportCsv = async () => {
    const head = ['時間', '來源', '等級', '訊息', source === 'client' ? '電腦' : '內容'];
    const rows = items.map((x) => [x.created_at, x.source, x.level, x.message, source === 'client' ? `${x.device_name || x.pc_name || ''} ${x.app || ''}` : JSON.stringify(x.context || {})]);
    const text = '﻿' + [head, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
    const s = await bridge.saveFile(`iStudy-log-${source}-${new Date().toISOString().slice(0, 10)}.csv`, text);
    if (s.ok) toast('已匯出：' + s.path, 'ok');
  };
  return (
    <div className="page">
      <PageHead title="Log"><Btn onClick={exportCsv} disabled={!items.length}>匯出 CSV</Btn></PageHead>
      <Tabs value={source} onChange={setSource} tabs={[
        { id: 'server', label: 'Server 錯誤記錄' }, { id: 'agent', label: 'Agent 工作記錄' }, { id: 'host', label: '主機維護' }, { id: 'client', label: 'Client 上傳的 Log' },
      ]} />
      <section className="panel">
        <div className="form-row">
          <Field label="從"><input type="date" value={f.from} onChange={(e: any) => setF({ ...f, from: e.target.value })} /></Field>
          <Field label="到"><input type="date" value={f.to} onChange={(e: any) => setF({ ...f, to: e.target.value })} /></Field>
          <Field label="等級">
            <select value={f.level} onChange={(e: any) => setF({ ...f, level: e.target.value })}>
              <option value="">全部</option><option value="error">錯誤</option><option value="warn">警告</option><option value="info">資訊</option>
            </select>
          </Field>
          <Btn kind="primary" onClick={() => setApplied({ ...f })}>篩選</Btn>
          <Btn onClick={r.reload}>重新整理</Btn>
        </div>
      </section>
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data && !items.length ? <Msg kind="info">沒有記錄</Msg> : null}
      {items.length ? (
        <table className="grid small log">
          <thead><tr><th>時間</th><th>等級</th><th>訊息</th><th>{source === 'client' ? '電腦／App' : '內容'}</th></tr></thead>
          <tbody>{items.map((x) => (
            <tr key={x.id} className={'lv-' + x.level}>
              <td className="nowrap">{dateTime(x.created_at, true)}{x.logged_at ? <div className="dim">發生 {dateTime(x.logged_at, true)}</div> : null}</td>
              <td className="nowrap">{LEVEL[x.level] || x.level}</td>
              <td className="msgcell">{x.message}</td>
              <td className="small">{source === 'client' ? `${x.device_name || x.pc_name || ''} ${x.app || ''}` : (x.context && Object.keys(x.context).length ? JSON.stringify(x.context) : '')}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
    </div>
  );
}
