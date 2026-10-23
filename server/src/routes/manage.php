<?php
declare(strict_types=1);

/** Client 內的管理功能（第 09 章）：帳號、備註、課程群組、個人覆寫、使用記錄、WOL、待登記設備、PC 資訊。 */

// ---------- 帳號管理 ----------

/**
 * 誰可以編輯誰（dev.55：權限只有 user／admin／super）：admin 編輯 user；super 編輯 user、admin。
 * 唯一的 Super 在清單中唯讀顯示，任何人都不能經由帳號管理修改（含權限）。
 */
function can_edit_account(array $a, array $target): bool
{
    if ($target['role'] === 'super') return false;
    if (is_super($a)) return true;
    return $a['role'] === 'admin' && $target['role'] === 'user';
}

/** 可指派的權限角色：super 永遠不在其中（ROLE-04）。 */
function assignable_roles(array $a): array
{
    return match ($a['role']) {
        'super' => ['user', 'admin'],
        'admin' => ['user'],
        default => [],
    };
}

/** 權限（role）：只接受可指派的角色；任何要求變成 super 的一律 403。 */
function requested_role(array $a, array $d): ?string
{
    if (!array_key_exists('role', $d)) return null;
    $role = $d['role'];
    if ($role === 'super' || !is_string($role) || !in_array($role, assignable_roles($a), true)) fail(403, '不能設定為此權限');
    return $role;
}

/** 稱謂（title_id）：只接受稱謂表內的值；與權限無關，修改稱謂不會改變權限。 */
function requested_title(array $d): ?string
{
    if (!array_key_exists('title_id', $d)) return null;
    $t = $d['title_id'];
    if (!is_string($t) || !isset(account_titles()[$t])) fail(400, '稱謂不存在');
    return $t;
}

function account_target(array $a, int $id): array
{
    $t = q_one('SELECT * FROM accounts WHERE id = ?', [$id]);
    if (!$t || $t['role'] === 'super') fail(404, '找不到帳號');
    return $t;
}

function account_out(array $u, bool $withGroups = true): array
{
    $out = [
        'id' => (int)$u['id'],
        'username' => $u['username'],
        'role' => $u['role'],
        'role_label' => $u['role'],
        'title_id' => $u['title_id'] ?? null,
        'title' => title_label($u),
        'full_name' => $u['full_name'],
        'phone' => $u['phone'],
        'birth_date' => $u['birth_date'],
        'start_year' => $u['start_year'] !== null ? (int)$u['start_year'] : null,
        'address' => $u['address'],
        'class_time' => $u['class_time'],
        'bio' => $u['bio'],
        'active' => (bool)$u['active'],
        'created_at' => ts($u['created_at']),
    ];
    if ($withGroups) {
        $out['groups'] = array_map(static fn($g) => ['id' => (int)$g['id'], 'name' => $g['name']],
            q_all('SELECT g.id, g.name FROM course_group_members m JOIN course_groups g ON g.id = m.group_id WHERE m.account_id = ? ORDER BY g.name', [$u['id']]));
    }
    return $out;
}

function set_account_groups(int $accountId, array $groupIds): void
{
    q('DELETE FROM course_group_members WHERE account_id = ?', [$accountId]);
    foreach ($groupIds as $gid) {
        if (!q_val('SELECT 1 FROM course_groups WHERE id = ?', [$gid])) fail(400, '課程群組不存在：' . $gid);
        q('INSERT INTO course_group_members(group_id, account_id) VALUES (?,?) ON CONFLICT DO NOTHING', [$gid, $accountId]);
    }
}

route('GET', '/api/accounts', static function (): array {
    $a = require_role('admin');
    $q = trim((string)Req::query('q', ''));
    $params = [];
    // dev.55 ROLE-01：清單包含 Super（唯一、唯讀、排第一）
    $where = 'TRUE';
    if ($q !== '') {
        $where .= ' AND (username ILIKE ? OR full_name ILIKE ?)';
        $params = [like_pattern($q), like_pattern($q)];
    }
    $rows = q_all("SELECT * FROM accounts WHERE $where ORDER BY (role = 'super') DESC, active DESC, full_name, username LIMIT 2000", $params);
    return [
        'accounts' => array_map(static fn($u) => account_out($u) + ['editable' => can_edit_account($a, $u)], $rows),
        'assignable_roles' => assignable_roles($a),
        'titles' => array_values(account_titles()),
    ];
});

