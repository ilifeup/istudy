/** 總覽（第 10 章）：資料庫、Agent、/Media、備份、佇列、Client 版本；Super 每次登入時已觸發一次診斷。 */
import React from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { Btn, LoadState, Msg, PageHead, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { ago, dateTime, size } from '../../../../packages/ui/src/format';
import { CheckList, ENCODER_TEXT } from '../srv';

function Pill(props: { kind: 'ok' | 'warn' | 'fail' | 'info'; children: any }): any {
  const mark = { ok: '✓', warn: '⚠', fail: '✗', info: 'ⓘ' }[props.kind];
  return <span className={'pill ' + props.kind}><span className="mark">{mark}</span> {props.children}</span>;
}

export function OverviewPage(): any {
  const { go, toast } = useApp();
  const r = useLoad(() => api.get<any>('/api/admin/overview'), []);
  useInterval(() => r.reload(), 30000);
  const rediagnose = async () => {
    try {
      await api.post('/api/admin/diagnostics', {});
      toast('已排入 Server 診斷，約 10 秒後更新', 'ok');
      setTimeout(() => r.reload(), 8000);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const d = r.data;
  const diag = d && d.diagnostics ? d.diagnostics.data : null;
  const media = diag && diag.media_disk;
  const c = d ? d.counts : {};
  return (
    <div className="page">
      <PageHead title="總覽">
        <Btn onClick={rediagnose}>重新診斷</Btn>
        <Btn onClick={r.reload}>重新整理</Btn>
      </PageHead>
      <LoadState loading={r.loading && !d} error={r.error} reload={r.reload} />
      {d ? (
        <>
          <section className="panel">
            <div className="pills">
              <Pill kind="ok">資料庫正常</Pill>
              {d.agent.running ? <Pill kind="ok">Agent 運作中（{ago(d.agent.last_seen_at)}）</Pill>
                : <Pill kind="fail">Agent 沒有回應{d.agent.last_seen_at ? `（最後 ${ago(d.agent.last_seen_at)}）` : ''}</Pill>}
              {media ? (media.free_pct < 20 ? <Pill kind="warn">/Media 剩餘 {media.free_pct}%</Pill> : <Pill kind="ok">/Media 剩餘 {media.free_pct}%（{size(media.free)}）</Pill>)
                : <Pill kind="info">/Media 空間：等待診斷</Pill>}
              {d.last_backup_days === null ? <Pill kind="warn">尚未備份</Pill>
                : d.last_backup_days > 2 ? <Pill kind="warn">上次備份 {d.last_backup_days} 天前</Pill> : <Pill kind="ok">上次備份 {d.last_backup_days === 0 ? '今天' : d.last_backup_days + ' 天前'}</Pill>}
              {d.agent.encoder === 'cpu' ? <Pill kind="warn">轉檔編碼器：CPU（硬體編碼無法使用）</Pill>
                : d.agent.encoder ? <Pill kind="ok">轉檔編碼器：{d.agent.encoder === 'qsv' ? 'Quick Sync' : 'VA-API'}</Pill> : null}
              {d.cpu_downgrades_7d ? <Pill kind="warn">近 7 天 {d.cpu_downgrades_7d} 次轉檔降級為 CPU</Pill> : null}
              {d.conversion_paused ? <Pill kind="warn">轉檔佇列已暫停</Pill> : null}
              <Pill kind="info">轉檔佇列 {c.convert_waiting} 支等待{c.convert_running ? `、${c.convert_running} 支處理中` : ''}</Pill>
              {c.convert_failed ? <Pill kind="fail">{c.convert_failed} 支轉檔失敗</Pill> : null}
              {c.asr_waiting ? <Pill kind="info">字幕 {c.asr_waiting} 支等待</Pill> : null}
              {c.doc_failed ? <Pill kind="fail">{c.doc_failed} 份文件轉換失敗</Pill> : null}
              {c.missing ? <Pill kind="warn">{c.missing} 份教材檔案遺失</Pill> : null}
              {c.originals_due ? <Pill kind="info">{c.originals_due} 個原檔可確認刪除</Pill> : null}
            </div>
            {d.agent.current_job ? <p className="dim">Agent 目前工作：{d.agent.current_job.type} #{d.agent.current_job.id}</p> : null}
            {d.agent.flags && d.agent.flags.includes('space_low') ? <Msg kind="warn">/Media 空間不足，轉檔已暫停等待空間</Msg> : null}
            {d.agent.flags && d.agent.flags.includes('media_marker_mismatch') ? <Msg kind="error">/Media 未掛載或媒體標記不符，Agent 已停止處理教材</Msg> : null}
            <div className="row">
              {c.convert_failed ? <Btn small onClick={() => go('srv-convert')}>查看失敗的轉檔</Btn> : null}
              {d.cpu_downgrades_7d ? <Btn small onClick={() => go('srv-logs')}>查看降級原因（Log）</Btn> : null}
              {c.originals_due ? <Btn small onClick={() => go('srv-originals')}>原檔刪除確認</Btn> : null}
              {d.last_backup_days === null || d.last_backup_days > 2 ? <Btn small onClick={() => go('srv-backup')}>前往備份</Btn> : null}
            </div>
          </section>
          <section className="panel">
            <h2>Client 版本</h2>
            <p className="dim">每台電腦的版本與更新狀態、發布新版：<Btn small kind="quiet" onClick={() => go('srv-clients')}>Client 版本與更新</Btn></p>
            {!d.client_versions.some((v: any) => v.installed) ? <p className="dim">尚未有 Client 電腦回報（Client App 在後續階段推出）</p> : (
            <table className="grid">
              <thead><tr><th>版別</th><th>Server 上的最新版本</th><th>已安裝</th><th>待更新</th><th>版別不符</th></tr></thead>
              <tbody>
                {d.client_versions.map((v: any) => (
                  <tr key={v.channel}>
                    <td>{v.label}</td>
                    <td>{v.latest_label ? `${v.label} ${v.latest_label}` : '—'}</td>
                    <td>{v.installed} 台</td>
                    <td>{v.outdated ? <>⚠ {v.outdated} 台<div className="dim">{v.outdated_devices.map((x: any) => `${x.name}（${x.version}）`).join('、')}</div></> : '—'}</td>
                    <td>{v.mismatch ? <>⚠ {v.mismatch} 台<div className="dim">{v.mismatch_devices.map((x: any) => `${x.name}（${x.os}）`).join('、')}</div></> : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </section>
          <section className="panel">
            <h2>Server 診斷</h2>
            {d.diagnostics ? (
              <>
                <p className="dim">檢查時間：{dateTime(d.diagnostics.created_at)}
                  {diag.host_age_sec !== null && diag.host_age_sec !== undefined ? `；主機檢查 ${Math.round(diag.host_age_sec / 60)} 分鐘前` : ''}</p>
                <CheckList items={diag.checks || []} />
                <table className="grid kv small">
                  <tbody>
                    {diag.cpu ? <tr><th>CPU</th><td>{diag.cpu.model}（{diag.cpu.cores} 執行緒；負載 {(diag.cpu.load || []).join(' / ')}）</td></tr> : null}
                    {diag.memory ? <tr><th>RAM</th><td>{size(diag.memory.total)}（可用 {size(diag.memory.available)}）</td></tr> : null}
                    {media ? <tr><th>/Media</th><td>{size(media.used)} / {size(media.total)}（剩餘 {media.free_pct}%）</td></tr> : null}
                    <tr><th>轉檔編碼器（實際使用）</th><td>{ENCODER_TEXT[diag.encoder] || diag.encoder || '—'}</td></tr>
                    {diag.ffmpeg ? <tr><th>FFmpeg</th><td>{diag.ffmpeg}</td></tr> : null}
                    {diag.libreoffice ? <tr><th>LibreOffice</th><td>{diag.libreoffice}</td></tr> : null}
                    {diag.database ? <tr><th>PostgreSQL</th><td>{diag.database.version}（{size(diag.database.size)}）</td></tr> : null}
                    <tr><th>Server 版本</th><td>{d.server_version_label}</td></tr>
                  </tbody>
                </table>
              </>
            ) : <Msg kind="info">尚無診斷結果（Agent 執行診斷後顯示）</Msg>}
          </section>
        </>
      ) : null}
    </div>
  );
}
