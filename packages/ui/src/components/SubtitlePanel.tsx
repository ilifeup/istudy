/**
 * 進階字幕面板（第 07 章）：
 * - 相鄰字幕片段在畫面上合併成約 25 秒一組（以片段邊界對齊，約 3～5 行）；原始字幕不修改。
 * - 目前段落以粗框線＋「◀ 目前段落」標示並自動捲到可見位置；不做逐字高亮。
 * - 搜尋目前影片的字幕；點時間或片段跳到該時間。
 * - 匯入／匯出只對 Super 與經授權的 Admin 顯示（API 也會再檢查）。
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, errText, fetchText } from '../api';
import { bridge, decodeBase64 } from '../bridge';
import { Btn, Msg, useApp } from '../core';
import { clock } from '../format';

export interface Segment { start: number; end: number; text: string }
export interface Group { start: number; end: number; lines: Segment[] }

export function groupSegments(segs: Segment[], windowSec = 25, maxLines = 5): Group[] {
  const groups: Group[] = [];
  let cur: Group | null = null;
  for (const s of segs) {
    if (!cur || s.start - cur.start >= windowSec || cur.lines.length >= maxLines) {
      cur = { start: s.start, end: s.end, lines: [] };
      groups.push(cur);
    }
    cur.lines.push(s);
    cur.end = Math.max(cur.end, s.end);
  }
  return groups;
}

export function SubtitlePanel(props: {
  mediaId: number; title: string; segments: Segment[]; currentTime: number; onSeek: (t: number) => void;
  canImportExport: boolean; onImported: () => void; serverApp?: boolean;
}): any {
  const { toast } = useApp();
  const [q, setQ] = useState('');
  const listRef = useRef<HTMLDivElement | null>(null);
  const groups = useMemo(() => groupSegments(props.segments), [props.segments]);
  const currentIdx = groups.findIndex((g, i) => props.currentTime >= g.start && (i === groups.length - 1 || props.currentTime < groups[i + 1].start));
  const hits = q.trim() ? props.segments.filter((s) => s.text.includes(q.trim())) : [];

  useEffect(() => {
    if (q.trim() || currentIdx < 0 || !listRef.current) return;
    const el = listRef.current.querySelector(`[data-group="${currentIdx}"]`) as HTMLElement | null;
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [currentIdx, q]);

  const doExport = async (format: 'srt' | 'vtt') => {
    try {
      const r = await fetchText(`/api/media/${props.mediaId}/subtitle/export?format=${format}`);
      const res = await bridge.saveFile(r.filename || `${props.title}.${format}`, r.text);
      if (res.ok) toast(`已匯出 ${format.toUpperCase()}：${res.path}`, 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const doImport = async () => {
    const f = await bridge.openTextFile('subtitle');
    if (!f) return;
    if (f.error) return toast(f.error, 'error');
    if (!window.confirm(`確定要把「${f.name}」匯入到教材 ${props.mediaId}「${props.title}」？\n匯入會覆蓋現有字幕。`)) return;
    try {
      const r = await api.post(`/api/media/${props.mediaId}/subtitle/import`, { content: decodeBase64(f.content), confirm_media_id: props.mediaId, filename: f.name });
      toast(`已排入匯入（${r.cues} 段），完成後重新開啟字幕面板即可看到`, 'ok', 6000);
      setTimeout(props.onImported, 4000);
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const queueWhisper = async () => {
    try {
      await api.post(`/api/admin/subtitles/${props.mediaId}/run`, {});
      toast('已排入字幕產生', 'ok');
    } catch (e) {
      toast(errText(e), 'error');
    }
  };

  return (
    <section className="subtitle-panel" aria-label="進階字幕面板">
      <div className="row between">
        <strong>進階字幕</strong>
        <div className="row">
          <label className="search-inline">搜尋：<input value={q} onChange={(e: any) => setQ(e.target.value)} placeholder="字幕文字" /></label>
          {q ? <Btn small kind="quiet" onClick={() => setQ('')}>✕</Btn> : null}
          {props.canImportExport ? (
            <>
              <Btn small onClick={doImport}>匯入 SRT/VTT</Btn>
              {props.segments.length ? <Btn small onClick={() => doExport('srt')}>匯出 SRT</Btn> : null}
              {props.segments.length ? <Btn small onClick={() => doExport('vtt')}>匯出 VTT</Btn> : null}
            </>
          ) : null}
        </div>
      </div>
      {!props.segments.length ? (
        <div className="stack">
          <Msg kind="info">此影片尚無字幕</Msg>
          {props.serverApp ? <Btn onClick={queueWhisper}>排入字幕產生</Btn> : null}
        </div>
      ) : q.trim() ? (
        <div className="sub-list" ref={listRef}>
          {!hits.length ? <Msg kind="info">找不到「{q}」</Msg> : null}
          {hits.map((s, i) => (
            <button key={i} type="button" className="sub-group" onClick={() => props.onSeek(s.start)}>
              <span className="sub-time">{clock(s.start)}</span>
              <span className="sub-text">{s.text}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="sub-list" ref={listRef}>
          {groups.map((g, i) => (
            <button key={i} type="button" data-group={i} className={'sub-group' + (i === currentIdx ? ' current' : '')}
              aria-current={i === currentIdx ? 'true' : undefined} onClick={() => props.onSeek(g.start)}>
              <span className="sub-time">{clock(g.start)}–{clock(g.end).slice(3)}</span>
              <span className="sub-text">{g.lines.map((l) => l.text).join('')}</span>
              {i === currentIdx ? <span className="sub-current">◀ 目前段落</span> : null}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export function segmentsToVtt(segs: Segment[]): string {
  const t = (s: number) => clock(s) + '.' + String(Math.round((s % 1) * 1000)).padStart(3, '0');
  return 'WEBVTT\n\n' + segs.map((s) => `${t(s.start)} --> ${t(s.end)}\n${s.text}\n`).join('\n');
}
