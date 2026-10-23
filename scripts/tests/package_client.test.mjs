/**
 * scripts/package_client.mjs 的打包驗證與發布流程測試（dev.52）。
 *
 *   node --test scripts/tests/package_client.test.mjs        （或 npm run test:packaging）
 *
 * 這是單元／整合測試：用自製的 fixture（ASAR、安裝檔、latest.yml）呼叫實際的驗證與發布函式並檢查檔案系統結果。
 * 不是 electron-builder 產生的真正安裝檔測試。另有一項讀取 Electron 官方發行版內的 default_app.asar，
 * 確認 ASAR 讀取程式能解析真正的 ASAR 格式（找不到 Electron 時略過）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { fileURLToPath } from 'url';
import {
  finalizeChannel, publishChannel, readAsarEntry, unpackedResources, validateChannelOutput, verifyPackagedUpdater, writeUbuntuManifest,
} from '../package_client.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const V = '1.0.0-dev.52'; // 測試用版本（與 VERSION.json 無關）
const UPDATER_MAIN = 'var e=require("electron");Object.defineProperty(exports,"NsisUpdater",{get(){return 1}});/* bundled electron-updater */';

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-pkgtest-'));
const sha512 = (b) => crypto.createHash('sha512').update(b).digest('base64');

/** 依 ASAR 格式寫一個封存檔（每個檔案附 SHA256 integrity，與 electron-builder 相同）。 */
function writeAsar(file, entries, { badIntegrity = false } = {}) {
  const files = {};
  const bodies = [];
  let offset = 0;
  for (const [name, content] of Object.entries(entries)) {
    const buf = Buffer.from(content);
    const hash = crypto.createHash('sha256').update(buf).digest('hex');
    files[name] = { size: buf.length, offset: String(offset), integrity: { algorithm: 'SHA256', hash: badIntegrity ? '0'.repeat(64) : hash, blockSize: 4194304, blocks: [hash] } };
    bodies.push(buf);
    offset += buf.length;
  }
  const json = Buffer.from(JSON.stringify({ files }));
  const padded = Math.ceil(json.length / 4) * 4;
  const header = Buffer.alloc(8 + padded);
  header.writeUInt32LE(4 + padded, 0);
  header.writeInt32LE(json.length, 4);
  json.copy(header, 8);
  const size = Buffer.alloc(8);
  size.writeUInt32LE(4, 0);
  size.writeUInt32LE(header.length, 4);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, Buffer.concat([size, header, ...bodies]));
}

/** 模擬 electron-builder 的 Windows 輸出目錄（fixture）。 */
function makeWinOut(dir, { channel = 'win10', version = V, ymlVersion = version, ymlPath, badSha = false, noInstaller = false, noBlockmap = false, main = UPDATER_MAIN, extraYml } = {}) {
  const label = { win7: 'Win7', win10: 'Win10', win11: 'Win11' }[channel];
  const exe = `iStudy-Client-${label}-${version}.exe`;
  const body = crypto.randomBytes(2048);
  fs.mkdirSync(dir, { recursive: true });
  if (!noInstaller) fs.writeFileSync(path.join(dir, exe), body);
  if (!noBlockmap) fs.writeFileSync(path.join(dir, exe + '.blockmap'), crypto.randomBytes(64));
  const sha = badSha ? sha512(Buffer.from('other')) : sha512(body);
  const p = ymlPath || exe;
  fs.writeFileSync(path.join(dir, 'latest.yml'),
    `version: ${ymlVersion}\nfiles:\n  - url: ${p}\n    sha512: ${sha}\n    size: ${body.length}\npath: ${p}\nsha512: ${sha}\nreleaseDate: '2026-10-19T00:00:00.000Z'\n`);
  if (extraYml) fs.writeFileSync(path.join(dir, extraYml), 'version: x\n');
  fs.writeFileSync(path.join(dir, 'builder-debug.yml'), 'x: 1\n');
  writeAsar(path.join(unpackedResources(dir), 'app.asar'), { 'package.json': JSON.stringify({ name: 'istudy-client-' + channel, version, main: 'main.js' }), 'main.js': main });
  return exe;
}

/** 正式目錄裡已有的舊版（有效產物）。 */
function makeOldPublished(clients, channel, marker) {
  const d = path.join(clients, channel);
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, 'old-installer.exe'), marker);
  fs.writeFileSync(path.join(d, 'latest.yml'), `version: old-${marker}\n`);
}
const listing = (d) => (fs.existsSync(d) ? fs.readdirSync(d).sort() : []);
const leftovers = (clients) => listing(clients).filter((f) => f.startsWith('.'));

