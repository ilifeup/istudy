#!/usr/bin/env node
/**
 * 打包 Client App 安裝檔（第 18 章）：Win10、Win11、Ubuntu、Win7 各自一個安裝檔與更新描述檔。
 *
 *   npm install                                    # 第一次（需要網路；產生 package-lock.json）。之後可改用 npm ci
 *   node scripts/package_client.mjs --channel win10   → dist/clients/win10/iStudy-Client-Win10-<版本>.exe＋latest.yml
 *   node scripts/package_client.mjs --channel win11
 *   node scripts/package_client.mjs --channel ubuntu  → dist/clients/ubuntu/istudy-client-ubuntu_<版本>_amd64.deb（＋latest-linux.yml）
 *   node scripts/package_client.mjs --channel win7    （Electron 22.3.27）
 *   node scripts/package_client.mjs --all             （這台電腦能打包的版別：Windows 上三個 Windows 版；Ubuntu 上 Ubuntu 版）
 *
 * Windows 安裝檔在 Windows 10／11 上打包（會寫入 .exe 圖示與版本資訊）。在 Linux 上打包 Windows 版需要先安裝 wine
 * （electron-builder 要執行 NSIS 產生的安裝程式來建立解除安裝程式）。Ubuntu .deb 只能在 Linux 上打包。
 * 產出的 dist/clients/ 交給 build_bundle.py --clients dist/clients，部署或更新時放到 Server 的 /srv/istudy/updates/<版別>/，
 * Client 每次啟動與每 6 小時從 http://<Server>/updates/<版別>/ 檢查更新。
 */
import { createRequire } from 'module';
import { execFileSync } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const VERSION = JSON.parse(fs.readFileSync(path.join(ROOT, 'VERSION.json'), 'utf8'));
const DEFAULT_SERVER = 'http://10.3.3.100:4080';
const LABEL = { win7: 'Win7', win10: 'Win10', win11: 'Win11', ubuntu: 'Ubuntu' };
const OS_NAME = { win7: 'Windows 7 SP1（64 位元）', win10: 'Windows 10（64 位元）', win11: 'Windows 11（64 位元）' };

const arg = (n) => { const i = process.argv.indexOf('--' + n); return i >= 0 ? process.argv[i + 1] : undefined; };

