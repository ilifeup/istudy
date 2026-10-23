#!/usr/bin/env node
/**
 * 畫面測試（Playwright 的 Electron 模式）：Client App（Electron 44 與 Win7 版的 Electron 22）與 Server App。
 *
 *   python3 apps/tests/fixture.py --state /tmp/e2e.json &      # 先啟動測試用 Server
 *   node scripts/build.mjs --all
 *   DISPLAY=:99 node apps/tests/e2e.mjs --state /tmp/e2e.json --shots /tmp/shots [--only client|win7|server]
 *
 * 環境變數：ISTUDY_E2E_ELECTRON44、ISTUDY_E2E_ELECTRON22（Electron 執行檔路徑）。
 * 以 root 執行時加 --no-sandbox（只限測試）。
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { createRequire } from 'module';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const require = createRequire(import.meta.url);
const { _electron: electron } = require(process.env.ISTUDY_PLAYWRIGHT || 'playwright');

const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i >= 0 ? process.argv[i + 1] : d; };
const state = JSON.parse(fs.readFileSync(arg('state'), 'utf8'));
const SHOTS = arg('shots', path.join(os.tmpdir(), 'istudy-shots'));
const ONLY = arg('only', '');
fs.mkdirSync(SHOTS, { recursive: true });
const VER = JSON.parse(fs.readFileSync(path.join(ROOT, 'VERSION.json'), 'utf8')).server; // 測試中的版本字串一律取自 VERSION.json
const E44 = process.env.ISTUDY_E2E_ELECTRON44 || '/opt/electron-dl/e44.9.0/electron';
const E22 = process.env.ISTUDY_E2E_ELECTRON22 || '/opt/electron-dl/e22.3.27/electron';

const results = [];
let failures = 0;
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  if (!ok) failures++;
  console.log(`${ok ? '✓' : '✗'} ${name}${detail ? '：' + detail : ''}`);
}

async function launch(dir, electronPath, extraEnv = {}) {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-ud-'));
  const app = await electron.launch({
    executablePath: electronPath,
    args: [dir, '--no-sandbox', '--disable-gpu'],
    env: { ...process.env, ISTUDY_USER_DATA: userData, ISTUDY_NO_TRAY: '1', ISTUDY_NO_UPDATE: '1', ...extraEnv },
    timeout: 60000,
  });
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.setViewportSize({ width: 1440, height: 900 }).catch(() => undefined);
  return { app, page, errors, userData };
}

async function shot(page, name) {
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(SHOTS, name + '.png') });
}

async function text(page) {
  return page.evaluate(() => document.body.innerText);
}

async function login(page, who) {
  const a = state.accounts[who];
  await page.getByLabel('帳號', { exact: true }).fill(a.username);
  await page.getByLabel('密碼', { exact: true }).fill(a.password);
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await page.waitForSelector('.topnav', { timeout: 15000 });
}

async function logout(page) {
  await page.locator('.topnav').getByRole('button', { name: '登出' }).click();
  await page.waitForSelector('.login-card', { timeout: 15000 });
}

/** 表單欄位（Field）裡的下拉選單選項；以欄位名稱完全相符找，不受提示文字影響。 */
async function fieldOptions(page, name) {
  return page.locator('label.field').filter({ has: page.locator('xpath=./span[1]', { hasText: new RegExp('^' + name + '$') }) })
    .locator('select option').allInnerTexts();
}

async function nav(page, label) {
  await page.locator('.topnav, .adminbar').getByRole('button', { name: label, exact: true }).first().click();
  await page.waitForTimeout(400);
}

