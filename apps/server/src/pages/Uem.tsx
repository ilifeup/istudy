/**
 * 主機盤點（UEM，dev.56）：磁碟歸屬（路徑 → 掛載點 → 實體磁碟／ZFS pool）、ZFS、服務與連接埠、版本對照、自我檢查。
 * 按鈕以 pkexec 執行固定的 `istudyctl uem --mode quick|full`（只讀）；畫面只讀取 /var/lib/istudy/uem 的最新報告，
 * 不常駐、不定時輪詢、不存資料庫。
 */
import React, { useState } from 'react';
import { errText } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, LoadState, Msg, PageHead, useApp, useLoad } from '../../../../packages/ui/src/core';
import { dateTime } from '../../../../packages/ui/src/format';
import { CheckList, eventsOf, lastDone, useHostRun } from '../srv';

const MARK: Record<string, string> = { PASS: '✓', WARN: '⚠', FAIL: '✗', UNKNOWN: '?', MISSING: '－', INFO: '·', SKIP: '－' };
const TEXT: Record<string, string> = { PASS: '通過', WARN: '注意', FAIL: '異常', UNKNOWN: '未知', MISSING: '不存在', INFO: '資訊', SKIP: '略過' };

function St(props: { s: string }): any {
  const s = props.s || 'UNKNOWN';
  return <span className={'ust ust-' + s.toLowerCase()}>{MARK[s] || '·'} {TEXT[s] || s}</span>;
}

/** 短欄位（狀態、類型、容量…）不換行，長路徑可換行。 */
const NOWRAP = new Set(['狀態', '類型', '容量', '掛載點', '來源', '類別', '讀寫', '版本', '結構', '容量／已用', '磁碟', '用途']);

