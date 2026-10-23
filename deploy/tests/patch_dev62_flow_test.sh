#!/bin/bash
# deploy/patch_dev62.sh 的流程測試：docker／psql（runuser）／istudyctl／dpkg／systemctl 用假指令；設定、備份、systemd 資料夾用暫存資料夾。
# 驗證：只接受 dev.61；age 壓縮檔 SHA-256 不符、基底缺 dev.61 元件時停止；備份→建映像（不連網、含 age）→更新→安裝 istudy-backup.timer
#       →migrate 0011→重啟→自我檢查的順序；.env 只改 ISTUDY_VERSION；失敗自動退回；rollback 完全退回、停用並移除排程，
#       但不刪除加密備份（.age）、舊備份、金鑰；永遠不刪除舊映像與 dev.61 回退檔。真正的 docker build／compose／dpkg 只能在 Server 上驗證。
#   sudo bash deploy/tests/patch_dev62_flow_test.sh
set -u
HERE=$(cd "$(dirname "$0")/../.." && pwd)
pass=0; fail=0
t() { if eval "$2"; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1"; fi; }

setup() {
  W=$(mktemp -d); export W
  mkdir -p "$W/opt/agent/istudy_common" "$W/opt/server/nginx" "$W/bk" "$W/bin" "$W/state" "$W/old" "$W/var/patch-dev61" "$W/systemd" "$W/keys"
  echo "dev61 rollback data" > "$W/var/patch-dev61/VERSION.json"
  printf 'DB_PASSWORD=secret-not-printed\nISTUDY_VERSION=1.0.0-dev.61\nPG_SOCKET_DIR=/var/run/postgresql\nISTUDY_ENCODER=vaapi\n' > "$W/opt/.env"
  echo 'services: {}' > "$W/opt/compose.yaml"
  echo '{"server": "1.0.0-dev.61"}' > "$W/opt/VERSION.json"
  echo '# dev61 istudyctl' > "$W/opt/agent/istudyctl.py"
  echo 'location /_protected/original/ { alias /Media/; }' > "$W/opt/server/nginx/istudy_protected.conf"
  echo 'fake dev61 deb' > "$W/old/istudy-server-app_1.0.0-dev.61_amd64.deb"
  echo 'legacy' > "$W/bk/istudy-backup-20261001-033000.tar.gz"
  echo 'dump' > "$W/bk/istudy-db-20261001-120000.sql"
  echo 'age-encryption.org/v1' > "$W/bk/istudy-backup-20261020-033000.tar.gz.age"
  echo 'AGE-SECRET-KEY-TEST' > "$W/keys/identity.key"
  printf 'istudy/php:1.0.0-dev.60 sha256:php60\nistudy/agent:1.0.0-dev.60 sha256:agent60\nistudy/php:1.0.0-dev.61 sha256:php61\nistudy/agent:1.0.0-dev.61 sha256:agent61\n' > "$W/state/images"
  echo 1 > "$W/state/supers"; echo ok > "$W/state/migrate"; echo '1.0.0~dev.61' > "$W/state/appver"; echo ok > "$W/state/dpkg"
  echo pass > "$W/state/doctor"; echo yes > "$W/state/base61"; echo ok > "$W/state/build"; echo 0 > "$W/state/busy"
  : > "$W/state/calls"
  cat > "$W/bin/docker" <<'EOS'
#!/bin/bash
[ "$1" = build ] && [ "${2:-}" = --help ] && exit 0
echo "docker $*" >> "$W/state/calls"
if [ "$1" = image ] && [ "$2" = inspect ]; then
  ref=${@: -1}; id=$(awk -v r="$ref" '$1==r{print $2}' "$W/state/images"); [ -n "$id" ] && { echo "$id"; exit 0; }; exit 1
fi
if [ "$1" = run ]; then case "$*" in
  *Carlito*libmfx*sherpa_onnx*purge.py*) [ "$(cat "$W/state/base61")" = yes ]; exit $? ;;
  *) exit 1 ;; esac; fi
