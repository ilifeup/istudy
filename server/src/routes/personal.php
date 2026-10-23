<?php
declare(strict_types=1);

/** 共用 Tag、Tag 提醒策略、書籤與最愛、學習歷程（第 08 章）。 */

route('GET', '/api/media/{id}/tags', static function (array $p): array {
    $m = media_for_actor(actor(), $p['id']);
    $tags = jdec($m['tags_json'], []);
    return ['tags' => array_slice($tags, 0, 100)];
});

route('POST|PATCH|DELETE', '/api/media/{id}/tags', static function (array $p): array {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    $method = Req::$method;
    $d = Req::body();
    $tag = valid_tag_input($d['tag'] ?? null);
    if ($tag === null) fail(400, 'Tag 必須是 1～60 個非空白字元');
    $newTag = '';
    if ($method === 'PATCH') {
        $newTag = valid_tag_input($d['new_tag'] ?? null) ?? fail(400, '新 Tag 必須是 1～60 個非空白字元');
    }
    $names = tx(static function () use ($m, $method, $tag, $newTag) {
        $current = jdec((string)q_val('SELECT to_json(tag_names)::text FROM media WHERE id = ? FOR UPDATE', [$m['id']]), []);
        try {
            $names = update_tag_names($current, $method, $tag, $newTag);
        } catch (InvalidArgumentException) {
            fail(404, '找不到這個 Tag（可能已被其他人修改）', ['code' => 'tag_not_found']);
        }
        if (count($names) > 100) fail(400, '每份教材最多 100 個 Tag');
        q('UPDATE media SET tag_names = ARRAY(SELECT jsonb_array_elements_text(CAST(? AS jsonb))), updated_at = NOW() WHERE id = ?',
            [jenc($names), $m['id']]);
        return $names;
    });
    $action = ['POST' => 'tag.added', 'PATCH' => 'tag.edited', 'DELETE' => 'tag.removed'][$method];
    audit($a, $action, array_filter(['tag' => $tag, 'new_tag' => $newTag ?: null]), (int)$m['id'], (string)$m['rel_path']);
    return ['tags' => $names];
});

route('GET', '/api/tag-policy', static function (): array {
    actor();
    $rows = q_all('SELECT role, show_warning FROM tag_role_policy ORDER BY role');
    return ['policy' => array_map(static fn($r) => ['role' => $r['role'], 'label' => $r['role'], 'show_warning' => (bool)$r['show_warning']], $rows)];
});

route('PUT', '/api/tag-policy', static function (): array {
    $a = require_role('super');
    $d = Req::body();
    $role = valid_role($d['role'] ?? null);
    $show = body_bool($d, 'show_warning');
    if ($role === null || $show === null) fail(400, '設定格式錯誤');
    q('INSERT INTO tag_role_policy(role, show_warning) VALUES (?, ?) ON CONFLICT (role) DO UPDATE SET show_warning = EXCLUDED.show_warning',
        [$role, $show]);
    audit($a, 'tag.policy.updated', ['role' => $role, 'show_warning' => $show]);
    return ['ok' => true];
});

// ---------- 書籤與最愛 ----------

