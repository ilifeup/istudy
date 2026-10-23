/**
 * 首次設定精靈（第 17 章 ③）：部署完成後第一次以 Super 登入時自動開啟。每一步的進度存在 server_config，
 * 可中途離開、下次繼續；完成後可在「主機維護」重新開啟查看結果。
 */
import React, { useEffect, useState } from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, Field, LoadState, Msg, PageHead, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, size } from '../../../../packages/ui/src/format';
import { deviceStateText } from '../../../../packages/ui/src/components/Login';
import { CheckList, ENCODER_TEXT, setSetupCache } from '../srv';
import { ScanSummary } from './Folders';
import { LoginConfigForm, ScheduleForm } from './Settings';
import { VerifyResult } from './Conversions';
import { EngineSelect } from './Queues';
import { AsrLengthLine } from './AsrLimits';
import { BackupList, KeyPanel } from './BackupsLogs';

const STEPS: [number, string, boolean][] = [
  [1, '環境確認', false], [2, '基本設定', false], [3, '教材盤點', false], [4, '試轉檔', true], [5, '文件與字幕', true],
  [6, '備份', true], [7, 'Client 電腦', true], [8, '帳號與群組', true], [9, '完成', false],
];

interface Setup { completed: boolean; step: number; steps: Record<string, { status: string; note?: string; result?: any; at?: string }>; completed_at?: string }

