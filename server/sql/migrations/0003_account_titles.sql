-- dev.55 ROLE-03：稱謂與權限是兩張獨立的表，accounts 以外鍵分別關聯；移除 mini_admin。
--   權限（account_roles）：user＝1、admin＝2、super＝3。後端授權只看 accounts.role。
--   稱謂（account_titles）：助教、講師。只用於畫面顯示，不影響任何權限。
-- 只新增資料表、欄位與外鍵；不改帳號 ID、帳號名稱、密碼雜湊；唯一的 Super 不變。

-- 1) 權限角色表
CREATE TABLE IF NOT EXISTS account_roles (
  role         VARCHAR(12) PRIMARY KEY,
  title_level  SMALLINT NOT NULL UNIQUE CHECK (title_level BETWEEN 1 AND 3)
);
INSERT INTO account_roles(role, title_level) VALUES ('user', 1), ('admin', 2), ('super', 3)
ON CONFLICT (role) DO NOTHING;

-- 2) 稱謂表（不含 admin、super，也沒有「學生」）
CREATE TABLE IF NOT EXISTS account_titles (
  title_id    VARCHAR(20) PRIMARY KEY CHECK (title_id ~ '^[a-z][a-z0-9_]{0,19}$'),
  en_title    TEXT NOT NULL,
  zh_title    TEXT NOT NULL UNIQUE,
  sort_order  SMALLINT NOT NULL DEFAULT 0
);
INSERT INTO account_titles(title_id, en_title, zh_title, sort_order) VALUES
  ('assistant', 'Assistant', '助教', 1),
  ('lecturer',  'Lecturer',  '講師', 2)
ON CONFLICT (title_id) DO NOTHING;

-- 3) 移除 mini_admin：原 mini_admin 帳號改為 user（不自動提升為 admin）；部署腳本會先列出受影響的帳號並要求確認
UPDATE accounts SET role = 'user', updated_at = NOW() WHERE role = 'mini_admin';
DELETE FROM tag_role_policy WHERE role = 'mini_admin';

-- 4) accounts.role、tag_role_policy.role 改以外鍵關聯權限角色表（取代原本含 mini_admin 的 CHECK）
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'accounts'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%mini_admin%'
  LOOP
    EXECUTE format('ALTER TABLE accounts DROP CONSTRAINT %I', c);
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'accounts'::regclass AND conname = 'accounts_role_fkey') THEN
    ALTER TABLE accounts ADD CONSTRAINT accounts_role_fkey FOREIGN KEY (role) REFERENCES account_roles(role);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'tag_role_policy'::regclass AND conname = 'tag_role_policy_role_fkey') THEN
    ALTER TABLE tag_role_policy ADD CONSTRAINT tag_role_policy_role_fkey FOREIGN KEY (role) REFERENCES account_roles(role);
  END IF;
END $$;

-- 5) accounts.title_id 以外鍵關聯稱謂表；Super 不需要稱謂（留空）
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS title_id VARCHAR(20) REFERENCES account_titles(title_id);
UPDATE accounts SET title_id = 'assistant' WHERE title_id IS NULL AND role <> 'super';