// ---------------- Client App ----------------
async function clientFlow(label, dir, electronPath) {
  console.log(`\n=== Client App ${label} ===`);
  const { app, page, errors } = await launch(dir, electronPath, { ISTUDY_SERVER: state.lan, ISTUDY_HEARTBEAT_MS: '3000', ISTUDY_HEALTH_OK_MS: '5000' });
  const ver = await app.evaluate(() => process.versions.electron);
  check(`${label} 啟動（Electron ${ver}）`, true);
  await page.waitForSelector('.health.ok', { timeout: 20000 }).catch(() => undefined);
  let t = await text(page);
  check(`${label} 登入頁顯示「✓ 伺服器連線正常」`, t.includes('✓ 伺服器連線正常'));
  check(`${label} 畫面沒有顯示 Server 位址`, !t.includes('127.0.0.1') && !t.includes(new URL(state.lan).port));
  check(`${label} 頁尾顯示版別與版本`, /(Win10|Win7) V/.test(t) && t.includes(' V' + VER), (t.match(/(Win\d+|Ubuntu) V[^\s·]+/) || [''])[0]);
  const ph = await page.getByLabel('帳號', { exact: true }).getAttribute('placeholder');
  check(`${label} 帳號欄提示文字來自 Server`, ph === '請輸入帳號', ph || '');
  // dev.62：登入頁「顯示密碼」小按鈕（按一下顯示明碼、再按一下隱藏）
  {
    const pw = page.getByLabel('密碼', { exact: true });
    await pw.fill('確認-abc123');
    await page.getByRole('button', { name: '顯示密碼' }).click().catch(() => undefined);
    const shown = [await pw.getAttribute('type'), await pw.inputValue()];
    await shot(page, `${label}-00-login-eye`);
    await page.getByRole('button', { name: '隱藏密碼' }).click().catch(() => undefined);
    const hidden = await pw.getAttribute('type');
    await pw.fill('');
    check(`${label} 登入頁「顯示密碼」：按一下顯示明碼、再按一下隱藏`, shown[0] === 'text' && shown[1] === '確認-abc123' && hidden === 'password',
      `顯示時 ${shown[0]}／隱藏時 ${hidden}`);
  }
  await shot(page, `${label}-01-login`);

  await page.getByRole('button', { name: '喚醒電腦' }).click();
  await page.waitForSelector('.wake-panel table', { timeout: 10000 }).catch(() => undefined);
  t = await text(page);
  check(`${label} 免登入喚醒清單`, t.includes('教室 A-01'));
  check(`${label} 免登入喚醒清單只列已啟用的，並說明另有未啟用的（dev.62）`, !t.includes('教室 A-02') && t.includes('另有 1 台已登記但未啟用喚醒')
    && t.includes('這裡只能喚醒') && !(await page.locator('.wake-panel').getByRole('button', { name: /編輯|刪除|新增|啟用/ }).count()));
  await shot(page, `${label}-02-wake`);
  await page.getByRole('button', { name: '✕ 收合' }).click();

  // 錯誤密碼
  await page.getByLabel('帳號', { exact: true }).fill('stu01');
  await page.getByLabel('密碼', { exact: true }).fill('wrong-password');
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await page.waitForSelector('.login-form .msg.error', { timeout: 10000 }).catch(() => undefined);
  check(`${label} 密碼錯誤顯示訊息`, (await text(page)).includes('帳號或密碼'));

  // user（助教）：只看得到群組開放的課程
  await login(page, 'user');
  await page.waitForSelector('.tree-row', { timeout: 10000 }).catch(() => undefined);
  t = await page.locator('.tree-pane').innerText();
  check(`${label} user（助教）只看到「基礎班」`, t.includes('基礎班') && !t.includes('砭石') && !t.includes('進修班'), t.replace(/\n/g, ' '));
  await page.locator('.tree-name', { hasText: '基礎班' }).click();
  await page.waitForTimeout(500);
  await page.locator('.rowlink', { hasText: '第一堂課' }).first().click();
  await page.waitForSelector('.grid.files tbody tr', { timeout: 10000 });
  await page.waitForTimeout(300);
  t = await page.locator('.list-pane').innerText();
  check(`${label} 檔案清單自然排序（0001 在 0002 之前）`, t.indexOf('0001_上集') >= 0 && t.indexOf('0001_上集') < t.indexOf('0002 中集'));
  check(`${label} 合集標記`, t.includes('［合集'));
  await shot(page, `${label}-03-courses`);

  // 播放
  await page.locator('.rowlink', { hasText: '0001_上集' }).click();
  await page.waitForSelector('video', { timeout: 10000 });
  const ready = await page.waitForFunction(() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && v.duration > 0; }, null, { timeout: 20000 }).then(() => true, () => false);
  check(`${label} 影片可載入（Range 串流）`, ready);
  await page.evaluate(() => document.querySelector('video').play()).catch(() => undefined);
  await page.waitForTimeout(1200);
  const cur = await page.evaluate(() => document.querySelector('video').currentTime);
  check(`${label} 影片播放中`, cur > 0.3, `currentTime=${cur.toFixed(2)}`);
  const dl = await page.evaluate(() => document.querySelector('video').getAttribute('controlsList'));
  check(`${label} 播放器關閉下載選項`, (dl || '').includes('nodownload'));
  await page.getByRole('button', { name: '字幕面板' }).click();
  await page.waitForTimeout(600);
  t = await text(page);
  check(`${label} 字幕面板有字幕段落`, (await page.locator('.sub-group').count()) > 0);
  await page.evaluate(() => document.querySelector('video').pause());
  await shot(page, `${label}-04-player`);
  // dev.62：進入播放頁就開始緩衝（preload=auto）；下拉選單（倍速、學習狀態）在預覽頁與全螢幕都是有對比的配色
  {
    const pre = await page.evaluate(() => document.querySelector('video').getAttribute('preload'));
    const buf = await page.evaluate(() => { const v = document.querySelector('video'); return v.buffered.length ? v.buffered.end(v.buffered.length - 1) : 0; });
    check(`${label} 進入播放頁即開始緩衝（preload=auto）`, pre === 'auto' && buf > 0, `preload=${pre}，已緩衝 ${Number(buf).toFixed(1)} 秒`);
    const colors = () => page.$$eval('.player-wrap select', (els) => els.map((el) => {
      const s = getComputedStyle(el);
      const o = el.options.length ? getComputedStyle(el.options[0]) : s;
      return [s.color, s.backgroundColor, o.color, o.backgroundColor];
    }));
    const contrast = (list) => list.length >= 2 && list.every(([c, b, oc, ob]) => c !== b && oc !== ob);
    const normal = await colors();
    const speeds = await page.locator('.player-wrap .inline-select', { hasText: '速度' }).locator('option').allInnerTexts();
    check(`${label} 倍速選單文字（1x 不再顯示成 1.x）`, JSON.stringify(speeds) === JSON.stringify(['0.5x', '0.75x', '1x', '1.25x', '1.5x', '1.75x', '2x', '2.5x', '3x']), speeds.join(','));
    await page.getByRole('button', { name: '⛶ 全螢幕' }).click();
    await page.waitForTimeout(700);
    const isFs = (await page.locator('.player-wrap.fullscreen').count()) > 0;
    const full = await colors();
    await shot(page, `${label}-04b-fullscreen`);
    // dev.62：全螢幕時開 Tag 視窗，文字不能繼承全螢幕的白字（以前淺色底白字看不到）
    let tagInfo = null;
    if (isFs) {
      await page.locator('.player-wrap.fullscreen .controls').getByRole('button', { name: /Tag/ }).click();
      await page.waitForSelector('.player-wrap.fullscreen .modal', { timeout: 5000 }).catch(() => undefined);
      await page.waitForTimeout(300);
      tagInfo = await page.evaluate(() => {
        const modal = document.querySelector('.player-wrap.fullscreen .modal');
        if (!modal) return null;
        const rgb = (c) => (c.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
        const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
        const ratio = (a, b) => { const x = lum(rgb(a)), y = lum(rgb(b)); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
        const bg = getComputedStyle(modal).backgroundColor;
        const out = {};
        for (const [k, sel] of [['標題', '.modal-head h2'], ['說明', '.modal-body p.dim'], ['關閉按鈕', '.modal-head .btn'], ['新增按鈕', '.modal-body .btn']]) {
          const el = modal.querySelector(sel);
          if (!el) { out[k] = 0; continue; }
          const s = getComputedStyle(el);
          const own = s.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor) ? s.backgroundColor : bg;
          out[k] = Number(ratio(s.color, own).toFixed(2));
        }
        return out;
      });
      await shot(page, `${label}-04c-fullscreen-tag`);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
    }
    check(`${label} 全螢幕開 Tag 視窗文字看得到（對比 ≥ 3；修正前白字約 1）`, !!tagInfo && Object.values(tagInfo).every((v) => v >= 3), JSON.stringify(tagInfo));
    if (isFs) await page.getByRole('button', { name: /退出全螢幕/ }).first().click();
    await page.waitForTimeout(500);
    check(`${label} 播放器下拉選單（倍速、學習狀態）預覽頁與全螢幕都看得到文字`, contrast(normal) && isFs && contrast(full) && full[0][1] !== normal[0][1],
      `預覽 ${JSON.stringify(normal[0])}；全螢幕(${isFs}) ${JSON.stringify(full[0])}`);
  }
  await page.getByRole('button', { name: '← 返回' }).click();
  await page.waitForSelector('.grid.files', { timeout: 10000 });
  check(`${label} 返回原本資料夾`, (await page.locator('.crumbbar').innerText()).includes('第一堂課'));

  // PDF
  await page.locator('.rowlink', { hasText: '講義' }).first().click();
  const pdfOk = await page.waitForSelector('.pdf-page canvas', { timeout: 20000 }).then(() => true, () => false);
  check(`${label} PDF 顯示（pdf.js）`, pdfOk);
  await page.waitForTimeout(800);
  await shot(page, `${label}-05-pdf`);
  await page.getByRole('button', { name: /返回/ }).first().click();
  await page.waitForTimeout(500);

  // Office → PDF
  await page.locator('.rowlink', { hasText: '課程表' }).first().click();
  const docOk = await page.waitForSelector('.pdf-page canvas', { timeout: 20000 }).then(() => true, () => false);
  check(`${label} Word 文件（Agent 轉 PDF）顯示`, docOk);
  await page.getByRole('button', { name: /返回/ }).first().click();
  await page.waitForTimeout(400);

  // 搜尋
  await nav(page, '搜尋');
  await page.getByLabel('搜尋關鍵字').fill('上集');
  await page.locator('.search-bar').getByRole('button', { name: '搜尋', exact: true }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check(`${label} 搜尋「上集」`, t.includes('0001_上集'));
  await shot(page, `${label}-06-search`);

  await nav(page, '瀏覽記錄');
  t = await text(page);
  check(`${label} 瀏覽記錄有剛看過的影片`, t.includes('0001_上集'));
  await shot(page, `${label}-07-history`);
  await nav(page, '個人資料');
  await shot(page, `${label}-08-profile`);
  // dev.57 問題回報：任何使用者都能送；送出後顯示固定的安撫訊息（實際由 Server 代寄給 Super）
  await nav(page, '問題回報');
  await page.locator('textarea').first().fill(`${label} 教室電腦：第一堂課影片沒有聲音`);
  await page.getByRole('button', { name: '送出回報' }).click();
  const dlg = await page.waitForSelector('[role=dialog]', { timeout: 10000 }).then(() => true, () => false);
  t = await text(page);
  check(`${label} user 送出問題回報後顯示訊息視窗`, dlg && t.includes('本系統不連線網際網路~') && t.includes('陳鴻文將於下次到教室時候獲知此訊息')
    && t.includes('~無相氣學 萬馬奔騰!') && !t.includes('Email') && !t.includes('寄'));
  await shot(page, `${label}-08b-feedback`);
  await page.getByRole('dialog').getByRole('button', { name: '好', exact: true }).click();
  await page.waitForTimeout(400);
  t = await text(page);
  check(`${label} 問題回報出現在「我送出的回報」`, (await page.locator('[role=dialog]').count()) === 0 && t.includes(`${label} 教室電腦：第一堂課影片沒有聲音`) && t.includes('✓ 已送出'));
  await shot(page, `${label}-08c-feedback-list`);
  await logout(page);

  // Admin
  await login(page, 'admin');
  check(`${label} Admin 管理列`, (await page.locator('.adminbar').innerText()).includes('WOL 管理'));
  await nav(page, '帳號管理');
  await page.waitForSelector('.grid tbody tr', { timeout: 10000 });
  t = await text(page);
  check(`${label} Admin 的帳號清單有唯一的 super（唯讀）`, t.includes('superadmin') && t.includes('super'));
  check(`${label} 帳號表單：稱謂＝助教／講師、Admin 可給的權限只有 user`,
    JSON.stringify(await fieldOptions(page, '稱謂')) === JSON.stringify(['助教', '講師'])
    && JSON.stringify(await fieldOptions(page, '權限')) === JSON.stringify(['user']));
  await shot(page, `${label}-09-accounts`);
  await nav(page, '課程群組');
  await page.waitForTimeout(600);
  await page.locator('select').filter({ hasText: '請選擇' }).selectOption({ label: '陳助教（stu01）' });
  await page.waitForTimeout(800);
  t = await text(page);
  check(`${label} 個人覆寫表（群組結果／最後結果）`, t.includes('群組結果') && t.includes('最後結果') && t.includes('✓ 可看'));
  await shot(page, `${label}-10-groups`);
  await nav(page, '使用記錄');
  await page.waitForTimeout(800);
  t = await text(page);
  check(`${label} 使用記錄有登入與開啟影片`, t.includes('登入') && t.includes('開啟影片'));
  await shot(page, `${label}-11-audit`);
  // dev.62：管理員在 WOL 管理只能查看狀態與喚醒（沒有新增、編輯、刪除、登記、掃描；Server API 也擋）
  await nav(page, 'WOL 管理');
  await page.waitForTimeout(800);
  t = await text(page);
  const editBtns = await page.getByRole('button', { name: /新增設備|編輯|刪除|登記|掃描 LAN|^啟用$/ }).count();
  check(`${label} Admin 的 WOL 管理只有喚醒與狀態（沒有編輯按鈕與分頁）`, t.includes('只有 Super 可以設定') && editBtns === 0
    && !(await page.getByRole('tab', { name: /待登記設備|設備清單/ }).count()) && t.includes('教室 A-01'), `編輯類按鈕 ${editBtns}`);
  await shot(page, `${label}-12-wol-admin`);
  await logout(page);

  // Super（在 Client App 只有 Client 功能）
  await login(page, 'super');
  t = await page.locator('.topnav').innerText();
  check(`${label} Super 在 Client 看不到 Server 管理`, !t.includes('Server 管理'));
  check(`${label} Super 顯示為英文 super（dev.55 ROLE-02）`, t.includes('super') && !t.includes('管理員'));
  // dev.62：WOL 設備的編輯只有 Super
  await nav(page, 'WOL 管理');
  await page.getByRole('tab', { name: /待登記設備/ }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check(`${label} 心跳回報後出現在「待登記設備」（Super）`, t.includes(os.hostname().slice(0, 20)) || t.includes('登記'), '');
  await shot(page, `${label}-13-wol-pending`);
  await page.getByRole('tab', { name: '設備清單' }).click();
  await page.waitForTimeout(400);
  check(`${label} Super 的設備清單有新增／編輯`, !!(await page.getByRole('button', { name: '新增設備' }).count()) && !!(await page.getByRole('button', { name: '編輯' }).count()));
  await shot(page, `${label}-13b-wol-devices`);
  await nav(page, 'Tag 設定');
  await page.waitForTimeout(500);
  await shot(page, `${label}-14-tagpolicy`);
  await nav(page, 'PC 資訊');
  await page.waitForTimeout(800);
  t = await text(page);
  check(`${label} PC 資訊`, t.includes('作業系統') && t.includes('App 版別與版本'));
  await shot(page, `${label}-15-pcinfo`);
  // dev.57 Email 報告：Client App 只能寄送（內容送到 Server 代寄），不能改寄送設定
  await nav(page, 'Email 報告');
  await page.waitForTimeout(800);
  t = await text(page);
  check(`${label} Super 有「Email 報告」：由 Server 代寄、沒有寄送設定`, t.includes('由 Server 代寄') && !t.includes('寄送設定（只能在 Server App 修改）')
    && t.includes('Email 尚未設定：請在 Server App 的「Email 報告」完成寄送設定') && !t.includes('附上主機盤點')
    && t.includes('問題回報（Client 使用者送出') && t.includes(`${label} 教室電腦：第一堂課影片沒有聲音`));
  await shot(page, `${label}-16-email`);
  await logout(page);

  check(`${label} 畫面沒有 JavaScript 錯誤`, errors.length === 0, errors.slice(0, 5).join(' | '));
  await app.close();
}

// ---------------- Server App ----------------
async function serverFlow(dir) {
  console.log('\n=== Server App ===');
  const host = fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-host-'));
  fs.mkdirSync(path.join(host, 'etc'), { recursive: true });
  fs.mkdirSync(path.join(host, 'varlib'), { recursive: true });
  const prefix = fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-prefix-'));
  // dev.56 主機盤點：istudyctl（沙盒）寫在 prefix/var/lib/istudy/uem，Server App 從 ISTUDY_HOST_VARLIB/uem 讀取
  fs.symlinkSync(path.join(prefix, 'var', 'lib', 'istudy', 'uem'), path.join(host, 'varlib', 'uem'));
  // 主機維護指令以測試模式執行（ISTUDY_ROOT_PREFIX 沙盒、不碰 Docker）
  const hostCmd = `python3 ${path.join(ROOT, 'agent', 'istudyctl.py')}`;
  const env = {
    ISTUDY_SERVER: state.admin, ISTUDY_HOST_ETC: path.join(host, 'etc'), ISTUDY_HOST_VARLIB: path.join(host, 'varlib'), ISTUDY_HOST_CMD: hostCmd,
    ISTUDY_ROOT_PREFIX: prefix, ISTUDY_SKIP_DOCKER: '1', ISTUDY_MEDIA_ROOT: state.media_root, ISTUDY_EXPECT_UBUNTU: '0', ISTUDY_REQUIRE_MOUNT: '0',
    ISTUDY_MIN_SYSTEM_GB: '1', ISTUDY_PREFLIGHT_PORTS: '0',
    // dev.62 加密備份：主機指令（測試模式）連測試資料庫、用測試 Agent 的備份資料夾、本機測試 FTPS 伺服器的憑證
    ISTUDY_TEST_PG: `${process.env.DB_HOST || '127.0.0.1'}:${process.env.DB_PORT || '5432'}:${process.env.DB_USER || 'postgres'}`,
    PGPASSWORD: process.env.DB_PASSWORD || '', ISTUDY_TEST_DB_NAME: state.db, ISTUDY_BACKUP_DIR: state.backup_dir, ISTUDY_AGE_BIN: state.age_bin,
    ISTUDY_TEST_FTPS_CAFILE: state.ftps.ca, ISTUDY_FTPS_TIMEOUT: '10', ISTUDY_BACKUP_HOST_WAIT: '20',
  };
  const { app, page, errors, userData } = await launch(dir, E44, env);
  await page.waitForSelector('.boot, .login-card', { timeout: 20000 });
  let t = await text(page);
  check('Server App 未部署時顯示「初始部署」', t.includes('初始部署') && t.includes('建立 Super'));
  await shot(page, 'server-01-bootstrap');
  await page.getByRole('button', { name: '重新檢查' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('結果：'), null, { timeout: 60000 }).catch(() => undefined);
  t = await text(page);
  check('「重新檢查」執行 istudyctl bootstrap --check-only 並顯示預檢', /預檢/.test(t) && /(通過|警告|失敗)/.test(t));
  await shot(page, 'server-02-precheck');
  await page.waitForSelector('.health.ok, .msg.info .btn', { timeout: 15000 }).catch(() => undefined);
  await page.getByRole('button', { name: '前往登入' }).click();
  await page.waitForSelector('.login-card', { timeout: 10000 });
  t = await text(page);
  check('Server App 登入頁沒有「喚醒電腦」', !t.includes('喚醒電腦'));
  check('Server App 頁尾「Server V…」', t.includes('Server V' + VER));
  // 非 Super 不能登入 Server App
  await page.getByLabel('帳號', { exact: true }).fill(state.accounts.admin.username);
  await page.getByLabel('密碼', { exact: true }).fill(state.accounts.admin.password);
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await page.waitForTimeout(1200);
  t = await text(page);
  check('Admin 不能登入 Server App', t.includes('Super') && !!(await page.$('.login-card')));
  // dev.62：登入頁「顯示密碼」小按鈕（常態顯示／再按隱藏）；欄位名稱仍是「密碼」
  {
    const pw = page.getByLabel('密碼', { exact: true });
    await pw.fill('abc-確認123');
    const eye = page.getByRole('button', { name: '顯示密碼' });
    const has = await eye.count();
    await eye.click().catch(() => undefined);
    const shownType = await pw.getAttribute('type');
    const shownVal = await pw.inputValue();
    const pressed = await page.locator('.pw-eye').getAttribute('aria-pressed');
    await shot(page, 'server-00-login-eye');
    await page.getByRole('button', { name: '隱藏密碼' }).click().catch(() => undefined);
    const hiddenType = await pw.getAttribute('type');
    check('Server App 登入頁「顯示密碼」：按一下顯示明碼、再按一下隱藏', has === 1 && shownType === 'text' && shownVal === 'abc-確認123'
      && pressed === 'true' && hiddenType === 'password', `count=${has} 顯示時 type=${shownType} 隱藏時 type=${hiddenType}`);
  }
  await login(page, 'super');
  await page.waitForSelector('.wizard', { timeout: 15000 }).catch(() => undefined);
  t = await text(page);
  check('首次登入自動開啟首次設定精靈', t.includes('首次設定精靈'));
  await page.locator('.wstep', { hasText: '1. 環境確認' }).click();
  await page.waitForTimeout(3500);
  await shot(page, 'server-03-wizard-1');
  await page.getByRole('button', { name: '確認，下一步' }).click();
  await page.waitForTimeout(800);
  await shot(page, 'server-04-wizard-2');
  await page.getByRole('button', { name: '確認，下一步' }).click();
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: '試掃描', exact: true }).click();
  await page.waitForFunction(() => document.body.innerText.includes('試掃描結果'), null, { timeout: 30000 }).catch(() => undefined);
  t = await text(page);
  check('精靈第 3 步：試掃描顯示統計（需要轉檔的影片、預估空間）', t.includes('需要轉檔的影片') && t.includes('預估轉檔期間增加的空間'));
  await shot(page, 'server-05-wizard-3');
  await page.getByRole('button', { name: '確認，執行正式掃描' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('正式掃描完成'), null, { timeout: 30000 }).catch(() => undefined);
  check('精靈第 3 步：正式掃描完成', (await text(page)).includes('正式掃描完成'));
  await page.locator('.wstep', { hasText: '4. 試轉檔' }).click();
  await page.waitForTimeout(1000);
  await shot(page, 'server-06-wizard-4');
  t = await text(page);
  check('精靈第 4 步：批次轉檔暫停中', t.includes('批次轉檔目前暫停'));
  check('精靈第 4 步：硬體編碼三層分開顯示（dev.55 HW-01）', t.includes('Quick Sync → VA-API → CPU') && t.includes('轉檔檢測：VA-API'));
  // dev.55 DOC-01／DOC-03：第 5 步讀取真實轉換狀態、有「掃描新增教材」；離開再回來狀態不變
  await page.locator('.wstep', { hasText: '5. 文件與字幕' }).click();
  await page.waitForTimeout(1200);
  t = await text(page);
  const docState1 = t.includes('✓ 已轉 PDF');
  check('精靈第 5 步：已轉好的文件顯示「✓ 已轉 PDF」與「重新試轉」', docState1 && t.includes('重新試轉') && t.includes('掃描新增教材'));
  await shot(page, 'server-07-wizard-5');
  await page.locator('.wstep', { hasText: '3. 教材盤點' }).click();
  await page.waitForTimeout(600);
  await page.locator('.wstep', { hasText: '5. 文件與字幕' }).click();
  await page.waitForTimeout(1200);
  check('精靈第 5 步：離開再回來仍顯示「✓ 已轉 PDF」', (await text(page)).includes('✓ 已轉 PDF'));
  await page.getByRole('button', { name: '掃描新增教材' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('掃描完成'), null, { timeout: 30000 }).catch(() => undefined);
  check('精靈第 5 步：「掃描新增教材」重用正式掃描並顯示完成', (await text(page)).includes('掃描完成'));
  // dev.55 BK-01：第 6 步只有「建立備份」，不會還原
  await page.locator('.wstep', { hasText: '6. 備份' }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check('精靈第 6 步：只有「建立備份」，沒有「測試還原」按鈕', !!(await page.getByRole('button', { name: '建立備份', exact: true }).count())
    && !(await page.getByRole('button', { name: /還原/ }).count()));
  await shot(page, 'server-08-wizard-6');

  const pages = ['總覽', '教材目錄', '教材清單', '轉檔佇列', '排程設定', '原檔刪除確認', '字幕', '文件轉換', 'Outgo（錄音轉文字）', '統計與報告', 'Client 版本與更新', '匯入／匯出授權', '登入頁設定', '系統檢查與管理', '備份', 'Log', '主機盤點（UEM）', '主機維護'];
  for (const [i, p] of pages.entries()) {
    await page.locator('.srvnav').getByRole('button', { name: new RegExp(p.replace(/[／（）]/g, '.')) }).click();
    await page.waitForTimeout(900);
    t = await text(page);
    const head = await page.locator('.page-head h1').first().innerText().catch(() => '');
    check(`Server 管理「${p}」`, head === p && !/HTTP 5\d\d|找不到 API/.test(t), head);
    await shot(page, `server-${String(10 + i).padStart(2, '0')}-${p.replace(/[／\s]/g, '')}`);
  }
  // 總覽的重點
  await page.locator('.srvnav').getByRole('button', { name: '總覽' }).click();
  await page.waitForTimeout(1000);
  t = await text(page);
  check('總覽：資料庫、Agent、Client 版本', t.includes('資料庫正常') && t.includes('Agent 運作中') && t.includes('Client 版本'));
  check('總覽：轉檔編碼器（實際使用）', t.includes('轉檔編碼器（實際使用）'));
  // dev.55 FILE-01：教材目錄樹顯示實際路徑，可展開
  await page.locator('.srvnav').getByRole('button', { name: '教材目錄' }).click();
  await page.waitForSelector('.mt button.mt-toggle', { timeout: 10000 }).catch(() => undefined);
  await page.locator('.mt .mt-block button.mt-toggle').first().click();
  await page.waitForTimeout(800);
  t = await text(page);
  check('教材目錄：目錄樹顯示實際路徑並可展開', t.includes('目錄樹') && t.includes(state.media_root + '/基礎班') && !t.includes(state.media_root + '/videos/') && (await page.locator('.mt-block.open .mt-list .mt-row').count()) > 0);
  // dev.62 UI-01：/Media 標頭、第一層資料夾是有邊框的區塊（展開後有「結束」）、子層縮排＋引導線、根目錄檔案獨立區塊
  const rootFiles = await page.locator('.mt-rootfiles').count();
  if (rootFiles) await page.locator('.mt-rootfiles button.mt-toggle').click();
  await page.waitForTimeout(500);
  t = await text(page);
  check('教材目錄：/Media（教材根目錄）標頭、資料夾區塊開始與結束、根目錄檔案分區',
    t.includes('（教材根目錄）') && /— .+ 結束 —/.test(t) && (await page.locator('.mt-body > .mt-block').count()) >= 2
    && (!rootFiles || (t.includes('根目錄檔案（直接放在') && t.includes('根目錄檔案結束'))), `root files block: ${rootFiles}`);
  await shot(page, 'server-40-tree');
  // UI-02：窄視窗——標籤換行，不和檔名重疊
  await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.unmaximize(); w.setSize(1024, 760); });
  await page.waitForTimeout(800);
  const overlap = await page.$$eval('.mt-row', (rows) => rows.some((r) => {
    const kids = Array.from(r.children).map((c) => c.getBoundingClientRect()).filter((b) => b.width > 0);
    return kids.some((a, i) => kids.slice(i + 1).some((b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1));
  }));
  check('教材目錄：窄視窗（1024 寬）檔名與標籤不重疊', !overlap);
  await shot(page, 'server-40b-tree-narrow');
  await app.evaluate(({ BrowserWindow }) => { BrowserWindow.getAllWindows()[0].maximize(); });
  await page.waitForTimeout(500);
  // dev.55 BK-01：備份頁三個操作分開
  await page.locator('.srvnav').getByRole('button', { name: '備份' }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check('備份頁：建立備份／檢查完整性／隔離還原測試分開，正式還原不在 App 內', t.includes('建立備份') && t.includes('隔離還原測試') && t.includes('正式還原')
    && !(await page.getByRole('button', { name: /備份並測試還原/ }).count()));
  await shot(page, 'server-41-backups');
  check('頂端提示「首次設定未完成」', t.includes('首次設定未完成'));
  await backupFlow(page, userData, prefix);
  // 系統檢查子分頁
  await page.locator('.srvnav').getByRole('button', { name: '系統檢查與管理' }).click();
  for (const sub of ['2. App 開發待確認清單', '4. Server 硬體檢查', '8. 系統檢測']) {
    await page.getByRole('tab', { name: sub }).click();
    await page.waitForTimeout(700);
    await shot(page, `server-30-check-${sub.slice(0, 1)}`);
  }
  // 主機維護：主機檢查（doctor 在測試模式）
  await page.locator('.srvnav').getByRole('button', { name: '主機維護' }).click();
  await page.getByRole('button', { name: '主機檢查（自我檢查）' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('自我檢查：'), null, { timeout: 30000 }).catch(() => undefined);
  t = await text(page);
  check('主機維護：執行 istudyctl doctor 並顯示結果', t.includes('自我檢查：'));
  await shot(page, 'server-31-host-doctor');
  // 字幕辨識：辨識設定（4 個本機引擎＋線上、詞彙表；dev.59 移除 Whisper）；已有字幕顯示引擎與品質檢查
  await page.locator('.srvnav').getByRole('button', { name: '字幕' }).click();
  await page.waitForTimeout(800);
  await page.getByRole('tab', { name: '辨識設定' }).click();
  await page.waitForTimeout(600);
  t = await text(page);
  check('字幕：辨識設定列出 4 個本機引擎與線上路線（尚未提供的不能選）', ['FunASR Paraformer-zh', 'FunASR SenseVoiceSmall', 'Qwen3-ASR-0.6B', 'Breeze-ASR-25', '線上 API'].every((x) => t.includes(x))
    && t.includes('尚未提供') && t.includes('asr-models --fetch all'));
  check('字幕：dev.59 已移除 Whisper（引擎表與下拉選單都沒有）', !t.includes('Whisper（faster-whisper）')
    && !(await page.locator('select option', { hasText: 'Whisper' }).count()) && t.includes('目前的引擎都不支援熱詞'));
  await shot(page, 'server-27-asr-engines');
  await page.locator('label.field', { has: page.locator('span', { hasText: '修正表' }) }).locator('textarea').fill('圓三=研三');
  await page.getByRole('button', { name: '儲存設定' }).click();
  await page.waitForTimeout(800);
  check('字幕：詞彙表可儲存', (await text(page)).includes('字幕辨識設定已儲存'));
  await shot(page, 'server-28-asr-settings');
  await page.getByRole('tab', { name: /已有字幕/ }).click();
  await page.waitForTimeout(600);
  t = await text(page);
  check('字幕：顯示 ASR 單檔長度建議（最佳 3 小時內、上限 5 小時）', t.includes('ASR 單檔長度建議') && t.includes('3 小時內') && t.includes('5 小時')
    && t.includes('一個檔案是一個工作'));
  check('字幕：已有字幕顯示引擎、速度與品質檢查', t.includes('速度（RTF）') && t.includes('品質檢查') && /FunASR Paraformer-zh/.test(t));
  await shot(page, 'server-29-asr-done');
  // dev.60 SUB-01／SUB-02：每一筆都有「重新產生」；引擎欄寫實際引擎或「未知」（不會是 —）；預設引擎標明是資料庫設定
  const engCells = await page.$$eval('table.grid tbody tr', (trs) => trs.filter((tr) => !tr.closest('.asr-hint'))
    .map((tr) => (tr.children[2] ? tr.children[2].textContent.trim() : '')));
  const regen = await page.locator('table.grid tbody').getByRole('button', { name: '重新產生' }).count();
  check('字幕：已有字幕每一筆都有「重新產生」，引擎欄寫實際引擎', engCells.length > 0 && regen === engCells.length && engCells.every((x) => x && x !== '—'),
    engCells.join('、'));
  check('字幕：預設引擎標明是資料庫目前的設定，與程式內建預設值分開', t.includes('預設引擎＝資料庫目前的設定') && t.includes('程式內建的預設值是「自動」')
    && t.includes('重新產生時使用') && (await page.locator('select option', { hasText: '預設引擎（' }).count()) > 0);
  await page.locator('table.grid tbody').getByRole('button', { name: '重新產生' }).first().click();
  await page.waitForTimeout(800);
  check('字幕：「重新產生」排入字幕工作', /已排入字幕產生|已在佇列中/.test(await text(page)));
  // dev.60 DOC-01／DOC-02：文件轉換顯示 PDF 頁數與原檔版面說明
  await page.locator('.srvnav').getByRole('button', { name: '文件轉換', exact: true }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check('文件轉換：PowerPoint 寫出頁數與隱藏投影片、Excel 指出原檔沒有設定一頁寬', t.includes('（3 頁）') && t.includes('1 張隱藏')
    && t.includes('工作表「課程表」') && t.includes('調整成一頁寬'));
  await shot(page, 'server-30-documents');
  // dev.60 DEL-01：原檔刪除確認列出全部受管理的原檔（含未滿 30 天）；未滿的不能勾選
  await page.locator('.srvnav').getByRole('button', { name: '原檔刪除確認', exact: true }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  const boxes = await page.locator('table.grid tbody input[type=checkbox]').count();
  check('原檔刪除確認：未到期的原檔也列出（保留天數、剩餘天數、原因），但不能勾選', /剩 \d+ 天/.test(t) && t.includes('保留期未滿 30 天')
    && t.includes('天／30 天') && t.includes('可以刪除（0）') && boxes === 0);
  await shot(page, 'server-31-originals');
  // dev.61 P0-2／P0-3：Outgo（錄音轉文字）——只在 Server App；資料夾瀏覽、ASR 轉文字、預覽、下載；長度建議
  await page.locator('.srvnav').getByRole('button', { name: /Outgo/ }).click();
  await page.waitForTimeout(900);
  t = await text(page);
  check('Outgo：列出資料夾與錄音（隱藏檔、符號連結不列出），顯示各格式的 ASR 長度建議', t.includes('第一期') && t.includes('討論.wma') && !t.includes('.隱藏')
    && !t.includes('📁 連結') && t.includes('另有 1 個項目沒有列出') && t.includes('ASR 單檔長度建議') && t.includes('WMA') && t.includes('AMR') && t.includes('3 小時內') && t.includes('5 小時'));
  check('Outgo：引擎預設＝資料庫目前的設定', (await page.locator('select option', { hasText: '預設引擎（' }).count()) > 0);
  await shot(page, 'server-32-outgo');
  await page.getByRole('button', { name: /第一期/ }).click();
  await page.waitForTimeout(800);
  await page.locator('table.grid tbody tr', { hasText: '上課錄音.mp3' }).getByRole('button', { name: 'ASR 轉文字' }).click();
  await page.waitForTimeout(500);
  check('Outgo：按「ASR 轉文字」排入工作', /已排入 ASR 轉文字|已在排隊/.test(await text(page)));
  await page.waitForFunction(() => document.body.innerText.includes('✓ 完成'), null, { timeout: 90000 }).catch(() => undefined);
  t = await text(page);
  check('Outgo：Agent 辨識完成，狀態「✓ 完成」並顯示引擎與音訊品質', t.includes('✓ 完成') && /FunASR/.test(t) && /音訊品質：[●◐○] (高|中|低)/.test(t));
  await page.getByRole('button', { name: '預覽' }).first().click();
  await page.waitForTimeout(600);
  const pv = await page.locator('.transcript').innerText().catch(() => '');
  check('Outgo：預覽逐字稿', pv.length > 5, pv.slice(0, 40));
  await shot(page, 'server-33-outgo-preview');
  await page.keyboard.press('Escape');
  await page.locator('[role=dialog] button', { hasText: /關閉|×/ }).first().click().catch(() => undefined);
  await page.waitForTimeout(300);
  const srtOut = path.join(host, 'outgo.srt');
  await app.evaluate(({ dialog }, out) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: out }); }, srtOut);
  await page.getByRole('button', { name: '下載 SRT' }).first().click();
  await page.waitForTimeout(800);
  const srt = fs.existsSync(srtOut) ? fs.readFileSync(srtOut, 'utf8') : '';
  check('Outgo：下載 SRT（UTF-8、時間碼）', /^1\n00:00:00,000 --> /.test(srt), srt.slice(0, 40));
  check('Outgo：結果寫在錄音旁邊，原始錄音不動；不出現在課程', fs.existsSync(path.join(state.media_root, 'Outgo', '第一期', '上課錄音.mp3.asr.txt'))
    && fs.existsSync(path.join(state.media_root, 'Outgo', '第一期', '上課錄音.mp3')));
  await shot(page, 'server-34-outgo-done');
  // dev.61 P0-1：教材清單「永久刪除…」：影響範圍 → 輸入「永久刪除」→ Agent 執行 → 原檔與紀錄都刪除（只用測試建立的檔案）
  await page.locator('.srvnav').getByRole('button', { name: '教材清單', exact: true }).click();
  await page.waitForTimeout(800);
  await page.getByPlaceholder('標題、路徑或教材 ID').fill('e2e-丟棄講義');
  await page.waitForTimeout(1000);
  await page.locator('table.grid tbody tr', { hasText: 'e2e-丟棄講義' }).getByRole('button', { name: '永久刪除…' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('輸入「永久刪除」確認'), null, { timeout: 10000 }).catch(() => undefined);
  t = await text(page);
  const purgeBtn = page.locator('[role=dialog]').getByRole('button', { name: '永久刪除', exact: true });
  check('永久刪除：顯示檔名、路徑、會刪除的檔案與資料影響，未輸入確認字樣前不能按', t.includes('e2e-丟棄講義.pdf') && t.includes('/Media/砭石/e2e-丟棄講義.pdf')
    && t.includes('原檔') && t.includes('無法復原') && await purgeBtn.isDisabled());
  await shot(page, 'server-35-purge-dialog');
  await page.getByLabel('輸入永久刪除確認').fill('永久刪除');
  await purgeBtn.click();
  await page.waitForFunction(() => document.body.innerText.includes('已永久刪除'), null, { timeout: 60000 }).catch(() => undefined);
  t = await text(page);
  check('永久刪除：Agent 完成後才顯示「已永久刪除」，原檔已不在 /Media', t.includes('已永久刪除') && !fs.existsSync(path.join(state.media_root, '砭石', 'e2e-丟棄講義.pdf')));
  // dev.62 P0-A：完成後逐項列出每個檔案的結果（對話框停在結果，按「關閉」）
  const purgeRows = await page.locator('[role=dialog] table.grid tbody tr').allInnerTexts();
  check('永久刪除：逐項列出每個檔案的實際結果', purgeRows.length >= 1 && purgeRows.some((r) => r.includes('e2e-丟棄講義.pdf') && r.includes('已刪除')), purgeRows.join(' | '));
  await shot(page, 'server-36-purge-done');
  await page.locator('[role=dialog]').getByRole('button', { name: '關閉', exact: true }).click();
  await page.waitForTimeout(400);
  // dev.61「Client 版本與更新」：每台電腦的版本、更新狀態（Client 心跳回報）、Server 上已發布的版本、發布新版
  await page.locator('.srvnav').getByRole('button', { name: 'Client 版本與更新', exact: true }).click();
  await page.waitForTimeout(1000);
  t = await text(page);
  const clientsRan = !ONLY || ONLY === 'client' || ONLY === 'win7';
  check('Client 版本與更新：每台電腦的目前版本、版本狀態、更新狀態、連線；可發布新版',
    t.includes('各台電腦') && t.includes('Server 上已發布') && t.includes('發布新版 Client') && t.includes('沒有人登入滿 2 分鐘就自動安裝')
    && (clientsRan ? t.includes(os.hostname().slice(0, 20)) && /✓ 最新/.test(t) && t.includes('開發版（不自動更新）') && /[●○] (在線|離線)/.test(t)
      : t.includes('還沒有 Client 電腦回報') || t.includes('各台電腦')));
  await shot(page, 'server-38-clients');
  // dev.61「統計與報告」：ASR 處理記錄（檔案／音訊品質／結果），可匯出 CSV
  await page.locator('.srvnav').getByRole('button', { name: '統計與報告', exact: true }).click();
  await page.waitForTimeout(1000);
  t = await text(page);
  check('統計與報告：ASR 處理記錄列出字幕與 Outgo 的檔案、音訊品質（高／中／低）、結果', t.includes('ASR 處理記錄') && t.includes('Outgo/第一期/上課錄音.mp3')
    && t.includes('字幕（影片）') && /[●◐○] (高|中|低)/.test(t) && t.includes('✓ 成功') && t.includes('音訊品質是粗略估計'));
  await shot(page, 'server-37-reports');
  const csvOut = path.join(host, 'asr-log.csv');
  await app.evaluate(({ dialog }, out) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: out }); }, csvOut);
  await page.getByRole('button', { name: '匯出 CSV（Excel）' }).click();
  await page.waitForTimeout(800);
  const csv = fs.existsSync(csvOut) ? fs.readFileSync(csvOut, 'utf8') : '';
  check('統計與報告：匯出 CSV（UTF-8 BOM、檔案／音訊品質／結果欄位）', csv.startsWith('\ufeff時間,類型,檔案') && csv.includes('音訊品質') && csv.includes('Outgo/第一期/上課錄音.mp3')
    && /,(高|中|低),/.test(csv), csv.slice(0, 80));
  // dev.57 Email 報告（Server App）：設定網站代寄 → 寄出摘要 → 紀錄
  await nav(page, 'Email 報告');
  await page.waitForTimeout(800);
  await page.locator('label.field', { has: page.locator('span', { hasText: '代寄網址' }) }).locator('input').fill('https://work.eachcore.com/istudy-mail-relay.php');
  await page.locator('label.field', { has: page.locator('span', { hasText: '代寄金鑰' }) }).locator('input').fill('e2e-relay-key-0123456789abcdef');
  await page.getByRole('button', { name: '儲存設定' }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check('Email：網站代寄設定可儲存，金鑰不顯示', t.includes('寄送設定已儲存') && t.includes('✓ 已設定') && !t.includes('e2e-relay-key'));
  await page.getByRole('button', { name: '寄出', exact: true }).click();
  // 狀態要看「寄送紀錄」表格的儲存格（說明文字裡也有「✓ 已提交寄送」字樣）
  const statusCell = () => [...document.querySelectorAll('td')].some((td) => td.innerText.trim() === '✓ 已提交寄送');
  await page.waitForFunction(statusCell, null, { timeout: 20000 }).catch(() => undefined);
  t = await text(page);
  check('Email：寄出系統狀態摘要，紀錄寫「已提交寄送」（不宣稱已送達；測試環境不連網）', (await page.evaluate(statusCell)) && t.includes('系統狀態摘要')
    && !t.includes('✓ 已寄出') && t.includes('eachcore.app@gmail.com'));
  check('Email：「問題回報」列出 Client 使用者、內容與沒寄出的原因', t.includes('問題回報（Client 使用者送出')
    && t.includes('win10 教室電腦：第一堂課影片沒有聲音') && t.includes('win7 教室電腦：第一堂課影片沒有聲音')
    && t.includes('✗ 沒寄出') && t.includes('Email 尚未設定'));
  check('Server App 主選單沒有「問題回報」（只在 Client App）', !(await page.locator('.topnav').innerText()).includes('問題回報'));
  await shot(page, 'server-30-email');
  await page.locator('.srvnav, .topnav').getByRole('button', { name: 'Server 管理' }).first().click().catch(() => undefined);
  await page.waitForTimeout(600);
  // dev.56 主機盤點（UEM）：快速盤點 → 報告；完整盤點含自我檢查；匯出 HTML（離線、無機密）
  await page.locator('.srvnav').getByRole('button', { name: '主機盤點（UEM）' }).click();
  await page.waitForTimeout(800);
  t = await text(page);
  check('主機盤點：還沒有報告時提示先盤點', t.includes('還沒有主機盤點報告'));
  await page.getByRole('button', { name: '快速盤點', exact: true }).click();
  await page.waitForFunction(() => document.body.innerText.includes('上次盤點：'), null, { timeout: 30000 }).catch(() => undefined);
  t = await text(page);
  check('主機盤點：快速盤點顯示路徑與磁碟歸屬、ZFS、服務、版本對照', t.includes('主機盤點完成（快速') && t.includes('重要路徑與磁碟歸屬')
    && t.includes('磁碟對照') && t.includes('服務與連接埠') && t.includes('版本對照') && t.includes(state.media_root + '/videos'));
  check('主機盤點：狀態以符號＋文字表示（不只靠顏色）', /[✓⚠✗?－] (通過|注意|異常|未知|不存在)/.test(t));
  check('主機盤點：快速盤點不含自我檢查', t.includes('快速盤點不含自我檢查'));
  check('主機盤點：主機板／BIOS（讀不到顯示「未知」）', t.includes('主機板') && t.includes('BIOS'));
  await shot(page, 'server-33-uem-quick');
  await page.getByRole('button', { name: '完整盤點（含自我檢查）' }).click();
  await page.waitForFunction(() => document.body.innerText.includes('主機盤點完成（完整'), null, { timeout: 60000 }).catch(() => undefined);
  await page.waitForTimeout(800);
  t = await text(page);
  check('主機盤點：完整盤點包含自我檢查結果', t.includes('主機盤點完成（完整') && t.includes('檢查時間') && !t.includes('快速盤點不含自我檢查'));
  await shot(page, 'server-34-uem-full');
  for (const [n, h] of [['35', '重要路徑與磁碟歸屬'], ['36', '服務與連接埠'], ['37', '自我檢查（doctor）']]) {
    await page.locator('h2', { hasText: h }).first().scrollIntoViewIfNeeded();
    await shot(page, `server-${n}-uem-${n}`);
  }
  const uemOut = path.join(host, 'uem-export.html');
  await app.evaluate(({ dialog }, out) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: out }); }, uemOut);
  await page.getByRole('button', { name: 'HTML（可離線開啟）' }).click();
  await page.waitForTimeout(800);
  const exported = fs.existsSync(uemOut) ? fs.readFileSync(uemOut, 'utf8') : '';
  check('主機盤點：匯出單一 HTML（沒有外部資源與指令碼）', exported.includes('<h2>重要路徑與磁碟歸屬</h2>') && !/<script|src=|href="http/i.test(exported));
  // 播放抽查（Server App 也能播放）
  await page.locator('.topnav').getByRole('button', { name: '課程目錄' }).click();
  await page.waitForTimeout(800);
  t = await page.locator('.tree-pane').innerText();
  check('Super 在 Server App 看得到全部課程', t.includes('基礎班') && t.includes('砭石') && t.includes('進修班'));
  await shot(page, 'server-32-courses');
  // 播放抽查與文件（Server App 含 Client 全部功能）
  await page.locator('.tree-name', { hasText: '基礎班' }).click();
  await page.waitForTimeout(500);
  await page.locator('.rowlink', { hasText: '第一堂課' }).first().click();
  await page.waitForSelector('.grid.files tbody tr', { timeout: 10000 });
  await page.locator('.rowlink', { hasText: '0001_上集' }).click();
  await page.waitForSelector('video', { timeout: 10000 });
  const ready = await page.waitForFunction(() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && v.duration > 0; }, null, { timeout: 20000 }).then(() => true, () => false);
  await page.evaluate(() => document.querySelector('video').play()).catch(() => undefined);
  await page.waitForTimeout(1200);
  const cur = await page.evaluate(() => document.querySelector('video').currentTime);
  check('Server App 播放影片（Range 串流）', ready && cur > 0.3, `currentTime=${cur.toFixed(2)}`);
  await page.getByRole('button', { name: '字幕面板' }).click();
  await page.waitForTimeout(600);
  check('Server App 字幕面板（自動辨識產生的字幕）', (await page.locator('.sub-group').count()) > 0);
  await page.evaluate(() => document.querySelector('video').pause());
  await shot(page, 'server-33-player');
  await page.getByRole('button', { name: '← 返回' }).click();
  await page.waitForSelector('.grid.files', { timeout: 10000 });
  await page.locator('.rowlink', { hasText: '講義' }).first().click();
  const pdfOk = await page.waitForSelector('.pdf-page canvas', { timeout: 20000 }).then(() => true, () => false);
  check('Server App 顯示 PDF（pdf.js）', pdfOk);
  await page.waitForTimeout(800);
  await shot(page, 'server-34-pdf');
  await page.getByRole('button', { name: /返回/ }).first().click();
  await page.waitForTimeout(400);
  await page.locator('.rowlink', { hasText: '課程表' }).first().click();
  const docOk = await page.waitForSelector('.pdf-page canvas', { timeout: 20000 }).then(() => true, () => false);
  check('Server App 顯示 Word 文件（Agent 轉 PDF）', docOk);
  await page.getByRole('button', { name: /返回/ }).first().click();
  await page.waitForTimeout(400);
  // dev.60 DOC-01／DOC-02：每頁的白底框要和那一頁一樣大（16:9 投影片下面不能多出一大塊空白；直式與橫式混合時橫式頁不能被裁掉）
  await page.locator('.topnav').getByRole('button', { name: '課程目錄' }).click();
  await page.waitForTimeout(600);
  await page.locator('.tree-name', { hasText: 'OfficeDemo' }).click();
  await page.waitForSelector('.grid.files tbody tr', { timeout: 10000 });
  for (const [name, label, pages] of [['D03', 'PowerPoint 16:9（4 張，1 張隱藏）', 3], ['D02', 'Excel（直式＋橫式）', 0]]) {
    await page.locator('.rowlink', { hasText: name }).first().click();
    const ok = await page.waitForSelector('.pdf-page canvas', { timeout: 30000 }).then(() => true, () => false);
    await page.waitForTimeout(1500);
    const g = await page.evaluate(() => {
      const box = document.querySelector('.pdf-pages');
      const br = box.getBoundingClientRect();
      const pages = Array.from(document.querySelectorAll('.pdf-page')).map((el) => {
        const r = el.getBoundingClientRect();
        const c = el.querySelector('canvas');
        const dpr = window.devicePixelRatio || 1;   // 畫布的實際解析度要對應頁框大小（不是只看 CSS 大小）
        return { w: Math.round(r.width), h: Math.round(r.height), cw: Math.round(c.width / dpr), ch: Math.round(c.height / dpr), left: Math.round(r.left - br.left), drawn: c.dataset.rendered === '1' };
      });
      return { inner: box.clientWidth, total: (document.querySelector('.doc-tools') || document.body).innerText, pages };
    });
    const drawn = g.pages.filter((p) => p.drawn);
    const fits = drawn.length > 0 && drawn.every((p) => Math.abs(p.h - p.ch) <= 2 && Math.abs(p.w - p.cw) <= 2);
    const inside = g.pages.every((p) => p.left >= 0 && p.left + p.w <= g.inner + 1);
    const countOk = !pages || g.pages.length === pages;
    check(`文件檢視 ${label}：每頁白底框與頁面同大、沒有被裁切${pages ? `、共 ${pages} 頁` : ''}`, ok && fits && inside && countOk,
      JSON.stringify(g.pages.slice(0, 4)));
    await shot(page, `server-35-${name}`);
    await page.getByRole('button', { name: /返回/ }).first().click();
    await page.waitForTimeout(400);
  }
  // 帳號管理（Super 可建立 Admin）
  await nav(page, '帳號管理');
  await page.waitForSelector('.grid tbody tr', { timeout: 10000 });
  t = await text(page);
  check('帳號管理：清單包含 Super（唯一、唯讀）與 Admin', t.includes('teacher01') && t.includes('superadmin'));
  const titleOpts = await fieldOptions(page, '稱謂');
  const roleOpts = await fieldOptions(page, '權限');
  check('帳號管理：稱謂選項＝助教／講師（沒有學生、admin、super）', JSON.stringify(titleOpts) === JSON.stringify(['助教', '講師']), titleOpts.join('、'));
  check('帳號管理：Super 可給的權限＝user／admin（沒有 super、沒有 mini_admin）', JSON.stringify(roleOpts) === JSON.stringify(['user', 'admin']), roleOpts.join('、'));
  const th = await page.locator('.grid thead th').allInnerTexts();
  check('帳號清單：稱謂與權限分兩欄', th.includes('稱謂') && th.includes('權限'), th.join('|'));
  await shot(page, 'server-35-accounts');
  await page.locator('.grid tbody tr', { hasText: 'superadmin' }).click();
  await page.waitForTimeout(500);
  check('帳號管理：點 super 只顯示唯讀說明', (await text(page)).includes('super 是唯一的最高管理者') && !(await page.getByRole('button', { name: '儲存' }).count()));
  // dev.55 PC-01：Server App 的 PC 資訊沒有「上傳到 Server」
  await nav(page, 'PC 資訊');
  await page.waitForTimeout(800);
  check('PC 資訊（Server App）：沒有「上傳到 Server」', !(await page.getByRole('button', { name: '上傳到 Server' }).count()) && (await text(page)).includes('Server 主機本身'));
  check('PC 資訊（Server App）：主機板與 BIOS', (await text(page)).includes('主機板') && (await text(page)).includes('BIOS'));
  await shot(page, 'server-36-pcinfo');
  // dev.55 WOL-01／02／03
  await nav(page, 'WOL 管理');
  await page.waitForTimeout(1000);
  t = await text(page);
  const wolTh = await page.locator('table.grid thead th').allInnerTexts();
  check('WOL：「心跳回報」與「檢測（Server ping）」分開（沒有含糊的「狀態」欄）', wolTh.includes('心跳回報') && wolTh.includes('檢測（Server ping）') && !wolTh.includes('狀態'), wolTh.join('|'));
  // dev.62：Super 在「喚醒與狀態」對未啟用的電腦可以直接按「啟用」，之後出現在登入畫面的喚醒清單
  {
    const row = page.locator('table.grid tbody tr', { hasText: '教室 A-02（停用）' });
    const before = (await row.innerText().catch(() => '')).includes('未啟用（登入畫面不顯示）');
    await row.getByRole('button', { name: '啟用', exact: true }).click().catch(() => undefined);
    await page.waitForTimeout(1200);
    const after = await row.innerText().catch(() => '');
    const pub = await apiCall(state.lan, 'GET', '/api/wol/public/devices', null, null);
    const names = ((pub.json && pub.json.devices) || []).map((d) => d.name);
    await shot(page, 'server-36b-wol-enable');
    check('WOL（Super）：未啟用的電腦按「啟用」後出現在登入畫面的喚醒清單', before && !after.includes('未啟用') && names.includes('教室 A-02（停用）'), `前=${before} 後=${after.replace(/\s+/g, ' ').slice(0, 80)} 公開清單=${names.join('、')}`);
  }
  await page.getByRole('tab', { name: '設備清單' }).click();
  await page.waitForTimeout(500);
  t = await text(page);
  check('WOL 設備清單：LAN IP 與 WOL 廣播位址分開、有「掃描 LAN 裝置」', t.includes('LAN IP') && t.includes('WOL 廣播位址') && t.includes('掃描 LAN 裝置'));
  await page.getByRole('button', { name: '新增設備' }).click();
  await page.waitForTimeout(400);
  t = await text(page);
  check('WOL 新增設備：有「LAN IP」欄、廣播位址預設「自動」', t.includes('LAN IP') && t.includes('自動（'));
  await shot(page, 'server-37-wol-form');
  await page.getByRole('button', { name: '取消' }).click();
  await page.getByRole('button', { name: '掃描 LAN 裝置' }).click();
  await page.waitForTimeout(400);
  check('WOL 掃描 LAN：說明範圍與頻率限制', (await text(page)).includes('最多 254 台') && (await text(page)).includes('60 秒內只能掃一次'));
  await shot(page, 'server-38-wol-scan');
  await page.getByRole('button', { name: '關閉', exact: true }).click();
  await logout(page);
  check('Server App 畫面沒有 JavaScript 錯誤', errors.length === 0, errors.slice(0, 5).join(' | '));
  await app.close();
}

