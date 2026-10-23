/**
 * iStudy Client App 主程序（四個版別同一份程式：Win7＝Electron 22、Win10／Win11／Ubuntu＝目前受支援的 Electron）。
 * - 開機自動啟動、關閉視窗縮到系統匣（心跳繼續），只有從系統匣「結束」才完全關閉（第 09 章）。
 * - dev.62：按視窗 X 會先登出（儲存播放位置、撤銷 Session、回到登入畫面）再縮到系統匣，下一位使用者不會接到上一位的登入。
 * - 不論是否登入 iStudy，每 60 秒心跳回報（第 09 章）。
 * - 每 30 秒檢查伺服器連線；睡眠喚醒時立即檢查（第 06 章）。
 * - 每次啟動與每 6 小時檢查自己版別的更新來源 /updates/<版別>/（第 18 章）。
 */
import { app, BrowserWindow, ipcMain, Menu, nativeImage, powerMonitor, screen, Tray } from 'electron';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { installHeaderInjection, osDescription, physicalMacs, registerBridge, ShellContext, store } from '../../../packages/shell/src/bridge';
import { ClientLog } from '../../../packages/shell/src/clientlog';
import { readBuildInfo, readServerAddress, versionLabel } from '../../../packages/shell/src/config';
import { HealthChecker } from '../../../packages/shell/src/health';
import { request } from '../../../packages/shell/src/net';
import { APP_ORIGIN, blockDownloads, hardenContents, registerAppScheme, secureWebPreferences, serveRenderer } from '../../../packages/shell/src/security';
import { IdleInstaller, noticeText, relaunchScript, UBUNTU_REQUESTS, UBUNTU_STATUS, ubuntuStatus, UpdateStatus } from './updater';

registerAppScheme();
if (process.env.ISTUDY_USER_DATA) app.setPath('userData', process.env.ISTUDY_USER_DATA);

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
const log = new ClientLog(app.getPath('userData'));
const server = readServerAddress((m) => log.write('warn', m));
const build = readBuildInfo(process.resourcesPath || __dirname, app.getVersion());
const health = new HealthChecker(server, build.apiLevel, (lv, m) => log.write(lv, m),
  Number(process.env.ISTUDY_HEALTH_OK_MS || 30000), Number(process.env.ISTUDY_HEALTH_FAIL_MS || 10000));
const ctx: ShellContext = { kind: 'client', server, build, log, health, getWindow: () => win };

function iconPath(): string {
  const p = path.join(__dirname, 'assets', 'icon.png');
  return fs.existsSync(p) ? p : path.join(__dirname, '..', 'assets', 'icon.png');
}

function createWindow(): void {
  // 1024×768 的螢幕也要放得下：最小 960×640；預設大小不超過工作區（開啟後仍會最大化）
  const wa = screen.getPrimaryDisplay().workAreaSize;
  win = new BrowserWindow({
    width: Math.min(1440, wa.width), height: Math.min(900, wa.height), minWidth: 960, minHeight: 640, show: false,
    title: 'iStudy 心賞氣悅・無相氣學 助教複習系統',
    icon: iconPath(),
    backgroundColor: '#eef1ee',
    autoHideMenuBar: true,
    webPreferences: secureWebPreferences(path.join(__dirname, 'preload.js')),
  });
  Menu.setApplicationMenu(null);
  hardenContents(win.webContents);
  win.once('ready-to-show', () => {
    if (process.argv.includes('--hidden') || process.env.ISTUDY_START_HIDDEN === '1') return;
    win?.maximize();
    win?.show();
  });
  win.on('close', (e) => {
    if (!quitting && process.env.ISTUDY_NO_TRAY !== '1') {
      e.preventDefault();
      void closeToTray(); // dev.62：先登出、回登入畫面，再縮到系統匣（心跳繼續）
    }
  });
  void win.loadURL(APP_ORIGIN + '/index.html');
}

