/**
 * 主機維護（第 10、17、18 章）：從 USB 更新、重新啟動服務、設為備份磁碟、主機檢查、重新開啟首次設定精靈。
 * 需要 root 的按鈕由 Electron 主程序以 pkexec 執行 /usr/local/sbin/istudyctl（固定子指令），跳出 Ubuntu 系統密碼視窗。
 */
import React, { useState } from 'react';
import { errText } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, LoadState, Msg, PageHead, useApp, useLoad } from '../../../../packages/ui/src/core';
import { size } from '../../../../packages/ui/src/format';
import { eventsOf, HostRunView, useHostRun } from '../srv';

const ROOT_NOTE = '需要 root 的按鈕會跳出 Ubuntu 系統密碼視窗；輸入一次後 5 分鐘內不再詢問。';

export function BackupDiskPicker(props: { onDone?: () => void }): any {
  const list = useHostRun();
  const set = useHostRun();
  const [err, setErr] = useState<string | null>(null);
  const disks: any[] = (eventsOf(list.run, 'disks')[0] || {}).disks || [];
  const scan = async () => {
    setErr(null);
    set.reset();
    try {
      await list.start('list-disks');
    } catch (e) {
      setErr(errText(e));
    }
  };
  const choose = async (d: any) => {
    if (!window.confirm(`把「${d.label || d.name}」（${size(d.size)}，${d.fstype}）設為備份磁碟？\n會寫入 /etc/fstab（nofail）並掛載到 /srv/istudy/backup，不會格式化。`)) return;
    setErr(null);
    try {
      const r = await set.start('backup-disk', { uuid: d.uuid });
      if (r && r.exit && r.exit.code === 0) props.onDone?.();
    } catch (e) {
      setErr(errText(e));
    }
  };
  return (
    <div className="stack">
      <div className="row">
        <Btn onClick={scan} disabled={list.running || set.running}>列出可用的磁碟</Btn>
        <span className="dim">只列出已連接、不在 /Media 上、不是系統碟的分割區</span>
      </div>
      {err ? <Msg kind="error">{err}</Msg> : null}
      {list.run && !list.run.exit ? <p className="dim">… 讀取磁碟清單</p> : null}
      {list.run && list.run.exit && list.run.exit.error ? <Msg kind="error">{list.run.exit.error}</Msg> : null}
      {list.run && list.run.exit && !list.run.exit.error && !disks.length ? <Msg kind="warn">沒有找到可用的磁碟：請接上外接硬碟（已格式化為 ext4、xfs、exFAT 等）後再試</Msg> : null}
      {disks.length ? (
        <table className="grid small">
          <thead><tr><th>裝置</th><th>標籤／型號</th><th>格式</th><th>大小</th><th>目前掛載</th><th></th></tr></thead>
          <tbody>{disks.map((d) => (
            <tr key={d.uuid}>
              <td>{d.name}{d.transport ? `（${d.transport.toUpperCase()}）` : ''}</td><td>{d.label || '—'}<div className="dim">{d.model}</div></td>
              <td>{d.fstype}</td><td>{size(d.size)}</td><td>{d.mountpoint || '—'}</td>
              <td><Btn small kind="primary" disabled={set.running} onClick={() => choose(d)}>設為備份磁碟</Btn></td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
      <HostRunView run={set.run} hideSteps />
    </div>
  );
}

export function HostPage(): any {
  const { go } = useApp();
  const state = useLoad(() => bridge.host.state(), []);
  const bundles = useLoad(() => bridge.host.bundles(), []);
  const h = useHostRun();
  const [bundle, setBundle] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const run = async (action: string, opts?: unknown, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setErr(null);
    try {
      await h.start(action, opts);
    } catch (e) {
      setErr(errText(e));
    }
  };
  const list = bundles.data || [];
  const chosen = bundle || (list[0] ? list[0].path : '');
  return (
    <div className="page">
      <PageHead title="主機維護" />
      <Msg kind="info">{ROOT_NOTE}</Msg>
      {state.data && !state.data.hasIstudyctl ? <Msg kind="error">找不到 /usr/local/sbin/istudyctl（請先以離線套件的 install.sh 安裝）</Msg> : null}
      {err ? <Msg kind="error">{err}</Msg> : null}
      <section className="panel">
        <h2>從 USB 更新</h2>
        <p className="dim">驗證套件雜湊 → 預檢 → 備份資料庫 → 載入新映像 → migration → 重新啟動 → 更新 Server App 與 Client 安裝檔 → 自我檢查；任一步失敗會自動復原到更新前的版本。</p>
        <LoadState loading={bundles.loading} error={bundles.error} reload={bundles.reload} />
        {bundles.data && !list.length ? <Msg kind="warn">找不到離線部署套件（istudy-server-…-offline）：請插入 USB 後按「重新尋找」</Msg> : null}
        <div className="row">
          {list.length ? (
            <select value={chosen} onChange={(e: any) => setBundle(e.target.value)} aria-label="離線部署套件">
              {list.map((b) => <option key={b.path} value={b.path}>{b.path}（Server {b.version}）</option>)}
            </select>
          ) : null}
          <Btn onClick={bundles.reload}>重新尋找</Btn>
          <Btn onClick={async () => {
            const r: any = await bridge.host.pickBundle();
            if (r && r.error) setErr(r.error);
            else if (r) { bundles.setData([r, ...list.filter((b) => b.path !== r.path)]); setBundle(r.path); }
          }}>選擇資料夾…</Btn>
          <Btn kind="primary" disabled={!chosen || h.running} onClick={() => run('update', { bundle: chosen }, `從 ${chosen} 更新 Server？\n更新期間 Client 會暫時無法連線（約數分鐘）。`)}>從 USB 更新</Btn>
        </div>
      </section>
      <section className="panel">
        <h2>服務與檢查</h2>
        <div className="row">
          <Btn disabled={h.running} onClick={() => run('restart', undefined, '重新啟動 iStudy 服務？Client 會暫時無法連線約 1 分鐘。')}>重新啟動服務</Btn>
          <Btn disabled={h.running} onClick={() => run('doctor')}>主機檢查（自我檢查）</Btn>
          <Btn disabled={h.running} onClick={() => run('hostcheck')}>立即更新主機狀態</Btn>
          <Btn onClick={() => go('srv-setup')}>重新開啟首次設定精靈</Btn>
          {h.running ? <Btn kind="quiet" onClick={h.cancel}>停止</Btn> : null}
        </div>
        <HostRunView run={h.run} />
      </section>
      <section className="panel">
        <h2>字幕辨識模型</h2>
        <p className="dim">FunASR Paraformer-zh、SenseVoiceSmall（sherpa-onnx int8 版）＋Silero VAD＋CT-Transformer 標點，約 450 MB。
          從 GitHub（k2-fsa/sherpa-onnx）固定網址下載並核對 SHA256；需要 Server 暫時連網。模型授權為 FunASR MODEL_LICENSE，商用與署名條件請自行確認。</p>
        <div className="row">
          <Btn disabled={h.running} onClick={() => run('asr-models')}>查看模型狀態</Btn>
          <Btn kind="primary" disabled={h.running} onClick={() => run('asr-models-fetch', { target: 'all' },
            '下載字幕辨識模型（約 450 MB）？\n需要 Server 能連上 github.com；已安裝的模型不會重新下載。')}>下載 FunASR 模型</Btn>
        </div>
      </section>
      <section className="panel">
        <h2>設為備份磁碟</h2>
        <BackupDiskPicker />
      </section>
      <section className="panel">
        <h2>只能在終端機執行</h2>
        <p className="dim">以下操作刻意不放進 Server App：</p>
        <ul className="plain">
          <li><code>sudo istudyctl reset-super</code>　Super 無法登入時重設密碼</li>
          <li><code>sudo istudyctl restore --file 備份檔 --target prod</code>　覆蓋正式資料庫（一律先 <code>--target test</code> 檢查）</li>
          <li><code>sudo ./install.sh --cli --restore 備份檔</code>　重建主機</li>
        </ul>
      </section>
    </div>
  );
}