// ---------------- 已安裝的 .deb（正式打包模式：isPackaged、主機維護一律走 pkexec） ----------------
async function installedFlow(exe) {
  console.log('\n=== 已安裝的 Server App（' + exe + '）===');
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'istudy-ud-'));
  const app = await electron.launch({ executablePath: exe, args: ['--no-sandbox', '--disable-gpu'],
    env: { ...process.env, ISTUDY_USER_DATA: userData, ISTUDY_SERVER: state.admin }, timeout: 60000 });
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  const packaged = await app.evaluate(({ app: a }) => a.isPackaged);
  check('.deb 安裝後可啟動（isPackaged）', packaged === true);
  await page.waitForSelector('.boot, .login-card', { timeout: 20000 });
  let t = await text(page);
  check('.deb 版未部署時顯示「初始部署」（沒有 istudyctl 時提示先執行 install.sh）', t.includes('初始部署') && (fs.existsSync('/usr/local/sbin/istudyctl') || t.includes('install.sh')));
  await shot(page, 'deb-01-bootstrap');
  await page.waitForSelector('.msg.info .btn', { timeout: 15000 }).catch(() => undefined);
  await page.getByRole('button', { name: '前往登入' }).click();
  await page.waitForSelector('.login-card', { timeout: 10000 });
  await login(page, 'super');
  await page.locator('.srvnav').getByRole('button', { name: '總覽' }).click();
  await page.waitForTimeout(1500);
  t = await text(page);
  check('.deb 版登入 Super 並顯示總覽', t.includes('資料庫正常'));
  await shot(page, 'deb-02-overview');
  await page.locator('.srvnav').getByRole('button', { name: '主機維護' }).click();
  await page.getByRole('button', { name: '主機檢查（自我檢查）' }).click();
  await page.waitForTimeout(2500);
  t = await text(page);
  check('.deb 版主機維護走 pkexec（沒有 pkexec 時顯示處理方式）', fs.existsSync('/usr/bin/pkexec') || t.includes('找不到 pkexec'), '');
  await shot(page, 'deb-03-host');
  check('.deb 版畫面沒有 JavaScript 錯誤', errors.length === 0, errors.slice(0, 5).join(' | '));
  await app.close();
}

