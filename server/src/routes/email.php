<?php
declare(strict_types=1);

/**
 * dev.57 Super 專用「Email 報告」：簡單設計，不做排程、不做通知平台。
 * - 寄送：Server App 與 Client App 都可以（Super 登入）；只寄給設定好的收件人（預設 eachcore.app@gmail.com）。
 * - 寄送方式：relay（預設）＝HTTPS 送到你網站主機上的 istudy-mail-relay.php，由網站的 PHP mail() 寄出，不需要信箱密碼；
 *             smtp＝寄件信箱帳號。
 * - 設定只能在 Server App（127.0.0.1:4081）修改；代寄金鑰與 SMTP 密碼另存 email_secret，任何 API 都不回傳。
 * - 實際寄信由 Agent 執行（背景工作 email_report），寄送紀錄就是 jobs。
 */

const EMAIL_DEFAULT_TO = ['eachcore.app@gmail.com'];
const EMAIL_REPORTS = ['summary', 'asr', 'message'];
const EMAIL_DAILY_LIMIT = 20;

function email_settings_out(): array
{
    $s = config_get('email', []);
    $s = is_array($s) ? $s : [];
    $sec = config_get('email_secret', []);
    $sec = is_array($sec) ? $sec : [];
    $to = array_values(array_filter(is_array($s['to'] ?? null) ? $s['to'] : EMAIL_DEFAULT_TO, 'is_string'));
    $method = ($s['method'] ?? 'relay') === 'smtp' ? 'smtp' : 'relay';
    $relayKeySet = (string)($sec['relay_key'] ?? '') !== '';
    return [
        'method' => $method,
        'relay_url' => (string)($s['relay_url'] ?? ''),
        'relay_key_set' => $relayKeySet,
        'host' => (string)($s['host'] ?? ''),
        'port' => (int)($s['port'] ?? 587),
        'security' => in_array($s['security'] ?? '', ['starttls', 'ssl', 'none'], true) ? $s['security'] : 'starttls',
        'username' => (string)($s['username'] ?? ''),
        'from' => (string)($s['from'] ?? ''),
        'to' => $to ?: EMAIL_DEFAULT_TO,
        'password_set' => (string)($sec['password'] ?? '') !== '',
        'configured' => $method === 'relay'
            ? (($s['relay_url'] ?? '') !== '' && $relayKeySet)
            : (($s['host'] ?? '') !== '' && ($s['from'] ?? '') !== ''),
    ];
}

function email_valid(mixed $v): bool
{
    return is_string($v) && strlen($v) <= 254 && filter_var($v, FILTER_VALIDATE_EMAIL) !== false;
}

// dev.58：「先數、再判斷、再新增」放在同一個交易，並先取得交易層級的鎖（pg_advisory_xact_lock，交易結束自動釋放），
// 同時送出的請求會排隊執行，不會都通過檢查而超過上限。只用 PostgreSQL 內建功能，不另加服務。
const EMAIL_LOCK_REPORT = 5801001;     // Email 報告（一次一封、24 小時 20 封）
const EMAIL_LOCK_FEEDBACK = 5801002;   // 問題回報（每人每小時 5 次、全部一天 50 次）

function email_enqueue(array $a, array $payload): array
{
    return tx(static function () use ($a, $payload): array {
        q_val('SELECT pg_advisory_xact_lock(' . EMAIL_LOCK_REPORT . ')');
        if (q_val("SELECT 1 FROM jobs WHERE type = 'email_report' AND status IN ('queued','running') AND payload->>'report' <> 'feedback' LIMIT 1")) {
            fail(409, '上一封還在寄送，請稍後再試');
        }
        $n = (int)q_val("SELECT COUNT(*) FROM jobs WHERE type = 'email_report' AND payload->>'report' <> 'feedback' AND created_at > NOW() - INTERVAL '24 hours'");
        if ($n >= EMAIL_DAILY_LIMIT) fail(429, '24 小時內最多寄 ' . EMAIL_DAILY_LIMIT . ' 封');
        $payload['requested_by'] = (string)$a['username'];
        $id = enqueue_job('email_report', $payload, 50, (int)$a['id']);
        audit($a, 'email.report_requested', ['job_id' => $id, 'report' => $payload['report'], 'attachment' => isset($payload['uem_html'])]);
        return ['job_id' => $id];
    });
}