/** 安裝前確認作業系統與版別相符；第一次安裝時寫入 %ProgramData%\iStudy\client.json（Server 位址）。 */
export function installerNsh(channel) {
  return `; 由 scripts/package_client.mjs 產生（${channel}）
!define ISTUDY_CHANNEL "${channel}"

!macro customInit
  ; 版別與作業系統必須相符。版本一律讀登錄 HKLM 的 SOFTWARE 下 Microsoft／Windows NT／CurrentVersion：
  ;   不受相容性模式與安裝程式 manifest 影響（WinVer.nsh 的 GetVersionEx 會受 manifest 影響）；
  ;   不用 ProductName（Windows 11 的 ProductName 仍寫 Windows 10）。
  ;   Win7  ：CurrentVersion＝6.1、CurrentBuildNumber≥7601（SP1）
  ;   Win10 ：CurrentMajorVersionNumber＝10、CurrentBuildNumber＜22000
  ;   Win11 ：CurrentMajorVersionNumber＝10、CurrentBuildNumber≥22000（Microsoft 以組建號區分 Win10／Win11）
  ;   讀不到組建號或不屬於以上三種：不猜測，停止安裝並顯示實際偵測值。
  ReadRegDWORD $0 HKLM "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion" "CurrentMajorVersionNumber"
  ReadRegDWORD $5 HKLM "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion" "CurrentMinorVersionNumber"
  ReadRegStr $1 HKLM "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion" "CurrentBuildNumber"
  ReadRegStr $2 HKLM "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion" "CurrentVersion"
  \${If} $0 == ""
    StrCpy $6 "$2"
  \${Else}
    StrCpy $6 "$0.$5"
  \${EndIf}
  StrCpy $3 "other"
  StrCpy $4 "無法判斷的 Windows 版本"
  StrCpy $7 "iStudy 只支援 Windows 7 SP1、Windows 10、Windows 11（64 位元）。"
  \${If} $1 == ""
    StrCpy $4 "讀不到 Windows 組建號"
  \${ElseIf} $0 == 10
    \${If} $1 >= 22000
      StrCpy $3 "win11"
      StrCpy $4 "Windows 11"
      StrCpy $7 "請改用 Win11 版安裝檔（iStudy-Client-Win11-…exe）。"
    \${Else}
      StrCpy $3 "win10"
      StrCpy $4 "Windows 10"
      StrCpy $7 "請改用 Win10 版安裝檔（iStudy-Client-Win10-…exe）。"
    \${EndIf}
  \${ElseIf} $2 == "6.1"
    \${If} $1 >= 7601
      StrCpy $3 "win7"
      StrCpy $4 "Windows 7 SP1"
      StrCpy $7 "請改用 Win7 版安裝檔（iStudy-Client-Win7-…exe）。"
    \${Else}
      StrCpy $4 "Windows 7（未安裝 SP1）"
      StrCpy $7 "請先安裝 Windows 7 Service Pack 1，再使用 Win7 版安裝檔。"
    \${EndIf}
  \${EndIf}
  \${If} $3 != "\${ISTUDY_CHANNEL}"
    MessageBox MB_OK|MB_ICONSTOP "這是 iStudy ${LABEL[channel]} 版，只能安裝在 ${OS_NAME[channel]}。$\\r$\\n$\\r$\\n這台電腦偵測到：$4$\\r$\\n（Windows NT 版本 $6，組建號 $1）$\\r$\\n$\\r$\\n$7" /SD IDOK
    Quit
  \${EndIf}
!macroend

!macro customInstall
  ; 安裝在使用者資料夾（不需系統管理員）。Windows 預設權限允許一般使用者在 %ProgramData% 建立資料夾與檔案
  ; （Users：建立資料夾／寫入資料；建立者擁有完全控制），所以 client.json 的位置與 Client 讀取的位置一致。
  ; 若這台電腦的權限被改過而寫不進去，就略過：Client 會使用內建的同一個預設位址。
  ReadEnvStr $5 "PROGRAMDATA"
  StrCmp $5 "" istudy_cfg_done 0
  IfFileExists "$5\\iStudy\\client.json" istudy_cfg_done 0
  ClearErrors
  CreateDirectory "$5\\iStudy"
  FileOpen $6 "$5\\iStudy\\client.json" w
  IfErrors istudy_cfg_done 0
  FileWrite $6 '{"server":"${DEFAULT_SERVER}"}$\\r$\\n'
  FileClose $6
  istudy_cfg_done:
!macroend
`;
}

