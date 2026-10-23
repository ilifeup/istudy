/**
 * App 根元件：登入前顯示登入頁；登入後顯示全域導航列＋管理列＋頁面（第 06 章）。
 * Client App 與 Server App 共用；Server App 另外傳入「Server 管理」頁面。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, onSessionExpired } from './api';
import { Account, AppInfo, bridge, HealthSnapshot } from './bridge';
import { AppContext, AppCtx, atLeast, MsgKind, roleBadge, Route, ToastItem, Toasts } from './core';
import { LoginPage } from './components/Login';
import { CoursesPage } from './pages/Courses';
import { PlayerPage } from './pages/Player';
import { DocPage } from './pages/DocViewer';
import { HistoryPage } from './pages/History';
import { BookmarksPage } from './pages/Bookmarks';
import { ProfilePage } from './pages/Profile';
import { SearchPage } from './pages/Search';
import { FeedbackPage } from './pages/Feedback';
import { AccountsPage } from './admin/Accounts';
import { GroupsPage } from './admin/Groups';
import { AuditPage } from './admin/Audit';
import { WolPage } from './admin/Wol';
import { TagPolicyPage } from './admin/TagPolicy';
import { PcInfoPage } from './admin/PcInfo';
import { EmailReportPage } from './admin/EmailReport';

export interface ServerExtension {
  /** 「Server 管理」分頁（Server App 才有） */
  pages: Record<string, (props: { params: Record<string, any> }) => any>;
  defaultPage: string;
  /** 登入後要做的事（Super 登入時觸發一次 Server 診斷；首次設定精靈） */
  afterLogin?: (account: Account, go: (p: string, params?: Record<string, any>) => void) => Promise<void> | void;
  /** 尚未部署時取代登入頁 */
  PreLogin?: (props: { info: AppInfo; health: HealthSnapshot; children: any }) => any;
  footerLabel?: string;
}

const CLIENT_PAGES: Record<string, (props: { params: Record<string, any> }) => any> = {
  courses: CoursesPage,
  player: PlayerPage,
  doc: DocPage,
  history: HistoryPage,
  bookmarks: BookmarksPage,
  profile: ProfilePage,
  search: SearchPage,
  feedback: FeedbackPage,
  accounts: AccountsPage,
  groups: GroupsPage,
  audit: AuditPage,
  wol: WolPage,
  tagpolicy: TagPolicyPage,
  pcinfo: PcInfoPage,
  email: EmailReportPage,
};

const NAV = [
  { id: 'courses', label: '課程目錄' },
  { id: 'history', label: '瀏覽記錄' },
  { id: 'bookmarks', label: '書籤' },
  { id: 'profile', label: '個人資料' },
  { id: 'search', label: '搜尋' },
  { id: 'feedback', label: '問題回報' },
];

function adminItems(a: Account): { id: string; label: string }[] {
  const items: { id: string; label: string }[] = [];
  if (atLeast(a, 'admin')) items.push({ id: 'accounts', label: '帳號管理' }, { id: 'groups', label: '課程群組' }, { id: 'audit', label: '使用記錄' }, { id: 'wol', label: 'WOL 管理' });
  if (a.role === 'super') items.push({ id: 'tagpolicy', label: 'Tag 設定' }, { id: 'pcinfo', label: 'PC 資訊' }, { id: 'email', label: 'Email 報告' });
  return items;
}