// ---------------- P0-1：打包產物靜態驗證（Fail Closed） ----------------

test('P0-1 不依賴外部 ASAR 工具（沒有 @electron/asar 也不會退回檢查打包前的 main.js）', () => {
  const src = fs.readFileSync(path.join(HERE, '..', 'package_client.mjs'), 'utf8');
  assert.ok(!src.includes("require('@electron/asar')") && !src.includes('require("@electron/asar")'));
  assert.ok(!src.includes('以打包前 dist'));
});

test('P0-1 找不到 app.asar → FAIL（即使打包前的 dist/main.js 存在且正確）', () => {
  const d = tmp();
  fs.mkdirSync(path.join(d, 'dist', 'client-win10'), { recursive: true });
  fs.writeFileSync(path.join(d, 'dist', 'client-win10', 'main.js'), UPDATER_MAIN);
  assert.throws(() => verifyPackagedUpdater(unpackedResources(path.join(d, 'out')), { version: V }), /找不到 .*app\.asar/);
});

test('P0-1 ASAR 損壞或無法讀取 → FAIL', () => {
  const d = tmp();
  const res = path.join(d, 'resources');
  writeAsar(path.join(res, 'app.asar'), { 'package.json': JSON.stringify({ version: V, main: 'main.js' }), 'main.js': UPDATER_MAIN });
  const full = fs.readFileSync(path.join(res, 'app.asar'));
  fs.writeFileSync(path.join(res, 'app.asar'), full.subarray(0, full.length - 20)); // 截斷
  assert.throws(() => verifyPackagedUpdater(res, { version: V }), /超出檔案範圍|讀不到打包產物的 package\.json/);
  fs.writeFileSync(path.join(res, 'app.asar'), Buffer.from('not an asar file at all, just text'));
  assert.throws(() => verifyPackagedUpdater(res, { version: V }), /ASAR/);
  writeAsar(path.join(res, 'app.asar'), { 'package.json': JSON.stringify({ version: V, main: 'main.js' }), 'main.js': UPDATER_MAIN }, { badIntegrity: true });
  assert.throws(() => verifyPackagedUpdater(res, { version: V }), /雜湊值不符/);
});

test('P0-1 找不到主程序 → FAIL', () => {
  const d = tmp();
  writeAsar(path.join(d, 'app.asar'), { 'package.json': JSON.stringify({ version: V, main: 'main.js' }), 'other.js': UPDATER_MAIN });
  assert.throws(() => verifyPackagedUpdater(d, { version: V }), /找不到打包產物的主程序 main\.js/);
  writeAsar(path.join(d, 'app.asar'), { 'package.json': JSON.stringify({ version: V }), 'main.js': UPDATER_MAIN });
  assert.throws(() => verifyPackagedUpdater(d, { version: V }), /沒有指定主程序/);
  writeAsar(path.join(d, 'app.asar'), { 'main.js': UPDATER_MAIN });
  assert.throws(() => verifyPackagedUpdater(d, { version: V }), /讀不到打包產物的 package\.json/);
});

test('P0-1 主程序缺少更新器程式碼 → FAIL', () => {
  const d = tmp();
  writeAsar(path.join(d, 'app.asar'), { 'package.json': JSON.stringify({ version: V, main: 'main.js' }), 'main.js': 'console.log("no updater")' });
  assert.throws(() => verifyPackagedUpdater(d, { version: V }), /沒有包含 electron-updater/);
  // 有字串但仍是外部載入（沒打包進去）也不行
  writeAsar(path.join(d, 'app.asar'), { 'package.json': JSON.stringify({ version: V, main: 'main.js' }), 'main.js': 'require("electron-updater");/*NsisUpdater*/' });
  assert.throws(() => verifyPackagedUpdater(d, { version: V }), /沒有包含 electron-updater/);
});

test('P0-1 產物版本與 VERSION.json 不符 → FAIL', () => {
  const d = tmp();
  writeAsar(path.join(d, 'app.asar'), { 'package.json': JSON.stringify({ version: '0.0.1-wrong', main: 'main.js' }), 'main.js': UPDATER_MAIN });
  assert.throws(() => verifyPackagedUpdater(d, { version: V }), /版本是 0\.0\.1-wrong/);
});

