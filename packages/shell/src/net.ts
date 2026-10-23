/**
 * 主程序的 HTTP 請求（Node 內建 http 模組）。
 * 不使用 Electron 25 之後才有的 net.fetch，讓 Electron 22（Win7 版）與目前版本共用同一段程式（第 06 章）。
 */
import * as http from 'http';
import { URL } from 'url';

export interface HttpResult {
  status: number;
  body: string;
  json: any;
  headers: http.IncomingHttpHeaders;
  ms: number;
}

export type FailKind = 'refused' | 'not_found' | 'timeout' | 'network' | 'http' | 'format' | 'db';

export class RequestError extends Error {
  constructor(public kind: FailKind, message: string, public ms: number, public status?: number) {
    super(message);
  }
}

export function request(base: string, method: string, path: string, opts: {
  body?: unknown; token?: string | null; timeoutMs?: number; headers?: Record<string, string>;
} = {}): Promise<HttpResult> {
  const started = Date.now();
  const url = new URL(path, base);
  const payload = opts.body === undefined ? undefined : Buffer.from(JSON.stringify(opts.body), 'utf8');
  const headers: Record<string, string> = { Accept: 'application/json', ...(opts.headers || {}) };
  if (payload) {
    headers['Content-Type'] = 'application/json';
    headers['Content-Length'] = String(payload.length);
  }
  if (opts.token) headers.Authorization = 'Bearer ' + opts.token;
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: url.hostname, port: url.port || 80, path: url.pathname + url.search, method, headers,
    }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c: Buffer) => chunks.push(c));
      res.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        let json: any = null;
        try {
          json = body ? JSON.parse(body) : null;
        } catch {
          json = null;
        }
        resolve({ status: res.statusCode || 0, body, json, headers: res.headers, ms: Date.now() - started });
      });
      res.on('error', (e) => reject(new RequestError('network', e.message, Date.now() - started)));
    });
    req.setTimeout(opts.timeoutMs ?? 10000, () => {
      req.destroy(new RequestError('timeout', '逾時', Date.now() - started));
    });
    req.on('error', (e: any) => {
      if (e instanceof RequestError) return reject(e);
      const ms = Date.now() - started;
      const code = e && e.code;
      if (code === 'ECONNREFUSED') reject(new RequestError('refused', '連線被拒', ms));
      else if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') reject(new RequestError('not_found', '找不到主機', ms));
      else if (code === 'ETIMEDOUT') reject(new RequestError('timeout', '逾時', ms));
      else reject(new RequestError('network', '網路不通' + (code ? `（${code}）` : ''), ms));
    });
    if (payload) req.write(payload);
    req.end();
  });
}
