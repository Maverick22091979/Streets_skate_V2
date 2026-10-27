const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '../app/static/js/dashboard.js'), 'utf8');
const funcs = src.slice(src.indexOf('async function readImportResponse'), src.indexOf('async function loadRoutes'));

function response(status, data) {
  return { status, ok: status >= 200 && status < 300, text: async () => typeof data === 'string' ? data : JSON.stringify(data) };
}

function setup(fetch) {
  let holds = 0;
  let refreshes = 0;
  const ctx = {
    window: {
      crypto: { randomUUID: () => 'test-import-id' },
      sessionGuard: { beginTask: () => { holds += 1; return () => { holds -= 1; }; } },
      setTimeout: (fn, ms) => ms === 30000 ? null : setImmediate(fn),
      clearTimeout() {},
    },
    setTimeout: () => {},
    FormData: class { constructor() { this.fields = []; } append(...args) { this.fields.push(args); } },
    fetch, encodeURIComponent, AbortController,
    csrfToken: 'csrf', csrfHeaders: () => ({}),
    gpxFiles: { files: [{ name: 'a.gpx', size: 100 }, { name: 'a.json', size: 100 }, { name: 'b.gpx', size: 100 }] },
    statusText: {}, gpxUploadState: {}, uploadGpxBtn: {},
    gpxUploadForm: { reset() {} },
    setGpxProgress() {}, resetGpxProgress() {},
    shortSkipMessage: () => '', wrongTypeSkipMessage: () => '',
    loadRoutes: async () => { refreshes += 1; },
  };
  vm.createContext(ctx);
  vm.runInContext(funcs, ctx);
  return { ctx, get holds() { return holds; }, get refreshes() { return refreshes; } };
}

test('HTML gateway errors report their HTTP status instead of a JSON syntax error', async () => {
  const { ctx } = setup();
  await assert.rejects(ctx.readImportResponse(response(524, '<html><h1>Timeout</h1></html>')), /HTTP 524.*Cloudflare/);
  await assert.rejects(ctx.readImportResponse(response(413, '<html>Request too large</html>')), /HTTP 413.*grandi/);
});

test('uploads bounded blocks, polls pending jobs, and holds the session until completion', async () => {
  let starts = 0;
  let polls = 0;
  let env;
  env = setup(async (url, opts) => {
    assert.equal(env.holds, 1);
    if (url.endsWith('/start')) {
      starts += 1;
      const files = opts.body.fields.filter(([key]) => key === 'files').map(([, value]) => value);
      assert.equal(files.filter((f) => f.name.endsWith('.gpx')).length, 2);
      return response(202, { job_id: `job${starts}` });
    }
    polls += 1;
    if (polls === 1) return response(200, { status: 'running' });
    return response(200, { status: 'done', result: { imported: 2, total_cached: 2 } });
  });
  await env.ctx.uploadGpx();
  assert.equal(starts, 1);
  assert.equal(polls, 2);
  assert.equal(env.holds, 0);
  assert.equal(env.refreshes, 1);
  assert.equal(env.ctx.uploadGpxBtn.disabled, false);
  assert.match(env.ctx.gpxUploadState.textContent, /Import completato: 2 percorsi/);
});

test('blocks respect count and byte limits and keep matching JSON with its GPX', () => {
  const { ctx } = setup();
  const groups = Array.from({ length: 12 }, (_, i) => [{ name: `${i}.gpx`, size: 100 }]);
  assert.deepEqual(Array.from(ctx.gpxBatches(groups), (b) => b.length), [5, 5, 2]);
  const pair = [{ name: 'a.gpx', size: 3 * 1024 * 1024 }, { name: 'a.json', size: 1024 * 1024 }];
  const large = [{ name: 'b.gpx', size: 6 * 1024 * 1024 }];
  const batches = ctx.gpxBatches([pair, large, groups[0]]);
  assert.deepEqual(Array.from(batches, (b) => b.length), [1, 1, 1]);
  assert.equal(batches[0][0], pair);
});