test('P0-1 合格產物 → 通過（僅靜態驗證）；asar:false 時檢查 resources/app/', () => {
  const d = tmp();
  writeAsar(path.join(d, 'app.asar'), { 'package.json': JSON.stringify({ version: V, main: './main.js' }), 'main.js': UPDATER_MAIN });
  assert.equal(verifyPackagedUpdater(d, { version: V }), 'main.js');
  const n = tmp();
  fs.mkdirSync(path.join(n, 'app'));
  fs.writeFileSync(path.join(n, 'app', 'package.json'), JSON.stringify({ version: V, main: 'main.js' }));
  assert.throws(() => verifyPackagedUpdater(n, { asar: false, version: V }), /找不到打包產物的主程序/);
  fs.writeFileSync(path.join(n, 'app', 'main.js'), UPDATER_MAIN);
  assert.equal(verifyPackagedUpdater(n, { asar: false, version: V }), 'main.js');
});

const realAsar = ['/opt/electron-dl/e44.9.0/resources/default_app.asar', '/opt/electron-dl/e22.3.27/resources/default_app.asar'].filter((f) => fs.existsSync(f));
test('ASAR 讀取程式能解析 Electron 官方發行版內真正的 ASAR（default_app.asar）', { skip: realAsar.length ? false : '這台電腦沒有 Electron 發行版' }, () => {
  for (const f of realAsar) {
    const pkg = JSON.parse(readAsarEntry(f, 'package.json').toString('utf8'));
    assert.ok(typeof pkg.main === 'string' && pkg.main.length > 0, f);
    assert.ok(readAsarEntry(f, pkg.main.replace(/^\.\//, '')).length > 0, f);
  }
});

// ---------------- P0-2：先驗證、後發布 ----------------

const opts = (channel = 'win10') => ({ channel, version: V, asar: true, differential: channel !== 'ubuntu' });

function expectNotPublished(fn, clients, re) {
  assert.throws(fn, re);
  assert.deepEqual(listing(path.join(clients, 'win10')), ['latest.yml', 'old-installer.exe'], '原有版本應保留');
  assert.equal(fs.readFileSync(path.join(clients, 'win10', 'old-installer.exe'), 'utf8'), 'v1');
  assert.deepEqual(leftovers(clients), [], '不應留下暫存目錄');
}

test('P0-2 latest.yml 版本錯誤 → 不發布，原有版本保留', () => {
  const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
  makeWinOut(path.join(d, 'out'), { ymlVersion: '0.0.1-wrong' });
  expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts()), clients, /驗證失敗[\s\S]*version 是「0\.0\.1-wrong」/);
});

test('P0-2 SHA512 不一致 → 不發布', () => {
  const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
  makeWinOut(path.join(d, 'out'), { badSha: true });
  expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts()), clients, /sha512 與安裝檔實際內容不符/);
});

test('P0-2 安裝檔缺失、blockmap 缺失、latest.yml 指向其他檔案、產生 dev.yml → 不發布', () => {
  for (const [o, re] of [[{ noInstaller: true }, /找不到安裝檔/], [{ noBlockmap: true }, /blockmap/], [{ ymlPath: 'other.exe' }, /應為本次的安裝檔/], [{ extraYml: 'dev.yml' }, /dev\.yml/]]) {
    const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
    makeWinOut(path.join(d, 'out'), o);
    expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts()), clients, re);
  }
});

test('P0-2 找不到 latest.yml → 不發布', () => {
  const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
  makeWinOut(path.join(d, 'out'));
  fs.rmSync(path.join(d, 'out', 'latest.yml'));
  expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts()), clients, /沒有產生 latest\.yml/);
});

test('P0-2 發布前的產物靜態驗證（P0-1）失敗 → 不發布', () => {
  const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
  makeWinOut(path.join(d, 'out'), { main: 'console.log(1)' });
  expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts()), clients, /打包產物靜態驗證/);
});

test('P0-2 發布複製途中失敗 → 清除本次未完成的檔案，原有版本保留', () => {
  const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
  makeWinOut(path.join(d, 'out'));
  let n = 0;
  const copyFile = (a, b) => { if (++n === 2) throw new Error('模擬磁碟寫入錯誤'); fs.copyFileSync(a, b); };
  expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts(), { copyFile }), clients, /發布失敗[\s\S]*模擬磁碟寫入錯誤/);
});

