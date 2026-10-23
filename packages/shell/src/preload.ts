/**
 * Preload：只暴露少數函式給畫面（contextIsolation＋sandbox）。Token 不會經過這裡。
 */
import { contextBridge, ipcRenderer } from 'electron';

const invoke = (ch: string, ...args: unknown[]) => ipcRenderer.invoke(ch, ...args);

const api = {
  info: () => invoke('istudy:info'),
  health: () => invoke('istudy:health'),
  retryHealth: () => invoke('istudy:health-retry'),
  onHealth: (fn: (s: unknown) => void) => {
    const h = (_e: unknown, s: unknown) => fn(s);
    ipcRenderer.on('istudy:health-changed', h);
    return () => ipcRenderer.removeListener('istudy:health-changed', h);
  },
  onNotice: (fn: (n: unknown) => void) => {
    const h = (_e: unknown, n: unknown) => fn(n);
    ipcRenderer.on('istudy:notice', h);
    return () => ipcRenderer.removeListener('istudy:notice', h);
  },
  // dev.62：Client 按視窗 X 時，主程序要求畫面先登出（只有 Client App 會送出）
  onCloseRequested: (fn: () => void) => {
    const h = () => fn();
    ipcRenderer.on('istudy:close-requested', h);
    return () => ipcRenderer.removeListener('istudy:close-requested', h);
  },
  closeDone: () => invoke('istudy:close-done'),
  login: (username: string, password: string) => invoke('istudy:login', username, password),
  logout: () => invoke('istudy:logout'),
  clearSession: () => invoke('istudy:clear-session'),
  loginConfig: () => invoke('istudy:login-config'),
  log: (level: string, message: string) => invoke('istudy:log', level, message),
  saveFile: (name: string, content: string) => invoke('istudy:save-file', name, content),
  openTextFile: (kind: string) => invoke('istudy:open-text-file', kind),
  pcInfo: () => invoke('istudy:pc-info'),
  uploadPcInfo: () => invoke('istudy:upload-pc-info'),
  copy: (text: string) => invoke('istudy:copy', text),
  setFullscreen: (on: boolean) => invoke('istudy:set-fullscreen', on),
  // 只有 Server App 有實作（Client App 呼叫會得到錯誤）
  host: {
    state: () => invoke('host:state'),
    bundles: () => invoke('host:bundles'),
    pickBundle: () => invoke('host:pick-bundle'),
    run: (action: string, opts: unknown) => invoke('host:run', action, opts),
    cancel: (runId: string) => invoke('host:cancel', runId),
    uemLatest: () => invoke('host:uem-latest'),
    publishedClients: () => invoke('host:published-clients'),
    pickClients: () => invoke('host:pick-clients'),
    onEvent: (fn: (e: unknown) => void) => {
      const h = (_e: unknown, ev: unknown) => fn(ev);
      ipcRenderer.on('host:event', h);
      return () => ipcRenderer.removeListener('host:event', h);
    },
  },
};

contextBridge.exposeInMainWorld('istudy', api);