const AFTER_INSTALL = `#!/bin/sh
# iStudy Client（Ubuntu）安裝後：指令連結、Chromium 沙盒、AppArmor、Server 位址、開機自動啟動
set -e
APP=/opt/iStudy
ln -sf "$APP/istudy-client" /usr/bin/istudy-client
chown root:root "$APP/chrome-sandbox" && chmod 4755 "$APP/chrome-sandbox" || true
if [ -f /etc/apparmor.d/abi/4.0 ] && command -v apparmor_parser >/dev/null 2>&1; then
  cat > /etc/apparmor.d/istudy-client <<'AA'
abi <abi/4.0>,
include <tunables/global>

profile istudy-client /opt/iStudy/istudy-client flags=(unconfined) {
  userns,

  include if exists <local/istudy-client>
}
AA
  apparmor_parser -r -T -W /etc/apparmor.d/istudy-client || true
fi
mkdir -p /etc/istudy
[ -f /etc/istudy/client.json ] || printf '{"server":"${DEFAULT_SERVER}"}\\n' > /etc/istudy/client.json
mkdir -p /etc/xdg/autostart
cat > /etc/xdg/autostart/istudy-client.desktop <<'DESK'
[Desktop Entry]
Type=Application
Name=iStudy
Exec=/opt/iStudy/istudy-client --hidden
Icon=istudy-client
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESK
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
# dev.61 自動更新：root 的系統服務從 Server 的 /updates/ubuntu/ 下載、核對並安裝新版（學生帳號不需要 sudo）
UPD=/opt/iStudy/resources/istudy-client-update.py
if [ -f "$UPD" ] && command -v python3 >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
  chmod 0755 "$UPD"
  cat > /etc/tmpfiles.d/istudy-client-update.conf <<'TMP'
# App 沒有人登入時寫「立即更新」要求的地方（任何人可寫入、不能列出或刪除別人的檔案）
d /run/istudy-client-update 1733 root root -
TMP
  cat > /etc/systemd/system/istudy-client-update.service <<'UNIT'
[Unit]
Description=iStudy Client 自動更新（從 iStudy Server 下載新版並安裝）
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 -I /opt/iStudy/resources/istudy-client-update.py
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=1800
UNIT
  cat > /etc/systemd/system/istudy-client-update.timer <<'UNIT'
[Unit]
Description=iStudy Client 定時檢查更新（開機 2 分鐘後、之後每 30 分鐘）

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
UNIT
  cat > /etc/systemd/system/istudy-client-update-boot.service <<'UNIT'
[Unit]
Description=iStudy Client 開機安裝已下載的新版（登入畫面出現前，不連網）
After=local-fs.target
Before=display-manager.service systemd-user-sessions.service

[Service]
Type=oneshot
Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 -I /opt/iStudy/resources/istudy-client-update.py --boot
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
UNIT
  cat > /etc/systemd/system/istudy-client-update-now.service <<'UNIT'
[Unit]
Description=iStudy Client 立即更新（App 在沒有人登入時要求）

[Service]
Type=oneshot
Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/python3 -I /opt/iStudy/resources/istudy-client-update.py --request
TimeoutStartSec=1800
UNIT
  cat > /etc/systemd/system/istudy-client-update-now.path <<'UNIT'
[Unit]
Description=iStudy Client 立即更新的要求

[Path]
DirectoryNotEmpty=/run/istudy-client-update
Unit=istudy-client-update-now.service

[Install]
WantedBy=paths.target
UNIT
  if [ -d /run/systemd/system ]; then
    systemd-tmpfiles --create /etc/tmpfiles.d/istudy-client-update.conf || true
    systemctl daemon-reload || true
    systemctl enable istudy-client-update-boot.service >/dev/null 2>&1 || true
    systemctl enable --now istudy-client-update.timer istudy-client-update-now.path >/dev/null 2>&1 || true
  fi
else
  echo "iStudy：沒有 python3 或 systemd，自動更新沒有啟用（新版請用 sudo apt install ./新版.deb 安裝）" >&2
fi
exit 0
`;

const AFTER_REMOVE = `#!/bin/sh
# 移除時清掉指令連結、開機自動啟動與 AppArmor 設定；保留 /etc/istudy/client.json
case "$1" in
  remove|purge)
    rm -f /usr/bin/istudy-client /etc/xdg/autostart/istudy-client.desktop
    if [ -d /run/systemd/system ]; then
      systemctl disable --now istudy-client-update.timer istudy-client-update-now.path istudy-client-update-boot.service >/dev/null 2>&1 || true
    fi
    rm -f /etc/systemd/system/istudy-client-update.service /etc/systemd/system/istudy-client-update.timer \
      /etc/systemd/system/istudy-client-update-boot.service /etc/systemd/system/istudy-client-update-now.service \
      /etc/systemd/system/istudy-client-update-now.path /etc/tmpfiles.d/istudy-client-update.conf
    [ -d /run/systemd/system ] && systemctl daemon-reload >/dev/null 2>&1 || true
    if [ -f /etc/apparmor.d/istudy-client ]; then
      command -v apparmor_parser >/dev/null 2>&1 && apparmor_parser -R /etc/apparmor.d/istudy-client 2>/dev/null || true
      rm -f /etc/apparmor.d/istudy-client
    fi
    ;;
esac
exit 0
`;

