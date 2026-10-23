-- dev.61：Client 心跳回報的更新狀態（Server App「Client 版本與更新」顯示哪台電腦現在的版本、更新到哪一步）。
--   只新增一個欄位，預設空物件；dev.60 以前的 Client 不回報，畫面顯示「未回報」。
ALTER TABLE client_devices ADD COLUMN IF NOT EXISTS last_update jsonb NOT NULL DEFAULT '{}'::jsonb;
