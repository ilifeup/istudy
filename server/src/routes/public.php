<?php
declare(strict_types=1);

/** 免登入 API：health、登入、登入頁設定、心跳、公開喚醒、Client Log。 */

route('GET', '/api/health', static function (): never {
    $v = version_info();
    try {
        $ok = (int)q_val('SELECT 1') === 1;
    } catch (Throwable) {
        $ok = false;
    }
    if (!$ok) json_out(['ok' => false, 'db' => 'fail'], 503);
    json_out([
        'ok' => true,
        'db' => 'ok',
        'version' => (string)$v['server'],
        'api_level' => (int)$v['api_level'],
        'min_api_level' => (int)$v['min_api_level'],
    ]);
});

route('GET', '/api/public/login-config', static function (): array {
    $p = config_get('login_placeholders', []);
    return [
        'username_placeholder' => (string)($p['username'] ?? '請輸入帳號'),
        'password_placeholder' => (string)($p['password'] ?? '請輸入密碼'),
    ];
});

route('POST', '/api/login', static function (): array {
    $ip = Req::ip();
    $wait = login_lock_remaining($ip);
    if ($wait > 0) {
        Resp::$headers[] = 'Retry-After: ' . $wait;
        fail(429, '嘗試次數過多，請 ' . (int)ceil($wait / 60) . ' 分鐘後再試', ['code' => 'locked', 'retry_after' => $wait]);
    }
    $d = Req::body();
    // dev.55 FILE-02：帳號先做 Unicode NFC（與新增帳號同一規則）；長度以字數計。英數帳號正規化後不變。
    $username = username_nfc($d['username'] ?? null) ?? '';
    $password = is_string($d['password'] ?? null) ? $d['password'] : '';
    $u = $username !== '' && mb_strlen($username) <= 80
        ? q_one('SELECT id, username, password_hash, role, full_name, active FROM accounts WHERE username = ?', [$username])
        : null;
    if (!$u || !password_verify($password, (string)$u['password_hash'])) {
        login_record_failure($ip);
        audit(null, 'login.failed', ['username' => mb_substr($username, 0, 80)]);
        fail(401, '帳號或密碼錯誤', ['code' => 'bad_credentials']);
    }
    if (!$u['active']) {
        audit(null, 'login.failed', ['username' => $u['username'], 'reason' => 'inactive']);
        fail(403, '此帳號已停用，請洽管理員', ['code' => 'inactive']);
    }
    $app = Req::isLocalAdmin() ? 'server' : 'client';
    // Server App（127.0.0.1:4081）只接受 Super；不建立 Session（第 10 章）
    if ($app === 'server' && $u['role'] !== 'super') {
        fail(403, 'Server App 只接受 Super 登入', ['code' => 'server_app_super_only']);
    }
    login_clear_failures($ip);
    if (password_needs_rehash((string)$u['password_hash'], PASSWORD_DEFAULT)) {
        q('UPDATE accounts SET password_hash = ? WHERE id = ?', [password_hash($password, PASSWORD_DEFAULT), $u['id']]);
    }
    $token = create_session((int)$u['id'], $app);
    q('DELETE FROM sessions WHERE expires_at < NOW()');
    audit($u, 'login', ['app' => $app]);
    return [
        'token' => $token,
        'expires_in' => SESSION_HOURS * 3600,
        'account' => me_out((int)$u['id']),
    ];
});

route('POST', '/api/logout', static function (): array {
    $a = actor();
    q('DELETE FROM sessions WHERE token_hash = ?', [$a['token_hash']]);
    audit($a, 'logout');
    return ['ok' => true];
});

// ---------------- 心跳回報（第 09 章） ----------------

