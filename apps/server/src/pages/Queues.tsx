/** 字幕與文件轉換佇列（第 10、12 章）。 */
import React, { useState } from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { Btn, Field, LoadState, Modal, Msg, PageHead, Tabs, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, duration, size } from '../../../../packages/ui/src/format';
import { AsrLengthHint, QualityTag, lengthWarning } from './AsrLimits';

export const JOB_STATUS: Record<string, string> = { queued: '⏳ 等待', running: '… 執行中', done: '✓ 完成', failed: '✗ 失敗', cancelled: '－ 已取消' };
const SOURCE_TEXT: Record<string, string> = { asr: '自動辨識', import: '匯入', manual: '手動' };
const JOB_TYPE_TEXT: Record<string, string> = { asr: '字幕辨識', subtitle_import: '匯入字幕', convert: '轉檔', probe: '讀取影片資訊', verify: '轉檔檢查' };

/** 字幕辨識引擎：本機 4 個名稱＋線上 1 個（順序與 Agent 的 engines.py 相同；dev.59 移除 Whisper） */
export const ASR_ENGINES: { id: string; name: string; route: string; note: string }[] = [
  { id: 'paraformer-zh', name: 'FunASR Paraformer-zh', route: '本機', note: '中文主力候選；非自回歸、CPU 快；不支援熱詞（用修正表）' },
  { id: 'sensevoice-small', name: 'FunASR SenseVoiceSmall', route: '本機', note: '速度基線；內建標點；不支援熱詞（用修正表）' },
  { id: 'qwen3-asr-0.6b', name: 'Qwen3-ASR-0.6B（＋ForcedAligner）', route: '本機', note: '準確度挑戰者' },
  { id: 'breeze-asr-25', name: 'Breeze-ASR-25', route: '本機', note: '台灣口音挑戰者' },
  { id: 'online', name: '線上 API', route: '線上', note: '只預留介面；啟用後音檔會送出 Server' },
];
/** 已移除的引擎：歷史字幕與舊設定照實顯示名稱（資料不改） */
const REMOVED_NAME: Record<string, string> = { whisper: 'Whisper（已移除）', 'whisper-small': 'Whisper small（已移除）', 'whisper-medium': 'Whisper medium（已移除）' };
const ENGINE_NAME = (id: string | null | undefined) => (ASR_ENGINES.find((e) => e.id === id) || { name: (id && REMOVED_NAME[id]) || id || '未知' }).name;
/** dev.60 SUB-02：「已有字幕」的引擎＝辨識報告記下的實際引擎；舊資料沒有報告時用 model；兩個都沒有就寫「未知」，不推測。 */
export function subtitleEngine(d: { source?: string; engine?: string | null; model?: string | null }): string {
  if (d.source === 'import') return '（匯入的字幕）';
  return d.engine || d.model ? ENGINE_NAME(d.engine || d.model) : '未知';
}
/** 程式內建的預設值（資料庫沒有設定時）＝自動；資料庫目前的設定以 /api/admin/subtitles 回傳的 asr.engine 為準 */
export const PROGRAM_DEFAULT_ENGINE = 'auto';
const QC_TEXT: Record<string, string> = { ok: '✓ 通過', needs_review: '⚠ 需檢查' };

function engineState(engines: any, id: string): { ok: boolean; text: string } {
  if (!engines || !engines.reported) return { ok: false, text: '? Agent 尚未回報' };
  const e = engines.engines[id];
  if (!e) return { ok: false, text: '? 未知' };
  if (e.ok) return { ok: true, text: '✓ 可用' };
  return { ok: false, text: (e.reason === '尚未提供' || e.reason === '尚未設定' ? '－ ' : '✗ ') + e.reason };
}

