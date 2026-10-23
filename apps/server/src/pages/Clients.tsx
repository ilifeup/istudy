/**
 * dev.61「Client 版本與更新」（Super 專用）：哪台電腦現在是什麼版本、更新到哪一步（Client 心跳回報），
 * Server 上目前發布的版本，以及「發布新版 Client」（istudyctl clients-publish，之後各台自動更新，不用逐台安裝）。
 */
import React from 'react';
import { api, errText } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, LoadState, Msg, PageHead, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime } from '../../../../packages/ui/src/format';
import { HostRunView, lastDone, useHostRun } from '../srv';

const VERSION_TEXT: Record<string, string> = { current: '✓ 最新', outdated: '⬆ 尚未更新', unknown: '？ 未回報版本' };

/** Client 回報的更新狀態 → 文字（符號＋文字，不只靠顏色）。 */
export function updateText(u: any, channel: string): string {
  if (!u || !u.state) return '？ 未回報（dev.61 以前的 Client）';
  const v = u.available ? ` ${u.available}` : '';
  switch (u.state) {
    case 'up_to_date': return '✓ 已是最新';
    case 'checking': return '… 檢查中';
    case 'downloading': return `⬇ 下載中${v}${typeof u.percent === 'number' ? ` ${u.percent}%` : ''}`;
    case 'downloaded': return `⏳ 已下載${v}：沒有人登入時自動安裝`;
    case 'installing': return `… 安裝中${v}（App 會自動重新開啟）`;
    case 'installed': return `✓ 已安裝${v}`;
    case 'failed': return `✗ 更新失敗${u.error ? '：' + u.error : ''}`;
    case 'manual': return channel === 'ubuntu' ? '－ 沒有自動更新服務（請手動安裝一次 dev.61 以後的版本）' : `－ 手動更新${u.error ? '（' + u.error + '）' : ''}`;
    case 'disabled': return '－ 開發版（不自動更新）';
    default: return u.state;
  }
}

export function ClientsPage(): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<any>('/api/admin/client-updates'), []);
  const pub = useLoad(() => bridge.host.publishedClients(), []);
  const h = useHostRun();
  useInterval(() => r.reload(), 30000);
  const pubBy: Record<string, any> = {};
  for (const p of pub.data || []) pubBy[p.channel] = p;
  const publish = async () => {
    const pick = await bridge.host.pickClients();
    if (!pick) return;
    if ('error' in pick) {
      toast(pick.error, 'error');
      return;
    }
    try {
      await h.start('clients-publish', { dir: pick.path });
      pub.reload();
      r.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const done = lastDone(h.run);
  const devices: any[] = r.data ? r.data.devices : [];
  return (
    <div className="page">
      <PageHead title="Client 版本與更新">
        <Btn onClick={() => { r.reload(); pub.reload(); }}>重新整理</Btn>
        <Btn kind="primary" disabled={h.running} onClick={() => void publish()}>發布新版 Client…</Btn>
      </PageHead>
      <p className="dim">Client 會自己到這台 Server 下載新版：Windows（Win7／Win10／Win11）開啟時與每 6 小時檢查，
        Ubuntu 由系統服務每 30 分鐘檢查。下載好之後，<strong>沒有人登入滿 2 分鐘就自動安裝並重新開啟</strong>；有人登入時等登出、關閉 App 或下次開機。
        更新新版時，只要按「發布新版 Client…」選原始碼包的 <code>dist/clients</code> 資料夾（需要 Ubuntu 系統密碼），不用到每台電腦安裝。</p>
      <Msg kind="info">Ubuntu Client 從 dev.61 開始才有自動更新服務：dev.60 以前的 Ubuntu Client 要先手動安裝一次（sudo apt install ./istudy-client-ubuntu_…_amd64.deb）。</Msg>
      {h.run ? <section className="panel"><h2>發布結果</h2><HostRunView run={h.run} hideSteps />
        {done ? <Msg kind={done.status === 'ok' ? 'ok' : done.status === 'warn' ? 'warn' : 'error'}>{done.message}</Msg> : null}</section> : null}
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data ? (
        <table className="grid small">
          <thead><tr><th>版別</th><th>這版 Server 對應的版本</th><th>Server 上已發布</th><th>電腦數</th><th>已是最新</th><th>尚未更新</th><th>在線</th></tr></thead>
          <tbody>{r.data.channels.map((c: any) => {
            const p = pubBy[c.channel];
            return (
              <tr key={c.channel}>
                <td>{c.label}</td><td>{c.latest || '—'}</td>
                <td>{p ? (p.version ? <>{p.version}{p.version !== c.latest ? <div className="warn-text">⚠ 與這版 Server 對應的版本不同</div> : null}</> : <span className="dim">{p.note || '尚未發布'}</span>) : <span className="dim">…</span>}</td>
                <td>{c.total}</td><td>{c.current}</td><td>{c.outdated ? `⬆ ${c.outdated}` : 0}</td><td>{c.online}</td>
              </tr>
            );
          })}</tbody>
        </table>
      ) : null}
      <h2>各台電腦</h2>
      {r.data && !devices.length ? <Msg kind="info">還沒有 Client 電腦回報。Client 開啟後每分鐘回報一次版本與更新狀態。</Msg> : null}
      {devices.length ? (
        <table className="grid small">
          <thead><tr><th>電腦</th><th>版別</th><th>目前版本</th><th>版本狀態</th><th>更新狀態</th><th>連線</th><th>IP</th></tr></thead>
          <tbody>{devices.map((d) => (
            <tr key={d.id}>
              <td>{d.name || '（未命名）'}{d.device_code ? <div className="dim">{d.device_code}</div> : null}{!d.registered ? <div className="dim">待登記（WOL 管理）</div> : null}</td>
              <td>{d.channel_label || '—'}</td>
              <td className="nowrap">{d.version || '—'}</td>
              <td className="nowrap">{VERSION_TEXT[d.version_state] || d.version_state}</td>
              <td>{updateText(d.update, d.channel)}{d.update && d.update.at ? <div className="dim">{dateTime(d.update.at)}</div> : null}</td>
              <td className="nowrap">{d.online ? '● 在線' : '○ 離線'}{d.last_seen_at ? <div className="dim">最後回報 {dateTime(d.last_seen_at)}</div> : null}</td>
              <td className="nowrap">{d.ip || '—'}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
    </div>
  );
}
