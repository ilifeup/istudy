-- dev.58 字幕辨識命名：工作類型與字幕來源的「whisper」（歷史名稱，實際意思是「自動辨識」）改為「asr」。
-- 實際用的引擎仍記在 subtitles.model 與 subtitles.asr_report（paraformer-zh、sensevoice-small、whisper-small…）。
-- 只做兩件事：換 CHECK 約束、改寫既有資料；不新增資料表或欄位，沒有相容層。
-- 換約束之後，任何程式若還寫入「whisper」都會被資料庫直接拒絕（錯誤會出現在 API／Agent 的 Log），不會悄悄卡住。
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
UPDATE jobs SET type = 'asr' WHERE type = 'whisper';
UPDATE subtitles SET source = 'asr' WHERE source = 'whisper';
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN (
  'scan','probe','convert','verify','delete_original','asr','office_pdf','wol_wake','diagnose','backup','cleanup',
  'subtitle_import','net_probe','lan_scan','backup_check','email_report'));
ALTER TABLE subtitles ADD CONSTRAINT subtitles_source_check CHECK (source IN ('asr','import'));
