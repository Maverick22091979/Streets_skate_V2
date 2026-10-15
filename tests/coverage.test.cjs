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

function setup(pending, fail = false, geo) {
  const els = new Map();
  const layers = new Set();
  const timers = [];
  const urls = [];
  let releases = 0;
  let calls = 0;
  const views = [];
  let frames = 0;
  const map = {
    setView(point, zoom) { views.push({ point: [...point], zoom }); return this; },
    fitBounds() { frames += 1; },
    removeLayer(layer) { layers.delete(layer); },
    hasLayer(layer) { return layers.has(layer); },
  };
  const L = {
    map: () => map,
    tileLayer: () => ({ addTo() {} }),
    circleMarker: () => ({ addTo() { return this; }, bindPopup() { return this; } }),
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
    navigator: { geolocation: geo },
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
  return { els, layers, timers, urls, views, get frames() { return frames; }, get releases() { return releases; } };
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

test('device position remains the map center after frequencies finish loading', async () => {
  let success;
  const env = setup(false, false, { getCurrentPosition(fn) { success = fn; } });
  await until(() => env.releases === 1);
  assert.equal(env.frames, 0);
  success({ coords: { latitude: 41.9, longitude: 12.5 } });
  assert.deepEqual(env.views.at(-1), { point: [41.9, 12.5], zoom: 15 });
  assert.equal(env.els.get('locateCoverage').disabled, false);
  assert.ok(env.els.get('coverageLocationStatus').textContent.includes('centrata'));
});

test('an early location result is not replaced by automatic route framing', async () => {
  const env = setup(false, false, {
    getCurrentPosition(success) { success({ coords: { latitude: 41.9, longitude: 12.5 } }); },
  });
  await until(() => env.releases === 1);
  assert.equal(env.frames, 0);
  assert.deepEqual(env.views.at(-1), { point: [41.9, 12.5], zoom: 15 });
});

test('denied location permission falls back to the loaded routes', async () => {
  let deny;
  const env = setup(false, false, { getCurrentPosition(_, fn) { deny = fn; } });
  await until(() => env.releases === 1);
  deny({ code: 1 });
  assert.equal(env.frames, 1);
  assert.equal(env.els.get('locateCoverage').disabled, false);
  assert.ok(env.els.get('coverageLocationStatus').textContent.includes('negato'));
});