if [ "$1" = build ]; then
  ctx=${@: -1}
  [ -f "$ctx/agent/istudy_common/backupcrypt.py" ] && [ -f "$ctx/agent/istudy_common/agepass.py" ] && [ -f "$ctx/server/sql/migrations/0011_backup_v2.sql" ] \
    && [ -f "$ctx/deploy/vendor/age-v1.2.1-linux-amd64.tar.gz" ] && echo "build-context-ok" >> "$W/state/calls"
  [ "$(cat "$W/state/build")" = ok ] || exit 1
  tag=""; while [ $# -gt 0 ]; do [ "$1" = -t ] && tag=$2; shift; done
  grep -v "^$tag " "$W/state/images" > "$W/state/i2"; echo "$tag sha256:new$RANDOM" >> "$W/state/i2"; mv "$W/state/i2" "$W/state/images"; exit 0
fi
if [ "$1" = compose ]; then
  case "$*" in
    *"ps --status running --services"*) echo php; echo nginx; echo agent ;;
    *"istudy.php migrate"*) [ "$(cat "$W/state/migrate")" = ok ] && echo '{"ok":true,"applied":["0011_backup_v2"]}' || echo '{"ok":false,"error":"boom"}' ;;
  esac
  exit 0
fi
exit 0
EOS
  cat > "$W/bin/runuser" <<'EOS'
#!/bin/bash
q=${@: -1}
case "$q" in
  UPDATE*) echo "sql $q" >> "$W/state/calls"; echo "UPDATE 0" ;;
  *"role = 'super'"*) cat "$W/state/supers" ;;
  *"status = 'running'"*) cat "$W/state/busy" ;;
  *"NOT enabled"*) echo 2 ;;
  *"count(*) FROM media"*) echo 12 ;;
esac
EOS
  cat > "$W/bin/istudyctl" <<'EOS'
#!/bin/bash
echo "istudyctl $*" >> "$W/state/calls"
case "$*" in
  backup) sleep 1; printf -- '--\n-- PostgreSQL database dump\n--\nCREATE TABLE public.accounts (id bigint);\n--\n-- PostgreSQL database dump complete\n--\n' > "$W/bk/istudy-db-$(date +%s%N).sql" ;;
  "--json doctor")
    echo '{"event":"check","id":"backup","name":"加密備份（方案 B）","status":"warn","message":"尚未設定備份恢復密碼"}'
    [ "$(cat "$W/state/doctor")" = fail ] && echo '{"event":"check","id":"api","name":"API","status":"fail","message":"沒有回應"}' ;;
  "restore --file "*"--target test") echo "已還原到測試資料庫 istudy_restore_test" ;;
esac
exit 0
EOS
  printf '#!/bin/bash\necho "systemctl $*" >> "$W/state/calls"\n' > "$W/bin/systemctl"
  printf '#!/bin/bash\nv=$(cat "$W/state/appver"); [ -n "$v" ] && printf "%%s" "$v"\n' > "$W/bin/dpkg-query"
  printf '#!/bin/bash\necho "$2" | sed -n "s/.*_\\(1\\.0\\.0\\)-\\(dev\\.[0-9]*\\)_amd64.*/\\1~\\2/p"\n' > "$W/bin/dpkg-deb"
  cat > "$W/bin/dpkg" <<'EOS'
#!/bin/bash
echo "dpkg $*" >> "$W/state/calls"
[ "$(cat "$W/state/dpkg")" = ok ] || exit 1
echo "$2" | sed -n 's/.*_\(1\.0\.0\)-\(dev\.[0-9]*\)_amd64.*/\1~\2/p' > "$W/state/appver"
EOS
  printf '#!/bin/bash\nexit 0\n' > "$W/bin/sleep"
  chmod +x "$W/bin/"*
  export PATH="$W/bin:$PATH" ISTUDY_PATCH_OPT="$W/opt" ISTUDY_PATCH_SAVE="$W/var/patch-dev62" ISTUDY_PATCH_BACKUP_DIR="$W/bk" \
    ISTUDY_PATCH_ISTUDYCTL="$W/bin/istudyctl" ISTUDY_PATCH_SYSTEMD="$W/systemd" ISTUDY_PATCH_KEYDIR="$W/keys"
  unset ISTUDY_PATCH_AGE_TGZ
}
OLDDEB() { echo "--old-deb $W/old/istudy-server-app_1.0.0-dev.61_amd64.deb"; }
run() { bash "$HERE/deploy/patch_dev62.sh" "$@" < /dev/null > "$W/out" 2>&1; echo $? > "$W/rc"; }
ver() { sed -n 's/^ISTUDY_VERSION=//p' "$W/opt/.env"; }
rc() { cat "$W/rc"; }
untouched() { [ "$(ver)" = 1.0.0-dev.61 ] && ! grep -q "docker build\|istudyctl backup\|dpkg -i\|^systemctl\|^sql UPDATE" "$W/state/calls" && [ -z "$(ls -A $W/systemd)" ]; }
backups_kept() { [ -f "$W/bk/istudy-backup-20261001-033000.tar.gz" ] && [ -f "$W/bk/istudy-db-20261001-120000.sql" ] \
  && [ -f "$W/bk/istudy-backup-20261020-033000.tar.gz.age" ] && [ "$(cat $W/keys/identity.key)" = AGE-SECRET-KEY-TEST ]; }
