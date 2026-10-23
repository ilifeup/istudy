#!/bin/bash
# iStudy dev.61 → dev.62 增量更新：
#   - 永久刪除一致性（P0-A）：權限預檢、實際狀態驗證、沒有誤報「已復原」、Agent 重啟續做、同 ID 舊衍生檔隔離。
#   - 直接播放 MP4 可以產生字幕（P0-B）：轉檔佇列「完成」加「字幕」欄與「立即產生／重試字幕」，「立即執行」會把排隊中的字幕工作改成馬上執行。
#   - 教材目錄樹狀顯示（/Media 標頭、第一層區塊、子層引導線、根目錄檔案分區）。
#   - 方案 B 加密備份（age）：Agent 只用公鑰加密；解密驗證、還原測試、InterServer 遠端（FTPS）上傳、10～15 天保留清理由主機 istudyctl 執行；
#     新增 systemd 排程 istudy-backup.timer（每小時）。
#   - WOL：設備的新增、修改、刪除、登記、掃描只有 Super；登入畫面清單說明「已登記但未啟用喚醒」的電腦。
#   - Server App 登入頁「顯示密碼」；備份頁（恢復密碼、遠端、保留天數）。
#   - migration 0011：backup_history 加欄位（都有預設值）、jobs 多 4 種主機備份工作類型。不改既有資料。
#   - Agent 映像加入 age v1.2.1（隨原始碼附的官方靜態執行檔，核對 SHA-256；建映像不需要網路）；其餘沿用 dev.61 映像。
#   - Client 程式也有修改（版本 1.0.0-dev.62），但 Client 安裝檔不在這個腳本：在 Windows 打包後用 Server App「發布新版 Client」。
#
# 在 Server 上解壓縮 dev.62 原始碼包後，於解壓出的資料夾內執行：
#   sudo ./deploy/patch_dev62.sh check  [--old-deb 檔案]                 只檢查並列出影響範圍，不改任何東西
#   sudo ./deploy/patch_dev62.sh apply  --old-deb 檔案 [--restore-test]  自我檢查 → 備份 → 建映像 → 更新 → 重啟 → 自我檢查
#   sudo ./deploy/patch_dev62.sh rollback                                 回到 dev.61
#
#   只接受從 1.0.0-dev.61 更新。--old-deb＝dev.61 的 Server App 安裝檔（dev.61 原始碼包 dist/istudy-server-app_1.0.0-dev.61_amd64.deb）。
#
# 保證：
#   - 只建立新標籤 istudy/php:1.0.0-dev.62、istudy/agent:1.0.0-dev.62；dev.61（以及更早）的映像與回退檔都不刪除、不覆蓋。
#   - 任何一步失敗都會自動退回；無法自動退回的項目會明確列出。
#   - 不刪除、不重建 PostgreSQL；不改教材、字幕、ZFS、主機套件、網路、RustDesk。
#   - 既有備份（舊的 .tar.gz、istudy-db-*.sql）一律不動；rollback 也不刪除加密備份（.age）與 /etc/istudy/backup 的金鑰。
set -Eeuo pipefail

NEW=1.0.0-dev.62
FROM_OK="1.0.0-dev.61"   # 只從 dev.61 更新
OPT=${ISTUDY_PATCH_OPT:-/opt/istudy}                 # ISTUDY_PATCH_* 環境變數只供測試覆寫，正式執行不要設定
SAVE=${ISTUDY_PATCH_SAVE:-/var/lib/istudy/patch-dev62}
BK=${ISTUDY_PATCH_BACKUP_DIR:-/srv/istudy/backup}
ISTUDYCTL=${ISTUDY_PATCH_ISTUDYCTL:-/usr/local/sbin/istudyctl}
SYSTEMD=${ISTUDY_PATCH_SYSTEMD:-/etc/systemd/system}
KEYDIR=${ISTUDY_PATCH_KEYDIR:-/etc/istudy/backup}
HERE=$(cd "$(dirname "$0")/.." && pwd)
DEB="$HERE/dist/istudy-server-app_${NEW}_amd64.deb"
APP_PKG=istudy-server-app
AGE_TGZ=${ISTUDY_PATCH_AGE_TGZ:-$HERE/deploy/vendor/age-v1.2.1-linux-amd64.tar.gz}
AGE_SHA=7df45a6cc87d4da11cc03a539a7470c15b1041ab2b396af088fe9990f7c79d50
UNITS="istudy-backup.service istudy-backup.timer"

