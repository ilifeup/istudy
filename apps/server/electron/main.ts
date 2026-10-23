/**
 * iStudy Server App 主程序（Super 專用，只裝在 Ubuntu Server，固定連 127.0.0.1:4081；第 10、17 章）。
 * - 尚未部署時顯示「初始部署」；部署後第一次登入開啟「首次設定精靈」。
 * - 需要 root 的主機操作：以 pkexec 執行 /usr/local/sbin/istudyctl（固定子指令、參數驗證），
 *   桌面（XFCE、GNOME 等）的 polkit 驗證代理跳出 Ubuntu 系統密碼視窗；進度以 JSON 逐行讀取後顯示成步驟清單。
 * - 本機接螢幕與 RustDesk 遠端操作用同一個程式；視窗最小 1024×640，配合無螢幕時常見的虛擬解析度。
 * - 不送心跳（Server 不在 WOL 設備清單中）。
 */
import { app, BrowserWindow, dialog, Menu } from 'electron';
import { ChildProcess, spawn } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { installHeaderInjection, registerBridge, ShellContext, store } from '../../../packages/shell/src/bridge';
import { ClientLog } from '../../../packages/shell/src/clientlog';
import { normalizeServer, readBuildInfo, versionLabel } from '../../../packages/shell/src/config';
import { HealthChecker } from '../../../packages/shell/src/health';
import { request } from '../../../packages/shell/src/net';
import { pkexecError } from './hosterrors';
import { APP_ORIGIN, blockDownloads, hardenContents, registerAppScheme, secureWebPreferences, serveRenderer } from '../../../packages/shell/src/security';

registerAppScheme();
if (process.env.ISTUDY_USER_DATA) app.setPath('userData', process.env.ISTUDY_USER_DATA);
if (!app.requestSingleInstanceLock()) app.quit();

let win: BrowserWindow | null = null;
const log = new ClientLog(app.getPath('userData'));
const server = normalizeServer(process.env.ISTUDY_SERVER || '') || 'http://127.0.0.1:4081';
const build = { ...readBuildInfo(process.resourcesPath || __dirname, app.getVersion()), channel: 'server' as const };
const health = new HealthChecker(server, build.apiLevel, (lv, m) => log.write(lv, m), 30000, 5000);
const ctx: ShellContext = { kind: 'server', server, build, log, health, getWindow: () => win };

const ISTUDYCTL = '/usr/local/sbin/istudyctl';
const ETC = process.env.ISTUDY_HOST_ETC || '/etc/istudy';
const VARLIB = process.env.ISTUDY_HOST_VARLIB || '/var/lib/istudy';
const UPDATES = process.env.ISTUDY_HOST_UPDATES || '/srv/istudy/updates';   // dev.61：Client 自動更新的來源（Nginx /updates/）
const CLIENT_CHANNELS = ['win7', 'win10', 'win11', 'ubuntu'];
const runs = new Map<string, ChildProcess>();

