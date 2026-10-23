/**
 * dev.61 Client 更新的共用邏輯（apps/client/electron/updater.ts）：閒置才安裝、Ubuntu 狀態檔對應、重新開啟程序。
 *
 *   node --test scripts/tests/client_updater.test.mjs
 *
 * 重新開啟程序用真的 /bin/sh 執行（模擬「更新服務寫入狀態檔」→「重新開啟 App」）。
 * electron-updater 的實際下載安裝、Ubuntu 的 systemd 服務需要在 Client 實機驗證（NOT_TESTED）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import { build } from 'esbuild';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-upd-'));
const out = path.join(tmp, 'updater.cjs');
await build({ entryPoints: [path.join(ROOT, 'apps', 'client', 'electron', 'updater.ts')], bundle: true, platform: 'node', format: 'cjs', outfile: out, logLevel: 'error' });
const { IdleInstaller, ubuntuStatus, relaunchScript, noticeText } = createRequire(import.meta.url)(out);

test('已下載的新版：沒有人登入連續 2 分鐘才安裝；有人登入就重新計時；只觸發一次', () => {
  const t = new IdleInstaller(120000);
  assert.equal(t.tick(true, false, 0), false);
  assert.equal(t.tick(true, false, 119000), false);
  assert.equal(t.tick(true, true, 125000), false, '有人登入：不安裝');
  assert.equal(t.tick(true, false, 130000), false, '登出後重新計時');
  assert.equal(t.tick(true, false, 250000), true);
  assert.equal(t.tick(true, false, 400000), false, '只觸發一次');
  const n = new IdleInstaller(1000);
  assert.equal(n.tick(false, false, 0), false);
  assert.equal(n.tick(false, false, 5000), false, '沒有下載好的新版：不安裝');
});

test('Ubuntu 更新服務的狀態檔 → 回報給 Server 的更新狀態', () => {
  const f = path.join(tmp, 'status.json');
  assert.equal(ubuntuStatus(path.join(tmp, '沒有.json'), '1.0.0-dev.61'), null, '沒有服務（舊版手動安裝）');
  const w = (o) => fs.writeFileSync(f, JSON.stringify(o));
  w({ state: 'waiting_close', available: '1.0.0-dev.62', checked_at: 'x' });
  assert.deepEqual([ubuntuStatus(f, '1.0.0-dev.61').state, ubuntuStatus(f, '1.0.0-dev.61').available], ['downloaded', '1.0.0-dev.62']);
  w({ state: 'installed', available: '1.0.0-dev.62' });
  assert.equal(ubuntuStatus(f, '1.0.0-dev.62').state, 'up_to_date', '已經是新版在執行');
  w({ state: 'failed', error: 'E'.repeat(500), available: '1.0.0-dev.62' });
  const s = ubuntuStatus(f, '1.0.0-dev.61');
  assert.deepEqual([s.state, s.error.length], ['failed', 200]);
  w({ state: 'up_to_date' });
  assert.equal(ubuntuStatus(f, '1.0.0-dev.61').state, 'up_to_date');
  assert.match(noticeText('1.0.0-dev.62'), /沒有人登入時，App 會自動更新並重新開啟/);
});

test('重新開啟程序：等更新服務處理完這次要求才開啟 App；指令列不含 App 路徑', async () => {
  const status = path.join(tmp, 'st2.json');
  const marker = path.join(tmp, 'relaunched');
  const app = path.join(tmp, 'fake-app.sh');
  fs.writeFileSync(app, `#!/bin/sh\necho started > "${marker}"\n`, { mode: 0o755 });
  fs.writeFileSync(status, JSON.stringify({ state: 'waiting_close' }, null, 1));
  const script = relaunchScript();
  assert.ok(!script.includes(app));
  const p = spawn('/bin/sh', ['-c', script], { env: { ...process.env, ISTUDY_RELAUNCH: app, ISTUDY_UPDATE_STATUS: status, ISTUDY_REQUEST_ID: '1000-42' } });
  await new Promise((r) => setTimeout(r, 3000));
  assert.ok(!fs.existsSync(marker), '更新還沒完成：還沒重新開啟');
  // 更新服務完成（Python json.dump(indent=1) 的格式）
  fs.writeFileSync(status, JSON.stringify({ state: 'installed', request_id: '1000-42' }, null, 1));
  const code = await new Promise((r) => p.on('exit', r));
  assert.equal(code, 0);
  assert.equal(fs.readFileSync(marker, 'utf8').trim(), 'started');
});