ACTION=${1:-check}
shift || true
OLD_DEB=""
RESTORE_TEST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --old-deb) OLD_DEB=${2:-}; shift 2 ;;
    --restore-test) RESTORE_TEST=1; shift ;;
    *) echo "不認得的參數：$1" >&2; exit 64 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*" >&2; }
IN_APPLY=0
die()  { # apply 進行到一半失敗時，先自動退回更新前版本再結束
  bad "$*"
  if [ "$IN_APPLY" = 1 ]; then
    IN_APPLY=0; trap - ERR
    say "== 更新失敗，自動退回 $OLD"
    rollback_files auto || true
  fi
  exit 1
}
dc()   { docker compose --project-directory "$OPT" --env-file "$OPT/.env" -f "$OPT/compose.yaml" "$@"; }
img_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }
env_get() { sed -n "s/^$1=//p" "$OPT/.env" 2>/dev/null | tail -n 1; }
env_set() { # 只改一行，其餘設定（含密碼）原封不動；不印出 .env 內容
  if grep -q "^$1=" "$OPT/.env"; then sed -i "s|^$1=.*|$1=$2|" "$OPT/.env"; else printf '%s=%s\n' "$1" "$2" >> "$OPT/.env"; fi
}
sql() { runuser -u postgres -- psql --cluster 18/main -X -d istudy -tA -c "$1" 2>/dev/null; }
super_count() { sql "SELECT count(*) FROM accounts WHERE role = 'super'" | tr -d '[:space:]'; }
app_version() { dpkg-query -W -f='${Version}' "$APP_PKG" 2>/dev/null || true; }
find_old_deb() {
  if [ -n "$OLD_DEB" ]; then echo "$OLD_DEB"; return; fi
  local b; b=$(cat /etc/istudy/last-bundle 2>/dev/null || true)
  for f in "$b/app/istudy-server-app_${OLD}_amd64.deb" "$b/dist/istudy-server-app_${OLD}_amd64.deb"; do
    [ -n "$b" ] && [ -f "$f" ] && { echo "$f"; return 0; }
  done
  return 0
}
deb_version() { dpkg-deb -f "$1" Version 2>/dev/null || true; }
ver_of() { python3 -I -c "import json,sys;print(json.load(open(sys.argv[1]))['server'])" "$1" 2>/dev/null || true; }
debver() { printf '%s' "$1" | sed 's/-dev\./~dev./'; }   # dpkg 版本寫法（1.0.0-dev.61 → 1.0.0~dev.61）
allowed_from() { case " $FROM_OK " in *" $1 "*) return 0 ;; esac; return 1; }
# 更新前版本：apply／check 取目前安裝的版本；已是 dev.62（或 rollback）時取 apply 保存的版本
CUR=$( [ -f "$OPT/VERSION.json" ] && ver_of "$OPT/VERSION.json" || true )
if [ "$CUR" = "$NEW" ] || [ "$ACTION" = rollback ]; then OLD=$( [ -f "$SAVE/VERSION.json" ] && ver_of "$SAVE/VERSION.json" || true ); else OLD=$CUR; fi
OLD=${OLD:-1.0.0-dev.61}
OLD_DEBVER=$(debver "$OLD")

