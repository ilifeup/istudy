/**
 * dev.61：Client 連線偵測（packages/shell/src/health.ts）的狀態轉換測試。
 *
 *   node --test scripts/tests/health.test.mjs
 *
 * 用本機 HTTP 伺服器模擬 Server 停止、恢復；時間間隔縮短（正常 400 ms、失敗 100 ms），驗證：
 * - 斷線：連續 2 次失敗才顯示 ✗；第一次失敗後改用「失敗間隔」，所以很快就顯示 ✗（不用等兩個正常間隔）。
 * - 恢復：失敗期間持續重試，Server 一恢復，下一次檢查就改回 ✓，不需要重開 App。
 * - 畫面按「重試」或收到 online／offline 事件時呼叫 check()：立即檢查。
 * 這是模擬測試；實際拔掉、插回網路線需要在教室電腦上驗證（NOT_TESTED）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import { build } from 'esbuild';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-health-'));
const out = path.join(tmp, 'health.cjs');
await build({ entryPoints: [path.join(ROOT, 'packages', 'shell', 'src', 'health.ts')], bundle: true, platform: 'node', format: 'cjs', outfile: out, logLevel: 'error' });
const { HealthChecker } = createRequire(import.meta.url)(out);

function server() {
  let up = true;
  const s = http.createServer((req, res) => {
    if (!up) { req.socket.destroy(); return; }
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ ok: true, db: 'ok', version: '1.0.0-dev.61', api_level: 1, min_api_level: 1 }));
  });
  return new Promise((resolve) => s.listen(0, '127.0.0.1', () => resolve({
    base: `http://127.0.0.1:${s.address().port}`, setUp: (v) => { up = v; }, close: () => new Promise((r) => s.close(r)),
  })));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (fn()) return Date.now() - t0;
    await sleep(10);
  }
  return null;
}

test('斷線很快顯示 ✗，恢復後自動改回 ✓（不用重開 App）', async (t) => {
  const srv = await server();
  const states = [];
  const h = new HealthChecker(srv.base, 1, () => undefined, 400, 100, 300);
  t.after(async () => { h.stop(); await srv.close(); });   // 斷言失敗時也停止計時器，測試才會結束
  h.onChange((s) => states.push(s.state));
  h.start();
  assert.ok(await waitFor(() => h.current.state === 'ok', 2000) !== null, '一開始連線正常');
  srv.setUp(false);                       // 拔掉網路線
  const down = await waitFor(() => h.current.state === 'fail', 3000);
  assert.ok(down !== null, '斷線後顯示 ✗');
  // 第一次失敗最晚在一個正常間隔（400 ms）後發生，第二次在失敗間隔（100 ms）後：不必等兩個正常間隔（dev.60 是 800 ms 以上）
  assert.ok(down < 400 + 100 + 200, `斷線到顯示 ✗ 花了 ${down} ms`);
  srv.setUp(true);                        // 插回網路線
  const back = await waitFor(() => h.current.state === 'ok', 2000);
  assert.ok(back !== null && back < 100 + 300 + 150, `恢復到顯示 ✓ 花了 ${back} ms`);
  assert.deepEqual(states.filter((s, i) => s !== states[i - 1]), ['ok', 'fail', 'ok']);
});

test('只失敗一次（網路短暫抖動）不會顯示 ✗', async (t) => {
  const srv = await server();
  const h = new HealthChecker(srv.base, 1, () => undefined, 400, 150, 300);
  t.after(async () => { h.stop(); await srv.close(); });   // 斷言失敗時也停止計時器，測試才會結束
  h.start();
  await waitFor(() => h.current.state === 'ok', 2000);
  srv.setUp(false);
  await h.check();                        // 失敗一次
  assert.equal(h.current.state, 'ok');
  srv.setUp(true);
  await h.check();
  assert.equal(h.current.state, 'ok');
});

test('online／offline 事件或按「重試」：立即檢查（不等排程）', async (t) => {
  const srv = await server();
  const h = new HealthChecker(srv.base, 1, () => undefined, 60000, 60000, 300);   // 排程很久才會再檢查
  t.after(async () => { h.stop(); await srv.close(); });   // 斷言失敗時也停止計時器，測試才會結束
  h.start();
  await waitFor(() => h.current.state === 'ok', 2000);
  srv.setUp(false);
  await h.check();                        // offline 事件
  await h.check();                        // 重試
  assert.equal(h.current.state, 'fail');
  srv.setUp(true);
  await h.check();                        // online 事件
  assert.equal(h.current.state, 'ok');
});
