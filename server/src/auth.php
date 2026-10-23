<?php
declare(strict_types=1);

/** 登入、Session、角色、使用記錄（第 05、16 章）。 */

/**
 * 權限角色（dev.55 ROLE-03）：只有三級，與資料表 account_roles 一致（user＝1、admin＝2、super＝3）。
 * 後端授權只依 accounts.role 判斷；稱謂（助教、講師）只用於顯示，絕不參與授權。
 */
const ROLE_LEVEL = ['user' => 1, 'admin' => 2, 'super' => 3];
const SESSION_HOURS = 12;
const LOGIN_MAX_FAILS = 5;
const LOGIN_LOCK_MINUTES = 15;

final class Auth
{
    /** @var array|null 目前登入者（accounts 欄位＋session_hash） */
    public static ?array $actor = null;
}

function bearer_token(): ?string
{
    $h = Req::header('Authorization');
    return preg_match('/^Bearer ([a-f0-9]{64})$/', $h, $m) ? $m[1] : null;
}

/** 取得目前登入者；未登入或 Session 失效回 401。 */
function actor(): array
{
    if (Auth::$actor !== null) return Auth::$actor;
    $token = bearer_token();
    if ($token === null) fail(401, '請先登入', ['code' => 'login_required']);
    $hash = hash('sha256', $token);
    $a = q_one(
        "SELECT a.id, a.username, a.role, a.full_name, a.active, s.app, s.token_hash,
                (s.last_seen_at < NOW() - INTERVAL '60 seconds') AS stale
           FROM sessions s JOIN accounts a ON a.id = s.account_id
          WHERE s.token_hash = ? AND s.expires_at > NOW() AND a.active = TRUE",
        [$hash]
    );
    if (!$a) fail(401, '登入已過期，請重新登入', ['code' => 'session_expired']);
    if ($a['stale']) {
        q('UPDATE sessions SET last_seen_at = NOW() WHERE token_hash = ?', [$hash]);
    }
    unset($a['stale']);
    return Auth::$actor = $a;
}

/** 選擇性取得登入者（心跳等免登入 API 用）；無效時回 null，不報錯。 */
function optional_actor(): ?array
{
    if (bearer_token() === null) return null;
    try {
        return actor();
    } catch (ApiError) {
        return null;
    }
}

/**
 * 帳號名稱的 Unicode NFC 正規化（dev.55 FILE-02）：由 PostgreSQL normalize() 處理（PHP 映像沒有 intl），
 * 新增帳號與登入用同一個函式，確保「看起來一樣」的中文帳號只會有一種存法。英數帳號正規化後不變。
 */
function username_nfc(mixed $v): ?string
{
    if (!is_string($v)) return null;
    $v = trim($v);
    if ($v === '' || strlen($v) > 400 || !mb_check_encoding($v, 'UTF-8')) return null;
    return (string)q_val('SELECT normalize(?::text, NFC)', [$v]);
}

/** 稱謂表（dev.55 ROLE-03）：title_id → {title_id, en_title, zh_title}；每個請求只讀一次。與權限無關。 */
function account_titles(): array
{
    static $t = null;
    if ($t === null) {
        $t = [];
        foreach (q_all('SELECT title_id, en_title, zh_title FROM account_titles ORDER BY sort_order, title_id') as $r) {
            $t[$r['title_id']] = ['title_id' => $r['title_id'], 'en_title' => $r['en_title'], 'zh_title' => $r['zh_title']];
        }
    }
    return $t;
}

/** 帳號的稱謂（中文）；Super 或未設定時為空字串。 */
function title_label(array $u): string
{
    return (string)(account_titles()[(string)($u['title_id'] ?? '')]['zh_title'] ?? '');
}

function role_at_least(array $a, string $role): bool
{
    return (ROLE_LEVEL[$a['role']] ?? 0) >= ROLE_LEVEL[$role];
}

function require_role(string $role): array
{
    $a = actor();
    if (!role_at_least($a, $role)) fail(403, '沒有權限執行此操作', ['code' => 'forbidden']);
    return $a;
}

function is_super(array $a): bool
{
    return $a['role'] === 'super';
}

/** 管理 API：只在 127.0.0.1:4081，且只有 Super。 */
function require_local_super(): array
{
    if (!Req::isLocalAdmin()) fail(404, '找不到此功能');
    $a = actor();
    if (!is_super($a)) fail(403, '只有 Super 可使用');
    return $a;
}

/** 匯入／匯出：Super，或經 Super 授權的 Admin（server_config.import_export_admin_ids）。每次都重新檢查。 */
function require_import_export(): array
{
    $a = actor();
    if (is_super($a)) return $a;
    if ($a['role'] === 'admin') {
        $ids = config_get('import_export_admin_ids', []);
        if (is_array($ids) && in_array((int)$a['id'], array_map('intval', $ids), true)) return $a;
    }
    fail(403, '匯入／匯出只有 Super 或經授權的管理員可使用', ['code' => 'import_export_forbidden']);
}

