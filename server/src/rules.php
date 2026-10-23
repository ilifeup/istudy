<?php
declare(strict_types=1);

/** 純函式規則：驗證、Tag、字幕格式、自然排序、路徑。不碰資料庫，方便單元測試。 */

// ---------- 帳號 ----------

/**
 * 帳號名稱（dev.55 FILE-02）：中文（漢字）、半形英文字母、數字，另保留既有的 _ . -；2～80 字（以字數計）。
 * 傳入值應先經 Unicode NFC 正規化（見 username_nfc()）；全形英數、空白、符號一律不接受。
 */
const USERNAME_RULE_TEXT = '帳號需為 2～80 字：中文、半形英文字母、數字（可含 _ . -）';

function valid_username(mixed $v): ?string
{
    if (!is_string($v)) return null;
    $v = trim($v);
    if (!mb_check_encoding($v, 'UTF-8')) return null;
    return preg_match('/^[\p{Han}A-Za-z0-9_.\-]{2,80}$/u', $v) ? $v : null;
}

function valid_password(mixed $v): ?string
{
    if (!is_string($v)) return null;
    if (strlen($v) < 8 || strlen($v) > 200) return null;
    if (preg_match('/[\x00-\x1F\x7F]/', $v)) return null;
    return $v;
}

function valid_role(mixed $v): ?string
{
    return is_string($v) && in_array($v, ['user', 'admin', 'super'], true) ? $v : null;
}

function valid_date(mixed $v): ?string
{
    if ($v === null || $v === '') return null;
    if (!is_string($v) || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $v)) return null;
    [$y, $m, $d] = array_map('intval', explode('-', $v));
    return checkdate($m, $d, $y) ? $v : null;
}

// ---------- Tag（沿用 dev.45） ----------

function valid_tag_input(mixed $value): ?string
{
    if (!is_string($value)) return null;
    $tag = preg_replace('/\s+/u', '', $value);
    if ($tag === null) return null;
    return $tag !== '' && preg_match('//u', $tag) === 1 && preg_match_all('/./us', $tag) <= 60 ? $tag : null;
}

function update_tag_names(array $names, string $method, string $tag, string $newTag = ''): array
{
    if ($method === 'POST') {
        if (!in_array($tag, $names, true)) $names[] = $tag;
    } elseif ($method === 'PATCH') {
        if (!in_array($tag, $names, true)) throw new InvalidArgumentException('Tag not found');
        $names = array_map(static fn($name) => $name === $tag ? $newTag : $name, $names);
    } elseif ($method === 'DELETE') {
        if (!in_array($tag, $names, true)) throw new InvalidArgumentException('Tag not found');
        $names = array_values(array_filter($names, static fn($name) => $name !== $tag));
    } else {
        throw new InvalidArgumentException('Unsupported tag action');
    }
    $names = array_values(array_unique($names));
    sort($names, SORT_STRING);
    return $names;
}

/** PostgreSQL text[] 字面值 → PHP 陣列（只用在 to_json 不方便的地方）。 */
function pg_text_array(?string $v): array
{
    if ($v === null || $v === '' || $v === '{}') return [];
    $d = json_decode($v, true);
    return is_array($d) ? $d : [];
}

// ---------- 自然排序（0001 → 0002 → 0010） ----------

function natural_compare(string $a, string $b): int
{
    $r = strnatcasecmp($a, $b);
    return $r !== 0 ? $r : strcmp($a, $b);
}

function natural_sort_by(array $rows, string $key): array
{
    usort($rows, static fn($x, $y) => natural_compare((string)$x[$key], (string)$y[$key]));
    return $rows;
}

// ---------- 路徑 ----------

/** 相對路徑：不可為絕對路徑、不可含 ..、反斜線與控制字元。 */
function safe_rel_path(mixed $v): ?string
{
    if (!is_string($v) || $v === '' || strlen($v) > 2048) return null;
    if (str_starts_with($v, '/') || str_contains($v, '\\') || preg_match('/[\x00-\x1F\x7F]/', $v)) return null;
    foreach (explode('/', $v) as $part) {
        if ($part === '' || $part === '.' || $part === '..') return null;
    }
    return $v;
}

/** 以 realpath 確認檔案在允許的根目錄內（沿用 dev.45 media_path）。 */
function resolve_under(string $root, string $rel): ?string
{
    $rootReal = realpath($root);
    if ($rootReal === false) return null;
    if (safe_rel_path($rel) === null) return null;
    $cand = realpath($rootReal . '/' . $rel);
    if ($cand === false || !str_starts_with($cand, $rootReal . DIRECTORY_SEPARATOR) || !is_file($cand)) return null;
    return $cand;
}

