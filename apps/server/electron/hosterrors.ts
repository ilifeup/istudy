/**
 * pkexec 與 istudyctl 的結束碼 → 白話訊息。
 * pkexec：126＝在密碼視窗按取消、127＝沒有驗證代理或未授權（密碼錯誤）；istudyctl：3＝預檢有警告待確認。
 */
export function pkexecError(code: number | null, stderr: string): string {
  if (!code || code === 3) return '';
  if (/No authentication agent/i.test(stderr)) {
    return '沒有跳出系統密碼視窗：桌面上沒有執行 polkit 驗證代理（XFCE 需另外安裝並自動啟動，見 DEPLOY.md）；也可以改用終端機 sudo ./install.sh --cli';
  }
  if (code === 126) return '已取消（系統密碼視窗被關閉），未執行';
  if (code === 127 || /Not authorized/i.test(stderr)) return '系統密碼不正確或這個 Ubuntu 帳號沒有管理員權限（需在 sudo 群組），未執行';
  const last = stderr.trim().split('\n').filter(Boolean).pop();
  return last || `執行失敗（結束碼 ${code}）`;
}
