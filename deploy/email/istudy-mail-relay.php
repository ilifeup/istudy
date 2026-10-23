<?php
/**
 * iStudy 網站代寄程式（relay v2，2026-10-20）：放在可以用 PHP mail() 寄信的網站主機上（https://work.eachcore.com/）。
 * 可以直接替換 dev.57 的同名檔：iStudy Server 送來的格式與回應都不變（dev.57、dev.58 通用）。
 *
 * 寄法對照同一台 InterServer 主機上已成功寄到 Gmail 的 Life Dashboard（send_login_code）：
 *   - 寄件人 no-reply@work.eachcore.com；
 *   - mail() 第五參數「-f 寄件地址」設定信封寄件人（envelope sender）；主機不接受時，不帶 -f 再試一次；
 *   - 內文 text/plain; charset=UTF-8，Content-Transfer-Encoding: 8bit；標頭與內文都以 CRLF 換行。
 *   有 HTML 附件時改用 multipart/mixed：文字部分同樣 8bit，附件 Base64。
 *
 * mail() 回傳成功只代表「網站主機已接受寄送」，不代表 Gmail 已收到；是否送達要看收件匣（含垃圾郵件）。
 *
 * 安裝（換檔）：
 *   1. 把舊檔的 RELAY_KEY 抄到下面（與 iStudy Server App「Email 報告」的代寄金鑰相同）；確認 RELAY_TO。
 *   2. 上傳覆蓋 https://work.eachcore.com/istudy-mail-relay.php。
 *   3. 在 Server App「Email 報告」按「寄測試信」，到 Gmail 確認收件。
 *
 * 安全：只收 POST；金鑰不對一律拒絕（錯誤訊息不含金鑰）；只寄給 RELAY_TO；最多 3MB；
 *       主旨、檔名去除換行與控制字元，收件人與寄件地址都先驗證格式（防止標頭注入）；不記錄任何內容。
 * 相容 PHP 7.4 以上。不修改、不依賴 Life Dashboard 的任何檔案。
 */
declare(strict_types=1);

const RELAY_KEY = 'CHANGE-ME-至少 24 個字的隨機金鑰';     // 與 iStudy Server App 設定的代寄金鑰相同
const RELAY_TO = ['eachcore.app@gmail.com'];                // 只能寄給這些地址
const RELAY_FROM_ADDR = 'no-reply@work.eachcore.com';       // 寄件地址（也用作 -f 信封寄件人）；用網站本身的網域
const RELAY_FROM_NAME = 'iStudy Server';                    // 寄件人顯示名稱
const RELAY_MAX_BYTES = 3 * 1024 * 1024;
const RELAY_VERSION = 'relay-v2';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function out(int $code, array $body): void
{
    http_response_code($code);
    echo json_encode($body + ['relay' => RELAY_VERSION], JSON_UNESCAPED_UNICODE);
    exit;
}

/** 合法、適合放在標頭與 sendmail 參數的地址（只允許常見字元，沒有空白、引號、角括號）。 */
function addr_ok(string $a): bool
{
    return strlen($a) <= 254 && filter_var($a, FILTER_VALIDATE_EMAIL) !== false
        && preg_match('/^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/', $a) === 1;
}

/** 轉成合法 UTF-8（無效位元組丟掉）。 */
function utf8(string $s): string
{
    if (preg_match('//u', $s) === 1) return $s;
    if (function_exists('mb_convert_encoding')) return (string)mb_convert_encoding($s, 'UTF-8', 'UTF-8');
    return (string)preg_replace('/[^\x09\x0A\x0D\x20-\x7E]/', '?', $s);
}

/** 單行文字：去除 CR、LF 與其他控制字元，限制長度（以字元計）。 */
function one_line(string $s, int $max): string
{
    $s = trim((string)preg_replace('/[\x00-\x1F\x7F]+/u', ' ', utf8(substr($s, 0, $max * 4 + 4))));
    $chars = preg_split('//u', $s, -1, PREG_SPLIT_NO_EMPTY) ?: [];
    return implode('', array_slice($chars, 0, $max));
}

/** RFC 2047 標頭編碼：純 ASCII 原樣；含中文時切成多段 =?UTF-8?B?…?=（每段不超過 75 字元、不切斷字元）。 */
function enc_header(string $s): string
{
    if (preg_match('/^[\x20-\x7E]*$/', $s) === 1) return $s;
    $parts = [];
    $buf = '';
    foreach (preg_split('//u', $s, -1, PREG_SPLIT_NO_EMPTY) ?: [] as $ch) {
        if (strlen($buf . $ch) > 45) {
            $parts[] = '=?UTF-8?B?' . base64_encode($buf) . '?=';
            $buf = '';
        }
        $buf .= $ch;
    }
    if ($buf !== '') $parts[] = '=?UTF-8?B?' . base64_encode($buf) . '?=';
    return implode(' ', $parts);
}

