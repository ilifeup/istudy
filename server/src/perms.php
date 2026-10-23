<?php
declare(strict_types=1);

/**
 * 課程權限（第 05 章）：權限單位是頂層課程資料夾；admin、super 看全部。
 * 每次讀取教材、搜尋、書籤、歷程、字幕都依目前權限重新判斷。
 */

/** 可見課程 ID（同一請求內快取）。 */
function visible_courses(array $a): array
{
    static $cache = [];
    $key = $a['id'] . ':' . $a['role'];
    if (!isset($cache[$key])) {
        $rows = q_all('SELECT visible_course_ids(?, ?) AS id', [(int)$a['id'], $a['role']]);
        $cache[$key] = array_map(static fn($r) => (int)$r['id'], $rows);
    }
    return $cache[$key];
}

/**
 * SQL 條件：$col 所屬課程可見。根目錄底下（不屬於任何課程）的教材只有 admin、super 看得到。
 * 回傳 [sql, params]。
 */
function vis_sql(array $a, string $col): array
{
    return [
        "($col IN (SELECT visible_course_ids(?, ?)) OR ($col IS NULL AND ? IN ('admin','super')))",
        [(int)$a['id'], $a['role'], $a['role']],
    ];
}

function course_name(?int $courseId): string
{
    if (!$courseId) return '';
    $r = q_one("SELECT COALESCE(NULLIF(display_name,''), name) AS n FROM folders WHERE id = ?", [$courseId]);
    return $r ? (string)$r['n'] : '';
}

function folder_label(array $f): string
{
    $d = trim((string)($f['display_name'] ?? ''));
    return $d !== '' ? $d : (string)$f['name'];
}

/** 由資料夾 ID 建立麵包屑 [{id,name}]（從課程到該資料夾）。 */
function folder_breadcrumbs(?int $folderId): array
{
    if (!$folderId) return [];
    $rows = q_all(
        "WITH RECURSIVE chain AS (
            SELECT id, parent_id, name, display_name, 0 AS depth FROM folders WHERE id = ?
            UNION ALL
            SELECT f.id, f.parent_id, f.name, f.display_name, c.depth + 1 FROM folders f JOIN chain c ON f.id = c.parent_id
         ) SELECT id, name, display_name FROM chain ORDER BY depth DESC", [$folderId]);
    return array_map(static fn($r) => ['id' => (int)$r['id'], 'name' => folder_label($r)], $rows);
}

function breadcrumb_text(?int $folderId): string
{
    return implode(' / ', array_column(folder_breadcrumbs($folderId), 'name'));
}

/** 資料夾可見性：課程可見即可（子資料夾繼承課程權限）。 */
function folder_for_actor(array $a, int $folderId): array
{
    $f = q_one('SELECT * FROM folders WHERE id = ?', [$folderId]);
    if (!$f || $f['status'] !== 'present') fail(404, '找不到此資料夾');
    $courseId = (int)($f['course_id'] ?: $f['id']);
    if (!in_array($courseId, visible_courses($a), true)) {
        fail(403, '尚未取得閱覽權限', ['code' => 'no_access', 'course_name' => course_name($courseId)]);
    }
    return $f;
}

function media_row(int $id): ?array
{
    return q_one(
        "SELECT m.*, to_json(m.tag_names)::text AS tags_json,
                cg.name AS clip_name, cg.source AS clip_source,
                (SELECT COUNT(*) FROM media x WHERE x.clip_group_id = m.clip_group_id) AS clip_count,
                EXISTS (SELECT 1 FROM subtitles s WHERE s.media_id = m.id) AS has_subtitle
           FROM media m LEFT JOIN clip_groups cg ON cg.id = m.clip_group_id
          WHERE m.id = ?", [$id]);
}

/** 取得有權限的教材；無權限回 403（附課程名稱，供「🔒 尚未取得閱覽權限」畫面）。 */
function media_for_actor(array $a, int $id): array
{
    $m = media_row($id);
    if (!$m) fail(404, '找不到此教材');
    $courseId = $m['course_id'] !== null ? (int)$m['course_id'] : null;
    $ok = $courseId === null ? role_at_least($a, 'admin') : in_array($courseId, visible_courses($a), true);
    if (!$ok) fail(403, '尚未取得閱覽權限', ['code' => 'no_access', 'course_name' => course_name($courseId)]);
    return $m;
}

/**
 * 教材可用狀態（畫面依此顯示文字與符號）：
 * ready、converting（⏳ 轉檔中）、doc_converting（⏳ 文件轉換中）、missing（⚠ 教材目前不可用）、failed（⚠ 轉檔失敗）。
 */
function media_state(array $m): string
{
    if ($m['status'] === 'missing') return 'missing';
    return match ($m['kind']) {
        'video' => match (true) {
            $m['play_mode'] === 'direct' && $m['status'] === 'present' => 'ready',
            $m['convert_status'] === 'done' && !empty($m['playback_path']) => 'ready',
            in_array($m['convert_status'], ['failed', 'verify_failed'], true) => 'failed',
            $m['status'] === 'original_deleted' => 'missing',
            default => 'converting',
        },
        'office' => match ($m['doc_status']) {
            'done' => 'ready',
            'failed' => 'failed',
            default => 'doc_converting',
        },
        default => $m['status'] === 'present' ? 'ready' : 'missing',
    };
}

function clip_info(array $m): ?array
{
    if (empty($m['clip_group_id']) || ($m['clip_name'] ?? null) === null) return null;
    return [
        'group_id' => (int)$m['clip_group_id'],
        'name' => (string)$m['clip_name'],
        'seq' => (int)$m['clip_seq'],
        'count' => (int)$m['clip_count'],
        'source' => (string)$m['clip_source'],
    ];
}

/** 教材的通用輸出格式。$extra 可附學習狀態等個人資料。 */
function media_out(array $m, array $a, array $extra = []): array
{
    $out = [
        'id' => (int)$m['id'],
        'title' => (string)$m['title'],
        'file_name' => (string)$m['file_name'],
        'kind' => (string)$m['kind'],
        'ext' => (string)$m['ext'],
        'size' => (int)$m['size_bytes'],
        'duration' => $m['duration_sec'] !== null ? (float)$m['duration_sec'] : null,
        'status' => (string)$m['status'],
        'state' => media_state($m),
        'folder_id' => $m['folder_id'] !== null ? (int)$m['folder_id'] : null,
        'course_id' => $m['course_id'] !== null ? (int)$m['course_id'] : null,
        'tags' => jdec($m['tags_json'] ?? null, []),
        'clip' => clip_info($m),
        'has_subtitle' => (bool)($m['has_subtitle'] ?? false),
    ];
    if (role_at_least($a, 'admin')) {
        $out['convert_status'] = (string)$m['convert_status'];
        $out['doc_status'] = (string)$m['doc_status'];
        $out['play_mode'] = $m['play_mode'];
    }
    return $out + $extra;
}

function like_pattern(string $s): string
{
    return '%' . str_replace(['\\', '%', '_'], ['\\\\', '\\%', '\\_'], $s) . '%';
}
