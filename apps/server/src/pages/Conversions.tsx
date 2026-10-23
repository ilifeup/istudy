/**
 * 轉檔佇列（第 10、11 章）：檢測結果、處理方式、狀態、錯誤；重試、整個佇列暫停、單支立即執行（不受時段限制）。
 * dev.62：加「字幕」欄與「產生字幕／立即產生／重試字幕」（直接播放的影片也能產生，不需要重新轉檔）。
 */
import React, { useState } from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { Btn, LoadState, Modal, Msg, PageHead, Tabs, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, duration, size } from '../../../../packages/ui/src/format';
import { convertText } from '../../../../packages/ui/src/pages/Courses';

export const PLAY_MODE: Record<string, string> = { direct: '直接播放', remux: '換封裝（不重新編碼）', transcode: '重新編碼' };

/** MP4 自動檢查（四項全過才算「檢查通過」，第 11 章）。 */
export function VerifyResult(props: { v: any; full?: boolean }): any {
  const v = props.v;
  if (!v || !Array.isArray(v.checks)) return <span className="dim">—</span>;
  const failed = v.checks.filter((c: any) => !c.ok);
  if (!props.full) {
    return v.passed ? <span>✓ 檢查通過（{v.checks.length} 項）</span>
      : <span className="err-text">✗ {failed.length} 項未通過：{failed.map((c: any) => c.name).join('、')}</span>;
  }
  return (
    <ul className="plain small">
      {v.checks.map((c: any, i: number) => <li key={i}>{c.ok ? '✓' : '✗'} {c.name}{c.message ? `：${c.message}` : ''}</li>)}
    </ul>
  );
}

function probeText(p: any): string {
  if (!p || !p.container) return '—';
  const container = /(^|,)mp4(,|$)/.test(p.container) ? 'mp4' : p.container === 'mpegts' ? 'MPEG-TS' : p.container;
  const parts = [container, p.video_codec, p.audio_codec, p.width && p.height ? `${p.width}×${p.height}` : '', p.fps ? `${p.fps}fps` : '',
    p.interlaced === true ? '交錯' : p.interlaced === false ? '逐行' : ''];
  return parts.filter(Boolean).join('・');
}

/** dev.62：字幕狀態（符號＋文字）。可以播放的影片（直接播放或轉檔完成）都能產生字幕，不需要重新轉檔。 */
export function subtitleState(m: any): { text: string; action: string | null } {
  const sub = m.subtitle || {};
  const j = sub.job;
  if (m.state !== 'ready') return { text: '－ 可以播放後才能產生', action: null };
  if (j && j.status === 'running') return { text: `… 辨識中${j.percent !== null && j.percent !== undefined ? ' ' + Math.round(Number(j.percent)) + '%' : ''}`, action: null };
  if (j && j.status === 'queued') return j.immediate ? { text: '⏳ 已排入，馬上執行', action: null } : { text: '⏳ 等系統時段（自動）', action: '立即產生' };
  if (sub.has) return { text: '✓ 有字幕', action: null };
  if (j && j.status === 'failed') return { text: `✗ 失敗${j.error ? '：' + String(j.error).slice(0, 80) : ''}`, action: '重試字幕' };
  return { text: '－ 尚無字幕', action: '產生字幕' };
}

