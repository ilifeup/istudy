/**
 * PC 資訊（Super）：由 Electron 主程序取得這台電腦的資訊；可複製，或上傳到 Server 填入 Client 檢查清單的自動欄位。
 */
import React from 'react';
import { bridge } from '../bridge';
import { Btn, LoadState, Msg, PageHead, useApp, useLoad } from '../core';

function rows(info: Record<string, any>): [string, string][] {
  const out: [string, string][] = [
    ['作業系統', info.os],
    ['架構', info.arch],
    ['CPU', `${info.cpu}（${info.cpu_cores} 執行緒）`],
    ['記憶體', `${info.memory_gb} GB`],
    ['主機名稱', info.hostname],
  ];
  // dev.61：主機板與 BIOS（讀不到寫「未知」；不顯示序號）
  const b = info.board || {};
  const v = (k: string) => b[k] || '未知';
  out.push(['主機板', `${v('board_vendor')} ${v('board_name')}`], ['主機板版本', v('board_version')], ['BIOS', `${v('bios_vendor')} ${v('bios_version')}`]);
  (info.gpu || []).forEach((g: any, i: number) => out.push([`顯示卡 ${i + 1}`, `${g.name || '（未知）'}（${g.vendor_id}:${g.device_id}${g.driver ? '，' + g.driver : ''}）`]));
  for (const n of info.network || []) out.push([`網卡 ${n.name}`, `${n.ip}　${n.mac}`]);
  out.push(['實體網卡 MAC', (info.macs || []).join('、') || '—']);
  (info.screens || []).forEach((s: any, i: number) => out.push([`螢幕 ${i + 1}`, `${s.width}×${s.height}（縮放 ${s.scale}）`]));
  if (info.app) {
    out.push(['App 版別與版本', info.app.label]);
    out.push(['Electron／Chromium', `${info.app.electron}／${info.app.chrome}`]);
  }
  return out;
}

export function PcInfoPage(): any {
  const { toast, info: appInfo } = useApp();
  const r = useLoad(() => bridge.pcInfo(), []);
  const text = () => (r.data ? rows(r.data).map(([k, v]) => `${k}：${v}`).join('\n') : '');
  const copy = async () => {
    await bridge.copy(text());
    toast('已複製到剪貼簿', 'ok', 2000);
  };
  const upload = async () => {
    const x = await bridge.uploadPcInfo();
    if (!x.ok) return toast(x.error || '上傳失敗', 'error');
    toast(x.device_id ? '已上傳，並對應到 WOL 設備清單中的這台電腦' : '已上傳（這台電腦尚未登記在 WOL 設備清單）', 'ok', 5000);
  };
  // dev.55 PC-01：在 Server App（Ubuntu Server 本機）時不顯示「上傳到 Server」；依 App 種類判斷，不看 IP
  const isServer = appInfo.kind === 'server';
  return (
    <div className="page narrow">
      <PageHead title="PC 資訊">
        <Btn onClick={copy} disabled={!r.data}>複製</Btn>
        {isServer ? <Btn onClick={r.reload}>重新讀取</Btn> : <Btn kind="primary" onClick={upload} disabled={!r.data}>上傳到 Server</Btn>}
      </PageHead>
      <LoadState loading={r.loading} error={r.error} reload={r.reload} />
      {r.data ? (
        <section className="panel">
          <table className="grid kv">
            <tbody>{rows(r.data).map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody>
          </table>
        </section>
      ) : null}
      {isServer ? (
        <Msg kind="info">這是 Server 主機本身的資訊（Server App），只供檢視與複製；Server 不需要上傳到自己。</Msg>
      ) : (
        <Msg kind="info">
          心跳已自動帶回電腦名稱、作業系統與 App 版本；完整 PC 資訊只在按「上傳到 Server」時才送出，
          會填入「Client 硬體／OS 通用檢查清單」中對應這台電腦的自動欄位。
        </Msg>
      )}
    </div>
  );
}