/** 引擎下拉選單：不可用的引擎不能選，並寫出原因。defaultLabel：加一個「使用預設引擎」選項（值是空字串）。 */
export function EngineSelect(props: { value: string; engines: any; onChange: (v: string) => void; label?: string; defaultLabel?: string }): any {
  const auto = props.engines && props.engines.auto;
  return (
    <label className="inline-select"><span>{props.label || '辨識引擎：'}</span>
      <select value={props.value} onChange={(e: any) => props.onChange(e.target.value)}>
        {props.defaultLabel ? <option value="">{props.defaultLabel}</option> : null}
        <option value="auto">自動（依序選第一個可用的；目前會選：{auto ? ENGINE_NAME(auto) : '沒有可用的引擎'}）</option>
        {REMOVED_NAME[props.value] ? <option value={props.value} disabled>{REMOVED_NAME[props.value]}：請改選其他引擎</option> : null}
        {ASR_ENGINES.map((e) => {
          const st = engineState(props.engines, e.id);
          return <option key={e.id} value={e.id} disabled={!st.ok}>{e.name}{st.ok ? '' : `（${st.text.replace(/^[✓✗?－]\s*/, '')}）`}</option>;
        })}
      </select>
    </label>
  );
}

function AsrSettings(props: { data: any; onSaved: () => void }): any {
  const { toast } = useApp();
  const a = props.data.asr;
  const [maxChars, setMaxChars] = useState(String(a.max_chars));
  const [maxSec, setMaxSec] = useState(String(a.max_sec));
  const [fixes, setFixes] = useState(a.glossary.fixes.map((p: string[]) => `${p[0]}=${p[1]}`).join('\n'));
  const [protect, setProtect] = useState(a.glossary.protect.join('\n'));
  const [hot, setHot] = useState(a.glossary.hotwords.join('\n'));
  const lines = (t: string) => t.split('\n').map((x) => x.trim()).filter(Boolean);
  const save = async () => {
    const pairs = lines(fixes).map((l) => l.split('='));
    const bad = pairs.find((p) => p.length !== 2 || !p[0].trim());
    if (bad) return toast(`修正表格式是「錯字=正確」：${bad.join('=')}`, 'error');
    try {
      await api.put('/api/admin/config', { asr: { max_chars: Number(maxChars), max_sec: Number(maxSec),
        glossary: { fixes: pairs.map((p) => [p[0].trim(), p[1].trim()]), protect: lines(protect), hotwords: lines(hot) } } });
      toast('字幕辨識設定已儲存（下一支字幕開始套用）', 'ok');
      props.onSaved();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const eng = props.data.engines;
  return (
    <div className="stack">
      <section className="panel">
        <h2>引擎狀態</h2>
        {eng && !eng.agent_running ? <Msg kind="warn">Agent 沒有在執行：引擎狀態可能是舊的</Msg> : null}
        <table className="grid small">
          <thead><tr><th>引擎</th><th>路線</th><th>狀態</th><th>說明</th></tr></thead>
          <tbody>{ASR_ENGINES.map((e) => {
            const st = engineState(eng, e.id);
            return <tr key={e.id}><td>{e.name}</td><td>{e.route}</td><td className="nowrap">{st.text}</td><td>{e.note}</td></tr>;
          })}</tbody>
        </table>
        <p className="dim">下載 FunASR 模型（Paraformer-zh、SenseVoiceSmall、VAD、標點，約 450 MB，需要 Server 暫時連網）：
          在 Server 終端機執行 <code>sudo istudyctl asr-models --fetch all</code>，或到「主機維護 → 字幕辨識模型」。
          模型授權為 FunASR MODEL_LICENSE，商用與署名條件請自行確認。</p>
      </section>
      <section className="panel">
        <h2>字幕斷句</h2>
        <div className="form-row tight">
          <Field label="每條最多字數（10～40）"><input value={maxChars} inputMode="numeric" onChange={(e: any) => setMaxChars(e.target.value)} /></Field>
          <Field label="每條最長秒數（3～10）"><input value={maxSec} inputMode="numeric" onChange={(e: any) => setMaxSec(e.target.value)} /></Field>
        </div>
      </section>
      <section className="panel">
        <h2>詞彙表</h2>
        <p className="dim">所有引擎都會套用修正表與保護詞（在轉成繁體之後）。目前的引擎都不支援熱詞，請用修正表；已填的熱詞會保留。</p>
        <div className="form-grid">
          <Field label="修正表（一行一個：錯字=正確）" wide hint="例如：圓三=研三"><textarea rows={5} value={fixes} onChange={(e: any) => setFixes(e.target.value)} /></Field>
          <Field label="保護詞（一行一個）" hint="轉繁體時不能被改掉的詞：人名、書名、課程用語"><textarea rows={5} value={protect} onChange={(e: any) => setProtect(e.target.value)} /></Field>
          <Field label="熱詞（一行一個）" hint="目前的引擎都不使用（dev.58 以前只有 Whisper 使用）；保留給之後支援熱詞的引擎"><textarea rows={5} value={hot} onChange={(e: any) => setHot(e.target.value)} /></Field>
        </div>
      </section>
      <div className="row"><Btn kind="primary" onClick={save}>儲存設定</Btn></div>
    </div>
  );
}

export function SubtitlesPage(): any {
  const { go, toast } = useApp();
  const r = useLoad(() => api.get<any>('/api/admin/subtitles'), []);
  const cfg = useLoad(() => api.get<any>('/api/admin/config'), []);
  const [tab, setTab] = useState('pending');
  const [rerun, setRerun] = useState('');           // 空字串＝用預設引擎（資料庫目前的設定）
  const [confirmRun, setConfirmRun] = useState<any>(null);
  const running = r.data && r.data.jobs.some((j: any) => j.status === 'running');
  useInterval(() => r.reload(), running ? 5000 : 30000);
  const run = async (id: number, engine?: string) => {
    try {
      const x = await api.post(`/api/admin/subtitles/${id}/run`, engine ? { engine } : {});
      toast(x.running ? '這支影片正在辨識中' : x.upgraded ? '已改為馬上執行（原本在等系統時段）' : x.existing ? '已在佇列中（馬上執行）' : '已排入字幕產生（不受時段限制）', 'ok');
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const setEngine = async (v: string) => {
    try {
      await api.put('/api/admin/config', { asr: { engine: v } });
      toast(`預設引擎改為 ${v === 'auto' ? '自動' : ENGINE_NAME(v)}`, 'ok');
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const defaultEngine = r.data ? r.data.asr.engine : PROGRAM_DEFAULT_ENGINE;
  const defaultName = defaultEngine === 'auto' ? `自動（目前會選：${r.data && r.data.engines.auto ? ENGINE_NAME(r.data.engines.auto) : '沒有可用的引擎'}）` : ENGINE_NAME(defaultEngine);
  const inQueue = new Set<number>(r.data ? r.data.jobs.filter((j: any) => j.type === 'asr' && (j.status === 'queued' || j.status === 'running'))
    .map((j: any) => Number(j.payload.media_id)) : []);
  const failed = r.data ? r.data.jobs.filter((j: any) => j.status === 'failed') : [];
  const queue = r.data ? r.data.jobs.filter((j: any) => j.status === 'queued' || j.status === 'running') : [];
  const review = r.data ? r.data.done.filter((d: any) => d.qc && d.qc.status === 'needs_review') : [];
  const s = cfg.data ? cfg.data.schedule : null;
  return (
    <div className="page">
      <PageHead title="字幕">
        {r.data ? <EngineSelect label="預設引擎：" value={r.data.asr.engine} engines={r.data.engines} onChange={(v) => void setEngine(v)} /> : null}
        {r.data && REMOVED_NAME[r.data.asr.engine] ? <Msg kind="warn">⚠ 預設引擎仍是已移除的 {REMOVED_NAME[r.data.asr.engine]}：字幕辨識會失敗，請改選「自動」</Msg> : null}
      </PageHead>
      {r.data ? <p className="dim">預設引擎＝資料庫目前的設定：<strong>{defaultName}</strong>（自動產生字幕、沒有指定引擎的工作都用它）。
        程式內建的預設值是「自動」（Paraformer-zh → SenseVoiceSmall，選第一個可用的），只在資料庫沒有設定時使用。</p> : null}
      {s ? <p className="dim">自動產生：{s.auto_whisper ? `開（${s.start}～${s.end}${s.whisper_after_convert ? '，轉檔佇列空了之後才開始' : ''}）` : '關'}；修改請到「排程設定」。字幕的匯入匯出在播放器的字幕面板。
        <Btn small kind="quiet" onClick={() => go('srv-schedule')}>排程設定</Btn></p> : null}
      <AsrLengthHint kind="video" speed={r.data ? r.data.speed : null} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'pending', label: `待產生${r.data ? `（${r.data.pending.length}）` : ''}` },
        { id: 'queue', label: `佇列${queue.length ? `（${queue.length}）` : ''}` },
        { id: 'failed', label: `失敗${failed.length ? `（${failed.length}）` : ''}` },
        { id: 'done', label: `已有字幕${review.length ? `（⚠ ${review.length} 需檢查）` : ''}` },
        { id: 'settings', label: '辨識設定' },
      ]} />
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data && tab === 'pending' ? (
        r.data.pending.length ? (
          <table className="grid small">
            <thead><tr><th>影片</th><th>長度</th><th>狀態</th><th></th></tr></thead>
            <tbody>{r.data.pending.map((m: any) => (
              <tr key={m.id}><td>{m.title}<div className="dim path">{m.rel_path}</div></td><td className="nowrap">{duration(m.duration)}{lengthWarning(m.duration) ? <div className="warn-text">{lengthWarning(m.duration)}</div> : null}</td>
                <td>{pendingState(r.data.jobs, m.id)}</td>
                <td><Btn small onClick={() => run(m.id)}>立即執行</Btn></td></tr>
            ))}</tbody>
          </table>
        ) : <Msg kind="info">所有可播放的影片都已有字幕</Msg>
      ) : null}
      {r.data && (tab === 'queue' || tab === 'failed') ? <JobTable jobs={tab === 'queue' ? queue : failed} onChange={r.reload} empty={tab === 'queue' ? '佇列是空的' : '沒有失敗的工作'}
        defaultName={defaultName} /> : null}
      {r.data && tab === 'done' ? (
        <>
          <div className="row">
            <EngineSelect label="重新產生時使用：" value={rerun} engines={r.data.engines} onChange={setRerun} defaultLabel={`預設引擎（${defaultName}）`} />
            <span className="dim">同一支影片可以用不同引擎重新產生、比較結果。新字幕辨識成功後才取代舊的；失敗時舊字幕保留。</span>
          </div>
          <table className="grid small">
            <thead><tr><th>影片</th><th>來源</th><th>引擎</th><th>速度（RTF）</th><th>音訊品質</th><th>品質檢查</th><th>句數</th><th>時間</th><th></th></tr></thead>
            <tbody>{r.data.done.map((d: any) => (
              <tr key={d.media_id}><td>{d.title}</td><td>{SOURCE_TEXT[d.source] || d.source}</td>
                <td>{subtitleEngine(d)}</td>
                <td className="nowrap">{d.rtf !== null && d.rtf !== undefined ? `${d.rtf}` : '—'}</td>
                <td>{d.audio_quality ? <QualityTag level={d.audio_quality.level} reasons={d.audio_quality.reasons} max={2} /> : <span className="dim">—（dev.61 前產生）</span>}</td>
                <td>{d.qc ? <>{QC_TEXT[d.qc.status] || d.qc.status}{(d.qc.reasons || []).map((x: string, i: number) => <div key={i} className="dim">⚠ {x}</div>)}
                  {(d.qc.notes || []).map((x: string, i: number) => <div key={'n' + i} className="dim">· {x}</div>)}</> : '—'}</td>
                <td>{d.segments}</td><td className="nowrap">{dateTime(d.created_at)}</td>
                <td className="actions"><Btn small onClick={() => go('player', { id: d.media_id })}>播放</Btn>
                  {inQueue.has(d.media_id) ? <span className="dim">… 已在佇列</span>
                    : <Btn small kind="quiet" onClick={() => (d.source === 'import' ? setConfirmRun(d) : void run(d.media_id, rerun))}>重新產生</Btn>}</td></tr>
            ))}</tbody>
          </table>
          <p className="dim">RTF＝處理時間 ÷ 影片長度；0.3 代表 10 分鐘影片約 3 分鐘處理完。引擎「未知」＝舊紀錄沒有記下引擎。</p>
          {confirmRun ? (
            <Modal title="以自動辨識取代匯入的字幕？" onClose={() => setConfirmRun(null)}
              footer={<><Btn onClick={() => setConfirmRun(null)}>取消</Btn>
                <Btn kind="primary" onClick={() => { const id = confirmRun.media_id; setConfirmRun(null); void run(id, rerun); }}>重新產生</Btn></>}>
              <p>「{confirmRun.title}」目前是匯入的字幕。重新產生成功後，自動辨識的字幕會取代它；辨識失敗時保留現在的字幕。</p>
            </Modal>
          ) : null}
        </>
      ) : null}
      {r.data && tab === 'settings' ? <AsrSettings data={r.data} onSaved={r.reload} /> : null}
    </div>
  );
}

/** dev.62：待產生的影片目前的字幕工作狀態（等系統時段／馬上執行／辨識中／上次失敗）。 */
function pendingState(jobs: any[], mid: number): string {
  const j = jobs.find((x: any) => x.type === 'asr' && Number(x.payload.media_id) === mid);
  if (!j) return '－ 還沒排入（系統時段內會自動排入）';
  if (j.status === 'running') return '… 辨識中';
  if (j.status === 'queued') return j.payload.immediate ? '⏳ 馬上執行' : '⏳ 等系統時段（可按「立即執行」）';
  if (j.status === 'failed') return '✗ 上次失敗：' + String(j.error || '').slice(0, 60);
  return '－';
}

/** 字幕工作的引擎：完成的用實際引擎；有指定的寫指定的；沒指定而且還沒跑完的用預設引擎；其他（舊紀錄）寫「未知」。 */
function jobEngine(j: any, defaultName?: string): string {
  if (j.result && j.result.engine) return ENGINE_NAME(j.result.engine);
  if (j.payload && j.payload.engine) return ENGINE_NAME(j.payload.engine);
  if ((j.status === 'queued' || j.status === 'running') && defaultName) return `預設引擎（${defaultName}）`;
  return '未知';
}

export function JobTable(props: { jobs: any[]; onChange: () => void; empty: string; defaultName?: string }): any {
  const { toast } = useApp();
  const cancel = async (id: number) => {
    try {
      await api.post(`/api/admin/jobs/${id}/cancel`, {});
      props.onChange();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  if (!props.jobs.length) return <Msg kind="info">{props.empty}</Msg>;
  return (
    <table className="grid small">
      <thead><tr><th>工作</th><th>教材</th><th>狀態</th><th>建立</th><th>開始／結束</th><th>錯誤</th><th></th></tr></thead>
      <tbody>{props.jobs.map((j) => (
        <tr key={j.id}>
          <td>#{j.id} {JOB_TYPE_TEXT[j.type] || j.type}{j.type === 'asr' ? <div className="dim">引擎：{jobEngine(j, props.defaultName)}</div> : null}</td><td>{j.payload.media_id ?? '—'}</td>
          <td>{JOB_STATUS[j.status] || j.status}{j.status === 'running' && j.result && j.result.percent !== undefined ? ` ${Math.round(j.result.percent)}%` : ''}
            {j.attempts > 1 ? <div className="dim">第 {j.attempts} 次</div> : null}</td>
          <td>{dateTime(j.created_at)}</td><td>{j.started_at ? dateTime(j.started_at) : '—'}{j.finished_at ? <div className="dim">{dateTime(j.finished_at)}</div> : null}</td>
          <td className="err-text">{j.error || ''}</td>
          <td>{j.status === 'queued' ? <Btn small onClick={() => cancel(j.id)}>取消</Btn> : null}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

const DOC_STATUS: Record<string, string> = { pending: '⏳ 等待轉換', converting: '… 轉換中', done: '✓ 已轉 PDF', failed: '✗ 轉換失敗', none: '—' };

export function DocumentsPage(): any {
  const { go, toast } = useApp();
  const r = useLoad(() => api.get<{ items: any[] }>('/api/admin/documents'), []);
  const [tab, setTab] = useState('all');
  const items = r.data ? r.data.items.filter((d) => tab === 'all' || (tab === 'queue' ? ['pending', 'converting'].includes(d.doc_status) : d.doc_status === tab)) : [];
  const busy = r.data && r.data.items.some((d) => d.doc_status === 'converting' || d.doc_status === 'pending');
  useInterval(() => r.reload(), busy ? 5000 : null);
  const retry = async (id: number) => {
    try {
      await api.post(`/api/admin/documents/${id}/retry`, {});
      toast('已重新排入轉換', 'ok');
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const count = (st: string[]) => (r.data ? r.data.items.filter((d) => st.includes(d.doc_status)).length : 0);
  return (
    <div className="page">
      <PageHead title="文件轉換" />
      <p className="dim">Word、Excel、PowerPoint 由 Agent 以 LibreOffice 轉成 PDF 後才能開啟；原檔不變。文件轉換不受系統工作時段限制。
        轉換依原檔的頁面設定（紙張、方向、縮放、列印範圍）；原檔的設定會讓表格分頁時，會寫在狀態下方（ⓘ）。修改原檔或程式更新後，按「重新轉換」。</p>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'all', label: '全部' }, { id: 'queue', label: `佇列（${count(['pending', 'converting'])}）` },
        { id: 'failed', label: `失敗（${count(['failed'])}）` }, { id: 'done', label: '已完成' },
      ]} />
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data && !items.length ? <Msg kind="info">沒有文件</Msg> : null}
      {items.length ? (
        <table className="grid small">
          <thead><tr><th>文件</th><th>大小</th><th>狀態</th><th>更新</th><th></th></tr></thead>
          <tbody>{items.map((d) => (
            <tr key={d.id}>
              <td>{d.title}<div className="dim path">{d.rel_path}</div></td><td>{size(d.size)}</td>
              <td>{DOC_STATUS[d.doc_status] || d.doc_status}{d.doc_status === 'done' && d.pages ? `（${d.pages} 頁）` : ''}
                {d.doc_error ? <div className="err-text">{d.doc_error}</div> : null}
                {d.doc_status === 'done' && d.notes && d.notes.length ? (
                  <ul className="plain small doc-notes">{d.notes.map((n: string, i: number) => <li key={i}>{n.startsWith('⚠') ? n : `ⓘ ${n}`}</li>)}</ul>
                ) : null}</td>
              <td>{dateTime(d.updated_at)}</td>
              <td className="actions">
                {d.doc_status === 'failed' || d.doc_status === 'done' ? <Btn small onClick={() => retry(d.id)}>{d.doc_status === 'failed' ? '重試' : '重新轉換'}</Btn> : null}
                {d.doc_status === 'done' ? <Btn small onClick={() => go('doc', { id: d.id })}>開啟</Btn> : null}
              </td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
    </div>
  );
}