# 自我檢查：istudyctl doctor 的結果分成 PASS／WARN／FAIL（SKIP＝不適用，例如 VA-API 可用時的 QSV）
doctor_run() { # $1＝輸出檔；回傳 0
  "$ISTUDYCTL" --json doctor > "$1" 2>/dev/null || true
}
doctor_summary() { # $1＝結果檔、$2＝基準檔（可空）；印出摘要，回傳 0 PASS、1 WARN、2 FAIL
  python3 -I - "$1" "${2:-}" <<'PY'
import json, sys
def load(p):
    out = []
    try:
        for ln in open(p, encoding="utf-8"):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("event") == "check":
                out.append(d)
    except OSError:
        pass
    return out
now, base = load(sys.argv[1]), (load(sys.argv[2]) if sys.argv[2] else [])
if not now:
    print("  ✗ 自我檢查沒有結果（istudyctl doctor 沒有執行成功）")
    sys.exit(2)
mark = {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "－"}
for d in now:
    print(f"   {mark.get(d.get('status'), '·')} {d.get('name')}" + (f"：{d.get('message')}" if d.get("message") else ""))
fails = [d.get("name") for d in now if d.get("status") == "fail"]
warns = [d.get("name") for d in now if d.get("status") == "warn"]
old_fails = {d.get("name") for d in base if d.get("status") == "fail"}
new_fails = [n for n in fails if n not in old_fails]
print(f"  自我檢查：PASS {sum(d.get('status') == 'pass' for d in now)}、WARN {len(warns)}、FAIL {len(fails)}、SKIP {sum(d.get('status') == 'skip' for d in now)}")
if fails:
    if base:
        print("  更新前就有的 FAIL：" + ("、".join(n for n in fails if n in old_fails) or "無"))
        print("  更新後新出現的 FAIL：" + ("、".join(new_fails) or "無"))
    sys.exit(2)
sys.exit(1 if warns else 0)
PY
}

[ "$(id -u)" = 0 ] || { echo "請用 sudo 執行" >&2; exit 1; }

# dev.61 Agent 映像應該已有的元件（dev.62 沿用；只加 age，建映像不需要網路）
BASE_TEST='[ -f /usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf ] && [ -f /etc/fonts/conf.d/64-istudy-cjk-tc.conf ] && ls /usr/lib/x86_64-linux-gnu/libmfx-gen.so.1.2* && /opt/venv/bin/python -c "import sherpa_onnx" && [ -f /opt/istudy-agent/worker/purge.py ]'
base_has() { # $1＝在 Agent 基底映像裡執行的 sh 指令（不連網）
  [ -n "$(img_id "istudy/agent:$OLD")" ] && docker run --rm --network none --entrypoint /bin/sh "istudy/agent:$OLD" -c "$1" >/dev/null 2>&1
}

