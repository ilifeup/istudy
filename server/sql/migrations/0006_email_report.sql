-- dev.57 Super 專用「Email 報告」：新增背景工作類型 email_report（由 Agent 以 SMTP 寄出）。
-- 寄件設定存在 server_config（email），SMTP 密碼另存 email_secret（API 永遠不回傳）。不新增資料表；寄送紀錄就是 jobs。
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'jobs'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%subtitle_import%'
  LOOP
    EXECUTE format('ALTER TABLE jobs DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN (
  'scan','probe','convert','verify','delete_original','whisper','office_pdf','wol_wake','diagnose','backup','cleanup',
  'subtitle_import','net_probe','lan_scan','backup_check','email_report'));
