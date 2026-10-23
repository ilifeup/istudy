<?php
declare(strict_types=1);

/**
 * dev.61「統計與報告」（Super 專用，只走本機管理埠）：ASR 處理記錄（字幕辨識＋Outgo 錄音轉文字）。
 * 每次處理一筆（成功、失敗都有）：檔案、長度、引擎、中文音訊品質（高／中／低的粗略估計）、結果。畫面可匯出 CSV／HTML。
 */

const ASR_QUALITY = ['high', 'medium', 'low', 'unknown'];

function asr_log_out(array $r): array
{
    $d = jdec($r['quality_detail'] ?? null, []);
    return [
        'id' => (int)$r['id'], 'created_at' => ts($r['created_at']), 'started_at' => $r['started_at'] ? ts($r['started_at']) : null,
        'kind' => $r['kind'], 'job_id' => $r['job_id'] !== null ? (int)$r['job_id'] : null,
        'media_id' => $r['media_id'] !== null ? (int)$r['media_id'] : null, 'path' => $r['path'],
        'file_size' => $r['file_size'] !== null ? (int)$r['file_size'] : null,
        'audio_sec' => $r['audio_sec'] !== null ? (float)$r['audio_sec'] : null,
        'engine_requested' => $r['engine_requested'], 'engine' => $r['engine'], 'status' => $r['status'], 'error' => $r['error'],
        'rtf' => $r['rtf'] !== null ? (float)$r['rtf'] : null, 'segments' => $r['segments'] !== null ? (int)$r['segments'] : null,
        'cues' => $r['cues'] !== null ? (int)$r['cues'] : null, 'qc_status' => $r['qc_status'],
        'quality' => $r['quality'], 'quality_reasons' => array_values(is_array($d['reasons'] ?? null) ? $d['reasons'] : []),
        'quality_metrics' => is_array($d['metrics'] ?? null) ? $d['metrics'] : new stdClass(),
        'requested_by' => $r['requested_by'] !== null ? (int)$r['requested_by'] : null,
    ];
}

route('GET', '/api/admin/asr-log', static function (): array {
    require_local_super();
    $days = (int)(Req::query('days', '30') ?? 30);
    $where = [];
    $args = [];
    if ($days > 0) { $where[] = "created_at >= NOW() - make_interval(days => ?)"; $args[] = min($days, 3660); }
    $kind = (string)(Req::query('kind', '') ?? '');
    if ($kind !== '') {
        if (!in_array($kind, ['subtitle', 'outgo'], true)) fail(400, '類型只能是 subtitle 或 outgo');
        $where[] = 'kind = ?'; $args[] = $kind;
    }
    $quality = (string)(Req::query('quality', '') ?? '');
    if ($quality !== '') {
        if (!in_array($quality, ASR_QUALITY, true)) fail(400, '品質只能是 high、medium、low、unknown');
        $where[] = 'quality = ?'; $args[] = $quality;
    }
    $status = (string)(Req::query('status', '') ?? '');
    if ($status !== '') {
        if (!in_array($status, ['done', 'failed'], true)) fail(400, '結果只能是 done 或 failed');
        $where[] = 'status = ?'; $args[] = $status;
    }
    $q = trim((string)(Req::query('q', '') ?? ''));
    if ($q !== '') {
        if (mb_strlen($q) > 100) fail(400, '關鍵字太長');
        $where[] = 'path ILIKE ?'; $args[] = like_pattern($q);
    }
    $limit = max(1, min(5000, (int)(Req::query('limit', '1000') ?? 1000)));
    $w = $where ? 'WHERE ' . implode(' AND ', $where) : '';
    $sum = q_one("SELECT count(*) AS total, count(*) FILTER (WHERE status = 'done') AS done, count(*) FILTER (WHERE status = 'failed') AS failed,
                         COALESCE(sum(audio_sec) FILTER (WHERE status = 'done'), 0) AS audio_sec,
                         count(*) FILTER (WHERE quality = 'high') AS q_high, count(*) FILTER (WHERE quality = 'medium') AS q_medium,
                         count(*) FILTER (WHERE quality = 'low') AS q_low, count(*) FILTER (WHERE quality = 'unknown') AS q_unknown,
                         count(*) FILTER (WHERE kind = 'subtitle') AS k_subtitle, count(*) FILTER (WHERE kind = 'outgo') AS k_outgo,
                         percentile_cont(0.5) WITHIN GROUP (ORDER BY rtf) FILTER (WHERE status = 'done' AND rtf > 0) AS rtf_median
                    FROM asr_log $w", $args);
    $rows = q_all("SELECT * FROM asr_log $w ORDER BY created_at DESC, id DESC LIMIT $limit", $args);
    return [
        'items' => array_map('asr_log_out', $rows),
        'summary' => [
            'total' => (int)$sum['total'], 'done' => (int)$sum['done'], 'failed' => (int)$sum['failed'],
            'audio_hours' => round((float)$sum['audio_sec'] / 3600, 2),
            'quality' => ['high' => (int)$sum['q_high'], 'medium' => (int)$sum['q_medium'], 'low' => (int)$sum['q_low'], 'unknown' => (int)$sum['q_unknown']],
            'kind' => ['subtitle' => (int)$sum['k_subtitle'], 'outgo' => (int)$sum['k_outgo']],
            'rtf_median' => $sum['rtf_median'] !== null ? round((float)$sum['rtf_median'], 3) : null,
        ],
        'truncated' => (int)$sum['total'] > count($rows),
        'filters' => ['days' => $days, 'kind' => $kind, 'quality' => $quality, 'status' => $status, 'q' => $q],
    ];
});
