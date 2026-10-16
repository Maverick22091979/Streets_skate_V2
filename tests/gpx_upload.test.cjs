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
    await assert.rejects(env.ctx.gpxJobStatus('job'), new RegExp(`HTTP ${status}`));
    assert.equal(polls, 1);
  }
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