function can_import_export(array $a): bool
{
    if (is_super($a)) return true;
    if ($a['role'] !== 'admin') return false;
    $ids = config_get('import_export_admin_ids', []);
    return is_array($ids) && in_array((int)$a['id'], array_map('intval', $ids), true);
}

function display_name(array $a): string
{
    $n = trim((string)($a['full_name'] ?? ''));
    return $n !== '' ? $n : (string)$a['username'];
}

function create_session(int $accountId, string $app): string
{
    $token = bin2hex(random_bytes(32));
    q("INSERT INTO sessions(token_hash, account_id, app, expires_at, client_ip, client_name)
       VALUES (?,?,?, NOW() + INTERVAL '" . SESSION_HOURS . " hours', ?, ?)",
        [hash('sha256', $token), $accountId, $app, Req::ip(), Req::pcName()]);
    return $token;
}

function revoke_all_sessions(int $accountId): void
{
    q('DELETE FROM sessions WHERE account_id = ?', [$accountId]);
}

/** 登入限流：同一 IP 連續失敗 5 次鎖定 15 分鐘（PHP-FPM 多行程共用，所以存資料庫）。 */
function login_lock_remaining(string $ip): int
{
    $s = q_val("SELECT CEIL(EXTRACT(EPOCH FROM (locked_until - NOW())))::int FROM login_failures
                WHERE ip = ? AND locked_until > NOW()", [$ip]);
    return $s ? max(1, (int)$s) : 0;
}

function login_record_failure(string $ip): void
{
    q("INSERT INTO login_failures(ip, fail_count, first_failed_at) VALUES (?, 1, NOW())
       ON CONFLICT (ip) DO UPDATE SET
         fail_count = CASE WHEN login_failures.locked_until IS NOT NULL AND login_failures.locked_until <= NOW()
                           THEN 1 ELSE login_failures.fail_count + 1 END,
         locked_until = CASE WHEN login_failures.locked_until IS NOT NULL AND login_failures.locked_until <= NOW()
                           THEN NULL ELSE login_failures.locked_until END", [$ip]);
    q("UPDATE login_failures SET locked_until = NOW() + INTERVAL '" . LOGIN_LOCK_MINUTES . " minutes', fail_count = 0
        WHERE ip = ? AND fail_count >= ?", [$ip, LOGIN_MAX_FAILS]);
}

function login_clear_failures(string $ip): void
{
    q('DELETE FROM login_failures WHERE ip = ?', [$ip]);
}

// ---------------- 使用記錄 ----------------

function actor_groups_text(int $accountId): string
{
    $names = q_all('SELECT g.name FROM course_group_members m JOIN course_groups g ON g.id = m.group_id
                     WHERE m.account_id = ? ORDER BY g.name', [$accountId]);
    return implode('、', array_column($names, 'name'));
}

/**
 * 寫入使用記錄。$actor 可為 null（例如登入頁免登入喚醒）。
 * 不記錄密碼、Token、雜湊值：$details 由呼叫端只放安全欄位。
 */
function audit(?array $actor, string $action, array $details = [], ?int $mediaId = null, string $path = '',
               ?array $snapshot = null): void
{
    $s = $snapshot ?? ($actor ? [
        'id' => (int)$actor['id'],
        'name' => display_name($actor),
        'role' => $actor['role'],
        'groups' => actor_groups_text((int)$actor['id']),
    ] : ['id' => null, 'name' => '', 'role' => '', 'groups' => '']);
    q('INSERT INTO audit_log(actor_id, actor_name, actor_role, actor_groups, action, media_id, path, details, client_ip, client_name)
       VALUES (?,?,?,?,?,?,?,CAST(? AS jsonb),?,?)',
        [$s['id'], $s['name'], $s['role'], $s['groups'], $action, $mediaId, mb_substr($path, 0, 1000),
         jenc($details ?: new stdClass()), Req::ip(), Req::pcName()]);
}

// ---------------- server_config ----------------

final class ConfigCache
{
    public static array $values = [];
}

function config_get(string $key, mixed $default = null): mixed
{
    if (!array_key_exists($key, ConfigCache::$values)) {
        $v = q_val('SELECT value::text FROM server_config WHERE key = ?', [$key]);
        ConfigCache::$values[$key] = $v === null ? null : json_decode((string)$v, true);
    }
    return ConfigCache::$values[$key] ?? $default;
}

function config_set(string $key, mixed $value, ?int $by = null): void
{
    q('INSERT INTO server_config(key, value, updated_at, updated_by) VALUES (?, CAST(? AS jsonb), NOW(), ?)
       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW(), updated_by = EXCLUDED.updated_by',
        [$key, jenc($value), $by]);
    ConfigCache::$values[$key] = $value;
}
