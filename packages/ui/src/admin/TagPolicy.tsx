/** Tag 設定（Super，沿用 dev.45）：每種角色勾選「修改共用 Tag 時顯示 5 秒提醒」，變更立即儲存並寫入記錄。 */
import React, { useState } from 'react';
import { api, errText } from '../api';
import { LoadState, Msg, PageHead, useApp, useLoad } from '../core';

export function TagPolicyPage(): any {
  const { toast } = useApp();
  const r = useLoad(() => api.get<{ policy: { role: string; label: string; show_warning: boolean }[] }>('/api/tag-policy'), []);
  const [saving, setSaving] = useState<string | null>(null);
  const toggle = async (role: string, show: boolean) => {
    setSaving(role);
    try {
      await api.put('/api/tag-policy', { role, show_warning: show });
      r.setData({ policy: r.data!.policy.map((p) => (p.role === role ? { ...p, show_warning: show } : p)) });
      toast('已儲存', 'ok', 2000);
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setSaving(null);
    }
  };
  return (
    <div className="page narrow">
      <PageHead title="Tag 設定" />
      <section className="panel">
        <p>Tag 是所有人共用的。勾選的角色在新增、修改或移除 Tag 時，會先看到 5 秒提醒「這個 Tag 會影響所有人」。</p>
        <LoadState loading={r.loading} error={r.error} reload={r.reload} />
        {r.data ? (
          <table className="grid">
            <thead><tr><th>角色</th><th>修改共用 Tag 時顯示 5 秒提醒</th></tr></thead>
            <tbody>
              {r.data.policy.map((p) => (
                <tr key={p.role}>
                  <td>{p.label}</td>
                  <td>
                    <label className="check">
                      <input type="checkbox" checked={p.show_warning} disabled={saving === p.role} onChange={() => void toggle(p.role, !p.show_warning)} />
                      {p.show_warning ? '☑ 顯示提醒' : '☐ 不提醒'}{saving === p.role ? '（儲存中…）' : ''}
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <Msg kind="info">變更立即生效，並寫入使用記錄。</Msg>
      </section>
    </div>
  );
}
