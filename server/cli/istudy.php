<?php
declare(strict_types=1);

/**
 * 主機維護用的 PHP 指令（由 istudyctl 透過 docker compose exec 呼叫）：
 *   php cli/istudy.php migrate                      全新資料庫建立結構；既有資料庫只套用尚未執行的 migration
 *   php cli/istudy.php create-super <帳號>          密碼由標準輸入讀取（兩行需相同），不出現在指令參數
 *   php cli/istudy.php reset-super                  重設 Super 密碼（密碼由標準輸入讀取）
 *   php cli/istudy.php status                       輸出 JSON：結構版本、帳號數、是否已有 Super
 *   php cli/istudy.php install-id <識別碼>           記錄安裝識別碼（與 /Media/.istudy-media 比對）
 */

$src = getenv('ISTUDY_SRC') ?: dirname(__DIR__) . '/src';
require $src . '/core.php';
require $src . '/auth.php';
require $src . '/rules.php';

function out(array $d, int $code = 0): never
{
    fwrite(STDOUT, json_encode($d, JSON_UNESCAPED_UNICODE) . PHP_EOL);
    exit($code);
}

function read_password(): string
{
    $a = rtrim((string)fgets(STDIN), "\r\n");
    $b = rtrim((string)fgets(STDIN), "\r\n");
    if ($a !== $b) out(['ok' => false, 'error' => '兩次輸入的密碼不同'], 2);
    if (strlen($a) < 10) out(['ok' => false, 'error' => 'Super 密碼至少 10 字'], 2);
    return $a;
}

$cmd = $argv[1] ?? '';
$sqlDir = getenv('ISTUDY_SQL') ?: dirname(__DIR__) . '/sql';

try {
    switch ($cmd) {
        case 'migrate':
            $fresh = !q_val("SELECT to_regclass('public.schema_migrations') IS NOT NULL");
            $applied = [];
            if ($fresh) {
                db()->exec((string)file_get_contents($sqlDir . '/schema.sql'));
                $applied[] = '0001_schema';
            }
            $done = array_column(q_all('SELECT version FROM schema_migrations'), 'version');
            $files = glob($sqlDir . '/migrations/[0-9][0-9][0-9][0-9]_*.sql') ?: [];
            sort($files, SORT_STRING);
            foreach ($files as $f) {
                $ver = basename($f, '.sql');
                if (in_array($ver, $done, true)) continue;
                tx(static function () use ($f, $ver) {
                    db()->exec((string)file_get_contents($f));
                    q('INSERT INTO schema_migrations(version) VALUES (?)', [$ver]);
                });
                $applied[] = $ver;
            }
            out(['ok' => true, 'fresh' => $fresh, 'applied' => $applied]);

        case 'create-super':
            // ROLE-04：Super 只有一位。已有就略過；資料庫另有唯一索引（0002），同時執行也不會建出第二位。
            $username = valid_username(username_nfc($argv[2] ?? null)) ?? out(['ok' => false, 'error' => USERNAME_RULE_TEXT], 2);
            if (q_val("SELECT 1 FROM accounts WHERE role = 'super'")) out(['ok' => true, 'skipped' => true, 'message' => '已有 Super，略過']);
            $pw = read_password();
            // Super 沒有稱謂（title_id 留空）；權限 super 由 account_roles 外鍵約束
            q("INSERT INTO accounts(username, password_hash, role, full_name) VALUES (?, ?, 'super', '')",
                [$username, password_hash($pw, PASSWORD_DEFAULT)]);
            out(['ok' => true, 'created' => $username]);

        case 'reset-super':
            // ROLE-04：只重設唯一那一位；不是剛好一位時拒絕，不猜測、不新增、不刪除
            $n = (int)q_val("SELECT COUNT(*) FROM accounts WHERE role = 'super'");
            if ($n === 0) out(['ok' => false, 'error' => '找不到 Super 帳號，請用 create-super 建立'], 3);
            if ($n > 1) out(['ok' => false, 'error' => "資料庫內有 {$n} 個 Super，已停止；請人工確認（不會自動修改帳號）"], 5);
            $row = q_one("SELECT id, username FROM accounts WHERE role = 'super'");
            $pw = read_password();
            q('UPDATE accounts SET password_hash = ?, active = TRUE, updated_at = NOW() WHERE id = ?', [password_hash($pw, PASSWORD_DEFAULT), $row['id']]);
            revoke_all_sessions((int)$row['id']);
            q("INSERT INTO system_logs(source, level, message) VALUES ('host', 'warn', 'Super 密碼已由主機重設')");
            out(['ok' => true, 'username' => $row['username']]);

        case 'status':
            $has = (bool)q_val("SELECT to_regclass('public.schema_migrations') IS NOT NULL");
            out(['ok' => true, 'schema' => $has ? array_column(q_all('SELECT version FROM schema_migrations ORDER BY version'), 'version') : [],
                'accounts' => $has ? (int)q_val('SELECT COUNT(*) FROM accounts') : 0,
                'has_super' => $has && (bool)q_val("SELECT 1 FROM accounts WHERE role = 'super'"),
                'super_count' => $has ? (int)q_val("SELECT COUNT(*) FROM accounts WHERE role = 'super'") : 0,
                'install_id' => $has ? config_get('install_id') : null]);

        case 'install-id':
            $id = $argv[2] ?? '';
            if (!preg_match('/^[a-f0-9]{32}$/', $id)) out(['ok' => false, 'error' => '識別碼格式錯誤'], 2);
            $cur = config_get('install_id');
            if ($cur && $cur !== $id) out(['ok' => false, 'error' => '資料庫已有不同的安裝識別碼', 'current' => $cur], 4);
            config_set('install_id', $id);
            out(['ok' => true, 'install_id' => $id]);

        default:
            out(['ok' => false, 'error' => '未知的指令：' . $cmd], 64);
    }
} catch (Throwable $e) {
    out(['ok' => false, 'error' => $e->getMessage()], 1);
}
