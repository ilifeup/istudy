<?php
declare(strict_types=1);

/**
 * 核心：設定、資料庫、請求與回應、錯誤格式。
 * 所有 API 錯誤一律回 {"error":"…"}（可附 code 與其他欄位）與正確的 HTTP 狀態碼。
 */

final class ApiError extends Exception
{
    public function __construct(public readonly int $status, string $message, public readonly array $extra = [])
    {
        parent::__construct($message);
    }
}

function fail(int $status, string $message, array $extra = []): never
{
    throw new ApiError($status, $message, $extra);
}

/** dev.59：教材根目錄（/Media；之前是 /Media/videos）。 */
function library_root(): string
{
    return rtrim(env('MEDIA_ROOT', '/Media'), '/');
}

/** 教材相對路徑 → 實體檔案；第一層是系統資料夾、跳出根目錄或不存在時回傳 null。 */
function library_file(string $rel): ?string
{
    if (!library_rel_ok($rel)) return null;
    $file = resolve_under(library_root(), $rel);
    if ($file === null) return null;
    // dev.61：符號連結解開後也不能落在系統資料夾（例如 Outgo、playback）或隱藏項目
    $root = realpath(library_root());
    return $root !== false && library_rel_ok(substr($file, strlen($root) + 1)) ? $file : null;
}

/** dev.61：/Media/Outgo（Super 的錄音轉文字工作區；不是教材） */
function outgo_root(): string
{
    return library_root() . '/Outgo';
}

/**
 * Outgo 底下的相對路徑 → 實體路徑（$dir＝true 時要是資料夾，否則要是一般檔案）。
 * 拒絕跳出 Outgo、隱藏項目與任何一層符號連結（Outgo 本身是符號連結也拒絕）；空字串＝Outgo 本身。
 */
function outgo_path(string $rel, bool $dir = false): ?string
{
    $root = outgo_root();
    if (is_link($root) || !is_dir($root)) return null;
    $rootReal = realpath($root);
    if ($rootReal === false) return null;
    if ($rel === '') return $dir ? $rootReal : null;
    if (!outgo_rel_ok($rel)) return null;
    $joined = $rootReal . '/' . $rel;
    $real = realpath($joined);
    if ($real === false || $real !== $joined) return null;     // 有符號連結或不存在
    return ($dir ? is_dir($real) : is_file($real)) ? $real : null;
}

function env(string $key, string $default = ''): string
{
    $v = getenv($key);
    return ($v === false || $v === '') ? $default : $v;
}

/** VERSION.json：所有版本號的唯一來源（第 19 章）。 */
function version_info(): array
{
    static $info = null;
    if ($info === null) {
        $file = env('ISTUDY_VERSION_FILE', '/var/www/VERSION.json');
        $raw = is_file($file) ? file_get_contents($file) : false;
        $info = $raw ? (json_decode($raw, true) ?: []) : [];
        $info += ['server' => '0.0.0', 'api_level' => 1, 'min_api_level' => 1, 'client' => []];
    }
    return $info;
}