route('POST', '/api/accounts', static function (): array {
    $a = require_role('admin');
    $d = Req::body();
    $username = valid_username(username_nfc($d['username'] ?? null)) ?? fail(400, USERNAME_RULE_TEXT);
    $password = valid_password($d['password'] ?? null) ?? fail(400, '初始密碼至少 8 碼');
    $role = requested_role($a, $d) ?? 'user';
    $titleId = requested_title($d) ?? 'assistant';
    $set = profile_updates($d);
    $groupIds = array_key_exists('group_ids', $d) ? body_ids($d, 'group_ids') : [];
    if ($groupIds && !role_at_least($a, 'admin')) fail(403, '只有管理員可以設定課程群組');
    $id = tx(static function () use ($username, $password, $role, $titleId, $set, $groupIds) {
        $id = (int)q_val('INSERT INTO accounts(username, password_hash, role, title_id) VALUES (?,?,?,?) RETURNING id',
            [$username, password_hash($password, PASSWORD_DEFAULT), $role, $titleId]);
        apply_account_update($id, $set);
        if ($groupIds) set_account_groups($id, $groupIds);
        return $id;
    });
    audit($a, 'account.created', ['account_id' => $id, 'username' => $username, 'role' => $role, 'title_id' => $titleId]);
    return ['account' => account_out(q_one('SELECT * FROM accounts WHERE id = ?', [$id]))];
});

route('PATCH', '/api/accounts/{id}', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    if (!can_edit_account($a, $t)) fail(403, '不能編輯此帳號');
    $d = Req::body();
    $set = profile_updates($d);
    // 權限與稱謂分開：只改送來的那一項
    $role = requested_role($a, $d);
    if ($role !== null) $set['role'] = $role;
    $title = requested_title($d);
    if ($title !== null) $set['title_id'] = $title;
    $deactivate = false;
    if (array_key_exists('active', $d)) {
        $active = body_bool($d, 'active');
        $set['active'] = $active;
        $deactivate = $active === false && $t['active'];
    }
    tx(static function () use ($t, $set, $d, $deactivate) {
        apply_account_update((int)$t['id'], $set);
        if (array_key_exists('group_ids', $d)) set_account_groups((int)$t['id'], body_ids($d, 'group_ids'));
        if ($deactivate) revoke_all_sessions((int)$t['id']); // 停用立即使 Session 失效
    });
    audit($a, 'account.updated', ['account_id' => (int)$t['id'], 'fields' => array_keys($set), 'groups_changed' => array_key_exists('group_ids', $d)]);
    return ['account' => account_out(q_one('SELECT * FROM accounts WHERE id = ?', [$t['id']]))];
});

route('DELETE', '/api/accounts/{id}', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    if (!can_edit_account($a, $t)) fail(403, '不能刪除此帳號');
    $d = Req::body();
    if (($d['confirm_username'] ?? null) !== $t['username']) fail(400, '請輸入帳號名稱確認刪除', ['code' => 'confirm_required']);
    // 使用記錄保留帳號 ID 與姓名快照（audit_log.actor_id 設為 NULL，姓名快照保留）
    q('UPDATE audit_log SET details = details || jsonb_build_object(\'deleted_account_id\', actor_id) WHERE actor_id = ?', [$t['id']]);
    q('DELETE FROM accounts WHERE id = ?', [$t['id']]);
    audit($a, 'account.deleted', ['account_id' => (int)$t['id'], 'username' => $t['username'], 'full_name' => $t['full_name']]);
    return ['ok' => true];
});

route('POST', '/api/accounts/{id}/password', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    if (!can_edit_account($a, $t)) fail(403, '不能重設此帳號的密碼');
    $pw = valid_password(Req::body()['password'] ?? null) ?? fail(400, '新密碼至少 8 碼');
    q('UPDATE accounts SET password_hash = ?, updated_at = NOW() WHERE id = ?', [password_hash($pw, PASSWORD_DEFAULT), $t['id']]);
    revoke_all_sessions((int)$t['id']);
    audit($a, 'account.password_reset', ['account_id' => (int)$t['id']]);
    return ['ok' => true];
});

// ---------- 帳號備註（追加式） ----------

function note_out(array $n): array
{
    return ['id' => (int)$n['id'], 'body' => $n['body'], 'created_by_name' => $n['created_by_name'],
        'created_at' => ts($n['created_at']), 'updated_at' => ts($n['updated_at'])];
}

route('GET', '/api/accounts/{id}/notes', static function (array $p): array {
    $a = require_role('admin');
    account_target($a, $p['id']);
    return ['notes' => array_map('note_out', q_all('SELECT * FROM account_notes WHERE account_id = ? ORDER BY created_at, id', [$p['id']])),
        'can_edit' => role_at_least($a, 'admin')];
});

