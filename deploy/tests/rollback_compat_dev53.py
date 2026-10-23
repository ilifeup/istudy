"""回退相容性（dev.55 → dev.53）：dev.55 migration 後的資料庫交給 dev.53 的 PHP 與 Agent 使用。
用法：python3 deploy/tests/rollback_compat_dev53.py <dev.53 原始碼目錄>（需要可建立資料庫的 PostgreSQL：DB_HOST=127.0.0.1、DB_USER=postgres）"""
import json, os, subprocess, sys, time, urllib.request
NEW = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); OLD = sys.argv[1]
DB = "istudy_rollback_t"
env = {**os.environ, "DB_HOST": "127.0.0.1", "DB_USER": "postgres", "DB_NAME": DB, "PGHOST": "127.0.0.1", "PGUSER": "postgres"}
def sh(*a, **k): return subprocess.run(list(a), capture_output=True, text=True, env=env, **k)
sh("psql", "-X", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"); sh("psql", "-X", "-d", "postgres", "-c", f"CREATE DATABASE {DB}")
# 1) dev.53 建立資料庫與 Super、資料；2) dev.55 migrate；3) 交回 dev.53
old_cli = ["php", os.path.join(OLD, "server/cli/istudy.php")]; new_cli = ["php", os.path.join(NEW, "server/cli/istudy.php")]
print("dev53 migrate", json.loads(sh(*old_cli, "migrate").stdout)["applied"])
print("dev53 create-super", json.loads(sh(*old_cli, "create-super", "ricky", input="SuperPass123\nSuperPass123\n").stdout)["ok"])
sh("psql", "-X", "-d", DB, "-c", "INSERT INTO client_devices(name, mac, broadcast, registered, enabled) VALUES ('Lenovo_S_PC','aa:bb:cc:00:00:03','10.3.3.3',TRUE,TRUE)")
print("dev55 migrate", json.loads(sh(*new_cli, "migrate").stdout)["applied"])
ok_all = True
def check(name, cond):
    global ok_all; ok_all &= bool(cond); print(("PASS " if cond else "FAIL ") + name)
p = subprocess.Popen(["php", "-S", "127.0.0.1:18381", "-t", os.path.join(OLD, "server/public"), os.path.join(OLD, "server/public/index.php")],
                     env={**env, "ISTUDY_LOCAL_ADMIN": "1", "ISTUDY_VERSION_FILE": os.path.join(OLD, "VERSION.json")}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
def call(m, path, body=None, tok=None):
    r = urllib.request.Request("http://127.0.0.1:18381" + path, data=json.dumps(body).encode() if body is not None else None, method=m)
    r.add_header("Content-Type", "application/json")
    if tok: r.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
try:
    st, r = call("POST", "/api/login", {"username": "ricky", "password": "SuperPass123"}); tok = r.get("token")
    check("dev.53 Super 登入", st == 200 and tok)
    st, r = call("POST", "/api/accounts", {"username": "stu01", "password": "stupass12", "role": "user"}, tok)
    check("dev.53 新增帳號（title_id 留空）", st == 200)
    st, r = call("GET", "/api/accounts", tok=tok); check("dev.53 帳號清單", st == 200 and any(a["username"] == "stu01" for a in r["accounts"]))
    lan = subprocess.Popen(["php", "-S", "127.0.0.1:18380", "-t", os.path.join(OLD, "server/public"), os.path.join(OLD, "server/public/index.php")],
                           env={**env, "ISTUDY_VERSION_FILE": os.path.join(OLD, "VERSION.json")}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    rq = urllib.request.Request("http://127.0.0.1:18380/api/login", data=json.dumps({"username": "stu01", "password": "stupass12"}).encode(), method="POST")
    rq.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(rq) as x: st = x.status
    except urllib.error.HTTPError as e: st = e.code
    lan.terminate(); lan.wait()
    check("dev.53 一般帳號登入（LAN 埠）", st == 200)
    st, r = call("GET", "/api/wol/devices", tok=tok); check("dev.53 WOL 設備清單（廣播位址未被自動改寫）", st == 200 and r["devices"][0]["broadcast"] == "10.3.3.3")
    st, r = call("POST", "/api/wol/wake", {"device_id": r["devices"][0]["id"]}, tok); check("dev.53 喚醒排入", st == 200 and len(r["sent"]) == 1)
    payload = json.loads(sh("psql", "-X", "-d", DB, "-tAc", "SELECT payload FROM jobs WHERE type='wol_wake' ORDER BY id DESC LIMIT 1").stdout)
    sys.path.insert(0, os.path.join(OLD, "agent"))
    from worker import wol
    sent = []
    wol.send = lambda mac, b, port, n: sent.append(b) or n
    wol.wol_wake(None, {"payload": payload})
    check("dev.53 Agent 依原設定送出喚醒封包", sent == ["10.3.3.3"])
    st, r = call("POST", "/api/accounts", {"username": "mini9", "password": "minipass9", "role": "mini_admin"}, tok)
    check("dev.53 程式無法再建立 mini_admin（資料庫外鍵拒絕，不會寫入）", st != 200 and "mini9" not in sh("psql", "-X", "-d", DB, "-tAc", "SELECT username FROM accounts").stdout)
    st, r = call("POST", "/api/accounts", {"username": "sup2", "password": "superpass2", "role": "super"}, tok)
    check("dev.53 程式也無法建立第二位 Super（API 403 ＋ 資料庫唯一索引）", st == 403)
    e = sh("psql", "-X", "-d", DB, "-c", "INSERT INTO accounts(username,password_hash,role) VALUES ('x','h','super')").stderr
    check("回退後資料庫仍拒絕第二位 Super", "accounts_one_super" in e)
finally:
    p.terminate(); p.wait()
    sh("psql", "-X", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0 if ok_all else 1)
