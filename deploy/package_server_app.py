#!/usr/bin/env python3
"""把 Server App 打包成 Ubuntu 的 .deb（MVP：不需要 electron-builder，也不需要 Node）。

  python3 deploy/package_server_app.py                     # 使用 dist/server（ZIP 內已附建置好的檔案）
  python3 deploy/package_server_app.py --electron-zip ~/Downloads/electron-v44.9.0-linux-x64.zip

做的事：
  1. 取得 Electron 官方 Linux x64 版（--electron-zip 指定；沒有就從 GitHub Releases 下載），比對 SHA256。
  2. 解壓到 /opt/istudy-server-app/，主程式改名為 istudy-server-app，畫面與主程序放在 resources/app/（= dist/server）。
  3. 加上選單捷徑、圖示、AppArmor 設定（Ubuntu 24.04 起限制未授權的 user namespace，Electron 需要）。
  4. dpkg-deb 產生 dist/istudy-server-app_<版本>_amd64.deb。

產出的 .deb 由離線套件的 install.sh 安裝（build_bundle.py 會自動放進 app/）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "istudy-server-app"
OPT = f"/opt/{PKG}"
# Electron 官方發行檔的 SHA256（https://github.com/electron/electron/releases 的 SHASUMS256.txt）
ELECTRON_SHA256 = {
    "44.9.0": "ecced7ecb55697f92ca870939c5988557faa8acd8c222c232cae5913d318c8b9",
}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_electron(version: str, zip_path: str | None, cache: str) -> str:
    name = f"electron-v{version}-linux-x64.zip"
    if not zip_path:
        zip_path = os.path.join(cache, name)
        if not os.path.exists(zip_path):
            url = f"https://github.com/electron/electron/releases/download/v{version}/{name}"
            print(f"下載 {url}", flush=True)
            os.makedirs(cache, exist_ok=True)
            with urllib.request.urlopen(url, timeout=600) as r, open(zip_path + ".part", "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            os.replace(zip_path + ".part", zip_path)
    expect = ELECTRON_SHA256.get(version)
    got = sha256(zip_path)
    if expect and got != expect:
        sys.exit(f"✗ {zip_path} 的 SHA256 不符（{got}），請重新下載")
    if not expect:
        print(f"⚠ 沒有 Electron {version} 的固定雜湊值，請自行確認：{got}")
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", default=os.path.join(ROOT, "dist", "server"), help="scripts/build.mjs --app server 的產出")
    ap.add_argument("--electron-zip")
    ap.add_argument("--cache", default=os.path.join(ROOT, "dist", ".cache"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()

    info_file = os.path.join(args.app_dir, "build-info.json")
    if not os.path.exists(info_file):
        sys.exit(f"✗ 找不到 {info_file}：請先執行 node scripts/build.mjs --app server（或使用 ZIP 內附的 dist/server）")
    info = json.load(open(info_file, encoding="utf-8"))
    version, electron = info["version"], info["electron"]
    deb_version = version.replace("-", "~")  # 1.0.0-dev.46 → 1.0.0~dev.46（dpkg 版本排序：dev 版小於正式版）
    zip_path = get_electron(electron, args.electron_zip, args.cache)

    work = tempfile.mkdtemp(prefix="istudy-deb-")
    try:
        root = os.path.join(work, "pkg")
        opt = os.path.join(root, OPT.lstrip("/"))
        os.makedirs(opt)
        with zipfile.ZipFile(zip_path) as z:
            for m in z.infolist():
                z.extract(m, opt)
                mode = (m.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(os.path.join(opt, m.filename), mode)
        os.rename(os.path.join(opt, "electron"), os.path.join(opt, PKG))
        for f in ("resources/default_app.asar",):
            p = os.path.join(opt, f)
            if os.path.exists(p):
                os.remove(p)
        shutil.copytree(args.app_dir, os.path.join(opt, "resources", "app"))
        os.chmod(os.path.join(opt, PKG), 0o755)

        # 選單捷徑與圖示
        apps = os.path.join(root, "usr", "share", "applications")
        os.makedirs(apps)
        with open(os.path.join(apps, f"{PKG}.desktop"), "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=iStudy Server App\nName[zh_TW]=iStudy Server App\n"
                    "Comment=iStudy 主機管理（Super 專用）\n"
                    f"Exec={OPT}/{PKG} %U\nIcon={PKG}\nTerminal=false\nCategories=Education;System;\nStartupWMClass=iStudy Server App\n")
        for size, src in ((512, "icon.png"), (256, "icon-256.png")):
            d = os.path.join(root, "usr", "share", "icons", "hicolor", f"{size}x{size}", "apps")
            os.makedirs(d, exist_ok=True)
            shutil.copy2(os.path.join(args.app_dir, "assets", src), os.path.join(d, f"{PKG}.png"))
        os.makedirs(os.path.join(root, "usr", "bin"))
        os.symlink(f"{OPT}/{PKG}", os.path.join(root, "usr", "bin", PKG))
        # AppArmor：允許這支程式建立 user namespace（Chromium 沙盒需要；Ubuntu 24.04 起預設限制）
        aa = os.path.join(root, "etc", "apparmor.d")
        os.makedirs(aa)
        with open(os.path.join(aa, PKG), "w", encoding="utf-8") as f:
            f.write(f"abi <abi/4.0>,\ninclude <tunables/global>\n\nprofile {PKG} {OPT}/{PKG} flags=(unconfined) {{\n  userns,\n\n"
                    f"  include if exists <local/{PKG}>\n}}\n")

        # 控制檔
        deb = os.path.join(root, "DEBIAN")
        os.makedirs(deb)
        installed_kb = sum(os.path.getsize(os.path.join(dp, n)) for dp, _d, fs in os.walk(opt) for n in fs) // 1024
        with open(os.path.join(deb, "control"), "w", encoding="utf-8") as f:
            f.write(f"Package: {PKG}\nVersion: {deb_version}\nArchitecture: amd64\nMaintainer: iStudy <noreply@localhost>\n"
                    f"Installed-Size: {installed_kb}\nSection: education\nPriority: optional\n"
                    "Depends: libgtk-3-0t64 | libgtk-3-0, libnss3, libasound2t64 | libasound2, libgbm1, libxss1, libxtst6, xdg-utils\n"
                    "Recommends: pkexec | policykit-1\n"
                    "Description: iStudy Server App（Super 專用，只連 127.0.0.1:4081）\n"
                    " 心賞氣悅・無相氣學 助教複習系統的主機管理程式：初始部署、首次設定精靈、教材與轉檔管理、主機維護。\n"
                    f" Electron {electron}。\n")
        with open(os.path.join(deb, "postinst"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nset -e\n"
                    f"# Chromium 的 setuid 沙盒（沒有 AppArmor 4 時的備援）\nchown root:root {OPT}/chrome-sandbox\nchmod 4755 {OPT}/chrome-sandbox\n"
                    f"if [ -f /etc/apparmor.d/abi/4.0 ] && command -v apparmor_parser >/dev/null 2>&1; then\n"
                    f"  apparmor_parser -r -T -W /etc/apparmor.d/{PKG} || true\nfi\n"
                    "if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q || true; fi\n"
                    "if command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -q /usr/share/icons/hicolor || true; fi\n"
                    "exit 0\n")
        with open(os.path.join(deb, "postrm"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nset -e\n"
                    f"if [ \"$1\" = remove ] || [ \"$1\" = purge ]; then\n"
                    f"  if command -v apparmor_parser >/dev/null 2>&1; then apparmor_parser -R /etc/apparmor.d/{PKG} 2>/dev/null || true; fi\nfi\nexit 0\n")
        for s in ("postinst", "postrm"):
            os.chmod(os.path.join(deb, s), 0o755)
        with open(os.path.join(deb, "conffiles"), "w") as f:
            f.write(f"/etc/apparmor.d/{PKG}\n")

        os.makedirs(args.out, exist_ok=True)
        out = os.path.join(args.out, f"{PKG}_{version}_amd64.deb")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-z6", "--build", root, out], check=True)
        print(f"✓ {out}（{os.path.getsize(out) / 1024 / 1024:.1f} MB，Server App {version}，Electron {electron}）")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
