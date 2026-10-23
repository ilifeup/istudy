/**
 * 文件檢視（第 08 章）：PDF（pdf.js：頁碼、上下頁、縮放、適合寬度、文字搜尋；無下載、無列印）、
 * Office（Agent 轉成 PDF 後以 PDF 顯示）、TXT（Server 轉成 UTF-8）、圖片（可縮放）。疊半透明浮水印。
 * 可取得頁碼時，翻頁後延遲儲存（最多每 30 分鐘一次）與登出時儲存「上次第 N 頁」。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiUrl, errText, fetchText } from '../api';
import { Btn, LoadState, Msg, useApp } from '../core';
import { PersonalControls, Watermark } from '../components/MediaControls';
import { NoAccess, stateText, useNoAccess } from './Courses';

let pdfjsPromise: Promise<any> | null = null;
function pdfjs(): Promise<any> {
  if (!pdfjsPromise) {
    // legacy 版本：Win7 版（Chromium 108）也能執行
    pdfjsPromise = import('pdfjs-dist/legacy/build/pdf.mjs').then((m: any) => {
      m.GlobalWorkerOptions.workerSrc = 'app://istudy/pdf.worker.min.mjs';
      return m;
    });
  }
  return pdfjsPromise;
}

const SAVE_GAP_MS = 30 * 60 * 1000;

type PageSize = { w: number; h: number };

/** dev.60：每一頁用自己的尺寸排版（16:9 投影片、直式與橫式混合的 Excel）。之前所有頁都用「第 1 頁寬 × 1.414」當高度，
 *  16:9 投影片下面會多出一大塊白色（看起來像空白頁），比第 1 頁寬的頁面則會被置中裁掉左右兩邊。 */