never_pruned() { ! grep -Eq "docker (image )?(rm|rmi|prune)|docker system prune|docker image prune" "$W/state/calls" \
  && grep -q "^istudy/agent:1.0.0-dev.61 sha256:agent61$" "$W/state/images" && grep -q "^istudy/php:1.0.0-dev.61 sha256:php61$" "$W/state/images" \
  && grep -q "^istudy/agent:1.0.0-dev.60 sha256:agent60$" "$W/state/images" && grep -q "dev61 rollback data" "$W/var/patch-dev61/VERSION.json"; }

# 1) check：不改任何東西
setup; run check $(OLDDEB)
t "check 通過（age SHA-256 相符、基底有 dev.61 元件：不需要網路）" '[ "$(rc)" = 0 ] && grep -q "SHA-256 相符" $W/out && grep -q "建映像不需要網路" $W/out'
t "check 列出資料影響範圍（0011、設定恢復密碼前不產生備份、舊備份不動、排程、WOL、Client 另外發布）" 'grep -q "migration 0011" $W/out && grep -q "設定恢復密碼之前，不會產生新的每日備份" $W/out && grep -q "舊的未加密備份 1 份" $W/out && grep -q "部署備份 1 份" $W/out && grep -q "istudy-backup.timer" $W/out && grep -q "未啟用喚醒的電腦：2 台" $W/out && grep -q "發布新版 Client" $W/out && grep -q "ISTUDY_ENCODER＝vaapi" $W/out'
t "check 不改任何東西、輸出不含密碼" 'untouched && backups_kept && ! grep -q secret-not-printed $W/out'

# 2) 停止條件
setup; echo '{"server": "1.0.0-dev.60"}' > "$W/opt/VERSION.json"; run check $(OLDDEB)
t "只接受 dev.61" '[ "$(rc)" != 0 ] && grep -q "只適用於 1.0.0-dev.61" $W/out'
setup; echo 2 > "$W/state/supers"; run apply $(OLDDEB)
t "Super=2：停止且不改任何東西" '[ "$(rc)" != 0 ] && untouched'
setup; echo no > "$W/state/base61"; run apply $(OLDDEB)
t "基底缺 dev.61 元件：停止且不改任何東西" '[ "$(rc)" != 0 ] && grep -q "不是正常的 dev.61 映像" $W/out && untouched'
setup; rm "$W/old/istudy-server-app_1.0.0-dev.61_amd64.deb"; run apply
t "沒有回退用的 Server App 安裝檔：停止" '[ "$(rc)" != 0 ] && grep -q "找不到 1.0.0-dev.61 的 Server App 安裝檔" $W/out && untouched'
setup; cp "$HERE/deploy/vendor/age-v1.2.1-linux-amd64.tar.gz" "$W/age.tgz"; printf x >> "$W/age.tgz"; export ISTUDY_PATCH_AGE_TGZ="$W/age.tgz"; run apply $(OLDDEB)
t "age 壓縮檔被換過（SHA-256 不符）：停止且不改任何東西" '[ "$(rc)" != 0 ] && grep -q "SHA-256 不符" $W/out && untouched'
unset ISTUDY_PATCH_AGE_TGZ

