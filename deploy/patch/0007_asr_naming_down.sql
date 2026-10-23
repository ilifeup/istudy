-- 只給 deploy/patch_dev58.sh rollback 使用：把 0007_asr_naming 的改名退回 dev.57 的寫法（asr → whisper），
-- 讓退回後的 dev.57 程式可以照常建立字幕工作。資料內容不變，只改名稱與 CHECK 約束；整段在一個交易裡（psql -1）。
-- 沒有套用過 0007 時執行也安全（沒有 asr 資料，約束換成 dev.57 的同一份）。
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'jobs'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%subtitle_import%'
  LOOP
    EXECUTE format('ALTER TABLE jobs DROP CONSTRAINT %I', c);
  END LOOP;
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'subtitles'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%import%'
  LOOP
    EXECUTE format('ALTER TABLE subtitles DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
UPDATE jobs SET type = 'whisper' WHERE type = 'asr';
UPDATE subtitles SET source = 'whisper' WHERE source = 'asr';
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN (
  'scan','probe','convert','verify','delete_original','whisper','office_pdf','wol_wake','diagnose','backup','cleanup',
  'subtitle_import','net_probe','lan_scan','backup_check','email_report'));
ALTER TABLE subtitles ADD CONSTRAINT subtitles_source_check CHECK (source IN ('whisper','import'));
DELETE FROM schema_migrations WHERE version = '0007_asr_naming';