function readJson(file: string): any {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

/** 找出 USB 或家目錄中的離線部署套件。 */
function findBundles(): { path: string; version: string }[] {
  const roots = [`/media/${os.userInfo().username}`, '/media', '/mnt', os.homedir(), path.join(os.homedir(), 'istudy', 'dist')];
  const found = new Map<string, string>();
  const consider = (dir: string) => {
    try {
      if (/istudy-server-.+-offline$/.test(dir) && fs.existsSync(path.join(dir, 'SHA256SUMS'))) {
        const v = readJson(path.join(dir, 'VERSION.json'));
        found.set(dir, (v && v.server) || '?');
      }
    } catch {
      /* 略過 */
    }
  };
  const last = (() => { try { return fs.readFileSync(path.join(ETC, 'last-bundle'), 'utf8').trim(); } catch { return ''; } })();
  if (last) consider(last);
  for (const root of roots) {
    try {
      for (const a of fs.readdirSync(root)) {
        const p1 = path.join(root, a);
        consider(p1);
        try {
          for (const b of fs.readdirSync(p1)) consider(path.join(p1, b));
        } catch {
          /* 不是資料夾 */
        }
      }
    } catch {
      /* 不存在 */
    }
  }
  return Array.from(found.entries()).map(([p, v]) => ({ path: p, version: v }));
}

function validBundle(p: unknown): string {
  if (typeof p !== 'string' || !path.isAbsolute(p) || !fs.existsSync(path.join(p, 'SHA256SUMS'))) throw new Error('請選擇有效的離線部署套件');
  return path.normalize(p);
}

/** dev.62：新的備份恢復密碼（至少 12 字、兩次相同、沒有換行）；與 Super 登入密碼分開由使用者自行決定。 */
function recoveryPassword(o: any): string {
  if (!o || typeof o.password !== 'string' || o.password.length < 12) throw new Error('恢復密碼至少 12 字');
  if (o.password.length > 256 || /[\x00-\x1f]/.test(o.password)) throw new Error('恢復密碼不能有換行等控制字元，最多 256 字');
  if (o.password !== o.password2) throw new Error('兩次輸入的恢復密碼不同');
  return o.password;
}

/** 固定的主機維護動作 → istudyctl 參數（不接受任意指令）。 */
function argsFor(action: string, o: any): { args: string[]; stdin?: string } {
  switch (action) {
    case 'bootstrap-check':
      return { args: ['bootstrap', '--check-only', ...(o && o.bundle ? ['--bundle', validBundle(o.bundle)] : [])] };
    case 'bootstrap': {
      const args = ['bootstrap', '--bundle', validBundle(o && o.bundle)];
      if (o && o.acceptWarnings) args.push('--accept-warnings');
      let stdin: string | undefined;
      if (o && o.superUser) {
        o.superUser = String(o.superUser).trim().normalize('NFC');
        if (!/^[\p{Script=Han}A-Za-z0-9_.\-]{2,80}$/u.test(o.superUser)) throw new Error('Super 帳號需為 2～80 字：中文、半形英文字母、數字（可含 _ . -）');
        if (typeof o.password !== 'string' || o.password.length < 10) throw new Error('Super 密碼至少 10 字');
        if (o.password !== o.password2) throw new Error('兩次輸入的密碼不同');
        args.push('--super-user', o.superUser, '--password-stdin');
        stdin = `${o.password}\n${o.password2}\n`;
      }
      return { args, stdin };
    }
    case 'update':
      return { args: ['update', '--bundle', validBundle(o && o.bundle)] };
    case 'restart':
      return { args: ['restart'] };
    case 'doctor':
      return { args: ['doctor'] };
    case 'hostcheck':
      return { args: ['hostcheck'] };
    case 'list-disks':
      return { args: ['list-disks'] };
    case 'uem-quick':
      return { args: ['uem', '--mode', 'quick'] };
    case 'uem-full':
      return { args: ['uem', '--mode', 'full'] };
    case 'asr-models':
      return { args: ['asr-models'] };
    case 'asr-models-fetch': {
      const t = o && typeof o.target === 'string' ? o.target : 'all';
      if (!['all', 'paraformer-zh', 'sensevoice-small'].includes(t)) throw new Error('不支援的模型');
      return { args: ['asr-models', '--fetch', t] };
    }
    case 'clients-publish':
      return { args: ['clients-publish', '--from', validClientsDir(o && o.dir)] };
    case 'backup-disk':
      if (!o || typeof o.uuid !== 'string' || !/^[0-9A-Fa-f\-]{8,40}$/.test(o.uuid)) throw new Error('磁碟 UUID 格式錯誤');
      return { args: ['backup-disk', '--uuid', o.uuid] };
    // dev.62 加密備份：恢復密碼、遠端密碼只經標準輸入交給 istudyctl（不放在參數、環境變數或檔案）
    case 'backup-key-init':
    case 'backup-key-change': {
      const pw = recoveryPassword(o);
      return { args: ['backup-key', action === 'backup-key-init' ? '--init' : '--change', '--password-stdin'], stdin: `${pw}\n${pw}\n` };
    }
    case 'backup-key-verify': {
      if (!o || typeof o.password !== 'string' || !o.password || /[\r\n\0]/.test(o.password)) throw new Error('請輸入恢復密碼');
      return { args: ['backup-key', '--verify', '--password-stdin'], stdin: `${o.password}\n` };
    }
    case 'backup-remote-set': {
      const port = Number(o && o.port);
      if (!o || typeof o.host !== 'string' || !/^[A-Za-z0-9.\-]{1,253}$/.test(o.host.trim())) throw new Error('主機名稱格式錯誤（只填主機名稱或 IP，不要加 ftp://）');
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('連接埠要在 1～65535');
      if (typeof o.username !== 'string' || !o.username.trim() || /[\r\n\0]/.test(o.username)) throw new Error('請填 FTP 帳號');
      if (o.password !== undefined && (typeof o.password !== 'string' || /[\r\n\0]/.test(o.password))) throw new Error('密碼格式錯誤');
      if (typeof o.dir !== 'string' || !o.dir.startsWith('/')) throw new Error('遠端資料夾要以 / 開頭');
      const cfg = { protocol: 'ftps', host: o.host.trim(), port, username: o.username.trim(), password: o.password || '', dir: o.dir.trim() };
      return { args: ['backup-remote', '--set'], stdin: JSON.stringify(cfg) + '\n' };
    }
    case 'backup-remote-test':
      return { args: ['backup-remote', '--test'] };
    case 'backup-verify': {
      const id = Number(o && o.id);
      if (!Number.isInteger(id) || id < 1) throw new Error('備份編號錯誤');
      return { args: ['backup-verify', '--id', String(id), ...(o.restoreTest ? ['--restore-test'] : [])] };
    }
    case 'backup-upload': {
      if (o && o.id !== undefined) {
        const id = Number(o.id);
        if (!Number.isInteger(id) || id < 1) throw new Error('備份編號錯誤');
        return { args: ['backup-upload', '--id', String(id)] };
      }
      return { args: ['backup-upload'] };
    }
    case 'backup-prune':
      return { args: ['backup-prune'] };
    default:
      throw new Error('不支援的主機維護動作');
  }
}

function hostCommand(args: string[]): { cmd: string; argv: string[] } {
  // 測試時可指定替代指令（例如 python3 istudyctl.py）；正式環境一律 pkexec istudyctl
  const override = process.env.ISTUDY_HOST_CMD;
  if (override && !app.isPackaged) {
    const parts = override.split(' ').filter(Boolean);
    return { cmd: parts[0], argv: [...parts.slice(1), '--json', ...args] };
  }
  return { cmd: 'pkexec', argv: [ISTUDYCTL, '--json', ...args] };
}

function runHost(action: string, opts: unknown): { runId: string } {
  const { args, stdin } = argsFor(action, opts);
  const runId = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  const { cmd, argv } = hostCommand(args);
  const child = spawn(cmd, argv, { stdio: ['pipe', 'pipe', 'pipe'] });
  runs.set(runId, child);
  const emit = (ev: unknown) => win?.webContents.send('host:event', { runId, action, ...(ev as object) });
  let buf = '';
  let errBuf = '';
  child.stdout.on('data', (d: Buffer) => {
    buf += d.toString('utf8');
    let i: number;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      try {
        emit({ type: 'event', data: JSON.parse(line) });
      } catch {
        emit({ type: 'text', text: line });
      }
    }
  });
  child.stderr.on('data', (d: Buffer) => { errBuf += d.toString('utf8'); });
  let spawnError = '';
  child.on('error', (e: any) => {
    spawnError = e && e.code === 'ENOENT' && cmd === 'pkexec'
      ? '找不到 pkexec：請在終端機執行 sudo apt install pkexec（或改用 sudo ./install.sh --cli）'
      : `無法執行：${e.message}`;
  });
  child.on('close', (code) => {
    runs.delete(runId);
    const error = spawnError || pkexecError(code, errBuf);
    emit({ type: 'exit', code: spawnError ? -1 : code, error });
    log.write(code === 0 ? 'info' : 'warn', `主機維護 ${action} 結束（${code}）`);
    if (store.token) {
      void request(server, 'POST', '/api/admin/logs', { token: store.token, body: {
        level: code === 0 ? 'info' : 'warn', message: `主機維護：${action}（結束碼 ${code}）${error ? '：' + error : ''}`, context: { action, code } } })
        .catch(() => undefined);
    }
  });
  if (stdin) child.stdin.write(stdin);
  child.stdin.end();
  return { runId };
}