/** dev.59：教材根目錄就是 /Media。第一層這些名稱是系統資料夾（不分大小寫），不能當教材路徑；與 Agent util.SYSTEM_TOP_DIRS 相同。 */
const SYSTEM_TOP_DIRS = ['playback', 'subtitles', 'doc-cache', 'backups', 'installers', 'lost+found', '$recycle.bin', 'system volume information',
    'outgo'];   // dev.61：/Media/Outgo＝Super 的錄音轉文字工作區，整個目錄樹都不是教材

/** 教材相對路徑可以用嗎：格式正確、第一層不是系統資料夾、也不是隱藏項目。 */
function library_rel_ok(string $rel): bool
{
    if (safe_rel_path($rel) === null) return false;
    $top = explode('/', $rel, 2)[0];
    return !str_starts_with($top, '.') && !in_array(mb_strtolower($top), SYSTEM_TOP_DIRS, true);
}

/** dev.61：Outgo 底下的相對路徑可以用嗎：格式正確、每一層都不是隱藏項目（.開頭，含 ASR 狀態檔）、沒有控制字元。 */
function outgo_rel_ok(string $rel): bool
{
    if (safe_rel_path($rel) === null) return false;
    foreach (explode('/', $rel) as $part) {
        if (str_starts_with($part, '.')) return false;
    }
    return true;
}

/** X-Accel-Redirect 需要 URL 編碼的路徑（中文檔名、空白）。 */
function accel_path(string $prefix, string $rel): string
{
    return rtrim($prefix, '/') . '/' . implode('/', array_map('rawurlencode', explode('/', $rel)));
}

function mime_for_ext(string $ext): string
{
    return match (strtolower($ext)) {
        'mp4', 'm4v' => 'video/mp4',
        'webm' => 'video/webm',
        'pdf' => 'application/pdf',
        'jpg', 'jpeg' => 'image/jpeg',
        'png' => 'image/png',
        'gif' => 'image/gif',
        'webp' => 'image/webp',
        'bmp' => 'image/bmp',
        'txt' => 'text/plain; charset=utf-8',
        'vtt' => 'text/vtt; charset=utf-8',
        default => 'application/octet-stream',
    };
}

// ---------- MAC ----------

function normalize_mac(mixed $v): ?string
{
    if (!is_string($v)) return null;
    $hex = strtolower(preg_replace('/[^0-9a-fA-F]/', '', $v) ?? '');
    if (strlen($hex) !== 12 || $hex === '000000000000' || $hex === 'ffffffffffff') return null;
    return implode(':', str_split($hex, 2));
}

function valid_ipv4(mixed $v): ?string
{
    return is_string($v) && filter_var($v, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) ? $v : null;
}

/** LAN IP（dev.55 WOL-02）：私有網段的單一主機位址（不接受 .0／.255 結尾與 255.255.255.255）。 */
function valid_lan_ipv4(mixed $v): ?string
{
    if (!is_string($v) || !filter_var($v, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) return null;
    if (filter_var($v, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4 | FILTER_FLAG_NO_PRIV_RANGE) !== false) return null; // 不是私有網段
    $last = (int)substr($v, strrpos($v, '.') + 1);
    return $last === 0 || $last === 255 ? null : $v;
}

/** 只接受區域網路來源（心跳、免登入喚醒、Client Log）。 */
function is_lan_ip(string $ip): bool
{
    if ($ip === '') return false;
    if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        $long = ip2long($ip);
        foreach ([['10.0.0.0', 8], ['172.16.0.0', 12], ['192.168.0.0', 16], ['127.0.0.0', 8], ['169.254.0.0', 16]] as [$net, $bits]) {
            $mask = -1 << (32 - $bits);
            if (($long & $mask) === (ip2long($net) & $mask)) return true;
        }
        return false;
    }
    if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
        $l = strtolower($ip);
        return $l === '::1' || str_starts_with($l, 'fe80:') || str_starts_with($l, 'fc') || str_starts_with($l, 'fd');
    }
    return false;
}

// ---------- 字幕（SRT／VTT） ----------

function parse_timestamp_ms(string $t): ?int
{
    $t = trim(str_replace(',', '.', $t));
    if (!preg_match('/^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?$/', $t, $m)) return null;
    $h = $m[1] !== '' ? (int)$m[1] : 0;
    $ms = isset($m[4]) && $m[4] !== '' ? (int)str_pad($m[4], 3, '0') : 0;
    return (($h * 60 + (int)$m[2]) * 60 + (int)$m[3]) * 1000 + $ms;
}

function format_vtt_time(int $ms): string
{
    $h = intdiv($ms, 3600000);
    $m = intdiv($ms % 3600000, 60000);
    $s = intdiv($ms % 60000, 1000);
    return sprintf('%02d:%02d:%02d.%03d', $h, $m, $s, $ms % 1000);
}

function format_srt_time(int $ms): string
{
    return str_replace('.', ',', format_vtt_time($ms));
}

/**
 * 解析 SRT 或 VTT 文字，回傳 [[start_ms, end_ms, text], ...]。
 * 格式錯誤（沒有任何片段）回傳空陣列。
 */
