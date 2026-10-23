-- dev.61：新增兩種工作類型（只換 CHECK 約束；不新增資料表或欄位）
--   purge_media：Super 永久刪除教材（Agent 刪除原檔與衍生檔，再刪資料庫紀錄）
--   outgo_asr：/Media/Outgo 錄音轉文字（短期工作；完成後 Agent 刪除這筆工作，結果與狀態存在 Outgo 目錄裡，不寫入教材資料表）
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
  'scan','probe','convert','verify','delete_original','asr','office_pdf','wol_wake','diagnose','backup','cleanup',
  'subtitle_import','net_probe','lan_scan','backup_check','email_report','purge_media','outgo_asr'));
