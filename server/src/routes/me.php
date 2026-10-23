<?php
declare(strict_types=1);

/** 個人資料與修改密碼（第 08 章）。只能讀寫自己的資料，不回傳密碼雜湊。 */

const PROFILE_FIELDS = ['full_name' => 80, 'phone' => 40, 'address' => 200, 'class_time' => 80, 'bio' => 2000];

function me_out(int $id): array
{
    $u = q_one('SELECT id, username, role, title_id, full_name, phone, birth_date, start_year, address, class_time, bio, active
                  FROM accounts WHERE id = ?', [$id]);
    if (!$u) fail(404, '找不到帳號');
    $groups = q_all('SELECT g.id, g.name FROM course_group_members m JOIN course_groups g ON g.id = m.group_id
                      WHERE m.account_id = ? ORDER BY g.name', [$id]);
    return [
        'id' => (int)$u['id'],
        'username' => $u['username'],
        'role' => $u['role'],
        // dev.55 ROLE-02／03：權限以英文顯示（user／admin／super）；稱謂（助教、講師）另外一欄，Super 沒有稱謂
        'role_label' => $u['role'],
        'title_id' => $u['title_id'],
        'title' => title_label($u),
        'full_name' => $u['full_name'],
        'display_name' => display_name($u),
        'phone' => $u['phone'],
        'birth_date' => $u['birth_date'],
        'start_year' => $u['start_year'] !== null ? (int)$u['start_year'] : null,
        'address' => $u['address'],
        'class_time' => $u['class_time'],
        'bio' => $u['bio'],
        'groups' => array_map(static fn($g) => ['id' => (int)$g['id'], 'name' => $g['name']], $groups),
        'can_import_export' => can_import_export($u),
        'tag_warning' => (bool)q_val('SELECT show_warning FROM tag_role_policy WHERE role = ?', [$u['role']]),
    ];
}

/** 帳號資料的白名單欄位（個人資料與帳號管理共用）。 */
function profile_updates(array $d): array
{
    $set = [];
    foreach (PROFILE_FIELDS as $k => $max) {
        $v = body_str($d, $k, $max);
        if ($v !== null) $set[$k] = $v;
    }
    if (array_key_exists('birth_date', $d)) {
        if ($d['birth_date'] !== null && $d['birth_date'] !== '' && valid_date($d['birth_date']) === null) fail(400, '生日格式錯誤（YYYY-MM-DD）');
        $set['birth_date'] = $d['birth_date'] === '' ? null : $d['birth_date'];
    }
    if (array_key_exists('start_year', $d)) {
        $set['start_year'] = $d['start_year'] === '' || $d['start_year'] === null ? null : body_int($d, 'start_year', 1900, 2200);
    }
    return $set;
}

function apply_account_update(int $id, array $set): void
{
    if (!$set) return;
    $cols = [];
    $vals = [];
    foreach ($set as $k => $v) {
        $cols[] = "$k = ?";
        $vals[] = $v;
    }
    $vals[] = $id;
    q('UPDATE accounts SET ' . implode(', ', $cols) . ', updated_at = NOW() WHERE id = ?', $vals);
}

route('GET', '/api/me', static function (): array {
    return me_out((int)actor()['id']);
});

route('PATCH', '/api/me', static function (): array {
    $a = actor();
    $d = Req::body();
    // 不可自行修改：帳號、角色、群組、權限
    foreach (['username', 'role', 'title_id', 'groups', 'active', 'password'] as $forbidden) {
        if (array_key_exists($forbidden, $d)) fail(400, '此欄位不可自行修改：' . $forbidden);
    }
    $set = profile_updates($d);
    apply_account_update((int)$a['id'], $set);
    audit($a, 'profile.updated', ['fields' => array_keys($set)]);
    return me_out((int)$a['id']);
});

route('POST', '/api/me/password', static function (): array {
    $a = actor();
    $d = Req::body();
    $current = is_string($d['current_password'] ?? null) ? $d['current_password'] : '';
    $new = valid_password($d['new_password'] ?? null);
    if ($new === null) fail(400, '新密碼至少 8 碼');
    $hash = (string)q_val('SELECT password_hash FROM accounts WHERE id = ?', [$a['id']]);
    if (!password_verify($current, $hash)) fail(400, '目前密碼不正確', ['code' => 'bad_current_password']);
    q('UPDATE accounts SET password_hash = ?, updated_at = NOW() WHERE id = ?', [password_hash($new, PASSWORD_DEFAULT), $a['id']]);
    revoke_all_sessions((int)$a['id']);
    audit($a, 'password.changed');
    return ['ok' => true, 'message' => '密碼已更新，請重新登入'];
});