function hasCommand(cmd) {
  try {
    execFileSync('sh', ['-c', `command -v ${cmd}`], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

/** 這台電腦能不能打包這個版別；不能時回傳原因。 */
function cannotBuild(channel) {
  if (channel === 'ubuntu') {
    return process.platform === 'linux' ? null : 'Ubuntu .deb 只能在 Linux（Ubuntu）上打包';
  }
  if (process.platform === 'win32') return null;
  if (process.platform === 'linux' && (hasCommand('wine') || hasCommand('wine64'))) return null;
  return 'Windows 安裝檔請在 Windows 10／11 上打包；要在 Ubuntu 上打包需先安裝 wine（sudo apt install wine）';
}

/** main.js 是否真的包含 electron-updater（不是留給執行時再找的外部模組）。特徵為何可靠：
 *  ① 沒打包進去時，esbuild 會留下外部載入 require("electron-updater")；② 打包進去時，electron-updater 的入口
 *  會匯出 NsisUpdater，esbuild 不改屬性名稱與字串，所以一定留著；iStudy 自己的程式（apps/、packages/）沒有這個字。
 *  兩個條件都成立只代表「程式碼在產物裡」，不代表自動更新已通過實機測試。 */
function updaterBundled(js) {
  return !/require\(\s*["']electron-updater["']\s*\)/.test(js) && js.includes('NsisUpdater');
}

/** 檢查 electron-builder 產生的 latest.yml：版本、安裝檔名稱、SHA512 都要和實際檔案一致。 */
export function verifyUpdateInfo(dir, version, installer) {
  const yml = path.join(dir, 'latest.yml');
  if (!fs.existsSync(yml)) return ['沒有產生 latest.yml'];
  const y = fs.readFileSync(yml, 'utf8');
  const get = (re) => ((y.match(re) || [])[1] || '').trim().replace(/^['"]|['"]$/g, '');
  const problems = [];
  const v = get(/^version:\s*(.+)$/m);
  const p = get(/^path:\s*(.+)$/m);
  const sha = get(/^sha512:\s*(.+)$/m);
  const url = get(/^\s*-\s*url:\s*(.+)$/m);
  if (v !== version) problems.push(`version 是「${v}」，應為「${version}」`);
  if (installer && p !== installer) problems.push(`path 是「${p}」，應為本次的安裝檔「${installer}」`);
  if (!p || !fs.existsSync(path.join(dir, p))) problems.push(`path「${p}」找不到對應的安裝檔`);
  if (url !== p) problems.push(`files.url「${url}」與 path「${p}」不同`);
  if (p && fs.existsSync(path.join(dir, p))) {
    const real = crypto.createHash('sha512').update(fs.readFileSync(path.join(dir, p))).digest('base64');
    if (sha !== real) problems.push('sha512 與安裝檔實際內容不符');
  }
  return problems;
}

// ---------- 打包產物驗證與發布（dev.52）：任何一項不符都視為失敗（Fail Closed），驗證全部通過才發布 ----------

/** 讀取 ASAR 封存檔裡的一個檔案。格式（與 electron-builder、@electron/asar 相同）：
 *  [UInt32 4][UInt32 目錄大小 N] ＋ [UInt32 內容大小][Int32 JSON 長度][JSON 目錄] ＋ 檔案內容（從 8＋N 起算 offset）。
 *  不依賴外部套件（沒有「讀取工具不存在」的情況）；檔頭、目錄、範圍、SHA256 任何不符都丟出錯誤。 */
export function readAsarEntry(asarPath, name) {
  if (!fs.existsSync(asarPath)) throw new Error(`找不到 ${asarPath}`);
  const fd = fs.openSync(asarPath, 'r');
  try {
    const total = fs.fstatSync(fd).size;
    const head = Buffer.alloc(8);
    if (total < 16 || fs.readSync(fd, head, 0, 8, 0) !== 8 || head.readUInt32LE(0) !== 4) throw new Error('ASAR 檔頭格式不正確');
    const headerSize = head.readUInt32LE(4);
    if (headerSize < 8 || 8 + headerSize > total) throw new Error('ASAR 目錄大小不正確');
    const hb = Buffer.alloc(headerSize);
    if (fs.readSync(fd, hb, 0, headerSize, 8) !== headerSize) throw new Error('讀不完 ASAR 目錄');
    const len = hb.readInt32LE(4);
    if (len <= 0 || 8 + len > headerSize) throw new Error('ASAR 目錄長度不正確');
    let node;
    try { node = JSON.parse(hb.toString('utf8', 8, 8 + len)); } catch { throw new Error('ASAR 目錄不是有效的 JSON'); }
    for (const part of name.split('/')) {
      node = node && node.files && Object.prototype.hasOwnProperty.call(node.files, part) ? node.files[part] : null;
    }
    if (!node || node.files || !Number.isSafeInteger(node.size)) throw new Error(`ASAR 內沒有 ${name}`);
    let data;
    if (node.unpacked) {
      const f = path.join(asarPath + '.unpacked', ...name.split('/'));
      if (!fs.existsSync(f)) throw new Error(`找不到 ${f}`);
      data = fs.readFileSync(f);
    } else {
      const off = 8 + headerSize + Number(node.offset);
      if (!Number.isSafeInteger(off) || off + node.size > total) throw new Error(`ASAR 內的 ${name} 超出檔案範圍（檔案不完整）`);
      data = Buffer.alloc(node.size);
      if (fs.readSync(fd, data, 0, node.size, off) !== node.size) throw new Error(`讀不完 ASAR 內的 ${name}`);
    }
    if (data.length !== node.size) throw new Error(`ASAR 內的 ${name} 大小不符`);
    if (node.integrity && node.integrity.hash && crypto.createHash('sha256').update(data).digest('hex') !== node.integrity.hash) {
      throw new Error(`ASAR 內的 ${name} 雜湊值不符（檔案損壞）`);
    }
    return data;
  } finally {
    fs.closeSync(fd);
  }
}

/** electron-builder 的 Windows 未封裝輸出：x64（預設架構）是 win-unpacked，其他架構是 win-<arch>-unpacked。 */
export function unpackedResources(outDir, arch = 'x64') {
  return path.join(outDir, arch === 'x64' ? 'win-unpacked' : `win-${arch}-unpacked`, 'resources');
}

/** 檢查真正的打包產物（asar:true → resources/app.asar；asar:false → resources/app/），不看打包前的 dist。
 *  主程序由產物內 package.json 的 main 決定。只證明 electron-updater 的程式碼在產物裡（靜態驗證），
 *  不代表自動更新已通過實機測試。 */
export function verifyPackagedUpdater(resourcesDir, { asar = true, version } = {}) {
  const read = asar
    ? (name) => readAsarEntry(path.join(resourcesDir, 'app.asar'), name)
    : (name) => {
        const f = path.join(resourcesDir, 'app', ...name.split('/'));
        if (!fs.existsSync(f)) throw new Error(`找不到 ${f}`);
        return fs.readFileSync(f);
      };
  let pkg;
  try {
    pkg = JSON.parse(read('package.json').toString('utf8'));
  } catch (e) {
    throw new Error(`讀不到打包產物的 package.json：${e.message}`);
  }
  if (version && pkg.version !== version) throw new Error(`打包產物的版本是 ${pkg.version}，應為 ${version}`);
  if (typeof pkg.main !== 'string' || !pkg.main.trim()) throw new Error('打包產物的 package.json 沒有指定主程序（main）');
  const mainName = pkg.main.trim().replace(/^\.\//, '');
  let js;
  try {
    js = read(mainName).toString('utf8');
  } catch (e) {
    throw new Error(`找不到打包產物的主程序 ${mainName}：${e.message}`);
  }
  if (!updaterBundled(js)) throw new Error(`打包產物的主程序 ${mainName} 沒有包含 electron-updater`);
  return mainName;
}

/** dev.61 Ubuntu 自動更新的描述檔 latest.json（版本、檔名、大小、SHA256），Client 的更新服務據此下載與核對。 */
export function writeUbuntuManifest(outDir, version) {
  const file = `istudy-client-ubuntu_${version}_amd64.deb`;
  const full = path.join(outDir, file);
  const data = fs.readFileSync(full);
  const m = { channel: 'ubuntu', version, file, size: data.length, sha256: crypto.createHash('sha256').update(data).digest('hex') };
  fs.writeFileSync(path.join(outDir, 'latest.json'), JSON.stringify(m, null, 1) + '\n');
  return m;
}

export function verifyUbuntuManifest(outDir, version, installer) {
  const problems = [];
  let m;
  try {
    m = JSON.parse(fs.readFileSync(path.join(outDir, 'latest.json'), 'utf8'));
  } catch (e) {
    return [`找不到或無法讀取 latest.json（${e.message}）`];
  }
  if (m.version !== version) problems.push(`版本是 ${m.version}，應為 ${version}`);
  if (m.file !== installer) problems.push(`檔名是 ${m.file}，應為 ${installer}`);
  try {
    const data = fs.readFileSync(path.join(outDir, installer));
    if (m.size !== data.length) problems.push('大小與安裝檔不符');
    if (m.sha256 !== crypto.createHash('sha256').update(data).digest('hex')) problems.push('SHA256 與安裝檔不符');
  } catch {
    /* 安裝檔缺少另外回報 */
  }
  return problems;
}

/** 發布前驗證一個版別的 electron-builder 輸出；通過時回傳要發布的檔案，否則丟出錯誤（error.problems 列出全部問題）。 */
export function validateChannelOutput(outDir, { channel, version, asar = true, differential = true }) {
  const isWin = channel !== 'ubuntu';
  const installer = isWin ? `iStudy-Client-${LABEL[channel]}-${version}.exe` : `istudy-client-ubuntu_${version}_amd64.deb`;
  const nonEmptyFile = (f) => {
    try {
      const st = fs.statSync(path.join(outDir, f));
      return st.isFile() && st.size > 0;
    } catch {
      return false;
    }
  };
  const problems = [];
  const files = [installer];
  if (!nonEmptyFile(installer)) problems.push(`找不到安裝檔或檔案是空的：${installer}`);
  if (isWin) {
    if (differential) {
      const bm = `${installer}.blockmap`; // electron-updater 以「安裝檔網址＋.blockmap」做差異下載
      files.push(bm);
      if (!nonEmptyFile(bm)) problems.push(`找不到 ${bm} 或檔案是空的（差異更新需要）`);
    }
    files.push('latest.yml');
    const listed = fs.existsSync(outDir) ? fs.readdirSync(outDir) : [];
    const otherYml = listed.filter((f) => /\.yml$/.test(f) && f !== 'latest.yml' && !f.startsWith('builder-'));
    if (otherYml.length) problems.push(`產生了 Client 不會讀取的更新描述檔：${otherYml.join('、')}（應只有 latest.yml）`);
    for (const p of verifyUpdateInfo(outDir, version, installer)) problems.push(`latest.yml：${p}`);
    try {
      verifyPackagedUpdater(unpackedResources(outDir), { asar, version });
    } catch (e) {
      problems.push(`打包產物靜態驗證：${e.message}`);
    }
  } else {
    files.push('latest.json');   // dev.61：Ubuntu 自動更新服務讀這個檔
    for (const p of verifyUbuntuManifest(outDir, version, installer)) problems.push(`latest.json：${p}`);
  }
  if (problems.length) {
    const err = new Error(problems.join('；'));
    err.problems = problems;
    throw err;
  }
  return files;
}

/** 先複製到同一資料夾下的暫存目錄並逐檔比對 SHA256，全部成功才把 dist/clients/<版別> 換成新版。
 *  失敗時清除暫存、保留原有版本；只動這個版別的目錄。ops 只給測試注入錯誤用。 */
export function publishChannel(srcDir, files, clientsDir, channel, ops = {}) {
  const copy = ops.copyFile || fs.copyFileSync;
  const rename = ops.rename || fs.renameSync;
  const sha256 = (f) => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
  fs.mkdirSync(clientsDir, { recursive: true });
  const dest = path.join(clientsDir, channel);
  const tag = `${channel}-${process.pid}-${Date.now()}`;
  const staging = path.join(clientsDir, `.staging-${tag}`);
  const old = path.join(clientsDir, `.old-${tag}`);
  fs.mkdirSync(staging);
  try {
    for (const f of files) {
      copy(path.join(srcDir, f), path.join(staging, f));
      if (sha256(path.join(staging, f)) !== sha256(path.join(srcDir, f))) throw new Error(`複製後內容不一致：${f}`);
    }
    const hadOld = fs.existsSync(dest);
    if (hadOld) rename(dest, old);
    try {
      rename(staging, dest);
    } catch (e) {
      if (hadOld) fs.renameSync(old, dest); // 換新失敗：放回原有版本
      throw e;
    }
    if (hadOld) fs.rmSync(old, { recursive: true, force: true });
  } finally {
    fs.rmSync(staging, { recursive: true, force: true });
  }
  return dest;
}

/** electron-builder 完成後：驗證 → 發布。驗證失敗時不發布，訊息列出全部問題與建置輸出位置。 */
export function finalizeChannel(outDir, clientsDir, opts, ops) {
  let files;
  try {
    files = validateChannelOutput(outDir, opts);
  } catch (e) {
    const list = (e.problems || [e.message]).map((p) => `\n  - ${p}`).join('');
    throw new Error(`${LABEL[opts.channel]}：驗證失敗，沒有發布到 dist/clients/${opts.channel}/（原有檔案保留）${list}\n  建置輸出保留在 ${outDir}`);
  }
  try {
    publishChannel(outDir, files, clientsDir, opts.channel, ops);
  } catch (e) {
    throw new Error(`${LABEL[opts.channel]}：發布失敗，已清除本次未完成的檔案，原有檔案保留：${e.message}`);
  }
  return files;
}

function loadBuilder() {
  try {
    return require('electron-builder');
  } catch {
    console.error('找不到 electron-builder：請先在專案根目錄執行 npm install（需要網路，第一次會產生 package-lock.json）。');
    process.exit(2);
  }
}

async function packageOne(channel) {
  const c = VERSION.client[channel];
  if (!c) throw new Error(`未知的版別：${channel}`);
  const why = cannotBuild(channel);
  if (why) throw new Error(`${LABEL[channel]} 版無法在這台電腦打包：${why}`);
  const builder = loadBuilder();
  const isWin = channel !== 'ubuntu';
  // Windows 版宣稱支援自動更新：沒有 electron-updater 就不打包（Ubuntu 版由系統服務更新，不需要 electron-updater）
  if (isWin) {
    try {
      require.resolve('electron-updater');
    } catch {
      throw new Error('找不到 electron-updater：請先在專案根目錄執行 npm install（Windows 版安裝檔必須包含自動更新）');
    }
  }
  const appDir = path.join(ROOT, 'dist', `client-${channel}`);
  // 先建置程式檔：Windows 版把 electron-updater 打包進 main.js，build.mjs 會檢查，沒包進去就失敗
  const buildArgs = [path.join(ROOT, 'scripts', 'build.mjs'), '--app', 'client', '--channel', channel];
  if (isWin) buildArgs.push('--require-updater');
  execFileSync(process.execPath, buildArgs, { stdio: 'inherit' });
  const pkgFile = path.join(appDir, 'package.json');
  const pkg = JSON.parse(fs.readFileSync(pkgFile, 'utf8'));
  if (channel === 'ubuntu') pkg.name = 'istudy-client';
  pkg.description = `iStudy 助教複習系統 Client（${LABEL[channel]}）`;
  pkg.author = { name: '無相氣學', email: 'noreply@localhost' };
  fs.writeFileSync(pkgFile, JSON.stringify(pkg, null, 1) + '\n');

  const work = path.join(ROOT, 'dist', '.client-build', channel);
  fs.rmSync(work, { recursive: true, force: true });
  fs.mkdirSync(work, { recursive: true });
  const assets = path.join(ROOT, 'apps', 'client', 'assets');
  const config = {
    appId: 'tw.istudy.client',            // 四個版別同一個 ID：Win10 換裝 Win11 版時直接取代
    productName: 'iStudy',
    electronVersion: c.electron,
    directories: { app: appDir, output: path.join(work, 'out'), buildResources: work },
    files: ['**/*'],
    asar: true,
    npmRebuild: false,
    // 版本號是 1.0.0-dev.N 時，electron-builder 預設會把更新描述檔命名為 dev.yml；Client 讀的是 latest.yml，所以關閉
    detectUpdateChannel: false,
    // Windows：產生 latest.yml（Client 從 /updates/<版別>/ 讀）；Ubuntu：electron-builder 不產生，另外寫 latest.json（dev.61）
    publish: isWin ? [{ provider: 'generic', url: `${DEFAULT_SERVER}/updates/${channel}/` }] : null,
  };
  let targets;
  if (channel === 'ubuntu') {
    fs.writeFileSync(path.join(work, 'after-install.sh'), AFTER_INSTALL, { mode: 0o755 });
    fs.writeFileSync(path.join(work, 'after-remove.sh'), AFTER_REMOVE, { mode: 0o755 });
    Object.assign(config, {
      linux: {
        target: [{ target: 'deb', arch: ['x64'] }], icon: path.join(assets, 'icon.png'), category: 'Education',
        executableName: 'istudy-client', maintainer: 'iStudy <noreply@localhost>', synopsis: 'iStudy 助教複習系統 Client',
        artifactName: 'istudy-client-ubuntu_${version}_amd64.${ext}',
      },
      deb: { afterInstall: path.join(work, 'after-install.sh'), afterRemove: path.join(work, 'after-remove.sh'), priority: 'optional' },
      // dev.61：自動更新服務的程式（安裝在 /opt/iStudy/resources/，由 after-install 建立的 systemd 單元以 root 執行）
      extraResources: [{ from: path.join(ROOT, 'apps', 'client', 'linux', 'istudy-client-update.py'), to: 'istudy-client-update.py' }],
    });
    targets = builder.Platform.LINUX.createTarget('deb', builder.Arch.x64);
  } else {
    // NSIS 讀 UTF-8 需要 BOM（中文訊息）
    fs.writeFileSync(path.join(work, 'installer.nsh'), '\uFEFF' + installerNsh(channel));
    Object.assign(config, {
      win: {
        target: [{ target: 'nsis', arch: ['x64'] }], icon: path.join(assets, 'icon.ico'), executableName: 'iStudy',
        artifactName: `iStudy-Client-${LABEL[channel]}-\${version}.\${ext}`,
        signAndEditExecutable: process.platform === 'win32',
      },
      nsis: {
        oneClick: true, perMachine: false, include: path.join(work, 'installer.nsh'), shortcutName: 'iStudy', differentialPackage: true,
        createDesktopShortcut: true, createStartMenuShortcut: true, runAfterFinish: true, deleteAppDataOnUninstall: false,
      },
    });
    targets = builder.Platform.WINDOWS.createTarget('nsis', builder.Arch.x64);
  }
  // electron-builder 失敗時這裡直接丟出錯誤：還沒有發布任何檔案
  await builder.build({ targets, config, publish: 'never', projectDir: ROOT });

  // 先驗證（在 dist/.client-build/<版別>/out），全部通過才發布到 dist/clients/<版別>/
  const outDir = path.join(work, 'out');
  if (!isWin) writeUbuntuManifest(outDir, c.version);
  const files = finalizeChannel(outDir, path.join(ROOT, 'dist', 'clients'), {
    channel, version: c.version, asar: config.asar !== false, differential: isWin && config.nsis.differentialPackage !== false,
  });
  if (isWin) {
    console.log('✓ 打包產物靜態驗證：app.asar 內的主程序包含 electron-updater（自動更新功能本身尚未實機測試）');
    console.log(`✓ latest.yml：版本 ${c.version}、安裝檔名稱、SHA512 與 .blockmap 一致`);
  }
  console.log(`✓ 已發布 ${LABEL[channel]} ${c.version}（Electron ${c.electron}）→ dist/clients/${channel}/：${files.join('、')}`);
  if (!isWin) console.log('  Ubuntu 版：dev.61 起由系統服務自動更新（latest.json）；dev.60 以前的 Ubuntu Client 需先手動安裝一次 dev.61');
}

// 被其他腳本 import 時（例如只用 verifyUpdateInfo）不執行打包
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();

async function main() {
let channels;
if (process.argv.includes('--all')) {
  channels = ['win10', 'win11', 'ubuntu', 'win7'].filter((ch) => {
    const why = cannotBuild(ch);
    if (why) console.log(`略過 ${LABEL[ch]}：${why}`);
    return !why;
  });
} else {
  channels = [arg('channel') || 'win10'];
}
try {
  for (const ch of channels) await packageOne(ch);
} catch (e) {
  console.error('✗ ' + (e && e.message ? e.message : e));
  process.exit(1);
}
}
