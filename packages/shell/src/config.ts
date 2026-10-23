/**
 * Server 位址：由安裝設定檔決定，不在任何畫面顯示，使用者不能修改（第 06 章）。
 *   Windows：%ProgramData%\iStudy\client.json；Ubuntu：/etc/istudy/client.json
 *   內容例如 {"server":"http://10.3.3.100:4080"}；檔案不存在時使用打包時內建的預設值。
 */
import * as fs from 'fs';
import * as path from 'path';

export const DEFAULT_SERVER = 'http://10.3.3.100:4080';

export function clientConfigPath(): string {
  if (process.platform === 'win32') {
    const base = process.env.ProgramData || 'C:\\ProgramData';
    return path.join(base, 'iStudy', 'client.json');
  }
  return '/etc/istudy/client.json';
}

export function normalizeServer(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const m = /^http:\/\/([A-Za-z0-9.\-]+)(?::(\d{1,5}))?\/?$/.exec(v.trim());
  if (!m) return null;
  const port = m[2] ? Number(m[2]) : 80;
  if (port < 1 || port > 65535) return null;
  return `http://${m[1]}${m[2] ? ':' + m[2] : ''}`;
}

export function readServerAddress(log?: (m: string) => void): string {
  const override = normalizeServer(process.env.ISTUDY_SERVER || '');
  if (override) return override; // 開發與測試用
  const file = clientConfigPath();
  try {
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
    const s = normalizeServer(raw && raw.server);
    if (s) return s;
    log?.(`client.json 的 server 格式錯誤，改用預設值（${file}）`);
  } catch (e: any) {
    if (e && e.code === 'ENOENT') log?.(`找不到 ${file}，使用內建預設位址 ${DEFAULT_SERVER}`);
    else log?.(`無法讀取 ${file}：${e && e.message}，使用內建預設位址 ${DEFAULT_SERVER}`);
  }
  return DEFAULT_SERVER;
}

export interface BuildInfo {
  channel: 'win7' | 'win10' | 'win11' | 'ubuntu' | 'server';
  version: string;
  apiLevel: number;
}

/** 建置時寫入的版別資訊（resources/build-info.json）；開發時用環境變數。 */
export function readBuildInfo(resourcesPath: string, fallbackVersion: string): BuildInfo {
  const candidates = [path.join(resourcesPath, 'build-info.json'), path.join(__dirname, 'build-info.json')];
  for (const f of candidates) {
    try {
      const d = JSON.parse(fs.readFileSync(f, 'utf8'));
      if (d && d.channel && d.version) return { channel: d.channel, version: String(d.version), apiLevel: Number(d.apiLevel || 1) };
    } catch {
      /* 下一個 */
    }
  }
  return {
    channel: (process.env.ISTUDY_CHANNEL as BuildInfo['channel']) || 'win10',
    version: process.env.ISTUDY_VERSION || fallbackVersion,
    apiLevel: Number(process.env.ISTUDY_API_LEVEL || 1),
  };
}

export const CHANNEL_LABEL: Record<string, string> = { win7: 'Win7', win10: 'Win10', win11: 'Win11', ubuntu: 'Ubuntu', server: 'Server' };

/** 頁尾顯示：「版別 V主.次」，修訂號不為 0 時一併顯示；開發版顯示完整版本（第 19 章）。 */
export function versionLabel(channel: string, version: string): string {
  const label = CHANNEL_LABEL[channel] || channel;
  if (version.includes('-')) return `${label} V${version}`;
  const p = version.split('.');
  const v = p.length >= 3 && p[2] !== '0' ? `${p[0]}.${p[1]}.${p[2]}` : `${p[0] || 0}.${p[1] || 0}`;
  return `${label} V${v}`;
}
