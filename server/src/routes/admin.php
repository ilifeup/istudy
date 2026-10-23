<?php
declare(strict_types=1);

/**
 * Server 管理 API（第 10、15 章）：只在 127.0.0.1:4081 提供，而且只有 Super。
 * LAN 的 :4080 對 /api/admin/* 一律 404（Nginx 擋一次、dispatch() 再擋一次、require_local_super() 第三次）。
 */

// ---------- 總覽 ----------

function agent_status(): array
{
    $hb = config_get('agent_heartbeat', null);
    $at = is_array($hb) && isset($hb['at']) ? strtotime((string)$hb['at']) : null;
    $age = $at ? time() - $at : null;
    return [
        'running' => $age !== null && $age <= 90,
        'last_seen_at' => $at ? date(DATE_ATOM, $at) : null,
        'age_sec' => $age,
        'version' => is_array($hb) ? ($hb['version'] ?? null) : null,
        'current_job' => is_array($hb) ? ($hb['current_job'] ?? null) : null,
        'flags' => is_array($hb) ? ($hb['flags'] ?? []) : [],
        'encoder' => is_array($hb) ? ($hb['encoder'] ?? null) : null,
        // dev.55 HW-01：三層各自的檢測結果（Agent 啟動時實測）；stderr 只在 Server App 顯示
        'encoders' => is_array($hb) && is_array($hb['encoders'] ?? null) ? $hb['encoders'] : null,
    ];
}

function client_versions(): array
{
    $v = version_info();
    $out = [];
    foreach (CLIENT_CHANNELS as $ch) {
        $latest = (string)($v['client'][$ch]['version'] ?? '');
        $rows = q_all('SELECT id, name, last_app_version, last_app_channel, last_os FROM client_devices WHERE registered AND last_app_channel = ?', [$ch]);
        $outdated = array_values(array_filter($rows, static fn($r) => $latest !== '' && $r['last_app_version'] !== $latest));
        $mismatch = array_values(array_filter($rows, static fn($r) => channel_mismatch($r)));
        $out[] = [
            'channel' => $ch, 'label' => channel_label($ch), 'latest' => $latest, 'latest_label' => $latest !== '' ? display_version($latest) : '',
            'api_level' => (int)($v['client'][$ch]['api_level'] ?? 0),
            'installed' => count($rows), 'outdated' => count($outdated), 'mismatch' => count($mismatch),
            'outdated_devices' => array_map(static fn($r) => ['id' => (int)$r['id'], 'name' => $r['name'], 'version' => $r['last_app_version']], $outdated),
            'mismatch_devices' => array_map(static fn($r) => ['id' => (int)$r['id'], 'name' => $r['name'], 'os' => $r['last_os']], $mismatch),
        ];
    }
    return $out;
}

