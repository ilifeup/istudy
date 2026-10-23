-- dev.61：ASR 處理記錄（字幕辨識與 Outgo 錄音轉文字都記；成功與失敗都記），給「統計與報告」與匯出用。
--   - 只新增一張表，不改既有資料表。
--   - media_id 不設外鍵：教材永久刪除時設為 NULL，紀錄（路徑、品質、結果）保留。
--   - quality：中文音訊品質的粗略估計（high／medium／low／unknown），判斷依據寫在 quality_detail。
CREATE TABLE IF NOT EXISTS asr_log (
  id               bigserial PRIMARY KEY,
  created_at       timestamptz NOT NULL DEFAULT NOW(),     -- 處理結束（寫入紀錄）的時間
  started_at       timestamptz,
  kind             text NOT NULL CHECK (kind IN ('subtitle', 'outgo')),
  job_id           bigint,
  media_id         bigint,
  path             text NOT NULL,                          -- 字幕：教材路徑；Outgo：Outgo/…
  file_size        bigint,
  audio_sec        real,
  engine_requested text,
  engine           text,
  status           text NOT NULL CHECK (status IN ('done', 'failed')),
  error            text,
  rtf              real,
  segments         integer,
  cues             integer,
  qc_status        text,
  quality          text NOT NULL DEFAULT 'unknown' CHECK (quality IN ('high', 'medium', 'low', 'unknown')),
  quality_detail   jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_by     bigint
);
CREATE INDEX IF NOT EXISTS asr_log_created_idx ON asr_log (created_at DESC);
CREATE INDEX IF NOT EXISTS asr_log_media_idx ON asr_log (media_id) WHERE media_id IS NOT NULL;
