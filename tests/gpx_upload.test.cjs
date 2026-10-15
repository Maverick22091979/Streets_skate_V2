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
      setTimeout: (fn) => setImmediate(fn),
    },
    setTimeout: () => {},
    FormData: class { constructor() { this.fields = []; } append(...args) { this.fields.push(args); } },
    fetch, encodeURIComponent,
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

test('uploads one GPX at a time, polls pending jobs, and holds the session until completion', async () => {
  let starts = 0;
  let polls = 0;
  let env;
  env = setup(async (url, opts) => {
    assert.equal(env.holds, 1);
    if (url.endsWith('/start')) {
      starts += 1;
      const files = opts.body.fields.filter(([key]) => key === 'files').map(([, value]) => value);
      assert.equal(files.filter((f) => f.name.endsWith('.gpx')).length, 1);
      return response(202, { job_id: `job${starts}` });
    }
    polls += 1;
    if (polls === 1) return response(200, { status: 'running' });
    return response(200, { status: 'done', result: { imported: 1, total_cached: starts } });
  });
  await env.ctx.uploadGpx();
  assert.equal(starts, 2);
  assert.equal(polls, 3);
  assert.equal(env.holds, 0);
  assert.equal(env.refreshes, 1);
  assert.equal(env.ctx.uploadGpxBtn.disabled, false);
  assert.match(env.ctx.gpxUploadState.textContent, /Import completato: 2 percorsi/);
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
