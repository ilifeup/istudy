<?php
declare(strict_types=1);
route('GET', '/api/page-atlas', static function (): array {
    require_role('super');
    $path = dirname(__DIR__) . '/private/page_atlas.json';
    if (!is_file($path) || is_link($path) || !is_readable($path)) fail(503, 'Page Atlas 私有索引尚未準備');
    $raw = file_get_contents($path);
    if ($raw === false) fail(503, 'Page Atlas 私有索引讀取失敗');
    $data = json_decode($raw, true);
    if (!is_array($data) || !isset($data['pages']) || !is_array($data['pages'])) fail(503, 'Page Atlas 私有索引格式錯誤');
    return ['pages' => $data['pages'], 'version' => $data['version'] ?? 1];
});