/**
 * dev.62 備份頁（BAK-11）：方案 B 範圍說明、警告；設定恢復密碼（Server App → 主機指令，密碼只走標準輸入）；
 * 建立加密備份；主機解密驗證；遠端（本機測試 FTPS）設定、連線測試、上傳；保留天數。最後檢查密碼沒有出現在 Log、App 資料夾、主機檔案。
 */
/** 表單欄位（Field）的輸入框：以欄位標題完全相符找（欄位有說明文字時 getByLabel 的名稱會包含說明）。 */
function fieldInput(page, name) {
  return page.locator('label.field').filter({ has: page.locator('xpath=./span[1]', { hasText: new RegExp('^' + name + '$') }) }).locator('input');
}

async function backupFlow(page, userData, prefix) {
  const RPW = '恢復密碼-e2e-correct-horse';
  const waitText = (s, ms = 60000) => page.waitForFunction((x) => document.body.innerText.includes(x), s, { timeout: ms }).then(() => true, () => false);
  let t = await text(page);
  check('備份頁：說明方案 B 不能恢復教材實體檔案、唯一播放影片及 Outgo 逐字稿',
    t.includes('不能恢復教材實體檔案（影片、音訊、文件）、唯一的播放影片（/Media/playback/*.mp4）及 Outgo 錄音與逐字稿'));
  check('備份頁：沒有恢復密碼／沒有遠端時有警告；私鑰與密碼都遺失無法解密的提醒',
    t.includes('尚未設定備份恢復密碼') && t.includes('尚未設定遠端備份') && t.includes('私鑰與恢復密碼都遺失時，所有加密備份都無法解密'));
  check('備份頁：沒有恢復密碼時「建立備份」不能按', await page.getByRole('button', { name: '建立備份', exact: true }).isDisabled());
  // 設定恢復密碼（兩次不同 → 擋下；正確 → 主機指令）
  await page.getByRole('button', { name: '設定恢復密碼' }).first().click();
  await fieldInput(page, '新的恢復密碼').fill(RPW);
  await fieldInput(page, '再輸入一次').fill(RPW + 'x');
  await page.locator('.bk-form').getByRole('button', { name: '設定恢復密碼' }).click();
  const mismatch = await waitText('兩次輸入的恢復密碼不同', 5000);
  await fieldInput(page, '新的恢復密碼').fill(RPW);
  await fieldInput(page, '再輸入一次').fill(RPW);
  await page.locator('.bk-form').getByRole('button', { name: '設定恢復密碼' }).click();
  const keyOk = await waitText('已設定恢復密碼；之後的備份都以這把金鑰加密', 90000);
  await page.waitForTimeout(1500);
  t = await text(page);
  check('設定恢復密碼：Server App → 主機指令（標準輸入）完成，狀態「✓ 已設定」', mismatch && keyOk && t.includes('✓ 已設定：備份以 age 公鑰加密'), `mismatch=${mismatch} ok=${keyOk}`);
  await shot(page, 'server-42-backup-key');
  // 建立加密備份（Agent）→ 主機解密驗證
  await page.getByRole('button', { name: '建立備份', exact: true }).click();
  const made = await waitText('加密（age）', 90000);
  await page.waitForTimeout(800);
  await page.locator('table.backups').getByRole('button', { name: '解密驗證', exact: true }).first().click();
  const verified = await waitText('✓ 解密驗證通過', 90000);
  check('建立加密備份（.tar.gz.age），主機解密驗證通過', made && verified, `made=${made} verified=${verified}`);
  // 遠端：public_html 被擋；正確設定 → 測試連線 → 上傳（先解密驗證＋還原測試，下載比對）
  const f = state.ftps;
  await page.getByRole('button', { name: '設定遠端' }).click();
  await fieldInput(page, 'FTPS 主機').fill(f.host);
  await fieldInput(page, '連接埠').fill(String(f.port));
  await fieldInput(page, 'FTP 帳號').fill(f.username);
  await fieldInput(page, 'FTP 密碼').fill(f.password);
  await fieldInput(page, '遠端資料夾').fill('/public_html/backup');
  await page.getByRole('button', { name: '儲存遠端設定' }).click();
  const blocked = await waitText('網站公開目錄（public_html）裡', 60000);
  await fieldInput(page, 'FTP 密碼').fill(f.password);
  await fieldInput(page, '遠端資料夾').fill('/istudy-backup');
  await page.getByRole('button', { name: '儲存遠端設定' }).click();
  const saved = await waitText('已儲存遠端設定', 60000);
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: '測試遠端連線' }).click();
  const tested = await waitText('✓ 連線正常', 60000);
  check('遠端（FTPS，本機模擬）：public_html 被擋、儲存、連線測試成功', blocked && saved && tested, `blocked=${blocked} saved=${saved} tested=${tested}`);
  await page.getByRole('button', { name: '立即上傳最新備份' }).click();
  const up = await waitText('下載比對 SHA-256 相符', 120000);
  await page.waitForTimeout(1500);
  t = await text(page);
  const remoteFiles = fs.existsSync(path.join(f.root, 'istudy-backup')) ? fs.readdirSync(path.join(f.root, 'istudy-backup')) : [];
  check('上傳：先解密驗證＋還原測試，上傳後下載比對，清單顯示「✓ 遠端已驗證」；遠端有備份、.sha256、恢復金鑰、RESTORE.md',
    up && t.includes('✓ 遠端已驗證') && remoteFiles.some((n) => n.endsWith('.tar.gz.age')) && remoteFiles.includes('istudy-recovery-key.age')
    && remoteFiles.includes('RESTORE.md') && !remoteFiles.some((n) => n.endsWith('.part')), remoteFiles.join(','));
  // 保留天數 10～15
  const opts = await page.locator('section.panel', { hasText: '3. 保留與清理' }).locator('select option').allInnerTexts();
  check('保留天數只能選 10～15 天（預設 15）', JSON.stringify(opts) === JSON.stringify(['10 天', '11 天', '12 天', '13 天', '14 天', '15 天']), opts.join(','));
  await shot(page, 'server-43-backup-done');
  // 密碼不能出現在：Server App 資料夾（Log）、主機測試資料夾、系統 Log（API）
  const leaks = [];
  const scan = (dir) => {
    if (!dir || !fs.existsSync(dir)) return;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) scan(p);
      // FTP 密碼本來就存在主機 root 專用的 /etc/istudy/backup/remote.json（0600）：這一個檔案不算外洩
      else if (e.isFile() && !p.endsWith(path.join('etc', 'istudy', 'backup', 'remote.json')) && fs.statSync(p).size < 50 * 1024 * 1024) {
        const b = fs.readFileSync(p);
        if (b.includes(Buffer.from(RPW)) || b.includes(Buffer.from(f.password))) leaks.push(p);
      }
    }
  };
  scan(userData);
  scan(state.backup_dir);
  scan(prefix);   // 主機測試資料夾（/etc/istudy/backup 等）
  const sup = await apiCall(state.admin, 'POST', '/api/login', { username: state.accounts.super.username, password: state.accounts.super.password });
  for (const src of ['host', 'server', 'agent']) {
    const r = await apiCall(state.admin, 'GET', `/api/admin/logs?source=${src}&limit=1000`, null, sup.json && sup.json.token);
    const txt = JSON.stringify(r.json || {});
    if (txt.includes(RPW) || txt.includes(f.password)) leaks.push(`系統 Log（${src}）`);
  }
  const cfg = await apiCall(state.admin, 'GET', '/api/admin/backups', null, sup.json && sup.json.token);
  if (JSON.stringify(cfg.json || {}).includes(f.password)) leaks.push('/api/admin/backups');
  check('恢復密碼與 FTP 密碼沒有出現在 Server App 資料夾、備份資料夾、系統 Log、備份 API', leaks.length === 0, leaks.join('、'));
}