/** dev.61：每台 Client 的版本與更新狀態（心跳回報）；最新版本＝這版 Server 對應的 Client 版本（VERSION.json）。 */
route('GET', '/api/admin/client-updates', static function (): array {
    require_local_super();
    $v = version_info();
    $rows = q_all("SELECT id, name, device_code, registered, last_pc_name, last_app_version, last_app_channel, last_os, last_ip,
                          last_seen_at, last_update, (last_seen_at > NOW() - INTERVAL '180 seconds') AS online
                     FROM client_devices WHERE registered OR last_app_channel <> ''
                    ORDER BY registered DESC, sort_order, name, last_pc_name, id");
    $devices = [];
    $sum = [];
    foreach (CLIENT_CHANNELS as $ch) {
        $sum[$ch] = ['channel' => $ch, 'label' => channel_label($ch), 'latest' => (string)($v['client'][$ch]['version'] ?? ''),
            'total' => 0, 'current' => 0, 'outdated' => 0, 'online' => 0];
    }
    foreach ($rows as $r) {
        $ch = (string)$r['last_app_channel'];
        $ver = (string)$r['last_app_version'];
        $latest = $ch !== '' ? (string)($v['client'][$ch]['version'] ?? '') : '';
        $vs = $ver === '' || $latest === '' ? 'unknown' : (version_compare($ver, $latest, '>=') ? 'current' : 'outdated');
        $u = jdec($r['last_update'] ?? null, []);
        $devices[] = [
            'id' => (int)$r['id'], 'name' => $r['name'] !== '' ? $r['name'] : $r['last_pc_name'], 'pc_name' => $r['last_pc_name'],
            'device_code' => $r['device_code'], 'registered' => (bool)$r['registered'],
            'channel' => $ch, 'channel_label' => $ch !== '' ? channel_label($ch) : '', 'version' => $ver, 'latest' => $latest,
            'version_state' => $vs, 'os' => $r['last_os'], 'ip' => $r['last_ip'],
            'last_seen_at' => $r['last_seen_at'] ? ts($r['last_seen_at']) : null, 'online' => (bool)$r['online'],
            'update' => is_array($u) && isset($u['state']) ? $u : null,
        ];
        if (isset($sum[$ch])) {
            $sum[$ch]['total']++;
            if ($vs === 'current') $sum[$ch]['current']++;
            if ($vs === 'outdated') $sum[$ch]['outdated']++;
            if ($r['online']) $sum[$ch]['online']++;
        }
    }
    return ['channels' => array_values($sum), 'devices' => $devices];
});

route('GET', '/api/admin/overview', static function (): array {
    require_local_super();
    $diag = q_one("SELECT created_at, data::text AS data FROM diagnostics WHERE kind = 'server' ORDER BY created_at DESC LIMIT 1");
    $data = $diag ? jdec($diag['data'], []) : [];
    $backup = q_one("SELECT created_at FROM backup_history WHERE status = 'success' ORDER BY created_at DESC LIMIT 1");
    $counts = q_one("SELECT
        COUNT(*) FILTER (WHERE kind = 'video' AND convert_status IN ('pending','probing','queued')) AS convert_waiting,
        COUNT(*) FILTER (WHERE kind = 'video' AND convert_status IN ('converting','verifying')) AS convert_running,
        COUNT(*) FILTER (WHERE kind = 'video' AND convert_status IN ('failed','verify_failed')) AS convert_failed,
        COUNT(*) FILTER (WHERE kind = 'office' AND doc_status IN ('pending','converting')) AS doc_waiting,
        COUNT(*) FILTER (WHERE kind = 'office' AND doc_status = 'failed') AS doc_failed,
        COUNT(*) FILTER (WHERE status = 'missing') AS missing,
        COUNT(*) FILTER (WHERE delete_due_at <= NOW() AND original_deleted_at IS NULL AND status = 'present'
                         AND play_mode IN ('remux','transcode') AND convert_status = 'done') AS originals_due
        FROM media");
    $asrWaiting = (int)q_val("SELECT COUNT(*) FROM jobs WHERE type = 'asr' AND status IN ('queued','running')");
    // dev.55 HW-01：近 7 天轉檔降級為 CPU 的次數（Agent 寫入系統 Log），Super 總覽顯示警示
    $downgrades = (int)q_val("SELECT COUNT(*) FROM system_logs WHERE source = 'agent' AND message LIKE '⚠ 轉檔降級為 CPU%'
                                AND created_at > NOW() - INTERVAL '7 days'");
    return [
        'db' => 'ok',
        'agent' => agent_status(),
        'cpu_downgrades_7d' => $downgrades,
        'diagnostics' => $diag ? ['created_at' => ts($diag['created_at']), 'data' => $data] : null,
        'last_backup_at' => ts($backup['created_at'] ?? null),
        'last_backup_days' => $backup ? (int)floor((time() - strtotime((string)$backup['created_at'])) / 86400) : null,
        'counts' => array_map('intval', $counts ?? []) + ['asr_waiting' => $asrWaiting],
        'conversion_paused' => (bool)config_get('conversion_paused', false),
        'client_versions' => client_versions(),
        'setup' => config_get('setup', ['completed' => false]),
        'server_version' => version_info()['server'],
        'server_version_label' => 'Server ' . display_version((string)version_info()['server']),
    ];
});

route('GET', '/api/admin/client-versions', static function (): array {
    require_local_super();
    return ['channels' => client_versions()];
});

// ---------- 教材清單 ----------

route('GET', '/api/admin/media', static function (): array {
    $a = require_local_super();
    $conds = ['TRUE'];
    $params = [];
    $q = trim((string)Req::query('q', ''));
    if ($q !== '') {
        $conds[] = '(m.title ILIKE ? OR m.rel_path ILIKE ? OR CAST(m.id AS text) = ?)';
        array_push($params, like_pattern($q), like_pattern($q), $q);
    }
    $course = Req::query('course');
    if ($course !== null && preg_match('/^\d+$/', $course)) {
        $conds[] = 'm.course_id = ?';
        $params[] = (int)$course;
    }
    $status = Req::query('status');
    if (in_array($status, ['present', 'missing', 'original_deleted'], true)) {
        $conds[] = 'm.status = ?';
        $params[] = $status;
    }
    $kind = Req::query('kind');
    if (in_array($kind, ['video', 'pdf', 'office', 'text', 'image'], true)) {
        $conds[] = 'm.kind = ?';
        $params[] = $kind;
    }
    $per = query_int('per', 100, 10, 500);
    $page = query_int('page', 1, 1, 100000);
    $where = implode(' AND ', $conds);
    $total = (int)q_val("SELECT COUNT(*) FROM media m WHERE $where", $params);
    $rows = q_all("SELECT m.*, to_json(m.tag_names)::text AS tags_json, cg.name AS clip_name, cg.source AS clip_source,
                          (SELECT COUNT(*) FROM media x WHERE x.clip_group_id = m.clip_group_id) AS clip_count,
                          EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id) AS has_subtitle
                     FROM media m LEFT JOIN clip_groups cg ON cg.id = m.clip_group_id
                    WHERE $where ORDER BY m.rel_path LIMIT $per OFFSET " . (($page - 1) * $per), $params);
    return ['total' => $total, 'page' => $page, 'per' => $per, 'items' => array_map(static fn($m) => media_out($m, $a, [
        'rel_path' => $m['rel_path'], 'clip_manual' => (bool)$m['clip_manual'], 'convert_error' => $m['convert_error'],
        'doc_error' => $m['doc_error'], 'verified_at' => ts($m['verified_at']), 'delete_due_at' => ts($m['delete_due_at']),
        'original_deleted_at' => ts($m['original_deleted_at']), 'encoder' => $m['encoder'],
        'path' => breadcrumb_text($m['folder_id'] !== null ? (int)$m['folder_id'] : null),
    ]), $rows)];
});

route('PATCH', '/api/media/{id}', static function (array $p): array {
    $a = require_local_super();
    $m = media_row($p['id']) ?? fail(404, '找不到此教材');
    $d = Req::body();
    $set = [];
    $title = body_str($d, 'title', 200);
    if ($title !== null) {
        if ($title === '') fail(400, '標題不可空白');
        $set['title'] = $title;
    }
    $jobs = [];
    if (array_key_exists('rel_path', $d)) {
        // 遺失檔案重新指定路徑（保留同一教材 ID）
        $rel = safe_rel_path($d['rel_path']) ?? fail(400, '路徑格式錯誤（需為 /Media 底下的相對路徑）');
        if (!library_rel_ok($rel)) fail(400, '不能指定系統資料夾（playback、subtitles、doc-cache…）裡的檔案');
        if (library_file($rel) === null) fail(400, '找不到這個檔案');
        if (q_val('SELECT 1 FROM media WHERE rel_path = ? AND id <> ?', [$rel, $m['id']])) fail(409, '這個檔案已經是另一份教材');
        $ext = strtolower(pathinfo($rel, PATHINFO_EXTENSION));
        $set += ['rel_path' => $rel, 'file_name' => basename($rel), 'ext' => $ext, 'status' => 'present'];
        $jobs[] = ['scan', ['relink_media_id' => (int)$m['id']]];
    }
    if (array_key_exists('subtitle_rel_path', $d)) {
        // 指定字幕：由 Agent 從 /Media 讀取 SRT／VTT 並轉成 WebVTT
        $rel = safe_rel_path($d['subtitle_rel_path']) ?? fail(400, '字幕路徑格式錯誤');
        if (!in_array(strtolower(pathinfo($rel, PATHINFO_EXTENSION)), ['srt', 'vtt'], true)) fail(400, '字幕需為 .srt 或 .vtt');
        $file = library_file($rel) ?? fail(400, '找不到字幕檔');
        $cues = parse_subtitles(text_to_utf8((string)file_get_contents($file)));
        if (!$cues) fail(400, '無法辨識字幕格式');
        $jobs[] = ['subtitle_import', ['media_id' => (int)$m['id'], 'vtt' => cues_to_vtt($cues), 'account_id' => (int)$a['id']]];
    }
    tx(static function () use ($m, $set) {
        if ($set) {
            $cols = array_map(static fn($k) => "$k = ?", array_keys($set));
            q('UPDATE media SET ' . implode(', ', $cols) . ', updated_at = NOW() WHERE id = ?', array_merge(array_values($set), [$m['id']]));
        }
    });
    $ids = [];
    foreach ($jobs as [$type, $payload]) $ids[] = enqueue_job($type, $payload, 50, (int)$a['id']);
    audit($a, 'media.updated', ['fields' => array_keys($set), 'jobs' => $ids], (int)$m['id'], (string)$m['rel_path']);
    return ['media' => media_out(media_row((int)$m['id']), $a), 'job_ids' => $ids];
});

route('DELETE', '/api/media/{id}', static function (array $p): array {
    $a = require_local_super();
    $m = media_row($p['id']) ?? fail(404, '找不到此教材');
    if ((Req::body()['confirm'] ?? false) !== true) fail(400, '請確認：只刪除資料庫紀錄，不刪實體檔案', ['code' => 'confirm_required']);
    q('DELETE FROM media WHERE id = ?', [$m['id']]);
    audit($a, 'media.unlinked', ['title' => $m['title']], (int)$m['id'], (string)$m['rel_path']);
    return ['ok' => true, 'physical_file_deleted' => false];
});

// ---------- dev.61 P0-1：Super 永久刪除教材（原檔＋衍生檔＋資料庫紀錄） ----------
// 「解除關聯」（DELETE /api/media/{id}）仍然只刪資料庫紀錄；永久刪除是另一個 API，實際刪除由 Agent 執行（PHP 對 /Media 只有唯讀）。

/** 這個教材正在執行／等待中的工作（含重新指定路徑的掃描）。 */
function media_jobs(int $id, array $statuses): array
{
    return q_all("SELECT id, type, status FROM jobs WHERE status = ANY(CAST(? AS text[])) AND type <> 'purge_media'
                    AND (payload->>'media_id' = ? OR payload->>'relink_media_id' = ?) ORDER BY id",
        ['{' . implode(',', $statuses) . '}', (string)$id, (string)$id]);
}

function purge_file_info(string $path, ?string $sharedSql = null, array $sharedArgs = []): array
{
    $exists = file_exists($path) || is_link($path);
    $link = is_link($path) || ($exists && realpath($path) !== $path);
    $shared = $exists && $sharedSql ? (int)q_val($sharedSql, $sharedArgs) > 0 : false;
    return ['path' => $path, 'exists' => $exists, 'size' => $exists && !$link && is_file($path) ? (int)filesize($path) : null,
        'symlink' => $link, 'shared' => $shared];
}

route('GET', '/api/admin/media/{id}/purge-preview', static function (array $p): array {
    require_local_super();
    $m = media_row($p['id']) ?? fail(404, '找不到此教材');
    $id = (int)$m['id'];
    $root = realpath(library_root()) ?: library_root();   // 以實際路徑比對，才看得出中間有沒有符號連結
    $blockers = [];
    $orig = null;
    if ($m['status'] !== 'original_deleted') {
        if (!library_rel_ok((string)$m['rel_path'])) $blockers[] = '教材路徑指向系統資料夾或格式不正確：不能刪除';
        $orig = purge_file_info($root . '/' . $m['rel_path']);
        if ($orig['symlink']) $blockers[] = '原檔或它所在的資料夾是符號連結：不刪除';
        elseif ($orig['exists'] && !is_file($orig['path'])) $blockers[] = '原檔不是一般檔案：不刪除';
    }
    // dev.62：衍生檔要資料庫記錄屬於這個教材才會刪（同 ID 的舊檔標示「不處理」，與 Agent 的判斷相同）
    $subPath = q_val('SELECT path FROM subtitles WHERE media_id = ?', [$id]);
    $files = array_values(array_filter([
        $orig ? ['label' => '原檔'] + $orig : null,
        ['label' => '播放用 MP4', 'owned' => ($m['playback_path'] ?? null) === "$id.mp4"]
            + purge_file_info("$root/playback/$id.mp4", 'SELECT count(*) FROM media WHERE id <> ? AND playback_path = ?', [$id, "$id.mp4"]),
        ['label' => '字幕檔', 'owned' => $subPath === "$id.vtt"]
            + purge_file_info("$root/subtitles/$id.vtt", 'SELECT count(*) FROM subtitles WHERE media_id <> ? AND path = ?', [$id, "$id.vtt"]),
        ['label' => '文件 PDF', 'owned' => ($m['doc_pdf_path'] ?? null) === "$id.pdf"]
            + purge_file_info("$root/doc-cache/$id.pdf", 'SELECT count(*) FROM media WHERE id <> ? AND doc_pdf_path = ?', [$id, "$id.pdf"]),
    ]));
    $running = media_jobs($id, ['running']);
    foreach ($running as $j) $blockers[] = "工作 #{$j['id']}（{$j['type']}）正在執行：請等它完成後再刪除";
    $group = $m['clip_group_id'] ? q_one('SELECT g.name, (SELECT count(*) FROM media x WHERE x.clip_group_id = g.id) AS n FROM clip_groups g WHERE g.id = ?',
        [$m['clip_group_id']]) : null;
    $db = [
        'subtitle' => (bool)q_val('SELECT 1 FROM subtitles WHERE media_id = ?', [$id]),
        'subtitle_cues' => (int)q_val('SELECT count(*) FROM subtitle_cues WHERE media_id = ?', [$id]),
        'bookmarks' => (int)q_val('SELECT count(*) FROM bookmarks WHERE media_id = ?', [$id]),
        'view_history' => (int)q_val('SELECT count(*) FROM view_history WHERE media_id = ?', [$id]),
        'tags' => count(jdec($m['tags_json'] ?? '[]', [])),
        'jobs_queued' => count(media_jobs($id, ['queued'])),
        'jobs_history' => count(media_jobs($id, ['done', 'failed', 'cancelled'])),
        'audit_refs' => (int)q_val('SELECT count(*) FROM audit_log WHERE media_id = ?', [$id]),
        'asr_log' => (int)q_val('SELECT count(*) FROM asr_log WHERE media_id = ?', [$id]),
        'clip_group' => $group ? ['name' => $group['name'], 'members' => (int)$group['n']] : null,
    ];
    return ['media' => ['id' => $id, 'title' => $m['title'], 'file_name' => $m['file_name'], 'rel_path' => $m['rel_path'], 'kind' => $m['kind'],
        'status' => $m['status'], 'size' => (int)$m['size_bytes']], 'files' => $files, 'db' => $db, 'blockers' => $blockers,
        'confirm_text' => '永久刪除'];
});

route('POST', '/api/admin/media/{id}/purge', static function (array $p): array {
    $a = require_local_super();
    $m = media_row($p['id']) ?? fail(404, '找不到此教材');
    $d = Req::body();
    if (($d['confirm'] ?? null) !== '永久刪除') fail(400, '請輸入「永久刪除」確認', ['code' => 'confirm_required']);
    if (($d['rel_path'] ?? null) !== $m['rel_path']) fail(409, '教材路徑已改變，請重新整理後再操作');
    if ($m['status'] !== 'original_deleted' && !library_rel_ok((string)$m['rel_path'])) fail(400, '教材路徑指向系統資料夾或格式不正確：不能刪除');
    if (media_jobs((int)$m['id'], ['running'])) fail(409, '這個教材有工作正在執行（轉檔或字幕辨識）：請等它完成後再刪除');
    // 排隊中的工作先取消，避免在刪除前又開始轉檔或產生字幕
    q("UPDATE jobs SET status = 'cancelled', finished_at = NOW(), error = '教材即將永久刪除'
        WHERE status = 'queued' AND type <> 'purge_media' AND (payload->>'media_id' = ? OR payload->>'relink_media_id' = ?)",
        [(string)$m['id'], (string)$m['id']]);
    $r = enqueue_unique('purge_media', ['media_id' => (int)$m['id']], ['media_id' => (int)$m['id'], 'rel_path' => $m['rel_path']], 90, (int)$a['id']);
    audit($a, 'media.purge_requested', ['job_id' => $r['job_id'], 'title' => $m['title']], (int)$m['id'], (string)$m['rel_path']);
    return $r;
});

// ---------- 課程資料夾 ----------

route('GET', '/api/admin/folders', static function (): array {
    require_local_super();
    $rows = q_all("SELECT f.*, (SELECT COUNT(*) FROM media m WHERE m.course_id = f.id) AS media_count
                     FROM folders f WHERE f.parent_id IS NULL");
    $rows = array_map(static fn($f) => ['id' => (int)$f['id'], 'name' => $f['name'], 'display_name' => $f['display_name'],
        'label' => folder_label($f), 'is_course' => (bool)$f['is_course'], 'sort_order' => (int)$f['sort_order'],
        'enabled' => (bool)$f['enabled'], 'status' => $f['status'], 'media_count' => (int)$f['media_count'],
        'scanned_at' => ts($f['scanned_at'])], $rows);
    usort($rows, static fn($x, $y) => $x['sort_order'] <=> $y['sort_order'] ?: natural_compare($x['name'], $y['name']));
    return ['folders' => $rows];
});

// dev.55 FILE-01：教材目錄樹（唯讀）。資料來自掃描索引（folders、media），逐層載入；不讀任意路徑、不碰檔案系統。
route('GET', '/api/admin/tree', static function (): array {
    require_local_super();
    $root = library_root();
    $fid = (int)Req::query('folder', '0');
    $folder = null;
    if ($fid > 0) {
        $f = q_one('SELECT * FROM folders WHERE id = ?', [$fid]) ?? fail(404, '找不到資料夾');
        $folder = ['id' => (int)$f['id'], 'name' => $f['name'], 'rel_path' => $f['rel_path'], 'full_path' => "$root/" . $f['rel_path'],
            'is_course' => $f['parent_id'] === null];
    }
    $where = $fid > 0 ? 'f.parent_id = ?' : 'f.parent_id IS NULL';
    $params = $fid > 0 ? [$fid] : [];
    $folders = array_map(static fn($r) => [
        'id' => (int)$r['id'], 'name' => $r['name'], 'label' => folder_label($r), 'rel_path' => $r['rel_path'],
        'full_path' => "$root/" . $r['rel_path'], 'is_course' => $r['parent_id'] === null, 'is_course_flag' => (bool)$r['is_course'],
        'enabled' => (bool)$r['enabled'], 'status' => $r['status'], 'scanned_at' => ts($r['scanned_at']),
        'subfolders' => (int)$r['subfolders'], 'files' => (int)$r['files'],
    ], q_all("SELECT f.*, (SELECT COUNT(*) FROM folders c WHERE c.parent_id = f.id) AS subfolders,
                     (SELECT COUNT(*) FROM media m WHERE m.folder_id = f.id) AS files
                FROM folders f WHERE $where", $params));
    usort($folders, static fn($x, $y) => natural_compare($x['name'], $y['name']));
    $mwhere = $fid > 0 ? 'folder_id = ?' : 'folder_id IS NULL';
    $rows = q_all("SELECT id, title, file_name, ext, rel_path, kind, size_bytes, status, convert_status, doc_status, play_mode
                     FROM media WHERE $mwhere ORDER BY file_name LIMIT 501", $params);
    $files = array_map(static fn($m) => [
        'id' => (int)$m['id'], 'name' => $m['file_name'], 'title' => $m['title'], 'ext' => $m['ext'], 'rel_path' => $m['rel_path'],
        'full_path' => "$root/" . $m['rel_path'], 'kind' => $m['kind'], 'size' => (int)$m['size_bytes'], 'status' => $m['status'],
        'convert_status' => $m['convert_status'], 'doc_status' => $m['doc_status'], 'play_mode' => $m['play_mode'],
    ], array_slice($rows, 0, 500));
    usort($files, static fn($x, $y) => natural_compare($x['name'], $y['name']));
    $last = q_one("SELECT finished_at FROM scan_runs WHERE NOT dry_run AND status = 'done' ORDER BY id DESC LIMIT 1");
    return ['root' => $root, 'folder' => $folder, 'folders' => $folders, 'files' => $files, 'truncated' => count($rows) > 500,
        'root_files_note' => $fid === 0 && $files ? '放在根目錄的教材不屬於任何課程：助教、講師、admin 都看不到，只有 super 看得到' : null,
        'last_scan_at' => ts($last['finished_at'] ?? null)];
});

route('PATCH', '/api/admin/folders/{id}', static function (array $p): array {
    $a = require_local_super();
    $f = q_one('SELECT * FROM folders WHERE id = ? AND parent_id IS NULL', [$p['id']]) ?? fail(404, '找不到課程資料夾');
    $d = Req::body();
    $set = [];
    $dn = body_str($d, 'display_name', 80);
    if ($dn !== null) $set['display_name'] = $dn;
    foreach (['is_course', 'enabled'] as $k) {
        $v = body_bool($d, $k);
        if ($v !== null) $set[$k] = $v;
    }
    $so = body_int($d, 'sort_order', -100000, 100000);
    if ($so !== null) $set['sort_order'] = $so;
    if (($set['is_course'] ?? true) === false
        && (q_val('SELECT 1 FROM group_course_permissions WHERE folder_id = ? LIMIT 1', [$f['id']])
            || q_val('SELECT 1 FROM account_course_overrides WHERE folder_id = ? LIMIT 1', [$f['id']]))) {
        fail(409, '此課程仍有群組權限或個人覆寫設定，請先移除', ['code' => 'in_use']);
    }
    if ($set) {
        $cols = array_map(static fn($k) => "$k = ?", array_keys($set));
        q('UPDATE folders SET ' . implode(', ', $cols) . ', updated_at = NOW() WHERE id = ?', array_merge(array_values($set), [$f['id']]));
    }
    audit($a, 'folder.updated', ['folder_id' => (int)$f['id'], 'fields' => array_keys($set)]);
    return ['ok' => true];
});

// ---------- AVCHD 合集（第 13 章） ----------

function clip_group_out(array $g): array
{
    $members = q_all('SELECT id, title, file_name, clip_seq, duration_sec FROM media WHERE clip_group_id = ? ORDER BY clip_seq, id', [$g['id']]);
    return ['id' => (int)$g['id'], 'name' => $g['name'], 'source' => $g['source'], 'folder_id' => $g['folder_id'] !== null ? (int)$g['folder_id'] : null,
        'recorded_at' => ts($g['recorded_at']), 'members' => array_map(static fn($m) => ['id' => (int)$m['id'], 'title' => $m['title'],
            'file_name' => $m['file_name'], 'seq' => (int)$m['clip_seq'], 'duration' => $m['duration_sec'] !== null ? (float)$m['duration_sec'] : null], $members)];
}

/** 依自然排序重新編號合集成員；只剩 1 支時取消合集。 */
function renumber_clip_group(int $gid): void
{
    $rows = natural_sort_by(q_all('SELECT id, file_name FROM media WHERE clip_group_id = ?', [$gid]), 'file_name');
    if (count($rows) < 2) {
        q('UPDATE media SET clip_group_id = NULL, clip_seq = NULL WHERE clip_group_id = ?', [$gid]);
        q('DELETE FROM clip_groups WHERE id = ?', [$gid]);
        return;
    }
    foreach ($rows as $i => $r) q('UPDATE media SET clip_seq = ? WHERE id = ?', [$i + 1, $r['id']]);
}

route('GET', '/api/admin/clip-groups', static function (): array {
    require_local_super();
    $folder = Req::query('folder');
    $rows = $folder !== null && preg_match('/^\d+$/', $folder)
        ? q_all('SELECT * FROM clip_groups WHERE folder_id = ? ORDER BY name', [(int)$folder])
        : q_all('SELECT * FROM clip_groups ORDER BY updated_at DESC LIMIT 500');
    return ['groups' => array_map('clip_group_out', $rows)];
});

route('POST', '/api/admin/clip-groups', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $ids = body_ids($d, 'media_ids', 200);
    if (count($ids) < 2) fail(400, '合集至少要 2 支影片');
    $rows = q_all("SELECT id, folder_id, kind FROM media WHERE id = ANY(CAST(? AS bigint[]))", ['{' . implode(',', $ids) . '}']);
    if (count($rows) !== count($ids)) fail(400, '有影片不存在');
    if (count(array_unique(array_column($rows, 'folder_id'))) !== 1 || array_filter($rows, static fn($r) => $r['kind'] !== 'video')) {
        fail(400, '合集的影片必須在同一個資料夾');
    }
    $name = body_str($d, 'name', 120) ?: '手動合集';
    $gid = tx(static function () use ($ids, $rows, $name) {
        $old = array_filter(array_column(q_all('SELECT DISTINCT clip_group_id FROM media WHERE id = ANY(CAST(? AS bigint[])) AND clip_group_id IS NOT NULL',
            ['{' . implode(',', $ids) . '}']), 'clip_group_id'));
        $gid = (int)q_val("INSERT INTO clip_groups(folder_id, name, source) VALUES (?, ?, 'manual') RETURNING id", [$rows[0]['folder_id'], $name]);
        q('UPDATE media SET clip_group_id = ?, clip_manual = TRUE WHERE id = ANY(CAST(? AS bigint[]))', [$gid, '{' . implode(',', $ids) . '}']);
        foreach ($old as $o) renumber_clip_group((int)$o);
        renumber_clip_group($gid);
        return $gid;
    });
    audit($a, 'clip_group.created', ['group_id' => $gid, 'media_ids' => $ids]);
    return ['group' => clip_group_out(q_one('SELECT * FROM clip_groups WHERE id = ?', [$gid]))];
});

route('PATCH|DELETE', '/api/admin/clip-groups/{id}', static function (array $p): array {
    $a = require_local_super();
    $g = q_one('SELECT * FROM clip_groups WHERE id = ?', [$p['id']]) ?? fail(404, '找不到合集');
    if (Req::$method === 'DELETE') {
        // 取消合集：成員標記為手動，重新掃描不再自動建立
        q('UPDATE media SET clip_group_id = NULL, clip_seq = NULL, clip_manual = TRUE WHERE clip_group_id = ?', [$g['id']]);
        q('DELETE FROM clip_groups WHERE id = ?', [$g['id']]);
        audit($a, 'clip_group.cancelled', ['group_id' => (int)$g['id'], 'name' => $g['name']]);
        return ['ok' => true];
    }
    $d = Req::body();
    tx(static function () use ($g, $d) {
        $name = body_str($d, 'name', 120);
        if ($name !== null && $name !== '') q("UPDATE clip_groups SET name = ?, source = 'manual', updated_at = NOW() WHERE id = ?", [$name, $g['id']]);
        $add = body_ids($d, 'add', 200);
        $remove = body_ids($d, 'remove', 200);
        foreach ($add as $mid) {
            $m = q_one('SELECT id, folder_id, kind, clip_group_id FROM media WHERE id = ?', [$mid]) ?? fail(400, '影片不存在：' . $mid);
            if ($m['kind'] !== 'video' || (int)$m['folder_id'] !== (int)$g['folder_id']) fail(400, '只能加入同一資料夾的影片');
            q('UPDATE media SET clip_group_id = ?, clip_manual = TRUE WHERE id = ?', [$g['id'], $mid]);
            if ($m['clip_group_id'] && (int)$m['clip_group_id'] !== (int)$g['id']) renumber_clip_group((int)$m['clip_group_id']);
        }
        foreach ($remove as $mid) q('UPDATE media SET clip_group_id = NULL, clip_seq = NULL, clip_manual = TRUE WHERE id = ? AND clip_group_id = ?', [$mid, $g['id']]);
        if ($add || $remove) {
            q("UPDATE clip_groups SET source = 'manual', updated_at = NOW() WHERE id = ?", [$g['id']]);
            q('UPDATE media SET clip_manual = TRUE WHERE clip_group_id = ?', [$g['id']]);
        }
        renumber_clip_group((int)$g['id']);
    });
    audit($a, 'clip_group.updated', ['group_id' => (int)$g['id']]);
    $g2 = q_one('SELECT * FROM clip_groups WHERE id = ?', [$g['id']]);
    return $g2 ? ['group' => clip_group_out($g2)] : ['group' => null, 'cancelled' => true];
});

// ---------- 掃描 ----------

route('POST', '/api/admin/scan', static function (): array {
    $a = require_local_super();
    $dry = body_bool(Req::body(), 'dry_run') === true;
    $r = enqueue_unique('scan', ['dry_run' => $dry], ['dry_run' => $dry], 70, (int)$a['id']);
    audit($a, $dry ? 'scan.dry_run_requested' : 'scan.requested', ['job_id' => $r['job_id']]);
    return $r;
});

function scan_run_out(array $r, bool $details = false): array
{
    $o = ['id' => (int)$r['id'], 'job_id' => $r['job_id'] !== null ? (int)$r['job_id'] : null, 'dry_run' => (bool)$r['dry_run'],
        'started_at' => ts($r['started_at']), 'finished_at' => ts($r['finished_at']), 'status' => $r['status'],
        'added' => (int)$r['added'], 'changed' => (int)$r['changed'], 'moved' => (int)$r['moved'], 'missing' => (int)$r['missing'],
        'skipped_count' => count(jdec($r['skipped'], [])), 'summary' => jdec($r['summary'], []), 'error' => $r['error']];
    if ($details) $o += ['skipped' => jdec($r['skipped'], []), 'details' => jdec($r['details'], [])];
    return $o;
}

route('GET', '/api/admin/scan-runs', static function (): array {
    require_local_super();
    return ['runs' => array_map('scan_run_out', q_all('SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 50'))];
});

route('GET', '/api/admin/scan-runs/{id}', static function (array $p): array {
    require_local_super();
    return ['run' => scan_run_out(q_one('SELECT * FROM scan_runs WHERE id = ?', [$p['id']]) ?? fail(404, '找不到掃描紀錄'), true)];
});

// ---------- 轉檔佇列 ----------

route('GET', '/api/admin/conversions', static function (): array {
    $a = require_local_super();
    $status = Req::query('status');
    $conds = ["m.kind = 'video'"];
    $params = [];
    $groups = ['waiting' => ['pending', 'probing', 'queued'], 'running' => ['converting', 'verifying'], 'done' => ['done', 'direct'],
        'failed' => ['failed', 'verify_failed']];
    if (isset($groups[$status])) {
        $conds[] = 'm.convert_status = ANY(CAST(? AS text[]))';
        $params[] = '{' . implode(',', $groups[$status]) . '}';
    }
    $rows = q_all("SELECT m.*, to_json(m.tag_names)::text AS tags_json, NULL AS clip_name, NULL AS clip_source, 0 AS clip_count,
                          EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id) AS has_subtitle, m.probe::text AS probe_json, m.verify_result::text AS verify_json,
                          (SELECT row_to_json(a)::text FROM (SELECT id, status, (payload->>'immediate')::bool AS immediate, error, finished_at,
                                    (result->>'percent')::numeric AS percent
                             FROM jobs WHERE type = 'asr' AND (payload->>'media_id')::bigint = m.id ORDER BY id DESC LIMIT 1) a) AS asr_json,
                          (SELECT row_to_json(j)::text FROM (SELECT id, type, status, started_at, run_after, attempts, error, (payload->>'immediate')::bool AS immediate,
                                    (result->>'percent')::numeric AS percent, result->>'stage' AS stage
                             FROM jobs WHERE type IN ('probe','convert','verify') AND (payload->>'media_id')::bigint = m.id
                             ORDER BY id DESC LIMIT 1) j) AS job_json
                     FROM media m WHERE " . implode(' AND ', $conds) . ' ORDER BY m.rel_path LIMIT 2000', $params);
    return ['paused' => (bool)config_get('conversion_paused', false), 'schedule' => config_get('schedule', []),
        'agent_encoder' => agent_status()['encoder'],
        'items' => array_map(static function ($m) use ($a) {
            $probe = jdec($m['probe_json'], []);
            return media_out($m, $a, ['rel_path' => $m['rel_path'], 'probe' => [
                'container' => $probe['container'] ?? null, 'video_codec' => $probe['video_codec'] ?? null, 'audio_codec' => $probe['audio_codec'] ?? null,
                'width' => $probe['width'] ?? null, 'height' => $probe['height'] ?? null, 'interlaced' => $probe['interlaced'] ?? null,
                'fps' => $probe['fps'] ?? null], 'encoder' => $m['encoder'], 'convert_error' => $m['convert_error'],
                'verify' => jdec($m['verify_json'], null), 'job' => jdec($m['job_json'], null), 'verified_at' => ts($m['verified_at']),
                // dev.62：字幕狀態（轉檔佇列也能看到並操作「產生字幕」）
                'subtitle' => ['has' => (bool)$m['has_subtitle'], 'job' => jdec($m['asr_json'] ?? null, null)]]);
        }, $rows)];
});

route('POST', '/api/admin/conversions/{id}/retry', static function (array $p): array {
    $a = require_local_super();
    $m = q_one("SELECT * FROM media WHERE id = ? AND kind = 'video'", [$p['id']]) ?? fail(404, '找不到影片');
    if ($m['status'] === 'missing') fail(409, '原檔不存在，無法重新轉檔');
    if ($m['status'] === 'original_deleted') fail(409, '原檔已刪除，無法重新轉檔');
    q("UPDATE media SET convert_status = 'pending', convert_error = NULL WHERE id = ?", [$m['id']]);
    $r = enqueue_unique('probe', ['media_id' => (int)$m['id']], ['media_id' => (int)$m['id'], 'force' => true], 20, (int)$a['id']);
    audit($a, 'conversion.retry', ['job_id' => $r['job_id']], (int)$m['id'], (string)$m['rel_path']);
    return $r;
});

route('POST', '/api/admin/conversions/{id}/run-now', static function (array $p): array {
    $a = require_local_super();
    $m = q_one("SELECT * FROM media WHERE id = ? AND kind = 'video'", [$p['id']]) ?? fail(404, '找不到影片');
    if (!in_array($m['convert_status'], ['queued', 'failed', 'verify_failed'], true)) fail(409, '這支影片目前不需要轉檔或正在處理');
    q("UPDATE jobs SET status = 'cancelled', finished_at = NOW() WHERE type = 'convert' AND status = 'queued' AND (payload->>'media_id')::bigint = ?", [$m['id']]);
    q("UPDATE media SET convert_status = 'queued', convert_error = NULL WHERE id = ?", [$m['id']]);
    $id = enqueue_job('convert', ['media_id' => (int)$m['id'], 'immediate' => true], 30, (int)$a['id']);
    audit($a, 'conversion.run_now', ['job_id' => $id], (int)$m['id'], (string)$m['rel_path']);
    return ['job_id' => $id];
});

route('POST', '/api/admin/conversions/pause', static function (): array {
    $a = require_local_super();
    $paused = body_bool(Req::body(), 'paused') ?? fail(400, '請指定 paused');
    config_set('conversion_paused', $paused, (int)$a['id']);
    audit($a, $paused ? 'conversion.paused' : 'conversion.resumed');
    return ['paused' => $paused];
});

// ---------- 原檔刪除確認 ----------

const ORIGINAL_KEEP_DAYS = 30;   // 與 Agent worker/video.py 的 DELETE_AFTER_DAYS 相同（檢查通過後保留天數）
const ORIGINALS_DUE_SQL = "SELECT m.* FROM media m WHERE m.kind = 'video' AND m.status = 'present' AND m.play_mode IN ('remux','transcode')
    AND m.convert_status = 'done' AND m.verified_at IS NOT NULL AND m.delete_due_at <= NOW() AND m.original_deleted_at IS NULL";

route('GET', '/api/admin/originals/due', static function (): array {
    require_local_super();
    $rows = q_all(ORIGINALS_DUE_SQL . ' ORDER BY m.delete_due_at, m.rel_path');
    $pending = array_map('intval', array_column(q_all("SELECT (payload->>'media_id')::bigint AS id FROM jobs WHERE type = 'delete_original' AND status IN ('queued','running')"), 'id'));
    return ['items' => array_map(static fn($m) => [
        'id' => (int)$m['id'], 'title' => $m['title'], 'rel_path' => $m['rel_path'], 'original_size' => (int)$m['size_bytes'],
        'playback_size' => $m['playback_size'] !== null ? (int)$m['playback_size'] : null, 'play_mode' => $m['play_mode'],
        'verify' => jdec($m['verify_result'] ?? null, null), 'verified_at' => ts($m['verified_at']), 'delete_due_at' => ts($m['delete_due_at']),
        'path' => breadcrumb_text($m['folder_id'] !== null ? (int)$m['folder_id'] : null), 'deleting' => in_array((int)$m['id'], $pending, true),
        'lossy' => $m['play_mode'] === 'transcode',
    ], $rows)];
});

/**
 * dev.60 DEL-01：所有受管理的原檔（需要轉成 MP4 才能播放的影片，原檔尚未刪除）都列出來，提前查看保留天數與檢查結果。
 * 能不能刪除仍完全由 ORIGINALS_DUE_SQL 判斷（與 POST /originals/delete、Agent 刪除前的檢查相同），這裡只是顯示原因。
 */
route('GET', '/api/admin/originals', static function (): array {
    require_local_super();
    $rows = q_all("SELECT m.*, FLOOR(EXTRACT(EPOCH FROM (NOW() - m.verified_at)) / 86400)::int AS kept_days,
                          CEIL(EXTRACT(EPOCH FROM (m.delete_due_at - NOW())) / 86400)::int AS remain_days
                     FROM media m WHERE m.kind = 'video' AND m.status = 'present' AND m.original_deleted_at IS NULL
                      AND m.play_mode IN ('remux','transcode') ORDER BY m.delete_due_at NULLS LAST, m.rel_path LIMIT 3000");
    $due = array_map('intval', array_column(q_all(ORIGINALS_DUE_SQL), 'id'));
    $pending = array_map('intval', array_column(q_all("SELECT (payload->>'media_id')::bigint AS id FROM jobs WHERE type = 'delete_original' AND status IN ('queued','running')"), 'id'));
    $items = array_map(static function ($m) use ($due, $pending) {
        $eligible = in_array((int)$m['id'], $due, true);
        $st = (string)$m['convert_status'];
        $remain = $m['remain_days'] !== null ? max(0, (int)$m['remain_days']) : null;
        $reason = $eligible ? null : match (true) {
            in_array($st, ['failed', 'verify_failed'], true) => '轉檔或四項檢查沒有通過：不能刪除',
            $st !== 'done' => '轉檔尚未完成：不能刪除',
            $m['verified_at'] === null => '四項檢查尚未通過：不能刪除',
            default => "保留期未滿 " . ORIGINAL_KEEP_DAYS . " 天：還要 {$remain} 天",
        };
        return [
            'id' => (int)$m['id'], 'title' => $m['title'], 'rel_path' => $m['rel_path'], 'original_size' => (int)$m['size_bytes'],
            'playback_size' => $m['playback_size'] !== null ? (int)$m['playback_size'] : null, 'play_mode' => $m['play_mode'],
            'convert_status' => $st, 'verify' => jdec($m['verify_result'] ?? null, null), 'verified_at' => ts($m['verified_at']),
            'delete_due_at' => ts($m['delete_due_at']), 'kept_days' => $m['verified_at'] !== null ? max(0, (int)$m['kept_days']) : null,
            'remain_days' => $m['verified_at'] !== null ? $remain : null, 'keep_days' => ORIGINAL_KEEP_DAYS,
            'eligible' => $eligible, 'reason' => $reason,
            'path' => breadcrumb_text($m['folder_id'] !== null ? (int)$m['folder_id'] : null), 'deleting' => in_array((int)$m['id'], $pending, true),
            'lossy' => $m['play_mode'] === 'transcode',
        ];
    }, $rows);
    return ['items' => $items, 'keep_days' => ORIGINAL_KEEP_DAYS, 'eligible' => count(array_filter($items, static fn($i) => $i['eligible']))];
});

route('POST', '/api/admin/originals/delete', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    if (($d['confirm'] ?? null) !== '永久刪除') fail(400, '請輸入「永久刪除」確認', ['code' => 'confirm_required']);
    $ids = body_ids($d, 'media_ids', 500);
    if (!$ids) fail(400, '請勾選要刪除的原檔');
    $due = array_column(q_all(ORIGINALS_DUE_SQL . ' AND m.id = ANY(CAST(? AS bigint[]))', ['{' . implode(',', $ids) . '}']), null, 'id');
    $jobs = [];
    foreach ($ids as $id) {
        if (!isset($due[$id])) fail(409, "教材 $id 尚未到期或狀態已改變，請重新整理清單");
        $r = enqueue_unique('delete_original', ['media_id' => $id], ['media_id' => $id, 'expected_playback_size' => (int)$due[$id]['playback_size']], 10, (int)$a['id']);
        $jobs[] = $r['job_id'];
        audit($a, 'original.delete_confirmed', ['job_id' => $r['job_id']], $id, (string)$due[$id]['rel_path']);
    }
    return ['job_ids' => $jobs];
});

// ---------- 設定（排程、合集門檻、匯入匯出授權、排除名單、登入頁提示、字幕辨識） ----------

function valid_hhmm(mixed $v): ?string
{
    return is_string($v) && preg_match('/^([01]\d|2[0-3]):[0-5]\d$/', $v) ? $v : null;
}

route('GET', '/api/admin/config', static function (): array {
    require_local_super();
    $admins = q_all("SELECT id, username, full_name, active FROM accounts WHERE role = 'admin' ORDER BY full_name, username");
    return [
        'schedule' => config_get('schedule', []),
        'clip_threshold_gib' => (float)config_get('clip_threshold_gib', 1.9),
        'import_export_admin_ids' => array_map('intval', config_get('import_export_admin_ids', [])),
        'exclude_patterns' => config_get('exclude_patterns', []),
        'login_placeholders' => config_get('login_placeholders', []),
        'asr' => asr_settings(),
        'admins' => array_map(static fn($r) => ['id' => (int)$r['id'], 'username' => $r['username'], 'full_name' => $r['full_name'], 'active' => (bool)$r['active']], $admins),
    ];
});

route('PUT', '/api/admin/config', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $changed = [];
    if (array_key_exists('schedule', $d)) {
        $s = is_array($d['schedule']) ? $d['schedule'] : fail(400, '排程格式錯誤');
        $cur = config_get('schedule', []);
        foreach (['start', 'end', 'backup_time'] as $k) {
            if (array_key_exists($k, $s)) $cur[$k] = valid_hhmm($s[$k]) ?? fail(400, "時間格式錯誤：{$k}（HH:MM）");
        }
        foreach (['auto_convert', 'auto_whisper', 'whisper_after_convert'] as $k) {
            if (array_key_exists($k, $s)) $cur[$k] = is_bool($s[$k]) ? $s[$k] : fail(400, "$k 必須是 true 或 false");
        }
        if (array_key_exists('backup_keep', $s)) $cur['backup_keep'] = body_int($s, 'backup_keep', 1, 365);
        if (($cur['start'] ?? '') === ($cur['end'] ?? '')) fail(400, '開始與結束時間不可相同');
        config_set('schedule', $cur, (int)$a['id']);
        $changed[] = 'schedule';
    }
    if (array_key_exists('clip_threshold_gib', $d)) {
        $v = $d['clip_threshold_gib'];
        if (!is_int($v) && !is_float($v) || $v < 0.1 || $v > 64) fail(400, '合集判定門檻需介於 0.1～64 GiB');
        config_set('clip_threshold_gib', (float)$v, (int)$a['id']);
        $changed[] = 'clip_threshold_gib';
    }
    if (array_key_exists('import_export_admin_ids', $d)) {
        $ids = body_ids($d, 'import_export_admin_ids', 200);
        foreach ($ids as $id) {
            if (!q_val("SELECT 1 FROM accounts WHERE id = ? AND role = 'admin'", [$id])) fail(400, '只能授權管理員（admin）帳號');
        }
        config_set('import_export_admin_ids', $ids, (int)$a['id']);
        $changed[] = 'import_export_admin_ids';
    }
    if (array_key_exists('exclude_patterns', $d)) {
        $list = is_array($d['exclude_patterns']) ? $d['exclude_patterns'] : fail(400, '排除名單格式錯誤');
        $clean = [];
        foreach (array_slice($list, 0, 200) as $pat) {
            if (!is_string($pat) || trim($pat) === '' || mb_strlen($pat) > 200) fail(400, '排除名單項目格式錯誤');
            $clean[] = trim($pat);
        }
        config_set('exclude_patterns', $clean, (int)$a['id']);
        $changed[] = 'exclude_patterns';
    }
    if (array_key_exists('login_placeholders', $d)) {
        $lp = is_array($d['login_placeholders']) ? $d['login_placeholders'] : fail(400, '登入頁設定格式錯誤');
        $cur = config_get('login_placeholders', []);
        foreach (['username', 'password'] as $k) {
            if (array_key_exists($k, $lp)) {
                $v = body_str($lp, $k, 40) ?? '';
                $cur[$k] = $v;
            }
        }
        config_set('login_placeholders', $cur, (int)$a['id']);
        $changed[] = 'login_placeholders';
    }
    // dev.59：Whisper 引擎已移除；server_config 的 whisper_model 舊資料保留不刪，但不再接受修改
    if (array_key_exists('whisper_model', $d)) fail(400, ASR_REMOVED['whisper']);
    if (array_key_exists('asr', $d)) {
        $in = is_array($d['asr']) ? $d['asr'] : fail(400, '字幕辨識設定格式錯誤');
        $cur = config_get('asr', []);
        config_set('asr', asr_validate($in, is_array($cur) ? $cur : []), (int)$a['id']);
        $changed[] = 'asr';
    }
    audit($a, 'config.updated', ['keys' => $changed]);
    return ['ok' => true, 'changed' => $changed];
});

// ---------- 字幕辨識（dev.57：可替換引擎） ----------

/** 本機 4 個名稱＋線上 1 個；實際能不能用以 Agent 心跳回報為準（套件與模型檔是否存在）。 */
const ASR_ENGINE_IDS = ['paraformer-zh', 'sensevoice-small', 'qwen3-asr-0.6b', 'breeze-asr-25', 'online'];
const ASR_READY = ['paraformer-zh', 'sensevoice-small'];   // 這一版有實作的引擎
/** dev.59 移除的引擎（與 Agent engines.REMOVED 相同）：不能再選；舊設定仍照實顯示，讓 Super 看得到並改掉。 */
const ASR_REMOVED = ['whisper' => 'Whisper（faster-whisper）已在 dev.59 移除：請把引擎改成「自動」或 FunASR 引擎'];

function asr_settings(): array
{
    $c = config_get('asr', []);
    $c = is_array($c) ? $c : [];
    $g = is_array($c['glossary'] ?? null) ? $c['glossary'] : [];
    $engine = (string)($c['engine'] ?? 'auto');
    return [
        'engine' => in_array($engine, array_merge(['auto'], ASR_READY, array_keys(ASR_REMOVED)), true) ? $engine : 'auto',
        'max_chars' => (int)($c['max_chars'] ?? 20),
        'max_sec' => (int)($c['max_sec'] ?? 7),
        'threads' => (int)($c['threads'] ?? 0),
        'glossary' => [
            'fixes' => array_values(is_array($g['fixes'] ?? null) ? $g['fixes'] : []),
            'protect' => array_values(is_array($g['protect'] ?? null) ? $g['protect'] : []),
            'hotwords' => array_values(is_array($g['hotwords'] ?? null) ? $g['hotwords'] : []),
        ],
    ];
}

function asr_engines_status(): array
{
    $hb = config_get('agent_heartbeat', null);
    $st = is_array($hb) && is_array($hb['asr'] ?? null) ? $hb['asr'] : [];
    return [
        'reported' => is_array($st['engines'] ?? null),
        'agent_running' => agent_status()['running'],
        'engines' => is_array($st['engines'] ?? null) ? $st['engines'] : new stdClass(),
        'auto' => $st['auto'] ?? null,
        'error' => $st['error'] ?? null,
    ];
}

/** dev.61：這台 Server 最近 20 次字幕辨識的速度（RTF 中位數）；畫面用來估計「1 小時錄音約需幾分鐘」。 */
function asr_speed(): array
{
    $v = array_map('floatval', array_column(q_all("SELECT (asr_report->'metrics'->>'rtf')::float AS rtf FROM subtitles
        WHERE jsonb_typeof(asr_report->'metrics'->'rtf') = 'number' AND (asr_report->'metrics'->>'rtf')::float > 0
        ORDER BY created_at DESC LIMIT 20"), 'rtf'));
    if (!$v) return ['rtf' => null, 'samples' => 0];
    sort($v);
    $n = count($v);
    $med = $n % 2 ? $v[intdiv($n, 2)] : ($v[$n / 2 - 1] + $v[$n / 2]) / 2;
    return ['rtf' => round($med, 3), 'samples' => $n];
}

function asr_engine_choice(mixed $e): string
{
    if (is_string($e) && isset(ASR_REMOVED[$e])) fail(400, ASR_REMOVED[$e]);
    if (!is_string($e) || !in_array($e, array_merge(['auto'], ASR_ENGINE_IDS), true)) fail(400, '沒有這個字幕辨識引擎');
    if (!in_array($e, array_merge(['auto'], ASR_READY), true)) fail(400, $e === 'online' ? '線上路線尚未設定任何服務' : '這個引擎尚未提供');
    return $e;
}

/** 詞彙表清單：去頭尾空白、去重複；每項最多 40 字。 */
function asr_word_list(mixed $v, string $label, int $max): array
{
    if (!is_array($v) || count($v) > $max) fail(400, "{$label}最多 {$max} 項");
    $out = [];
    foreach ($v as $w) {
        if (!is_string($w)) fail(400, "{$label}的項目必須是文字");
        $w = trim($w);
        if ($w === '') continue;
        if (mb_strlen($w) > 40 || preg_match('/[\x00-\x1F\x7F]/u', $w)) fail(400, "{$label}的項目太長或含有不允許的字元：{$w}");
        $out[$w] = $w;
    }
    return array_values($out);
}

function asr_validate(array $in, array $cur): array
{
    if (array_key_exists('engine', $in)) $cur['engine'] = asr_engine_choice($in['engine']);
    foreach ([['max_chars', 10, 40], ['max_sec', 3, 10], ['threads', 0, 32]] as [$k, $lo, $hi]) {
        if (array_key_exists($k, $in)) $cur[$k] = body_int($in, $k, $lo, $hi);
    }
    if (array_key_exists('glossary', $in)) {
        $g = is_array($in['glossary']) ? $in['glossary'] : fail(400, '詞彙表格式錯誤');
        $fixes = [];
        foreach ((is_array($g['fixes'] ?? []) ? ($g['fixes'] ?? []) : fail(400, '修正表格式錯誤')) as $p) {
            if (!is_array($p) || count($p) !== 2 || !is_string($p[0]) || !is_string($p[1])) fail(400, '修正表每一項要有「錯字」與「正確」');
            [$a, $b] = [trim($p[0]), trim($p[1])];
            if ($a === '' || $a === $b) continue;
            if (mb_strlen($a) > 40 || mb_strlen($b) > 40 || preg_match('/[\x00-\x1F\x7F]/u', $a . $b)) fail(400, "修正表的項目太長或含有不允許的字元：{$a}");
            $fixes[$a] = [$a, $b];
        }
        if (count($fixes) > 500) fail(400, '修正表最多 500 項');
        $cur['glossary'] = [
            'fixes' => array_values($fixes),
            'protect' => asr_word_list($g['protect'] ?? [], '保護詞', 300),
            'hotwords' => asr_word_list($g['hotwords'] ?? [], '熱詞', 300),
        ];
    }
    return $cur;
}

// ---------- 字幕與文件轉換佇列 ----------

route('GET', '/api/admin/subtitles', static function (): array {
    require_local_super();
    $pending = q_all("SELECT m.id, m.title, m.rel_path, m.duration_sec FROM media m
                       WHERE m.kind = 'video' AND m.status <> 'missing' AND NOT EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id)
                         AND (m.play_mode = 'direct' OR m.convert_status = 'done') ORDER BY m.rel_path LIMIT 2000");
    $jobs = q_all("SELECT * FROM jobs WHERE type IN ('asr','subtitle_import') ORDER BY id DESC LIMIT 200");
    $done = q_all('SELECT s.media_id, s.source, s.model, s.segment_count, s.created_at, s.asr_report, m.title
                     FROM subtitles s JOIN media m ON m.id = s.media_id ORDER BY s.created_at DESC LIMIT 200');
    return [
        'asr' => asr_settings(),
        'engines' => asr_engines_status(),
        'speed' => asr_speed(),
        'pending' => array_map(static fn($r) => ['id' => (int)$r['id'], 'title' => $r['title'], 'rel_path' => $r['rel_path'],
            'duration' => $r['duration_sec'] !== null ? (float)$r['duration_sec'] : null], $pending),
        'jobs' => array_map('job_out', array_map(static function ($j) {
            $p = jdec($j['payload'], []);
            unset($p['vtt']);
            $j['payload'] = jenc($p ?: new stdClass());
            return $j;
        }, $jobs)),
        'done' => array_map(static function ($r) {
            $rep = jdec($r['asr_report'] ?? null);
            return ['media_id' => (int)$r['media_id'], 'title' => $r['title'], 'source' => $r['source'], 'model' => $r['model'],
                'segments' => (int)$r['segment_count'], 'created_at' => ts($r['created_at']),
                'engine' => is_array($rep) ? ($rep['engine'] ?? null) : null,
                'audio_quality' => is_array($rep) && is_array($rep['audio_quality'] ?? null)
                    ? ['level' => $rep['audio_quality']['level'] ?? 'unknown', 'reasons' => array_values($rep['audio_quality']['reasons'] ?? [])] : null,
                'qc' => is_array($rep) && is_array($rep['qc'] ?? null)
                    ? ['status' => $rep['qc']['status'] ?? null, 'reasons' => $rep['qc']['reasons'] ?? [], 'notes' => $rep['qc']['notes'] ?? []] : null,
                'rtf' => is_array($rep) ? ($rep['metrics']['rtf'] ?? null) : null,
                'audio_sec' => is_array($rep) ? ($rep['metrics']['audio_sec'] ?? null) : null];
        }, $done),
    ];
});

route('POST', '/api/admin/subtitles/{id}/run', static function (array $p): array {
    $a = require_local_super();
    $m = q_one("SELECT * FROM media WHERE id = ? AND kind = 'video'", [$p['id']]) ?? fail(404, '找不到影片');
    if (media_state($m) !== 'ready') fail(409, '影片尚未可以播放（轉檔完成後才能產生字幕）');
    $d = Req::body();
    $payload = ['media_id' => (int)$m['id'], 'immediate' => true];
    // dev.57：可指定這一次用哪個引擎（例如盲測比較）；沒指定就用設定（預設「自動」）
    if (array_key_exists('engine', $d) && $d['engine'] !== null) $payload['engine'] = asr_engine_choice($d['engine']);
    // dev.62：已經有排隊中的字幕工作（例如新影片自動排入、要等系統時段 22:30～03:00）→ 改成立即執行，不再回「已在佇列」卻一直不動
    $ex = q_one("SELECT id, status, payload FROM jobs WHERE type = 'asr' AND status IN ('queued','running') AND payload @> CAST(? AS jsonb)
                  ORDER BY id LIMIT 1", [jenc(['media_id' => (int)$m['id']])]);
    if ($ex && $ex['status'] === 'running') return ['job_id' => (int)$ex['id'], 'existing' => true, 'running' => true];
    if ($ex) {
        $pl = jdec($ex['payload'], []);
        $upgrade = empty($pl['immediate']) || (isset($payload['engine']) && ($pl['engine'] ?? null) !== $payload['engine']);
        if ($upgrade) {
            $pl['immediate'] = true;
            if (isset($payload['engine'])) $pl['engine'] = $payload['engine'];
            q("UPDATE jobs SET payload = CAST(? AS jsonb), priority = GREATEST(priority, 40), run_after = LEAST(run_after, NOW())
                WHERE id = ? AND status = 'queued'", [jenc($pl), (int)$ex['id']]);
            audit($a, 'subtitle.asr_requested', ['job_id' => (int)$ex['id'], 'engine' => $pl['engine'] ?? 'auto', 'upgraded' => true],
                (int)$m['id'], (string)$m['rel_path']);
        }
        return ['job_id' => (int)$ex['id'], 'existing' => true, 'upgraded' => $upgrade];
    }
    $r = enqueue_unique('asr', ['media_id' => (int)$m['id']], $payload, 40, (int)$a['id']);
    audit($a, 'subtitle.asr_requested', ['job_id' => $r['job_id'], 'engine' => $payload['engine'] ?? 'auto'], (int)$m['id'], (string)$m['rel_path']);
    return $r;
});

route('GET', '/api/admin/documents', static function (): array {
    require_local_super();
    $rows = q_all("SELECT id, title, rel_path, ext, doc_status, doc_error, size_bytes, updated_at FROM media WHERE kind = 'office' ORDER BY rel_path LIMIT 3000");
    // dev.60：最近一次實際轉換（不含「快取仍有效」）的 PDF 頁數與原檔版面說明（Agent worker/office.py layout_check）
    $last = [];
    foreach (q_all("SELECT DISTINCT ON ((payload->>'media_id')::bigint) (payload->>'media_id')::bigint AS mid, result FROM jobs
                     WHERE type = 'office_pdf' AND status = 'done' AND result->>'pdf_size' IS NOT NULL ORDER BY (payload->>'media_id')::bigint, id DESC") as $j) {
        $last[(int)$j['mid']] = jdec($j['result'], []);
    }
    return ['items' => array_map(static function ($r) use ($last) {
        $res = $last[(int)$r['id']] ?? [];
        return ['id' => (int)$r['id'], 'title' => $r['title'], 'rel_path' => $r['rel_path'], 'ext' => $r['ext'],
            'doc_status' => $r['doc_status'], 'doc_error' => $r['doc_error'], 'size' => (int)$r['size_bytes'], 'updated_at' => ts($r['updated_at']),
            'pages' => isset($res['pages']) ? (int)$res['pages'] : null,
            'notes' => array_values(array_filter(is_array($res['notes'] ?? null) ? $res['notes'] : [], 'is_string'))];
    }, $rows)];
});

route('POST', '/api/admin/documents/{id}/retry', static function (array $p): array {
    $a = require_local_super();
    $m = q_one("SELECT * FROM media WHERE id = ? AND kind = 'office'", [$p['id']]) ?? fail(404, '找不到文件');
    if ($m['status'] !== 'present') fail(409, '原檔不存在');
    q("UPDATE media SET doc_status = 'pending', doc_error = NULL WHERE id = ?", [$m['id']]);
    $r = enqueue_unique('office_pdf', ['media_id' => (int)$m['id']], ['media_id' => (int)$m['id'], 'force' => true], 20, (int)$a['id']);
    audit($a, 'document.retry', ['job_id' => $r['job_id']], (int)$m['id'], (string)$m['rel_path']);
    return $r;
});

// ---------- 系統檢查清單 ----------

const CHECKLIST_CATEGORIES = ['pending_dev', 'os_packages', 'server_hw', 'client_hw', 'client_os', 'custom'];

function checklist_out(array $r): array
{
    return ['id' => (int)$r['id'], 'category' => $r['category'], 'title' => $r['title'], 'status' => $r['status'], 'note' => $r['note'],
        'auto_value' => $r['auto_value'], 'device_id' => $r['device_id'] !== null ? (int)$r['device_id'] : null,
        'device_name' => $r['device_name'] ?? null, 'sort_order' => (int)$r['sort_order'], 'updated_at' => ts($r['updated_at'])];
}

route('GET', '/api/admin/checklist', static function (): array {
    require_local_super();
    $cat = Req::query('category');
    if ($cat !== null && !in_array($cat, CHECKLIST_CATEGORIES, true)) fail(400, '分類錯誤');
    $rows = q_all('SELECT c.*, d.name AS device_name FROM checklist_items c LEFT JOIN client_devices d ON d.id = c.device_id'
        . ($cat ? ' WHERE c.category = ?' : '') . ' ORDER BY c.category, c.sort_order, c.id', $cat ? [$cat] : []);
    return ['items' => array_map('checklist_out', $rows)];
});

route('POST', '/api/admin/checklist', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $cat = $d['category'] ?? 'custom';
    if (!in_array($cat, CHECKLIST_CATEGORIES, true)) fail(400, '分類錯誤');
    $title = body_str($d, 'title', 200) ?? '';
    if ($title === '') fail(400, '請輸入項目名稱');
    $dev = body_int($d, 'device_id', 1);
    $id = (int)q_val('INSERT INTO checklist_items(category, title, device_id, sort_order, updated_by)
                      VALUES (?,?,?, COALESCE((SELECT MAX(sort_order) + 1 FROM checklist_items WHERE category = ?), 0), ?) RETURNING id',
        [$cat, $title, $dev, $cat, $a['id']]);
    return ['item' => checklist_out(q_one('SELECT c.*, NULL AS device_name FROM checklist_items c WHERE id = ?', [$id]))];
});

route('PATCH|DELETE', '/api/admin/checklist/{id}', static function (array $p): array {
    $a = require_local_super();
    $it = q_one('SELECT * FROM checklist_items WHERE id = ?', [$p['id']]) ?? fail(404, '找不到項目');
    if (Req::$method === 'DELETE') {
        q('DELETE FROM checklist_items WHERE id = ?', [$it['id']]);
        return ['ok' => true];
    }
    $d = Req::body();
    $set = [];
    if (array_key_exists('status', $d)) {
        if (!in_array($d['status'], ['pending', 'confirmed', 'abnormal', 'na'], true)) fail(400, '狀態只能是待確認、已確認、異常、不適用');
        $set['status'] = $d['status'];
    }
    $note = body_str($d, 'note', 4000);
    if ($note !== null) $set['note'] = $note;
    $title = body_str($d, 'title', 200);
    if ($title !== null && $title !== '') $set['title'] = $title;
    if ($set) {
        $set['updated_by'] = (int)$a['id'];
        $cols = array_map(static fn($k) => "$k = ?", array_keys($set));
        q('UPDATE checklist_items SET ' . implode(', ', $cols) . ', updated_at = NOW() WHERE id = ?', array_merge(array_values($set), [$it['id']]));
    }
    return ['item' => checklist_out(q_one('SELECT c.*, d.name AS device_name FROM checklist_items c LEFT JOIN client_devices d ON d.id = c.device_id WHERE c.id = ?', [$it['id']]))];
});

// ---------- 診斷、PC 資訊、備份、首次設定、工作、Log ----------

route('POST', '/api/admin/diagnostics', static function (): array {
    $a = require_local_super();
    $r = enqueue_unique('diagnose', [], ['requested_by' => (int)$a['id']], 80, (int)$a['id']);
    return $r;
});

route('GET', '/api/admin/diagnostics', static function (): array {
    require_local_super();
    $kind = Req::query('kind') === 'client' ? 'client' : 'server';
    $rows = q_all('SELECT g.id, g.created_at, g.device_id, g.data::text AS data, d.name AS device_name FROM diagnostics g
                     LEFT JOIN client_devices d ON d.id = g.device_id WHERE g.kind = ? ORDER BY g.created_at DESC LIMIT 30', [$kind]);
    return ['items' => array_map(static fn($r) => ['id' => (int)$r['id'], 'created_at' => ts($r['created_at']),
        'device_id' => $r['device_id'] !== null ? (int)$r['device_id'] : null, 'device_name' => $r['device_name'], 'data' => jdec($r['data'], [])], $rows)];
});

// ---------- dev.62 方案 B 加密備份 ----------
const BACKUP_KEEP_MIN = 10;
const BACKUP_KEEP_MAX = 15;
const BACKUP_KEEP_DEFAULT = 15;
const BACKUP_UPLOAD_EVERY_DAYS = 3;

function backup_keep_days(): int
{
    $v = config_get('backup_settings', [])['keep_days'] ?? BACKUP_KEEP_DEFAULT;
    return is_int($v) && $v >= BACKUP_KEEP_MIN && $v <= BACKUP_KEEP_MAX ? $v : BACKUP_KEEP_DEFAULT;
}

function backup_out(array $r): array
{
    return ['id' => (int)$r['id'], 'created_at' => ts($r['created_at']), 'kind' => $r['kind'], 'status' => $r['status'],
        'file_path' => $r['file_path'], 'size' => (int)$r['size_bytes'], 'file_count' => (int)$r['file_count'],
        'verify' => jdec($r['verify_result'], null), 'error' => $r['error_message'],
        'format' => $r['format'] ?? 'legacy', 'sha256' => $r['sha256'] ?? null, 'enc_status' => $r['enc_status'] ?? 'none',
        'remote_status' => $r['remote_status'] ?? 'none', 'remote_path' => $r['remote_path'] ?? null,
        'remote_verified_at' => ts($r['remote_verified_at'] ?? null), 'remote_error' => $r['remote_error'] ?? null,
        'deleted_at' => ts($r['deleted_at'] ?? null), 'delete_reason' => $r['delete_reason'] ?? null,
        'manifest' => jdec($r['manifest'] ?? null, null)];
}

/** 備份頁的警告（不只靠顏色：每一則都有 ✗／⚠／ⓘ 與文字）。 */
function backup_warnings(array $key, array $remote): array
{
    $w = [];
    if (!preg_match('/^age1[02-9ac-hj-np-z]{58}$/', (string)($key['recipient'] ?? ''))) {
        $w[] = ['level' => 'error', 'text' => '✗ 尚未設定備份恢復密碼：Agent 不會建立任何備份（不產生未加密備份）。請在下方「加密與恢復密碼」設定。'];
    }
    $lastFail = q_one("SELECT created_at, error_message FROM backup_history WHERE status = 'failed' ORDER BY id DESC LIMIT 1");
    $lastOk = q_one("SELECT created_at FROM backup_history WHERE status = 'success' ORDER BY id DESC LIMIT 1");
    if ($lastFail && (!$lastOk || strtotime((string)$lastFail['created_at']) > strtotime((string)$lastOk['created_at']))) {
        $w[] = ['level' => 'error', 'text' => '✗ 最近一次備份失敗：' . mb_substr((string)$lastFail['error_message'], 0, 200)];
    }
    if (q_val("SELECT 1 FROM backup_history WHERE format = 'age' AND enc_status = 'failed' AND deleted_at IS NULL AND created_at > NOW() - INTERVAL '3 days'")) {
        $w[] = ['level' => 'error', 'text' => '✗ 有加密備份的解密驗證失敗：請看下方清單的「解密驗證」欄。'];
    }
    if (q_val("SELECT 1 FROM backup_history WHERE format = 'age' AND status = 'success' AND enc_status = 'unverified' AND deleted_at IS NULL AND created_at < NOW() - INTERVAL '26 hours'")) {
        $w[] = ['level' => 'warn', 'text' => '⚠ 有備份超過一天還沒做解密驗證：主機排程可能沒有執行（終端機：systemctl status istudy-backup.timer）。'];
    }
    if (empty($remote['configured'])) {
        $w[] = ['level' => 'warn', 'text' => '⚠ 尚未設定遠端備份（InterServer）：備份只在這台 Server 的 SSD，Server 損毀時會一起遺失。'];
    } else {
        $okAt = q_val("SELECT MAX(remote_verified_at) FROM backup_history WHERE remote_status = 'verified'");
        $up = $remote['last_upload'] ?? null;
        if (is_array($up) && empty($up['ok'])) {
            $n = (int)($remote['upload_failures'] ?? 1);
            $w[] = ['level' => 'warn', 'text' => "⚠ 遠端上傳失敗（連續 {$n} 次）：" . mb_substr((string)($up['message'] ?? ''), 0, 200) . '。本機備份都保留，可按「立即上傳」重試。'];
        }
        if (!$okAt) {
            $w[] = ['level' => 'warn', 'text' => '⚠ 還沒有任何已驗證的遠端副本。'];
        } elseif (time() - strtotime((string)$okAt) > (BACKUP_UPLOAD_EVERY_DAYS + 4) * 86400) {
            $w[] = ['level' => 'warn', 'text' => '⚠ 超過 ' . (BACKUP_UPLOAD_EVERY_DAYS + 4) . ' 天沒有成功的遠端副本。'];
        }
    }
    if (($key['remote_synced'] ?? true) === false && !empty($remote['configured'])) {
        $w[] = ['level' => 'info', 'text' => 'ⓘ 恢復密碼已更改：遠端的恢復金鑰檔會在下次上傳時更新。'];
    }
    $legacy = (int)q_val("SELECT COUNT(*) FROM backup_history WHERE format = 'legacy' AND status = 'success' AND deleted_at IS NULL");
    if ($legacy) {
        $w[] = ['level' => 'info', 'text' => "ⓘ 有 {$legacy} 份 dev.61 以前的未加密備份（.tar.gz）：系統不會自動上傳或刪除；內含 .env 等設定，請自行評估保存方式。"];
    }
    return $w;
}

route('GET', '/api/admin/backups', static function (): array {
    require_local_super();
    $rows = q_all('SELECT * FROM backup_history ORDER BY created_at DESC, id DESC LIMIT 60');
    $last = q_one("SELECT created_at FROM backup_history WHERE status = 'success' ORDER BY created_at DESC LIMIT 1");
    $key = config_get('backup_key', []);
    $remote = config_get('backup_remote', []);
    $key = is_array($key) ? $key : [];
    $remote = is_array($remote) ? $remote : [];
    $recipient = (string)($key['recipient'] ?? '');
    return ['items' => array_map('backup_out', $rows),
        'last_success_at' => ts($last['created_at'] ?? null),
        'days_since_success' => $last ? (int)floor((time() - strtotime((string)$last['created_at'])) / 86400) : null,
        'settings' => ['keep_days' => backup_keep_days(), 'keep_min' => BACKUP_KEEP_MIN, 'keep_max' => BACKUP_KEEP_MAX,
            'upload_every_days' => BACKUP_UPLOAD_EVERY_DAYS, 'backup_time' => config_get('schedule', [])['backup_time'] ?? '03:30'],
        // 只放公開資訊：公鑰、時間；私鑰、恢復密碼、遠端密碼都不在資料庫
        'key' => ['initialized' => (bool)preg_match('/^age1[02-9ac-hj-np-z]{58}$/', $recipient), 'recipient' => $recipient ?: null,
            'created_at' => $key['created_at'] ?? null, 'changed_at' => $key['changed_at'] ?? null, 'remote_synced' => $key['remote_synced'] ?? null],
        'remote' => ['configured' => !empty($remote['configured']), 'protocol' => $remote['protocol'] ?? null, 'host' => $remote['host'] ?? null,
            'port' => $remote['port'] ?? null, 'username' => $remote['username'] ?? null, 'dir' => $remote['dir'] ?? null,
            'updated_at' => $remote['updated_at'] ?? null, 'last_test' => $remote['last_test'] ?? null, 'last_upload' => $remote['last_upload'] ?? null,
            'last_ok_at' => ts(q_val("SELECT MAX(remote_verified_at) FROM backup_history WHERE remote_status = 'verified'"))],
        'host' => config_get('backup_host', null),
        'warnings' => backup_warnings($key, $remote)];
});

// dev.62：保留天數 10～15 天（主機清理時讀取）
route('PUT', '/api/admin/backups/settings', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $v = $d['keep_days'] ?? null;
    if (!is_int($v) || $v < BACKUP_KEEP_MIN || $v > BACKUP_KEEP_MAX) fail(400, '保留天數要在 ' . BACKUP_KEEP_MIN . '～' . BACKUP_KEEP_MAX . ' 天');
    $before = backup_keep_days();
    config_set('backup_settings', ['keep_days' => $v], (int)$a['id']);
    audit($a, 'backup.settings_changed', ['keep_days' => $v, 'before' => $before]);
    return ['keep_days' => $v];
});

route('POST', '/api/admin/backups', static function (): array {
    $a = require_local_super();
    // dev.55 BK-01：「建立備份」只建立備份，不做還原。verify 參數只為相容舊畫面保留。
    $verify = body_bool(Req::body(), 'verify') === true;
    $r = enqueue_unique('backup', [], ['kind' => 'manual', 'verify' => $verify], 60, (int)$a['id']);
    audit($a, 'backup.requested', ['job_id' => $r['job_id'], 'verify' => $verify]);
    return $r;
});

// dev.55 BK-01：對既有備份另外做「完整性檢查」或「隔離還原測試」（只還原到 istudy_restore_test，不碰正式資料庫）。
// 正式還原不提供 API：只能在主機終端機執行 istudyctl restore --target prod。
route('POST', '/api/admin/backups/{id}/check', static function (array $p): array {
    $a = require_local_super();
    $mode = Req::body()['mode'] ?? null;
    if (!in_array($mode, ['integrity', 'restore_test'], true)) fail(400, '檢查方式只能是 integrity 或 restore_test');
    if (!q_val("SELECT 1 FROM backup_history WHERE id = ? AND status = 'success'", [$p['id']])) fail(404, '找不到這份備份');
    $r = enqueue_unique('backup_check', ['backup_id' => (int)$p['id'], 'mode' => $mode], [], 60, (int)$a['id']);
    audit($a, $mode === 'integrity' ? 'backup.integrity_check_requested' : 'backup.restore_test_requested',
        ['job_id' => $r['job_id'], 'backup_id' => (int)$p['id']]);
    return $r;
});

route('GET', '/api/admin/setup', static function (): array {
    require_local_super();
    return ['setup' => config_get('setup', ['completed' => false, 'step' => 1, 'steps' => []])];
});

route('PUT', '/api/admin/setup', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $cur = config_get('setup', ['completed' => false, 'step' => 1, 'steps' => []]);
    $step = body_int($d, 'step', 1, 9);
    if ($step !== null) $cur['step'] = $step;
    if (array_key_exists('steps', $d)) {
        if (!is_array($d['steps'])) fail(400, '步驟格式錯誤');
        foreach ($d['steps'] as $k => $v) {
            if (!preg_match('/^[1-9]$/', (string)$k) || !is_array($v)) fail(400, '步驟格式錯誤');
            $cur['steps'][(string)$k] = array_intersect_key($v, array_flip(['status', 'note', 'result', 'at']));
        }
    }
    $completed = body_bool($d, 'completed');
    if ($completed !== null) {
        $cur['completed'] = $completed;
        if ($completed) $cur['completed_at'] = date(DATE_ATOM);
    }
    if (strlen(jenc($cur)) > 256 * 1024) fail(400, '資料太大');
    config_set('setup', $cur, (int)$a['id']);
    if ($completed) audit($a, 'setup.completed');
    return ['setup' => $cur];
});

route('GET', '/api/admin/jobs', static function (): array {
    require_local_super();
    $conds = [];
    $params = [];
    $type = Req::query('type');
    if ($type) {
        // dev.62：可用逗號一次查幾種（備份頁同時看 Agent 與主機的備份工作）
        $types = array_slice(array_values(array_filter(explode(',', (string)$type), static fn($t) => $t !== '')), 0, 10);
        $conds[] = 'type IN (' . implode(',', array_fill(0, max(1, count($types)), '?')) . ')';
        array_push($params, ...($types ?: ['']));
    }
    $status = Req::query('status');
    if (in_array($status, ['queued', 'running', 'done', 'failed', 'cancelled'], true)) {
        $conds[] = 'status = ?';
        $params[] = $status;
    }
    $limit = query_int('limit', 200, 1, 2000);
    $rows = q_all('SELECT * FROM jobs' . ($conds ? ' WHERE ' . implode(' AND ', $conds) : '') . " ORDER BY id DESC LIMIT $limit", $params);
    return ['items' => array_map(static function ($j) {
        $o = job_out($j);
        unset($o['payload']['vtt']);
        return $o;
    }, $rows)];
});

route('GET', '/api/admin/jobs/{id}', static function (array $p): array {
    require_local_super();
    $j = q_one('SELECT * FROM jobs WHERE id = ?', [$p['id']]) ?? fail(404, '找不到工作');
    $o = job_out($j);
    unset($o['payload']['vtt']);
    return ['job' => $o];
});

route('POST', '/api/admin/jobs/{id}/cancel', static function (array $p): array {
    $a = require_local_super();
    $n = q("UPDATE jobs SET status = 'cancelled', finished_at = NOW() WHERE id = ? AND status = 'queued'", [$p['id']])->rowCount();
    if (!$n) fail(409, '只能取消等待中的工作');
    audit($a, 'job.cancelled', ['job_id' => $p['id']]);
    return ['ok' => true];
});

route('GET', '/api/admin/logs', static function (): array {
    require_local_super();
    $source = Req::query('source', 'server');
    $limit = query_int('limit', 500, 1, 5000);
    $dateConds = [];
    $params = [];
    foreach (['from' => '>=', 'to' => '<'] as $k => $op) {
        $v = Req::query($k);
        if ($v !== null && $v !== '') {
            if (valid_date($v) === null) fail(400, '日期格式錯誤');
            $dateConds[] = $k === 'to' ? 'created_at < CAST(? AS date) + 1' : 'created_at >= CAST(? AS date)';
            $params[] = $v;
        }
    }
    if ($source === 'client') {
        $where = $dateConds ? 'WHERE ' . implode(' AND ', array_map(static fn($c) => 'l.' . $c, $dateConds)) : '';
        $rows = q_all("SELECT l.*, d.name AS device_name FROM client_logs l LEFT JOIN client_devices d ON d.id = l.device_id $where
                        ORDER BY l.created_at DESC LIMIT $limit", $params);
        return ['items' => array_map(static fn($r) => ['id' => (int)$r['id'], 'created_at' => ts($r['created_at']), 'logged_at' => ts($r['logged_at']),
            'source' => 'client', 'level' => $r['level'], 'message' => $r['message'], 'pc_name' => $r['pc_name'], 'device_name' => $r['device_name'],
            'app' => trim(channel_label((string)$r['app_channel']) . ' ' . $r['app_version'])], $rows)];
    }
    if (!in_array($source, ['server', 'agent', 'host'], true)) fail(400, '來源錯誤');
    $conds = array_merge(['source = ?'], $dateConds);
    $rows = q_all('SELECT * FROM system_logs WHERE ' . implode(' AND ', $conds) . " ORDER BY created_at DESC LIMIT $limit", array_merge([$source], $params));
    return ['items' => array_map(static fn($r) => ['id' => (int)$r['id'], 'created_at' => ts($r['created_at']), 'source' => $r['source'],
        'level' => $r['level'], 'message' => $r['message'], 'context' => jdec($r['context'], [])], $rows)];
});

route('POST', '/api/admin/logs', static function (): array {
    // Server App 回報主機維護（pkexec istudyctl）的執行結果
    $a = require_local_super();
    $d = Req::body();
    $msg = body_str($d, 'message', 2000) ?? '';
    if ($msg === '') fail(400, '缺少內容');
    $level = in_array($d['level'] ?? 'info', ['info', 'warn', 'error'], true) ? $d['level'] : 'info';
    $ctx = is_array($d['context'] ?? null) ? $d['context'] : [];
    q('INSERT INTO system_logs(source, level, message, context) VALUES (\'host\', ?, ?, CAST(? AS jsonb))', [$level, $msg, jenc($ctx ?: new stdClass())]);
    audit($a, 'host.action', ['message' => mb_substr($msg, 0, 200)]);
    return ['ok' => true];
});