export function ConversionsPage(): any {
  const { go, toast } = useApp();
  const [tab, setTab] = useState('all');
  const r = useLoad(() => api.get<any>('/api/admin/conversions' + (tab === 'all' ? '' : `?status=${tab}`)), [tab]);
  const [detail, setDetail] = useState<any | null>(null);
  const items = r.data ? r.data.items : [];
  // dev.55 HW-01：Agent 目前可用硬體編碼，但這支實際用了 CPU ＝ 這支降級（原因寫在 Log）
  const cpuDowngrade = !!(r.data && r.data.agent_encoder && r.data.agent_encoder !== 'cpu');
  const running = items.some((m: any) => ['converting', 'verifying', 'probing'].includes(m.convert_status));
  useInterval(() => r.reload(), running ? 5000 : 30000);
  const act = async (url: string, body: unknown, ok: string) => {
    try {
      await api.post(url, body);
      toast(ok, 'ok');
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const s = r.data ? r.data.schedule : null;
  return (
    <div className="page">
      <PageHead title="轉檔佇列">
        {r.data ? (r.data.paused
          ? <Btn kind="primary" onClick={() => act('/api/admin/conversions/pause', { paused: false }, '已恢復轉檔佇列')}>▶ 恢復佇列</Btn>
          : <Btn onClick={() => act('/api/admin/conversions/pause', { paused: true }, '已暫停轉檔佇列（進行中的那一支會做完）')}>⏸ 暫停整個佇列</Btn>) : null}
        <Btn onClick={() => go('srv-schedule')}>排程設定</Btn>
      </PageHead>
      {r.data && r.data.paused ? <Msg kind="warn">轉檔佇列已暫停；「立即執行」的單支影片仍會執行</Msg> : null}
      {s ? <p className="dim">系統工作時段 {s.start}～{s.end}（Asia/Taipei）；自動轉檔：{s.auto_convert ? '開' : '關'}；時段外只執行按了「立即執行」的影片</p> : null}
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'all', label: '全部' }, { id: 'waiting', label: '等待' }, { id: 'running', label: '處理中' }, { id: 'done', label: '完成' }, { id: 'failed', label: '失敗' },
      ]} />
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data && !items.length ? <Msg kind="info">沒有影片</Msg> : null}
      {items.length ? (
        <table className="grid small">
          <thead><tr><th>影片</th><th>檢測結果</th><th>處理方式</th><th>狀態</th><th>MP4 檢查</th><th>字幕</th><th>操作</th></tr></thead>
          <tbody>
            {items.map((m: any) => {
              const pct = m.job && m.job.status === 'running' && m.job.percent !== null && m.job.percent !== undefined ? Math.round(Number(m.job.percent)) : null;
              const st = subtitleState(m);
              return (
                <tr key={m.id}>
                  <td>{m.title}<div className="dim path">{m.rel_path}・{size(m.size)}{m.duration ? '・' + duration(m.duration) : ''}</div></td>
                  <td>{probeText(m.probe)}</td>
                  <td>{m.play_mode ? PLAY_MODE[m.play_mode] : '—'}{m.encoder ? <div className="dim">{m.encoder === 'qsv' ? 'Quick Sync' : m.encoder === 'vaapi' ? 'VA-API' : m.encoder === 'cpu' ? (cpuDowngrade ? '⚠ CPU（已降級，原因見 Log）' : 'CPU') : m.encoder}</div> : null}</td>
                  <td>
                    {convertText(m)}{pct !== null ? ` ${pct}%` : ''}
                    {pct !== null ? <progress max={100} value={pct} aria-label="轉檔進度" /> : null}
                    {m.job && m.job.immediate && m.job.status === 'queued' ? <div className="dim">已設為立即執行</div> : null}
                    {m.convert_error ? <div className="err-text">{m.convert_error}</div> : null}
                  </td>
                  <td><VerifyResult v={m.verify} />{m.verified_at ? <div className="dim">{dateTime(m.verified_at)}</div> : null}</td>
                  <td>{st.text}</td>
                  <td className="actions">
                    {st.action ? <Btn small onClick={() => act(`/api/admin/subtitles/${m.id}/run`, {}, '已排入字幕產生（馬上執行，不受時段限制）')}>{st.action}</Btn> : null}
                    {['failed', 'verify_failed'].includes(m.convert_status) ? <Btn small onClick={() => act(`/api/admin/conversions/${m.id}/retry`, {}, '已重新排入檢測與轉檔')}>重試</Btn> : null}
                    {['queued', 'failed', 'verify_failed'].includes(m.convert_status) ? <Btn small onClick={() => act(`/api/admin/conversions/${m.id}/run-now`, {}, '已排入，馬上執行')}>立即執行</Btn> : null}
                    {m.state === 'ready' ? <Btn small onClick={() => go('player', { id: m.id })}>播放抽查</Btn> : null}
                    <Btn small kind="quiet" onClick={() => setDetail(m)}>詳細</Btn>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      {detail ? (
        <Modal title={`檢測與轉檔：${detail.title}`} onClose={() => setDetail(null)} wide>
          <pre className="hostout">{JSON.stringify({ probe: detail.probe, play_mode: detail.play_mode, encoder: detail.encoder, verify: detail.verify, job: detail.job, error: detail.convert_error }, null, 2)}</pre>
        </Modal>
      ) : null}
    </div>
  );
}