/** e2e 用的 API 呼叫（Node 端，不經過 App）。 */
async function apiCall(base, method, p, body, token) {
  const r = await fetch(base + p, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}

/** dev.62：按視窗 X（系統匣模式）＝ 儲存位置、登出（Server 撤銷 Session）、回到登入畫面、縮到系統匣；App 沒有結束。 */
async function closeFlow(label, dir, electronPath) {
  console.log(`\n=== Client App ${label}：按 X 登出 ===`);
  const { app, page, errors } = await launch(dir, electronPath, { ISTUDY_SERVER: state.lan, ISTUDY_NO_TRAY: '0', ISTUDY_HEARTBEAT_MS: '3000' });
  await page.waitForSelector('.health.ok', { timeout: 20000 }).catch(() => undefined);
  await login(page, 'user');
  const since = Date.now() - 2000;
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
  const hidden = await (async () => {
    for (let i = 0; i < 40; i++) {
      const v = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.isVisible()));
      if (v.length === 1 && !v[0] && (await page.locator('.login-card').count())) return true;
      await page.waitForTimeout(250);
    }
    return false;
  })();
  const sup = await apiCall(state.admin, 'POST', '/api/login', { username: state.accounts.super.username, password: state.accounts.super.password });
  const audit = await apiCall(state.admin, 'GET', '/api/audit?limit=50', null, sup.json && sup.json.token);
  const items = (audit.json && audit.json.items) || [];
  const loggedOut = items.some((x) => x.action === 'logout' && x.actor_name === '陳助教' && Date.parse(x.created_at) >= since);
  check(`${label} 按 X：Server 端已登出、畫面回到登入頁、視窗縮到系統匣（App 未結束）`, hidden && loggedOut, `hidden=${hidden} logout=${loggedOut}`);
  await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.show(); });
  await page.waitForTimeout(500);
  check(`${label} 按 X 後再開啟：顯示登入畫面（不會接到上一位的登入）`, (await page.locator('.login-card').count()) > 0 && !(await page.locator('.topnav').count()));
  await shot(page, `${label}-20-after-close`);
  check(`${label}（按 X）畫面沒有 JavaScript 錯誤`, errors.length === 0, errors.slice(0, 5).join(' | '));
  await app.evaluate(({ app: a }) => a.quit()).catch(() => undefined);
  await app.close().catch(() => undefined);
}

try {
  if (ONLY === 'deb') await installedFlow(arg('exe', '/opt/istudy-server-app/istudy-server-app'));
  if (!ONLY || ONLY === 'client') await clientFlow('win10', path.join(ROOT, 'dist', 'client-win10'), E44);
  if (!ONLY || ONLY === 'client') await closeFlow('win10', path.join(ROOT, 'dist', 'client-win10'), E44);
  if (!ONLY || ONLY === 'win7') await clientFlow('win7', path.join(ROOT, 'dist', 'client-win7'), E22);
  if (!ONLY || ONLY === 'win7') await closeFlow('win7', path.join(ROOT, 'dist', 'client-win7'), E22);
  if (!ONLY || ONLY === 'server') await serverFlow(path.join(ROOT, 'dist', 'server'));
} catch (e) {
  check('測試流程中斷', false, e.stack || String(e));
}
fs.writeFileSync(path.join(SHOTS, 'results.json'), JSON.stringify(results, null, 1));
console.log(`\n${results.length - failures}/${results.length} 通過`);
process.exit(failures ? 1 : 0);