route('GET', '/api/bookmarks', static function (): array {
    $a = actor();
    [$vis, $vp] = vis_sql($a, 'm.course_id');
    $conds = ['b.account_id = ?'];
    $params = array_merge($vp, [$a['id']]);
    if (Req::query('favorite') === '1') $conds[] = 'b.is_favorite';
    $type = Req::query('type');
    if ($type === 'video') $conds[] = "m.kind = 'video'";
    if ($type === 'doc') $conds[] = "m.kind <> 'video'";
    $tag = Req::query('tag');
    if ($tag !== null && $tag !== '') {
        $conds[] = '? = ANY(m.tag_names)';
        $params[] = $tag;
    }
    $rows = q_all("SELECT m.*, to_json(m.tag_names)::text AS tags_json, NULL AS clip_name, NULL AS clip_source, 0 AS clip_count,
                          FALSE AS has_subtitle, b.is_favorite, b.is_bookmarked, b.updated_at AS bookmarked_at, $vis AS has_access
                     FROM bookmarks b JOIN media m ON m.id = b.media_id
                    WHERE " . implode(' AND ', $conds) . ' ORDER BY b.updated_at DESC LIMIT 1000', $params);
    $tags = [];
    $items = [];
    foreach ($rows as $r) {
        $ok = (bool)$r['has_access'];
        $item = media_out($r, $a, [
            'is_favorite' => (bool)$r['is_favorite'],
            'is_bookmarked' => (bool)$r['is_bookmarked'],
            'bookmarked_at' => ts($r['bookmarked_at']),
            'has_access' => $ok,
            'path' => $ok ? breadcrumb_text($r['folder_id'] !== null ? (int)$r['folder_id'] : null) : '',
        ]);
        if (!$ok) $item['tags'] = [];
        foreach ($item['tags'] as $t) $tags[$t] = true;
        $items[] = $item;
    }
    $tagList = array_keys($tags);
    sort($tagList, SORT_STRING);
    return ['items' => $items, 'tags' => $tagList];
});

route('PUT', '/api/media/{id}/bookmark', static function (array $p): array {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    $d = Req::body();
    $fav = body_bool($d, 'is_favorite');
    $bm = body_bool($d, 'is_bookmarked');
    if ($fav === null && $bm === null) fail(400, '請指定 is_favorite 或 is_bookmarked');
    $cur = q_one('SELECT is_bookmarked, is_favorite FROM bookmarks WHERE account_id = ? AND media_id = ?', [$a['id'], $m['id']]);
    $isBm = $cur ? (bool)$cur['is_bookmarked'] : false;
    $isFav = $cur ? (bool)$cur['is_favorite'] : false;
    if ($bm === false) {
        // 移除書籤時連最愛一起移除
        $isBm = false;
        $isFav = false;
    } elseif ($bm === true) {
        $isBm = true;
    }
    if ($fav !== null) {
        $isFav = $fav;
        if ($fav) $isBm = true; // 最愛一定也是書籤；取消最愛不刪除書籤
    }
    if (!$isBm && !$isFav) {
        q('DELETE FROM bookmarks WHERE account_id = ? AND media_id = ?', [$a['id'], $m['id']]);
    } else {
        q('INSERT INTO bookmarks(account_id, media_id, is_bookmarked, is_favorite) VALUES (?,?,?,?)
           ON CONFLICT (account_id, media_id) DO UPDATE SET is_bookmarked = EXCLUDED.is_bookmarked,
             is_favorite = EXCLUDED.is_favorite, updated_at = NOW()', [$a['id'], $m['id'], $isBm, $isFav]);
    }
    return ['is_bookmarked' => $isBm, 'is_favorite' => $isFav];
});

// ---------- 學習歷程 ----------

route('GET', '/api/history', static function (): array {
    $a = actor();
    [$vis, $vp] = vis_sql($a, 'm.course_id');
    $rows = q_all("SELECT m.*, to_json(m.tag_names)::text AS tags_json, NULL AS clip_name, NULL AS clip_source, 0 AS clip_count,
                          FALSE AS has_subtitle, h.last_viewed_at, h.last_position_sec, h.last_page, h.learning_status,
                          $vis AS has_access
                     FROM view_history h JOIN media m ON m.id = h.media_id
                    WHERE h.account_id = ? ORDER BY h.last_viewed_at DESC LIMIT 500", array_merge($vp, [$a['id']]));
    return ['items' => array_map(static function ($r) use ($a) {
        $ok = (bool)$r['has_access'];
        return media_out($r, $a, [
            'last_viewed_at' => ts($r['last_viewed_at']),
            'last_position_sec' => $r['last_position_sec'] !== null ? (int)$r['last_position_sec'] : null,
            'last_page' => $r['last_page'] !== null ? (int)$r['last_page'] : null,
            'learning_status' => $r['learning_status'] ?? 'not_started',
            'has_access' => $ok,
            'path' => $ok ? breadcrumb_text($r['folder_id'] !== null ? (int)$r['folder_id'] : null) : '',
        ]);
    }, $rows)];
});

/**
 * 記錄瀏覽與位置：opened=true 為開啟教材（記錄一次並寫入使用記錄）；
 * 之後只更新位置（每 30 分鐘、登出時），不計入瀏覽次數。
 */
route('PUT', '/api/media/{id}/progress', static function (array $p): array {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    $d = Req::body();
    $opened = body_bool($d, 'opened') === true;
    $pos = body_int($d, 'position_sec', 0, 1000000);
    $page = body_int($d, 'page', 1, 100000);
    q('INSERT INTO view_history(account_id, media_id, last_viewed_at, last_position_sec, last_page)
       VALUES (?, ?, NOW(), ?, ?)
       ON CONFLICT (account_id, media_id) DO UPDATE SET
         last_viewed_at = CASE WHEN ? THEN NOW() ELSE view_history.last_viewed_at END,
         last_position_sec = COALESCE(EXCLUDED.last_position_sec, view_history.last_position_sec),
         last_page = COALESCE(EXCLUDED.last_page, view_history.last_page)',
        [$a['id'], $m['id'], $pos, $page, $opened]);
    if ($opened) {
        audit($a, $m['kind'] === 'video' ? 'media.open_video' : 'media.open_document', ['title' => $m['title']], (int)$m['id'], (string)$m['rel_path']);
    }
    return ['ok' => true];
});

route('PUT', '/api/media/{id}/status', static function (array $p): array {
    $a = actor();
    $m = media_for_actor($a, $p['id']);
    $d = Req::body();
    $status = $d['status'] ?? null;
    if (!in_array($status, ['not_started', 'in_progress', 'completed'], true)) fail(400, '學習狀態只能是未開始、學習中、已完成');
    q('INSERT INTO view_history(account_id, media_id, learning_status) VALUES (?,?,?)
       ON CONFLICT (account_id, media_id) DO UPDATE SET learning_status = EXCLUDED.learning_status',
        [$a['id'], $m['id'], $status]);
    return ['learning_status' => $status];
});
