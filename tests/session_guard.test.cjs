const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '../app/static/js/session-guard.js'), 'utf8');

function setup() {
  let now = 0;
  let tick;
  let closes = 0;
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const win = {
    location: { href: 'http://localhost/coverage', origin: 'http://localhost', replace() {} },
    addEventListener() {},
    setInterval(fn) { tick = fn; },
    fetch(url) {
      if (url === '/session/close-tab') {
        closes += 1;
        return Promise.resolve({});
      }
      return pending;
    },
  };
  vm.runInNewContext(src, {
    window: win,
    document: {
      querySelector: () => ({ dataset: { csrf: 'test', timeout: '1' } }),
      addEventListener() {},
    },
    Date: { now: () => now }, URL, Request, FormData, navigator: {},
    fetch: (...args) => win.fetch(...args),
  });
  return {
    win, finish,
    advance(ms) { now += ms; tick(); },
    get closes() { return closes; },
  };
}

test('long calculations suspend timeout and restart the full idle period', () => {
  const env = setup();
  const end = env.win.sessionGuard.beginTask();
  env.advance(60000);
  assert.equal(env.closes, 0);
  end();
  env.advance(999);
  assert.equal(env.closes, 0);
  env.advance(1);
  assert.equal(env.closes, 1);
});

test('response headers do not release the calculation hold', async () => {
  const env = setup();
  const end = env.win.sessionGuard.beginTask();
  const req = env.win.fetch('/api/coverage');
  env.finish({});
  await req;
  env.advance(60000);
  assert.equal(env.closes, 0);
  end();
  env.advance(1000);
  assert.equal(env.closes, 1);
});

test('releasing a task twice cannot release another active task', () => {
  const env = setup();
  const a = env.win.sessionGuard.beginTask();
  const b = env.win.sessionGuard.beginTask();
  a();
  a();
  env.advance(60000);
  assert.equal(env.closes, 0);
  b();
  env.advance(1000);
  assert.equal(env.closes, 1);
});