check() {
  say "== 部署前檢查（不會修改任何東西）"
  local fail=0
  for f in .env compose.yaml VERSION.json agent/istudyctl.py; do
    [ -e "$OPT/$f" ] || { bad "找不到 $OPT/$f（這台不是已部署的 iStudy？）"; fail=1; }
  done
  [ $fail = 0 ] || exit 1
  local cur=$CUR
  if [ "$cur" = "$NEW" ]; then ok "目前已是 $NEW（要重新套用請先執行 rollback）"
  elif allowed_from "$cur"; then ok "目前版本 $cur（更新到 $NEW）"
  else bad "目前版本是「${cur:-讀不到}」，這個增量更新只適用於 ${FROM_OK// /、}"; fail=1; fi
  for r in istudy/php istudy/agent; do
    if [ -n "$(img_id "$r:$OLD")" ]; then ok "基底映像 $r:$OLD 存在（$(img_id "$r:$OLD" | cut -c1-19)）"
    else bad "找不到基底映像 $r:$OLD（請貼上 docker image ls 'istudy/*' 的結果）"; fail=1; fi
  done
  for f in "$HERE/deploy/patch/agent.Dockerfile" "$HERE/deploy/patch/php.Dockerfile" "$HERE/server/sql/migrations/0011_backup_v2.sql" \
           "$HERE/agent/istudy_common/backupcrypt.py" "$HERE/agent/istudy_common/agepass.py" "$HERE/agent/worker/maintenance.py" \
           "$HERE/agent/worker/purge.py" "$HERE/VERSION.json" "$AGE_TGZ" "$DEB"; do
    [ -f "$f" ] || { bad "更新包不完整：缺 ${f#$HERE/}"; fail=1; }
  done
  if [ -f "$AGE_TGZ" ]; then
    if [ "$(sha256sum "$AGE_TGZ" | cut -d' ' -f1)" = "$AGE_SHA" ]; then ok "age v1.2.1（官方靜態執行檔）SHA-256 相符：建映像時放進 Agent 映像，不安裝到主機"
    else bad "age 壓縮檔的 SHA-256 不符（檔案可能損壞或被換過），停止"; fail=1; fi
  fi
  # Server App：目前版本與回退用的舊版安裝檔
  local av od; av=$(app_version); od=$(find_old_deb || true)
  if [ -z "$av" ]; then warn "這台沒有安裝 Server App：只會安裝 $NEW，回退時不需要處理"
  else
    ok "目前 Server App 版本 $av"
    if [ -n "$od" ] && [ -f "$od" ] && [ "$(deb_version "$od")" = "$OLD_DEBVER" ]; then ok "回退用安裝檔 $od（$(deb_version "$od")）"
    else bad "找不到 $OLD 的 Server App 安裝檔：請用 --old-deb 指定（$OLD 原始碼包 dist/istudy-server-app_${OLD}_amd64.deb）"; fail=1; fi
  fi
  local n; n=$(super_count || true)
  if [ "$n" = 1 ]; then ok "Super 帳號剛好 1 位"
  else bad "Super 帳號數量是「${n:-無法查詢}」，必須剛好 1 位才能更新（不會自動新增、刪除或修改帳號）"; fail=1; fi
  local free; free=$(df -Pk "$BK" | awk 'NR==2{print int($4/1024)}')
  [ "${free:-0}" -ge 1536 ] && ok "$BK 剩餘 ${free} MB" || { bad "$BK 剩餘不足 1.5 GB（加密備份需要至少 1 GB 剩餘空間）"; fail=1; }
  dc ps --status running --services 2>/dev/null | grep -x php >/dev/null && ok "php 容器運作中" || { bad "php 容器沒有在執行（請先 sudo systemctl start istudy）"; fail=1; }
  docker build --help >/dev/null 2>&1 && ok "docker build 可用" || { bad "docker build 不可用"; fail=1; }
  if base_has "$BASE_TEST"; then ok "Agent 基底映像有 dev.61 的字型、libmfx-gen1.2、sherpa-onnx、永久刪除程式（沿用，建映像不需要網路）"
  else bad "Agent 基底映像缺 dev.61 的元件：這不是正常的 dev.61 映像，請貼上 docker image ls 'istudy/*'"; fail=1; fi
  [ -d "$SYSTEMD" ] && ok "systemd 設定資料夾 $SYSTEMD" || { bad "找不到 $SYSTEMD"; fail=1; }
  say "== 資料影響範圍（請確認）"
  say "  資料庫：migration 0011 在 backup_history 加欄位（都有預設值，既有紀錄自動標為「舊格式、未加密」）、jobs 多允許 4 種主機備份工作類型。不改任何既有資料。"
  say "  備份：更新後 Agent 只建立加密備份（.tar.gz.age，附 .sha256）。在 Server App「備份」設定恢復密碼之前，不會產生新的每日備份（畫面會顯示警告）。"
  say "  既有備份不動：舊的未加密備份 $(ls -1 "$BK"/istudy-backup-*.tar.gz 2>/dev/null | wc -l | tr -d ' ') 份（.tar.gz）、部署備份 $(ls -1 "$BK"/istudy-db-*.sql 2>/dev/null | wc -l | tr -d ' ') 份（istudy-db-*.sql）——不自動上傳、不自動刪除。"
  say "  新的主機排程：istudy-backup.timer（每小時：新備份解密驗證、每 3 天上傳一份到遠端、每天清理一次超過 10～15 天的加密備份）。"
  say "  WOL：設備的新增、修改、刪除、登記、掃描改為只有 Super；管理員只能查看與喚醒。已登記但未啟用喚醒的電腦：$(sql "SELECT count(*) FROM client_devices WHERE registered AND NOT enabled" | tr -d '[:space:]' || echo '?') 台（登入畫面的喚醒清單不會列出；Super 可在「WOL 管理」啟用）。"
  say "  教材、字幕、帳號、課程權限、設定都不動；.env 只改 ISTUDY_VERSION 這一行；Nginx 設定不動；主機不安裝任何套件。"
  say "  教材數：$(sql "SELECT count(*) FROM media" | tr -d '[:space:]' || echo '?') 筆；正式轉檔編碼器不變：.env 的 ISTUDY_ENCODER＝$(env_get ISTUDY_ENCODER)"
  say "  保留不刪：istudy/*:$OLD 與更早的映像、$(dirname "$SAVE")/patch-dev61 等回退檔。"
  say "  Client：dev.62 有 Client 程式修改（版本 1.0.0-dev.62）。這個腳本不發布 Client：安裝檔在 Windows 打包後，用 Server App「Client 版本與更新」→「發布新版 Client」。"
  [ $fail = 0 ] || die "檢查未通過，沒有做任何修改"
  say "預計影響：重建 2 個增量映像（約 1 分鐘，不需要網路）、服務重啟約 1 分鐘、Server App 重新安裝約 1 分鐘、"
  say "自我檢查前後各一次。合計約 5～7 分鐘。"
}