route('POST', '/api/accounts/{id}/notes', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    $body = body_str(Req::body(), 'body', 2000) ?? '';
    if ($body === '') fail(400, '備註內容不可空白');
    $id = (int)q_val('INSERT INTO account_notes(account_id, body, created_by, created_by_name) VALUES (?,?,?,?) RETURNING id',
        [$t['id'], $body, $a['id'], display_name($a)]);
    audit($a, 'account.note_added', ['account_id' => (int)$t['id'], 'note_id' => $id]);
    return ['note' => note_out(q_one('SELECT * FROM account_notes WHERE id = ?', [$id]))];
});

route('PATCH|DELETE', '/api/accounts/{id}/notes/{nid}', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    $n = q_one('SELECT * FROM account_notes WHERE id = ? AND account_id = ?', [$p['nid'], $t['id']]) ?? fail(404, '找不到備註');
    if (Req::$method === 'DELETE') {
        q('DELETE FROM account_notes WHERE id = ?', [$n['id']]);
        audit($a, 'account.note_deleted', ['account_id' => (int)$t['id'], 'note_id' => (int)$n['id']]);
        return ['ok' => true];
    }
    $body = body_str(Req::body(), 'body', 2000) ?? '';
    if ($body === '') fail(400, '備註內容不可空白');
    q('UPDATE account_notes SET body = ?, updated_by = ?, updated_at = NOW() WHERE id = ?', [$body, $a['id'], $n['id']]);
    audit($a, 'account.note_edited', ['account_id' => (int)$t['id'], 'note_id' => (int)$n['id']]);
    return ['note' => note_out(q_one('SELECT * FROM account_notes WHERE id = ?', [$n['id']]))];
});

// ---------- 課程與群組 ----------

