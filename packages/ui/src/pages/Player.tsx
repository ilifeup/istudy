/**
 * 影片播放器（第 07 章）：上一集／下一集（同資料夾自然排序）、合集標示、0.5～3.0 倍速、音量、字幕開關、進階字幕面板、
 * Tag、最愛、書籤、學習狀態、全螢幕（同一個 <video> 元件，只切換外層樣式）。
 * - 開啟時記錄一次瀏覽；播放中每 30 分鐘與登出時儲存位置；不自動跳到上次位置。
 * - controlsList="nodownload"、停用右鍵與畫中畫；疊半透明浮水印。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, apiUrl, errText } from '../api';
import { cachedInfo } from '../bridge';
import { Btn, LoadState, Msg, useApp, useInterval, useLocalState } from '../core';
import { approxPosition, clock } from '../format';
import { PersonalControls, Watermark } from '../components/MediaControls';
import { Segment, segmentsToVtt, SubtitlePanel } from '../components/SubtitlePanel';
import { NoAccess, stateText, useNoAccess } from './Courses';

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
/** 倍速文字：1x、1.25x、0.5x（dev.62：原本 1 倍會顯示成「1.x」）。 */
export function speedLabel(s: number): string {
  return `${Number(s.toFixed(2))}x`;
}
const SAVE_EVERY_MS = 30 * 60 * 1000;

