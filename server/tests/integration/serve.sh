#!/bin/sh
# 開發與測試用：以 PHP 內建伺服器模擬 Nginx 的兩個埠（沒有 Nginx 時）。
#   LAN 埠：$LAN_PORT（預設 18080）；Server App 管理埠：$ADMIN_PORT（預設 18081，設定 ISTUDY_LOCAL_ADMIN=1）
# 正式環境由 Nginx＋PHP-FPM 提供，ISTUDY_SENDFILE=nginx 時串流交給 X-Accel-Redirect。
set -eu
HERE=$(cd "$(dirname "$0")/../.." && pwd)
LAN_PORT=${LAN_PORT:-18080}
ADMIN_PORT=${ADMIN_PORT:-18081}
export ISTUDY_SENDFILE=${ISTUDY_SENDFILE:-php}
export PHP_CLI_SERVER_WORKERS=${PHP_CLI_SERVER_WORKERS:-4}
php -S 127.0.0.1:$LAN_PORT -t "$HERE/public" "$HERE/public/index.php" >"${LOG_DIR:-/tmp}/istudy-lan.log" 2>&1 &
echo $! > "${LOG_DIR:-/tmp}/istudy-lan.pid"
ISTUDY_LOCAL_ADMIN=1 php -S 127.0.0.1:$ADMIN_PORT -t "$HERE/public" "$HERE/public/index.php" >"${LOG_DIR:-/tmp}/istudy-admin.log" 2>&1 &
echo $! > "${LOG_DIR:-/tmp}/istudy-admin.pid"
sleep 0.5
