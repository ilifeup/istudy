#!/bin/sh
# iStudy 離線部署套件入口（第 17 章）
#   sudo ./install.sh          驗證套件、安裝前檢查、安裝 istudyctl、polkit 規則與 Server App，之後在 Server App 按「開始部署」
#   sudo ./install.sh --cli    直接在終端機執行 bootstrap（桌面無法使用時）；其餘參數會交給 istudyctl bootstrap
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "$(id -u)" != "0" ]; then
  echo "請用 sudo 執行：sudo ./install.sh" >&2
  exit 1
fi
cd "$HERE"
echo "[1/5] 驗證套件檔案…"
for f in SHA256SUMS VERSION.json images/istudy-images.tar images/manifest.json istudyctl/istudyctl.py stack/compose.yaml stack/istudyctl.sh stack/polkit/tw.istudy.ctl.policy; do
  if [ ! -s "$f" ]; then
    echo "✗ 套件缺少必要檔案：$f（不是完整的離線部署套件；請依 DEPLOY_DEV53.md 重新產生）" >&2
    exit 2
  fi
done
if ! sha256sum --quiet -c SHA256SUMS; then
  echo "✗ 套件檔案不完整或內容不符，請重新複製 USB" >&2
  exit 2
fi
echo "[2/5] 安裝前檢查（PostgreSQL 18、/Media、Docker Compose、連接埠、磁碟、桌面）…"
# 只做檢查、不安裝：有 ✗ 時停止；有 ⚠ 時繼續（部署時會再要求確認）
if ! python3 -I "$HERE/istudyctl/istudyctl.py" bootstrap --check-only --bundle "$HERE"; then
  echo "✗ 安裝前檢查有必須先處理的項目（見上方 ✗）；沒有安裝任何 iStudy 程式。處理後再執行 sudo ./install.sh" >&2
  exit 3
fi
echo "[3/5] 安裝 istudyctl…"
install -d -o root -g root -m 755 /opt/istudy /opt/istudy/agent
rm -rf /opt/istudy/agent/istudy_common
cp -r istudyctl/istudyctl.py istudyctl/istudy_common /opt/istudy/agent/
chown -R root:root /opt/istudy/agent
chmod -R go-w /opt/istudy/agent
install -o root -g root -m 755 stack/istudyctl.sh /usr/local/sbin/istudyctl
echo "[4/5] 安裝 polkit 規則…"
install -d /usr/share/polkit-1/actions
install -o root -g root -m 644 stack/polkit/tw.istudy.ctl.policy /usr/share/polkit-1/actions/tw.istudy.ctl.policy
install -d -m 755 /etc/istudy
echo "$HERE" > /etc/istudy/last-bundle
echo "[5/5] 安裝 Server App…"
DEB=$(ls app/istudy-server-app_*_amd64.deb 2>/dev/null | tail -n 1 || true)
if [ -n "$DEB" ]; then
  # 離線安裝：只用本機已有的相依套件（Ubuntu 桌面版都已內建）
  if ! { apt-get install -y --no-download "$HERE/$DEB" >/dev/null 2>&1 || dpkg -i "$DEB"; }; then
    echo "⚠ Server App 安裝失敗（缺少桌面相依套件？）；仍可用 sudo ./install.sh --cli 部署" >&2
    APP_FAILED=1
  fi
else
  echo "⚠ 套件中沒有 Server App .deb（以 --no-server-app 產生的套件只能用 --cli 部署）" >&2
  APP_FAILED=1
fi
if ! command -v pkexec >/dev/null 2>&1; then
  if ls debs/pkexec_*.deb >/dev/null 2>&1; then
    dpkg -i debs/pkexec_*.deb || true
  fi
  command -v pkexec >/dev/null 2>&1 || echo "⚠ 沒有 pkexec：Server App 的主機維護按鈕無法使用（sudo apt install pkexec），可改用 sudo ./install.sh --cli" >&2
fi
# 系統密碼視窗由桌面上的 polkit 驗證代理負責（GNOME 內建；XFCE 需要另外安裝並自動啟動）
if ! pgrep -f 'polkit[-_a-z]*agent|xfce-polkit|lxpolkit|gnome-shell' >/dev/null 2>&1; then
  echo "⚠ 桌面上沒有執行 polkit 驗證代理：Server App 按「開始部署」時不會跳出系統密碼視窗。" >&2
  echo "  處理方式見 docs/DEPLOY.md「系統密碼視窗」；或直接在終端機部署：sudo ./install.sh --cli" >&2
fi
if [ "${1:-}" = "--cli" ]; then
  shift
  exec /usr/local/sbin/istudyctl bootstrap --bundle "$HERE" "$@"
fi
echo ""
if [ "${APP_FAILED:-0}" = "1" ]; then
  echo "⚠ istudyctl 與 polkit 已安裝，但 Server App 沒有安裝成功：請改用 sudo ./install.sh --cli 部署"
  exit 4
fi
echo "✓ 安裝完成（尚未部署）。請從應用程式選單開啟「iStudy Server App」，按「開始部署」。"
echo "  （桌面無法使用時：sudo ./install.sh --cli）"