function hostState(): Record<string, unknown> {
  const state = readJson(path.join(VARLIB, 'bootstrap.json'));
  return {
    deployed: fs.existsSync(path.join(ETC, 'install_id')) && !!state && state.steps && state.steps['14'] && state.steps['14'].status === 'ok',
    bootstrap: state,
    hasIstudyctl: fs.existsSync(ISTUDYCTL) || !!process.env.ISTUDY_HOST_CMD,
    hostname: os.hostname(),
  };
}

/** 主機盤點（UEM）最新報告：istudyctl uem 寫入 /var/lib/istudy/uem（0644）；這裡只讀取，不執行任何指令。 */
/** dev.61：要發布的 Client 安裝檔資料夾（原始碼包的 dist/clients）：絕對路徑、真的資料夾、裡面至少有一個版別資料夾。 */
function validClientsDir(dir: unknown): string {
  if (typeof dir !== 'string' || !path.isAbsolute(dir) || /[\0\n]/.test(dir)) throw new Error('資料夾路徑不正確');
  const real = fs.realpathSync(dir);
  if (!fs.statSync(real).isDirectory()) throw new Error('不是資料夾');
  if (!CLIENT_CHANNELS.some((ch) => fs.existsSync(path.join(real, ch)))) {
    throw new Error('這個資料夾裡沒有 win7、win10、win11、ubuntu 任何一個資料夾（請選原始碼包的 dist/clients）');
  }
  return real;
}

