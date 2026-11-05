import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze, parseHttpCalls, parsePhpRoutes, parsePages, parsePhpFunctions } from '../page-atlas/analyze.mjs';

test('CG-001 resolves a static API route and its SQL table', () => {
  const result = analyze([
    { path: 'apps/client/src/index.tsx', content: "const RAW: Record<string, any> = { 'client-catalog': CatalogPage\n};" },
    { path: 'apps/client/src/Catalog.tsx', content: "api.get('/api/tree');" },
    { path: 'server/src/routes/catalog.php', content: "route('GET', '/api/tree', static function (): array { return q_all('SELECT * FROM folders'); });" }
  ]);
  assert.equal(result.pages.length, 1);
  assert.equal(result.edges[0].status, 'RESOLVED');
  assert.deepEqual(result.edges[0].routes[0].tables, ['folders']);
});

test('HTTP methods normalize delete and dynamic route segments', () => {
  assert.deepEqual(parseHttpCalls("api.del('/api/media/12'); api.patch('/api/media/\${id}');"), [
    { method: 'DELETE', path: '/api/media/12', dynamic: false },
    { method: 'PATCH', path: '/api/media/{id}', dynamic: true }
  ]);
});

test('PHP routes preserve multi-method declarations', () => {
  const routes = parsePhpRoutes("route('POST|PATCH|DELETE', '/api/media/{id}/tags', static function () { q('DELETE FROM media'); });", 'routes.php');
  assert.deepEqual(routes[0].methods, ['POST', 'PATCH', 'DELETE']);
  assert.deepEqual(routes[0].tables, ['media']);
});

test('Client CLIENT_PAGES registry maps page IDs to imported modules', () => {
  const pages = parsePages("import { CoursesPage } from './pages/Courses';\nconst CLIENT_PAGES: Record<string, (props: any) => any> = {\n courses: CoursesPage,\n};", 'packages/ui/src/App.tsx');
  assert.deepEqual(pages.map(page => page.id), ['courses']);
  assert.equal(pages[0].componentFile, 'packages/ui/src/pages/Courses.tsx');
  const result = analyze([
    { path: 'packages/ui/src/App.tsx', content: "import { CoursesPage } from './pages/Courses';\nconst CLIENT_PAGES: Record<string, any> = {\n courses: CoursesPage,\n};" },
    { path: 'packages/ui/src/pages/Courses.tsx', content: "api.get('/api/tree');" },
    { path: 'server/src/routes/catalog.php', content: "route('GET', '/api/tree', static function () { return q_all('SELECT * FROM folders'); });" }
  ]);
  assert.equal(result.edges[0].source, 'courses');
});
test('Server RAW registry maps IDs to actual server page modules', () => {
  const pages = parsePages("import { UemPage } from './pages/Uem';\nconst RAW: Record<string, () => any> = {\n 'srv-uem': UemPage,\n};", 'apps/server/src/index.tsx');
  assert.equal(pages[0].id, 'srv-uem');
  assert.equal(pages[0].componentFile, 'apps/server/src/pages/Uem.tsx');
});


test('PHP helper call graph resolves cross-file helpers and transitive SQL tables', () => {
  const result = analyze([
    { path: 'server/src/routes/catalog.php', content: "route('GET', '/api/folders/{id}', static function (array $p) { return folder_detail($p['id']); });" },
    { path: 'server/src/catalog_helpers.php', content: "function folder_detail(int $id): array { return load_folder($id); }\nfunction load_folder(int $id): array { return q_one('SELECT * FROM folders WHERE id = ?', [$id]) ?? []; }\nroute('GET', '/api/unrelated', static function () { return q_all('SELECT * FROM unrelated_table'); });" },
    { path: 'server/src/core.php', content: "function q_one(string $sql, array $params = []): ?array { return q($sql, $params)->fetch() ?: null; }\nfunction q(string $sql, array $params = []): PDOStatement { return db()->prepare($sql); }" }
  ]);
  const route = result.routes[0];
  assert.deepEqual(route.calls, ['folder_detail']);
  assert.deepEqual(route.helperChain.map(item => item.name), ['folder_detail', 'load_folder', 'q_one', 'q']);
  assert.deepEqual(route.tables, ['folders']);
  assert.equal(result.functions.length, 4);
});

test('unmatched API calls remain UNKNOWN', () => {
  const result = analyze([{ path: 'page.tsx', content: "api.get('/api/not-found');" }]);
  assert.equal(result.edges[0].status, 'UNKNOWN');
});

test('no API call means no invented route edge', () => {
  const result = analyze([{ path: 'page.tsx', content: 'export function Page() { return null; }' }]);
  assert.equal(result.edges.length, 0);
});