function parse_subtitles(string $content): array
{
    $content = preg_replace('/^\xEF\xBB\xBF/', '', $content) ?? $content;
    $content = str_replace(["\r\n", "\r"], "\n", $content);
    $blocks = preg_split("/\n{2,}/", trim($content)) ?: [];
    $cues = [];
    foreach ($blocks as $block) {
        $lines = explode("\n", trim($block));
        if (!$lines || str_starts_with($lines[0], 'WEBVTT') || str_starts_with($lines[0], 'NOTE')
            || str_starts_with($lines[0], 'STYLE') || str_starts_with($lines[0], 'REGION')) continue;
        $i = 0;
        if (!str_contains($lines[0], '-->')) $i = 1; // 序號或 cue id
        if (!isset($lines[$i]) || !str_contains($lines[$i], '-->')) continue;
        [$a, $b] = array_map('trim', explode('-->', $lines[$i], 2));
        $b = preg_split('/\s+/', $b)[0] ?? $b; // 去掉 VTT cue 設定
        $start = parse_timestamp_ms($a);
        $end = parse_timestamp_ms($b);
        if ($start === null || $end === null || $end < $start) continue;
        $text = trim(implode("\n", array_slice($lines, $i + 1)));
        $text = trim(strip_tags($text));
        if ($text === '') continue;
        $cues[] = [$start, $end, $text];
    }
    usort($cues, static fn($x, $y) => $x[0] <=> $y[0]);
    return $cues;
}

function cues_to_vtt(array $cues): string
{
    $out = "WEBVTT\n\n";
    foreach ($cues as [$s, $e, $t]) {
        $out .= format_vtt_time($s) . ' --> ' . format_vtt_time($e) . "\n" . $t . "\n\n";
    }
    return $out;
}

function cues_to_srt(array $cues): string
{
    $out = '';
    foreach (array_values($cues) as $i => [$s, $e, $t]) {
        $out .= ($i + 1) . "\n" . format_srt_time($s) . ' --> ' . format_srt_time($e) . "\n" . $t . "\n\n";
    }
    return $out;
}

// ---------- 文字檔編碼（UTF-8／Big5） ----------

function text_to_utf8(string $raw): string
{
    $raw = preg_replace('/^\xEF\xBB\xBF/', '', $raw) ?? $raw;
    if (mb_check_encoding($raw, 'UTF-8')) return $raw;
    foreach (['BIG-5', 'CP950', 'GB18030'] as $enc) {
        $conv = @mb_convert_encoding($raw, 'UTF-8', $enc);
        if (is_string($conv) && mb_check_encoding($conv, 'UTF-8')) return $conv;
    }
    return mb_convert_encoding($raw, 'UTF-8', 'ISO-8859-1');
}

// ---------- 版本與版別 ----------

const CLIENT_CHANNELS = ['win7', 'win10', 'win11', 'ubuntu'];
/** dev.61 Client 回報的更新狀態（apps/client/electron/updater.ts 的 UpdateState） */
const CLIENT_UPDATE_STATES = ['disabled', 'manual', 'checking', 'up_to_date', 'downloading', 'downloaded', 'installing', 'installed', 'failed'];

/** 由作業系統字串推測應安裝的版別（Windows 組建號 22000 以上為 Win11）。 */
function channel_for_os(string $os): ?string
{
    $l = strtolower($os);
    if (str_contains($l, 'ubuntu') || str_contains($l, 'linux')) return 'ubuntu';
    if (preg_match('/windows[^0-9]*(?:nt\s*)?(\d+)\.(\d+)(?:\.(\d+))?/', $l, $m)) {
        $major = (int)$m[1];
        $minor = (int)$m[2];
        $build = isset($m[3]) ? (int)$m[3] : 0;
        if ($major === 6 && $minor === 1) return 'win7';
        if ($major === 10) return $build >= 22000 ? 'win11' : 'win10';
    }
    if (str_contains($l, 'windows 7')) return 'win7';
    if (str_contains($l, 'windows 11')) return 'win11';
    if (str_contains($l, 'windows 10')) return 'win10';
    return null;
}

/** 頁尾顯示：主.次，修訂號不為 0 時一併顯示；開發版顯示完整版本。 */
function display_version(string $v): string
{
    if (str_contains($v, '-')) return 'V' . $v;
    $p = explode('.', $v);
    if (count($p) >= 3 && $p[2] !== '0') return 'V' . $p[0] . '.' . $p[1] . '.' . $p[2];
    return 'V' . ($p[0] ?? '0') . '.' . ($p[1] ?? '0');
}

function channel_label(string $ch): string
{
    return match ($ch) { 'win7' => 'Win7', 'win10' => 'Win10', 'win11' => 'Win11', 'ubuntu' => 'Ubuntu', default => $ch };
}
