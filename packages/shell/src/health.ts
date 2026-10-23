/**
 * 伺服器連線狀態的偵測（第 06 章「伺服器連線狀態的偵測方式」）。
 *
 * - 由主程序檢查；畫面只收到「檢查中／正常／無法連線／App 需要更新」與檢查時間，拿不到錯誤細節。
 * - GET /api/health，逾時 3 秒；3 秒內收到 200、JSON、ok=true、db="ok" 才算正常。
 * - App 開啟時第一次失敗就顯示 ✗；之後定期檢查要連續 2 次失敗才從 ✓ 改為 ✗；失敗中第一次成功就改回 ✓。
 * - 正常時每 30 秒一次；一出現失敗（含還沒改成 ✗ 的第一次）就改成每 10 秒，所以拔掉網路線約 10～40 秒內顯示 ✗。
 * - 按「重試」、睡眠喚醒、作業系統回報網路中斷或恢復（畫面的 offline／online 事件）時立即檢查：插回網路線後通常幾秒內改回 ✓。
 * - 本機版別的 API 等級低於 min_api_level：顯示「✗ App 需要更新」（連線本身正常）。
 */
import { request, RequestError } from './net';

export type HealthState = 'checking' | 'ok' | 'fail' | 'update_required';

export interface HealthSnapshot {
  state: HealthState;
  checkedAt: string | null;
  serverVersion?: string;
}

export interface HealthLogger {
  (level: 'info' | 'warn' | 'error', message: string): void;
}

export class HealthChecker {
  private snapshot: HealthSnapshot = { state: 'checking', checkedAt: null };
  private failures = 0;
  private firstDone = false;
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private listeners = new Set<(s: HealthSnapshot) => void>();

  constructor(private base: string, private apiLevel: number, private log: HealthLogger,
              private okInterval = 30000, private failInterval = 10000, private timeoutMs = 3000) {}

  get current(): HealthSnapshot {
    return { ...this.snapshot };
  }

  onChange(fn: (s: HealthSnapshot) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  start(): void {
    void this.check();
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** 心跳或任何 API 請求成功：視為連線正常並更新檢查時間。 */
  reportSuccess(): void {
    if (this.snapshot.state === 'fail') {
      this.failures = 0;
      this.set({ state: 'ok', checkedAt: new Date().toISOString(), serverVersion: this.snapshot.serverVersion });
      this.log('info', '伺服器連線恢復（API 請求成功）');
      this.schedule();
    } else if (this.snapshot.state === 'ok') {
      this.snapshot.checkedAt = new Date().toISOString();
    }
  }

  /** 任何 API 請求遇到連線錯誤：立即觸發一次 health 檢查。 */
  reportConnectionError(): void {
    void this.check();
  }

  async check(): Promise<HealthSnapshot> {
    if (this.running) return this.current;
    this.running = true;
    if (this.timer) clearTimeout(this.timer);
    try {
      const r = await request(this.base, 'GET', '/api/health', { timeoutMs: this.timeoutMs });
      const d = r.json;
      if (r.status !== 200) throw new RequestError(d && d.db === 'fail' ? 'db' : 'http', `HTTP ${r.status}`, r.ms, r.status);
      if (!d || typeof d !== 'object') throw new RequestError('format', '回應不是 JSON', r.ms);
      if (d.ok !== true || d.db !== 'ok') throw new RequestError('db', '資料庫失敗', r.ms);
      this.failures = 0;
      const minLevel = typeof d.min_api_level === 'number' ? d.min_api_level : 0;
      const state: HealthState = this.apiLevel < minLevel ? 'update_required' : 'ok';
      if (state !== this.snapshot.state) {
        this.log('info', state === 'ok' ? `伺服器連線正常（${r.ms}ms）` : `App API 等級 ${this.apiLevel} 低於伺服器最低 ${minLevel}`);
      }
      this.set({ state, checkedAt: new Date().toISOString(), serverVersion: String(d.version || '') });
    } catch (e: any) {
      const kind = e instanceof RequestError ? e.kind : 'network';
      const ms = e instanceof RequestError ? e.ms : 0;
      this.failures += 1;
      this.log('warn', `health 失敗：${kind}${e && e.status ? ' ' + e.status : ''}，${ms}ms，${this.base}`);
      const shouldFail = !this.firstDone || this.failures >= 2 || this.snapshot.state === 'fail' || this.snapshot.state === 'checking';
      if (shouldFail) {
        this.set({ state: 'fail', checkedAt: new Date().toISOString() });
      }
    } finally {
      this.firstDone = true;
      this.running = false;
      this.schedule();
    }
    return this.current;
  }

  private schedule(): void {
    if (this.timer) clearTimeout(this.timer);
    // dev.61：正常時每 30 秒；只要有失敗（含第一次失敗、尚未改為 ✗）就每 10 秒，斷線能更快顯示、恢復也更快
    const wait = this.snapshot.state === 'fail' || this.failures > 0 ? this.failInterval : this.okInterval;
    this.timer = setTimeout(() => void this.check(), wait);
  }

  private set(s: HealthSnapshot): void {
    const changed = s.state !== this.snapshot.state;
    this.snapshot = s;
    for (const fn of this.listeners) fn(this.current);
    if (changed) this.log('info', `連線狀態：${s.state}`);
  }
}