test('planning splits 450 routes into 90 blocks and 12 groups of at most eight blocks', () => {
  const { ctx } = setup();
  const files = Array.from({ length: 450 }, (_, i) => ({ name: `${i}.gpx`, size: 100 }));
  const plan = ctx.gpxPlan(ctx.gpxGroups(files));
  assert.equal(plan.batches.length, 90);
  assert.equal(plan.sessions.length, 12);
  assert.ok(plan.sessions.every((s) => s.blocks <= 8 && s.routes <= 40));
  assert.equal(plan.sessions[11].blocks, 2);
  assert.equal(plan.sessions[11].routes, 10);
});

test('large files increase the real block count and JSON files are not counted as routes', () => {
  const { ctx } = setup();
  const files = Array.from({ length: 9 }, (_, i) => [
    { name: `${i}.gpx`, size: 3 * 1024 * 1024 }, { name: `${i}.json`, size: 10 },
  ]).flat();
  const plan = ctx.gpxPlan(ctx.gpxGroups(files));
  assert.equal(plan.batches.length, 9);
  assert.equal(plan.sessions.length, 2);
  assert.equal(plan.sessions[1].routes, 1);
});

test('queue rejection retries the same upload id and respects Retry-After', async () => {
  const ids = [];
  const waits = [];
  const env = setup(async (url, opts) => {
    ids.push(opts.headers['X-Import-Id']);
    return ids.length === 1
      ? { ...response(429, { detail: 'Coda piena' }), headers: { get: () => '7' } }
      : response(202, { job_id: 'accepted' });
  });
  const data = await env.ctx.startGpxImport({}, (ms) => waits.push(ms));
  assert.equal(data.job_id, 'accepted');
  assert.deepEqual(ids, ['test-import-id', 'test-import-id']);
  assert.deepEqual(waits, [7000]);
});

test('an upload gateway timeout retries with the same id to avoid a second job', async () => {
  const ids = [];
  const env = setup(async (url, opts) => {
    ids.push(opts.headers['X-Import-Id']);
    return ids.length === 1 ? response(504, '<html>Timeout</html>') : response(202, { job_id: 'original-job' });
  });
  const data = await env.ctx.startGpxImport({}, () => {});
  assert.equal(data.job_id, 'original-job');
  assert.deepEqual(ids, ['test-import-id', 'test-import-id']);
});

test('groups run sequentially with a pause after eight completed blocks and show percentages', async () => {
  let starts = 0;
  let completed = 0;
  let pauses = 0;
  const labels = [];
  const env = setup(async (url) => {
    if (url.endsWith('/start')) {
      assert.equal(starts, completed);
      starts++;
      return response(202, { job_id: String(starts) });
    }
    completed++;
    return response(200, { status: 'done', result: { imported: completed === 9 ? 1 : 5 } });
  });
  env.ctx.gpxFiles.files = Array.from({ length: 41 }, (_, i) => ({ name: `${i}.gpx`, size: 100 }));
  env.ctx.setGpxProgress = (done, total, label) => labels.push(label);
  env.ctx.window.setTimeout = (fn, ms) => {
    if (ms === 30000) return null;
    if (ms === 5000) {
      pauses++;
      assert.equal(completed, 8);
      assert.equal(env.holds, 1);
    }
    return setImmediate(fn);
  };
  await env.ctx.uploadGpx();
  assert.equal(starts, 9);
  assert.equal(pauses, 1);
  assert.ok(labels.some((label) => /Gruppo 2\/2: 100%.*Blocco 9\/9: 100%/.test(label)));
  assert.match(env.ctx.gpxUploadState.textContent, /Import completato: 41 percorsi/);
});

