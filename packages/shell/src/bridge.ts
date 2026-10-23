/**
 * 主程序與畫面之間的橋接（Client App 與 Server App 共用）。
 * - Token 只放在主程序記憶體，由 onBeforeSendHeaders 對 Server 網址加上 Authorization；畫面拿不到 Token。
 * - 登入、登出由主程序呼叫 API；畫面的其他請求直接送到 Server（Header 自動注入）。
 * - 下載一律被攔截：匯出檔案由主程序顯示「另存新檔」對話框後寫入。
 */
import { app, BrowserWindow, clipboard, dialog, ipcMain, screen, session } from 'electron';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { ClientLog } from './clientlog';
import { BuildInfo, versionLabel } from './config';
import { HealthChecker } from './health';
import { request, RequestError } from './net';

export interface ShellContext {
  kind: 'client' | 'server';
  server: string;
  build: BuildInfo;
  log: ClientLog;
  health: HealthChecker;
  getWindow: () => BrowserWindow | null;
}

export class SessionStore {
  token: string | null = null;
  account: any = null;
}

export const store = new SessionStore();

export function osDescription(): string {
  if (process.platform === 'win32') return `Windows ${os.release()}`;
  if (process.platform === 'linux') {
    try {
      const m = /PRETTY_NAME="?([^"\n]+)"?/.exec(fs.readFileSync('/etc/os-release', 'utf8'));
      if (m) return m[1];
    } catch {
      /* 忽略 */
    }
    return `Linux ${os.release()}`;
  }
  return `${os.type()} ${os.release()}`;
}

export function physicalMacs(): string[] {
  const out = new Set<string>();
  const ifs = os.networkInterfaces();
  for (const name of Object.keys(ifs)) {
    if (/^(lo|docker|veth|br-|virbr|vmnet|vboxnet|tun|tap|zt)/i.test(name)) continue;
    for (const a of ifs[name] || []) {
      if (a.internal || !a.mac || a.mac === '00:00:00:00:00:00') continue;
      out.add(a.mac.toLowerCase());
    }
  }
  return Array.from(out).slice(0, 16);
}

/** 顯示卡（Q-05：Client 安裝時由 Super 上傳，用來決定各版別的測試機）。Electron 22 起都有 app.getGPUInfo。 */
export async function gpuInfo(): Promise<{ name: string; vendor_id: string; device_id: string; driver: string }[]> {
  try {
    const info: any = await Promise.race([app.getGPUInfo('complete'), new Promise((r) => setTimeout(() => r(null), 3000))]);
    if (!info || !info.gpuDevice) return [];
    const renderer = (info.auxAttributes && (info.auxAttributes.glRenderer || info.auxAttributes.glVendor)) || '';
    return info.gpuDevice.map((d: any, i: number) => ({
      name: (i === 0 && renderer) || d.deviceString || d.description || '',
      vendor_id: '0x' + Number(d.vendorId || 0).toString(16).padStart(4, '0'),
      device_id: '0x' + Number(d.deviceId || 0).toString(16).padStart(4, '0'),
      driver: [d.driverVendor, d.driverVersion].filter(Boolean).join(' '),
    }));
  } catch {
    return [];
  }
}

/** dev.61：主機板與 BIOS（Linux 的 /sys/class/dmi/id，一般使用者可讀；不讀序號、UUID）。其他系統或讀不到時是 null（畫面顯示「未知」）。 */
export function boardInfo(dir = '/sys/class/dmi/id'): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const k of ['board_vendor', 'board_name', 'board_version', 'bios_vendor', 'bios_version']) {
    try {
      out[k] = process.platform === 'linux' ? (fs.readFileSync(path.join(dir, k), 'utf8').trim() || null) : null;
    } catch {
      out[k] = null;
    }
  }
  return out;
}

