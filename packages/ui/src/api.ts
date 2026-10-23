/**
 * API 呼叫：請求直接送到 Server，Authorization 由主程序注入（畫面拿不到 Token）。
 * 錯誤一律轉成白話訊息；Session 失效時通知 App 回到登入頁。
 */
import { cachedInfo } from './bridge';

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public extra: Record<string, any> = {}) {
    super(message);
  }
}

type Listener = () => void;
const expiredListeners = new Set<Listener>();
export function onSessionExpired(fn: Listener): () => void {
  expiredListeners.add(fn);
  return () => expiredListeners.delete(fn);
}

export function apiUrl(path: string): string {
  return cachedInfo().apiBase + path;
}

async function parse(res: Response): Promise<any> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // 非 JSON 回應（沿用 dev.43：顯示可讀訊息）
    throw new ApiError(res.status, `伺服器回應格式錯誤（HTTP ${res.status}）`, 'bad_response');
  }
}

export async function call<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'omit',
      cache: 'no-store',
    });
  } catch {
    throw new ApiError(0, 'Server 沒有回應，請確認網路後按「重試」', 'network');
  }
  const data = await parse(res);
  if (!res.ok) {
    const msg = (data && data.error) || `發生錯誤（HTTP ${res.status}）`;
    if (res.status === 401 && data && (data.code === 'session_expired' || data.code === 'login_required')) {
      for (const fn of expiredListeners) fn();
    }
    throw new ApiError(res.status, msg, data && data.code, data || {});
  }
  return data as T;
}

export const api = {
  get: <T = any>(p: string) => call<T>('GET', p),
  post: <T = any>(p: string, b: unknown = {}) => call<T>('POST', p, b),
  put: <T = any>(p: string, b: unknown = {}) => call<T>('PUT', p, b),
  patch: <T = any>(p: string, b: unknown = {}) => call<T>('PATCH', p, b),
  del: <T = any>(p: string, b: unknown = {}) => call<T>('DELETE', p, b),
};

/** 文字內容（字幕匯出、TXT、CSV）。 */
export async function fetchText(path: string): Promise<{ text: string; filename: string | null }> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { credentials: 'omit', cache: 'no-store' });
  } catch {
    throw new ApiError(0, 'Server 沒有回應，請確認網路後按「重試」', 'network');
  }
  if (!res.ok) {
    let msg = `發生錯誤（HTTP ${res.status}）`;
    try {
      const d = JSON.parse(await res.text());
      if (d && d.error) msg = d.error;
    } catch {
      /* 非 JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  return { text: await res.text(), filename: m ? decodeURIComponent(m[1]) : null };
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export const qs = (o: Record<string, string | number | boolean | undefined | null>): string => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return p.length ? '?' + p.join('&') : '';
};
