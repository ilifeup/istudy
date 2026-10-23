/**
 * WOL 管理（第 09 章）：設備清單、待登記設備、喚醒與狀態。
 * 喚醒封包由 Server Agent 送出；App 只傳設備 ID。畫面每 15 秒重讀。
 * dev.55：三種資訊分開——心跳回報（Client 主動回報）、檢測（Server 主動 ping LAN IP，進入此頁時觸發）、
 * 掃描 LAN（手動，只掃 Server 所在網段且最多 /24，結果勾選才加入）。LAN IP 與 WOL 廣播位址是兩個欄位。
 */
import React, { useEffect, useRef, useState } from 'react';
import { api, errText } from '../api';
import { Btn, Field, LoadState, Modal, Msg, PageHead, Tabs, useApp, useInterval, useLoad } from '../core';
import { deviceStateText } from '../components/Login';
import { shortDateTime } from '../format';

const CHANNEL_TEXT: Record<string, string> = { win7: 'Win7', win10: 'Win10', win11: 'Win11', ubuntu: 'Ubuntu' };
// dev.62：新增與登記時預設「✓ 啟用」（停用的電腦不會出現在登入畫面的喚醒清單）；掃描 LAN 一次加入多台時仍預設停用
const BLANK = { id: null as number | null, device_code: '', name: '', os_hint: '', mac: '', lan_ip: '', broadcast: '', port: 9,
  send_count: 3, cooldown_sec: 60, enabled: true, sort_order: 0 };

/** dev.62：設備的新增、修改、刪除、登記、掃描只有 Super（Server API 也一樣限制）；管理員只看「喚醒與狀態」。 */
export function WolPage(): any {
  const { account } = useApp();
  const isSuper = !!account && account.role === 'super';
  const [tab, setTab] = useState('status');
  const devices = useLoad(() => api.get<{ devices: any[]; server_lan?: any }>('/api/wol/devices'), []);
  const pending = useLoad(() => (isSuper ? api.get<{ devices: any[] }>('/api/devices/pending') : Promise.resolve({ devices: [] as any[] })), [isSuper]);
  useInterval(() => { devices.reload(); pending.reload(); }, 15000);
  const pendingCount = pending.data ? pending.data.devices.length : 0;
  return (
    <div className="page">
      <PageHead title="WOL 管理" />
      {isSuper ? (
        <Tabs value={tab} onChange={setTab} tabs={[
          { id: 'status', label: '喚醒與狀態' },
          { id: 'devices', label: '設備清單' },
          { id: 'pending', label: `待登記設備${pendingCount ? `（${pendingCount}）` : ''}` },
        ]} />
      ) : <p className="dim">管理員可以查看狀態與喚醒電腦；設備的新增、修改、刪除、登記與啟用只有 Super 可以設定。</p>}
      {tab === 'status' || !isSuper ? <StatusTab d={devices} canEdit={isSuper} /> : null}
      {isSuper && tab === 'devices' ? <DevicesTab d={devices} /> : null}
      {isSuper && tab === 'pending' ? <PendingTab p={pending} lan={devices.data ? devices.data.server_lan : null} onDone={() => { pending.reload(); devices.reload(); }} /> : null}
    </div>
  );
}

function versionText(d: any): any {
  if (!d.version_label) return <span className="dim">—</span>;
  return (
    <>
      {d.version_label}
      {d.channel_mismatch ? <div className="warn-text">⚠ 版別不符（作業系統應裝 {CHANNEL_TEXT[d.expected_channel] || d.expected_channel} 版）</div> : null}
    </>
  );
}

/** 廣播位址看起來像單一主機 IP（不是 x.x.x.255 或 255.255.255.255）：只提示人工確認，不自動改寫。 */
function looksLikeHostIp(b: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(b || '') && b !== '255.255.255.255' && !b.endsWith('.255');
}

/** 檢測欄：Server 主動 ping 的結果。未回應只代表沒有回 ping（可能關機、防火牆或網路），不判定為關機。 */
export function probeText(x: any): string {
  if (!x.lan_ip && !x.last_ip) return '— 未填 LAN IP';
  if (!x.probe_at) return '… 尚未檢測';
  if (x.probe_ok === true) return `✓ 有回應（${shortDateTime(x.probe_at)}）`;
  if (x.probe_ok === false) return `○ 未回應（${shortDateTime(x.probe_at)}）`;
  return '— 無法 ping（見 Log）';
}