install_units() { # 只寫入 dev.62 新增的兩個 unit（內容取自新版 istudyctl，避免兩份不同）
  python3 -I - "$OPT/agent" "$SYSTEMD" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
import istudyctl
for name, text in istudyctl.BACKUP_UNITS.items():
    with open(os.path.join(sys.argv[2], name), "w", encoding="utf-8") as f:
        f.write(text)
PY
  systemctl daemon-reload
  systemctl enable --now istudy-backup.timer >/dev/null 2>&1
}

rollback_files() {
  # 盡力而為：每一步都做，最後列出結果；有任何一步失敗就回傳非 0，並說明要人工處理什麼
  local mode=${1:-manual}
  set +e
  local errs=()
  say "== 退回 $OLD"
  if [ ! -d "$SAVE" ]; then bad "找不到 $SAVE（沒有執行過 apply），無法自動退回"; set -e; return 3; fi
  # 備份工作正在執行（Agent 建立備份，或主機驗證、上傳、清理）：手動退回時先等它完成
  local busy; busy=$(sql "SELECT count(*) FROM jobs WHERE type IN ('backup','backup_verify','backup_upload','backup_prune','backup_remote_test') AND status = 'running'" | tr -d '[:space:]')
  if [ "$mode" = manual ] && [ -n "$busy" ] && [ "$busy" != 0 ]; then
    bad "有 $busy 個備份工作正在執行（建立、驗證、上傳或清理）：請等它完成（Server App「備份」）後再退回；沒有做任何修改"
    set -e; return 3
  fi
  # dev.61 的 istudyctl 沒有 backup-run：先停用主機備份排程（金鑰與加密備份都保留）
  if [ -f "$SYSTEMD/istudy-backup.timer" ] || [ -f "$SYSTEMD/istudy-backup.service" ]; then
    systemctl disable --now istudy-backup.timer >/dev/null 2>&1
    { rm -f "$SYSTEMD/istudy-backup.timer" "$SYSTEMD/istudy-backup.service" && systemctl daemon-reload; } \
      && ok "已停用並移除 istudy-backup.timer（dev.61 沒有這個排程）" || errs+=("istudy-backup.timer 無法移除：sudo systemctl disable --now istudy-backup.timer")
  fi
  if [ -n "$(img_id istudy/php:$OLD)" ] && [ -n "$(img_id istudy/agent:$OLD)" ]; then
    env_set ISTUDY_VERSION "$OLD" && ok ".env 的 ISTUDY_VERSION＝$OLD" || errs+=(".env 無法修改：請把 $OPT/.env 的 ISTUDY_VERSION 改回 $OLD")
  else
    errs+=("找不到 $OLD 映像：服務無法用 $OLD 啟動，請把 docker image ls 'istudy/*' 的結果貼給我")
  fi
  cp -p "$SAVE/VERSION.json" "$OPT/VERSION.json" && ok "VERSION.json" || errs+=("VERSION.json 無法還原：cp $SAVE/VERSION.json $OPT/")
  if [ -d "$SAVE/agent/istudy_common" ]; then
    { rm -rf "$OPT/agent/istudy_common" && cp -rp "$SAVE/agent/istudy_common" "$OPT/agent/" && cp -p "$SAVE/agent/istudyctl.py" "$OPT/agent/istudyctl.py"; } \
      && ok "istudyctl" || errs+=("istudyctl 無法還原：cp -rp $SAVE/agent/* $OPT/agent/")
  fi
  dc up -d --no-build --remove-orphans >/dev/null 2>&1 && ok "服務以 istudy/*:$OLD 啟動" || errs+=("服務無法啟動：sudo systemctl restart istudy 後執行 sudo istudyctl doctor")
  if [ -f "$SAVE/app-was-installed" ]; then
    local od; od=$(ls "$SAVE"/app/*.deb 2>/dev/null | head -n 1)
    if [ -n "$od" ] && dpkg -i "$od" >/dev/null 2>&1; then ok "Server App 已裝回 $(app_version)（請關閉並重新開啟 Server App）"
    else errs+=("Server App 無法自動裝回：sudo dpkg -i <$OLD 原始碼包>/dist/istudy-server-app_${OLD}_amd64.deb"); fi
  else
    ok "更新前沒有安裝 Server App：保留現狀（要移除可執行 sudo apt remove $APP_PKG）"
  fi
  say "  資料庫：migration 0011 保留（多出的欄位與工作類型，dev.61 不使用、不受影響）。"
  say "  加密備份（.tar.gz.age）與 $KEYDIR 的金鑰都保留、沒有刪除；dev.61 的 Agent 會恢復建立未加密的 .tar.gz，"
  say "  它的保留份數清理只比對 istudy-backup-*.tar.gz，不會碰到 .age。要解密 .age：看備份資料夾的 RESTORE.md，"
  say "  或重新更新到 dev.62（istudy/agent:$NEW 映像保留，裡面有 age）。WOL：dev.61 的管理員又可以編輯設備。"
  say "  Client：已更新到 dev.62 的電腦不受影響（與 dev.61 Server 相容），不需要退回。"
  say "  要連資料庫一起回到更新前："
  say "  sudo istudyctl restore --file $(cat "$SAVE/backup-file" 2>/dev/null || echo '<更新前的備份>') --target prod"
  say "  （先還原到測試資料庫檢查，再要求在終端機輸入「覆蓋」才會動正式資料庫）"
  set -e
  if [ ${#errs[@]} -gt 0 ]; then
    bad "沒有完全退回，以下需要人工處理："
    printf '    - %s\n' "${errs[@]}" >&2
    return 3
  fi
  ok "已完全退回 $OLD（PostgreSQL、ZFS、教材、備份檔與金鑰都沒有被刪除或改動）"
  return 0
}

apply() {
  check
  allowed_from "$CUR" || die "目前是 $CUR，不能直接 apply（已是 $NEW 時要重新套用請先執行 rollback）"
  local av; av=$(app_version)
  local od=""; [ -n "$av" ] && od=$(find_old_deb)
  say "== 1/7 更新前自我檢查（基準）"
  install -d -m 700 "$SAVE.tmp"
  doctor_run "$SAVE.tmp/doctor-before.jsonl"
  doctor_summary "$SAVE.tmp/doctor-before.jsonl" "" || true
  say "== 2/7 備份資料庫（更新前）"
  local before; before=$(ls -1t "$BK"/istudy-db-*.sql 2>/dev/null | head -n 1 || true)
  "$ISTUDYCTL" backup
  local file; file=$(ls -1t "$BK"/istudy-db-*.sql 2>/dev/null | head -n 1 || true)
  [ -n "$file" ] && [ "$file" != "$before" ] || die "備份沒有產生新檔案，停止"
  # grep 不用 -q：-q 找到就先結束，前面的指令可能收到 SIGPIPE，在 pipefail 下被誤判失敗
  [ -s "$file" ] && head -c 4096 "$file" | grep "PostgreSQL database dump" >/dev/null \
    && tail -c 4096 "$file" | grep "PostgreSQL database dump complete" >/dev/null \
    && grep -q "CREATE TABLE public.accounts" "$file" \
    || die "備份檔不完整或不是 PostgreSQL 備份（$file），停止"
  ok "$file：已產生、非空、格式完整、可讀（$(du -h "$file" | cut -f1)；這不是還原測試）"
  if [ "$RESTORE_TEST" = 1 ]; then
    "$ISTUDYCTL" restore --file "$file" --target test || die "隔離還原測試失敗，停止（正式資料庫未變動）"
    ok "隔離還原測試通過（istudy_restore_test；正式資料庫未變動）"
  fi
  say "== 3/7 保存目前設定（退回用）"
  rm -rf "$SAVE" && mv "$SAVE.tmp" "$SAVE"
  cp -p "$OPT/VERSION.json" "$SAVE/VERSION.json"
  install -d -m 700 "$SAVE/agent" "$SAVE/app"
  cp -rp "$OPT/agent/istudy_common" "$SAVE/agent/"
  cp -p "$OPT/agent/istudyctl.py" "$SAVE/agent/istudyctl.py"
  if [ -n "$av" ]; then echo "$av" > "$SAVE/app-was-installed"; cp -p "$od" "$SAVE/app/"; fi
  echo "$file" > "$SAVE/backup-file"
  ok "$SAVE"
  IN_APPLY=1
  trap 'die "更新失敗（第 $LINENO 行的指令出錯）"' ERR
  say "== 4/7 建立 dev.62 增量映像（以 $OLD 為基底，加入 age；不需要網路）"
  local php_old agent_old ctx; php_old=$(img_id istudy/php:$OLD); agent_old=$(img_id istudy/agent:$OLD)
  ctx=$(mktemp -d)
  install -d "$ctx/agent" "$ctx/server" "$ctx/deploy/vendor"
  cp -r "$HERE/agent/istudy_common" "$HERE/agent/worker" "$ctx/agent/"
  cp -r "$HERE/server/public" "$HERE/server/src" "$HERE/server/cli" "$HERE/server/sql" "$ctx/server/"
  cp "$AGE_TGZ" "$ctx/deploy/vendor/"
  find "$ctx" -name '__pycache__' -prune -exec rm -rf {} +
  docker build --pull=false --network none --progress=plain -f "$HERE/deploy/patch/agent.Dockerfile" --build-arg BASE=istudy/agent:$OLD -t istudy/agent:$NEW "$ctx"
  docker build --pull=false --network none --progress=plain -f "$HERE/deploy/patch/php.Dockerfile" --build-arg BASE=istudy/php:$OLD -t istudy/php:$NEW "$ctx"
  rm -rf "$ctx"
  [ "$(img_id istudy/php:$OLD)" = "$php_old" ] && [ "$(img_id istudy/agent:$OLD)" = "$agent_old" ] || die "$OLD 映像被改動，停止"
  ok "istudy/php:$NEW、istudy/agent:$NEW（$OLD 標籤未變）"
  say "== 5/7 更新設定與 istudyctl、安裝主機備份排程"
  cp "$HERE/VERSION.json" "$OPT/VERSION.json"
  rm -rf "$OPT/agent/istudy_common"
  cp -r "$HERE/agent/istudy_common" "$OPT/agent/"
  cp "$HERE/agent/istudyctl.py" "$OPT/agent/istudyctl.py"
  find "$OPT/agent" -name '__pycache__' -prune -exec rm -rf {} +
  chown -R root:root "$OPT/agent" && chmod -R go-w "$OPT/agent"
  env_set ISTUDY_VERSION "$NEW"
  ok "VERSION.json、.env 的 ISTUDY_VERSION、$OPT/agent"
  install_units
  ok "istudy-backup.timer 已啟用（每小時：解密驗證、到期上傳、每日清理；尚未設定恢復密碼時什麼都不做）"
  say "== 6/7 重新啟動服務、套用資料庫更新（0011）、安裝 Server App"
  dc up -d --no-build php
  local out; out=$(dc exec -T php php /var/www/html/cli/istudy.php migrate | tail -n 1)
  python3 -I -c "import json,sys;d=json.loads(sys.argv[1]);sys.exit(0 if d.get('ok') else 1)" "$out" || die "資料庫更新失敗：$out"
  [ "$(super_count)" = 1 ] || die "Super 數量不是 1"
  ok "Super 1 位；資料庫更新：$(python3 -I -c "import json,sys;print('、'.join(json.loads(sys.argv[1]).get('applied') or ['沒有新的 migration']))" "$out")"
  dc up -d --no-build --remove-orphans
  ok "已啟動 istudy/*:$NEW"
  dpkg -i "$DEB" >/dev/null
  ok "Server App $(app_version)"
  trap - ERR
  IN_APPLY=0
  say "== 7/7 更新後自我檢查"
  sleep 30
  doctor_run "$SAVE/doctor-after.jsonl"
  local rc=0; doctor_summary "$SAVE/doctor-after.jsonl" "$SAVE/doctor-before.jsonl" > "$SAVE/doctor-after.txt" || rc=$?
  if [ $rc = 2 ]; then # 剛啟動時 Agent 心跳可能還沒寫入：等 30 秒再檢查一次
    say "  有 FAIL，30 秒後再檢查一次…"; sleep 30
    doctor_run "$SAVE/doctor-after.jsonl"
    rc=0; doctor_summary "$SAVE/doctor-after.jsonl" "$SAVE/doctor-before.jsonl" > "$SAVE/doctor-after.txt" || rc=$?
  fi
  cat "$SAVE/doctor-after.txt"
  say "下一步（依序）："
  say "  1. 關閉並重新開啟 Server App（新畫面才會載入）。"
  say "  2. Server App「備份」→「設定恢復密碼」（至少 12 字、不要和 Super 登入密碼相同，另外妥善保管）。設定前不會產生新的每日備份。"
  say "  3. 取得 InterServer 的 FTPS 主機、連接埠、帳號後：「備份」→「設定遠端」→「測試遠端連線」。"
  say "  4. Client：在 Windows 打包 dev.62 安裝檔，用「Client 版本與更新」→「發布新版 Client」，各台自動更新。"
  say "  5. 依 DEPLOY_DEV62.md 逐項驗收。"
  say "更新前的資料庫備份：$file"
  case $rc in
    0) say "結果：PASS——部署步驟完成、自我檢查通過。實機功能驗收仍是 NOT_TESTED，請依 DEPLOY_DEV62.md 逐項測試。" ;;
    1) say "結果：WARN——部署步驟完成，服務可運作，但有非關鍵警告（見上；尚未設定恢復密碼時「加密備份」一定是警告）。實機功能驗收仍是 NOT_TESTED。" ;;
    *) bad "結果：FAIL——部署步驟已執行，但自我檢查有重要項目失敗（見上），不算部署成功。"
       bad "請把上面的結果貼給我；要退回請執行：sudo ./deploy/patch_dev62.sh rollback"
       exit 2 ;;
  esac
}

case "$ACTION" in
  check) check ;;
  apply) apply ;;
  rollback) rollback_files manual ;;
  *) echo "用法：sudo $0 check|apply|rollback [--old-deb 檔案] [--restore-test]" >&2; exit 64 ;;
esac