// ---------- dev.62：按 X ＝ 登出＋縮到系統匣 ----------
let closing = false;
let closeWaiter: (() => void) | null = null;
const CLOSE_LOGOUT_MS = Number(process.env.ISTUDY_CLOSE_LOGOUT_MS || 8000);
ipcMain.handle('istudy:close-done', () => {
  if (closeWaiter) closeWaiter();
  closeWaiter = null;
  return true;
});

async function closeToTray(): Promise<void> {
  if (closing || !win) return;
  closing = true;
  const hadSession = !!store.token;
  win.hide();                                   // 視窗馬上消失；登出在背景完成
  try {
    if (hadSession && !win.webContents.isDestroyed()) {
      // 畫面負責：儲存播放位置（最多 5 秒）→ 向 Server 登出 → 清除 Token → 回到登入畫面
      const done = new Promise<void>((resolve) => { closeWaiter = resolve; });
      win.webContents.send('istudy:close-requested');
      await Promise.race([done, new Promise((r) => setTimeout(r, CLOSE_LOGOUT_MS))]);
    }
    if (store.token) {
      // 畫面沒有在時間內完成（例如畫面沒有回應）：主程序直接登出；連不上 Server 也清除本機 Token
      try {
        await request(server, 'POST', '/api/logout', { body: {}, token: store.token, timeoutMs: 4000 });
      } catch {
        /* 連不上 Server：Session 會在到期後失效；這台電腦已經不能再用它 */
      }
      store.token = null;
      store.account = null;
      if (win && !win.webContents.isDestroyed()) win.webContents.reload();   // 重新載入＝回到登入畫面
    }
    if (hadSession) {
      log.write('info', '按 X 關閉視窗：已登出，App 留在系統匣（心跳繼續）');
      if (tray && process.platform === 'win32') {
        tray.displayBalloon({ title: 'iStudy 已登出', content: 'iStudy 仍在背景執行（回報開機狀態）。要完全結束：在系統匣圖示按右鍵 →「結束 iStudy」。' });
      }
    }
  } finally {
    closeWaiter = null;
    closing = false;
  }
}

function createTray(): void {
  if (process.env.ISTUDY_NO_TRAY === '1') return;
  try {
    tray = new Tray(nativeImage.createFromPath(iconPath()));
    tray.setToolTip('iStudy（執行中）');
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '顯示 iStudy', click: () => { win?.show(); win?.focus(); } },
      { type: 'separator' },
      { label: '結束 iStudy', click: () => { quitting = true; app.quit(); } },
    ]));
    tray.on('click', () => { win?.show(); win?.focus(); });
  } catch (e: any) {
    log.write('warn', '無法建立系統匣圖示：' + e.message);
  }
}

/** Windows：Electron 的開機啟動設定；Ubuntu：.deb 安裝 /etc/xdg/autostart 捷徑（使用者層級補一份）。 */
function ensureAutostart(): void {
  if (!app.isPackaged) return;
  if (process.platform === 'win32') {
    app.setLoginItemSettings({ openAtLogin: true, args: ['--hidden'] });
  } else if (process.platform === 'linux' && !fs.existsSync('/etc/xdg/autostart/istudy-client.desktop')) {
    const dir = path.join(os.homedir(), '.config', 'autostart');
    try {
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'istudy-client.desktop'),
        `[Desktop Entry]\nType=Application\nName=iStudy\nExec="${process.execPath}" --hidden\nX-GNOME-Autostart-enabled=true\n`, 'utf8');
    } catch (e: any) {
      log.write('warn', '無法建立開機自動啟動：' + e.message);
    }
  }
}

