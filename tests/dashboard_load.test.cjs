const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '../app/static/js/dashboard.js'), 'utf8');
const funcs = src.slice(src.indexOf('async function readImportResponse'), src.indexOf('async function importRoutes'));

function response(status, data) {
  return { status, ok: status === 200, text: async () => typeof data === 'string' ? data : JSON.stringify(data) };
}

function setup(fetch) {
  let holds = 0;
  const ctx = {
    fetch, AbortController,
    window: { sessionGuard: { beginTask: () => { holds++; return () => { holds--; }; } } },
    grid: { innerHTML: 'old cards' }, statusText: {}, reloadBtn: {}, importBtn: {},
    allRoutes: [{ id: 99 }], provider: 'admin',
    renderUserFilter() {}, renderRoutes: (routes) => routes,
  };
  vm.createContext(ctx);
  vm.runInContext(funcs, ctx);
  return { ctx, get holds() { return holds; } };
}

test('reload follows every page and restores dashboard date ordering', async () => {
  const urls = [];
  let env;
  env = setup(async (url) => {
    assert.equal(env.holds, 1);
    urls.push(url);
    return urls.length === 1
      ? response(200, { routes: [{ id: 1, start_date_local: '2026-01-01' }], users: [], has_more: true, next_after: 1 })
      : response(200, { routes: [{ id: 2, start_date_local: '2026-02-01' }], has_more: false });
  });
  await env.ctx.loadRoutes();
  assert.deepEqual(urls, ['/api/routes?after_id=0&limit=25', '/api/routes?after_id=1&limit=25']);
  assert.deepEqual(Array.from(env.ctx.allRoutes, (r) => r.id), [2, 1]);
  assert.match(env.ctx.statusText.textContent, /2 percorsi in archivio/);
  assert.equal(env.ctx.reloadBtn.disabled, false);
  assert.equal(env.holds, 0);
});

test('HTML proxy error reports HTTP status and preserves the displayed archive', async () => {
  const env = setup(async () => response(504, '<!DOCTYPE html><html>Gateway Timeout</html>'));
  await env.ctx.loadRoutes();
  assert.match(env.ctx.statusText.textContent, /HTTP 504.*Timeout del proxy/);
  assert.equal(env.ctx.grid.innerHTML, 'old cards');
  assert.equal(env.ctx.allRoutes[0].id, 99);
  assert.equal(env.holds, 0);
  assert.equal(env.ctx.reloadBtn.disabled, false);
});

test('failed later page does not replace the archive with a partial result', async () => {
  let calls = 0;
  const env = setup(async () => ++calls === 1
    ? response(200, { routes: [{ id: 1 }], has_more: true, next_after: 1 })
    : response(503, '<html>Unavailable</html>'));
  await env.ctx.loadRoutes();
  assert.equal(env.ctx.allRoutes[0].id, 99);
  assert.match(env.ctx.statusText.textContent, /HTTP 503/);
});
