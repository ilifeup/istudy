#!/usr/bin/env node
/**
 * 建置 Client App（四個版別）與 Server App 的程式檔（第 19 章）。
 *
 *   node scripts/build.mjs                                → dist/server/（MVP：預設只建 Server App）
 *   node scripts/build.mjs --app client --channel win10   → dist/client-win10/（Client 版別：後續階段）
 *   node scripts/build.mjs --app client --channel win7    → dist/client-win7/（Electron 22：主程序 node16、畫面 chrome108）
 *   node scripts/build.mjs --app server                   → dist/server/
 *   node scripts/build.mjs --all                          → 五個都建
 *
 * 產出：main.js、preload.js（CommonJS）、renderer/（index.html、app.js＋分割檔、app.css、pdf.worker.min.mjs）、
 *       assets/icon.*、build-info.json（版別、版本、API 等級）、package.json（給 electron 與 electron-builder）。
 * 版本號一律取自根目錄 VERSION.json（單一來源）。安裝檔由 scripts/package_client.mjs（electron-builder）產生。
 */
import { build } from 'esbuild';
import { createRequire } from 'module';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const VERSION = JSON.parse(fs.readFileSync(path.join(ROOT, 'VERSION.json'), 'utf8'));
const CHANNELS = ['win7', 'win10', 'win11', 'ubuntu'];

// 開發機若把 react 等套件裝在全域，可用 ISTUDY_NODE_PATH 指定（以冒號分隔）
const nodePaths = (process.env.ISTUDY_NODE_PATH || process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean);

function resolvePkg(spec) {
  try {
    return require.resolve(spec, { paths: [ROOT, ...nodePaths] });
  } catch {
    for (const p of nodePaths) {
      const f = path.join(p, spec);
      if (fs.existsSync(f)) return f;
    }
    throw new Error(`找不到 ${spec}（請先在專案根目錄執行 npm install，或設定 ISTUDY_NODE_PATH）`);
  }
}

function canResolve(spec) {
  try {
    resolvePkg(spec);
    return true;
  } catch {
    return false;
  }
}

function targetFor(app, channel) {
  if (app === 'server') {
    return { name: 'server', channel: 'server', electron: VERSION.server_app.electron, version: VERSION.server, apiLevel: VERSION.api_level };
  }
  const c = VERSION.client[channel];
  if (!c) throw new Error(`未知的版別：${channel}（${CHANNELS.join('、')}）`);
  return { name: `client-${channel}`, channel, electron: c.electron, version: c.version, apiLevel: c.api_level };
}

/** Electron 主版號 → 主程序 Node 與畫面 Chromium 目標（Win7 版固定 Electron 22＝Node 16／Chrome 108）。 */
function engines(electron) {
  const major = Number(String(electron).split('.')[0]);
  return major <= 22 ? { node: 'node16', chrome: 'chrome108' } : { node: 'node20', chrome: 'chrome120' };
}