// ---------- 心跳回報 ----------
async function heartbeat(): Promise<void> {
  const macs = physicalMacs();
  if (!macs.length) {
    log.write('warn', '找不到實體網卡 MAC，略過心跳');
    return;
  }
  try {
    const r = await request(server, 'POST', '/api/client/heartbeat', {
      token: store.token, timeoutMs: 5000,
      body: { macs, pc_name: os.hostname().slice(0, 64), os: osDescription(), channel: build.channel, app_version: build.version, api_level: build.apiLevel,
        update: updateStatus },   // dev.61：更新狀態（Server App「Client 版本與更新」顯示）
    });
    if (r.status === 200) health.reportSuccess();
    else log.write('warn', `心跳回報 HTTP ${r.status}`);
  } catch (e: any) {
    log.write('warn', `心跳回報失敗：${e.kind || e.message}`);
    health.reportConnectionError();
  }
}

async function uploadLogs(): Promise<void> {
  const p = log.pendingUpload();
  if (!p) return;
  if (!p.entries.length) return p.markDone();
  try {
    const r = await request(server, 'POST', '/api/client/logs', {
      token: store.token, timeoutMs: 10000,
      body: { mac: physicalMacs()[0], pc_name: os.hostname(), channel: build.channel, app_version: build.version, entries: p.entries },
    });
    if (r.status === 200) p.markDone();
  } catch {
    /* 下次啟動再試 */
  }
}

// ---------- 更新（dev.61：Windows electron-updater；Ubuntu 系統服務；狀態回報給 Server） ----------
const IDLE_MS = Number(process.env.ISTUDY_UPDATE_IDLE_MS || 120000);   // 沒有人登入滿 2 分鐘才自動安裝
let updateStatus: UpdateStatus = { state: app.isPackaged && process.env.ISTUDY_NO_UPDATE !== '1' ? 'checking' : 'disabled', at: new Date().toISOString() };

function setUpdate(s: Partial<UpdateStatus>): void {
  const prev = updateStatus.state;
  updateStatus = { ...updateStatus, ...s, at: new Date().toISOString() };
  if (updateStatus.state !== prev) log.write('info', `更新狀態：${updateStatus.state}${updateStatus.available ? ' ' + updateStatus.available : ''}`);
}

function notifyUpdate(version: string | null | undefined): void {
  win?.webContents.send('istudy:notice', { kind: 'update', message: noticeText(version), version });
}

function setupUpdater(): void {
  if (!app.isPackaged || process.env.ISTUDY_NO_UPDATE === '1') return;
  if (build.channel === 'ubuntu') {
    setupUbuntuUpdater();
    return;
  }
  let autoUpdater: any;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    autoUpdater = require('electron-updater').autoUpdater;
  } catch {
    log.write('warn', 'electron-updater 無法載入，改為手動更新');
    setUpdate({ state: 'manual', error: 'electron-updater 無法載入' });
    return;
  }
  autoUpdater.setFeedURL({ provider: 'generic', url: `${server}/updates/${build.channel}/` });
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on('checking-for-update', () => { if (updateStatus.state !== 'downloaded') setUpdate({ state: 'checking' }); });
  autoUpdater.on('update-not-available', () => setUpdate({ state: 'up_to_date', available: null, percent: null, error: null }));
  autoUpdater.on('update-available', (info: any) => setUpdate({ state: 'downloading', available: info && info.version, percent: 0, error: null }));
  autoUpdater.on('download-progress', (p: any) => setUpdate({ state: 'downloading', percent: p && typeof p.percent === 'number' ? Math.round(p.percent) : null }));
  autoUpdater.on('update-downloaded', (info: any) => {
    log.write('info', `已下載新版本 ${info && info.version}`);
    setUpdate({ state: 'downloaded', available: info && info.version, percent: 100, error: null });
    notifyUpdate(info && info.version);
  });
  autoUpdater.on('error', (e: any) => {
    log.write('warn', '更新檢查失敗：' + (e && e.message));
    if (updateStatus.state !== 'downloaded') setUpdate({ state: 'failed', error: String((e && e.message) || e).slice(0, 200) });
  });
  const check = () => autoUpdater.checkForUpdates().catch((e: any) => log.write('warn', '更新檢查失敗：' + e.message));
  check();
  setInterval(check, 6 * 3600 * 1000);
  health.onChange((s) => { if (s.state === 'update_required') check(); });
  // 已下載：登入畫面閒置（沒有人登入）滿 2 分鐘就自動安裝並重新開啟，不用等使用者關 App
  const idle = new IdleInstaller(IDLE_MS);
  setInterval(() => {
    if (!idle.tick(updateStatus.state === 'downloaded', !!store.token)) return;
    log.write('info', `沒有人登入：自動安裝 ${updateStatus.available} 並重新開啟`);
    setUpdate({ state: 'installing' });
    void heartbeat().finally(() => {
      quitting = true;
      autoUpdater.quitAndInstall(true, true);   // 靜默安裝、完成後重新開啟
    });
  }, 15000);
}

