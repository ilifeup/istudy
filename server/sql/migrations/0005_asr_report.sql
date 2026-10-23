-- dev.57 字幕辨識（ASR）：記錄每份自動字幕用哪個引擎、速度與品質檢查結果。
-- 只新增一個可為空的欄位；既有字幕、帳號與教材都不變。舊版程式不讀這個欄位，回退不受影響。
-- asr_report 內容：{engine, model, engine_version, route, qc:{status: ok|needs_review, reasons[], notes[]},
--                  metrics:{audio_sec, speech_sec, elapsed_sec, rtf, segments, cues, threads}, settings:{…}}
ALTER TABLE subtitles ADD COLUMN IF NOT EXISTS asr_report JSONB;