export function PlayerPage(props: { params: Record<string, any> }): any {
  const { go, back, onBeforeLogout, toast } = useApp();
  const id = Number(props.params.id);
  const [m, setM] = useState<any>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [trackUrl, setTrackUrl] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const [volume, setVolume] = useLocalState<number>('istudy.volume', 0.8);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(1);
  const [ccOn, setCcOn] = useLocalState<boolean>('istudy.cc', true);
  const [panelOpen, setPanelOpen] = useLocalState<boolean>('istudy.subPanel', false);
  const [fs, setFs] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [playError, setPlayError] = useState<string | null>(null);
  const [clipOpen, setClipOpen] = useState(false);
  const idleTimer = useRef<any>(null);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      // dev.62：教材資料與字幕同時讀取（以前是讀完資料才讀字幕，字幕越長影片越晚開始緩衝）
      const segReq = api.get(`/api/media/${id}/subtitle/segments`).catch(() => null);
      const d = await api.get(`/api/media/${id}`);
      setM(d);
      void api.put(`/api/media/${id}/progress`, { opened: true }).catch(() => undefined); // 開啟時記錄一次瀏覽
      const s = d.has_subtitle ? await segReq : null;
      setSegments((s && s.segments) || []);
    } catch (e) {
      setLoadErr(e);
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  // 字幕：以片段產生 WebVTT（blob），同步 textTracks[].mode
  useEffect(() => {
    if (!segments.length) {
      setTrackUrl(null);
      return;
    }
    const url = URL.createObjectURL(new Blob([segmentsToVtt(segments)], { type: 'text/vtt' }));
    setTrackUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [segments]);
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    for (let i = 0; i < v.textTracks.length; i++) v.textTracks[i].mode = ccOn ? 'showing' : 'hidden';
  }, [ccOn, trackUrl, m]);

  // 位置儲存：每 30 分鐘、離開播放器、登出前
  const savePosition = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !isFinite(v.currentTime) || v.currentTime < 1) return;
    await api.put(`/api/media/${id}/progress`, { position_sec: Math.floor(v.currentTime) }).catch(() => undefined);
  }, [id]);
  useInterval(() => { if (playing) void savePosition(); }, SAVE_EVERY_MS);
  useEffect(() => onBeforeLogout(savePosition), [onBeforeLogout, savePosition]);
  useEffect(() => () => { void savePosition(); }, [savePosition]);

  useEffect(() => {
    const v = videoRef.current;
    if (v) {
      v.volume = volume;
      v.muted = muted;
    }
  }, [volume, muted, m]);

  // 全螢幕：Fullscreen API（Win7 版的 Chromium 108 同樣支援）
  useEffect(() => {
    const onFs = () => setFs(document.fullscreenElement === wrapRef.current);
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);
  const toggleFs = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (wrapRef.current) await wrapRef.current.requestFullscreen();
    } catch (e) {
      toast('無法切換全螢幕：' + errText(e), 'error');
    }
  };
  const poke = () => {
    setControlsVisible(true);
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => setControlsVisible(false), 3000);
  };
  const hideControls = fs && playing && !controlsVisible && !menuOpen;

  const playPause = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play().catch((e: any) => setPlayError(errText(e)));
    else v.pause();
  };
  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = Math.max(0, Math.min(t, v.duration || t));
  };

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.querySelector('.modal')) return;
      if (e.key === ' ') { e.preventDefault(); playPause(); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); seek((videoRef.current?.currentTime || 0) - 10); }
      if (e.key === 'ArrowRight') { e.preventDefault(); seek((videoRef.current?.currentTime || 0) + 10); }
      if (fs) poke();
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  });

  const onVideoError = async () => {
    // 播放錯誤時顯示可理解的原因
    try {
      const r = await fetch(apiUrl(`/api/media/${id}/stream`), { headers: { Range: 'bytes=0-0' }, cache: 'no-store' });
      if (r.ok) setPlayError('⚠ 播放發生錯誤，請按重試');
      else if (r.status === 409) setPlayError('⏳ 此影片尚未轉檔完成');
      else if (r.status === 403) setPlayError('🔒 無權限');
      else if (r.status === 503 || r.status === 410) setPlayError('⚠ Server 無法讀取檔案');
      else if (r.status === 401) setPlayError('⚠ 登入已過期，請重新登入');
      else setPlayError(`⚠ 播放發生錯誤（HTTP ${r.status}）`);
    } catch {
      setPlayError('⚠ 連線中斷，請按重試');
    }
  };

  const course = useNoAccess(loadErr);
  if (course !== null) return <NoAccess course={course} />;
  if (loadErr) return <LoadState loading={false} error={errText(loadErr)} reload={load} />;
  // dev.62：教材資料還在讀取時就先建立 <video> 並開始緩衝（preload=auto），按播放時開頭不會卡；
  //        讀取中與讀取後的畫面結構相同（影片框都在同一個位置），所以同一個 <video> 會沿用、不會重新載入。
  const notReady = m ? stateText(m) : null;
  const pl: { id: number; title: string }[] = m ? m.playlist || [] : [];
  const idx: number | null = m ? m.playlist_index : null;
  const prev = idx !== null && idx > 0 ? pl[idx - 1] : null;
  const next = idx !== null && idx < pl.length - 1 ? pl[idx + 1] : null;
  const path = m ? (m.breadcrumbs || []).map((b: any) => b.name).join(' / ') : '';
  const startAt = Number(props.params.start || 0);
  const serverApp = cachedInfo().kind === 'server';

  const navRow = !m ? (
    <div className="player-top">
      <Btn small onClick={back}>← 返回</Btn>
      <span className="dim">… 讀取中（影片已開始緩衝）</span>
    </div>
  ) : (
    <div className="player-top">
      {fs ? <Btn small onClick={toggleFs}>✕ 退出全螢幕</Btn> : <Btn small onClick={back}>← 返回</Btn>}
      <Btn small disabled={!prev} title={prev ? prev.title : '已是第一集'} onClick={() => prev && go('player', { id: prev.id })}>⏮ 上一集</Btn>
      <span>{idx !== null ? `第 ${idx + 1} / ${pl.length} 集` : '單支影片'}</span>
      <Btn small disabled={!next} title={next ? next.title : '已是最後一集'} onClick={() => next && go('player', { id: next.id })}>下一集 ⏭</Btn>
      <span className="dim path">{path}{path ? ' / ' : ''}{m.file_name}</span>
    </div>
  );

  return (
    <div className="player-page">
      <div ref={wrapRef} className={'player-wrap' + (fs ? ' fullscreen' : '') + (hideControls ? ' idle' : '')}
        onMouseMove={fs ? poke : undefined} onClick={fs ? poke : undefined}>
        {navRow}
        {m && m.clip ? (
          <div className="clip-row">
            <button type="button" className="clip-badge" onClick={() => setClipOpen(!clipOpen)} aria-expanded={clipOpen}>
              ［合集 {m.clip.seq}/{m.clip.count}］
            </button>
            <span>同一次錄影的第 {m.clip.seq} 段：{m.clip.name}</span>
            {(m.clip_members || []).map((c: any) => (
              <Btn key={c.id} small on={c.id === m.id} onClick={() => c.id !== m.id && go('player', { id: c.id })}>{c.file_name.replace(/\.[^.]+$/, '')}</Btn>
            ))}
          </div>
        ) : null}
        <div className="video-box">
          {notReady ? <div className="video-msg">{notReady}</div> : (
            <video ref={videoRef} src={apiUrl(`/api/media/${id}/stream`)} preload="auto" controlsList="nodownload noplaybackrate"
              disablePictureInPicture playsInline onContextMenu={(e: any) => e.preventDefault()}
              onPlay={() => { setPlaying(true); setPlayError(null); if (fs) poke(); }} onPause={() => { setPlaying(false); setControlsVisible(true); }}
              onTimeUpdate={(e: any) => setTime(e.target.currentTime)}
              onLoadedMetadata={(e: any) => {
                setDur(e.target.duration);
                e.target.playbackRate = rate;
                (e.target as any).preservesPitch = true;
                if (startAt > 0) e.target.currentTime = startAt;
              }}
              onError={() => void onVideoError()} onClick={playPause}>
              {trackUrl ? <track kind="subtitles" srcLang="zh-Hant" label="字幕" src={trackUrl} default /> : null}
            </video>
          )}
          <Watermark />
          {playError && !notReady ? <div className="video-msg">{playError} <Btn small onClick={() => { setPlayError(null); videoRef.current?.load(); }}>重試</Btn></div> : null}
        </div>
        {m ? <>
        <div className="progress-row">
          <input type="range" className="progress" aria-label="播放進度" min={0} max={dur || 0} step={1} value={Math.min(time, dur || 0)}
            onChange={(e: any) => seek(Number(e.target.value))} disabled={!!notReady} />
          <span className="time">{clock(time)} / {clock(dur)}</span>
        </div>
        <div className="controls">
          <Btn small disabled={!prev} onClick={() => prev && go('player', { id: prev.id })} title="上一集">⏮</Btn>
          <Btn small onClick={playPause} disabled={!!notReady}>{playing ? '❚❚ 暫停' : '▶ 播放'}</Btn>
          <Btn small disabled={!next} onClick={() => next && go('player', { id: next.id })} title="下一集">⏭</Btn>
          <Btn small onClick={() => setMuted(!muted)} on={muted}>{muted || volume === 0 ? '🔇 靜音' : volume < 0.5 ? '🔉' : '🔊'}</Btn>
          <input type="range" className="volume" aria-label="音量" min={0} max={1} step={0.05} value={volume}
            onChange={(e: any) => { setVolume(Number(e.target.value)); setMuted(false); }} />
          <span className="num">{Math.round((muted ? 0 : volume) * 100)}%</span>
          <label className="inline-select">
            <span>速度</span>
            <select value={rate} onFocus={() => setMenuOpen(true)} onBlur={() => setMenuOpen(false)}
              onChange={(e: any) => { const r = Number(e.target.value); setRate(r); if (videoRef.current) videoRef.current.playbackRate = r; }}>
              {SPEEDS.map((s) => <option key={s} value={s}>{speedLabel(s)}</option>)}
            </select>
          </label>
          {segments.length ? <Btn small on={ccOn} onClick={() => setCcOn(!ccOn)}>{ccOn ? 'CC 字幕：開' : 'CC 字幕：關'}</Btn> : null}
          <Btn small on={panelOpen} onClick={() => setPanelOpen(!panelOpen)}>字幕面板</Btn>
          <PersonalControls media={m} onChange={(p) => setM({ ...m, ...p })} onMenu={setMenuOpen} />
          <span className="grow" />
          <Btn small on={fs} onClick={toggleFs}>{fs ? '⛶ 退出全螢幕' : '⛶ 全螢幕'}</Btn>
        </div>
        {panelOpen && !fs ? (
          <SubtitlePanel mediaId={id} title={m.title} segments={segments} currentTime={time} onSeek={(t) => { seek(t); }}
            canImportExport={!!m.can_import_export} onImported={() => void load()} serverApp={serverApp} />
        ) : null}
        </> : null}
      </div>
      {m && !fs && m.last_position_sec ? <p className="dim">上次看到{approxPosition(m.last_position_sec)}（不會自動跳轉，可自行拖曳）</p> : null}
      {m && m.error_text ? <Msg kind="warn">管理資訊：{m.error_text}</Msg> : null}
    </div>
  );
}

export function isNoAccess(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'no_access';
}