export function SetupWizardPage(): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<{ setup: Setup }>('/api/admin/setup'), []);
  const [step, setStep] = useState<number | null>(null);
  const setup = r.data ? r.data.setup : null;
  useEffect(() => { if (setup && step === null) setStep(setup.completed ? 9 : setup.step || 1); }, [setup]);
  const save = async (n: number, status: 'done' | 'skipped', result?: any, next = true) => {
    try {
      const x = await api.put('/api/admin/setup', { step: next ? Math.min(9, n + 1) : n, steps: { [n]: { status, result, at: new Date().toISOString() } } });
      r.setData({ setup: x.setup });
      setSetupCache({ completed: x.setup.completed, step: x.setup.step });
      if (next) setStep(Math.min(9, n + 1));
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  if (!setup || step === null) return <div className="page"><LoadState loading={r.loading} error={r.error} reload={r.reload} /></div>;
  const st = (n: number) => (setup.steps[String(n)] || {}).status;
  const props = { onDone: (result?: any) => save(step, 'done', result), onSkip: () => save(step, 'skipped'), status: st(step) };
  return (
    <div className="page wizard">
      <PageHead title="首次設定精靈" />
      {setup.completed ? <Msg kind="ok">首次設定已於 {dateTime(setup.completed_at)} 完成；可以回頭查看或重做任一步驟。</Msg>
        : <p className="dim">每一步的進度都會儲存，可以中途離開，下次登入再繼續。</p>}
      <div className="wizard-body">
        <ol className="wizard-steps">
          {STEPS.map(([n, name]) => (
            <li key={n}>
              <button type="button" className={'wstep' + (n === step ? ' on' : '')} aria-current={n === step ? 'step' : undefined} onClick={() => setStep(n)}>
                <span className="mark">{st(n) === 'done' ? '✓' : st(n) === 'skipped' ? '－' : n === step ? '▶' : '○'}</span> {n}. {name}
                {st(n) === 'skipped' ? <span className="dim">（略過）</span> : null}
              </button>
            </li>
          ))}
        </ol>
        <section className="panel wizard-main">
          <h2>{step}. {STEPS[step - 1][1]}</h2>
          {step === 1 ? <StepEnv {...props} /> : null}
          {step === 2 ? <StepBasic {...props} /> : null}
          {step === 3 ? <StepInventory {...props} /> : null}
          {step === 4 ? <StepTrialConvert {...props} /> : null}
          {step === 5 ? <StepDocsSubs {...props} /> : null}
          {step === 6 ? <StepBackup {...props} /> : null}
          {step === 7 ? <StepClients {...props} /> : null}
          {step === 8 ? <StepAccounts {...props} /> : null}
          {step === 9 ? <StepFinish setup={setup} onFinish={async () => {
            const x = await api.put('/api/admin/setup', { completed: true, step: 9, steps: { 9: { status: 'done', at: new Date().toISOString() } } });
            r.setData({ setup: x.setup });
            setSetupCache({ completed: true, step: 9 });
            toast('首次設定完成', 'ok');
          }} /> : null}
          {STEPS[step - 1][2] && step !== 9 ? <div className="row wizard-skip"><Btn kind="quiet" onClick={props.onSkip}>略過這一步（之後再做）</Btn></div> : null}
        </section>
      </div>
    </div>
  );
}

interface StepProps { onDone: (result?: any) => Promise<void> | void; onSkip: () => void; status?: string }

function DoneBtn(props: { onClick: () => any; disabled?: boolean; children?: any }): any {
  return <Btn kind="primary" onClick={props.onClick} disabled={props.disabled}>{props.children || '確認，下一步'}</Btn>;
}

// 1. 環境確認：部署時的自我檢查結果＋重跑一次 diagnose
function StepEnv(p: StepProps): any {
  const host = useLoad(() => bridge.host.state(), []);
  const diag = useLoad(() => api.get<{ items: any[] }>('/api/admin/diagnostics?kind=server'), []);
  const [asked, setAsked] = useState<string | null>(null);
  const run = async () => {
    const t = new Date().toISOString();
    await api.post('/api/admin/diagnostics', {});
    setAsked(t);
  };
  useEffect(() => { void run(); }, []);
  const latest = diag.data && diag.data.items[0];
  const fresh = !!latest && !!asked && new Date(latest.created_at).getTime() >= new Date(asked).getTime() - 2000;
  useInterval(() => diag.reload(), asked && !fresh ? 3000 : null);
  const bs = host.data && host.data.bootstrap ? host.data.bootstrap.steps || {} : {};
  return (
    <div className="stack">
      <h3>部署時的自我檢查（步驟 ② 第 13 步）</h3>
      {bs['13'] ? <Msg kind={bs['13'].status === 'ok' ? 'ok' : 'warn'}>{bs['13'].message}（{bs['13'].at}）</Msg> : <Msg kind="info">找不到部署紀錄（可在終端機執行 sudo istudyctl doctor）</Msg>}
      <h3>Server 診斷</h3>
      {!fresh ? <p className="dim">… 正在重跑一次 Server 診斷</p> : null}
      {latest ? <><p className="dim">檢查時間：{dateTime(latest.created_at)}</p><CheckList items={latest.data.checks || []} /></> : null}
      <div className="row"><Btn onClick={run}>重跑診斷</Btn><DoneBtn onClick={() => p.onDone({ diagnose: latest ? latest.data.status : null })} /></div>
    </div>
  );
}

// 2. 基本設定：登入頁提示文字、系統工作排程、每日備份時間
function StepBasic(p: StepProps): any {
  return (
    <div className="stack">
      <h3>登入頁提示文字</h3>
      <LoginConfigForm />
      <h3>系統工作排程與每日備份</h3>
      <ScheduleForm compact />
      <div className="row"><DoneBtn onClick={() => p.onDone()} /></div>
    </div>
  );
}

function useLatestRun(dry: boolean) {
  const runs = useLoad(() => api.get<{ runs: any[] }>('/api/admin/scan-runs'), []);
  const latest = runs.data ? runs.data.runs.find((r) => r.dry_run === dry) : null;
  return { runs, latest };
}

// 3. 教材盤點：試掃描（不寫入）→ 確認 → 正式掃描
function StepInventory(p: StepProps): any {
  const { toast } = useApp();
  const dry = useLatestRun(true);
  const real = useLatestRun(false);
  const [waiting, setWaiting] = useState<'dry' | 'real' | null>(null);
  const busy = waiting !== null || (dry.latest && dry.latest.status === 'running') || (real.latest && real.latest.status === 'running');
  useInterval(() => { dry.runs.reload(); real.runs.reload(); }, busy ? 2000 : null);
  useEffect(() => {
    if (waiting === 'dry' && dry.latest && dry.latest.status !== 'running' && dry.latest.finished_at) setWaiting(null);
    if (waiting === 'real' && real.latest && real.latest.status !== 'running' && real.latest.finished_at) setWaiting(null);
  }, [dry.latest, real.latest]);
  const start = async (isDry: boolean) => {
    try {
      await api.post('/api/admin/scan', { dry_run: isDry });
      setWaiting(isDry ? 'dry' : 'real');
      setTimeout(() => { dry.runs.reload(); real.runs.reload(); }, 800);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="stack">
      <p>先做試掃描：只統計 /Media 的檔案數（系統資料夾不算）、容量、副檔名與需要轉檔的數量，不寫入教材資料。確認後再執行正式掃描。</p>
      <div className="row"><Btn onClick={() => start(true)} disabled={!!busy}>試掃描</Btn>{waiting === 'dry' ? <span className="dim">… 試掃描中</span> : null}</div>
      {dry.latest && dry.latest.status === 'done' ? <><h3>試掃描結果（{dateTime(dry.latest.finished_at)}）</h3><ScanSummary s={dry.latest.summary} /></> : null}
      {dry.latest && dry.latest.status === 'failed' ? <Msg kind="error">{dry.latest.error}</Msg> : null}
      {dry.latest && dry.latest.status === 'done' && dry.latest.summary && dry.latest.summary.files === 0 ? <Msg kind="warn">/Media 裡沒有教材檔案；可以先把教材複製進去再試掃描</Msg> : null}
      <div className="row">
        <Btn kind="primary" onClick={() => start(false)} disabled={!!busy || !dry.latest || dry.latest.status !== 'done'}>確認，執行正式掃描</Btn>
        {waiting === 'real' ? <span className="dim">… 正式掃描中</span> : null}
      </div>
      {real.latest && real.latest.status === 'done' ? <Msg kind="ok">正式掃描完成（{dateTime(real.latest.finished_at)}）：新增 {real.latest.added}、變更 {real.latest.changed}、移動 {real.latest.moved}、遺失 {real.latest.missing}</Msg> : null}
      {real.latest && real.latest.status === 'failed' ? <Msg kind="error">{real.latest.error}</Msg> : null}
      <div className="row"><DoneBtn disabled={!real.latest || real.latest.status !== 'done'}
        onClick={() => p.onDone({ dry: dry.latest && dry.latest.summary, added: real.latest && real.latest.added })} /></div>
    </div>
  );
}

// 4. 試轉檔：硬體編碼三層測試結果；選一支 .MTS 試轉；通過四項檢查後播放抽查；確認後開放批次轉檔
function StepTrialConvert(p: StepProps): any {
  const { go, toast } = useApp();
  const ov = useLoad(() => api.get<any>('/api/admin/overview'), []);
  const conv = useLoad(() => api.get<any>('/api/admin/conversions'), []);
  const [pick, setPick] = useState<number | null>(null);
  const items: any[] = conv.data ? conv.data.items : [];
  const candidates = items.filter((m) => ['queued', 'failed', 'verify_failed'].includes(m.convert_status));
  const mts = candidates.filter((m) => /\.(mts|m2ts)$/i.test(m.rel_path));
  const current = pick ? items.find((m) => m.id === pick) : null;
  const active = current && ['queued', 'converting', 'verifying', 'probing'].includes(current.convert_status);
  useInterval(() => conv.reload(), active ? 4000 : null);
  const run = async (id: number) => {
    try {
      await api.post(`/api/admin/conversions/${id}/run-now`, {});
      setPick(id);
      conv.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const open = async () => {
    try {
      await api.post('/api/admin/conversions/pause', { paused: false });
      await p.onDone({ media_id: pick, encoder: current && current.encoder });
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const diag = ov.data && ov.data.diagnostics ? ov.data.diagnostics.data : null;
  const passed = current && current.convert_status === 'done' && current.verify && current.verify.passed;
  return (
    <div className="stack">
      <h3>硬體編碼測試（Quick Sync → VA-API → CPU）</h3>
      {diag ? (
        <>
          {diag.encoder === 'cpu' ? <Msg kind="warn">Quick Sync 與 VA-API 都無法使用，轉檔降級為 CPU（較慢）</Msg>
            : <Msg kind="ok">轉檔將使用：{ENCODER_TEXT[diag.encoder] || diag.encoder}</Msg>}
          <CheckList items={(diag.checks || []).filter((c: any) => String(c.id || '').startsWith('encoder_'))} compact />
        </>
      ) : <Msg kind="info">等待診斷結果</Msg>}
      {ov.data && ov.data.conversion_paused ? <Msg kind="info">批次轉檔目前暫停，完成這一步確認後才開放</Msg> : null}
      <h3>選一支影片試轉</h3>
      <LoadState loading={conv.loading && !conv.data} error={conv.error} reload={conv.reload} />
      {conv.data && !candidates.length && !current ? <Msg kind="info">沒有需要轉檔的影片（全部可直接播放，或尚未掃描）</Msg> : null}
      {!current && candidates.length ? (
        <table className="grid small">
          <thead><tr><th>影片</th><th>大小</th><th></th></tr></thead>
          <tbody>{(mts.length ? mts : candidates).slice(0, 15).map((m) => (
            <tr key={m.id}><td>{m.title}<div className="dim path">{m.rel_path}</div></td><td>{size(m.size)}</td>
              <td><Btn small kind="primary" onClick={() => run(m.id)}>試轉這一支</Btn></td></tr>
          ))}</tbody>
        </table>
      ) : null}
      {current ? (
        <div className="stack">
          <p><strong>{current.title}</strong>　{current.convert_status === 'done' ? '✓ 轉檔完成' : current.convert_status.includes('failed') ? '✗ 失敗' : '… 處理中'}
            {current.job && current.job.percent !== null && current.job.percent !== undefined && active ? ` ${Math.round(Number(current.job.percent))}%` : ''}</p>
          {current.convert_error ? <Msg kind="error">{current.convert_error}</Msg> : null}
          {current.verify ? <VerifyResult v={current.verify} full /> : null}
          <div className="row">
            {passed ? <Btn onClick={() => go('player', { id: current.id })}>播放抽查</Btn> : null}
            <Btn kind="quiet" onClick={() => setPick(null)}>換一支</Btn>
          </div>
        </div>
      ) : null}
      <div className="row">
        <DoneBtn onClick={open} disabled={!passed}>確認，開放批次轉檔</DoneBtn>
        {!passed && conv.data && !candidates.length ? <Btn onClick={open}>沒有需要轉檔的影片，開放批次轉檔</Btn> : null}
      </div>
    </div>
  );
}

// 5. 文件與字幕：Office 文件轉換狀態（每次進入都讀真實狀態）、掃描新增教材；選擇字幕辨識預設引擎
const DOC_STATE: Record<string, string> = {
  done: '✓ 已轉 PDF', failed: '✗ 轉換失敗', pending: '… 等待轉換', converting: '… 轉換中', none: '○ 尚未轉換',
};

function StepDocsSubs(p: StepProps): any {
  const { go, toast } = useApp();
  const docs = useLoad(() => api.get<{ items: any[] }>('/api/admin/documents'), []);
  const cfg = useLoad(() => api.get<any>('/api/admin/config'), []);
  const real = useLatestRun(false);
  const subs = useLoad(() => api.get<any>('/api/admin/subtitles'), []);
  const [engine, setEngine] = useState<string>('auto');
  const [scanJob, setScanJob] = useState<number | null>(null);
  const [scanDone, setScanDone] = useState<any | null>(null);
  useEffect(() => { if (cfg.data && cfg.data.asr) setEngine(cfg.data.asr.engine || 'auto'); }, [cfg.data]);
  const items = docs.data ? docs.data.items : [];
  const busy = items.some((d) => ['pending', 'converting'].includes(d.doc_status));
  useInterval(() => docs.reload(), busy ? 3000 : null);
  // dev.55 DOC-03：掃描新增教材＝重用既有的正式掃描工作；以工作 ID 追蹤這一次掃描是否完成
  useInterval(() => real.runs.reload(), scanJob ? 2000 : null);
  const myRun = scanJob && real.runs.data ? real.runs.data.runs.find((r: any) => r.job_id === scanJob) : null;
  useEffect(() => {
    if (myRun && myRun.status !== 'running') {
      setScanJob(null);
      setScanDone(myRun);
      docs.reload();
    }
  }, [myRun ? myRun.status : null]);
  const rescan = async () => {
    try {
      const r = await api.post('/api/admin/scan', { dry_run: false });
      setScanDone(null);
      setScanJob(r.job_id);
      toast(r.existing ? '已有掃描在等待或執行中，完成後會自動刷新清單' : '已開始掃描 /Media', 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  // dev.55 DOC-01：「試轉」只對尚未轉換的文件；已轉好的要另外按「重新試轉」並確認，不會因畫面狀態誤觸重跑
  const convert = async (d: any) => {
    if (d.doc_status === 'done' && !window.confirm(`重新轉換「${d.title}」？已轉好的 PDF 會被新的結果取代。`)) return;
    try {
      await api.post(`/api/admin/documents/${d.id}/retry`, {});
      docs.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const finish = async () => {
    try {
      await api.put('/api/admin/config', { asr: { engine } });
      const done = items.filter((d) => d.doc_status === 'done').length;
      await p.onDone({ asr_engine: engine, docs_done: done });
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <div className="stack">
      <h3>Office 文件轉 PDF</h3>
      <div className="row">
        <Btn onClick={rescan} disabled={scanJob !== null}>掃描新增教材</Btn>
        {scanJob !== null ? <span className="dim">… 掃描中（{myRun ? '執行中' : '等待開始'}）</span> : null}
        <span className="dim">剛放進 /Media 的文件要先掃描才會出現</span>
      </div>
      {scanDone && scanDone.status === 'done' ? <Msg kind="ok">掃描完成（{dateTime(scanDone.finished_at)}）：新增 {scanDone.added}、變更 {scanDone.changed}、遺失 {scanDone.missing}</Msg> : null}
      {scanDone && scanDone.status === 'failed' ? <Msg kind="error">掃描失敗：{scanDone.error}</Msg> : null}
      <LoadState loading={docs.loading && !docs.data} error={docs.error} reload={docs.reload} />
      {docs.data && !items.length ? <Msg kind="info">目前沒有 Word／Excel／PowerPoint 文件；放進 /Media 的課程資料夾後按「掃描新增教材」</Msg> : null}
      {items.length ? (
        <table className="grid small">
          <thead><tr><th>文件</th><th>狀態</th><th></th></tr></thead>
          <tbody>{items.slice(0, 30).map((d) => (
            <tr key={d.id}>
              <td>{d.title}<div className="dim path">{d.rel_path}（{size(d.size)}）</div></td>
              <td>{DOC_STATE[d.doc_status] || d.doc_status}{d.doc_error ? <div className="err-text">{d.doc_error}</div> : null}</td>
              <td className="actions">
                {d.doc_status === 'done' ? <><Btn small onClick={() => go('doc', { id: d.id })}>開啟</Btn><Btn small kind="quiet" onClick={() => convert(d)}>重新試轉</Btn></> : null}
                {d.doc_status === 'failed' ? <Btn small onClick={() => convert(d)}>重新試轉</Btn> : null}
                {d.doc_status === 'none' ? <Btn small kind="primary" onClick={() => convert(d)}>試轉</Btn> : null}
              </td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
      {items.length > 30 ? <p className="dim">只列出前 30 份；全部文件在「文件轉換」頁</p> : null}
      <h3>字幕辨識引擎</h3>
      {subs.data ? <EngineSelect value={engine} engines={subs.data.engines} onChange={setEngine} label="預設引擎：" /> : null}
      <p className="dim">建議「自動」：用 Paraformer-zh，沒有它的模型時用 SenseVoiceSmall。模型在「主機維護 → 字幕辨識模型」下載（需要 Server 暫時連網）。
        字幕在系統工作時段、轉檔佇列空了之後才開始產生（排程設定可改）。</p>
      <AsrLengthLine />
      <div className="row"><DoneBtn onClick={finish} /></div>
    </div>
  );
}

// 6. 備份：首次設定只需要「建立備份」（dev.55 BK-01：檢查與隔離還原測試在「備份」頁另外做；正式還原不在 App 內）
function StepBackup(p: StepProps): any {
  const { go, toast } = useApp();
  const [key, setKey] = useState(0);
  const bk = useLoad(() => api.get<any>('/api/admin/backups'), [key]);
  const ok = bk.data && bk.data.items.some((b: any) => b.status === 'success');
  useInterval(() => setKey(key + 1), !ok ? 5000 : null);
  const backup = async () => {
    try {
      const r = await api.post('/api/admin/backups', { verify: false });
      toast(r.existing ? '已有備份在進行中' : '已開始建立備份', 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const keyOk = !!(bk.data && bk.data.key && bk.data.key.initialized);
  return (
    <div className="stack">
      <p>備份以 age 加密後儲存於 SSD <code>/srv/istudy/backup</code>（不需要外接硬碟）。先設定備份恢復密碼，再建立第一份備份；這一步只建立備份，不會還原任何資料。</p>
      {/* dev.62：沒有恢復密碼（加密金鑰）就不會產生任何備份 */}
      {bk.data && !keyOk ? <KeyPanel data={bk.data} reload={() => setKey(key + 1)} /> : null}
      <div className="row"><Btn kind="primary" onClick={backup} disabled={!keyOk}>建立備份</Btn></div>
      <BackupList key={key} limit={3} actions={false} />
      <p className="dim">之後可在「備份」頁另外做「檢查完整性」與「隔離還原測試」（只用測試資料庫）。
        <Btn small kind="quiet" onClick={() => go('srv-backup')}>前往備份頁</Btn></p>
      <div className="row"><DoneBtn onClick={() => p.onDone()} disabled={!ok} /></div>
    </div>
  );
}

// 7. Client 電腦：待登記設備 → 登記名稱 → 確認版別 → 試喚醒
function StepClients(p: StepProps): any {
  const { go } = useApp();
  const pending = useLoad(() => api.get<{ devices: any[] }>('/api/devices/pending'), []);
  const devs = useLoad(() => api.get<{ devices: any[] }>('/api/wol/devices'), []);
  useInterval(() => { pending.reload(); devs.reload(); }, 15000);
  return (
    <div className="stack">
      <p>已裝好 Client App 並開機的電腦會自動出現在「待登記設備」。在 WOL 管理登記名稱、確認版別與作業系統相符，並每台試喚醒一次。</p>
      <Msg kind={pending.data && pending.data.devices.length ? 'warn' : 'info'}>待登記設備：{pending.data ? pending.data.devices.length : '…'} 台</Msg>
      {devs.data && devs.data.devices.length ? (
        <table className="grid small">
          <thead><tr><th>電腦</th><th>狀態</th><th>版別</th></tr></thead>
          <tbody>{devs.data.devices.map((d) => (
            <tr key={d.id}><td>{d.name}</td><td>{deviceStateText(d)}</td><td>{d.version_label || '—'}{d.channel_mismatch ? ' ⚠ 版別不符' : ''}</td></tr>
          ))}</tbody>
        </table>
      ) : <p className="dim">尚未登記任何電腦</p>}
      <div className="row"><Btn onClick={() => go('wol')}>前往 WOL 管理</Btn><DoneBtn onClick={() => p.onDone({ devices: devs.data ? devs.data.devices.length : 0 })} /></div>
    </div>
  );
}

// 8. 帳號與群組
function StepAccounts(p: StepProps): any {
  const { go } = useApp();
  const acc = useLoad(() => api.get<{ accounts: any[] }>('/api/accounts'), []);
  const grp = useLoad(() => api.get<{ groups: any[] }>('/api/groups'), []);
  // dev.55 ROLE-01：帳號清單包含 Super（唯一）；分開顯示，避免「admin 0」被誤會
  const all = acc.data ? acc.data.accounts : [];
  const supers = all.filter((a) => a.role === 'super').length;
  const admins = all.filter((a) => a.role === 'admin').length;
  const members = all.filter((a) => a.role === 'user').length;
  return (
    <div className="stack">
      <p>建立帳號（稱謂：助教／講師；權限：user／admin）與課程群組（例如依班別），再把帳號加入群組。可略過，之後在管理列處理。</p>
      <ul className="plain">
        <li>帳號：{acc.data ? `共 ${all.length} 個（權限：super ${supers}、admin ${admins}、user ${members}）` : '…'}　<Btn small onClick={() => go('accounts')}>帳號管理</Btn></li>
        <li>課程群組：{grp.data ? `${grp.data.groups.length} 個` : '…'}　<Btn small onClick={() => go('groups')}>課程群組</Btn></li>
      </ul>
      <div className="row"><DoneBtn onClick={() => p.onDone({ accounts: acc.data ? acc.data.accounts.length : 0 })} /></div>
    </div>
  );
}

// 9. 完成
function StepFinish(props: { setup: Setup; onFinish: () => Promise<void> }): any {
  return (
    <div className="stack">
      <table className="grid">
        <thead><tr><th>步驟</th><th>結果</th><th>時間</th></tr></thead>
        <tbody>{STEPS.slice(0, 8).map(([n, name]) => {
          const s = props.setup.steps[String(n)];
          return <tr key={n}><td>{n}. {name}</td><td>{s ? (s.status === 'done' ? '✓ 完成' : '－ 略過') : '○ 尚未進行'}</td><td>{s && s.at ? dateTime(s.at) : '—'}</td></tr>;
        })}</tbody>
      </table>
      {props.setup.completed ? <Msg kind="ok">已完成（{dateTime(props.setup.completed_at)}）</Msg>
        : <div className="row"><Btn kind="primary" onClick={props.onFinish}>完成首次設定</Btn><span className="dim">略過的步驟之後可以再回來做</span></div>}
    </div>
  );
}