test('P0-2 換成新版的那一步失敗 → 放回原有版本', () => {
  const d = tmp(); const clients = path.join(d, 'clients'); makeOldPublished(clients, 'win10', 'v1');
  makeWinOut(path.join(d, 'out'));
  const rename = (a, b) => { if (path.basename(a).startsWith('.staging-')) throw new Error('模擬資料夾被占用'); fs.renameSync(a, b); };
  expectNotPublished(() => finalizeChannel(path.join(d, 'out'), clients, opts(), { rename }), clients, /模擬資料夾被占用/);
});

test('P0-2 合格產物 → 發布成功、取代舊版；其他版別不受影響', () => {
  const d = tmp(); const clients = path.join(d, 'clients');
  makeOldPublished(clients, 'win10', 'v1');
  makeOldPublished(clients, 'win7', 'w7');
  // 先讓 win11 失敗一次，確認不會動到 win10、win7
  makeWinOut(path.join(d, 'out11'), { channel: 'win11', badSha: true });
  assert.throws(() => finalizeChannel(path.join(d, 'out11'), clients, opts('win11')), /驗證失敗/);
  assert.ok(!fs.existsSync(path.join(clients, 'win11')));
  // win10 成功
  const exe = makeWinOut(path.join(d, 'out'));
  const files = finalizeChannel(path.join(d, 'out'), clients, opts());
  assert.deepEqual(files, [exe, exe + '.blockmap', 'latest.yml']);
  assert.deepEqual(listing(path.join(clients, 'win10')), [exe, exe + '.blockmap', 'latest.yml'].sort());
  assert.deepEqual(fs.readFileSync(path.join(clients, 'win10', exe)), fs.readFileSync(path.join(d, 'out', exe)));
  assert.deepEqual(listing(path.join(clients, 'win7')), ['latest.yml', 'old-installer.exe']);
  assert.equal(fs.readFileSync(path.join(clients, 'win7', 'old-installer.exe'), 'utf8'), 'w7');
  assert.deepEqual(leftovers(clients), []);
  assert.deepEqual(listing(clients), ['win10', 'win7']);
});

test('P0-2 Ubuntu：發布 .deb＋latest.json（dev.61 自動更新）；安裝檔或描述檔缺失、不符時不發布', () => {
  const d = tmp(); const clients = path.join(d, 'clients');
  const out = path.join(d, 'out'); fs.mkdirSync(out);
  assert.throws(() => finalizeChannel(out, clients, opts('ubuntu')), /找不到安裝檔/);
  assert.ok(!fs.existsSync(path.join(clients, 'ubuntu')));
  const deb = `istudy-client-ubuntu_${V}_amd64.deb`;
  fs.writeFileSync(path.join(out, deb), crypto.randomBytes(512));
  assert.throws(() => finalizeChannel(out, clients, opts('ubuntu')), /latest\.json/, '沒有描述檔：不發布');
  const m = writeUbuntuManifest(out, V);
  assert.deepEqual([m.version, m.file, m.size], [V, deb, 512]);
  fs.writeFileSync(path.join(out, deb), crypto.randomBytes(512));          // 安裝檔換了、描述檔沒更新
  assert.throws(() => finalizeChannel(out, clients, opts('ubuntu')), /SHA256 與安裝檔不符/);
  writeUbuntuManifest(out, V);
  assert.deepEqual(finalizeChannel(out, clients, opts('ubuntu')), [deb, 'latest.json']);
  assert.deepEqual(listing(path.join(clients, 'ubuntu')), [deb, 'latest.json'].sort());
  const pub = JSON.parse(fs.readFileSync(path.join(clients, 'ubuntu', 'latest.json'), 'utf8'));
  assert.equal(pub.sha256, crypto.createHash('sha256').update(fs.readFileSync(path.join(clients, 'ubuntu', deb))).digest('hex'));
  assert.deepEqual(leftovers(clients), []);
});

test('驗證函式回傳全部問題（validateChannelOutput）', () => {
  const d = tmp();
  makeWinOut(d, { ymlVersion: 'x', badSha: true, noBlockmap: true });
  try {
    validateChannelOutput(d, opts());
    assert.fail('應該失敗');
  } catch (e) {
    assert.ok(e.problems.length >= 3, e.problems.join(' | '));
  }
  publishChannel; // 匯出存在
});