async function buildOne(app, channel, opts) {
  const t = targetFor(app, channel);
  const out = path.join(ROOT, 'dist', t.name);
  // 自動更新只用在 Windows 版（Ubuntu 版採手動更新）：electron-updater 打包進 main.js
  const wantsUpdater = app === 'client' && channel !== 'ubuntu';
  const bundleUpdater = wantsUpdater && canResolve('electron-updater');
  if (wantsUpdater && !bundleUpdater && opts.requireUpdater) {
    throw new Error('找不到 electron-updater：請先在專案根目錄執行 npm install（Windows 版安裝檔必須包含自動更新）');
  }
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(path.join(out, 'renderer'), { recursive: true });
  fs.mkdirSync(path.join(out, 'assets'), { recursive: true });
  const eng = engines(t.electron);
  // 畫面一律以 chrome108 為目標：四個版別與 Server App 共用同一份語法（Win7 版的 Electron 22）
  const rendererTarget = 'chrome108';
  const common = { bundle: true, logLevel: 'warning', legalComments: 'none', minify: !opts.dev, sourcemap: opts.dev ? 'inline' : false, nodePaths };

  await build({
    ...common,
    entryPoints: { app: path.join(ROOT, 'apps', app, 'src', 'index.tsx') },
    outdir: path.join(out, 'renderer'),
    format: 'esm',
    splitting: true,
    platform: 'browser',
    target: rendererTarget,
    jsx: 'automatic',
    chunkNames: 'chunks/[name]-[hash]',
    define: { 'process.env.NODE_ENV': JSON.stringify(opts.dev ? 'development' : 'production') },
    loader: { '.css': 'css' },
  });
  await build({
    ...common,
    entryPoints: { app: path.join(ROOT, 'packages', 'ui', 'src', 'styles.css') },
    outdir: path.join(out, 'renderer'),
    target: rendererTarget,
  });
  await build({
    ...common,
    entryPoints: [path.join(ROOT, 'apps', app, 'electron', 'main.ts')],
    outfile: path.join(out, 'main.js'),
    platform: 'node',
    format: 'cjs',
    target: eng.node,
    // Windows 版：electron-updater 直接打包進 main.js（安裝檔不需要 node_modules）；開發機沒裝時保留外部（不能自動更新）
    external: bundleUpdater ? ['electron'] : ['electron', 'electron-updater'],
  });
  if (bundleUpdater) {
    const js = fs.readFileSync(path.join(out, 'main.js'), 'utf8');
    if (/require\(\s*["']electron-updater["']\s*\)/.test(js) || !js.includes('NsisUpdater')) {
      throw new Error('main.js 沒有正確包含 electron-updater（esbuild 打包結果不完整）');
    }
  }
  await build({
    ...common,
    entryPoints: [path.join(ROOT, 'packages', 'shell', 'src', 'preload.ts')],
    outfile: path.join(out, 'preload.js'),
    platform: 'node',
    format: 'cjs',
    target: eng.node,
    external: ['electron'],
  });

  fs.copyFileSync(path.join(ROOT, 'packages', 'ui', 'static', 'index.html'), path.join(out, 'renderer', 'index.html'));
  fs.copyFileSync(resolvePkg('pdfjs-dist/legacy/build/pdf.worker.min.mjs'), path.join(out, 'renderer', 'pdf.worker.min.mjs'));
  for (const f of fs.readdirSync(path.join(ROOT, 'apps', app, 'assets'))) {
    fs.copyFileSync(path.join(ROOT, 'apps', app, 'assets', f), path.join(out, 'assets', f));
  }
  const info = { channel: t.channel, version: t.version, apiLevel: t.apiLevel, electron: t.electron, builtAt: new Date().toISOString() };
  fs.writeFileSync(path.join(out, 'build-info.json'), JSON.stringify(info, null, 1) + '\n');
  const pkg = {
    name: app === 'server' ? 'istudy-server-app' : `istudy-client-${channel}`,
    productName: app === 'server' ? 'iStudy Server App' : 'iStudy',
    version: t.version,
    description: app === 'server' ? 'iStudy Server App（Super 專用）' : `iStudy Client App（${channel}）`,
    main: 'main.js',
    author: '無相氣學',
    license: 'UNLICENSED',
    private: true,
  };
  fs.writeFileSync(path.join(out, 'package.json'), JSON.stringify(pkg, null, 1) + '\n');
  const size = dirSize(out);
  const updater = app !== 'client' ? '' : !wantsUpdater ? '，Ubuntu 版：手動更新' : bundleUpdater ? '，原始碼建置：main.js 已包含 electron-updater（打包後另檢查安裝產物）' : '，⚠ 沒有 electron-updater（npm install 後重建才能自動更新）';
  console.log(`✓ ${t.name}  ${t.version}  Electron ${t.electron}（主程序 ${eng.node}、畫面 ${rendererTarget}${updater}）  ${(size / 1024 / 1024).toFixed(1)} MB → ${path.relative(ROOT, out)}`);
  return out;
}

function dirSize(d) {
  let n = 0;
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    n += e.isDirectory() ? dirSize(p) : fs.statSync(p).size;
  }
  return n;
}

function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const dev = process.argv.includes('--dev');
const requireUpdater = process.argv.includes('--require-updater');
const jobs = process.argv.includes('--all')
  ? [...CHANNELS.map((c) => ['client', c]), ['server', 'server']]
  : [[arg('app') || 'server', arg('channel') || ((arg('app') || 'server') === 'server' ? 'server' : 'win10')]]; // MVP：預設建 Server App
for (const [app, channel] of jobs) {
  await buildOne(app, channel, { dev, requireUpdater });
}