function Table(props: { head: string[]; rows: any[][]; empty?: string }): any {
  if (!props.rows.length) return props.empty ? <p className="dim">{props.empty}</p> : null;
  return (
    <div className="hscroll">
      <table className="grid small">
        <thead><tr>{props.head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{props.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={NOWRAP.has(props.head[j]) ? 'nowrap' : undefined}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

const disksText = (ds: any[]) => (ds || []).map((d) => `${d.name}（${d.media}${d.model ? '，' + d.model : ''}）`).join(' + ') || '—';

export function UemPage(): any {
  const { toast } = useApp();
  const latest = useLoad(() => bridge.host.uemLatest(), []);
  const h = useHostRun();
  const [err, setErr] = useState<string | null>(null);
  const start = async (mode: 'quick' | 'full') => {
    setErr(null);
    try {
      await h.start(mode === 'full' ? 'uem-full' : 'uem-quick');
      latest.reload();
    } catch (e) {
      setErr(errText(e));
    }
  };
  const save = async (kind: 'html' | 'md' | 'json') => {
    const d = latest.data;
    if (!d || !d.json) return;
    const stamp = String(d.json.time || '').replace(/[^0-9]/g, '').slice(0, 12) || 'latest';
    const content = kind === 'json' ? JSON.stringify(d.json, null, 1) : kind === 'md' ? d.md || '' : d.html || '';
    const r = await bridge.saveFile(`istudy-uem-${stamp}.${kind}`, content);
    if (r.ok) toast(`已匯出 ${r.path}`, 'ok');
  };
  const rep = latest.data && latest.data.json;
  const c = (rep && rep.counts) || {};
  const done = lastDone(h.run);
  const infos = eventsOf(h.run, 'info').map((x) => x.message);
  const errors = eventsOf(h.run, 'error').map((x) => x.message.replace(/^✗\s*/, ''));
  const services = rep ? [...(rep.services || []), ...(rep.containers || []).map((x: any) => ({ ...x, unit: x.image })), ...(rep.db ? [{ ...rep.db, unit: '' }] : [])] : [];
  return (
    <div className="page">
      <PageHead title="主機盤點（UEM）">
        <Btn kind="primary" disabled={h.running} onClick={() => start('quick')}>快速盤點</Btn>
        <Btn disabled={h.running} onClick={() => start('full')}>完整盤點（含自我檢查）</Btn>
        {h.running ? <Btn kind="quiet" onClick={h.cancel}>停止</Btn> : null}
      </PageHead>
      <Msg kind="info">只讀：不修改設定、不讀 .env 內容、不刪除任何檔案。快速盤點約數秒；完整盤點另外執行自我檢查（轉檔與文件試轉），約 1～3 分鐘。需要輸入 Ubuntu 系統密碼。</Msg>
      {err ? <Msg kind="error">{err}</Msg> : null}
      {h.run ? (
        <div className="hostrun">
          {infos.length ? <pre className="hostout">{infos.join('\n')}</pre> : null}
          {errors.map((m, i) => <Msg key={i} kind="error">{m}</Msg>)}
          {!h.run.exit ? <p className="dim">… 盤點中</p> : null}
          {h.run.exit && h.run.exit.error ? <Msg kind="error">{h.run.exit.error}</Msg> : null}
          {h.run.exit && done && done.message ? <Msg kind={done.status === 'pass' ? 'ok' : done.status === 'warn' ? 'warn' : 'error'}>{done.message}</Msg> : null}
        </div>
      ) : null}
      <LoadState loading={latest.loading && !latest.data} error={latest.error} reload={latest.reload} />
      {latest.data && latest.data.error ? <Msg kind="error">{latest.data.error}</Msg> : null}
      {latest.data && latest.data.none ? <Msg kind="warn">還沒有主機盤點報告：按「快速盤點」產生第一份。</Msg> : null}
      {rep ? (
        <>
          <section className="panel">
            <p>
              上次盤點：<b>{dateTime(rep.time)}</b>（{rep.mode === 'full' ? '完整' : '快速'}，{rep.duration_sec} 秒）
              {rep.istudy_version ? `　iStudy ${rep.istudy_version}` : ''}
              {rep.host && rep.host.hostname ? `　主機 ${rep.host.hostname}` : ''}
            </p>
            {rep.host && rep.host.board ? (
              <p className="dim">主機板：{boardText(rep.host.board)}</p>
            ) : null}
            <div className="pills">
              <span className={'pill ' + (rep.overall === 'FAIL' ? 'fail' : rep.overall === 'WARN' ? 'warn' : 'ok')}>
                <span className="mark">{MARK[rep.overall]}</span> 整體：{TEXT[rep.overall]}</span>
              {c.FAIL ? <span className="pill fail"><span className="mark">✗</span> 異常 {c.FAIL}</span> : null}
              {c.WARN ? <span className="pill warn"><span className="mark">⚠</span> 注意 {c.WARN}</span> : null}
              {c.MISSING ? <span className="pill warn"><span className="mark">－</span> 不存在 {c.MISSING}</span> : null}
              {c.UNKNOWN ? <span className="pill"><span className="mark">?</span> 未知 {c.UNKNOWN}</span> : null}
              <span className="pill ok"><span className="mark">✓</span> 通過 {c.PASS || 0}</span>
            </div>
            {(rep.notes || []).map((n: string, i: number) => <Msg key={i} kind="warn">{n}</Msg>)}
            <div className="row">
              <span className="dim">匯出報告：</span>
              <Btn small onClick={() => save('html')}>HTML（可離線開啟）</Btn>
              <Btn small onClick={() => save('md')}>Markdown</Btn>
              <Btn small onClick={() => save('json')}>JSON</Btn>
            </div>
          </section>

          <section className="panel">
            <h2>需要處理</h2>
            {rep.issues && rep.issues.length ? (
              <Table head={['狀態', '類別', '項目', '說明']} rows={rep.issues.map((i: any) => [
                <St s={i.status} />, i.section, <>{i.name}{i.path ? <div className="dim mono">{i.path}</div> : null}</>, i.detail])} />
            ) : <Msg kind="ok">沒有需要處理的項目</Msg>}
          </section>

          <section className="panel">
            <h2>重要路徑與磁碟歸屬</h2>
            <p className="dim">磁碟類別由 findmnt → lsblk（ROTA）→ zpool 成員實際查出；讀不到就是「未知」，不依路徑名稱推測。</p>
            <Table head={['名稱／用途', '完整路徑', '掛載點', '來源', '磁碟類別', '狀態']} rows={(rep.paths || []).map((p: any) => [
              <>{p.name}<div className="dim">{p.purpose}</div></>, <span className="mono">{p.path}</span>, <span className="mono">{p.mount || '—'}</span>,
              <span className="mono">{p.source || '—'}</span>, <>{p.storage_label || '—'}{p.disks && p.disks.length ? <div className="dim">{disksText(p.disks)}</div> : null}</>,
              <><St s={p.status} />{p.detail ? <div className="dim">{p.detail}</div> : null}</>])} />
          </section>

          <section className="panel">
            <h2>磁碟對照</h2>
            <Table head={['磁碟', '類型', '型號', '容量', '用途', '分割區', 'iStudy 目錄']} empty="讀不到磁碟清單（lsblk）"
              rows={(rep.disk_map || []).map((d: any) => [
                d.name, d.media, d.model || '—', d.size_gb ? `${d.size_gb} GB` : '—', d.role,
                (d.partitions || []).map((k: any) => `${k.name}（${k.fstype || '—'}${k.mountpoints && k.mountpoints.length ? '，' + k.mountpoints.join('、') : ''}）`).join('、') || '—',
                (d.istudy_dirs || []).map((x: any) => x.path).join('、') || '—'])} />
          </section>

          <section className="panel">
            <h2>ZFS</h2>
            <Table head={['pool', '結構', '成員', '容量／已用', '狀態']}
              empty={rep.zfs && rep.zfs.available ? '沒有 ZFS pool' : '沒有 ZFS 工具或無法讀取（未知）'}
              rows={((rep.zfs && rep.zfs.pools) || []).map((p: any) => [
                p.name + (p.media_pool ? '（教材）' : ''), p.layout_text,
                (p.members || []).map((m: any) => `${m.disk || m.path}（${m.media}，${m.state}）`).join('、') || '—',
                p.size ? `${Math.round(p.size / 1e9)} GB／${p.used_pct}%` : '—',
                <><St s={p.status} />{p.detail ? <div className="dim">{p.detail}</div> : null}</>])} />
          </section>

          <section className="panel">
            <h2>服務與連接埠</h2>
            <Table head={['項目', '單元／映像', '狀態', '說明']} rows={services.map((s: any) => [s.name, <span className="mono">{s.unit || '—'}</span>, <St s={s.status} />, s.detail || ''])} />
            <Table head={['連接埠', '監聽位址', '狀態', '說明']} rows={(rep.ports || []).map((p: any) => [p.name, <span className="mono">{(p.addrs || []).join('、') || '—'}</span>, <St s={p.status} />, p.detail])} />
          </section>

          <section className="panel">
            <h2>版本對照</h2>
            {rep.versions ? <p><St s={rep.versions.status} />　{rep.versions.detail}</p> : null}
            <Table head={['來源', '版本', '讀取位置']} rows={((rep.versions && rep.versions.rows) || []).map((r: any) => [r.name, r.value || '（讀不到）', <span className="mono">{r.note || r.source}</span>])} />
          </section>

          {rep.mounts && rep.mounts.length ? (
            <section className="panel">
              <details>
                <summary>容器掛載來源（{rep.mounts.length} 項）</summary>
                <Table head={['容器', '主機路徑', '容器內路徑', '讀寫', '磁碟類別']} rows={rep.mounts.map((m: any) => [
                  m.container, <span className="mono">{m.source}</span>, <span className="mono">{m.target}</span>, m.rw ? '讀寫' : '唯讀', m.storage_label])} />
              </details>
            </section>
          ) : null}

          <section className="panel">
            <h2>自我檢查（doctor）</h2>
            {rep.doctor ? (
              <>
                <p className="dim">檢查時間 {dateTime(rep.doctor.time)}{rep.doctor.carried ? '（沿用上次完整盤點的結果；按「完整盤點」重新檢查）' : ''}</p>
                <CheckList items={rep.doctor.items || []} compact />
              </>
            ) : <p className="dim">快速盤點不含自我檢查；按「完整盤點（含自我檢查）」執行。</p>}
          </section>

          {rep.hostcheck ? (
            <section className="panel">
              <h2>主機檢查（每 10 分鐘）</h2>
              <p className="dim">最後更新 {dateTime(rep.hostcheck.time)}</p>
              <CheckList items={rep.hostcheck.checks || []} compact />
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/** dev.61：主機板與 BIOS（讀不到寫「未知」，不猜測） */
export function boardText(b: Record<string, string | null>): string {
  const v = (k: string) => (b && b[k]) || '未知';
  return `${v('board_vendor')} ${v('board_name')}（版本 ${v('board_version')}）；BIOS：${v('bios_vendor')} ${v('bios_version')}`;
}
