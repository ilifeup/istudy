/**
 * 初始部署畫面（第 17 章 ②）：尚未部署時開啟 Server App 即顯示，取代登入頁。
 * 「開始部署」以 pkexec 執行 istudyctl bootstrap（14 步、可重複執行、從未完成的步驟繼續）；
 * Super 密碼經標準輸入交給 istudyctl，不出現在指令參數、畫面紀錄與報告。
 */
import React, { useEffect, useState } from 'react';
import { errText } from '../../../../packages/ui/src/api';
import { AppInfo, bridge, HealthSnapshot } from '../../../../packages/ui/src/bridge';
import { Btn, Field, Loading, Msg, useLoad } from '../../../../packages/ui/src/core';
import { CHECK_MARK, CHECK_TEXT, CheckList, eventsOf, lastDone, useHostRun } from '../srv';

export const BOOT_STEPS: Record<number, string> = {
  1: '驗證套件', 2: '預檢', 3: '安裝 Docker', 4: '系統設定', 5: '目錄與媒體標記', 6: '產生設定', 7: '載入映像',
  8: '資料庫', 9: '建立 Super', 10: '開機啟動', 11: '啟動服務', 12: 'Client 安裝檔', 13: '自我檢查', 14: '報告',
};

/** 按過「前往登入」後，本次執行期間（含登出後）直接顯示登入頁。 */
let skipForSession = false;

export function BootstrapGate(props: { info: AppInfo; health: HealthSnapshot; children: any }): any {
  const [tick, setTick] = useState(0);
  const st = useLoad(() => bridge.host.state(), [tick]);
  const [skip, setSkipState] = useState(skipForSession);
  const setSkip = (v: boolean) => { skipForSession = v; setSkipState(v); };
  if (!st.data) return <div className="boot"><Loading text="檢查部署狀態" /></div>;
  if (st.data.deployed || skip) return props.children;
  return <BootstrapScreen info={props.info} health={props.health} state={st.data} onRecheck={() => setTick(tick + 1)} onLogin={() => setSkip(true)} />;
}

