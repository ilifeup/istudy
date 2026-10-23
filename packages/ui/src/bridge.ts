/** 主程序橋接（preload 暴露的 window.istudy）。 */

export type HealthState = 'checking' | 'ok' | 'fail' | 'update_required';
export interface HealthSnapshot { state: HealthState; checkedAt: string | null; serverVersion?: string }

export interface AppInfo {
  kind: 'client' | 'server';
  channel: string;
  version: string;
  versionLabel: string;
  apiLevel: number;
  apiBase: string;
  platform: string;
  hostname: string;
}

export interface Account {
  id: number;
  username: string;
  role: 'user' | 'admin' | 'super';   // 權限（dev.55：只有三級）
  role_label: string;
  title_id?: string | null;            // 稱謂（助教、講師），與權限無關；Super 沒有
  title?: string;
  full_name: string;
  display_name: string;
  phone: string;
  birth_date: string | null;
  start_year: number | null;
  address: string;
  class_time: string;
  bio: string;
  groups: { id: number; name: string }[];
  can_import_export: boolean;
  tag_warning: boolean;
}

export interface HostEvent {
  runId: string;
  action: string;
  type: 'event' | 'text' | 'exit';
  data?: any;
  text?: string;
  code?: number;
  error?: string;
}

export interface Bridge {
  info(): Promise<AppInfo>;
  health(): Promise<HealthSnapshot>;
  retryHealth(): Promise<HealthSnapshot>;
  onHealth(fn: (s: HealthSnapshot) => void): () => void;
  onNotice(fn: (n: { kind: string; message: string }) => void): () => void;
  login(u: string, p: string): Promise<{ ok: boolean; account?: Account; error?: string; code?: string; retryAfter?: number }>;
  logout(): Promise<{ ok: boolean; error?: string }>;
  /** dev.62：Client 按視窗 X（主程序要求先登出再縮到系統匣）。 */
  onCloseRequested?(fn: () => void): () => void;
  closeDone?(): Promise<boolean>;
  clearSession(): Promise<boolean>;
  loginConfig(): Promise<{ username_placeholder: string; password_placeholder: string }>;
  log(level: 'info' | 'warn' | 'error', message: string): Promise<void>;
  saveFile(name: string, content: string): Promise<{ ok: boolean; canceled?: boolean; path?: string }>;
  openTextFile(kind: 'subtitle' | 'text'): Promise<{ name: string; content: string; error?: string } | null>;
  pcInfo(): Promise<Record<string, any>>;
  uploadPcInfo(): Promise<{ ok: boolean; error?: string; device_id?: number | null }>;
  copy(text: string): Promise<boolean>;
  setFullscreen(on: boolean): Promise<boolean>;
  host: {
    state(): Promise<any>;
    bundles(): Promise<{ path: string; version: string }[]>;
    pickBundle(): Promise<{ path: string; version: string } | { error: string } | null>;
    run(action: string, opts?: unknown): Promise<{ runId?: string; error?: string }>;
    cancel(runId: string): Promise<boolean>;
    /** 主機盤點（UEM）最新報告（只有 Server App）。 */
    uemLatest(): Promise<{ json?: any; md?: string; html?: string; none?: boolean; error?: string }>;
    /** dev.61：Server 上目前發布給 Client 自動更新的版本（只有 Server App）。 */
    publishedClients(): Promise<{ channel: string; version: string | null; file?: string | null; at?: string; note?: string }[]>;
    /** dev.61：選擇要發布的 Client 安裝檔資料夾（原始碼包的 dist/clients）。 */
    pickClients(): Promise<{ path: string } | { error: string } | null>;
    onEvent(fn: (e: HostEvent) => void): () => void;
  };
}

export const bridge: Bridge = (window as any).istudy;

let infoCache: AppInfo | null = null;
export async function appInfo(): Promise<AppInfo> {
  if (!infoCache) infoCache = await bridge.info();
  return infoCache;
}
export function cachedInfo(): AppInfo {
  if (!infoCache) throw new Error('appInfo() 尚未載入');
  return infoCache;
}

/** base64 → UTF-8 文字（openTextFile 回傳 base64，避免編碼問題）。 */
export function decodeBase64(b64: string): string {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}