export async function pcInfo(ctx: ShellContext): Promise<Record<string, unknown>> {
  const cpus = os.cpus();
  const ifs = os.networkInterfaces();
  const nets: { name: string; ip: string; mac: string }[] = [];
  for (const name of Object.keys(ifs)) {
    for (const a of ifs[name] || []) {
      if (!a.internal && a.family === 'IPv4') nets.push({ name, ip: a.address, mac: a.mac });
    }
  }
  return {
    os: osDescription(),
    arch: os.arch(),
    cpu: cpus[0] ? cpus[0].model : '',
    cpu_cores: cpus.length,
    memory_gb: Math.round((os.totalmem() / 1024 ** 3) * 10) / 10,
    hostname: os.hostname(),
    board: boardInfo(),
    network: nets,
    macs: physicalMacs(),
    gpu: await gpuInfo(),
    screens: screen.getAllDisplays().map((d) => ({ width: d.size.width, height: d.size.height, scale: d.scaleFactor })),
    app: { channel: ctx.build.channel, version: ctx.build.version, label: versionLabel(ctx.build.channel, ctx.build.version),
      electron: process.versions.electron, chrome: process.versions.chrome },
    collected_at: new Date().toISOString(),
  };
}

/** 對送往 Server 的請求加上 Authorization、電腦名稱與版別；其他網址不加。 */
export function installHeaderInjection(ctx: ShellContext): void {
  const filter = { urls: [ctx.server + '/*'] };
  session.defaultSession.webRequest.onBeforeSendHeaders(filter, (details, cb) => {
    const headers = { ...details.requestHeaders };
    if (store.token) headers.Authorization = 'Bearer ' + store.token;
    headers['X-iStudy-PC'] = os.hostname().slice(0, 64);
    headers['X-iStudy-Channel'] = ctx.build.channel;
    cb({ requestHeaders: headers });
  });
  session.defaultSession.webRequest.onCompleted(filter, (d) => {
    if (d.statusCode > 0 && d.statusCode < 500) ctx.health.reportSuccess();
  });
  session.defaultSession.webRequest.onErrorOccurred(filter, (d) => {
    if (/ERR_CONNECTION|ERR_NAME|ERR_ADDRESS|ERR_INTERNET|ERR_NETWORK|ERR_TIMED_OUT/.test(d.error)) ctx.health.reportConnectionError();
  });
}

function send(ctx: ShellContext, channel: string, payload: unknown): void {
  const w = ctx.getWindow();
  if (w && !w.isDestroyed()) w.webContents.send(channel, payload);
}

