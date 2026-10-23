/**
 * 共用 Tag 編輯（第 08 章，沿用 dev.45 規格）：所有有閱覽權限的人共用；輸入時即時移除所有空白；
 * 需要提醒的角色在新增、改名、移除時顯示非阻擋式 5 秒提醒。
 */
import React, { useState } from 'react';
import { api, errText } from '../api';
import { Btn, Modal, Msg, useApp } from '../core';

export const stripSpaces = (s: string) => s.replace(/\s+/gu, '');

export function TagEditor(props: { mediaId: number; tags: string[]; onChange: (tags: string[]) => void; onClose: () => void }): any {
  const { account, toast } = useApp();
  const [value, setValue] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const warn = () => {
    if (account.tag_warning) toast('提醒：您正在修改全體共用的 Tag（{s} 秒後關閉）', 'warn', 5000);
  };
  const run = async (method: 'POST' | 'PATCH' | 'DELETE', body: Record<string, string>) => {
    setError(null);
    warn();
    try {
      const r = await (method === 'POST' ? api.post : method === 'PATCH' ? api.patch : api.del)(`/api/media/${props.mediaId}/tags`, body);
      props.onChange(r.tags);
      return true;
    } catch (e) {
      setError(errText(e));
      return false;
    }
  };
  const add = async () => {
    const t = stripSpaces(value);
    if (!t) {
      setHint('請輸入 Tag 文字（空白會自動移除）');
      return;
    }
    setHint(null);
    if (await run('POST', { tag: t })) setValue('');
  };
  return (
    <Modal title={`🏷 共用 Tag（${props.tags.length}）`} onClose={props.onClose}>
      <div className="stack">
        <p className="dim">Tag 是所有有閱覽權限的人共用的；修改會讓其他人也看到。</p>
        <div className="row">
          <input aria-label="新增 Tag" value={value} maxLength={80} placeholder="輸入 Tag（空白會自動移除）"
            onChange={(e: any) => setValue(stripSpaces(e.target.value))}
            onKeyDown={(e: any) => { if (e.key === 'Enter') void add(); }} />
          <Btn kind="primary" onClick={add}>新增</Btn>
        </div>
        {hint ? <Msg kind="info">{hint}</Msg> : null}
        {error ? <Msg kind="error">{error}</Msg> : null}
        {!props.tags.length ? <Msg kind="info">目前沒有 Tag</Msg> : null}
        <ul className="tag-list">
          {props.tags.map((t) => (
            <li key={t}>
              {editing === t ? (
                <>
                  <input aria-label="新的 Tag" value={editValue} onChange={(e: any) => setEditValue(stripSpaces(e.target.value))} autoFocus />
                  <Btn small kind="primary" onClick={async () => {
                    const nv = stripSpaces(editValue);
                    if (!nv) { setHint('請輸入 Tag 文字（空白會自動移除）'); return; }
                    if (await run('PATCH', { tag: t, new_tag: nv })) setEditing(null);
                  }}>儲存</Btn>
                  <Btn small onClick={() => setEditing(null)}>取消</Btn>
                </>
              ) : (
                <>
                  <span className="tag">🏷 {t}</span>
                  <Btn small onClick={() => { setEditing(t); setEditValue(t); }}>改名</Btn>
                  <Btn small kind="danger" onClick={() => run('DELETE', { tag: t })}>移除</Btn>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}
