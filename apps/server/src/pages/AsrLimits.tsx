/**
 * dev.61：ASR 單檔長度建議（Super 介面所有用到 ASR 的頁面共用：「字幕」「Outgo（錄音轉文字）」）。
 *
 * 依據（iStudy 的 ASR 流程，與格式無關的部分）：
 * - 任何格式都先由 FFmpeg 轉成 16 kHz 單聲道 WAV，再 VAD 切段（每段 ≤ 20 秒）送進 FunASR：
 *   記憶體不隨長度增加，辨識速度與準確度跟原始格式無關，只跟「長度」有關。
 * - 一個檔案＝一個工作，從頭到尾一次完成：中途重開機或 Agent 重啟會從頭再辨識（最多 3 次）；
 *   Agent 一次只做一個工作，長錄音辨識期間，轉檔與文件轉換會排隊等候。
 * - 暫存 WAV 每小時約 115 MB（在系統 SSD）。
 * 因此「最佳」與「上限建議」對所有格式相同；格式之間的差別只在檔案大小、完整性與音質（見各列說明）。
 */
import React from 'react';

export const ASR_BEST_HOURS = 3;     // 最佳：單檔 3 小時內
export const ASR_MAX_HOURS = 5;      // 上限建議：超過請先分段

type Row = [string, string];

const VIDEO_ROWS: Row[] = [
  ['影片：MTS、M2TS、MP4、MOV、MKV、AVI、WMV、MPG 等', '產生字幕時先抽出音軌，之後與錄音相同'],
];

const AUDIO_ROWS: Row[] = [
  ['MP3、M4A、AAC、WMA、OGG／OGA、OPUS、FLAC、MKA、AC3、WV', '壓縮格式：檔案小，速度與準確度跟格式無關'],
  ['WAV、AIF／AIFF（未壓縮）', '檔案大（48 kHz 立體聲每小時約 660 MB）；一般 WAV 單檔上限 4 GB（約 6 小時），錄音機通常會自動分檔'],
  ['AMR、3GA（手機、電話錄音）', '8 kHz 窄頻：可以辨識，但準確度通常比一般錄音低'],
  ['M4A（手機錄音中斷、沒有正常結束）', '檔案可能無法開啟（缺少索引），與長度無關；辨識會顯示「抽取音訊失敗」'],
];

/** 速度（RTF＝處理時間 ÷ 長度）→ 每小時約幾分鐘；沒有紀錄時回傳 null。 */
export function minutesPerHour(rtf: number | null | undefined): number | null {
  return typeof rtf === 'number' && rtf > 0 ? Math.max(1, Math.round(rtf * 60)) : null;
}

/** 影片長度（秒）超過建議時的提醒文字（不是錯誤：仍然可以辨識）。 */
export function lengthWarning(sec: number | null | undefined): string | null {
  if (typeof sec !== 'number' || !isFinite(sec)) return null;
  if (sec > ASR_MAX_HOURS * 3600) return `⚠ 超過 ${ASR_MAX_HOURS} 小時：建議先分段`;
  if (sec > ASR_BEST_HOURS * 3600) return `⚠ 超過 ${ASR_BEST_HOURS} 小時（仍可辨識）`;
  return null;
}

export function AsrLengthHint(props: { kind: 'video' | 'audio'; speed?: { rtf: number | null; samples: number } | null }): any {
  const rows = props.kind === 'video' ? VIDEO_ROWS : AUDIO_ROWS;
  const sp = props.speed;
  const mph = sp ? minutesPerHour(sp.rtf) : null;
  return (
    <details className="asr-hint" open>
      <summary>ASR 單檔長度建議：最佳 {ASR_BEST_HOURS} 小時內、上限 {ASR_MAX_HOURS} 小時（{props.kind === 'video' ? '每支影片' : '每個錄音檔'}）</summary>
      <table className="grid small">
        <thead><tr><th>格式</th><th className="nowrap">最佳（單檔）</th><th className="nowrap">上限建議（單檔）</th><th>說明</th></tr></thead>
        <tbody>{rows.map(([fmt, note]) => (
          <tr key={fmt}><td>{fmt}</td><td className="nowrap">{ASR_BEST_HOURS} 小時內</td><td className="nowrap">{ASR_MAX_HOURS} 小時</td><td>{note}</td></tr>
        ))}</tbody>
      </table>
      <p className="dim">所有格式都先轉成 16 kHz 單聲道再分段辨識，所以建議長度相同。一個檔案是一個工作、一次做完：
        中途重新開機或 Agent 重啟會從頭再辨識；辨識期間轉檔與文件轉換會排隊等候。超過 {ASR_MAX_HOURS} 小時仍可辨識，但請先分段（例如每堂課一個檔案）。
        每次辨識（成功或失敗）都寫進「統計與報告」的 ASR 處理記錄（檔案、音訊品質、結果），可匯出。
        {mph !== null ? ` 這台 Server 最近 ${sp!.samples} 次辨識的速度：RTF ${sp!.rtf}，1 小時約 ${mph} 分鐘。`
          : ' 這台 Server 還沒有辨識速度紀錄（第一次辨識完成後會顯示 1 小時約需幾分鐘）。'}</p>
    </details>
  );
}

/** 一行版（排程設定、首次設定精靈的字幕段落）。 */
export function AsrLengthLine(): any {
  return <p className="dim">ASR 單檔長度建議：影片或錄音每個檔案最佳 {ASR_BEST_HOURS} 小時內、上限 {ASR_MAX_HOURS} 小時（各格式說明見「字幕」與「Outgo（錄音轉文字）」頁）。</p>;
}

/** dev.61 中文音訊品質（粗略估計）：符號＋文字，不只靠顏色。 */
export const QUALITY_TEXT: Record<string, string> = { high: '● 高', medium: '◐ 中', low: '○ 低', unknown: '？ 未知' };
export const QUALITY_HELP = '音訊品質是粗略估計：依背景雜音（訊噪比）、人聲音量、爆音、頻寬，以及辨識不出文字的比例、每秒辨識字數判斷，取最差的一項。'
  + '高＝雜音少、音量足、辨識正常；中＝有一項偏弱（例如雜音偏高、電話錄音），結果建議抽查；低＝雜音大、太小聲或爆音、很多段辨識不出來，建議改善錄音或人工校對。';

export function QualityTag(props: { level?: string | null; reasons?: string[] | null; max?: number }): any {
  const lv = props.level || 'unknown';
  const rs = (props.reasons || []).slice(0, props.max ?? 3);
  return (
    <span className="quality">
      <span>{QUALITY_TEXT[lv] || lv}</span>
      {rs.map((x, i) => <div key={i} className="dim">· {x}</div>)}
    </span>
  );
}