function db(): PDO
{
    static $pdo = null;
    if ($pdo === null) {
        $dsn = sprintf('pgsql:host=%s;port=%s;dbname=%s;connect_timeout=2',
            env('DB_HOST', 'db'), env('DB_PORT', '5432'), env('DB_NAME', 'istudy'));
        $pdo = new PDO($dsn, env('DB_USER', 'istudy'), env('DB_PASSWORD'), [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec("SET TIME ZONE 'Asia/Taipei'");
    }
    return $pdo;
}

/** 執行參數化 SQL，回傳 PDOStatement。 */
function q(string $sql, array $params = []): PDOStatement
{
    $st = db()->prepare($sql);
    foreach (array_values($params) as $i => $v) {
        $type = match (true) {
            is_bool($v) => PDO::PARAM_BOOL,
            is_int($v) => PDO::PARAM_INT,
            $v === null => PDO::PARAM_NULL,
            default => PDO::PARAM_STR,
        };
        $st->bindValue($i + 1, $v, $type);
    }
    $st->execute();
    return $st;
}

function q_one(string $sql, array $params = []): ?array
{
    $row = q($sql, $params)->fetch();
    return $row === false ? null : $row;
}

function q_all(string $sql, array $params = []): array
{
    return q($sql, $params)->fetchAll();
}

function q_val(string $sql, array $params = []): mixed
{
    $v = q($sql, $params)->fetchColumn();
    return $v === false ? null : $v;
}

function tx(callable $fn): mixed
{
    $pdo = db();
    $pdo->beginTransaction();
    try {
        $r = $fn();
        $pdo->commit();
        return $r;
    } catch (Throwable $e) {
        if ($pdo->inTransaction()) $pdo->rollBack();
        throw $e;
    }
}

/** PostgreSQL 時間字串 → ISO 8601（Client 用 Date 解析）。 */
function ts(?string $v): ?string
{
    if ($v === null || $v === '') return null;
    try {
        return (new DateTimeImmutable($v))->format(DATE_ATOM);
    } catch (Throwable) {
        return $v;
    }
}

/** JSON 欄位 → PHP 陣列 */
function jdec(?string $v, mixed $default = null): mixed
{
    if ($v === null || $v === '') return $default;
    $d = json_decode($v, true);
    return $d === null && $v !== 'null' ? $default : $d;
}

function jenc(mixed $v): string
{
    return json_encode($v, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
}

// ---------------- 請求 ----------------

final class Req
{
    public static string $method = 'GET';
    public static string $path = '/';
    public static ?array $json = null;
    public static array $params = [];

    public static function init(): void
    {
        self::$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
        $p = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
        self::$path = rawurldecode($p);
    }

    public static function body(): array
    {
        if (self::$json === null) {
            $raw = file_get_contents('php://input');
            if ($raw === '' || $raw === false) {
                self::$json = [];
            } else {
                $d = json_decode($raw, true);
                if (!is_array($d)) fail(400, '資料格式錯誤（需要 JSON）');
                self::$json = $d;
            }
        }
        return self::$json;
    }

    public static function query(string $key, ?string $default = null): ?string
    {
        $v = $_GET[$key] ?? $default;
        return is_string($v) ? $v : $default;
    }

    public static function header(string $name): string
    {
        $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
        $v = $_SERVER[$key] ?? '';
        if ($v === '' && strcasecmp($name, 'Authorization') === 0) {
            $v = $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
        }
        return is_string($v) ? $v : '';
    }

    public static function ip(): string
    {
        return (string)($_SERVER['REMOTE_ADDR'] ?? '');
    }

    /** Electron 主程序附上的電腦名稱（只用於記錄）。 */
    public static function pcName(): string
    {
        $n = self::header('X-iStudy-PC');
        $n = preg_replace('/[^\p{L}\p{N} ._\-]/u', '', $n) ?? '';
        return mb_substr($n, 0, 64);
    }

    /** 只綁 127.0.0.1:4081 的管理埠：由 Nginx 以 fastcgi_param 設定，用戶端無法偽造。 */
    public static function isLocalAdmin(): bool
    {
        $v = $_SERVER['ISTUDY_LOCAL_ADMIN'] ?? null;
        if ($v === null && PHP_SAPI === 'cli-server') $v = getenv('ISTUDY_LOCAL_ADMIN') ?: null;
        return $v === '1';
    }
}

function body_str(array $d, string $key, int $max = 2000, bool $trim = true): ?string
{
    if (!array_key_exists($key, $d)) return null;
    $v = $d[$key];
    if ($v === null) return null;
    if (!is_string($v)) fail(400, "欄位 $key 必須是文字");
    if ($trim) $v = trim($v);
    if (mb_strlen($v) > $max) fail(400, "欄位 $key 太長（最多 $max 字）");
    if (preg_match('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', $v)) fail(400, "欄位 $key 含有不允許的字元");
    return $v;
}

function body_int(array $d, string $key, ?int $min = null, ?int $max = null): ?int
{
    if (!array_key_exists($key, $d) || $d[$key] === null) return null;
    $v = $d[$key];
    if (is_string($v) && preg_match('/^-?\d+$/', $v)) $v = (int)$v;
    if (!is_int($v)) fail(400, "欄位 $key 必須是整數");
    if (($min !== null && $v < $min) || ($max !== null && $v > $max)) fail(400, "欄位 $key 超出範圍");
    return $v;
}

function body_bool(array $d, string $key): ?bool
{
    if (!array_key_exists($key, $d) || $d[$key] === null) return null;
    if (!is_bool($d[$key])) fail(400, "欄位 $key 必須是 true 或 false");
    return $d[$key];
}

function body_ids(array $d, string $key, int $maxCount = 2000): array
{
    $v = $d[$key] ?? [];
    if (!is_array($v) || count($v) > $maxCount) fail(400, "欄位 $key 必須是 ID 陣列");
    $out = [];
    foreach ($v as $id) {
        if (!is_int($id) || $id < 1) fail(400, "欄位 $key 含有無效的 ID");
        $out[$id] = $id;
    }
    return array_values($out);
}

function query_int(string $key, int $default, int $min, int $max): int
{
    $v = Req::query($key);
    if ($v === null || $v === '') return $default;
    if (!preg_match('/^\d+$/', $v)) fail(400, "參數 $key 必須是整數");
    return max($min, min($max, (int)$v));
}

// ---------------- 回應 ----------------

final class Resp
{
    public static array $headers = [];
}

function cors_origin(): ?string
{
    $origin = Req::header('Origin');
    if ($origin === '') return null;
    $allowed = array_filter(array_map('trim', explode(',', env('ISTUDY_CORS_ORIGINS', 'app://istudy'))));
    return in_array($origin, $allowed, true) ? $origin : null;
}

function send_cors_headers(): void
{
    $origin = cors_origin();
    if ($origin === null) return;
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Vary: Origin');
    header('Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS');
    header('Access-Control-Allow-Headers: Authorization, Content-Type, Range, X-iStudy-PC, X-iStudy-Channel');
    header('Access-Control-Expose-Headers: Content-Range, Accept-Ranges, Content-Length, Content-Disposition, Retry-After');
    header('Access-Control-Max-Age: 600');
}

function json_out(mixed $data, int $status = 200): never
{
    http_response_code($status);
    send_cors_headers();
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    foreach (Resp::$headers as $h) header($h);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function sys_log(string $level, string $message, array $context = []): void
{
    try {
        q('INSERT INTO system_logs(source, level, message, context) VALUES (?,?,?,CAST(? AS jsonb))',
            ['server', $level, mb_substr($message, 0, 2000), jenc($context)]);
    } catch (Throwable) {
        error_log('[istudy] ' . $message);
    }
}