/**
 * HTML 附件（dev.58，目前只有主機盤點報告；之後其他系統報告也用同一套規則）：
 *   權限：只有 Super、而且只能從 Server App（本機管理埠）送出；Client App（LAN）一律拒絕。
 *   內容：完整的 HTML 文件、UTF-8、2MB 以內；不能有會執行或會連外的內容。
 * 這不是 HTML 清理器：不符合規則就整份拒絕，不嘗試修改內容。回傳錯誤原因，沒問題回傳 null。
 */
function email_html_problem(string $html): ?string
{
    if (strlen($html) > 2 * 1024 * 1024) return '附件超過 2MB';
    if (!mb_check_encoding($html, 'UTF-8')) return '附件必須是 UTF-8 文字';
    if (!preg_match('/^\s*<!doctype html>/i', $html)) return '附件必須是完整的 HTML 報告（<!doctype html> 開頭）';
    $rules = [
        '/<\s*\/?\s*(script|iframe|frame|frameset|object|embed|applet|form|input|button|textarea|select|base|link|portal)\b/i'
            => '不能含有指令碼、內嵌頁面、表單或外部樣式',
        '/<\s*meta\b[^>]*http-equiv/i' => '不能含有自動轉址（meta http-equiv）',
        '/[\s"\'\/]on[a-z]+\s*=/i' => '不能含有事件屬性（onload、onclick…）',
        '/(javascript|vbscript|livescript)\s*:/i' => '不能含有 javascript: 連結',
        '/\b(src|href|action|formaction|poster|background|srcset|data|xlink:href)\s*=\s*["\']?\s*(https?:|\/\/|ftp:|file:|data:(?!image\/(png|jpe?g|gif|webp);base64,))/i'
            => '不能載入或連到外部資源（只允許內嵌圖片）',
        '/url\s*\(\s*["\']?\s*(https?:|\/\/|ftp:|file:)/i' => '樣式不能載入外部資源',
        '/@import\b/i' => '樣式不能載入外部資源',
    ];
    // 實體編碼（例如 java&#115;cript:）解開後也要再檢查一次
    foreach ([$html, html_entity_decode($html, ENT_QUOTES | ENT_HTML5, 'UTF-8')] as $text) {
        foreach ($rules as $re => $why) {
            if (preg_match($re, $text)) return $why;
        }
    }
    return null;
}

// ---------- 設定（只在 Server App） ----------

route('GET', '/api/admin/email', static function (): array {
    require_local_super();
    return email_settings_out();
});

route('PUT', '/api/admin/email', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $cur = config_get('email', []);
    $cur = is_array($cur) ? $cur : [];
    if (array_key_exists('method', $d)) {
        if (!in_array($d['method'], ['relay', 'smtp'], true)) fail(400, '寄送方式只能是 relay（網站代寄）或 smtp');
        $cur['method'] = $d['method'];
    }
    if (array_key_exists('relay_url', $d)) {
        $u = body_str($d, 'relay_url', 300) ?? '';
        if ($u !== '' && (!preg_match('#^https://[A-Za-z0-9.\-]+(:\d+)?/\S*$#', $u) || filter_var($u, FILTER_VALIDATE_URL) === false)) {
            fail(400, '代寄網址必須是 https:// 開頭的完整網址');
        }
        $cur['relay_url'] = $u;
    }
    if (array_key_exists('host', $d)) {
        $h = body_str($d, 'host', 253) ?? '';
        if ($h !== '' && !preg_match('/^[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?$/', $h)) fail(400, 'SMTP 主機格式錯誤（例如 smtp.gmail.com）');
        $cur['host'] = $h;
    }
    if (array_key_exists('port', $d)) $cur['port'] = body_int($d, 'port', 1, 65535);
    if (array_key_exists('security', $d)) {
        if (!in_array($d['security'], ['starttls', 'ssl', 'none'], true)) fail(400, '加密方式只能是 starttls、ssl 或 none');
        $cur['security'] = $d['security'];
    }
    if (array_key_exists('username', $d)) $cur['username'] = body_str($d, 'username', 200) ?? '';
    if (array_key_exists('from', $d)) {
        $f = body_str($d, 'from', 254) ?? '';
        if ($f !== '' && !email_valid($f)) fail(400, '寄件人 Email 格式錯誤');
        $cur['from'] = $f;
    }
    if (array_key_exists('to', $d)) {
        $to = is_array($d['to']) ? $d['to'] : fail(400, '收件人格式錯誤');
        $clean = [];
        foreach ($to as $t) {
            $t = is_string($t) ? trim($t) : '';
            if ($t === '') continue;
            if (!email_valid($t)) fail(400, "收件人 Email 格式錯誤：{$t}");
            $clean[strtolower($t)] = $t;
        }
        if (!$clean || count($clean) > 5) fail(400, '收件人需要 1～5 位');
        $cur['to'] = array_values($clean);
    }
    $sec = config_get('email_secret', []);
    $sec = is_array($sec) ? $sec : [];
    $secChanged = [];
    if (array_key_exists('password', $d) && is_string($d['password']) && $d['password'] !== '') {
        if (strlen($d['password']) > 200) fail(400, '密碼太長');
        $sec['password'] = $d['password'];
        $secChanged[] = 'password';
    } elseif (!empty($d['clear_password'])) {
        $sec['password'] = '';
        $secChanged[] = 'password';
    }
    if (array_key_exists('relay_key', $d) && is_string($d['relay_key']) && $d['relay_key'] !== '') {
        if (strlen($d['relay_key']) < 24 || strlen($d['relay_key']) > 200 || preg_match('/[\x00-\x20\x7F]/', $d['relay_key'])) {
            fail(400, '代寄金鑰需要 24～200 個字元，不能有空白');
        }
        $sec['relay_key'] = $d['relay_key'];
        $secChanged[] = 'relay_key';
    }
    config_set('email', $cur, (int)$a['id']);
    if ($secChanged) config_set('email_secret', $sec, (int)$a['id']);
    $changed = array_merge(array_keys(array_intersect_key($d, array_flip(['method', 'relay_url', 'host', 'port', 'security', 'username', 'from', 'to']))), $secChanged);
    audit($a, 'email.settings_updated', ['keys' => $changed]);   // 不記錄密碼
    return email_settings_out();
});

