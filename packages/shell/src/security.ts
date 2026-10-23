/**
 * Electron 安全設定（第 16 章；Client 四個版別與 Server App 相同）：
 * contextIsolation、sandbox、不開 nodeIntegration；只允許連線到 Server 位址（CSP）；
 * 阻擋外部導航與新視窗；攔截下載；正式版關閉開發者工具；停用右鍵與 Ctrl+S／P／U、F12。
 */
import { app, BrowserWindow, protocol, session, WebContents } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

export const APP_ORIGIN = 'app://istudy';

/** 必須在 app ready 之前呼叫。 */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
  ]);
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
  '.otf': 'font/otf', '.json': 'application/json', '.ico': 'image/x-icon', '.map': 'application/json',
};

export function csp(serverOrigin: string): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: ${serverOrigin}`,
    `media-src 'self' blob: ${serverOrigin}`,
    `connect-src 'self' ${serverOrigin}`,
    "worker-src 'self' blob:",
    "font-src 'self' data:",
    "object-src 'none'",
    "frame-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ');
}

/** 以 app://istudy/ 提供畫面檔案（Electron 22 用 registerBufferProtocol；新版用 protocol.handle）。 */
export function serveRenderer(rootDir: string, serverOrigin: string): void {
  const resolve = (url: string): string | null => {
    const u = new URL(url);
    let p = decodeURIComponent(u.pathname);
    if (p === '/' || p === '') p = '/index.html';
    const full = path.normalize(path.join(rootDir, p));
    if (!full.startsWith(path.normalize(rootDir) + path.sep)) return null;
    return full;
  };
  const headersFor = (file: string): Record<string, string> => ({
    'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
    'Content-Security-Policy': csp(serverOrigin),
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'no-store',
  });
  const p: any = protocol;
  if (typeof p.handle === 'function') {
    p.handle('app', async (req: Request) => {
      const file = resolve(req.url);
      if (!file || !fs.existsSync(file)) return new Response('Not found', { status: 404 });
      return new Response(fs.readFileSync(file), { status: 200, headers: headersFor(file) });
    });
  } else {
    protocol.registerBufferProtocol('app', (req, cb) => {
      const file = resolve(req.url);
      if (!file || !fs.existsSync(file)) return cb({ statusCode: 404, data: Buffer.from('Not found') });
      const h = headersFor(file);
      cb({ statusCode: 200, mimeType: h['Content-Type'].split(';')[0], charset: 'utf-8', headers: h, data: fs.readFileSync(file) });
    });
  }
}

export function secureWebPreferences(preload: string): Electron.WebPreferences {
  return {
    preload,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    spellcheck: false,
    devTools: !app.isPackaged || process.env.ISTUDY_DEVTOOLS === '1',
  };
}

const BLOCKED_KEYS = new Set(['s', 'p', 'u', 'o']);

export function hardenContents(contents: WebContents): void {
  contents.on('will-navigate', (e, url) => {
    if (!url.startsWith(APP_ORIGIN + '/')) e.preventDefault();
  });
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('will-attach-webview', (e) => e.preventDefault());
  contents.on('before-input-event', (e, input) => {
    const key = (input.key || '').toLowerCase();
    if (input.type !== 'keyDown') return;
    if ((input.control || input.meta) && BLOCKED_KEYS.has(key)) e.preventDefault();
    if (key === 'f12' || ((input.control || input.meta) && input.shift && (key === 'i' || key === 'j' || key === 'c'))) {
      if (app.isPackaged && process.env.ISTUDY_DEVTOOLS !== '1') e.preventDefault();
    }
  });
  // 停用右鍵選單：不顯示任何選單（輸入框的複製貼上仍可用鍵盤）
  contents.on('context-menu', (e) => e.preventDefault());
}

export function blockDownloads(): void {
  session.defaultSession.on('will-download', (e) => e.preventDefault());
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
    cb(permission === 'fullscreen' || permission === 'clipboard-sanitized-write');
  });
}

export function applyAll(win: BrowserWindow): void {
  hardenContents(win.webContents);
}