route('POST', '/api/client/heartbeat', static function (): array {
    if (!is_lan_ip(Req::ip())) fail(403, '只接受區域網路來源');
    $d = Req::body();
    $macs = [];
    foreach (array_slice(is_array($d['macs'] ?? null) ? $d['macs'] : [], 0, 16) as $raw) {
        $mac = normalize_mac($raw);
        if ($mac) $macs[$mac] = $mac;
    }
    $macs = array_values($macs);
    if (!$macs) fail(400, '缺少網卡 MAC');
    $pc = mb_substr(preg_replace('/[\x00-\x1F\x7F]/u', '', (string)($d['pc_name'] ?? '')) ?? '', 0, 64);
    $os = mb_substr(preg_replace('/[\x00-\x1F\x7F]/u', '', (string)($d['os'] ?? '')) ?? '', 0, 120);
    $channel = in_array($d['channel'] ?? null, CLIENT_CHANNELS, true) ? $d['channel'] : '';
    $ver = preg_match('/^[0-9A-Za-z.\-+]{1,40}$/', (string)($d['app_version'] ?? '')) ? (string)$d['app_version'] : '';
    $api = is_int($d['api_level'] ?? null) ? max(0, min(1000, $d['api_level'])) : null;
    // dev.61：更新狀態（沒有回報的舊版 Client 保留原值）
    $upd = null;
    if (is_array($d['update'] ?? null) && in_array($d['update']['state'] ?? null, CLIENT_UPDATE_STATES, true)) {
        $u = $d['update'];
        $upd = jenc([
            'state' => $u['state'],
            'available' => is_string($u['available'] ?? null) && preg_match('/^[0-9A-Za-z.\-+]{1,40}$/', $u['available']) ? $u['available'] : null,
            'percent' => is_numeric($u['percent'] ?? null) ? max(0, min(100, (int)$u['percent'])) : null,
            'error' => is_string($u['error'] ?? null) ? mb_substr(preg_replace('/[\x00-\x1F\x7F]/u', ' ', $u['error']) ?? '', 0, 200) : null,
            'at' => date('c'),
        ]);
    }
    $actor = optional_actor();
    $accountId = $actor ? (int)$actor['id'] : null;

    $dev = q_one("SELECT id, last_seen_at > NOW() - INTERVAL '30 seconds' AS throttled FROM client_devices
                   WHERE mac = ANY(CAST(? AS text[])) OR all_macs && CAST(? AS text[])
                   ORDER BY registered DESC, id LIMIT 1",
        ['{' . implode(',', $macs) . '}', '{' . implode(',', $macs) . '}']);
    if ($dev && $dev['throttled']) return ['ok' => true, 'throttled' => true];
    $params = [$pc, $os, $ver, $channel, $api, $accountId, Req::ip(), '{' . implode(',', $macs) . '}'];
    if ($dev) {
        q('UPDATE client_devices SET last_seen_at = NOW(), last_pc_name = ?, last_os = ?, last_app_version = ?,
              last_app_channel = ?, last_api_level = ?, last_account_id = ?, last_ip = ?, all_macs = CAST(? AS text[]),
              last_update = COALESCE(CAST(? AS jsonb), last_update), updated_at = NOW() WHERE id = ?', array_merge($params, [$upd, (int)$dev['id']]));
        return ['ok' => true, 'registered' => true];
    }
    // 不認得的 MAC：列入「待登記設備」
    q('INSERT INTO client_devices(mac, registered, enabled, last_seen_at, last_pc_name, last_os, last_app_version,
          last_app_channel, last_api_level, last_account_id, last_ip, all_macs, name, os_hint, last_update)
       VALUES (?, FALSE, FALSE, NOW(), ?, ?, ?, ?, ?, ?, ?, CAST(? AS text[]), ?, ?, COALESCE(CAST(? AS jsonb), \'{}\'::jsonb))
       ON CONFLICT (mac) DO NOTHING',
        array_merge([$macs[0]], $params, [$pc, $os, $upd]));
    return ['ok' => true, 'registered' => false];
});

// ---------------- WOL：登入頁免登入（第 06 章） ----------------

/** 依心跳與喚醒時間計算顯示狀態。 */
function device_state(array $d): array
{
    $now = time();
    $seen = $d['last_seen_at'] ? strtotime((string)$d['last_seen_at']) : null;
    $wake = $d['last_wake_at'] ? strtotime((string)$d['last_wake_at']) : null;
    $online = $seen !== null && $now - $seen <= 180;
    $state = $online ? 'online' : 'offline';
    if ($wake !== null && $now - $wake <= 3600 && ($seen === null || $seen < $wake)) {
        $state = $now - $wake <= 300 ? 'waiting' : 'no_report';
    } elseif ($wake !== null && $seen !== null && $seen >= $wake && $now - $wake <= 300) {
        $state = 'booted';
    }
    return ['state' => $state, 'online' => $online, 'last_seen_at' => ts($d['last_seen_at']), 'last_wake_at' => ts($d['last_wake_at'])];
}

function channel_mismatch(array $d): bool
{
    if ($d['last_app_channel'] === '' || $d['last_os'] === '') return false;
    $expected = channel_for_os((string)$d['last_os']);
    return $expected !== null && $expected !== $d['last_app_channel'];
}

route('GET', '/api/wol/public/devices', static function (): array {
    if (!is_lan_ip(Req::ip())) fail(403, '只接受區域網路來源');
    $rows = q_all('SELECT id, device_code, name, os_hint, last_os, last_seen_at, last_wake_at FROM client_devices
                    WHERE registered AND enabled ORDER BY sort_order, name, id');
    // dev.62：只回傳數量（不含名稱、MAC），讓登入畫面說明「已登記但未啟用喚醒」的電腦為什麼不在清單裡
    $cnt = q_one('SELECT COUNT(*) FILTER (WHERE registered) AS registered, COUNT(*) FILTER (WHERE registered AND NOT enabled) AS disabled FROM client_devices');
    return ['devices' => array_map(static fn($d) => [
        'id' => (int)$d['id'],
        'device_code' => $d['device_code'],
        'name' => $d['name'],
        'os' => $d['os_hint'] !== '' ? $d['os_hint'] : $d['last_os'],
    ] + device_state($d), $rows), 'registered_total' => (int)$cnt['registered'], 'disabled_total' => (int)$cnt['disabled']];
});

/**
 * 喚醒：檢查設備已登記、啟用與冷卻時間，排入高優先 wol_wake 工作並寫入使用記錄。
 * 回傳 [已送出, 冷卻中]。
 */
function wake_devices(array $ids, ?array $actor): array
{
    $sent = [];
    $cooling = [];
    foreach ($ids as $id) {
        $d = q_one('SELECT * FROM client_devices WHERE id = ? AND registered AND enabled', [$id]);
        if (!$d) continue;
        $remain = (int)q_val("SELECT CEIL(EXTRACT(EPOCH FROM (last_wake_at + make_interval(secs => cooldown_sec) - NOW())))::int
                               FROM client_devices WHERE id = ? AND last_wake_at IS NOT NULL", [$id]);
        if ($remain > 0) {
            $cooling[] = ['id' => (int)$id, 'retry_after' => $remain];
            continue;
        }
        q('UPDATE client_devices SET last_wake_at = NOW() WHERE id = ?', [$id]);
        $job = enqueue_job('wol_wake', [
            'device_id' => (int)$d['id'], 'mac' => trim((string)$d['mac']), 'broadcast' => $d['broadcast'],
            'port' => (int)$d['port'], 'send_count' => (int)$d['send_count'],
        ], 100, $actor ? (int)$actor['id'] : null);
        audit($actor, 'wol.wake', ['device_id' => (int)$d['id'], 'device' => $d['name'], 'job_id' => $job]);
        $sent[] = ['id' => (int)$id, 'job_id' => $job];
    }
    return [$sent, $cooling];
}

function wake_request(?array $actor): array
{
    $d = Req::body();
    if (($d['all'] ?? false) === true) {
        $ids = array_map('intval', array_column(q_all('SELECT id FROM client_devices WHERE registered AND enabled'), 'id'));
    } else {
        $id = body_int($d, 'device_id', 1);
        if (!$id) fail(400, '請指定要喚醒的電腦');
        $ids = [$id];
    }
    [$sent, $cooling] = wake_devices($ids, $actor);
    if (!$sent && $cooling && count($ids) === 1) {
        Resp::$headers[] = 'Retry-After: ' . $cooling[0]['retry_after'];
        fail(429, '剛剛已喚醒過，請 ' . $cooling[0]['retry_after'] . ' 秒後再試', ['code' => 'cooldown', 'retry_after' => $cooling[0]['retry_after']]);
    }
    if (!$sent && !$cooling) fail(404, '找不到可喚醒的電腦（需已登記並啟用）');
    return ['sent' => $sent, 'cooling' => $cooling];
}

route('POST', '/api/wol/public/wake', static function (): array {
    if (!is_lan_ip(Req::ip())) fail(403, '只接受區域網路來源');
    return wake_request(optional_actor());
});

route('POST', '/api/wol/wake', static function (): array {
    return wake_request(actor());
});

// ---------------- Client Log 上傳（每天第一次啟動時；可在登入前） ----------------

route('POST', '/api/client/logs', static function (): array {
    if (!is_lan_ip(Req::ip())) fail(403, '只接受區域網路來源');
    $d = Req::body();
    $entries = is_array($d['entries'] ?? null) ? array_slice($d['entries'], 0, 500) : [];
    $mac = normalize_mac($d['mac'] ?? null);
    $deviceId = $mac ? q_val('SELECT id FROM client_devices WHERE mac = ? OR ? = ANY(all_macs) LIMIT 1', [$mac, $mac]) : null;
    if ($deviceId) {
        $recent = (int)q_val("SELECT COUNT(*) FROM client_logs WHERE device_id = ? AND created_at > NOW() - INTERVAL '10 minutes'", [$deviceId]);
        if ($recent > 2000) fail(429, '上傳太頻繁');
    }
    $actor = optional_actor();
    $pc = mb_substr((string)($d['pc_name'] ?? ''), 0, 64);
    $channel = in_array($d['channel'] ?? null, CLIENT_CHANNELS, true) ? $d['channel'] : '';
    $ver = mb_substr((string)($d['app_version'] ?? ''), 0, 40);
    $n = 0;
    foreach ($entries as $e) {
        if (!is_array($e)) continue;
        $msg = mb_substr(preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', '', (string)($e['message'] ?? '')) ?? '', 0, 2000);
        if ($msg === '') continue;
        // 不保存 Token 或密碼：遮蔽可能的 Bearer 值
        $msg = preg_replace('/Bearer\s+[A-Za-z0-9]+/i', 'Bearer ***', $msg) ?? $msg;
        $level = in_array($e['level'] ?? '', ['debug', 'info', 'warn', 'error'], true) ? $e['level'] : 'info';
        $at = is_string($e['time'] ?? null) && strtotime($e['time']) ? date(DATE_ATOM, strtotime($e['time'])) : null;
        q('INSERT INTO client_logs(logged_at, account_id, device_id, pc_name, app_channel, app_version, level, message)
           VALUES (?,?,?,?,?,?,?,?)', [$at, $actor ? (int)$actor['id'] : null, $deviceId ? (int)$deviceId : null, $pc, $channel, $ver, $level, $msg]);
        $n++;
    }
    return ['ok' => true, 'stored' => $n];
});
