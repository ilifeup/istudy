-- dev.55 WOL-01／02／03、BK-01
-- 1) 設備新增 LAN IP（與 WOL 廣播位址分開）及 Server 主動 ping 的最後結果
ALTER TABLE client_devices ADD COLUMN IF NOT EXISTS lan_ip   TEXT NOT NULL DEFAULT '';
ALTER TABLE client_devices ADD COLUMN IF NOT EXISTS probe_ok BOOLEAN;
ALTER TABLE client_devices ADD COLUMN IF NOT EXISTS probe_at TIMESTAMPTZ;

-- 2) 新背景工作：net_probe（ping 設備）、lan_scan（掃描 LAN）、backup_check（備份完整性／隔離還原測試）
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
  'subtitle_import','net_probe','lan_scan','backup_check'));

-- 既有設備的「廣播位址」不自動改寫：是否被當成 LAN IP 填寫，由管理者在設備清單人工核對（畫面會提示）。