export function registerBridge(ctx: ShellContext, extra: (h: typeof ipcMain.handle) => void = () => undefined): void {
  ctx.health.onChange((s) => send(ctx, 'istudy:health-changed', s));

  ipcMain.handle('istudy:info', () => ({
    kind: ctx.kind,
    channel: ctx.build.channel,
    version: ctx.build.version,
    versionLabel: versionLabel(ctx.build.channel, ctx.build.version),
    apiLevel: ctx.build.apiLevel,
    apiBase: ctx.server,
    platform: process.platform,
    hostname: os.hostname(),
  }));
  ipcMain.handle('istudy:health', () => ctx.health.current);
  ipcMain.handle('istudy:health-retry', () => ctx.health.check());

  ipcMain.handle('istudy:login', async (_e, username: unknown, password: unknown) => {
    if (typeof username !== 'string' || typeof password !== 'string') return { ok: false, error: '請輸入帳號與密碼' };
    try {
      const r = await request(ctx.server, 'POST', '/api/login', { body: { username, password }, timeoutMs: 10000,
        headers: { 'X-iStudy-PC': os.hostname().slice(0, 64), 'X-iStudy-Channel': ctx.build.channel } });
      ctx.health.reportSuccess();
      if (r.status === 200 && r.json && r.json.token) {
        store.token = r.json.token;
        store.account = r.json.account;
        ctx.log.write('info', `登入：${username}`);
        return { ok: true, account: r.json.account };
      }
      const err = (r.json && r.json.error) || `登入失敗（HTTP ${r.status}）`;
      ctx.log.write('warn', `登入失敗：${username}（${r.status}）`);
      return { ok: false, error: err, code: r.json && r.json.code, retryAfter: r.json && r.json.retry_after };
    } catch (e: any) {
      ctx.health.reportConnectionError();
      ctx.log.write('warn', `登入請求失敗：${e instanceof RequestError ? e.kind : e}`);
      return { ok: false, error: '伺服器沒有回應，請確認網路後再試', code: 'network' };
    }
  });

  ipcMain.handle('istudy:logout', async () => {
    if (!store.token) return { ok: true };
    try {
      const r = await request(ctx.server, 'POST', '/api/logout', { body: {}, token: store.token, timeoutMs: 8000 });
      if (r.status === 200 || r.status === 401) {
        store.token = null;
        store.account = null;
        return { ok: true };
      }
      return { ok: false, error: '登出失敗，請重試' };
    } catch {
      // 連線失敗時保留 Token 供重試（第 10 章）
      return { ok: false, error: '伺服器沒有回應，登出尚未完成，請稍後重試' };
    }
  });

  ipcMain.handle('istudy:clear-session', () => {
    store.token = null;
    store.account = null;
    return true;
  });

  ipcMain.handle('istudy:login-config', async () => {
    const cache = path.join(app.getPath('userData'), 'login-config.json');
    try {
      const r = await request(ctx.server, 'GET', '/api/public/login-config', { timeoutMs: 3000 });
      if (r.status === 200 && r.json) {
        fs.writeFileSync(cache, JSON.stringify(r.json), 'utf8');
        return r.json;
      }
    } catch {
      /* 連不到 Server 時沿用上次取得的文字 */
    }
    try {
      return JSON.parse(fs.readFileSync(cache, 'utf8'));
    } catch {
      return { username_placeholder: '請輸入帳號', password_placeholder: '請輸入密碼' };
    }
  });

  ipcMain.handle('istudy:log', (_e, level: unknown, message: unknown) => {
    if (typeof message === 'string' && ['info', 'warn', 'error'].includes(String(level))) ctx.log.write(level as any, message);
  });

  ipcMain.handle('istudy:save-file', async (_e, defaultName: unknown, content: unknown) => {
    if (typeof defaultName !== 'string' || typeof content !== 'string') return { ok: false };
    const w = ctx.getWindow();
    const opts = { defaultPath: path.join(app.getPath('documents'), defaultName.replace(/[\\/:*?"<>|]/g, '_')) };
    const r = w ? await dialog.showSaveDialog(w, opts) : await dialog.showSaveDialog(opts);
    if (r.canceled || !r.filePath) return { ok: false, canceled: true };
    fs.writeFileSync(r.filePath, content, 'utf8');
    return { ok: true, path: path.basename(r.filePath) };
  });

  ipcMain.handle('istudy:open-text-file', async (_e, kind: unknown) => {
    const filters = kind === 'subtitle' ? [{ name: '字幕（SRT、VTT）', extensions: ['srt', 'vtt'] }] : [{ name: '文字檔', extensions: ['txt', 'csv'] }];
    const w = ctx.getWindow();
    const r = w ? await dialog.showOpenDialog(w, { properties: ['openFile'], filters }) : await dialog.showOpenDialog({ properties: ['openFile'], filters });
    if (r.canceled || !r.filePaths[0]) return null;
    const file = r.filePaths[0];
    if (fs.statSync(file).size > 8 * 1024 * 1024) return { error: '檔案超過 8MB' };
    return { name: path.basename(file), content: fs.readFileSync(file).toString('base64') };
  });

  ipcMain.handle('istudy:pc-info', () => pcInfo(ctx));
  ipcMain.handle('istudy:copy', (_e, text: unknown) => {
    if (typeof text === 'string') clipboard.writeText(text);
    return true;
  });
  ipcMain.handle('istudy:upload-pc-info', async () => {
    if (!store.token) return { ok: false, error: '請先登入' };
    try {
      const r = await request(ctx.server, 'POST', '/api/client/info', { body: await pcInfo(ctx), token: store.token });
      return r.status === 200 ? { ok: true, ...r.json } : { ok: false, error: (r.json && r.json.error) || `HTTP ${r.status}` };
    } catch {
      return { ok: false, error: '伺服器沒有回應' };
    }
  });
  ipcMain.handle('istudy:set-fullscreen', (_e, on: unknown) => {
    const w = ctx.getWindow();
    if (w) w.setFullScreen(Boolean(on));
    return true;
  });

  extra(ipcMain.handle.bind(ipcMain));
}