/** dev.61：Server 上目前發布給 Client 的版本（讀 /srv/istudy/updates/<版別>/latest.yml、latest.json；只讀）。 */
function publishedClients(): Record<string, unknown>[] {
  return CLIENT_CHANNELS.map((ch) => {
    const dir = path.join(UPDATES, ch);
    try {
      if (ch === 'ubuntu') {
        const f = path.join(dir, 'latest.json');
        if (!fs.existsSync(f)) {
          const debs = fs.existsSync(dir) ? fs.readdirSync(dir).filter((x) => x.endsWith('.deb')) : [];
          return { channel: ch, version: null, note: debs.length ? '只有 .deb、沒有 latest.json：Ubuntu Client 無法自動更新' : '尚未發布' };
        }
        const m = JSON.parse(fs.readFileSync(f, 'utf8'));
        return { channel: ch, version: m.version || null, file: m.file || null, at: fs.statSync(f).mtime.toISOString() };
      }
      const f = path.join(dir, 'latest.yml');
      if (!fs.existsSync(f)) return { channel: ch, version: null, note: '尚未發布' };
      const t = fs.readFileSync(f, 'utf8');
      const v = /^version:\s*['"]?([^'"\n]+?)['"]?\s*$/m.exec(t);
      const p = /^path:\s*['"]?([^'"\n]+?)['"]?\s*$/m.exec(t);
      return { channel: ch, version: v ? v[1] : null, file: p ? p[1] : null, at: fs.statSync(f).mtime.toISOString() };
    } catch (e: any) {
      return { channel: ch, version: null, note: `無法讀取：${e.message}` };
    }
  });
}

function uemLatest(): Record<string, unknown> {
  const dir = path.join(VARLIB, 'uem');
  const file = (n: string) => path.join(dir, n);
  if (!fs.existsSync(file('latest.json'))) return { none: true };
  try {
    if (fs.statSync(file('latest.json')).size > 8 * 1024 * 1024) return { error: '主機盤點報告超過 8MB，無法顯示' };
    const text = (n: string) => (fs.existsSync(file(n)) ? fs.readFileSync(file(n), 'utf8') : '');
    return { json: JSON.parse(text('latest.json')), md: text('latest.md'), html: text('latest.html') };
  } catch (e: any) {
    return { error: `無法讀取主機盤點報告：${e.message}` };
  }
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1500, height: 940, minWidth: 1024, minHeight: 640, show: false,
    title: 'iStudy Server App',
    backgroundColor: '#eef1ee',
    autoHideMenuBar: true,
    webPreferences: secureWebPreferences(path.join(__dirname, 'preload.js')),
  });
  Menu.setApplicationMenu(null);
  hardenContents(win.webContents);
  win.once('ready-to-show', () => { win?.maximize(); win?.show(); });
  void win.loadURL(APP_ORIGIN + '/index.html');
}

app.whenReady().then(() => {
  log.write('info', `Server App 啟動 ${versionLabel('server', build.version)}`);
  blockDownloads();
  serveRenderer(path.join(__dirname, 'renderer'), server);
  installHeaderInjection(ctx);
  registerBridge(ctx, (handle) => {
    handle('host:state', () => hostState());
    handle('host:bundles', () => findBundles());
    handle('host:uem-latest', () => uemLatest());
    handle('host:published-clients', () => publishedClients());
    handle('host:pick-clients', async () => {
      const opts = { title: '選擇新版 Client 安裝檔資料夾（原始碼包的 dist/clients）', properties: ['openDirectory' as const] };
      const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
      if (r.canceled || !r.filePaths[0]) return null;
      try {
        return { path: validClientsDir(r.filePaths[0]) };
      } catch (e: any) {
        return { error: e.message };
      }
    });
    handle('host:pick-bundle', async () => {
      const opts = { title: '選擇離線部署套件資料夾（istudy-server-…-offline）', properties: ['openDirectory' as const] };
      const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
      if (r.canceled || !r.filePaths[0]) return null;
      try {
        const dir = validBundle(r.filePaths[0]);
        const v = readJson(path.join(dir, 'VERSION.json'));
        return { path: dir, version: (v && v.server) || '?' };
      } catch (e: any) {
        return { error: e.message };
      }
    });
    handle('host:run', (_e, action: unknown, opts: unknown) => {
      try {
        return runHost(String(action), opts);
      } catch (e: any) {
        return { error: e.message };
      }
    });
    handle('host:cancel', (_e, runId: unknown) => {
      const c = runs.get(String(runId));
      if (c) c.kill('SIGTERM');
      return Boolean(c);
    });
  });
  createWindow();
  health.start();
});

app.on('window-all-closed', () => app.quit());
