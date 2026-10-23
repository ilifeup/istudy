-- dev.62：方案 B 加密備份與遠端備份。
--   只新增欄位與工作類型，全部有預設值：既有備份紀錄自動視為「舊格式、未加密」（format='legacy'、enc_status='none'），
--   dev.61 的 Agent（回滾時）新增紀錄不寫這些欄位也照常運作；回滾不需要移除這些欄位。
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS format VARCHAR(8) NOT NULL DEFAULT 'legacy';
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS sha256 TEXT;
-- none＝未加密（舊格式）；unverified＝加密完成、主機尚未解密驗證；verified＝主機解密並核對每個檔案的 SHA-256；failed＝驗證失敗
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS enc_status VARCHAR(12) NOT NULL DEFAULT 'none';
-- none＝未上傳；uploading＝上傳中；verified＝已上傳並下載回來比對 SHA-256 相符；failed＝上傳或比對失敗；deleted＝遠端副本已依保留規則刪除
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS remote_status VARCHAR(12) NOT NULL DEFAULT 'none';
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS remote_path TEXT;
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS remote_verified_at TIMESTAMPTZ;
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS remote_error TEXT;
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS delete_reason TEXT;
ALTER TABLE backup_history ADD COLUMN IF NOT EXISTS manifest JSONB;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backup_history_format_check') THEN
    ALTER TABLE backup_history ADD CONSTRAINT backup_history_format_check CHECK (format IN ('legacy','age'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backup_history_enc_status_check') THEN
    ALTER TABLE backup_history ADD CONSTRAINT backup_history_enc_status_check CHECK (enc_status IN ('none','unverified','verified','failed'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'backup_history_remote_status_check') THEN
    ALTER TABLE backup_history ADD CONSTRAINT backup_history_remote_status_check CHECK (remote_status IN ('none','uploading','verified','failed','deleted'));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_backup_history_created ON backup_history(created_at);

-- 主機（istudyctl，root）執行的備份工作：解密驗證、上傳、清理、遠端連線測試。
--   這些工作不由 Agent 執行（Agent 沒有私鑰與遠端帳密）；jobs 表只用來記錄進度與結果，讓 Server App 看得到。
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
  'subtitle_import','net_probe','lan_scan','backup_check','email_report','purge_media','outgo_asr',
  'backup_verify','backup_upload','backup_prune','backup_remote_test'));
