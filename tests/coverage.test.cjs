const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '../app/static/js/coverage.js'), 'utf8');
const feature = {
  type: 'Feature', properties: { count: 1, percentage: 100 },
  geometry: { type: 'MultiLineString', coordinates: [[[9, 45], [9.001, 45]]] },
};
const ready = {
  total_routes: 1, mapped_routes: 1, missing_routes: 0,
  tolerance_m: 12, max_gap_m: 250, skipped_gaps: 0,
  geojson: { type: 'FeatureCollection', features: [feature] },
};

async function until(fn) {
  for (let i = 0; i < 100; i += 1) {
    if (fn()) return;
    await new Promise(setImmediate);
  }
  assert.fail('Expected async state was not reached');
}

function setup(pending, fail = false) {
  const els = new Map();
  const layers = new Set();
  const timers = [];
  const urls = [];
  let releases = 0;
  let calls = 0;
  const map = {
    setView() { return this; },
    fitBounds() {},
    removeLayer(layer) { layers.delete(layer); },
    hasLayer(layer) { return layers.has(layer); },
  };
  const L = {
    map: () => map,
    tileLayer: () => ({ addTo() {} }),
    geoJSON: (_, opts) => ({
      opts, features: [],
      addData(f) { this.features.push(f); },
      addTo() { layers.add(this); return this; },
      getBounds() { return { isValid: () => this.features.length > 0 }; },
    }),
  };
  const document = {
    getElementById(id) {
      if (!els.has(id)) els.set(id, { checked: true, addEventListener() {}, textContent: '' });
      return els.get(id);
    },
  };
  const win = {
    L, location: { assign() {} },
    sessionGuard: { beginTask: () => () => { releases += 1; } },
    setTimeout(fn, ms) {
      if (ms === 0) setImmediate(fn);
      else timers.push(fn);
    },
  };
  async function fetch(url) {
    urls.push(url);
    if (url.startsWith('/api/coverage/tracks')) {
      return { ok: true, status: 200, json: async () => ({ geojson: feature, loaded_routes: 1, next_after: 1, has_more: false }) };
    }
    calls += 1;
    if (pending && calls === 1) return { ok: true, status: 202, json: async () => ({ status: 'processing' }) };
    if (fail) return { ok: false, status: 503 };
    return { ok: true, status: 200, json: async () => ready };
  }
  vm.runInNewContext(src, { window: win, document, L, fetch, AbortController });
  return { els, layers, timers, urls, get releases() { return releases; } };
}

test('database tracks are visible while frequencies are still processing', async () => {
  const env = setup(true);
  await until(() => [...env.layers].some((l) => l.features.length));
  assert.ok([...env.layers].some((l) => l.opts.style.color === '#64748b'));
  assert.equal(env.els.get('fitCoverage').disabled, false);
  assert.equal(env.releases, 0);
  assert.ok(env.els.get('coverageStatus').textContent.includes('puoi già esplorare'));
  env.timers.shift()();
  await until(() => env.releases === 1);
  assert.equal(env.layers.size, 1);
  assert.equal(env.els.get('reloadCoverage').disabled, false);
  assert.ok(env.els.get('coverageStatus').textContent.includes('1 pattinate totali'));
});

test('saved frequencies render directly without fetching previews', async () => {
  const env = setup(false);
  await until(() => env.releases === 1);
  assert.deepEqual(env.urls, ['/api/coverage']);
  assert.equal(env.layers.size, 1);
});

test('calculation failure keeps database tracks usable and releases the session hold', async () => {
  const env = setup(true, true);
  await until(() => [...env.layers].some((l) => l.features.length));
  env.timers.shift()();
  await until(() => env.releases === 1);
  assert.equal(env.layers.size, 1);
  assert.equal(env.els.get('reloadCoverage').disabled, false);
  assert.ok(env.els.get('coverageStatus').textContent.includes('restano esplorabili'));
});