export function App(props: { info: AppInfo; server?: ServerExtension }): any {
  const { info, server } = props;
  const [health, setHealth] = useState<HealthSnapshot>({ state: 'checking', checkedAt: null });
  const [account, setAccount] = useState<Account | null>(null);
  const [route, setRoute] = useState<Route>({ page: 'courses' });
  const [stack, setStack] = useState<Route[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [loginNotice, setLoginNotice] = useState<string | null>(null);
  const beforeLogout = useRef(new Set<() => Promise<void> | void>());

  useEffect(() => {
    bridge.health().then(setHealth, () => undefined);
    const off = bridge.onHealth(setHealth);
    const off2 = bridge.onNotice((n) => setNotice(n.message));
    // dev.61：作業系統回報網路中斷或恢復（拔掉、插回網路線）時立即重新檢查伺服器連線（任何畫面都有效，不只登入頁）
    const recheck = () => void bridge.retryHealth();
    window.addEventListener('online', recheck);
    window.addEventListener('offline', recheck);
    return () => { off(); off2(); window.removeEventListener('online', recheck); window.removeEventListener('offline', recheck); };
  }, []);

  const toast = useCallback((text: string, kind: MsgKind = 'info', ms = 4000) => {
    const id = Date.now() + Math.random();
    setToasts((t: ToastItem[]) => [...t, { id, text, kind, until: Date.now() + ms }]);
    setTimeout(() => setToasts((t: ToastItem[]) => t.filter((x) => x.id !== id)), ms);
  }, []);

  const toLogin = useCallback(async (message: string | null) => {
    await bridge.clearSession();
    setAccount(null);
    setStack([]);
    setRoute({ page: 'courses' });
    setLoginNotice(message);
  }, []);

  useEffect(() => onSessionExpired(() => void toLogin('登入已過期，請重新登入')), [toLogin]);

  const routeRef = useRef<Route>(route);
  routeRef.current = route;
  const stackRef = useRef<Route[]>(stack);
  stackRef.current = stack;

  const go = useCallback((page: string, params: Record<string, any> = {}) => {
    const cur = routeRef.current;
    // 播放器與文件檢視器記住從哪裡來（返回原本的課程目錄位置或歷程／書籤／搜尋分頁）
    if (page === 'player' || page === 'doc') {
      if (cur.page !== 'player' && cur.page !== 'doc') setStack([...stackRef.current, cur]);
    } else {
      setStack([]);
    }
    setRoute({ page, params });
  }, []);

  const back = useCallback(() => {
    const s = stackRef.current;
    setStack(s.slice(0, -1));
    setRoute(s[s.length - 1] || { page: 'courses' });
  }, []);

  const logout = useCallback(async () => {
    // 先儲存播放進度（最多等 5 秒），再撤銷 Session
    const tasks = Array.from(beforeLogout.current).map((fn) => Promise.resolve().then(fn).catch(() => undefined));
    await Promise.race([Promise.all(tasks), new Promise((r) => setTimeout(r, 5000))]);
    const r = await bridge.logout();
    if (!r.ok) {
      toast(r.error || '登出失敗，請重試', 'error', 6000);
      return;
    }
    await toLogin(null);
  }, [toast, toLogin]);

  // dev.62：Client 按視窗 X：儲存播放位置 → 登出（Server 連不上也清除本機登入）→ 回到登入畫面 → 通知主程序
  const accountRef = useRef<Account | null>(account);
  accountRef.current = account;
  useEffect(() => {
    if (!bridge.onCloseRequested) return undefined;
    return bridge.onCloseRequested(() => {
      void (async () => {
        if (accountRef.current) {
          const tasks = Array.from(beforeLogout.current).map((fn) => Promise.resolve().then(fn).catch(() => undefined));
          await Promise.race([Promise.all(tasks), new Promise((r) => setTimeout(r, 5000))]);
          await bridge.logout().catch(() => undefined);
          await toLogin(null);
        }
        await bridge.closeDone?.();
      })();
    });
  }, [toLogin]);

  const onBeforeLogout = useCallback((fn: () => Promise<void> | void) => {
    beforeLogout.current.add(fn);
    return () => { beforeLogout.current.delete(fn); };
  }, []);

  const onLogin = async (a: Account) => {
    setAccount(a);
    setLoginNotice(null);
    const first = server ? server.defaultPage : 'courses';
    setRoute({ page: first });
    if (server && server.afterLogin) await server.afterLogin(a, go);
  };

  if (!account) {
    const login = <LoginPage info={info} health={health} onLogin={onLogin} serverApp={!!server} notice={loginNotice} />;
    return server && server.PreLogin ? <server.PreLogin info={info} health={health}>{login}</server.PreLogin> : login;
  }

  const ctx: AppCtx = { info, account, health, route, go, back, setAccount, logout, onBeforeLogout, toast };
  const pages = { ...CLIENT_PAGES, ...(server ? server.pages : {}) };
  const Page = pages[route.page] || CoursesPage;
  const admin = adminItems(account);

  return (
    <AppContext.Provider value={ctx}>
      <div className="app">
        <header className="topnav" aria-label="主選單">
          <span className="seal small" aria-hidden="true">無相</span>
          <span className="who" title={account.username}>👤 {account.display_name}{account.display_name === roleBadge(account) ? '' : `（${roleBadge(account)}）`}</span>
          <nav className="navlinks">
            {NAV.filter((n) => !(server && n.id === 'feedback')).map((n) => ( /* 問題回報只在 Client App */
              <button key={n.id} type="button" className={'navbtn' + (route.page === n.id ? ' on' : '')}
                aria-current={route.page === n.id ? 'page' : undefined} onClick={() => go(n.id)}>{n.label}</button>
            ))}
            {server ? (
              <button type="button" className={'navbtn' + (route.page.startsWith('srv-') ? ' on' : '')}
                onClick={() => go(server.defaultPage)}>Server 管理</button>
            ) : null}
          </nav>
          <span className="grow" />
          <button type="button" className="navbtn" onClick={() => void logout()}>登出</button>
        </header>
        {admin.length ? (
          <div className="adminbar" aria-label="管理列">
            <span className="dim">管理：</span>
            {admin.map((n) => (
              <button key={n.id} type="button" className={'btn small' + (route.page === n.id ? ' on' : '')} onClick={() => go(n.id)}>{n.label}</button>
            ))}
          </div>
        ) : null}
        {health.state === 'fail' ? <div className="banner fail" role="alert">⚠ 伺服器無法連線，部分功能暫停</div> : null}
        {health.state === 'update_required' ? <div className="banner fail" role="alert">✗ App 需要更新，請洽管理員</div> : null}
        {notice ? <div className="banner info">ⓘ {notice} <button type="button" className="btn small" onClick={() => setNotice(null)}>知道了</button></div> : null}
        <main className="content">
          <Page key={route.page + JSON.stringify(route.params || {})} params={route.params || {}} />
        </main>
        <footer className="statusbar">
          <span>{health.state === 'ok' ? '● Server 已連線' : health.state === 'checking' ? '… 檢查連線中' : '○ Server 無法連線'}</span>
          <span className="grow" />
          <span>{server && server.footerLabel ? server.footerLabel : info.versionLabel} · © 2026 無相氣學</span>
        </footer>
        <Toasts items={toasts} />
      </div>
    </AppContext.Provider>
  );
}

export async function loadMe(): Promise<Account> {
  return api.get<Account>('/api/me');
}
