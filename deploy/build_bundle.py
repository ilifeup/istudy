#!/usr/bin/env python3
"""在可連網的開發電腦產生離線部署套件（第 17 章），再複製到 USB。

  python3 deploy/build_bundle.py --debs ~/istudy-debs --models ~/istudy-models [--zip]
  （Server App .deb 自動取 dist/istudy-server-app_<版本>_amd64.deb，必須先執行 deploy/package_server_app.py，找不到就停止；
   只用終端機 install.sh --cli 部署時加 --no-server-app。Client 安裝檔用 --clients dist/clients，可省略）

產出 dist/istudy-server-<Server 版本>-offline/：
  install.sh、istudyctl/、debs/、images/istudy-images.tar＋manifest.json、models/、app/、clients/<版別>/、stack/、VERSION.json、SHA256SUMS

--debs：Docker Engine、Compose 外掛、containerd、smartmontools 的 .deb，必須對應 Server 的 Ubuntu 版本
        （在一台同版本 Ubuntu 上：apt-get download docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin smartmontools pkexec）
--models：字幕辨識模型目錄，結構與 Server 的 /opt/istudy/models 相同（asr/vad、asr/punct-ct-transformer、asr/paraformer-zh、
          asr/sensevoice-small；可從已部署的 Server 複製，或在可連網的電腦執行 istudyctl asr-models --fetch all 後複製）。
          dev.59 起不再收 faster-whisper-*（Whisper 引擎已移除）。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS = ("win7", "win10", "win11", "ubuntu")


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_images(out: str, version: dict) -> None:
    tag = version["server"]
    env = {**os.environ, "ISTUDY_VERSION": tag, "DB_PASSWORD": "build-only"}
    sh(["docker", "compose", "-f", os.path.join(ROOT, "compose.yaml"), "build", "php", "agent"], env=env, cwd=ROOT)
    compose = open(os.path.join(ROOT, "compose.yaml"), encoding="utf-8").read()
    pinned = [line.split("image:", 1)[1].strip() for line in compose.splitlines() if "image:" in line and "${" not in line]
    for ref in pinned:
        if ref.endswith(":latest") or ":" not in ref:
            sys.exit(f"映像必須鎖定版本：{ref}")
        sh(["docker", "pull", ref])
    refs = pinned + [f"istudy/php:{tag}", f"istudy/agent:{tag}"]
    manifest = {}
    for ref in refs:
        p = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", ref], capture_output=True, text=True, check=True)
        manifest[ref] = p.stdout.strip()
    os.makedirs(os.path.join(out, "images"), exist_ok=True)
    sh(["docker", "save", "-o", os.path.join(out, "images", "istudy-images.tar"), *refs])
    with open(os.path.join(out, "images", "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--debs")
    ap.add_argument("--models")
    ap.add_argument("--clients", help="Client 安裝檔目錄：<版別>/ 底下放安裝檔與 latest*.yml")
    ap.add_argument("--server-app", help="Server App .deb")
    ap.add_argument("--no-server-app", action="store_true",
                    help="不放 Server App .deb（只用終端機 sudo ./install.sh --cli 部署時才用）")
    ap.add_argument("--skip-images", action="store_true", help="只產生結構（測試用，不能拿來部署）")
    ap.add_argument("--zip", action="store_true", help="另外產生 dist/istudy_<版本>-offline.zip（完整離線部署包）＋ .sha256")
    args = ap.parse_args()
    if args.zip and args.skip_images:
        sys.exit("--skip-images 產生的套件沒有 Docker 映像，不能拿來部署；不會產生 offline.zip")

    version = json.load(open(os.path.join(ROOT, "VERSION.json"), encoding="utf-8"))
    # Server App .deb 是正式部署必要的成品：找不到就停止（在建立套件之前）
    server_app = args.server_app or os.path.join(args.out, f"istudy-server-app_{version['server']}_amd64.deb")
    need_app = not (args.no_server_app or args.skip_images)
    if need_app and not os.path.exists(server_app):
        sys.exit(f"找不到 Server App .deb：{server_app}\n"
                 f"請先執行 python3 deploy/package_server_app.py；只用終端機部署（install.sh --cli）時改加 --no-server-app")
    out = os.path.join(args.out, f"istudy-server-{version['server']}-offline")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)

    shutil.copy2(os.path.join(ROOT, "deploy", "install.sh"), out)
    shutil.copy2(os.path.join(ROOT, "VERSION.json"), out)
    # istudyctl（主機端 Python，只用標準函式庫）
    os.makedirs(os.path.join(out, "istudyctl"))
    shutil.copy2(os.path.join(ROOT, "agent", "istudyctl.py"), os.path.join(out, "istudyctl"))
    shutil.copytree(os.path.join(ROOT, "agent", "istudy_common"), os.path.join(out, "istudyctl", "istudy_common"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    # stack：compose、Nginx、systemd 與 polkit
    stack = os.path.join(out, "stack")
    os.makedirs(os.path.join(stack, "nginx"))
    shutil.copy2(os.path.join(ROOT, "compose.yaml"), stack)
    for f in glob.glob(os.path.join(ROOT, "server", "nginx", "*.conf")):
        shutil.copy2(f, os.path.join(stack, "nginx"))
    shutil.copytree(os.path.join(ROOT, "deploy", "polkit"), os.path.join(stack, "polkit"))
    shutil.copy2(os.path.join(ROOT, "deploy", "istudyctl.sh"), stack)
    # Docker .deb
    os.makedirs(os.path.join(out, "debs"))
    if args.debs:
        for f in glob.glob(os.path.join(args.debs, "*.deb")):
            shutil.copy2(f, os.path.join(out, "debs"))
    elif not args.skip_images:
        sys.exit("請用 --debs 指定 Docker 的 .deb（對應 Server 的 Ubuntu 版本）")
    # 模型
    os.makedirs(os.path.join(out, "models"))
    if args.models:
        if os.path.isdir(os.path.join(args.models, "asr")):
            shutil.copytree(os.path.join(args.models, "asr"), os.path.join(out, "models", "asr"))
        else:
            print(f"⚠ {args.models} 底下沒有 asr/：這個套件不含字幕辨識模型（部署後可用 istudyctl asr-models --fetch all 下載）", file=sys.stderr)
    # Server App 與四個 Client 版別
    os.makedirs(os.path.join(out, "app"))
    if os.path.exists(server_app) and not args.no_server_app:
        shutil.copy2(server_app, os.path.join(out, "app"))
    else:
        print("⚠ 這個套件沒有 Server App：只能用終端機 sudo ./install.sh --cli 部署", file=sys.stderr)
    for ch in CHANNELS:
        os.makedirs(os.path.join(out, "clients", ch))
        if args.clients and os.path.isdir(os.path.join(args.clients, ch)):
            for f in os.listdir(os.path.join(args.clients, ch)):
                shutil.copy2(os.path.join(args.clients, ch, f), os.path.join(out, "clients", ch))
    if args.skip_images:
        os.makedirs(os.path.join(out, "images"))
        with open(os.path.join(out, "images", "manifest.json"), "w") as f:
            json.dump({}, f)
        with open(os.path.join(out, "images", "istudy-images.tar"), "wb"):
            pass
    else:
        build_images(out, version)
    # SHA256SUMS（install.sh 與 istudyctl 安裝前全部比對）
    lines = []
    for dirpath, _dirs, files in os.walk(out):
        for name in sorted(files):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, out)
            if rel == "SHA256SUMS":
                continue
            lines.append(f"{sha256(full)}  {rel}")
    with open(os.path.join(out, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(lines, key=lambda x: x.split("  ", 1)[1])) + "\n")
    print(f"✓ 離線部署套件：{out}（{len(lines)} 個檔案）")
    if args.zip:
        make_zip(out, os.path.join(args.out, f"istudy_{version['server']}-offline.zip"))


def make_zip(bundle: str, zip_path: str) -> None:
    """壓成單一 ZIP：最上層只有一個資料夾 istudy-server-<版本>-offline/；保留執行權限（install.sh）。
    映像 tar、.deb、.exe 已是壓縮或二進位格式，直接存入（不再壓縮）。"""
    import zipfile
    top = os.path.basename(bundle.rstrip(os.sep))
    tmp = zip_path + ".part"
    stored = (".tar", ".deb", ".exe", ".blockmap", ".gz", ".zip")
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
        for dirpath, _dirs, files in os.walk(bundle):
            for name in sorted(files):
                full = os.path.join(dirpath, name)
                arc = os.path.join(top, os.path.relpath(full, bundle))
                zf.write(full, arc, compress_type=zipfile.ZIP_STORED if name.endswith(stored) else zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(tmp) as zf:
        bad = zf.testzip()
    if bad:
        os.remove(tmp)
        sys.exit(f"ZIP 檢查失敗：{bad}")
    os.replace(tmp, zip_path)
    digest = sha256(zip_path)
    with open(zip_path + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{digest}  {os.path.basename(zip_path)}\n")
    print(f"✓ 完整離線部署包：{zip_path}（{os.path.getsize(zip_path) / 1024 ** 3:.2f} GB）")
    print(f"  SHA256：{digest}（另存 {os.path.basename(zip_path)}.sha256）")


if __name__ == "__main__":
    main()
