-- iStudy 全新資料庫結構（計畫書第 14 章）。
-- 只在全新資料卷第一次執行；之後的結構變更一律新增 server/sql/migrations/NNNN_*.sql。
-- 版本紀錄寫在 schema_migrations；schema.sql 本身代表版本 0001。

SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS schema_migrations (
  version     TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------- 帳號與 Session ----------
CREATE TABLE IF NOT EXISTS accounts (
  id            BIGSERIAL PRIMARY KEY,
  username      VARCHAR(80) NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role          VARCHAR(12) NOT NULL CHECK (role IN ('user','mini_admin','admin','super')),
  full_name     TEXT NOT NULL DEFAULT '',
  phone         TEXT NOT NULL DEFAULT '',
  birth_date    DATE,
  start_year    INTEGER CHECK (start_year IS NULL OR start_year BETWEEN 1900 AND 2200),
  address       TEXT NOT NULL DEFAULT '',
  class_time    TEXT NOT NULL DEFAULT '',
  bio           TEXT NOT NULL DEFAULT '',
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_notes (
  id          BIGSERIAL PRIMARY KEY,
  account_id  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  body        TEXT NOT NULL,
  created_by  BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
  created_by_name TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by  BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
  updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_account_notes_account ON account_notes(account_id, created_at);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash   CHAR(64) PRIMARY KEY,
  account_id   BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  app          VARCHAR(10) NOT NULL DEFAULT 'client' CHECK (app IN ('client','server')),
  expires_at   TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  client_ip    TEXT NOT NULL DEFAULT '',
  client_name  TEXT NOT NULL DEFAULT '',
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);

CREATE TABLE IF NOT EXISTS login_failures (
  ip              TEXT PRIMARY KEY,
  fail_count      INTEGER NOT NULL DEFAULT 0,
  first_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  locked_until    TIMESTAMPTZ
);

-- ---------- 目錄、課程群組、權限 ----------
CREATE TABLE IF NOT EXISTS folders (
  id            BIGSERIAL PRIMARY KEY,
  parent_id     BIGINT REFERENCES folders(id) ON DELETE RESTRICT,
  course_id     BIGINT REFERENCES folders(id) ON DELETE RESTRICT,
  rel_path      TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  display_name  TEXT NOT NULL DEFAULT '',
  is_course     BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order    INTEGER NOT NULL DEFAULT 0,
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  status        VARCHAR(10) NOT NULL DEFAULT 'present' CHECK (status IN ('present','missing')),
  scanned_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_folders_course ON folders(course_id);

CREATE TABLE IF NOT EXISTS course_groups (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS course_group_members (
  group_id    BIGINT NOT NULL REFERENCES course_groups(id) ON DELETE CASCADE,
  account_id  BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_group_members_account ON course_group_members(account_id);

CREATE TABLE IF NOT EXISTS group_course_permissions (
  group_id   BIGINT NOT NULL REFERENCES course_groups(id) ON DELETE CASCADE,
  folder_id  BIGINT NOT NULL REFERENCES folders(id) ON DELETE RESTRICT,
  PRIMARY KEY (group_id, folder_id)
);

CREATE TABLE IF NOT EXISTS account_course_overrides (
  account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  folder_id  BIGINT NOT NULL REFERENCES folders(id) ON DELETE RESTRICT,
  mode       VARCHAR(5) NOT NULL CHECK (mode IN ('allow','deny')),
  PRIMARY KEY (account_id, folder_id)
);

-- ---------- 教材 ----------
CREATE TABLE IF NOT EXISTS clip_groups (
  id          BIGSERIAL PRIMARY KEY,
  folder_id   BIGINT REFERENCES folders(id) ON DELETE SET NULL,
  name        TEXT NOT NULL,
  recorded_at TIMESTAMPTZ,
  source      VARCHAR(6) NOT NULL DEFAULT 'auto' CHECK (source IN ('auto','manual')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS media (
  id                  BIGSERIAL PRIMARY KEY,           -- 永久教材 ID
  folder_id           BIGINT REFERENCES folders(id) ON DELETE SET NULL,
  course_id           BIGINT REFERENCES folders(id) ON DELETE SET NULL,
  kind                VARCHAR(8) NOT NULL CHECK (kind IN ('video','pdf','office','text','image')),
  title               TEXT NOT NULL,
  file_name           TEXT NOT NULL,
  ext                 TEXT NOT NULL DEFAULT '',
  rel_path            TEXT NOT NULL UNIQUE,
  size_bytes          BIGINT NOT NULL DEFAULT 0,
  mtime               TIMESTAMPTZ,
  quick_hash          TEXT NOT NULL DEFAULT '',
  status              VARCHAR(16) NOT NULL DEFAULT 'present'
                      CHECK (status IN ('present','missing','original_deleted')),
  duration_sec        DOUBLE PRECISION,
  recorded_at         TIMESTAMPTZ,
  probe               JSONB,
  play_mode           VARCHAR(10) CHECK (play_mode IS NULL OR play_mode IN ('direct','remux','transcode')),
  playback_path       TEXT,
  playback_size       BIGINT,
  encoder             VARCHAR(8),
  convert_status      VARCHAR(16) NOT NULL DEFAULT 'none'
                      CHECK (convert_status IN ('none','pending','probing','queued','converting','verifying',
                                                'done','direct','failed','verify_failed')),
  convert_error       TEXT,
  verify_result       JSONB,
  verified_at         TIMESTAMPTZ,
  delete_due_at       TIMESTAMPTZ,
  original_deleted_at TIMESTAMPTZ,
  doc_pdf_path        TEXT,
  doc_status          VARCHAR(10) NOT NULL DEFAULT 'none'
                      CHECK (doc_status IN ('none','pending','converting','done','failed')),
  doc_error           TEXT,
  doc_source_sig      TEXT,
  clip_group_id       BIGINT REFERENCES clip_groups(id) ON DELETE SET NULL,
  clip_seq            INTEGER,
  clip_manual         BOOLEAN NOT NULL DEFAULT FALSE,  -- Super 手動調整過，重新掃描不覆蓋
  tag_names           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_media_folder ON media(folder_id);
CREATE INDEX IF NOT EXISTS idx_media_course ON media(course_id);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status);
CREATE INDEX IF NOT EXISTS idx_media_convert ON media(convert_status);
CREATE INDEX IF NOT EXISTS idx_media_clip ON media(clip_group_id);
CREATE INDEX IF NOT EXISTS idx_media_quick_hash ON media(size_bytes, quick_hash);

CREATE TABLE IF NOT EXISTS subtitles (
  media_id      BIGINT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
  path          TEXT NOT NULL,
  source        VARCHAR(8) NOT NULL CHECK (source IN ('whisper','import')),
  model         TEXT NOT NULL DEFAULT '',
  segment_count INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by    BIGINT REFERENCES accounts(id) ON DELETE SET NULL
);

-- 字幕文字索引：字幕搜尋（第 08 章）與進階字幕面板用；由 Agent 寫入字幕時同步更新
CREATE TABLE IF NOT EXISTS subtitle_cues (
  media_id  BIGINT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  idx       INTEGER NOT NULL,
  start_ms  INTEGER NOT NULL,
  end_ms    INTEGER NOT NULL,
  text      TEXT NOT NULL,
  PRIMARY KEY (media_id, idx)
);

-- ---------- 個人資料 ----------
CREATE TABLE IF NOT EXISTS bookmarks (
  account_id    BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  media_id      BIGINT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  is_bookmarked BOOLEAN NOT NULL DEFAULT TRUE,
  is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (account_id, media_id)
);

CREATE TABLE IF NOT EXISTS view_history (
  account_id        BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  media_id          BIGINT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  last_viewed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_position_sec INTEGER,
  last_page         INTEGER,
  learning_status   VARCHAR(12) CHECK (learning_status IS NULL OR learning_status IN ('not_started','in_progress','completed')),
  PRIMARY KEY (account_id, media_id)
);
CREATE INDEX IF NOT EXISTS idx_history_account ON view_history(account_id, last_viewed_at DESC);

-- ---------- 使用記錄 ----------
CREATE TABLE IF NOT EXISTS audit_log (
  id           BIGSERIAL PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  actor_id     BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
  actor_name   TEXT NOT NULL DEFAULT '',
  actor_role   TEXT NOT NULL DEFAULT '',
  actor_groups TEXT NOT NULL DEFAULT '',
  action       TEXT NOT NULL,
  media_id     BIGINT,
  path         TEXT NOT NULL DEFAULT '',
  details      JSONB NOT NULL DEFAULT '{}'::jsonb,
  client_ip    TEXT NOT NULL DEFAULT '',
  client_name  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, created_at);

CREATE TABLE IF NOT EXISTS tag_role_policy (
  role         TEXT PRIMARY KEY,
  show_warning BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO tag_role_policy(role, show_warning) VALUES
  ('user', TRUE), ('mini_admin', TRUE), ('admin', FALSE), ('super', FALSE)
ON CONFLICT (role) DO NOTHING;

-- ---------- Client 電腦（WOL＋心跳） ----------
CREATE TABLE IF NOT EXISTS client_devices (
  id                BIGSERIAL PRIMARY KEY,
  device_code       TEXT UNIQUE,
  name              TEXT NOT NULL DEFAULT '',
  os_hint           TEXT NOT NULL DEFAULT '',
  mac               CHAR(17) NOT NULL UNIQUE,          -- aa:bb:cc:dd:ee:ff
  broadcast         TEXT NOT NULL DEFAULT '255.255.255.255',
  port              INTEGER NOT NULL DEFAULT 9 CHECK (port IN (7, 9)),
  send_count        INTEGER NOT NULL DEFAULT 3 CHECK (send_count BETWEEN 1 AND 3),
  cooldown_sec      INTEGER NOT NULL DEFAULT 10 CHECK (cooldown_sec BETWEEN 0 AND 3600),
  enabled           BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  registered        BOOLEAN NOT NULL DEFAULT FALSE,
  all_macs          TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  last_seen_at      TIMESTAMPTZ,
  last_pc_name      TEXT NOT NULL DEFAULT '',
  last_os           TEXT NOT NULL DEFAULT '',
  last_app_version  TEXT NOT NULL DEFAULT '',
  last_app_channel  TEXT NOT NULL DEFAULT '',
  last_api_level    INTEGER,
  last_account_id   BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
  last_ip           TEXT NOT NULL DEFAULT '',
  last_wake_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_devices_mac ON client_devices(mac);

-- ---------- PHP 與 Agent 之間的工作佇列 ----------
CREATE TABLE IF NOT EXISTS jobs (
  id          BIGSERIAL PRIMARY KEY,
  type        TEXT NOT NULL CHECK (type IN ('scan','probe','convert','verify','delete_original','whisper',
                                            'office_pdf','wol_wake','diagnose','backup','cleanup','subtitle_import')),
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
  status      VARCHAR(10) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','cancelled')),
  priority    INTEGER NOT NULL DEFAULT 0,
  run_after   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  attempts    INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  result      JSONB,
  error       TEXT,
  created_by  BIGINT REFERENCES accounts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_pick ON jobs(status, priority DESC, run_after);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(type, status);

CREATE TABLE IF NOT EXISTS scan_runs (
  id          BIGSERIAL PRIMARY KEY,
  job_id      BIGINT,
  dry_run     BOOLEAN NOT NULL DEFAULT FALSE,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  status      VARCHAR(10) NOT NULL DEFAULT 'running' CHECK (status IN ('running','done','failed')),
  added       INTEGER NOT NULL DEFAULT 0,
  changed     INTEGER NOT NULL DEFAULT 0,
  moved       INTEGER NOT NULL DEFAULT 0,
  missing     INTEGER NOT NULL DEFAULT 0,
  skipped     JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb,
  error       TEXT
);

-- ---------- Super 檢查清單、診斷、Log、備份、設定 ----------
CREATE TABLE IF NOT EXISTS checklist_items (
  id          BIGSERIAL PRIMARY KEY,
  category    TEXT NOT NULL CHECK (category IN ('pending_dev','os_packages','server_hw','client_hw','client_os','custom')),
  title       TEXT NOT NULL,
  status      VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','abnormal','na')),
  note        TEXT NOT NULL DEFAULT '',
  auto_value  TEXT NOT NULL DEFAULT '',
  device_id   BIGINT REFERENCES client_devices(id) ON DELETE CASCADE,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by  BIGINT REFERENCES accounts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_checklist_category ON checklist_items(category, sort_order);

CREATE TABLE IF NOT EXISTS diagnostics (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  kind        VARCHAR(8) NOT NULL CHECK (kind IN ('server','client')),
  device_id   BIGINT REFERENCES client_devices(id) ON DELETE SET NULL,
  data        JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_diagnostics_kind ON diagnostics(kind, created_at DESC);

CREATE TABLE IF NOT EXISTS client_logs (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  logged_at   TIMESTAMPTZ,
  account_id  BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
  device_id   BIGINT REFERENCES client_devices(id) ON DELETE SET NULL,
  pc_name     TEXT NOT NULL DEFAULT '',
  app_channel TEXT NOT NULL DEFAULT '',
  app_version TEXT NOT NULL DEFAULT '',
  level       VARCHAR(8) NOT NULL DEFAULT 'info',
  message     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_client_logs_created ON client_logs(created_at);

-- Server（PHP）、Agent、主機（istudyctl）記錄
CREATE TABLE IF NOT EXISTS system_logs (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source      VARCHAR(8) NOT NULL CHECK (source IN ('server','agent','host')),
  level       VARCHAR(8) NOT NULL DEFAULT 'info',
  message     TEXT NOT NULL,
  context     JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs(created_at);

CREATE TABLE IF NOT EXISTS backup_history (
  id            BIGSERIAL PRIMARY KEY,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  kind          VARCHAR(8) NOT NULL DEFAULT 'daily',
  status        TEXT NOT NULL,
  file_path     TEXT NOT NULL DEFAULT '',
  size_bytes    BIGINT NOT NULL DEFAULT 0,
  file_count    INTEGER NOT NULL DEFAULT 0,
  verify_result JSONB,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS server_config (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by BIGINT REFERENCES accounts(id) ON DELETE SET NULL
);
INSERT INTO server_config(key, value) VALUES
  ('schedule', '{"start":"22:30","end":"03:00","auto_convert":true,"auto_whisper":true,"whisper_after_convert":true,"backup_time":"03:30","backup_keep":14}'::jsonb),
  ('conversion_paused', 'true'::jsonb),   -- 新安裝先暫停批次轉檔：首次設定精靈第 4 步試轉通過、Super 確認後才開放
  ('clip_threshold_gib', '1.9'::jsonb),
  ('import_export_admin_ids', '[]'::jsonb),
  ('exclude_patterns', '[]'::jsonb),
  ('login_placeholders', '{"username":"請輸入帳號","password":"請輸入密碼"}'::jsonb),
  ('whisper_model', '"small"'::jsonb),
  ('setup', '{"completed":false,"step":1,"steps":{}}'::jsonb)
ON CONFLICT (key) DO NOTHING;


-- ---------- Super 檢查清單初始內容（第 10 章；pending_dev 取自第 21 章未來實機驗證） ----------
INSERT INTO checklist_items(category, title, sort_order)
SELECT v.category, v.title, v.sort_order FROM (VALUES
  ('pending_dev','V-01 轉檔速度與品質（3 支真實 .MTS 含交錯片＋1 支其他格式）',1),
  ('pending_dev','V-02 Quick Sync 可用性（vainfo＋h264_qsv）',2),
  ('pending_dev','V-03 Win10、Win11 版播放（3 小時 MP4、0.5～3x、拖曳、全螢幕、VTT）',3),
  ('pending_dev','V-04 Ubuntu 版播放（含由 Win7 改裝的舊機）',4),
  ('pending_dev','V-05 Win7 版播放（冷啟動 20 次）',5),
  ('pending_dev','V-06 7 人同時播放',6),
  ('pending_dev','V-07 Whisper 準確度與耗時（small／medium）',7),
  ('pending_dev','V-08 Office 轉 PDF（docx、xlsx、pptx 含中文）',8),
  ('pending_dev','V-09 中文輸入（fcitx5、ibus）',9),
  ('pending_dev','V-10 Win7 Electron 22 相容（Fullscreen、PDF.js、electron-updater）',10),
  ('pending_dev','V-11 WOL 與心跳（7 台各喚醒一次）',11),
  ('pending_dev','V-12 備份與還原（還原到測試資料庫）',12),
  ('pending_dev','V-13 防下載（右鍵、Ctrl+S／P／U、F12、拖曳）',13),
  ('pending_dev','V-14 媒體路徑（中文、空白、特殊字元、符號連結）',14),
  ('pending_dev','V-15 安全（Session、停用帳號、權限、CORS、登入限流）',15),
  ('pending_dev','V-16 開機免登入與自動啟動',16),
  ('pending_dev','V-17 伺服器連線狀態偵測',17),
  ('pending_dev','V-18 Server 初始部署演練',18),
  ('pending_dev','V-19 Client 版別與更新頻道',19),
  ('pending_dev','V-20 Server App 主機維護（pkexec）',20),
  ('os_packages','Server：Ubuntu 版本',1),
  ('os_packages','Server：Docker Engine',2),
  ('os_packages','Server：Docker Compose',3),
  ('os_packages','Server：PostgreSQL',4),
  ('os_packages','Server：FFmpeg',5),
  ('os_packages','Server：Intel 媒體驅動（iHD）',6),
  ('os_packages','Server：LibreOffice',7),
  ('os_packages','Server：Python',8),
  ('os_packages','Client：作業系統版本',9),
  ('os_packages','Client：App 版別與版本',10),
  ('server_hw','CPU',1),('server_hw','RAM',2),('server_hw','磁碟容量與剩餘空間',3),
  ('server_hw','ZFS',4),('server_hw','SMART',5),('server_hw','網路',6),
  ('client_hw','CPU',1),('client_hw','RAM',2),('client_hw','磁碟',3),
  ('client_hw','網卡',4),('client_hw','螢幕',5),('client_hw','音效',6),
  ('client_os','作業系統',1),('client_os','App 版本',2),('client_os','連線',3),
  ('client_os','影片播放',4),('client_os','PDF',5),('client_os','中文輸入',6)
) AS v(category, title, sort_order)
WHERE NOT EXISTS (SELECT 1 FROM checklist_items);

-- ---------- 課程權限計算（第 05 章）----------
-- admin、super：全部啟用中的頂層資料夾。其他角色：
--   個人覆寫「允許」→ 可看；個人覆寫「禁止」→ 不可看；否則任一所屬群組有權限 → 可看。
CREATE OR REPLACE FUNCTION visible_course_ids(p_account BIGINT, p_role TEXT)
RETURNS SETOF BIGINT LANGUAGE sql STABLE AS $$
  SELECT f.id FROM folders f
   WHERE f.parent_id IS NULL AND f.enabled AND f.status = 'present'
     AND (
       p_role IN ('admin','super')
       OR (f.is_course AND (
            EXISTS (SELECT 1 FROM account_course_overrides o
                     WHERE o.account_id = p_account AND o.folder_id = f.id AND o.mode = 'allow')
            OR (
              EXISTS (SELECT 1 FROM group_course_permissions g
                        JOIN course_group_members m ON m.group_id = g.group_id
                       WHERE m.account_id = p_account AND g.folder_id = f.id)
              AND NOT EXISTS (SELECT 1 FROM account_course_overrides o
                               WHERE o.account_id = p_account AND o.folder_id = f.id AND o.mode = 'deny')
            )
          ))
     )
$$;

INSERT INTO schema_migrations(version) VALUES ('0001_schema') ON CONFLICT DO NOTHING;
