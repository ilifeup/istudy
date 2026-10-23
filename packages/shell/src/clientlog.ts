/**
 * Client Log：存在本機 App 資料夾，保留 7 天；每天第一次啟動時上傳前一次執行的記錄（第 06、09 章）。
 * 不記錄 Token 與密碼。
 */
import * as fs from 'fs';
import * as path from 'path';

export interface LogEntry {
  time: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  message: string;
}

const KEEP_DAYS = 7;

function day(d = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

export function scrub(message: string): string {
  return message.replace(/Bearer\s+[A-Za-z0-9]+/gi, 'Bearer ***').replace(/"password"\s*:\s*"[^"]*"/gi, '"password":"***"');
}

export class ClientLog {
  private dir: string;

  constructor(userData: string) {
    this.dir = path.join(userData, 'logs');
    try {
      fs.mkdirSync(this.dir, { recursive: true });
    } catch {
      /* 寫不進去時只輸出到主控台 */
    }
    this.prune();
  }

  write(level: LogEntry['level'], message: string): void {
    const entry: LogEntry = { time: new Date().toISOString(), level, message: scrub(message).slice(0, 2000) };
    try {
      fs.appendFileSync(path.join(this.dir, `client-${day()}.log`), JSON.stringify(entry) + '\n', 'utf8');
    } catch {
      /* 忽略 */
    }
    if (process.env.ISTUDY_LOG_CONSOLE) console.log(`[${level}] ${entry.message}`);
  }

  private prune(): void {
    const cutoff = Date.now() - KEEP_DAYS * 86400000;
    try {
      for (const f of fs.readdirSync(this.dir)) {
        const m = /^client-(\d{4}-\d{2}-\d{2})\.log$/.exec(f);
        if (m && new Date(m[1] + 'T00:00:00').getTime() < cutoff) fs.unlinkSync(path.join(this.dir, f));
      }
    } catch {
      /* 忽略 */
    }
  }

  /** 尚未上傳的前幾天記錄（每天第一次啟動時上傳一次）。 */
  pendingUpload(): { entries: LogEntry[]; markDone: () => void } | null {
    const markFile = path.join(this.dir, 'uploaded.json');
    let uploaded: string[] = [];
    try {
      uploaded = JSON.parse(fs.readFileSync(markFile, 'utf8'));
    } catch {
      uploaded = [];
    }
    const today = day();
    if (uploaded.includes(today + ':checked')) return null;
    const files: string[] = [];
    try {
      for (const f of fs.readdirSync(this.dir).sort()) {
        const m = /^client-(\d{4}-\d{2}-\d{2})\.log$/.exec(f);
        if (m && m[1] < today && !uploaded.includes(m[1])) files.push(f);
      }
    } catch {
      return null;
    }
    const entries: LogEntry[] = [];
    for (const f of files) {
      try {
        for (const line of fs.readFileSync(path.join(this.dir, f), 'utf8').split('\n')) {
          if (!line.trim()) continue;
          const e = JSON.parse(line);
          if (e.level === 'warn' || e.level === 'error' || e.level === 'info') entries.push(e);
        }
      } catch {
        /* 損壞的行略過 */
      }
    }
    return {
      entries: entries.slice(-500),
      markDone: () => {
        const next = [...uploaded.filter((u) => u >= day(new Date(Date.now() - 30 * 86400000))),
          ...files.map((f) => f.slice(7, 17)), today + ':checked'];
        try {
          fs.writeFileSync(markFile, JSON.stringify(next), 'utf8');
        } catch {
          /* 忽略 */
        }
      },
    };
  }
}
