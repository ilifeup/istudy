<?php
declare(strict_types=1);

final class Router
{
    /** @var array<int, array{0: string[], 1: string, 2: callable}> */
    public static array $routes = [];
}

/**
 * 註冊路由。$pattern 中的 {name} 只接受正整數；{key} 接受英數與 _-。
 * 例：route('GET', '/api/media/{id}/stream', fn($p) => ...)
 */
function route(string $methods, string $pattern, callable $handler): void
{
    $regex = preg_replace_callback('/\{(\w+)\}/', static function ($m) {
        return $m[1] === 'key' ? '(?P<key>[A-Za-z0-9_\-]{1,64})' : '(?P<' . $m[1] . '>[1-9]\d{0,17})';
    }, $pattern);
    Router::$routes[] = [explode('|', $methods), '#^' . $regex . '$#', $handler];
}

function dispatch(): never
{
    Req::init();
    $method = Req::$method;
    $path = Req::$path;

    try {
        if ($method === 'OPTIONS') {
            http_response_code(204);
            send_cors_headers();
            exit;
        }
        // 管理 API 只在 127.0.0.1:4081（Nginx 對 LAN 的 :4080 也會擋，這裡再擋一次）
        if (str_starts_with($path, '/api/admin/') && !Req::isLocalAdmin()) fail(404, '找不到此功能');

        $allowed = [];
        foreach (Router::$routes as [$methods, $regex, $handler]) {
            if (!preg_match($regex, $path, $m)) continue;
            if (!in_array($method, $methods, true) && !($method === 'HEAD' && in_array('GET', $methods, true))) {
                $allowed = array_merge($allowed, $methods);
                continue;
            }
            $params = [];
            foreach ($m as $k => $v) {
                if (is_string($k)) $params[$k] = $k === 'key' ? $v : (int)$v;
            }
            $result = $handler($params);
            json_out($result ?? ['ok' => true]);
        }
        if ($allowed) {
            Resp::$headers[] = 'Allow: ' . implode(', ', array_unique($allowed));
            fail(405, '不支援此操作方式');
        }
        fail(404, '找不到此功能');
    } catch (ApiError $e) {
        json_out(['error' => $e->getMessage()] + $e->extra, $e->status);
    } catch (PDOException $e) {
        $code = (string)$e->getCode();
        try {
            if (db()->inTransaction()) db()->rollBack();
        } catch (Throwable) {
        }
        if ($code === '23505') json_out(['error' => '資料重複，請確認名稱或代號沒有和既有資料相同', 'code' => 'duplicate'], 409);
        if ($code === '23503') json_out(['error' => '資料仍被其他項目使用，無法刪除', 'code' => 'in_use'], 409);
        error_log('[istudy] DB error ' . $code . ': ' . $e->getMessage());
        sys_log('error', 'DB error ' . $code, ['path' => $path]);
        json_out(['error' => '伺服器發生錯誤，請稍後再試', 'code' => 'server_error'], 500);
    } catch (Throwable $e) {
        error_log('[istudy] ' . get_class($e) . ': ' . $e->getMessage() . ' @' . $e->getFile() . ':' . $e->getLine());
        sys_log('error', get_class($e) . ': ' . $e->getMessage(), ['path' => $path]);
        json_out(['error' => '伺服器發生錯誤，請稍後再試', 'code' => 'server_error'], 500);
    }
}

/** 排入 Agent 工作；PHP 不執行任何系統指令（第 04 章）。 */
function enqueue_job(string $type, array $payload = [], int $priority = 0, ?int $by = null, ?string $runAfter = null): int
{
    return (int)q_val(
        'INSERT INTO jobs(type, payload, priority, created_by, run_after)
         VALUES (?, CAST(? AS jsonb), ?, ?, COALESCE(CAST(? AS timestamptz), NOW())) RETURNING id',
        [$type, jenc($payload ?: new stdClass()), $priority, $by, $runAfter]);
}

/** 若已有相同工作在等待或執行中，回傳其 ID，不重複排入。 */
function enqueue_unique(string $type, array $match, array $payload = [], int $priority = 0, ?int $by = null): array
{
    $existing = q_val("SELECT id FROM jobs WHERE type = ? AND status IN ('queued','running') AND payload @> CAST(? AS jsonb)
                       ORDER BY id LIMIT 1", [$type, jenc($match ?: new stdClass())]);
    if ($existing) return ['job_id' => (int)$existing, 'existing' => true];
    return ['job_id' => enqueue_job($type, $payload ?: $match, $priority, $by), 'existing' => false];
}

function job_out(array $j): array
{
    $payload = jdec($j['payload'] ?? null, []);
    if (is_array($payload) && isset($payload['uem_html'])) {   // dev.57：Email 附件不回傳內容
        unset($payload['uem_html']);
        $payload['attachment'] = true;
    }
    return [
        'id' => (int)$j['id'],
        'type' => $j['type'],
        'status' => $j['status'],
        'priority' => (int)$j['priority'],
        'payload' => $payload,
        'attempts' => (int)$j['attempts'],
        'created_at' => ts($j['created_at']),
        'started_at' => ts($j['started_at']),
        'finished_at' => ts($j['finished_at']),
        'run_after' => ts($j['run_after']),
        'result' => jdec($j['result'] ?? null),
        'error' => $j['error'],
    ];
}