/** 8bit 內文：換行統一為 CRLF（與 PHP 產生的標頭一致）、去除 NUL、每行不超過 900 位元組（郵件規定一行最多 998）。 */
function body_8bit(string $text): string
{
    $text = str_replace(["\r\n", "\r", "\0"], ["\n", "\n", ''], utf8($text));
    $out = [];
    foreach (explode("\n", $text) as $line) {
        while (strlen($line) > 900) {
            $cut = '';
            foreach (preg_split('//u', $line, -1, PREG_SPLIT_NO_EMPTY) ?: [] as $ch) {
                if (strlen($cut . $ch) > 900) break;
                $cut .= $ch;
            }
            if ($cut === '') break;
            $out[] = $cut;
            $line = (string)substr($line, strlen($cut));
        }
        $out[] = $line;
    }
    return rtrim(implode("\r\n", $out)) . "\r\n";
}

// ---------- 檢查請求 ----------
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') out(405, ['ok' => false, 'error' => '只接受 POST']);
if (strpos(RELAY_KEY, 'CHANGE-ME') === 0 || strlen(RELAY_KEY) < 24) out(503, ['ok' => false, 'error' => '代寄程式尚未設定金鑰']);
if (!addr_ok(RELAY_FROM_ADDR)) out(503, ['ok' => false, 'error' => '代寄程式的寄件地址設定不正確']);
if (!hash_equals(RELAY_KEY, (string)($_SERVER['HTTP_X_ISTUDY_KEY'] ?? ''))) out(403, ['ok' => false, 'error' => '金鑰錯誤']);
$raw = file_get_contents('php://input', false, null, 0, RELAY_MAX_BYTES + 1);
if ($raw === false || strlen($raw) > RELAY_MAX_BYTES) out(413, ['ok' => false, 'error' => '內容超過 3MB']);
$d = json_decode($raw, true);
if (!is_array($d) || !is_string($d['subject'] ?? null) || !is_string($d['text'] ?? null)) out(400, ['ok' => false, 'error' => '格式錯誤']);

$subject = one_line($d['subject'], 200);
if ($subject === '') $subject = 'iStudy';
$text = $d['text'];
if (strlen($text) > 600000) $text = function_exists('mb_strcut') ? mb_strcut($text, 0, 600000, 'UTF-8') : substr($text, 0, 600000);
$text = body_8bit($text);

// 收件人：只能是 RELAY_TO 裡的地址（請求沒有指定時寄給全部 RELAY_TO）
$allow = array_values(array_filter(RELAY_TO, 'addr_ok'));
$want = is_array($d['to'] ?? null) ? array_map('strtolower', array_filter($d['to'], 'is_string')) : [];
$to = array_values(array_filter($allow, static function ($a) use ($want) { return !$want || in_array(strtolower($a), $want, true); }));
if (!$to) out(400, ['ok' => false, 'error' => '收件人不在代寄程式允許的清單']);

// ---------- 組信 ----------
$host = substr(RELAY_FROM_ADDR, strpos(RELAY_FROM_ADDR, '@') + 1);
$headers = [
    'From: ' . enc_header(one_line(RELAY_FROM_NAME, 60)) . ' <' . RELAY_FROM_ADDR . '>',
    'MIME-Version: 1.0',
    'Date: ' . date('r'),
    'Message-ID: <' . bin2hex(random_bytes(12)) . '.' . time() . '@' . $host . '>',
];
$att = is_string($d['attachment_html'] ?? null) && $d['attachment_html'] !== '' ? $d['attachment_html'] : null;
if ($att === null) {
    $headers[] = 'Content-Type: text/plain; charset=UTF-8';
    $headers[] = 'Content-Transfer-Encoding: 8bit';
    $body = $text;
} else {
    $name = (string)preg_replace('/[^A-Za-z0-9._-]/', '_', one_line((string)($d['attachment_name'] ?? ''), 80));
    if ($name === '' || !preg_match('/\.html?$/i', $name)) $name = 'istudy-report.html';
    $b = 'istudy-' . bin2hex(random_bytes(12));
    $headers[] = "Content-Type: multipart/mixed; boundary=\"{$b}\"";
    $body = "This is a multi-part message in MIME format.\r\n\r\n"
        . "--{$b}\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n{$text}\r\n"
        . "--{$b}\r\nContent-Type: text/html; charset=UTF-8; name=\"{$name}\"\r\nContent-Disposition: attachment; filename=\"{$name}\"\r\n"
        . "Content-Transfer-Encoding: base64\r\n\r\n" . chunk_split(base64_encode(utf8($att)), 76, "\r\n")
        . "--{$b}--\r\n";
}

// ---------- 寄出：先帶 -f（與 Life Dashboard 相同），主機不接受時不帶 -f 再試一次 ----------
$toList = implode(', ', $to);
$subj = enc_header($subject);
$hdr = implode("\r\n", $headers);
$envelope = '-f ' . RELAY_FROM_ADDR;
$ok = @mail($toList, $subj, $body, $hdr, '-f' . RELAY_FROM_ADDR);
if (!$ok) {
    $envelope = '主機預設（-f 沒有被接受）';
    $ok = @mail($toList, $subj, $body, $hdr);
}
if (!$ok) out(502, ['ok' => false, 'error' => '網站主機的 mail() 沒有接受寄送（帶 -f 與不帶 -f 都試過；請向主機商確認寄信設定）']);
out(200, ['ok' => true, 'accepted' => true, 'to' => $to, 'envelope' => $envelope, 'from' => RELAY_FROM_ADDR,
    'note' => '網站主機已接受寄送；不代表 Gmail 已收到，請到收件匣（含垃圾郵件）確認']);