function StatusTab(props: { d: any; canEdit: boolean }): any {
  const { toast } = useApp();
  const { d } = props;
  const [probing, setProbing] = useState(false);
  const probe = async (manual: boolean) => {
    try {
      const r = await api.post('/api/wol/probe', {});
      if (r.skipped) {
        if (manual) toast(`剛剛檢測過，${r.retry_after} 秒後可再檢測`, 'warn');
        return;
      }
      setProbing(true);
      window.setTimeout(() => { d.reload(); setProbing(false); }, 5000);
    } catch (e) {
      if (manual) toast(errText(e), 'error');
    }
  };
  // 進入此頁時自動檢測一次（Server 端 30 秒內不重複）
  useEffect(() => { void probe(false); }, []);
  const wake = async (body: Record<string, any>) => {
    try {
      const r = await api.post('/api/wol/wake', body);
      if (r.sent.length) toast(`已送出喚醒封包（${r.sent.length} 台），等待開機回報…`, 'ok');
      if (r.cooling.length) toast(`${r.cooling.length} 台剛喚醒過，冷卻中`, 'warn');
      d.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  // dev.62：未啟用的電腦不會出現在登入畫面的喚醒清單；Super 可以在這裡直接啟用
  const enable = async (x: any) => {
    try {
      await api.patch(`/api/wol/devices/${x.id}`, { enabled: true });
      toast(`已啟用「${x.name}」：登入畫面的喚醒清單會顯示這台`, 'ok');
      d.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const list = d.data ? d.data.devices : [];
  const enabled = list.filter((x: any) => x.enabled);
  const disabled = list.length - enabled.length;
  return (
    <section className="panel">
      <div className="row">
        <span className="dim">心跳回報＝Client App 主動回報（● 在線＝3 分鐘內）；檢測＝Server ping LAN IP，未回應不代表已關機</span>
        <span className="grow" />
        {probing ? <span className="dim">… 檢測中</span> : null}
        <Btn small onClick={() => void probe(true)}>重新檢測</Btn>
        <Btn small onClick={d.reload}>重新整理</Btn>
        <Btn kind="primary" disabled={!enabled.length} onClick={() => wake({ all: true })}>全部喚醒</Btn>
      </div>
      <LoadState loading={d.loading && !d.data} error={d.error} reload={d.reload} />
      {d.data && !list.length ? <Msg kind="info">{props.canEdit ? '尚未登記任何電腦；已裝好 Client App 並開機的電腦會出現在「待登記設備」，也可以在「設備清單」掃描 LAN' : '尚未登記任何電腦（由 Super 設定）'}</Msg> : null}
      {disabled > 0 ? <Msg kind="warn">有 {disabled} 台未啟用喚醒：不會出現在登入畫面的「喚醒電腦」清單，也不能喚醒{props.canEdit ? '；按該列的「啟用」即可' : '（請 Super 啟用）'}。</Msg> : null}
      {list.length ? (
        <table className="grid">
          <thead><tr><th>電腦</th><th>心跳回報</th><th>檢測（Server ping）</th><th>版別與版本</th><th>目前帳號</th><th>操作</th></tr></thead>
          <tbody>
            {list.map((x: any) => (
              <tr key={x.id}>
                <td>{x.name}{x.device_code ? <span className="dim">（{x.device_code}）</span> : null}<div className="dim">{x.os_hint || x.last_os}</div></td>
                <td>{deviceStateText(x)}</td>
                <td>{probeText(x)}<div className="dim">{x.lan_ip || (x.last_ip ? `${x.last_ip}（心跳回報的 IP）` : '')}</div></td>
                <td>{versionText(x)}</td>
                <td>{x.online ? (x.last_account || '（未登入）') : '—'}</td>
                <td>{x.enabled ? <Btn small onClick={() => wake({ device_id: x.id })}>喚醒</Btn> : <>
                  <span className="dim">✗ 未啟用（登入畫面不顯示）</span>{props.canEdit ? <Btn small onClick={() => enable(x)}>啟用</Btn> : null}</>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function DevicesTab(props: { d: any }): any {
  const { toast } = useApp();
  const { d } = props;
  const [edit, setEdit] = useState<any | null>(null);
  const [scan, setScan] = useState(false);
  const list = d.data ? d.data.devices : [];
  const lan = d.data ? d.data.server_lan : null;
  const remove = async (x: any) => {
    if (!window.confirm(`刪除設備「${x.name}」？`)) return;
    try {
      await api.del(`/api/wol/devices/${x.id}`);
      d.reload();
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  return (
    <section className="panel">
      <div className="row">
        <span className="dim">只有「✓ 啟用」的設備會出現在登入畫面的喚醒清單；新增、登記時預設啟用，掃描 LAN 加入的預設停用</span>
        <span className="grow" />
        <Btn onClick={() => setScan(true)}>掃描 LAN 裝置</Btn>
        <Btn kind="primary" onClick={() => setEdit({ ...BLANK })}>新增設備</Btn>
      </div>
      <LoadState loading={d.loading && !d.data} error={d.error} reload={d.reload} />
      {list.length ? (
        <table className="grid small">
          <thead><tr><th>順序</th><th>代號</th><th>名稱</th><th>作業系統</th><th>MAC</th><th>LAN IP</th><th>WOL 廣播位址／埠</th><th>次數／冷卻</th><th>啟用</th><th>最後回報</th><th>版別與版本</th><th></th></tr></thead>
          <tbody>
            {list.map((x: any) => (
              <tr key={x.id}>
                <td>{x.sort_order}</td><td>{x.device_code || '—'}</td><td>{x.name}</td><td>{x.os_hint || x.last_os || '—'}</td>
                <td className="mono-ish">{x.mac}</td><td>{x.lan_ip || '—'}</td>
                <td>{x.broadcast_auto ? `自動（${x.broadcast_effective}）` : x.broadcast}:{x.port}
                  {looksLikeHostIp(x.broadcast) ? <div className="warn-text">⚠ 這看起來是單一電腦的 IP，不是廣播位址；請確認（電腦 IP 應填在 LAN IP）</div> : null}</td><td>{x.send_count} 次／{x.cooldown_sec} 秒</td>
                <td>{x.enabled ? '✓ 啟用' : '✗ 停用'}</td>
                <td>{x.last_seen_at ? shortDateTime(x.last_seen_at) : '—'}{x.last_pc_name ? <div className="dim">{x.last_pc_name}</div> : null}</td>
                <td>{versionText(x)}</td>
                <td className="actions"><Btn small onClick={() => setEdit({ ...x })}>編輯</Btn><Btn small kind="danger" onClick={() => remove(x)}>刪除</Btn></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {edit ? <DeviceForm dev={edit} lan={lan} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); d.reload(); }} /> : null}
      {scan ? <ScanPanel lan={lan} onClose={() => setScan(false)} onAdded={() => d.reload()} /> : null}
    </section>
  );
}

function DeviceForm(props: { dev: any; lan?: any; register?: boolean; onClose: () => void; onSaved: () => void }): any {
  const [f, setF] = useState<any>({ ...props.dev, lan_ip: props.dev.lan_ip || '', broadcast: props.dev.broadcast || '' });
  const [manual, setManual] = useState<boolean>(!!props.dev.broadcast);
  const autoText = props.lan && props.lan.broadcast ? props.lan.broadcast : '依 Server 網段自動計算';
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const save = async () => {
    setErr(null);
    const body: Record<string, any> = {
      device_code: f.device_code || null, name: f.name, os_hint: f.os_hint, lan_ip: (f.lan_ip || '').trim(),
      broadcast: manual ? (f.broadcast || '').trim() : '', port: Number(f.port),
      send_count: Number(f.send_count), cooldown_sec: Number(f.cooldown_sec), enabled: !!f.enabled, sort_order: Number(f.sort_order),
    };
    if (!props.register) body.mac = f.mac;
    try {
      if (props.register) await api.post(`/api/devices/${f.id}/register`, body);
      else if (f.id) await api.patch(`/api/wol/devices/${f.id}`, body);
      else await api.post('/api/wol/devices', body);
      props.onSaved();
    } catch (e) {
      setErr(errText(e));
    }
  };
  const title = props.register ? `登記設備：${f.last_pc_name || f.mac}` : f.id ? `編輯設備：${f.name}` : '新增設備';
  return (
    <Modal title={title} onClose={props.onClose} wide footer={<>
      <Btn kind="primary" onClick={save}>{props.register ? '登記' : '儲存'}</Btn><Btn onClick={props.onClose}>取消</Btn>
    </>}>
      <div className="form-grid">
        <Field label="名稱"><input value={f.name} onChange={(e: any) => set('name', e.target.value)} autoFocus /></Field>
        <Field label="設備代號（可空白）" hint="1～20 字英數字"><input value={f.device_code || ''} onChange={(e: any) => set('device_code', e.target.value)} /></Field>
        <Field label="作業系統"><input value={f.os_hint || ''} onChange={(e: any) => set('os_hint', e.target.value)} placeholder={f.last_os || '例如 Windows 10'} /></Field>
        <Field label="MAC">{props.register
          ? <input value={f.mac} disabled />
          : <input value={f.mac} onChange={(e: any) => set('mac', e.target.value)} placeholder="aa:bb:cc:dd:ee:ff" />}</Field>
        <Field label="LAN IP" hint="這台電腦在區域網路的 IP，例如 10.3.3.21（Server 檢測用）">
          <input value={f.lan_ip} onChange={(e: any) => set('lan_ip', e.target.value)} placeholder="10.3.3.x" />
        </Field>
        <Field label="WOL 廣播位址" hint="喚醒封包送往的位址，不是電腦的 IP；建議用自動">
          <select value={manual ? 'manual' : 'auto'} onChange={(e: any) => setManual(e.target.value === 'manual')}>
            <option value="auto">自動（{autoText}）</option><option value="manual">手動指定</option>
          </select>
          {manual ? <input value={f.broadcast} onChange={(e: any) => set('broadcast', e.target.value)} placeholder="例如 10.3.3.255" aria-label="手動 WOL 廣播位址" /> : null}
          {manual && looksLikeHostIp(f.broadcast) ? <small className="warn-text">⚠ 這看起來是單一電腦的 IP；電腦的 IP 請填在「LAN IP」，廣播位址通常是 x.x.x.255 或選「自動」</small> : null}
        </Field>
        <Field label="連接埠">
          <select value={String(f.port)} onChange={(e: any) => set('port', Number(e.target.value))}><option value="9">9</option><option value="7">7</option></select>
        </Field>
        <Field label="送出次數">
          <select value={String(f.send_count)} onChange={(e: any) => set('send_count', Number(e.target.value))}>
            {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </Field>
        <Field label="冷卻秒數"><input type="number" min={0} max={3600} value={f.cooldown_sec} onChange={(e: any) => set('cooldown_sec', e.target.value)} /></Field>
        <Field label="顯示順序"><input type="number" value={f.sort_order} onChange={(e: any) => set('sort_order', e.target.value)} /></Field>
        <Field label="是否啟用">
          <select value={f.enabled ? '1' : '0'} onChange={(e: any) => set('enabled', e.target.value === '1')}><option value="0">✗ 停用</option><option value="1">✓ 啟用</option></select>
        </Field>
      </div>
      {props.register && f.all_macs && f.all_macs.length > 1 ? <p className="dim">這台電腦回報的所有網卡：{f.all_macs.join('、')}</p> : null}
      {err ? <Msg kind="error">{err}</Msg> : null}
    </Modal>
  );
}

function PendingTab(props: { p: any; lan?: any; onDone: () => void }): any {
  const { p } = props;
  const [reg, setReg] = useState<any | null>(null);
  const list = p.data ? p.data.devices : [];
  return (
    <section className="panel">
      <p className="dim">收到未登記 MAC 的心跳時會自動列在這裡。按「登記」補上名稱與 LAN IP 即可，不必手動輸入 MAC。</p>
      <LoadState loading={p.loading && !p.data} error={p.error} reload={p.reload} />
      {p.data && !list.length ? <Msg kind="info">目前沒有待登記的電腦</Msg> : null}
      {list.length ? (
        <table className="grid">
          <thead><tr><th>電腦名稱</th><th>MAC</th><th>作業系統</th><th>App 版別與版本</th><th>最後回報</th><th></th></tr></thead>
          <tbody>
            {list.map((x: any) => (
              <tr key={x.id}>
                <td>{x.last_pc_name || '—'}</td><td>{x.mac}</td><td>{x.last_os || '—'}</td><td>{versionText(x)}</td>
                <td>{shortDateTime(x.last_seen_at)}</td>
                <td><Btn small kind="primary" onClick={() => setReg({ ...BLANK, ...x, broadcast: '', name: x.last_pc_name || '', os_hint: x.last_os || '', lan_ip: x.lan_ip || x.last_ip || '' })}>登記</Btn></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {reg ? <DeviceForm dev={reg} lan={props.lan} register onClose={() => setReg(null)} onSaved={() => { setReg(null); props.onDone(); }} /> : null}
    </section>
  );
}

/** 掃描 LAN（dev.55 WOL-01）：手動按下才掃；只掃 Server 所在網段（最多 /24）；結果勾選後才加入設備清單（預設停用）。 */
function ScanPanel(props: { lan: any; onClose: () => void; onAdded: () => void }): any {
  const { toast } = useApp();
  const [job, setJob] = useState<number | null>(null);
  const [res, setRes] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const poll = async (id: number) => {
    try {
      const r = await api.get(`/api/wol/scan/${id}`);
      if (r.status === 'done') { setRes(r.result); setJob(null); return; }
      if (r.status === 'failed' || r.status === 'cancelled') { setErr(r.error || '掃描失敗'); setJob(null); return; }
      timer.current = window.setTimeout(() => void poll(id), 1500);
    } catch (e) {
      setErr(errText(e));
      setJob(null);
    }
  };
  const start = async () => {
    setErr(null); setRes(null); setPick({});
    try {
      const r = await api.post('/api/wol/scan', {});
      setJob(r.job_id);
      void poll(r.job_id);
    } catch (e) {
      setErr(errText(e));
    }
  };
  const selectable = (h: any) => !!h.mac && !h.known_device_id;
  const toggle = (h: any) => {
    const next = { ...pick };
    if (next[h.ip] !== undefined) delete next[h.ip]; else next[h.ip] = h.name || '';
    setPick(next);
  };
  const add = async () => {
    setBusy(true);
    const hosts = (res ? res.hosts : []).filter((h: any) => pick[h.ip] !== undefined);
    let ok = 0;
    const fails: string[] = [];
    for (const h of hosts) {
      try {
        await api.post('/api/wol/devices', { name: (pick[h.ip] || '').trim() || h.ip, mac: h.mac, lan_ip: h.ip, broadcast: '', enabled: false });
        ok++;
      } catch (e) {
        fails.push(`${h.ip}：${errText(e)}`);
      }
    }
    setBusy(false);
    if (ok) { toast(`已加入 ${ok} 台（預設停用，確認後再啟用）`, 'ok'); props.onAdded(); }
    if (fails.length) setErr(fails.join('；'));
    else props.onClose();
  };
  const hosts = res ? res.hosts : [];
  const n = Object.keys(pick).length;
  return (
    <Modal title="掃描 LAN 裝置" onClose={props.onClose} wide footer={<>
      <Btn kind="primary" onClick={add} disabled={!n || busy}>加入勾選的 {n} 台</Btn>
      <Btn onClick={props.onClose}>關閉</Btn>
    </>}>
      <div className="stack">
        <p className="dim">
          範圍：Server 所在網段{props.lan && props.lan.network ? `（${props.lan.network}，網卡 ${props.lan.iface}）` : ''}，最多 254 台；
          每次掃描約數秒，60 秒內只能掃一次；不會自動加入。MAC 從 Server 的 ARP 表讀取，名稱用 DNS 反查，取不到就空白。
        </p>
        <div className="row">
          <Btn kind="primary" onClick={start} disabled={job !== null}>{res ? '重新掃描' : '開始掃描'}</Btn>
          {job !== null ? <span className="dim">… 掃描中</span> : null}
        </div>
        {err ? <Msg kind="error">{err}</Msg> : null}
        {res ? (
          <>
            <p>已掃描 {res.network}（{res.scanned} 個位址，{res.seconds} 秒）：找到 {hosts.length} 台{res.partial ? '；⚠ 結果不完整' : ''}</p>
            {(res.notes || []).map((t: string, i: number) => <Msg key={i} kind="warn">{t}</Msg>)}
            {hosts.length ? (
              <table className="grid small">
                <thead><tr><th>選取</th><th>IP</th><th>MAC</th><th>名稱（可修改）</th><th>ping</th><th>清單狀態</th></tr></thead>
                <tbody>
                  {hosts.map((h: any) => (
                    <tr key={h.ip}>
                      <td>{selectable(h)
                        ? <label className="check"><input type="checkbox" checked={pick[h.ip] !== undefined} onChange={() => toggle(h)} />{pick[h.ip] !== undefined ? '☑' : '☐'}</label>
                        : <span className="dim">—</span>}</td>
                      <td>{h.ip}</td>
                      <td className="mono-ish">{h.mac || <span className="dim">（取不到）</span>}</td>
                      <td>{pick[h.ip] !== undefined
                        ? <input value={pick[h.ip]} onChange={(e: any) => setPick({ ...pick, [h.ip]: e.target.value })} placeholder={h.ip} aria-label={`${h.ip} 名稱`} />
                        : (h.name || <span className="dim">（無）</span>)}</td>
                      <td>{h.ping === true ? '✓ 有回應' : h.ping === false ? '○ 未回應' : '—'}</td>
                      <td>{h.known_device_id ? (h.known_registered ? `已在清單：${h.known_name}` : '待登記（請到「待登記設備」）') : h.mac ? '新裝置' : '無 MAC，不能加入'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </>
        ) : null}
      </div>
    </Modal>
  );
}
