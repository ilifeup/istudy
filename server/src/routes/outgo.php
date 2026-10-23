<?php
declare(strict_types=1);

/**
 * dev.61 P0-3：Outgo（Super 專用）——瀏覽 /Media/Outgo、對錄音排入「ASR 轉文字」、預覽與下載逐字稿（TXT、SRT）。
 *
 * - 只走本機管理埠（127.0.0.1:4081）並且要 Super：require_local_super()；LAN 的 Nginx 對 /api/admin/ 一律 404。
 * - Outgo 不是教材：不寫 media、folders、subtitles 等資料表；工作狀態在錄音旁邊的隱藏檔「.<檔名>.asr.json」，
 *   jobs 只用來排隊（Agent 做完就刪除那一筆）。
 * - 所有路徑都經 outgo_path()：限制在 Outgo 內、拒絕隱藏項目與任何一層符號連結。PHP 對 /Media 只有唯讀，結果由 Agent 寫入。
 */

const OUTGO_AUDIO_EXT = ['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'oga', 'opus', 'wma', 'aif', 'aiff', 'amr', 'mka', 'ac3', 'wv', '3ga'];
const OUTGO_TXT = '.asr.txt';
const OUTGO_SRT = '.asr.srt';
const OUTGO_MAX_ENTRIES = 5000;

function outgo_is_audio(string $name): bool
{
    return in_array(strtolower(pathinfo($name, PATHINFO_EXTENSION)), OUTGO_AUDIO_EXT, true);
}

/** 錄音的狀態：Agent 的狀態檔＋排隊中的工作（jobs 只在排隊與執行期間存在）。 */
function outgo_audio_state(string $full, string $rel, array $active): array
{
    $dir = dirname($full);
    $name = basename($full);
    $state = null;
    $sf = "$dir/.$name.asr.json";
    if (is_file($sf) && !is_link($sf) && filesize($sf) < 65536) {
        $v = json_decode((string)file_get_contents($sf), true);
        $state = is_array($v) ? $v : null;
    }
    $txt = $full . OUTGO_TXT;
    $srt = $full . OUTGO_SRT;
    $out = [
        'state' => 'none', 'percent' => null, 'error' => null, 'engine' => null, 'finished_at' => null, 'segments' => null,
        'audio_sec' => null, 'rtf' => null, 'job_id' => null, 'quality' => null, 'quality_reasons' => [],
        'has_txt' => is_file($txt) && !is_link($txt), 'has_srt' => is_file($srt) && !is_link($srt),
    ];
    if ($state) {
        $last = is_array($state['last_done'] ?? null) ? $state['last_done'] : [];
        $done = ($state['state'] ?? '') === 'done' ? $state : $last;
        $out['engine'] = $done['engine'] ?? null;
        $out['finished_at'] = $done['finished_at'] ?? null;
        $out['segments'] = isset($done['segments']) ? (int)$done['segments'] : null;
        $out['audio_sec'] = $done['audio_sec'] ?? null;
        $out['rtf'] = $done['rtf'] ?? null;
        $out['quality'] = in_array($done['quality'] ?? null, ['high', 'medium', 'low', 'unknown'], true) ? $done['quality'] : null;
        $out['quality_reasons'] = array_values(array_filter(is_array($done['quality_reasons'] ?? null) ? $done['quality_reasons'] : [], 'is_string'));
        $out['state'] = in_array($state['state'] ?? '', ['running', 'done', 'failed'], true) ? $state['state'] : 'none';
        if ($out['state'] === 'failed') $out['error'] = mb_substr((string)($state['error'] ?? ''), 0, 500);
    }
    if (isset($active[$rel])) {           // 排隊中或執行中：以 jobs 為準
        $j = $active[$rel];
        $out['state'] = $j['status'] === 'running' ? 'running' : 'queued';
        $out['job_id'] = (int)$j['id'];
        $res = jdec($j['result'] ?? null, []);
        $out['percent'] = isset($res['percent']) ? (float)$res['percent'] : null;
    } elseif ($out['state'] === 'running') {
        $out['state'] = 'interrupted';    // 狀態檔寫著執行中，但已經沒有工作（Agent 重啟或工作被取消）
    }
    return $out;
}

function outgo_active_jobs(): array
{
    $out = [];
    foreach (q_all("SELECT id, status, payload, result FROM jobs WHERE type = 'outgo_asr' AND status IN ('queued','running') ORDER BY id") as $j) {
        $p = jdec($j['payload'], []);
        if (isset($p['path']) && is_string($p['path'])) $out[$p['path']] = $j;
    }
    return $out;
}

route('GET', '/api/admin/outgo', static function (): array {
    require_local_super();
    $rel = (string)(Req::query('path', '') ?? '');
    $root = outgo_path('', true);
    if ($root === null) {
        return ['ready' => false, 'path' => '', 'entries' => [], 'error' => '找不到 /Media/Outgo（或它是符號連結）：請執行 sudo istudyctl media-acl --fix 或重新啟動 Agent 建立',
            'asr' => asr_settings(), 'engines' => asr_engines_status(), 'speed' => asr_speed(), 'audio_ext' => OUTGO_AUDIO_EXT];
    }
    $dir = outgo_path($rel, true) ?? fail(404, '找不到這個資料夾');
    $active = outgo_active_jobs();
    $names = @scandir($dir);
    if ($names === false) fail(500, '無法讀取資料夾');
    $entries = [];
    $skipped = 0;
    foreach ($names as $n) {
        if ($n === '.' || $n === '..' || str_starts_with($n, '.')) continue;          // 隱藏項目（含 ASR 狀態檔）不列出
        $full = "$dir/$n";
        $childRel = $rel === '' ? $n : "$rel/$n";
        if (is_link($full) || !outgo_rel_ok($childRel)) { $skipped++; continue; }     // 符號連結、不合法名稱
        if (count($entries) >= OUTGO_MAX_ENTRIES) { $skipped++; continue; }
        $st = @stat($full);
        if ($st === false) { $skipped++; continue; }
        if (is_dir($full)) {
            $entries[] = ['type' => 'dir', 'name' => $n, 'path' => $childRel, 'mtime' => date('c', $st['mtime'])];
            continue;
        }
        if (!is_file($full)) { $skipped++; continue; }
        $e = ['type' => 'file', 'name' => $n, 'path' => $childRel, 'ext' => strtolower(pathinfo($n, PATHINFO_EXTENSION)),
            'size' => (int)$st['size'], 'mtime' => date('c', $st['mtime']), 'audio' => outgo_is_audio($n), 'output_of' => null];
        foreach ([OUTGO_TXT, OUTGO_SRT] as $suf) {      // 「<錄音檔名>.asr.txt／.srt」：錄音還在時歸在錄音那一列
            if (str_ends_with($n, $suf) && outgo_is_audio(substr($n, 0, -strlen($suf))) && is_file("$dir/" . substr($n, 0, -strlen($suf)))) {
                $e['output_of'] = substr($n, 0, -strlen($suf));
            }
        }
        if ($e['audio']) $e['asr'] = outgo_audio_state($full, $childRel, $active);
        $entries[] = $e;
    }
    usort($entries, static fn($x, $y) => ($x['type'] === 'dir' ? 0 : 1) <=> ($y['type'] === 'dir' ? 0 : 1) ?: natural_compare($x['name'], $y['name']));
    return ['ready' => true, 'path' => $rel, 'entries' => $entries, 'skipped' => $skipped,
        'asr' => asr_settings(), 'engines' => asr_engines_status(), 'speed' => asr_speed(), 'audio_ext' => OUTGO_AUDIO_EXT];
});

route('POST', '/api/admin/outgo/asr', static function (): array {
    $a = require_local_super();
    $d = Req::body();
    $rel = is_string($d['path'] ?? null) ? $d['path'] : '';
    $full = outgo_path($rel) ?? fail(404, '找不到這個錄音檔');
    if (!outgo_is_audio($full)) fail(400, '不是支援的錄音格式（' . implode('、', OUTGO_AUDIO_EXT) . '）');
    $payload = ['path' => $rel];
    if (array_key_exists('engine', $d) && $d['engine'] !== null && $d['engine'] !== '') $payload['engine'] = asr_engine_choice($d['engine']);
    // 同一個檔案已在排隊或執行中：不重複排入
    $r = enqueue_unique('outgo_asr', ['path' => $rel], $payload, 30, (int)$a['id']);
    if (!$r['existing']) audit($a, 'outgo.asr_requested', ['job_id' => $r['job_id'], 'engine' => $payload['engine'] ?? 'default'], null, 'Outgo/' . $rel);
    return $r;
});

route('GET', '/api/admin/outgo/result', static function (): never {
    require_local_super();
    $rel = (string)(Req::query('path', '') ?? '');
    $format = Req::query('format', 'txt') === 'srt' ? 'srt' : 'txt';
    $audio = outgo_path($rel) ?? fail(404, '找不到這個錄音檔');
    $file = outgo_path($rel . ($format === 'srt' ? OUTGO_SRT : OUTGO_TXT)) ?? fail(404, '還沒有逐字稿（請先執行 ASR 轉文字）');
    if (filesize($file) > 32 * 1024 * 1024) fail(413, '逐字稿太大，請從 Outgo 資料夾直接開啟');
    send_cors_headers();          // Server App（app://istudy）用 fetch 下載：自己送出內容時也要加 CORS
    header('Content-Type: ' . ($format === 'srt' ? 'application/x-subrip' : 'text/plain') . '; charset=utf-8');
    header("Content-Disposition: attachment; filename*=UTF-8''" . rawurlencode(basename($file)));
    header('Cache-Control: no-store');
    readfile($file);
    exit;
});
