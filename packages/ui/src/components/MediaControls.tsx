/** 播放器與文件檢視器共用：Tag、最愛、書籤、學習狀態（只由使用者手動選）、浮水印。 */
import React, { useEffect, useState } from 'react';
import { api, errText } from '../api';
import { Btn, useApp } from '../core';
import { LEARNING } from '../format';
import { TagEditor } from './TagEditor';

export function PersonalControls(props: {
  media: { id: number; tags: string[]; is_favorite?: boolean; is_bookmarked?: boolean; learning_status?: string };
  onChange: (patch: Record<string, any>) => void;
  onMenu?: (open: boolean) => void;
  compact?: boolean;
}): any {
  const { toast } = useApp();
  const m = props.media;
  const [tagOpen, setTagOpen] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  useEffect(() => { props.onMenu?.(tagOpen); }, [tagOpen]);
  const setBm = async (body: Record<string, boolean>) => {
    try {
      props.onChange(await api.put(`/api/media/${m.id}/bookmark`, body));
    } catch (e) {
      toast(errText(e), 'error');
    }
  };
  const setStatus = async (status: string) => {
    setStatusBusy(true);
    try {
      const r = await api.put(`/api/media/${m.id}/status`, { status });
      props.onChange({ learning_status: r.learning_status });
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setStatusBusy(false);
    }
  };
  return (
    <>
      <Btn small onClick={() => setTagOpen(true)} title="共用 Tag">🏷 Tag {m.tags.length}</Btn>
      <Btn small on={!!m.is_favorite} onClick={() => setBm({ is_favorite: !m.is_favorite })}>
        {m.is_favorite ? '❤ 已加入最愛' : '♡ 加入最愛'}
      </Btn>
      <Btn small on={!!m.is_bookmarked} onClick={() => setBm({ is_bookmarked: !m.is_bookmarked })}>
        {m.is_bookmarked ? '🔖 已加書籤' : '🔖 加入書籤'}
      </Btn>
      <label className="inline-select">
        <span>學習狀態：</span>
        <select value={m.learning_status || 'not_started'} disabled={statusBusy} onChange={(e: any) => void setStatus(e.target.value)}
          onFocus={() => props.onMenu?.(true)} onBlur={() => props.onMenu?.(false)}>
          {Object.entries(LEARNING).map(([k, v]) => <option key={k} value={k}>{v.mark} {v.text}</option>)}
        </select>
      </label>
      {tagOpen ? <TagEditor mediaId={m.id} tags={m.tags} onChange={(tags) => props.onChange({ tags })} onClose={() => setTagOpen(false)} /> : null}
    </>
  );
}

export function Watermark(): any {
  const { account } = useApp();
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(t);
  }, []);
  const p = (n: number) => String(n).padStart(2, '0');
  const text = `${account.display_name} ${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}`;
  return <div className="watermark" aria-hidden="true"><span>{text}</span><span>{text}</span></div>;
}
