import { JSDOM, VirtualConsole } from 'jsdom';
import { fixtures } from './fixtures.mjs';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const root = process.env.AIPOOL_TEST_WEB ? path.resolve(process.env.AIPOOL_TEST_WEB)
  : path.resolve(fileURLToPath(new URL('../../web/', import.meta.url)));
const state = fixtures(), source = 'plus-gpt-6.1-sol', target = 'Relay/gpt-6.1-sol';
state.settings.plus_fallback = { enabled: false, check_interval: 30, models: {} };
let snapshot = {state: 'disabled', reason: '备用路由未启用', total: 0, available: 0, exhausted: 0, unknown: 0, checked_at: 0};
const requests = [], errors = [], console = new VirtualConsole();
console.on('jsdomError', e => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
  url: 'http://127.0.0.1:19326/#settings', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: console,
});
const w = dom.window, d = w.document, q = s => d.querySelector(s);
w.addEventListener('error', e => errors.push(e.message));
w.fetch = async (url, options = {}) => {
  const p = String(url).split('?')[0], body = options.body ? JSON.parse(options.body) : null;
  requests.push({p, method: options.method || 'GET', body});
  let data = {ok: true};
  if (p === '/api/whoami') data = {allowed: true};
  else if (p === '/api/keys') data = {keys: state.keys};
  else if (p === '/api/providers') data = {providers: state.providers};
  else if (p === '/api/overview') data = state.overview;
  else if (p === '/api/settings') {
    if (body) { Object.assign(state.settings, body); data = {ok: true, restarted: false}; }
    else data = state.settings;
  } else if (p === '/api/plus-fallback') {
    data = {snapshot, config: state.settings.plus_fallback,
      sources: [{id: source, display: 'Plus · GPT 6.1 Sol'}],
      targets: [{id: target, display: 'Relay · GPT 6.1 Sol', provider: 'Test Relay'}], routes: []};
  }
  return {ok: true, status: 200, text: async () => JSON.stringify(data)};
};
w.eval(fs.readFileSync(path.join(root, 'app.js'), 'utf8'));
const tick = () => new Promise(r => setTimeout(r, 5));
async function wait(fn) { for(let i = 0; i < 200; i++) { if(fn()) return; await tick(); } throw Error('UI wait timed out'); }
function select(value) { const el = q('[data-fallback-source]'); el.value = value; el.dispatchEvent(new w.Event('change')); }
async function save() { q('#btn-save-settings').click(); await wait(() => !q('#btn-save-settings').disabled && !q('#btn-fallback-refresh').disabled); await tick(); }
async function refresh() { q('#btn-fallback-refresh').click(); await wait(() => !q('#btn-fallback-refresh').disabled); }
let count = 0;
function check(label, fn) { fn(); count++; process.stdout.write('PASS ' + label + '\n'); }
try {
  await wait(() => q('[data-fallback-source]'));
  check('disabled by default with explicit path selector', () => {
    assert.equal(q('#f-plus-fallback-enabled').checked, false);
    assert.equal(q('[data-fallback-source]').value, '');
    assert.ok(q('#plus-fallback-card').textContent.includes('当前 Key 需同时授权'));
  });
  q('#f-plus-fallback-enabled').click();
  let before = requests.filter(r => r.method === 'PUT').length;
  q('#btn-save-settings').click(); await tick();
  check('enabled empty mapping is rejected before writing', () => {
    assert.equal(requests.filter(r => r.method === 'PUT').length, before);
    assert.ok(q('#toast').textContent.includes('至少选择一条'));
  });
  select(target);
  check('unsaved path is visibly marked', () => assert.equal(q('[data-fallback-badge]').textContent, '待保存'));
  await save();
  check('save sends exact source-target mapping without restart confirmation', () => {
    const write = requests.filter(r => r.method === 'PUT').at(-1);
    assert.deepEqual(write.body.plus_fallback, {enabled: true, check_interval: 30, models: {[source]: target}});
    assert.ok(q('#dlg-confirm').classList.contains('hidden'));
  });
  snapshot = {state:'fallback', reason:'全部启用的 Plus 账号已确认限额', total:3, available:0, exhausted:3, unknown:0, checked_at:Date.now()/1000};
  await refresh();
  check('all exhausted visibly selects provider path', () => {
    assert.equal(q('[data-fallback-badge]').textContent, '代理商备用');
    assert.equal(q('[data-fallback-actual]').textContent, target);
  });
  snapshot = {...snapshot, state:'primary', reason:'Plus 仍有可用额度', available:1, exhausted:2};
  await refresh();
  check('recovered quota visibly returns to original plus path', () => {
    assert.equal(q('[data-fallback-badge]').textContent, 'Plus 优先');
    assert.equal(q('[data-fallback-actual]').textContent, source);
    assert.ok(q('[data-fallback-restore]').textContent.includes('恢复'));
  });
  snapshot = {...snapshot, state:'unknown', reason:'部分账号额度未知，继续使用 Plus', unknown:1};
  await refresh();
  check('unknown quota does not display paid fallback', () => {
    assert.equal(q('[data-fallback-badge]').textContent, '额度待确认');
    assert.equal(q('[data-fallback-actual]').textContent, source);
  });
  q('#f-plus-fallback-interval').value = 1;
  before = requests.filter(r => r.method === 'PUT').length;
  q('#btn-save-settings').click(); await tick();
  check('invalid polling interval blocks save', () => assert.equal(requests.filter(r => r.method === 'PUT').length, before));
  q('#f-plus-fallback-interval').value = 30;
  q('#f-plus-fallback-enabled').click();
  select('');
  await save();
  check('clearing route persists an empty mapping', () => assert.deepEqual(requests.filter(r => r.method === 'PUT').at(-1).body.plus_fallback.models, {}));
  check('no uncaught script errors', () => assert.deepEqual(errors, []));
  process.stdout.write(count + ' UI checks passed\n');
} finally { w.close(); }
