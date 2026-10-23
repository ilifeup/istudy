/** 顯示格式：時間、長度、大小、約略位置。 */

export function clock(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return '00:00:00';
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

/** 清單用長度：2:58:10 或 12:05 */
export function duration(sec: number | null | undefined): string {
  if (!sec || !isFinite(sec)) return '';
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${m}:${String(ss).padStart(2, '0')}`;
}

export function size(n: number | null | undefined): string {
  if (n === null || n === undefined) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return i === 0 ? `${v} B` : `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

/** 學習歷程的約略位置：「約 X 分鐘」「約 X 小時 Y 分鐘」（第 08 章）。 */
export function approxPosition(sec: number | null | undefined): string {
  if (!sec || sec < 60) return '約 1 分鐘內';
  const totalMin = Math.round(sec / 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `約 ${m} 分鐘`;
  return m === 0 ? `約 ${h} 小時` : `約 ${h} 小時 ${m} 分鐘`;
}

export function dateTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}${withSeconds ? ':' + p(d.getSeconds()) : ''}`;
}

export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return '';
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 30) return '剛剛';
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} 分鐘前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小時前`;
  return `${Math.round(s / 86400)} 天前`;
}

export const LEARNING: Record<string, { mark: string; text: string }> = {
  not_started: { mark: '○', text: '未開始' },
  in_progress: { mark: '◐', text: '學習中' },
  completed: { mark: '✓', text: '已完成' },
};

export function learningText(s: string | null | undefined): string {
  const v = LEARNING[s || 'not_started'] || LEARNING.not_started;
  return `${v.mark} ${v.text}`;
}

export const KIND_ICON: Record<string, string> = { video: '🎬', pdf: '📄', office: '📄', text: '📃', image: '🖼', folder: '📁' };
export const KIND_TEXT: Record<string, string> = { video: '影片', pdf: '文件', office: '文件', text: '文字', image: '圖片', folder: '資料夾' };

/** 自然排序（0001 → 0002 → 0010） */
const collator = new Intl.Collator('zh-Hant', { numeric: true, sensitivity: 'base' });
export const naturalCompare = (a: string, b: string) => collator.compare(a, b);