function BootstrapScreen(props: { info: AppInfo; health: HealthSnapshot; state: any; onRecheck: () => void; onLogin: () => void }): any {
  const bundles = useLoad(() => bridge.host.bundles(), []);
  const h = useHostRun();
  const [bundle, setBundle] = useState('');
  const [su, setSu] = useState({ user: '', pw: '', pw2: '' });
  const [err, setErr] = useState<string | null>(null);
  const [needAccept, setNeedAccept] = useState(false);
  const list = bundles.data || [];
  const chosen = bundle || (list[0] ? list[0].path : '');
  const saved = (props.state.bootstrap && props.state.bootstrap.steps) || {};
  const superDone = saved['9'] && ['ok', 'skip'].includes(saved['9'].status);

  // 合併：上次執行的紀錄（bootstrap.json）＋這次執行的即時進度
  const live = new Map<number, any>();
  for (const s of eventsOf(h.run, 'step')) live.set(s.step, s);
  const rows = Object.entries(BOOT_STEPS).map(([n, name]) => {
    const k = Number(n);
    const l = live.get(k);
    if (l) return { step: k, name, status: l.status, message: l.message || '' };
    const s = saved[n];
    return s ? { step: k, name, status: s.status === 'skip' ? 'skip' : s.status, message: s.message || '', old: true } : { step: k, name, status: 'pending', message: '' };
  });
  const checks = eventsOf(h.run, 'check');
  const done = lastDone(h.run);

  useEffect(() => {
    if (h.run && h.run.exit) {
      setNeedAccept(h.run.exit.code === 3);
      if (h.run.exit.code === 0 && h.run.action === 'bootstrap') props.onRecheck();
    }
  }, [h.run && h.run.exit]);

  const start = async (action: 'bootstrap' | 'bootstrap-check', accept = false) => {
    setErr(null);
    if (action === 'bootstrap' && !superDone) {
      if (!/^[\p{Script=Han}A-Za-z0-9_.\-]{2,80}$/u.test(su.user.trim().normalize('NFC'))) return setErr('請輸入 Super 帳號（2～80 字：中文、半形英文字母、數字，可含 _ . -）');
      if (su.pw.length < 10) return setErr('Super 密碼至少 10 字');
      if (su.pw !== su.pw2) return setErr('兩次輸入的密碼不同');
    }
    try {
      await h.start(action, {
        bundle: chosen || undefined, acceptWarnings: accept,
        ...(action === 'bootstrap' && !superDone ? { superUser: su.user, password: su.pw, password2: su.pw2 } : {}),
      });
    } catch (e) {
      setErr(errText(e));
    }
  };
  const saveReport = async () => {
    if (!h.run) return;
    const lines = h.run.events.map((e) => (e.type === 'event' ? JSON.stringify(e.data) : e.type === 'text' ? e.text : `exit ${e.code} ${e.error || ''}`));
    await bridge.saveFile(`iStudy-bootstrap-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.log`, lines.join('\n') + '\n');
  };

  return (
    <div className="boot">
      <header className="boot-head">
        <span className="seal" aria-hidden="true">無相</span>
        <div>
          <h1>iStudy Server App　初始部署</h1>
          <p className="dim">這台 Server 尚未完成部署。插入離線部署套件（USB）後按「開始部署」。</p>
        </div>
      </header>
      {!props.state.hasIstudyctl ? <Msg kind="error">找不到 /usr/local/sbin/istudyctl：請先在終端機執行 USB 上的 sudo ./install.sh</Msg> : null}
      {props.health.state === 'ok' ? (
        <Msg kind="info" action={<Btn small onClick={props.onLogin}>前往登入</Btn>}>偵測到 iStudy 服務已在本機執行（例如已用終端機部署）</Msg>
      ) : null}
      <section className="panel">
        <div className="row">
          <span>部署套件：</span>
          {list.length ? (
            <select value={chosen} onChange={(e: any) => setBundle(e.target.value)} aria-label="離線部署套件">
              {list.map((b) => <option key={b.path} value={b.path}>{b.path}（Server {b.version}）</option>)}
            </select>
          ) : <span className="warn-text">⚠ 找不到 istudy-server-…-offline（請插入 USB）</span>}
          <Btn small onClick={bundles.reload}>重新尋找</Btn>
          <Btn small onClick={async () => {
            const r: any = await bridge.host.pickBundle();
            if (r && r.error) setErr(r.error);
            else if (r) { bundles.setData([r, ...list.filter((b) => b.path !== r.path)]); setBundle(r.path); }
          }}>選擇資料夾…</Btn>
        </div>
        <table className="grid steps">
          <thead><tr><th>步驟</th><th>狀態</th><th>說明</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.step} className={'st-' + r.status + (r.old ? ' old' : '')}>
                <td>{r.step}. {r.name}</td>
                <td className="nowrap">{CHECK_MARK[r.status] || '·'} {r.old && r.status === 'ok' ? '已完成' : CHECK_TEXT[r.status] || r.status}</td>
                <td>{r.step === 9 && !superDone && !live.get(9) ? (
                  <div className="form-row tight">
                    <Field label="Super 帳號"><input value={su.user} onChange={(e: any) => setSu({ ...su, user: e.target.value })} autoComplete="off" /></Field>
                    <Field label="密碼（至少 10 字）"><input type="password" value={su.pw} onChange={(e: any) => setSu({ ...su, pw: e.target.value })} autoComplete="new-password" /></Field>
                    <Field label="再輸入一次"><input type="password" value={su.pw2} onChange={(e: any) => setSu({ ...su, pw2: e.target.value })} autoComplete="new-password" /></Field>
                  </div>
                ) : r.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {checks.length ? <><h3>預檢與自我檢查</h3><CheckList items={checks} /></> : null}
        {err ? <Msg kind="error">{err}</Msg> : null}
        {h.run && h.run.exit && h.run.exit.error ? <Msg kind="error">{h.run.exit.error}</Msg> : null}
        {needAccept ? <Msg kind="warn" action={<Btn small kind="primary" onClick={() => start('bootstrap', true)}>了解，繼續</Btn>}>預檢有警告（見上方 ⚠）；確認可以接受後按「了解，繼續」</Msg> : null}
        {done && !needAccept ? <Msg kind={done.status === 'ok' || done.status === 'pass' ? 'ok' : done.status === 'warn' ? 'warn' : 'error'}>{done.message}</Msg> : null}
        <div className="row">
          <Btn kind="primary" disabled={h.running || !chosen || !props.state.hasIstudyctl} onClick={() => start('bootstrap')}>開始部署</Btn>
          <Btn disabled={h.running || !props.state.hasIstudyctl} onClick={() => start('bootstrap-check')}>重新檢查</Btn>
          <Btn disabled={!h.run} onClick={saveReport}>儲存報告</Btn>
          {h.running ? <span className="dim">… 執行中</span> : null}
        </div>
        <p className="dim">需要 root 的步驟會跳出 Ubuntu 系統密碼視窗。可重複執行：已完成的步驟會略過，從未完成的步驟繼續；不刪除任何資料。</p>
      </section>
      <footer className="statusbar"><span className="grow" /><span>{props.info.versionLabel} · © 2026 無相氣學</span></footer>
    </div>
  );
}
