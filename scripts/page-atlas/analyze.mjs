import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(process.argv[2] || path.join(path.dirname(fileURLToPath(import.meta.url)), '../..'));
const sourceDirs = ['packages/ui/src', 'apps/client/src', 'apps/server/src', 'server/src'];
const unique = values => [...new Set(values)].sort();
const normalizePath = value => value.replace(/\$\{[^}]+\}/g, '{id}').replace(/\/$/, '');

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  });
}
function loadSources() {
  return sourceDirs.flatMap(dir => walk(path.join(root, dir)))
    .filter(file => /\.(tsx?|php)$/.test(file))
    .map(file => ({ path: path.relative(root, file).split(path.sep).join('/'), content: fs.readFileSync(file, 'utf8') }));
}
export function parseHttpCalls(source) {
  const calls = [];
  const pattern = /\bapi\.(get|post|put|patch|del|delete)\s*\(\s*(['"`])([^'"`]+)\2/gs;
  for (const match of source.matchAll(pattern)) {
    calls.push({ method: ({ del: 'DELETE', delete: 'DELETE' })[match[1]] || match[1].toUpperCase(),
      path: normalizePath(match[3].split('?')[0]), dynamic: match[3].includes('$' + '{') });
  }
  return calls;
}
function extractSqlTables(source) {
  const tables = [];
  for (const sql of source.matchAll(/(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+["']?([a-z_][a-z0-9_]*)/gi)) tables.push(sql[1].toLowerCase());
  return unique(tables);
}
function extractFunctionCalls(source, knownNames) {
  const calls = [];
  for (const match of source.matchAll(/\b([a-z_][a-z0-9_]*)\s*\(/gi)) {
    const name = match[1].toLowerCase();
    if (knownNames.has(name)) calls.push(name);
  }
  return unique(calls);
}
function findFunctionBodyEnd(source, openingBrace) {
  let depth = 0;
  let quote = null;
  let lineComment = false;
  let blockComment = false;
  for (let index = openingBrace; index < source.length; index++) {
    const char = source[index], next = source[index + 1];
    if (lineComment) { if (char === '\n') lineComment = false; continue; }
    if (blockComment) { if (char === '*' && next === '/') { blockComment = false; index++; } continue; }
    if (quote !== null) {
      if (char === '\\') { index++; continue; }
      if (char === quote) quote = null;
      continue;
    }
    if (char === '/' && next === '/') { lineComment = true; index++; continue; }
    if (char === '/' && next === '*') { blockComment = true; index++; continue; }
    if (char === '#') { lineComment = true; continue; }
    if (char === "'" || char === '"' || char === '\x60') { quote = char; continue; }
    if (char === '{') depth++;
    if (char === '}' && --depth === 0) return index;
  }
  return -1;
}
export function parsePhpFunctions(source, file) {
  const definitions = [];
  const pattern = /\bfunction\s+([a-z_][a-z0-9_]*)\s*\(/gi;
  for (const match of source.matchAll(pattern)) {
    const openingBrace = source.indexOf('{', match.index + match[0].length);
    if (openingBrace < 0) continue;
    const closingBrace = findFunctionBodyEnd(source, openingBrace);
    if (closingBrace < 0) continue;
    const name = match[1].toLowerCase();
    const body = source.slice(openingBrace + 1, closingBrace);
    definitions.push({ name, file, calls: [...body.matchAll(/\b([a-z_][a-z0-9_]*)\s*\(/gi)].map(call => call[1].toLowerCase()),
      tables: extractSqlTables(body) });
  }
  return definitions;
}
export function parsePhpRoutes(source, file) {
  const routes = [];
  const pattern = /route\(\s*'([^']+)'\s*,\s*'([^']+)'/g;
  for (const match of source.matchAll(pattern)) {
    const next = source.indexOf('\nroute(', match.index + match[0].length);
    const body = source.slice(match.index, next < 0 ? source.length : next);
    routes.push({ methods: match[1].split('|').map(value => value.trim()), path: normalizePath(match[2]),
      file, tables: extractSqlTables(body), calls: [], _body: body });
  }
  return routes;
}
function expandRouteCallGraph(route, functionIndex) {
  const names = new Set(functionIndex.keys());
  const calls = extractFunctionCalls(route._body || '', names);
  const visited = new Set();
  const queue = [...calls];
  const chain = [];
  const tables = new Set(route.tables);
  while (queue.length) {
    const name = queue.shift();
    if (visited.has(name)) continue;
    visited.add(name);
    const definition = functionIndex.get(name);
    if (!definition) continue;
    chain.push({ name, file: definition.file });
    for (const table of definition.tables) tables.add(table);
    for (const child of definition.calls) if (!visited.has(child)) queue.push(child);
  }
  return { calls: unique(calls), helperChain: chain, tables: unique([...tables]) };
}
export function parsePages(source, file) {
  const imports = new Map();
  const importPattern = /import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]/g;
  for (const match of source.matchAll(importPattern)) {
    const modulePath = match[2];
    for (const entry of match[1].split(',')) {
      const parts = entry.trim().split(/\s+as\s+/);
      const imported = parts[0].trim();
      const local = (parts[1] || parts[0]).trim();
      if (imported && local) imports.set(local, modulePath);
    }
  }
  const registries = [
    { name: 'CLIENT_PAGES', pattern: /\bCLIENT_PAGES\s*:\s*Record<[\s\S]*?=\s*\{([\s\S]*?)\n\};/m },
    { name: 'RAW', pattern: /\bRAW\s*:\s*Record<[\s\S]*?=\s*\{([\s\S]*?)\n\};/m },
    { name: 'CLIENT_PAGES', pattern: /\bCLIENT_PAGES\s*=\s*\{([\s\S]*?)\n\};/m },
    { name: 'RAW', pattern: /\bRAW\s*=\s*\{([\s\S]*?)\n\};/m },
  ];
  const pages = [];
  for (const registry of registries) {
    const match = registry.pattern.exec(source);
    if (!match) continue;
    for (const item of match[1].matchAll(/(?:['"]([^'"]+)['"]|([A-Za-z_$][\w$]*))\s*:\s*([A-Za-z_$][\w$]*)/g)) {
      const pageId = item[1] || item[2];
      const component = item[3];
      const modulePath = imports.get(component);
      let componentFile = null;
      if (modulePath && modulePath.startsWith('.')) {
        componentFile = path.posix.normalize(path.posix.join(path.posix.dirname(file), modulePath));
        if (!/\.(tsx?|jsx?)$/.test(componentFile)) componentFile += '.tsx';
      }
      pages.push({ id: pageId, component, file, componentFile, registry: registry.name });
    }
  }
  return pages;
}
export function analyze(sources) {
  const ui = sources.filter(file => /\.(tsx?|jsx?)$/.test(file.path));
  const php = sources.filter(file => file.path.endsWith('.php'));
  const pages = ui.flatMap(file => parsePages(file.content, file.path));
  const routes = php.flatMap(file => parsePhpRoutes(file.content, file.path));
  const functions = php.flatMap(file => parsePhpFunctions(file.content, file.path));
  const functionIndex = new Map(functions.map(definition => [definition.name, definition]));
  for (const definition of functions) definition.calls = unique(definition.calls.filter(name => functionIndex.has(name)));
  for (const route of routes) {
    const expanded = expandRouteCallGraph(route, functionIndex);
    route.calls = expanded.calls;
    route.helperChain = expanded.helperChain;
    route.tables = expanded.tables;
    delete route._body;
  }
  const edges = [];
  for (const file of ui) {
    const pageCandidates = pages.filter(item => item.componentFile === file.path);
    const source = pageCandidates.length === 1 ? pageCandidates[0].id : file.path;
    for (const call of parseHttpCalls(file.content)) {
      const matches = routes.filter(route => route.path === call.path && route.methods.includes(call.method));
      edges.push({ source, ...(pageCandidates.length > 1 ? { pageCandidates: pageCandidates.map(item => item.id) } : {}),
        kind: 'HTTP', method: call.method, target: call.path,
        status: matches.length === 1 ? 'RESOLVED' : matches.length ? 'AMBIGUOUS' : 'UNKNOWN',
        routes: matches.map(route => ({ file: route.file, tables: route.tables })) });
    }
  }
  return { schema: 'istudy-page-atlas-analysis/1', summary: { files: sources.length, pages: pages.length, routes: routes.length,
    resolved: edges.filter(edge => edge.status === 'RESOLVED').length, unknown: edges.filter(edge => edge.status !== 'RESOLVED').length,
    phpFunctions: functions.length, routesWithHelpers: routes.filter(route => route.helperChain.length > 0).length },
    pages, routes, functions: functions.map(({ name, file, calls, tables }) => ({ name, file, calls, tables })), edges, limitations: ['PHP helper tracing follows statically named functions across files; dynamic dispatch, methods, callbacks and closures remain unresolved.',
      'Function body scanning uses balanced braces and skips quoted strings and comments; heredoc/nowdoc edge cases are not parsed as a PHP AST.',
      'Page mapping resolves named imports in CLIENT_PAGES and RAW registries; shared component files retain pageCandidates instead of guessing.',
      'Only direct api.get/post/put/patch/del/delete calls with literal or template paths are collected; fetchText and indirect wrappers are not yet mapped.',
      'SQL extraction is lexical; dynamic SQL and query construction in indirect abstractions may be incomplete.',
      'UNKNOWN is intentional; no missing relation is inferred.'] };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = analyze(loadSources());
  console.log(JSON.stringify(result, null, 2));
}
