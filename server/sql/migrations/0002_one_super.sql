-- dev.55 ROLE-04：Super 永遠只有一位（資料庫層保證，另見 API、create-super、reset-super 的檢查）。
-- 已有兩位以上 Super 時立即中止：本 migration 在交易內執行，失敗會整個回復，
-- 不刪除、不覆蓋、不新增任何帳號。全新安裝時這裡執行在建立 Super 之前（0 位），屬正常。
DO $$
DECLARE n integer;
BEGIN
  SELECT count(*) INTO n FROM accounts WHERE role = 'super';
  IF n > 1 THEN
    RAISE EXCEPTION 'ROLE-04：資料庫內有 % 個 Super，已停止 migration，請人工確認（不會自動刪除或修改帳號）', n;
  END IF;
END $$;

-- 部分唯一索引：role = 'super' 的列最多一筆
CREATE UNIQUE INDEX IF NOT EXISTS accounts_one_super ON accounts ((role)) WHERE role = 'super';