route('POST', '/api/admin/email/test', static function (): array {
    $a = require_local_super();
    if (!email_settings_out()['configured']) fail(409, '請先完成寄送設定（網站代寄網址與金鑰，或 SMTP）');
    return email_enqueue($a, ['report' => 'test']);
});

// ---------- 寄送與紀錄（Server App、Client App 的 Super 都可以） ----------

route('GET', '/api/email', static function (): array {
    require_role('super');
    $s = email_settings_out();
    $rows = q_all("SELECT id, status, payload, result, error, created_at, finished_at FROM jobs WHERE type = 'email_report'
                    AND payload->>'report' <> 'feedback' ORDER BY id DESC LIMIT 20");
    // 問題回報另外列出完整內容：Server 不能上網、寄信失敗時，Super 到教室也看得到
    $fb = q_all("SELECT id, status, payload, error, created_at FROM jobs WHERE type = 'email_report'
                  AND payload->>'report' = 'feedback' ORDER BY id DESC LIMIT 30");
    return [
        'configured' => $s['configured'], 'method' => $s['method'], 'to' => $s['to'], 'can_edit' => Req::isLocalAdmin(),
        'reports' => EMAIL_REPORTS, 'daily_limit' => EMAIL_DAILY_LIMIT,
        'history' => array_map(static function ($r) {
            $p = jdec($r['payload'], []);
            $res = jdec($r['result'] ?? null);
            return ['id' => (int)$r['id'], 'status' => $r['status'], 'report' => $p['report'] ?? '', 'by' => $p['requested_by'] ?? '',
                'attachment' => isset($p['uem_html']), 'subject' => is_array($res) ? ($res['subject'] ?? null) : null,
                'to' => is_array($res) ? ($res['to'] ?? []) : [], 'error' => $r['error'],
                'created_at' => ts($r['created_at']), 'finished_at' => ts($r['finished_at'])];
        }, $rows),
        'feedback' => array_map(static function ($r) {
            $p = jdec($r['payload'], []);
            $who = is_array($p['reporter'] ?? null) ? $p['reporter'] : [];
            return ['id' => (int)$r['id'], 'status' => $r['status'], 'error' => $r['error'], 'summary' => (string)($p['note'] ?? ''),
                'username' => (string)($who['username'] ?? ''), 'full_name' => (string)($who['full_name'] ?? ''),
                'pc' => (string)($p['pc'] ?? ''), 'app' => (string)($p['app'] ?? ''), 'created_at' => ts($r['created_at'])];
        }, $fb),
    ];
});

route('POST', '/api/email/send', static function (): array {
    $a = require_role('super');
    if (!email_settings_out()['configured']) fail(409, 'Email 尚未設定：請在 Server App「Email 報告」完成寄送設定');
    $d = Req::body();
    $report = $d['report'] ?? '';
    if (!in_array($report, EMAIL_REPORTS, true)) fail(400, '報告類型只能是 summary、asr 或 message');
    $note = body_str($d, 'note', 2000) ?? '';
    $subject = body_str($d, 'subject', 120) ?? '';
    if ($report === 'message' && $note === '') fail(400, '請輸入訊息內容');
    $payload = ['report' => $report, 'note' => $note, 'subject' => $subject];
    if (isset($d['uem_html']) && $d['uem_html'] !== '') {
        if (!Req::isLocalAdmin()) fail(403, 'HTML 附件只能從 Server App 寄出');
        if (!is_string($d['uem_html'])) fail(400, '附件格式錯誤');
        $why = email_html_problem($d['uem_html']);
        if ($why !== null) fail(400, '附件不能寄出：' . $why);
        $payload['uem_html'] = $d['uem_html'];
        if (isset($d['attachment_name'])) {
            $name = is_string($d['attachment_name']) ? $d['attachment_name'] : '';
            if (!preg_match('/^[A-Za-z0-9._-]{1,80}\.html$/', $name)) fail(400, '附件檔名只能用英數字、點、底線、連字號，並以 .html 結尾');
            $payload['attachment_name'] = $name;
        }
    }
    return email_enqueue($a, $payload);
});

// ---------- 問題回報（dev.57：任何登入的使用者；內容送到 Server，由 Server 寄給 Super 設定的收件人） ----------

const FEEDBACK_PER_HOUR = 5;
const FEEDBACK_PER_DAY = 50;   // 全部使用者合計

route('POST', '/api/feedback', static function (): array {
    $a = actor();
    $d = Req::body();
    $summary = body_str($d, 'summary', 1000) ?? '';
    if (mb_strlen($summary) < 2) fail(400, '請寫下問題概述');
    $title = q_one('SELECT t.zh_title FROM accounts x LEFT JOIN account_titles t ON t.title_id = x.title_id WHERE x.id = ?', [(int)$a['id']]);
    $payload = [
        'report' => 'feedback', 'note' => $summary, 'requested_by' => (string)$a['username'],
        'reporter' => ['username' => (string)$a['username'], 'full_name' => (string)($a['full_name'] ?? ''), 'role' => (string)$a['role'],
            'title' => (string)($title['zh_title'] ?? '')],
        // 以下由 App 提供，只作為參考資訊
        'app' => body_str($d, 'app', 80) ?? '', 'pc' => body_str($d, 'pc', 80) ?? '', 'page' => body_str($d, 'page', 80) ?? '',
    ];
    $id = tx(static function () use ($a, $payload): int {
        q_val('SELECT pg_advisory_xact_lock(' . EMAIL_LOCK_FEEDBACK . ')');   // 計數與新增在同一把鎖裡（dev.58）
        $mine = (int)q_val("SELECT COUNT(*) FROM jobs WHERE type = 'email_report' AND payload->>'report' = 'feedback' AND created_by = ?
                             AND created_at > NOW() - INTERVAL '1 hour'", [(int)$a['id']]);
        if ($mine >= FEEDBACK_PER_HOUR) fail(429, '1 小時內最多回報 ' . FEEDBACK_PER_HOUR . ' 次，請稍後再試');
        $all = (int)q_val("SELECT COUNT(*) FROM jobs WHERE type = 'email_report' AND payload->>'report' = 'feedback' AND created_at > NOW() - INTERVAL '24 hours'");
        if ($all >= FEEDBACK_PER_DAY) fail(429, '今天的問題回報已達上限，請直接告訴管理員');
        $id = enqueue_job('email_report', $payload, 45, (int)$a['id']);
        audit($a, 'feedback.submitted', ['job_id' => $id]);
        return $id;
    });
    return ['ok' => true, 'job_id' => $id, 'email_configured' => email_settings_out()['configured']];
});

route('GET', '/api/feedback/mine', static function (): array {
    $a = actor();
    $rows = q_all("SELECT id, status, payload, created_at FROM jobs WHERE type = 'email_report' AND payload->>'report' = 'feedback'
                    AND created_by = ? ORDER BY id DESC LIMIT 10", [(int)$a['id']]);
    return ['items' => array_map(static function ($r) {
        $p = jdec($r['payload'], []);
        return ['id' => (int)$r['id'], 'status' => $r['status'], 'summary' => (string)($p['note'] ?? ''), 'created_at' => ts($r['created_at'])];
    }, $rows)];
});
