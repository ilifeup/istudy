<?php
declare(strict_types=1);

/** 課程目錄、教材、串流、字幕、搜尋（第 06、07、08、12 章）。 */

function personal_map(array $a, array $mediaIds): array
{
    if (!$mediaIds) return [];
    $in = '{' . implode(',', array_map('intval', $mediaIds)) . '}';
    $rows = q_all(
        'SELECT m.id, h.learning_status, h.last_position_sec, h.last_page, h.last_viewed_at,
                COALESCE(b.is_favorite, FALSE) AS is_favorite, COALESCE(b.is_bookmarked, FALSE) AS is_bookmarked
           FROM unnest(CAST(? AS bigint[])) AS m(id)
           LEFT JOIN view_history h ON h.media_id = m.id AND h.account_id = ?
           LEFT JOIN bookmarks b ON b.media_id = m.id AND b.account_id = ?', [$in, $a['id'], $a['id']]);
    $out = [];
    foreach ($rows as $r) {
        $out[(int)$r['id']] = [
            'learning_status' => $r['learning_status'] ?? 'not_started',
            'last_position_sec' => $r['last_position_sec'] !== null ? (int)$r['last_position_sec'] : null,
            'last_page' => $r['last_page'] !== null ? (int)$r['last_page'] : null,
            'last_viewed_at' => ts($r['last_viewed_at']),
            'is_favorite' => (bool)$r['is_favorite'],
            'is_bookmarked' => (bool)$r['is_bookmarked'],
        ];
    }
    return $out;
}

// ---------- 目錄樹（只到資料夾層） ----------

route('GET', '/api/tree', static function (): array {
    $a = actor();
    [$vis, $vp] = vis_sql($a, 'COALESCE(f.course_id, f.id)');
    $rows = q_all(
        "SELECT f.id, f.parent_id, f.name, f.display_name, f.is_course, f.sort_order,
                EXISTS (SELECT 1 FROM folders c WHERE c.parent_id = f.id AND c.status = 'present') AS has_children
           FROM folders f
          WHERE f.status = 'present' AND $vis
            AND (f.parent_id IS NOT NULL OR f.enabled)", $vp);
    $out = array_map(static fn($r) => [
        'id' => (int)$r['id'],
        'parent_id' => $r['parent_id'] !== null ? (int)$r['parent_id'] : null,
        'name' => folder_label($r),
        'is_course' => (bool)$r['is_course'],
        'sort_order' => (int)$r['sort_order'],
        'has_children' => (bool)$r['has_children'],
    ], $rows);
    usort($out, static fn($x, $y) => [$x['parent_id'] ?? 0, $x['sort_order']] <=> [$y['parent_id'] ?? 0, $y['sort_order']]
        ?: natural_compare($x['name'], $y['name']));
    $indexed = (int)q_val('SELECT COUNT(*) FROM scan_runs WHERE status = \'done\' AND NOT dry_run') > 0;
    return ['folders' => $out, 'indexed' => $indexed];
});

route('GET', '/api/folders/{id}/items', static function (array $p): array {
    $a = actor();
    $f = folder_for_actor($a, $p['id']);
    $folders = q_all("SELECT id, name, display_name, sort_order FROM folders WHERE parent_id = ? AND status = 'present'", [$f['id']]);
    $folders = array_map(static fn($r) => ['id' => (int)$r['id'], 'name' => folder_label($r), 'sort_order' => (int)$r['sort_order']], $folders);
    usort($folders, static fn($x, $y) => $x['sort_order'] <=> $y['sort_order'] ?: natural_compare($x['name'], $y['name']));
    $media = q_all(
        "SELECT m.*, to_json(m.tag_names)::text AS tags_json, cg.name AS clip_name, cg.source AS clip_source,
                (SELECT COUNT(*) FROM media x WHERE x.clip_group_id = m.clip_group_id) AS clip_count,
                EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id) AS has_subtitle
           FROM media m LEFT JOIN clip_groups cg ON cg.id = m.clip_group_id
          WHERE m.folder_id = ?", [$f['id']]);
    $media = natural_sort_by($media, 'file_name');
    $personal = personal_map($a, array_column($media, 'id'));
    return [
        'folder' => ['id' => (int)$f['id'], 'name' => folder_label($f), 'parent_id' => $f['parent_id'] !== null ? (int)$f['parent_id'] : null],
        'breadcrumbs' => folder_breadcrumbs((int)$f['id']),
        'folders' => $folders,
        'media' => array_map(static fn($m) => media_out($m, $a, $personal[(int)$m['id']] ?? []), $media),
    ];
});

// ---------- 單一教材（含同資料夾播放清單、合集） ----------

route('GET', '/api/media/{id}', static function (array $p): array {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    $playlist = [];
    $index = null;
    if ($m['kind'] === 'video' && $m['folder_id'] !== null) {
        $rows = natural_sort_by(q_all("SELECT id, title, file_name FROM media WHERE folder_id = ? AND kind = 'video'", [$m['folder_id']]), 'file_name');
        foreach ($rows as $i => $r) {
            $playlist[] = ['id' => (int)$r['id'], 'title' => $r['title']];
            if ((int)$r['id'] === (int)$m['id']) $index = $i;
        }
    }
    $clipMembers = [];
    if ($m['clip_group_id']) {
        $clipMembers = array_map(static fn($r) => ['id' => (int)$r['id'], 'title' => $r['title'], 'file_name' => $r['file_name'], 'seq' => (int)$r['clip_seq']],
            q_all('SELECT id, title, file_name, clip_seq FROM media WHERE clip_group_id = ? ORDER BY clip_seq, id', [$m['clip_group_id']]));
    }
    $personal = personal_map($a, [(int)$m['id']])[(int)$m['id']] ?? [];
    return media_out($m, $a, $personal + [
        'breadcrumbs' => folder_breadcrumbs($m['folder_id'] !== null ? (int)$m['folder_id'] : null),
        'playlist' => $index === null ? [] : $playlist,
        'playlist_index' => $index,
        'clip_members' => $clipMembers,
        'can_import_export' => can_import_export($a),
        'error_text' => $m['convert_error'] && role_at_least($a, 'admin') ? $m['convert_error'] : null,
    ]);
});

// ---------- 串流：PHP 驗證後交給 Nginx（X-Accel-Redirect） ----------

/** 決定要送出的實體檔案：[nginx internal 前綴, 相對路徑, 本機根目錄, Content-Type] */
function stream_target(array $m): array
{
    $media = rtrim(env('MEDIA_ROOT', '/Media'), '/');
    if ($m['status'] === 'missing') fail(410, '⚠ 教材目前不可用（待管理者重新掃描）', ['code' => 'missing']);
    switch ($m['kind']) {
        case 'video':
            if ($m['play_mode'] === 'direct' && $m['status'] === 'present') {
                return ['/_protected/original', $m['rel_path'], library_root(), mime_for_ext((string)$m['ext'])];
            }
            if ($m['convert_status'] === 'done' && $m['playback_path']) {
                return ['/_protected/playback', $m['id'] . '.mp4', "$media/playback", 'video/mp4'];
            }
            fail(409, '⏳ 此影片尚未轉檔完成', ['code' => 'not_ready']);
        case 'office':
            if ($m['doc_status'] === 'done') return ['/_protected/doc', $m['id'] . '.pdf', "$media/doc-cache", 'application/pdf'];
            fail(409, '⏳ 文件轉換中', ['code' => 'doc_converting']);
        case 'pdf':
        case 'image':
            if ($m['status'] !== 'present') fail(410, '⚠ 教材目前不可用', ['code' => 'missing']);
            return ['/_protected/original', $m['rel_path'], library_root(), mime_for_ext((string)$m['ext'])];
        default:
            fail(400, '此類型不以串流方式開啟');
    }
}

route('GET', '/api/media/{id}/stream', static function (array $p): never {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    if ($m['kind'] === 'text') stream_text($m);
    [$prefix, $rel, $root, $mime] = stream_target($m);
    $file = $prefix === '/_protected/original' ? library_file($rel) : resolve_under($root, $rel);   // 原檔不能指向系統資料夾
    if ($file === null) {
        sys_log('warn', 'stream file unavailable', ['media_id' => (int)$m['id']]);
        fail(503, '⚠ Server 無法讀取檔案', ['code' => 'file_unavailable']);
    }
    if (env('ISTUDY_SENDFILE', 'nginx') === 'nginx') {
        // CORS 標頭由 Nginx 的 internal location 加上（避免重複）
        header('X-Accel-Redirect: ' . accel_path($prefix, $rel));
        header('Content-Type: ' . $mime);
        header('Content-Disposition: inline');
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');
        exit;
    }
    send_file_ranged($file, $mime); // 開發與測試用（沒有 Nginx 時）
});

/** 開發／測試用：PHP 自行處理分段請求（沿用 dev.45）。正式環境由 Nginx 處理。 */
function send_file_ranged(string $file, string $mime): never
{
    $size = filesize($file);
    $start = 0;
    $end = $size - 1;
    $range = $_SERVER['HTTP_RANGE'] ?? '';
    send_cors_headers();
    if ($range !== '') {
        if (!preg_match('/^bytes=(\d*)-(\d*)$/', $range, $r) || ($r[1] === '' && $r[2] === '')) {
            header('Content-Range: bytes */' . $size);
            http_response_code(416);
            exit;
        }
        $start = $r[1] === '' ? max(0, $size - (int)$r[2]) : (int)$r[1];
        $end = $r[1] === '' ? $size - 1 : ($r[2] === '' ? $size - 1 : min($size - 1, (int)$r[2]));
        if ($start > $end || $start >= $size) {
            header('Content-Range: bytes */' . $size);
            http_response_code(416);
            exit;
        }
        http_response_code(206);
        header("Content-Range: bytes $start-$end/$size");
    }
    header('Accept-Ranges: bytes');
    header('Content-Type: ' . $mime);
    header('Content-Disposition: inline');
    header('Cache-Control: no-store');
    header('Content-Length: ' . ($end - $start + 1));
    if (Req::$method === 'HEAD') exit;
    $f = fopen($file, 'rb');
    fseek($f, $start);
    $left = $end - $start + 1;
    while ($left > 0 && !feof($f)) {
        $buf = fread($f, (int)min(262144, $left));
        if ($buf === false) break;
        echo $buf;
        $left -= strlen($buf);
        if (connection_aborted()) break;
    }
    fclose($f);
    exit;
}

/** TXT：由 Server 偵測編碼（UTF-8／Big5）後以 UTF-8 回傳（第 12 章）。 */
function stream_text(array $m): never
{
    if ($m['status'] !== 'present') fail(410, '⚠ 教材目前不可用', ['code' => 'missing']);
    $file = library_file((string)$m['rel_path']);
    if ($file === null) fail(503, '⚠ Server 無法讀取檔案', ['code' => 'file_unavailable']);
    if (filesize($file) > 5 * 1024 * 1024) fail(413, '文字檔超過 5MB，無法在 App 內顯示');
    http_response_code(200);
    send_cors_headers();
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo text_to_utf8((string)file_get_contents($file));
    exit;
}

// ---------- 字幕 ----------

function subtitle_cues_for(int $mediaId): array
{
    return array_map(static fn($r) => [(int)$r['start_ms'], (int)$r['end_ms'], $r['text']],
        q_all('SELECT start_ms, end_ms, text FROM subtitle_cues WHERE media_id = ? ORDER BY idx', [$mediaId]));
}

route('GET', '/api/media/{id}/subtitle', static function (array $p): never {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    if (!$m['has_subtitle']) fail(404, '此影片尚無字幕', ['code' => 'no_subtitle']);
    http_response_code(200);
    send_cors_headers();
    header('Content-Type: text/vtt; charset=utf-8');
    header('Cache-Control: no-store');
    echo cues_to_vtt(subtitle_cues_for((int)$m['id']));
    exit;
});

route('GET', '/api/media/{id}/subtitle/segments', static function (array $p): array {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    if (!$m['has_subtitle']) return ['segments' => [], 'has_subtitle' => false];
    $info = q_one('SELECT source, model, created_at FROM subtitles WHERE media_id = ?', [$m['id']]);
    return [
        'has_subtitle' => true,
        'source' => $info['source'] ?? null,
        'model' => $info['model'] ?? null,
        'created_at' => ts($info['created_at'] ?? null),
        'segments' => array_map(static fn($c) => ['start' => $c[0] / 1000, 'end' => $c[1] / 1000, 'text' => $c[2]], subtitle_cues_for((int)$m['id'])),
    ];
});

route('POST', '/api/media/{id}/subtitle/import', static function (array $p): array {
    $a = require_import_export();
    $m = media_for_actor($a, $p['id']);
    if ($m['kind'] !== 'video') fail(400, '只有影片可以匯入字幕');
    $d = Req::body();
    // 必須明確確認教材 ID，避免匯入到錯誤影片
    if (body_int($d, 'confirm_media_id', 1) !== (int)$m['id']) fail(400, '請確認要匯入的教材 ID', ['code' => 'confirm_required']);
    $content = $d['content'] ?? null;
    if (!is_string($content) || $content === '' || strlen($content) > 8 * 1024 * 1024) fail(400, '字幕內容是空的或超過 8MB');
    $content = text_to_utf8($content);
    $cues = parse_subtitles($content);
    if (!$cues) fail(400, '無法辨識字幕格式（需要 SRT 或 VTT）', ['code' => 'bad_subtitle']);
    $job = enqueue_job('subtitle_import', ['media_id' => (int)$m['id'], 'vtt' => cues_to_vtt($cues), 'account_id' => (int)$a['id']], 50, (int)$a['id']);
    audit($a, 'subtitle.imported', ['cues' => count($cues), 'job_id' => $job, 'file' => mb_substr((string)($d['filename'] ?? ''), 0, 200)],
        (int)$m['id'], (string)$m['rel_path']);
    return ['job_id' => $job, 'cues' => count($cues)];
});

route('GET', '/api/media/{id}/subtitle/export', static function (array $p): never {
    $a = require_import_export();
    $m = media_for_actor($a, $p['id']);
    if (!$m['has_subtitle']) fail(404, '此影片尚無字幕');
    $format = Req::query('format', 'vtt') === 'srt' ? 'srt' : 'vtt';
    $cues = subtitle_cues_for((int)$m['id']);
    audit($a, 'subtitle.exported', ['format' => $format], (int)$m['id'], (string)$m['rel_path']);
    $name = pathinfo((string)$m['file_name'], PATHINFO_FILENAME) . '.' . $format;
    http_response_code(200);
    send_cors_headers();
    header('Content-Type: ' . ($format === 'srt' ? 'application/x-subrip; charset=utf-8' : 'text/vtt; charset=utf-8'));
    header("Content-Disposition: attachment; filename*=UTF-8''" . rawurlencode($name));
    header('Cache-Control: no-store');
    echo $format === 'srt' ? cues_to_srt($cues) : cues_to_vtt($cues);
    exit;
});

// ---------- 搜尋（先過濾權限再取前 200 筆） ----------

route('GET', '/api/search', static function (): array {
    $a = actor();
    $q = trim((string)Req::query('q', ''));
    if (mb_strlen($q) < 1) fail(400, '請輸入關鍵字');
    if (mb_strlen($q) > 100) fail(400, '關鍵字太長');
    $type = Req::query('type', 'all');
    if (!in_array($type, ['all', 'video', 'doc', 'folder'], true)) $type = 'all';
    $scope = array_filter(explode(',', (string)Req::query('scope', 'name,tag')));
    $scope = array_values(array_intersect($scope, ['name', 'tag', 'subtitle'])) ?: ['name', 'tag'];
    $limit = 200;
    $like = like_pattern($q);
    $results = [];

    if (in_array($type, ['all', 'folder'], true) && in_array('name', $scope, true)) {
        [$vis, $vp] = vis_sql($a, 'COALESCE(f.course_id, f.id)');
        foreach (q_all("SELECT f.id, f.name, f.display_name, f.parent_id FROM folders f
                         WHERE f.status = 'present' AND $vis AND (f.name ILIKE ? OR f.display_name ILIKE ?)
                         ORDER BY f.rel_path LIMIT " . ($limit + 1), array_merge($vp, [$like, $like])) as $f) {
            $results[] = ['type' => 'folder', 'id' => (int)$f['id'], 'name' => folder_label($f),
                'path' => breadcrumb_text($f['parent_id'] !== null ? (int)$f['parent_id'] : null)];
        }
    }
    if ($type !== 'folder' && (in_array('name', $scope, true) || in_array('tag', $scope, true))) {
        [$vis, $vp] = vis_sql($a, 'm.course_id');
        $conds = [];
        $params = $vp;
        if (in_array('name', $scope, true)) {
            $conds[] = '(m.title ILIKE ? OR m.file_name ILIKE ?)';
            array_push($params, $like, $like);
        }
        if (in_array('tag', $scope, true)) {
            $conds[] = 'EXISTS (SELECT 1 FROM unnest(m.tag_names) t(n) WHERE t.n ILIKE ?)';
            $params[] = $like;
        }
        $kindCond = match ($type) { 'video' => "AND m.kind = 'video'", 'doc' => "AND m.kind <> 'video'", default => '' };
        $rows = q_all("SELECT m.*, to_json(m.tag_names)::text AS tags_json, NULL AS clip_name, NULL AS clip_source, 0 AS clip_count,
                              EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id) AS has_subtitle
                         FROM media m WHERE $vis $kindCond AND (" . implode(' OR ', $conds) . ")
                        ORDER BY m.title LIMIT " . ($limit + 1), $params);
        foreach ($rows as $m) {
            $results[] = ['type' => 'media', 'path' => breadcrumb_text($m['folder_id'] !== null ? (int)$m['folder_id'] : null)]
                + media_out($m, $a);
        }
    }
    if ($type !== 'folder' && $type !== 'doc' && in_array('subtitle', $scope, true)) {
        [$vis, $vp] = vis_sql($a, 'm.course_id');
        $rows = q_all("SELECT c.media_id, c.start_ms, c.text, m.title, m.folder_id FROM subtitle_cues c JOIN media m ON m.id = c.media_id
                        WHERE $vis AND c.text ILIKE ? ORDER BY m.title, c.start_ms LIMIT " . ($limit + 1), array_merge($vp, [$like]));
        foreach ($rows as $r) {
            $results[] = ['type' => 'subtitle', 'id' => (int)$r['media_id'], 'title' => $r['title'], 'start' => (int)$r['start_ms'] / 1000,
                'text' => mb_substr($r['text'], 0, 200), 'path' => breadcrumb_text($r['folder_id'] !== null ? (int)$r['folder_id'] : null)];
        }
    }
    $truncated = count($results) > $limit;
    $indexed = (int)q_val("SELECT COUNT(*) FROM scan_runs WHERE status = 'done' AND NOT dry_run") > 0;
    return ['results' => array_slice($results, 0, $limit), 'truncated' => $truncated, 'indexed' => $indexed];
});