/** Ubuntu：root 的系統服務負責下載、核對、安裝；App 讀狀態、提示，沒有人登入時要求立即更新並關閉自己。 */
function setupUbuntuUpdater(): void {
  const file = process.env.ISTUDY_UPDATE_STATUS || UBUNTU_STATUS;
  const reqDir = process.env.ISTUDY_UPDATE_REQUESTS || UBUNTU_REQUESTS;
  const idle = new IdleInstaller(IDLE_MS);
  let notified = '';
  const tick = () => {
    const s = ubuntuStatus(file, build.version);
    if (!s) {
      setUpdate({ state: 'manual', error: '這台沒有自動更新服務：請用 sudo apt install 安裝一次 dev.61 以後的 .deb' });
      return;
    }
    setUpdate(s);
    if (s.state === 'downloaded' && s.available && notified !== s.available) {
      notified = s.available;
      notifyUpdate(s.available);
    }
    if (!idle.tick(s.state === 'downloaded', !!store.token)) return;
    const id = `${typeof process.getuid === 'function' ? process.getuid() : 0}-${Date.now()}`;
    try {
      fs.writeFileSync(path.join(reqDir, `request-${id}`), id + '\n', { mode: 0o644 });
    } catch (e: any) {
      log.write('warn', `無法要求立即更新（${e.message}）：等下次開機或關閉 App 後由系統服務安裝`);
      return;
    }
    log.write('info', `沒有人登入：要求系統服務安裝 ${s.available}，App 先關閉，完成後自動重新開啟`);
    spawn('/bin/sh', ['-c', relaunchScript()], {
      detached: true, stdio: 'ignore',
      env: { ...process.env, ISTUDY_RELAUNCH: process.execPath, ISTUDY_UPDATE_STATUS: file, ISTUDY_REQUEST_ID: id },
    }).unref();
    setUpdate({ state: 'installing' });
    void heartbeat().finally(() => { quitting = true; app.quit(); });
  };
  setTimeout(tick, 20000);
  setInterval(tick, 60000);
}

app.on('second-instance', () => {
  win?.show();
  win?.focus();
});

app.whenReady().then(() => {
  log.write('info', `啟動 ${versionLabel(build.channel, build.version)}（Electron ${process.versions.electron}），Server ${server}`);
  blockDownloads();
  serveRenderer(path.join(__dirname, 'renderer'), server);
  installHeaderInjection(ctx);
  registerBridge(ctx);
  createWindow();
  createTray();
  ensureAutostart();
  health.start();
  powerMonitor.on('resume', () => {
    log.write('info', '電腦從睡眠喚醒，重新檢查連線');
    void health.check();
    void heartbeat();
  });
  void heartbeat();
  setInterval(() => void heartbeat(), Number(process.env.ISTUDY_HEARTBEAT_MS || 60000));
  setTimeout(() => void uploadLogs(), 15000);
  setupUpdater();
});

app.on('before-quit', () => { quitting = true; });
app.on('window-all-closed', () => {
  if (process.env.ISTUDY_NO_TRAY === '1') app.quit();
});
