/**
 * dev.61 Outgo（Super 專用）：瀏覽 /Media/Outgo 的資料夾與檔案，把課程錄音用 FunASR 轉成逐字稿（TXT、SRT）。
 * Outgo 不是教材：不會出現在課程、搜尋、播放清單；結果與狀態都存在 Outgo 資料夾裡（錄音旁邊的「<檔名>.asr.txt／.srt」）。
 */
import React, { useState } from 'react';
import { api, errText, fetchText, qs } from '../../../../packages/ui/src/api';
import { bridge } from '../../../../packages/ui/src/bridge';
import { Btn, LoadState, Modal, Msg, PageHead, useApp, useInterval, useLoad } from '../../../../packages/ui/src/core';
import { dateTime, duration, size } from '../../../../packages/ui/src/format';
import { AsrLengthHint, QualityTag } from './AsrLimits';
import { EngineSelect, subtitleEngine } from './Queues';

const STATE_TEXT: Record<string, string> = {
  none: '－ 尚未轉文字', queued: '⏳ 等待中', running: '… 處理中', done: '✓ 完成', failed: '✗ 失敗', interrupted: '⚠ 中斷（可重新辨識）',
};

export function OutgoPage(): any {
  const { toast } = useApp();
  const [path, setPath] = useState('');
  const [engine, setEngine] = useState('');          // 空字串＝預設引擎（資料庫目前的設定）
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const r = useLoad(() => api.get<any>('/api/admin/outgo' + qs({ path })), [path]);
  const entries: any[] = r.data ? r.data.entries : [];
  const busy = entries.some((e) => e.asr && (e.asr.state === 'queued' || e.asr.state === 'running'));
  useInterval(() => r.reload(), busy ? 4000 : null);
  const files = entries.filter((e) => e.type === 'dir' || !e.output_of);   // 逐字稿歸在錄音那一列
  const crumbs = path ? path.split('/') : [];
  const defaultName = r.data ? (r.data.asr.engine === 'auto' ? `自動（目前會選：${r.data.engines.auto ? subtitleEngine({ engine: r.data.engines.auto }) : '沒有可用的引擎'}）`
    : subtitleEngine({ engine: r.data.asr.engine })) : '';

  const run = async (e: any) => {
    try {
      const x = await api.post('/api/admin/outgo/asr', engine ? { path: e.path, engine } : { path: e.path });
      toast(x.existing ? '這個檔案已在排隊或處理中' : `已排入 ASR 轉文字：${e.name}`, 'ok');
      r.reload();
    } catch (err) {
      toast(errText(err), 'error');
    }
  };
  const download = async (e: any, format: 'txt' | 'srt') => {
    try {
      const x = await fetchText('/api/admin/outgo/result' + qs({ path: e.path, format }));
      const res = await bridge.saveFile(x.filename || `${e.name}.asr.${format}`, x.text);
      if (res.ok) toast(`已儲存 ${format.toUpperCase()}：${res.path}`, 'ok');
    } catch (err) {
      toast(errText(err), 'error');
    }
  };
  const show = async (e: any) => {
    try {
      const x = await fetchText('/api/admin/outgo/result' + qs({ path: e.path, format: 'txt' }));
      setPreview({ name: e.name, text: x.text });
    } catch (err) {
      toast(errText(err), 'error');
    }
  };

  return (
    <div className="page">
      <PageHead title="Outgo（錄音轉文字）">
        {r.data && r.data.ready ? <EngineSelect label="辨識引擎：" value={engine} engines={r.data.engines} onChange={setEngine}
          defaultLabel={`預設引擎（${defaultName}）`} /> : null}
      </PageHead>
      <p className="dim">把課程錄音放進 Server 的 <code>/Media/Outgo</code>（可以有任意層子資料夾），在這裡按「ASR 轉文字」。
        Outgo 不是教材：不會被掃描、不會出現在課程或搜尋，只有本機 Server App 的 Super 看得到。
        逐字稿存在錄音旁邊（<code>檔名.asr.txt</code>、<code>檔名.asr.srt</code>），原始錄音不會被修改或刪除。</p>
      <AsrLengthHint kind="audio" speed={r.data ? r.data.speed : null} />
      <div className="row crumbs">
        <Btn small kind={path ? 'quiet' : undefined} onClick={() => setPath('')}>📁 Outgo</Btn>
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            <span>／</span>
            <Btn small kind={i === crumbs.length - 1 ? undefined : 'quiet'} onClick={() => setPath(crumbs.slice(0, i + 1).join('/'))}>{c}</Btn>
          </React.Fragment>
        ))}
        <span className="grow" />
        <Btn small onClick={r.reload}>重新整理</Btn>
      </div>
      <LoadState loading={r.loading && !r.data} error={r.error} reload={r.reload} />
      {r.data && !r.data.ready ? <Msg kind="error">{r.data.error}</Msg> : null}
      {r.data && r.data.ready && !files.length ? <Msg kind="info">這個資料夾是空的。把錄音檔（{r.data.audio_ext.join('、')}）放進 /Media/Outgo 後按「重新整理」。</Msg> : null}
      {files.length ? (
        <table className="grid small">
          <thead><tr><th>名稱</th><th>格式</th><th>大小</th><th>修改時間</th><th>ASR 狀態</th><th></th></tr></thead>
          <tbody>{files.map((e) => (
            <tr key={e.path}>
              <td>{e.type === 'dir'
                ? <button type="button" className="rowlink" onClick={() => setPath(e.path)}>📁 {e.name}</button>
                : <>{e.audio ? '🎙' : '📄'} {e.name}</>}</td>
              <td>{e.type === 'dir' ? '資料夾' : (e.ext || '—').toUpperCase()}</td>
              <td className="nowrap">{e.type === 'dir' ? '—' : size(e.size)}</td>
              <td className="nowrap">{dateTime(e.mtime)}</td>
              <td>{e.asr ? <AsrState a={e.asr} /> : e.type === 'dir' ? '' : <span className="dim">不是錄音</span>}</td>
              <td className="actions">
                {e.asr && e.asr.state !== 'queued' && e.asr.state !== 'running'
                  ? <Btn small kind={e.asr.has_txt ? 'quiet' : 'primary'} onClick={() => run(e)}>{e.asr.has_txt || e.asr.state === 'failed' || e.asr.state === 'interrupted' ? '重新辨識' : 'ASR 轉文字'}</Btn> : null}
                {e.asr && e.asr.has_txt ? <><Btn small onClick={() => show(e)}>預覽</Btn><Btn small onClick={() => download(e, 'txt')}>下載 TXT</Btn></> : null}
                {e.asr && e.asr.has_srt ? <Btn small onClick={() => download(e, 'srt')}>下載 SRT</Btn> : null}
              </td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
      {r.data && r.data.skipped ? <p className="dim">另有 {r.data.skipped} 個項目沒有列出（符號連結、名稱不合法或無法讀取）。</p> : null}
      {preview ? (
        <Modal title={`逐字稿：${preview.name}`} wide onClose={() => setPreview(null)}>
          <pre className="hostout transcript">{preview.text}</pre>
        </Modal>
      ) : null}
    </div>
  );
}

function AsrState(props: { a: any }): any {
  const a = props.a;
  return (
    <div>
      <span>{STATE_TEXT[a.state] || a.state}{a.state === 'running' && a.percent !== null ? ` ${Math.round(a.percent)}%` : ''}</span>
      {a.state === 'failed' && a.error ? <div className="err-text">{a.error}</div> : null}
      {a.finished_at && (a.has_txt || a.state === 'done') ? (
        <div className="dim">{a.state === 'done' ? '' : '上次完成：'}{dateTime(a.finished_at)}・{a.engine ? subtitleEngine({ engine: a.engine }) : '引擎未知'}
          {a.audio_sec ? `・${duration(a.audio_sec)}` : ''}{a.segments ? `・${a.segments} 句` : ''}</div>
      ) : null}
      {a.quality && (a.has_txt || a.state === 'done') ? <div>音訊品質：<QualityTag level={a.quality} reasons={a.quality_reasons} max={2} /></div> : null}
    </div>
  );
}
