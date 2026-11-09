const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '../app/static/js/dashboard.js'), 'utf8');
const code = src.slice(src.indexOf('function updateRouteSelection()'), src.indexOf('function readWeights()'));

test('select all includes only personal routes and sends their ids', async () => {
  let onSelect, onPublish, sent;
  const selectAllPersonalRoutes = { addEventListener(_, cb) { onSelect = cb; } };
  const publishSelectedRoutes = { addEventListener(_, cb) { onPublish = cb; } };
  const ctx = {
    selectAllPersonalRoutes, publishSelectedRoutes, selectionStatus: {}, selectedRouteIds: new Set(),
    allRoutes: [{ id: 1, import_user_id: 7 }, { id: 2, import_user_id: 9 }, { id: 3, import_user_id: 7 }],
    personalOwnerId: 7, document: { querySelectorAll: () => [] }, csrfHeaders: () => ({}),
    fetch: async (_, options) => { sent = JSON.parse(options.body); return { ok: true, json: async () => ({ published: 2 }) }; },
  };
  ctx.renderRoutes = () => ctx.updateRouteSelection();
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  selectAllPersonalRoutes.checked = true;
  onSelect();
  assert.deepEqual([...ctx.selectedRouteIds], [1, 3]);
  assert.equal(selectAllPersonalRoutes.checked, true);
  assert.equal(publishSelectedRoutes.hidden, false);
  await onPublish();
  assert.deepEqual(sent, [1, 3]);
  assert.match(ctx.selectionStatus.textContent, /2 percorsi/);
  assert.equal(ctx.selectedRouteIds.size, 0);
  assert.equal(publishSelectedRoutes.hidden, true);
});

test('verification link disappears after the admin decision', () => {
  const render = src.slice(src.indexOf('function renderRoute(route)'), src.indexOf('function renderUserFilter('));
  const ctx = { isAdmin: true, personalOwnerId: 1, selectedRouteIds: new Set(),
    safeNum: n => Number(n || 0), escapeHtml: x => String(x), safeAttr: x => String(x),
    safeText: x => String(x || ''), badgeClass: () => '', declaredDifficultyClasses: { Unknown: '' } };
  vm.createContext(ctx);
  vm.runInContext(render, ctx);
  assert.match(ctx.renderRoute({ id: 1, import_user_id: 1 }), /Verifica percorso/);
  assert.doesNotMatch(ctx.renderRoute({ id: 1, import_user_id: 1, official_reviewed: true }), /Verifica percorso/);
});
