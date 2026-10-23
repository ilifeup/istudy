/**
 * dev.61 Client 線上更新的共用邏輯（Windows 與 Ubuntu）：更新狀態（心跳回報給 Server，Super 在 Server App 看得到）、
 * 「沒有人登入」一段時間後自動安裝，以及 Ubuntu 自動更新服務的狀態檔。
 *
 * - Windows（Win7／10／11）：electron-updater 從 Server 的 /updates/<版別>/ 下載；下載完成後，登入畫面閒置
 *   （沒有人登入）滿 2 分鐘就自動安裝並重新開啟（quitAndInstall），不用等使用者自己關 App。
 * - Ubuntu：安裝 .deb 需要 root，由系統服務 istudy-client-update（root、systemd timer）下載並核對；App 只讀狀態檔，
 *   閒置時寫一個「立即更新」要求並關閉自己，系統服務安裝完成後，由分離的小程序重新開啟 App。
 */
import * as fs from 'fs';

export type UpdateState = 'disabled' | 'manual' | 'checking' | 'up_to_date' | 'downloading' | 'downloaded' | 'installing' | 'installed' | 'failed';

export interface UpdateStatus {
  state: UpdateState;
  available?: string | null;
  percent?: number | null;
  error?: string | null;
  at: string;
}

export const UBUNTU_STATUS = '/var/lib/istudy-client-update/status.json';
export const UBUNTU_REQUESTS = '/run/istudy-client-update';

/** 已下載的新版要在「沒有人登入」連續 idleMs 之後才安裝；有人登入就重新計時。 */
export class IdleInstaller {
  private since: number | null = null;
  private fired = false;

  constructor(private idleMs: number) {}

  tick(ready: boolean, loggedIn: boolean, now = Date.now()): boolean {
    if (!ready || loggedIn) {
      this.since = null;
      return false;
    }
    if (this.since === null) this.since = now;
    if (!this.fired && now - this.since >= this.idleMs) {
      this.fired = true;
      return true;
    }
    return false;
  }
}

/** Ubuntu 自動更新服務的狀態檔 → 回報用的更新狀態；沒有服務（舊版手動安裝的）時回傳 null。 */
export function ubuntuStatus(file: string, running: string): UpdateStatus | null {
  let st: any;
  try {
    st = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
  if (!st || typeof st !== 'object') return null;
  const at = typeof st.checked_at === 'string' ? st.checked_at : new Date().toISOString();
  const available = typeof st.available === 'string' ? st.available : null;
  const error = typeof st.error === 'string' ? st.error.slice(0, 200) : null;
  if (available && available === running && st.state !== 'failed') return { state: 'up_to_date', available, at };
  switch (st.state) {
    case 'waiting_close': return { state: 'downloaded', available, at };
    case 'installed': return { state: 'installed', available, at };
    case 'up_to_date': return { state: 'up_to_date', available, at };
    case 'failed': return { state: 'failed', available, error, at };
    default: return { state: 'checking', available, at };
  }
}

/** 分離的重新開啟程序（sh）：等系統服務處理完這次要求（狀態檔出現 request_id）或最多 3 分鐘，再開啟 App。
 *  App 的路徑放在環境變數裡，指令列不含 App 路徑（更新服務用指令列判斷 App 是否還在執行）。 */
export function relaunchScript(): string {
  return [
    'i=0',
    'while [ "$i" -lt 90 ]; do',
    '  sleep 2; i=$((i+1))',
    '  if grep -q "\\"request_id\\": \\"$ISTUDY_REQUEST_ID\\"" "$ISTUDY_UPDATE_STATUS" 2>/dev/null; then break; fi',
    'done',
    'sleep 1',
    'exec "$ISTUDY_RELAUNCH"',
  ].join('\n');
}

export function noticeText(version: string | null | undefined): string {
  return `有新版本${version ? ' ' + version : ''}：沒有人登入時，App 會自動更新並重新開啟（約 1 分鐘）`;
}