function PdfView(props: { mediaId: number; startPage: number | null; onPage: (p: number) => void }): any {
  const [doc, setDoc] = useState<any>(null);
  const [sizes, setSizes] = useState<PageSize[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [scale, setScale] = useState(1.2);
  const [fit, setFit] = useState(true);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<number[] | null>(null);
  const [hitIdx, setHitIdx] = useState(0);
  const [boxW, setBoxW] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const rendered = useRef(new Map<number, number>()); // 頁碼 → 已渲染的縮放比例
  const tasks = useRef(new Map<number, any>());       // 頁碼 → 進行中的 pdf.js 渲染（同一個 canvas 不能同時畫兩次）
  const textCache = useRef(new Map<number, string>());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(apiUrl(`/api/media/${props.mediaId}/stream`), { cache: 'no-store' });
        if (!res.ok) {
          let msg = `無法開啟文件（HTTP ${res.status}）`;
          try { msg = (await res.json()).error || msg; } catch { /* 非 JSON */ }
          throw new Error(msg);
        }
        const data = new Uint8Array(await res.arrayBuffer());
        const lib = await pdfjs();
        const d = await lib.getDocument({ data, isEvalSupported: false, enableXfa: false }).promise;
        // 先取得每一頁的尺寸（只讀頁面資訊，不渲染）
        const all: PageSize[] = await Promise.all(Array.from({ length: d.numPages }, async (_, i) => {
          const vp = (await d.getPage(i + 1)).getViewport({ scale: 1 });
          return { w: vp.width, h: vp.height };
        }));
        if (cancelled) return;
        setSizes(all);
        setDoc(d);
      } catch (e) {
        if (!cancelled) setErr(errText(e));
      }
    })();
    return () => { cancelled = true; };
  }, [props.mediaId]);

  // 容器寬度（適合寬度用）；視窗大小改變時重新計算
  useEffect(() => {
    const box = boxRef.current;
    if (!doc || !box) return;
    const measure = () => setBoxW(box.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(box);
    return () => ro.disconnect();
  }, [doc]);

  const avail = Math.max(200, (boxW || 648) - 48);
  const scaleOf = useCallback((i: number) => {
    const sz = sizes[i] || sizes[0] || { w: 600, h: 848 };
    return fit ? Math.max(0.3, avail / sz.w) : scale;   // 適合寬度：每一頁各自剛好填滿寬度
  }, [sizes, fit, avail, scale]);
  const effScale = scaleOf(Math.max(0, page - 1));

  const renderPage = useCallback(async (n: number, canvas: HTMLCanvasElement) => {
    const s = scaleOf(n - 1);
    if (!doc || (fit && !boxW) || rendered.current.get(n) === s) return;   // 適合寬度：量到容器寬度後才畫
    rendered.current.set(n, s);
    const prev = tasks.current.get(n);
    if (prev) prev.cancel();
    const pg = await doc.getPage(n);
    const vp = pg.getViewport({ scale: s });
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(vp.width * ratio);
    canvas.height = Math.floor(vp.height * ratio);   // 顯示大小由 CSS 填滿頁框（縮放改變、還沒重畫的頁面只會暫時模糊，不會變形）
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const task = pg.render({ canvasContext: ctx, viewport: vp });
    tasks.current.set(n, task);
    try {
      await task.promise;
      canvas.dataset.rendered = '1';
    } catch (e: any) {
      if (!e || e.name !== 'RenderingCancelledException') throw e;
    } finally {
      if (tasks.current.get(n) === task) tasks.current.delete(n);
    }
  }, [doc, scaleOf, fit, boxW]);

  // 只渲染可見的頁面（長講義也不會一次全部渲染）
  useEffect(() => {
    if (!doc || !boxRef.current) return;
    rendered.current.clear();
    const box = boxRef.current;
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        const n = Number((en.target as HTMLElement).dataset.page);
        if (en.isIntersecting) void renderPage(n, en.target.querySelector('canvas') as HTMLCanvasElement);
      }
    }, { root: box, rootMargin: '600px 0px' });
    box.querySelectorAll('.pdf-page').forEach((el) => io.observe(el));
    const onScroll = () => {
      const pages = Array.from(box.querySelectorAll('.pdf-page')) as HTMLElement[];
      const top = box.scrollTop + box.clientHeight / 3;
      let cur = 1;
      for (const p of pages) if (p.offsetTop <= top) cur = Number(p.dataset.page);
      setPage(cur);
    };
    box.addEventListener('scroll', onScroll);
    return () => { io.disconnect(); box.removeEventListener('scroll', onScroll); };
  }, [doc, renderPage]);

  useEffect(() => { props.onPage(page); }, [page]);

  const goPage = (n: number) => {
    if (!doc || !boxRef.current) return;
    const p = Math.max(1, Math.min(doc.numPages, n));
    const el = boxRef.current.querySelector(`[data-page="${p}"]`) as HTMLElement | null;
    if (el) boxRef.current.scrollTop = el.offsetTop - 8;
  };
  useEffect(() => {
    if (doc && props.startPage) setTimeout(() => goPage(props.startPage!), 50);
  }, [doc]);

  const search = async () => {
    if (!doc || !q.trim()) { setHits(null); return; }
    const found: number[] = [];
    for (let i = 1; i <= doc.numPages; i++) {
      if (!textCache.current.has(i)) {
        const pg = await doc.getPage(i);
        const tc = await pg.getTextContent();
        textCache.current.set(i, tc.items.map((it: any) => it.str || '').join(''));
      }
      if (textCache.current.get(i)!.includes(q.trim())) found.push(i);
    }
    setHits(found);
    setHitIdx(0);
    if (found.length) goPage(found[0]);
  };

  if (err) return <Msg kind="error">{err}</Msg>;
  if (!doc) return <div className="loading">… 文件載入中</div>;
  return (
    <div className="pdf-view">
      <div className="doc-tools">
        <span>頁：</span>
        <input className="page-input" type="number" min={1} max={doc.numPages} value={page}
          onChange={(e: any) => goPage(Number(e.target.value))} aria-label="頁碼" />
        <span>/ {doc.numPages}</span>
        <Btn small onClick={() => goPage(page - 1)} disabled={page <= 1}>▲ 上一頁</Btn>
        <Btn small onClick={() => goPage(page + 1)} disabled={page >= doc.numPages}>▼ 下一頁</Btn>
        <Btn small onClick={() => { setFit(false); setScale(Math.max(0.4, +(effScale - 0.2).toFixed(2))); }}>−</Btn>
        <span className="num">{Math.round(effScale * 100)}%</span>
        <Btn small onClick={() => { setFit(false); setScale(Math.min(4, +(effScale + 0.2).toFixed(2))); }}>+</Btn>
        <Btn small on={fit} onClick={() => setFit(true)}>適合寬度</Btn>
        <label className="search-inline">搜尋：<input value={q} onChange={(e: any) => setQ(e.target.value)}
          onKeyDown={(e: any) => { if (e.key === 'Enter') void search(); }} /></label>
        <Btn small onClick={search}>搜尋</Btn>
        {hits ? <span>{hits.length} 頁有結果</span> : null}
        {hits && hits.length ? (
          <>
            <Btn small onClick={() => { const i = (hitIdx - 1 + hits.length) % hits.length; setHitIdx(i); goPage(hits[i]); }}>▲</Btn>
            <Btn small onClick={() => { const i = (hitIdx + 1) % hits.length; setHitIdx(i); goPage(hits[i]); }}>▼</Btn>
          </>
        ) : null}
      </div>
      <div className="pdf-pages" ref={boxRef}>
        {Array.from({ length: doc.numPages }, (_, i) => {
          const sz = sizes[i] || sizes[0];
          const s = scaleOf(i);
          return (
          <div key={i} className="pdf-page" data-page={i + 1} style={{ width: Math.floor(sz.w * s), height: Math.floor(sz.h * s) }}>
            <canvas aria-label={`第 ${i + 1} 頁`} />
            <Watermark />
          </div>
          );
        })}
      </div>
    </div>
  );
}

