<?php
declare(strict_types=1);

/** iStudy Server API 入口（Nginx → PHP-FPM）。所有 /api/* 都由這裡處理。 */

$src = getenv('ISTUDY_SRC') ?: dirname(__DIR__) . '/src';
require $src . '/core.php';
require $src . '/auth.php';
require $src . '/rules.php';
require $src . '/perms.php';
require $src . '/router.php';
foreach (['public', 'me', 'catalog', 'personal', 'manage', 'admin', 'email', 'outgo', 'reports'] as $r) {
    require $src . '/routes/' . $r . '.php';
}
date_default_timezone_set('Asia/Taipei');
dispatch();