function course_folders(): array
{
    $rows = q_all("SELECT id, name, display_name, sort_order, enabled FROM folders
                    WHERE parent_id IS NULL AND is_course AND status = 'present'");
    $rows = array_map(static fn($r) => ['id' => (int)$r['id'], 'name' => folder_label($r), 'sort_order' => (int)$r['sort_order'], 'enabled' => (bool)$r['enabled']], $rows);
    usort($rows, static fn($x, $y) => $x['sort_order'] <=> $y['sort_order'] ?: natural_compare($x['name'], $y['name']));
    return $rows;
}

route('GET', '/api/courses', static function (): array {
    require_role('admin');
    return ['courses' => course_folders()];
});

function group_out(array $g): array
{
    $members = q_all("SELECT a.id, a.username, a.full_name FROM course_group_members m JOIN accounts a ON a.id = m.account_id
                       WHERE m.group_id = ? AND a.role <> 'super' ORDER BY a.full_name, a.username", [$g['id']]);
    $courses = array_map('intval', array_column(q_all('SELECT folder_id FROM group_course_permissions WHERE group_id = ?', [$g['id']]), 'folder_id'));
    return ['id' => (int)$g['id'], 'name' => $g['name'], 'course_ids' => $courses,
        'members' => array_map(static fn($m) => ['id' => (int)$m['id'], 'username' => $m['username'], 'full_name' => $m['full_name']], $members)];
}

route('GET', '/api/groups', static function (): array {
    require_role('admin');
    $groups = q_all('SELECT * FROM course_groups');
    usort($groups, static fn($x, $y) => natural_compare($x['name'], $y['name']));
    return ['groups' => array_map('group_out', $groups), 'courses' => course_folders()];
});

route('POST', '/api/groups', static function (): array {
    $a = require_role('admin');
    $name = body_str(Req::body(), 'name', 60) ?? '';
    if ($name === '') fail(400, '請輸入群組名稱');
    $id = (int)q_val('INSERT INTO course_groups(name) VALUES (?) RETURNING id', [$name]);
    audit($a, 'group.created', ['group_id' => $id, 'name' => $name]);
    return ['group' => group_out(q_one('SELECT * FROM course_groups WHERE id = ?', [$id]))];
});

route('PATCH|DELETE', '/api/groups/{id}', static function (array $p): array {
    $a = require_role('admin');
    $g = q_one('SELECT * FROM course_groups WHERE id = ?', [$p['id']]) ?? fail(404, '找不到群組');
    if (Req::$method === 'DELETE') {
        $count = (int)q_val('SELECT COUNT(*) FROM course_group_members WHERE group_id = ?', [$g['id']]);
        if ((Req::body()['confirm'] ?? false) !== true) {
            fail(409, "此群組有 $count 位成員，刪除後他們會失去此群組的課程權限", ['code' => 'confirm_required', 'member_count' => $count]);
        }
        q('DELETE FROM course_groups WHERE id = ?', [$g['id']]);
        audit($a, 'group.deleted', ['group_id' => (int)$g['id'], 'name' => $g['name'], 'member_count' => $count]);
        return ['ok' => true];
    }
    $name = body_str(Req::body(), 'name', 60) ?? '';
    if ($name === '') fail(400, '請輸入群組名稱');
    q('UPDATE course_groups SET name = ? WHERE id = ?', [$name, $g['id']]);
    audit($a, 'group.renamed', ['group_id' => (int)$g['id'], 'name' => $name]);
    return ['group' => group_out(q_one('SELECT * FROM course_groups WHERE id = ?', [$g['id']]))];
});

route('PUT', '/api/groups/{id}/courses', static function (array $p): array {
    $a = require_role('admin');
    $g = q_one('SELECT * FROM course_groups WHERE id = ?', [$p['id']]) ?? fail(404, '找不到群組');
    $ids = body_ids(Req::body(), 'course_ids');
    tx(static function () use ($g, $ids) {
        q('DELETE FROM group_course_permissions WHERE group_id = ?', [$g['id']]);
        foreach ($ids as $fid) {
            if (!q_val('SELECT 1 FROM folders WHERE id = ? AND parent_id IS NULL AND is_course', [$fid])) fail(400, '只能設定課程資料夾');
            q('INSERT INTO group_course_permissions(group_id, folder_id) VALUES (?,?)', [$g['id'], $fid]);
        }
    });
    audit($a, 'group.courses_updated', ['group_id' => (int)$g['id'], 'course_ids' => $ids]);
    return ['group' => group_out($g)];
});

route('POST', '/api/groups/{id}/members', static function (array $p): array {
    $a = require_role('admin');
    $g = q_one('SELECT * FROM course_groups WHERE id = ?', [$p['id']]) ?? fail(404, '找不到群組');
    $ids = body_ids(Req::body(), 'account_ids', 500);
    foreach ($ids as $aid) {
        account_target($a, $aid);
        q('INSERT INTO course_group_members(group_id, account_id) VALUES (?,?) ON CONFLICT DO NOTHING', [$g['id'], $aid]);
    }
    audit($a, 'group.members_added', ['group_id' => (int)$g['id'], 'account_ids' => $ids]);
    return ['group' => group_out($g)];
});

route('DELETE', '/api/groups/{id}/members/{aid}', static function (array $p): array {
    $a = require_role('admin');
    $g = q_one('SELECT * FROM course_groups WHERE id = ?', [$p['id']]) ?? fail(404, '找不到群組');
    q('DELETE FROM course_group_members WHERE group_id = ? AND account_id = ?', [$g['id'], $p['aid']]);
    audit($a, 'group.member_removed', ['group_id' => (int)$g['id'], 'account_id' => $p['aid']]);
    return ['group' => group_out($g)];
});

// ---------- 個人覆寫：群組結果、個人覆寫、最後結果 ----------

function overrides_out(int $accountId): array
{
    $role = (string)q_val('SELECT role FROM accounts WHERE id = ?', [$accountId]);
    $rows = [];
    foreach (course_folders() as $c) {
        $group = (bool)q_val('SELECT EXISTS (SELECT 1 FROM group_course_permissions g JOIN course_group_members m ON m.group_id = g.group_id
                               WHERE m.account_id = ? AND g.folder_id = ?)', [$accountId, $c['id']]);
        $mode = q_val('SELECT mode FROM account_course_overrides WHERE account_id = ? AND folder_id = ?', [$accountId, $c['id']]);
        $final = in_array($role, ['admin', 'super'], true) ? true : ($mode === 'allow' ? true : ($mode === 'deny' ? false : $group));
        $rows[] = ['course_id' => $c['id'], 'course_name' => $c['name'], 'group_result' => $group, 'override' => $mode, 'final' => $final,
            'reason' => in_array($role, ['admin', 'super'], true) ? 'role' : ($mode === 'allow' ? 'personal_allow' : ($mode === 'deny' ? 'personal_deny' : 'group'))];
    }
    return $rows;
}

route('GET', '/api/accounts/{id}/overrides', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    return ['account_id' => (int)$t['id'], 'rows' => overrides_out((int)$t['id'])];
});

route('PUT', '/api/accounts/{id}/overrides', static function (array $p): array {
    $a = require_role('admin');
    $t = account_target($a, $p['id']);
    $d = Req::body();
    tx(static function () use ($t, $d) {
        if (($d['clear'] ?? false) === true) {
            q('DELETE FROM account_course_overrides WHERE account_id = ?', [$t['id']]); // 套用群組權限
            return;
        }
        $items = is_array($d['overrides'] ?? null) ? $d['overrides'] : fail(400, '格式錯誤');
        foreach ($items as $it) {
            $fid = is_array($it) ? body_int($it, 'folder_id', 1) : null;
            $mode = is_array($it) ? ($it['mode'] ?? null) : null;
            if (!$fid || !q_val('SELECT 1 FROM folders WHERE id = ? AND parent_id IS NULL AND is_course', [$fid])) fail(400, '只能覆寫課程資料夾');
            if ($mode === null || $mode === '') {
                q('DELETE FROM account_course_overrides WHERE account_id = ? AND folder_id = ?', [$t['id'], $fid]);
            } elseif (in_array($mode, ['allow', 'deny'], true)) {
                q('INSERT INTO account_course_overrides(account_id, folder_id, mode) VALUES (?,?,?)
                   ON CONFLICT (account_id, folder_id) DO UPDATE SET mode = EXCLUDED.mode', [$t['id'], $fid, $mode]);
            } else {
                fail(400, '覆寫只能是 allow、deny 或空白');
            }
        }
    });
    audit($a, 'account.overrides_updated', ['account_id' => (int)$t['id'], 'clear' => ($d['clear'] ?? false) === true]);
    return ['account_id' => (int)$t['id'], 'rows' => overrides_out((int)$t['id'])];
});

// ---------- 使用記錄（Admin 近 14 天、看不到 Super；Super 全部，可清除） ----------

function audit_query(array $a, int $defaultLimit): array
{
    $conds = [];
    $params = [];
    if (!is_super($a)) {
        $conds[] = "created_at > NOW() - INTERVAL '14 days'";
        $conds[] = "actor_role <> 'super'";
    }
    $acc = Req::query('account');
    if ($acc !== null && $acc !== '') {
        if (!preg_match('/^\d+$/', $acc)) fail(400, '帳號參數錯誤');
        $conds[] = '(actor_id = ? OR (details->>\'account_id\')::bigint = ?)';
        array_push($params, (int)$acc, (int)$acc);
    }
    $action = Req::query('action');
    if ($action !== null && $action !== '') {
        $conds[] = 'action LIKE ?';
        $params[] = str_replace(['%', '_'], ['\\%', '\\_'], $action) . '%';
    }
    foreach (['from' => '>=', 'to' => '<'] as $k => $op) {
        $v = Req::query($k);
        if ($v !== null && $v !== '') {
            if (valid_date($v) === null) fail(400, '日期格式錯誤（YYYY-MM-DD）');
            $conds[] = $k === 'to' ? "created_at < CAST(? AS date) + 1" : "created_at >= CAST(? AS date)";
            $params[] = $v;
        }
    }
    $limit = query_int('limit', $defaultLimit, 1, 5000);
    $where = $conds ? 'WHERE ' . implode(' AND ', $conds) : '';
    return [q_all("SELECT * FROM audit_log $where ORDER BY created_at DESC, id DESC LIMIT $limit", $params), $limit];
}

function audit_out(array $r): array
{
    return ['id' => (int)$r['id'], 'created_at' => ts($r['created_at']), 'actor_id' => $r['actor_id'] !== null ? (int)$r['actor_id'] : null,
        'actor_name' => $r['actor_name'], 'actor_role' => $r['actor_role'], 'actor_groups' => $r['actor_groups'],
        'action' => $r['action'], 'media_id' => $r['media_id'] !== null ? (int)$r['media_id'] : null, 'path' => $r['path'],
        'details' => jdec($r['details'], []), 'client_ip' => $r['client_ip'], 'client_name' => $r['client_name']];
}

route('GET', '/api/audit', static function (): array {
    $a = require_role('admin');
    [$rows] = audit_query($a, Req::query('account') ? 300 : 1000);
    return ['items' => array_map('audit_out', $rows), 'limited_to_days' => is_super($a) ? null : 14];
});

route('GET', '/api/audit/export', static function (): never {
    $a = require_import_export();
    [$rows] = audit_query($a, 5000);
    audit($a, 'audit.exported', ['rows' => count($rows)]);
    http_response_code(200);
    send_cors_headers();
    header('Content-Type: text/csv; charset=utf-8');
    header("Content-Disposition: attachment; filename*=UTF-8''" . rawurlencode('使用記錄_' . date('Ymd_His') . '.csv'));
    header('Cache-Control: no-store');
    $out = fopen('php://output', 'w');
    fwrite($out, "\xEF\xBB\xBF");
    fputcsv($out, ['時間', '帳號ID', '姓名', '角色', '群組', '動作', '教材ID', '路徑', '來源IP', '來源電腦', '內容'], ',', '"', '');
    foreach ($rows as $r) {
        $o = audit_out($r);
        fputcsv($out, [$o['created_at'], $o['actor_id'], $o['actor_name'], $o['actor_role'], $o['actor_groups'], $o['action'],
            $o['media_id'], $o['path'], $o['client_ip'], $o['client_name'], jenc($o['details'])], ',', '"', '');
    }
    fclose($out);
    exit;
});

route('DELETE', '/api/audit', static function (): array {
    $a = require_role('super');
    $d = Req::body();
    $before = $d['before'] ?? null;
    if ($before !== null && valid_date($before) === null) fail(400, '日期格式錯誤（YYYY-MM-DD）');
    if (($d['confirm'] ?? null) !== '清除') fail(400, '請輸入「清除」確認', ['code' => 'confirm_required']);
    $n = $before ? q('DELETE FROM audit_log WHERE created_at < CAST(? AS date)', [$before])->rowCount()
                 : q('DELETE FROM audit_log')->rowCount();
    audit($a, 'audit.cleared', ['before' => $before, 'rows' => $n]); // 清除本身也留下一筆紀錄
    return ['deleted' => $n];
});

// ---------- WOL 設備管理 ----------

function device_out(array $d): array
{
    $ver = (string)$d['last_app_version'];
    return [
        'id' => (int)$d['id'], 'device_code' => $d['device_code'], 'name' => $d['name'], 'os_hint' => $d['os_hint'],
        'mac' => trim((string)$d['mac']), 'all_macs' => pg_text_array($d['all_macs_json'] ?? null),
        'broadcast' => $d['broadcast'], 'port' => (int)$d['port'], 'send_count' => (int)$d['send_count'],
        'cooldown_sec' => (int)$d['cooldown_sec'], 'enabled' => (bool)$d['enabled'], 'sort_order' => (int)$d['sort_order'],
        'registered' => (bool)$d['registered'], 'last_pc_name' => $d['last_pc_name'], 'last_os' => $d['last_os'],
        'last_app_version' => $ver, 'last_app_channel' => $d['last_app_channel'], 'last_api_level' => $d['last_api_level'] !== null ? (int)$d['last_api_level'] : null,
        'version_label' => $d['last_app_channel'] !== '' && $ver !== '' ? channel_label((string)$d['last_app_channel']) . ' ' . display_version($ver) : '',
        'channel_mismatch' => channel_mismatch($d), 'expected_channel' => $d['last_os'] !== '' ? channel_for_os((string)$d['last_os']) : null,
        'last_account' => $d['last_account_name'] ?? null, 'last_ip' => $d['last_ip'],
        // dev.55 WOL-02／03：LAN IP 與 WOL 廣播位址分開；廣播位址空白＝自動（依 Server 網卡計算）
        'lan_ip' => (string)($d['lan_ip'] ?? ''),
        'broadcast_auto' => (string)$d['broadcast'] === '',
        'broadcast_effective' => (string)$d['broadcast'] !== '' ? (string)$d['broadcast'] : server_lan_broadcast(),
        'probe_ok' => isset($d['probe_ok']) ? (bool)$d['probe_ok'] : null,
        'probe_at' => ts($d['probe_at'] ?? null),
    ] + device_state($d);
}

/** Agent 心跳回報的 Server 區域網路資訊（介面、IP、遮罩、廣播位址）；沒有時回傳 []。 */
function server_lan(): array
{
    $hb = config_get('agent_heartbeat', null);
    return is_array($hb) && is_array($hb['lan'] ?? null) ? $hb['lan'] : [];
}

function server_lan_broadcast(): string
{
    $b = server_lan()['broadcast'] ?? '';
    return is_string($b) && valid_ipv4($b) ? $b : '255.255.255.255';
}

function device_rows(string $where, array $params = []): array
{
    return q_all("SELECT d.*, to_json(d.all_macs)::text AS all_macs_json,
                         NULLIF(COALESCE(NULLIF(a.full_name,''), a.username), '') AS last_account_name
                    FROM client_devices d LEFT JOIN accounts a ON a.id = d.last_account_id AND a.role <> 'super'
                   WHERE $where ORDER BY d.sort_order, d.name, d.id", $params);
}

function device_fields(array $d, bool $creating): array
{
    $set = [];
    foreach (['name' => 60, 'os_hint' => 40] as $k => $max) {
        $v = body_str($d, $k, $max);
        if ($v !== null) $set[$k] = $v;
    }
    if (array_key_exists('device_code', $d)) {
        $code = $d['device_code'];
        if ($code !== null && (!is_string($code) || !preg_match('/^[A-Za-z0-9_\-]{1,20}$/', $code))) fail(400, '設備代號需為 1～20 字英數字或 _-');
        $set['device_code'] = $code === '' ? null : $code;
    }
    if (array_key_exists('mac', $d)) $set['mac'] = normalize_mac($d['mac']) ?? fail(400, 'MAC 格式錯誤');
    // 廣播位址：空白＝自動（建議）；手動填寫時必須是 IPv4
    if (array_key_exists('broadcast', $d)) {
        $set['broadcast'] = ($d['broadcast'] === '' || $d['broadcast'] === null) ? '' : (valid_ipv4($d['broadcast']) ?? fail(400, 'WOL 廣播位址格式錯誤'));
    }
    if (array_key_exists('lan_ip', $d)) {
        $set['lan_ip'] = ($d['lan_ip'] === '' || $d['lan_ip'] === null) ? '' : (valid_lan_ipv4($d['lan_ip']) ?? fail(400, 'LAN IP 需為區域網路的 IPv4 位址'));
    }
    if (array_key_exists('port', $d)) {
        $port = body_int($d, 'port');
        if (!in_array($port, [7, 9], true)) fail(400, '連接埠只能是 7 或 9');
        $set['port'] = $port;
    }
    foreach (['send_count' => [1, 3], 'cooldown_sec' => [0, 3600], 'sort_order' => [-100000, 100000]] as $k => [$min, $max]) {
        $v = body_int($d, $k, $min, $max);
        if ($v !== null) $set[$k] = $v;
    }
    $en = body_bool($d, 'enabled');
    if ($en !== null) $set['enabled'] = $en;
    if ($creating && !isset($set['mac'])) fail(400, '請輸入 MAC');
    return $set;
}

/** dev.62：WOL 設備的新增、修改、刪除、登記與 LAN 掃描（用來新增設備）只有 Super 可以；
 *  管理員（Admin）在「WOL 管理」只能查看狀態、主動檢測與喚醒（登入畫面的「喚醒電腦」本來就只有喚醒）。 */
function require_wol_editor(): array
{
    $a = actor();
    if (!is_super($a)) fail(403, 'WOL 設備只有 Super 能新增、修改、刪除、登記或掃描；管理員可以查看狀態與喚醒', ['code' => 'super_only']);
    return $a;
}

route('GET', '/api/wol/devices', static function (): array {
    require_role('admin');
    return ['devices' => array_map('device_out', device_rows('d.registered')), 'server_lan' => server_lan() ?: null];
});

/** 最近一次某類工作的建立時間距今秒數（頻率限制用）；沒有時回傳 null。 */
function job_age_sec(string $type): ?int
{
    $v = q_val('SELECT EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))::int FROM jobs WHERE type = ?', [$type]);
    return $v === null ? null : (int)$v;
}

const NET_PROBE_MIN_SEC = 30;
const LAN_SCAN_MIN_SEC = 60;

// dev.55 WOL-03：Server 主動檢測（ICMP ping）。進入「喚醒與狀態」頁時觸發；30 秒內重複開頁不再送。
route('POST', '/api/wol/probe', static function (): array {
    $a = require_role('admin');
    $age = job_age_sec('net_probe');
    if ($age !== null && $age < NET_PROBE_MIN_SEC) return ['skipped' => true, 'retry_after' => NET_PROBE_MIN_SEC - $age];
    $r = enqueue_unique('net_probe', [], ['requested_by' => (int)$a['id']], 90, (int)$a['id']);
    return $r + ['skipped' => false];
});

// dev.55 WOL-01：掃描 LAN（手動、只掃 Server 所在網段且最多 /24、60 秒最多一次、不自動加入）
route('POST', '/api/wol/scan', static function (): array {
    $a = require_wol_editor();
    $age = job_age_sec('lan_scan');
    if ($age !== null && $age < LAN_SCAN_MIN_SEC) {
        $wait = LAN_SCAN_MIN_SEC - $age;
        Resp::$headers[] = 'Retry-After: ' . $wait;
        fail(429, "剛剛掃描過，請 {$wait} 秒後再試", ['code' => 'cooldown', 'retry_after' => $wait]);
    }
    $r = enqueue_unique('lan_scan', [], ['requested_by' => (int)$a['id']], 80, (int)$a['id']);
    audit($a, 'wol.lan_scan', ['job_id' => $r['job_id']]);
    return $r;
});

route('GET', '/api/wol/scan/{id}', static function (array $p): array {
    require_role('admin');
    $j = q_one("SELECT * FROM jobs WHERE id = ? AND type = 'lan_scan'", [$p['id']]) ?? fail(404, '找不到掃描工作');
    return ['status' => $j['status'], 'error' => $j['error'], 'result' => jdec($j['result'] ?? null), 'finished_at' => ts($j['finished_at'])];
});

route('POST', '/api/wol/devices', static function (): array {
    $a = require_wol_editor();
    $set = device_fields(Req::body(), true) + ['enabled' => false];
    $set['registered'] = true;
    $cols = array_keys($set);
    $id = (int)q_val('INSERT INTO client_devices(' . implode(',', $cols) . ') VALUES (' . implode(',', array_fill(0, count($cols), '?')) . ') RETURNING id', array_values($set));
    audit($a, 'wol.device_created', ['device_id' => $id, 'name' => $set['name'] ?? '']);
    return ['device' => device_out(device_rows('d.id = ?', [$id])[0])];
});

route('PATCH|DELETE', '/api/wol/devices/{id}', static function (array $p): array {
    $a = require_wol_editor();
    $dev = q_one('SELECT * FROM client_devices WHERE id = ?', [$p['id']]) ?? fail(404, '找不到設備');
    if (Req::$method === 'DELETE') {
        q('DELETE FROM client_devices WHERE id = ?', [$dev['id']]);
        audit($a, 'wol.device_deleted', ['device_id' => (int)$dev['id'], 'name' => $dev['name']]);
        return ['ok' => true];
    }
    $set = device_fields(Req::body(), false);
    if ($set) {
        $cols = array_map(static fn($k) => "$k = ?", array_keys($set));
        q('UPDATE client_devices SET ' . implode(', ', $cols) . ', updated_at = NOW() WHERE id = ?', array_merge(array_values($set), [$dev['id']]));
    }
    audit($a, 'wol.device_updated', ['device_id' => (int)$dev['id'], 'fields' => array_keys($set)]);
    return ['device' => device_out(device_rows('d.id = ?', [$dev['id']])[0])];
});

route('GET', '/api/devices/pending', static function (): array {
    require_role('admin');
    return ['devices' => array_map('device_out', device_rows('NOT d.registered'))];
});

route('POST', '/api/devices/{id}/register', static function (array $p): array {
    $a = require_wol_editor();
    $dev = q_one('SELECT * FROM client_devices WHERE id = ? AND NOT registered', [$p['id']]) ?? fail(404, '找不到待登記設備');
    $set = device_fields(Req::body(), false);
    unset($set['mac']); // 不必手動輸入 MAC
    if (($set['name'] ?? '') === '') fail(400, '請輸入電腦名稱');
    $set['registered'] = true;
    $cols = array_map(static fn($k) => "$k = ?", array_keys($set));
    q('UPDATE client_devices SET ' . implode(', ', $cols) . ', updated_at = NOW() WHERE id = ?', array_merge(array_values($set), [$dev['id']]));
    audit($a, 'wol.device_registered', ['device_id' => (int)$dev['id'], 'name' => $set['name']]);
    return ['device' => device_out(device_rows('d.id = ?', [$dev['id']])[0])];
});

// ---------- PC 資訊（Super 按「上傳到 Server」） ----------

route('POST', '/api/client/info', static function (): array {
    $a = require_role('super');
    $d = Req::body();
    $raw = jenc($d);
    if (strlen($raw) > 64 * 1024) fail(400, 'PC 資訊太大');
    $deviceId = null;
    foreach (is_array($d['macs'] ?? null) ? $d['macs'] : [] as $m) {
        $mac = normalize_mac($m);
        if ($mac && ($id = q_val('SELECT id FROM client_devices WHERE mac = ? OR ? = ANY(all_macs) LIMIT 1', [$mac, $mac]))) {
            $deviceId = (int)$id;
            break;
        }
    }
    $id = (int)q_val("INSERT INTO diagnostics(kind, device_id, data) VALUES ('client', ?, CAST(? AS jsonb)) RETURNING id", [$deviceId, $raw]);
    audit($a, 'client.info_uploaded', ['diagnostic_id' => $id, 'device_id' => $deviceId]);
    return ['id' => $id, 'device_id' => $deviceId];
});