function TextView(props: { mediaId: number }): any {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetchText(`/api/media/${props.mediaId}/stream`).then((r) => setText(r.text), (e) => setErr(errText(e)));
  }, [props.mediaId]);
  if (err) return <Msg kind="error">{err}</Msg>;
  if (text === null) return <div className="loading">… 讀取中</div>;
  return <div className="text-view"><pre>{text}</pre><Watermark /></div>;
}

function ImageView(props: { mediaId: number }): any {
  const [zoom, setZoom] = useState(1);
  return (
    <div className="image-view">
      <div className="doc-tools">
        <Btn small onClick={() => setZoom(Math.max(0.2, zoom - 0.2))}>−</Btn>
        <span className="num">{Math.round(zoom * 100)}%</span>
        <Btn small onClick={() => setZoom(Math.min(5, zoom + 0.2))}>+</Btn>
        <Btn small onClick={() => setZoom(1)}>原始大小</Btn>
      </div>
      <div className="image-box">
        <img src={apiUrl(`/api/media/${props.mediaId}/stream`)} alt="" style={{ transform: `scale(${zoom})` }} draggable={false}
          onContextMenu={(e: any) => e.preventDefault()} />
        <Watermark />
      </div>
    </div>
  );
}

export function DocPage(props: { params: Record<string, any> }): any {
  const { back, onBeforeLogout } = useApp();
  const id = Number(props.params.id);
  const [m, setM] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const pageRef = useRef<number | null>(null);
  const lastSave = useRef(0);
  const savedPage = useRef<number | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      setM(await api.get(`/api/media/${id}`));
      void api.put(`/api/media/${id}/progress`, { opened: true }).catch(() => undefined);
      lastSave.current = Date.now();
    } catch (e) {
      setErr(e);
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  const savePage = useCallback(async () => {
    const p = pageRef.current;
    if (!p || p === savedPage.current) return;
    savedPage.current = p;
    lastSave.current = Date.now();
    await api.put(`/api/media/${id}/progress`, { page: p }).catch(() => undefined);
  }, [id]);
  const onPage = (p: number) => {
    pageRef.current = p;
    if (Date.now() - lastSave.current >= SAVE_GAP_MS) void savePage(); // 最多每 30 分鐘一次
  };
  useEffect(() => onBeforeLogout(savePage), [onBeforeLogout, savePage]);
  useEffect(() => () => { void savePage(); }, [savePage]);

  const course = useNoAccess(err);
  if (course !== null) return <NoAccess course={course} />;
  if (err) return <LoadState loading={false} error={errText(err)} reload={load} />;
  if (!m) return <div className="loading">… 讀取中</div>;
  const notReady = stateText(m);
  const icon = m.kind === 'image' ? '🖼' : '📄';
  return (
    <div className="doc-page">
      <div className="doc-head">
        <Btn small onClick={back}>← 返回</Btn>
        <strong className="doc-title">{icon} {m.title}</strong>
        <span className="dim">{(m.breadcrumbs || []).map((b: any) => b.name).join(' / ')}</span>
        <span className="grow" />
        <PersonalControls media={m} onChange={(p) => setM({ ...m, ...p })} />
      </div>
      {notReady ? <Msg kind="info">{notReady}</Msg>
        : m.kind === 'text' ? <TextView mediaId={id} />
        : m.kind === 'image' ? <ImageView mediaId={id} />
        : <PdfView mediaId={id} startPage={props.params.page || null} onPage={onPage} />}
    </div>
  );
}