test('a transient polling timeout is retried without re-uploading files', async () => {
  let starts = 0;
  let polls = 0;
  const env = setup(async (url) => {
    if (url.endsWith('/start')) {
      starts += 1;
      return response(202, { job_id: 'job' });
    }
    polls += 1;
    if (polls === 1) return response(504, '<html>Timeout</html>');
    return response(200, { status: 'done', result: { imported: 2 } });
  });
  await env.ctx.uploadGpx();
  assert.equal(starts, 1);
  assert.equal(polls, 2);
  assert.match(env.ctx.gpxUploadState.textContent, /Import completato/);
});

test('repeated browser aborts keep polling the same import and preserve the session hold', async () => {
  let starts = 0;
  let polls = 0;
  const env = setup(async (url, opts) => {
    if (url.endsWith('/start')) {
      starts += 1;
      return response(202, { job_id: 'same-job' });
    }
    assert.equal(url, '/api/manual-import/jobs/same-job');
    assert.equal(env.holds, 1);
    polls += 1;
    if (polls <= 5) {
      return new Promise((resolve, reject) => opts.signal.addEventListener('abort', () => reject(opts.signal.reason), { once: true }));
    }
    assert.match(env.ctx.gpxUploadState.textContent, /riconnessione allo stesso import/);
    return response(200, { status: 'done', result: { imported: 2 } });
  });
  env.ctx.window.setTimeout = (fn) => setImmediate(fn);
  env.ctx.window.clearTimeout = (id) => clearImmediate(id);
  await env.ctx.uploadGpx();
  assert.equal(starts, 1);
  assert.equal(polls, 6);
  assert.equal(env.holds, 0);
  assert.match(env.ctx.gpxUploadState.textContent, /Import completato/);
});

test('session expiry and missing jobs stop polling without retrying', async () => {
  for (const status of [403, 404]) {
    let polls = 0;
    const env = setup(async () => {
      polls += 1;
      return response(status, { detail: 'Sessione o job non disponibile' });
    });
    await assert.rejects(env.ctx.importJobStatus('job'), new RegExp(`HTTP ${status}`));
    assert.equal(polls, 1);
  }
});

test('provider status retries use the provider job URL rather than the GPX URL', async () => {
  let polls = 0;
  const env = setup(async (url) => {
    assert.equal(url, '/api/import-jobs/strava-job');
    polls += 1;
    if (polls === 1) return response(504, '<html>Timeout</html>');
    return response(200, { status: 'done', result: { imported: 52 } });
  });
  const result = await env.ctx.importJobStatus('strava-job', () => {}, '/api/import-jobs/');
  assert.equal(polls, 2);
  assert.equal(result.result.imported, 52);
});

test('provider import keeps the session held through polling and shows stream warnings', async () => {
  const env = setup(async () => response(200, { job_id: 'strava-job' }));
  Object.assign(env.ctx, {
    provider: 'strava', importAllowed: true, importBtn: {}, allRoutes: [],
    startImportPulse() {}, stopImportPulse() {}, setImportFooter() {},
    setImportProgress() {}, hideImportFooter() {}, renderRoutes() {},
    pollImportJob: async () => {
      assert.equal(env.holds, 1);
      return { imported: 52, total_cached: 52, routes: [], warnings: ['GPS dettagliato non disponibile'] };
    },
  });
  const start = src.indexOf('async function importRoutes');
  vm.runInContext(src.slice(start, src.indexOf('if (reloadBtn)', start)), env.ctx);
  await env.ctx.importRoutes();
  assert.equal(env.holds, 0);
  assert.equal(env.ctx.importBtn.disabled, false);
  assert.match(env.ctx.statusText.textContent, /Importati 52 percorsi/);
  assert.match(env.ctx.statusText.textContent, /GPS dettagliato non disponibile/);
});

test('failed background imports display the actual error and release the session', async () => {
  const env = setup(async (url) => url.endsWith('/start')
    ? response(202, { job_id: 'job' })
    : response(200, { status: 'error', error: 'GPX non valido' }));
  await env.ctx.uploadGpx();
  assert.equal(env.holds, 0);
  assert.equal(env.ctx.uploadGpxBtn.disabled, false);
  assert.match(env.ctx.gpxUploadState.textContent, /GPX non valido/);
});