# 3) apply 成功
setup; run apply $(OLDDEB) --restore-test
t "apply 成功（WARN：尚未設定恢復密碼）" '[ "$(rc)" = 0 ] && grep -q "結果：WARN" $W/out && grep -q "設定恢復密碼" $W/out'
t ".env 只改 ISTUDY_VERSION（ISTUDY_ENCODER、密碼不變）" '[ "$(ver)" = 1.0.0-dev.62 ] && grep -q "^ISTUDY_ENCODER=vaapi$" $W/opt/.env && grep -q "^DB_PASSWORD=secret-not-printed$" $W/opt/.env && [ "$(wc -l < $W/opt/.env)" = 4 ]'
t "以 dev.61 為基底、建映像不連網、內容含 age 壓縮檔與新程式、0011" 'grep "docker build" $W/state/calls | grep -q "BASE=istudy/agent:1.0.0-dev.61" && ! grep "docker build" $W/state/calls | grep -vq -- "--network none" && [ "$(grep -c build-context-ok $W/state/calls)" = 2 ]'
t "istudy-backup.timer：由新版 istudyctl 寫入、每小時、執行 backup-run，並啟用" 'grep -q "OnCalendar=hourly" $W/systemd/istudy-backup.timer && grep -q "ExecStart=/usr/local/sbin/istudyctl backup-run" $W/systemd/istudy-backup.service && grep -q "systemctl enable --now istudy-backup.timer" $W/state/calls && [ "$(ls $W/systemd | wc -l)" = 2 ]'
t "migrate 套用 0011，Nginx 設定不動" 'grep -q "0011_backup_v2" $W/out && grep -q "alias /Media/;" $W/opt/server/nginx/istudy_protected.conf'
t "順序：自我檢查 → 備份 → 還原測試 → 建映像 → 排程 → migrate → Server App → 自我檢查" 'awk "/--json doctor/{if(!d1)d1=NR; else d2=NR}/istudyctl backup$/{b=NR}/--target test/{r=NR}/docker build/{if(!x)x=NR}/systemctl enable/{s=NR}/istudy.php migrate/{m=NR}/dpkg -i .*dev.62/{p=NR}END{exit !(d1<b&&b<r&&r<x&&x<s&&s<m&&m<p&&p<d2)}" $W/state/calls'
t "既有備份、加密備份、金鑰、舊映像、dev.61 回退檔都保留" 'backups_kept && never_pruned'

# 4) rollback：完全退回；停用並移除排程；備份與金鑰不動
echo 1 > "$W/state/busy"; run rollback
t "有備份工作執行中：rollback 不做任何修改" '[ "$(rc)" != 0 ] && grep -q "正在執行" $W/out && [ "$(ver)" = 1.0.0-dev.62 ] && [ -f $W/systemd/istudy-backup.timer ]'
echo 0 > "$W/state/busy"; run rollback
t "rollback 完全退回 dev.61（版本、istudyctl、Server App）" '[ "$(rc)" = 0 ] && [ "$(ver)" = 1.0.0-dev.61 ] && grep -q "dev61 istudyctl" $W/opt/agent/istudyctl.py && [ "$(cat $W/state/appver)" = "1.0.0~dev.61" ] && grep -q "已完全退回" $W/out'
t "rollback：停用並移除 istudy-backup.timer；加密備份、舊備份、金鑰都不刪" '[ -z "$(ls -A $W/systemd)" ] && grep -q "systemctl disable --now istudy-backup.timer" $W/state/calls && backups_kept && grep -q "不會碰到 .age" $W/out'
t "rollback 不刪映像與回退檔" 'never_pruned && grep -q "^istudy/agent:1.0.0-dev.62 " $W/state/images'
run apply $(OLDDEB)
t "退回後可以再更新到 dev.62" '[ "$(rc)" = 0 ] && [ "$(ver)" = 1.0.0-dev.62 ] && [ -f $W/systemd/istudy-backup.timer ]'

# 5) 中途失敗自動退回
setup; echo fail > "$W/state/migrate"; run apply $(OLDDEB)
t "資料庫更新失敗：自動退回 dev.61（排程移除、備份與金鑰不動）" '[ "$(rc)" != 0 ] && grep -q "自動退回" $W/out && [ "$(ver)" = 1.0.0-dev.61 ] && grep -q "dev61 istudyctl" $W/opt/agent/istudyctl.py && [ -z "$(ls -A $W/systemd)" ] && backups_kept && never_pruned'
setup; echo fail > "$W/state/build"; run apply $(OLDDEB)
t "建映像失敗：自動退回、版本不變" '[ "$(rc)" != 0 ] && grep -q "自動退回" $W/out && [ "$(ver)" = 1.0.0-dev.61 ] && never_pruned && backups_kept'

# 6) 自我檢查 FAIL
setup; echo fail > "$W/state/doctor"; run apply $(OLDDEB)
t "自我檢查有 FAIL：不宣稱成功、回傳 2" '[ "$(rc)" = 2 ] && grep -q "結果：FAIL" $W/out'

echo "$pass 通過、$fail 失敗"
[ $fail = 0 ]
