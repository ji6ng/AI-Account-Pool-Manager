import { JSDOM, VirtualConsole } from 'jsdom';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { fixtures } from './fixtures.mjs';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(testDir, '../../web');
const resultsDir = path.join(testDir, '.results'); fs.mkdirSync(resultsDir, { recursive: true });
const state = fixtures();
fs.writeFileSync(path.join(resultsDir, 'preview-fixture.json'), JSON.stringify(state));
const requests = [], errors = [], failNext = new Map();
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', e => errors.push(e.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), { url: 'http://127.0.0.1:19326/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole });
const w = dom.window, d = w.document;
w.addEventListener('error', e => errors.push(e.message));
w.confirm = () => { throw Error('Native confirm must not be used'); };
let copied = '';
d.execCommand = command => { if (command === 'copy') { copied = d.activeElement.value; return true; } return false; };
w.fetch = async (url, options = {}) => {
  const request = { path: String(url).split('?')[0], method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null };
  requests.push(request);
  if (failNext.has(request.path)) {
    const result = failNext.get(request.path); failNext.delete(request.path);
    return { status: 200, ok: true, text: async () => JSON.stringify(result) };
  }
  let data = { ok: true };
  const p = request.path, body = request.body;
  if (p === '/api/whoami') data = { allowed: true, local: true };
  else if (p === '/api/overview') data = state.overview;
  else if (p === '/api/settings') { if (body) Object.assign(state.settings, body); data = request.method === 'GET' ? state.settings : { ok: true, restart_ui: !!body?.ui_port }; }
  else if (p === '/api/accounts') data = { accounts: state.accounts };
  else if (p === '/api/usage') data = { usage: state.usage };
  else if (p === '/api/runtime/accounts') data = { accounts: state.accounts.map(a => ({ file: a.file, status: 'active', recent_total: 12 })) };
  else if (p === '/api/providers') data = { providers: state.providers };
  else if (p === '/api/keys' && request.method === 'GET') data = { keys: state.keys };
  else if (p === '/api/keys' && request.method === 'POST') { state.keys.push({ id: 3, name: body.name, secret: 'qk-DEMO-NEW', model_list: body.models.split(','), enabled: true }); }
  else if (p === '/api/models') data = { models: state.models, exposed: state.models, pools: state.settings.pools };
  else if (p === '/api/logs') data = { logs: [], pool: [] };
  else if (p === '/api/modeltrace/bank') data = { ok: true, model_count: 40, built_at: '2026-10-29' };
  else if (p === '/api/modeltrace/targets') data = state.targets;
  return { status: 200, ok: true, text: async () => JSON.stringify(data) };
};
w.eval(fs.readFileSync(path.join(root, 'app.js'), 'utf8'));
const q = s => d.querySelector(s);
const qa = s => [...d.querySelectorAll(s)];
async function waitFor(fn, label) {
  for (let i = 0; i < 100; i++) { if (fn()) return; await new Promise(r => setTimeout(r, 5)); }
  throw Error('Timed out: ' + label);
}
async function nav(page) { q(`.nav-item[data-page="${page}"]`).click(); await waitFor(() => !q('#page-' + page).classList.contains('hidden'), 'navigate ' + page); await new Promise(r => setImmediate(r)); }
const results = [];
async function test(name, fn) { try { await fn(); results.push({ name, ok: true }); console.log('PASS', name); } catch (error) { results.push({ name, ok: false, error: error.message }); console.log('FAIL', name, error.message); } }
try {
  await waitFor(() => q('#s-accounts').textContent.includes('3'), 'initial data');
  await test('all eight pages have unique IDs and usable navigation', () => { const ids = qa('[id]').map(e => e.id); assert.equal(new Set(ids).size, ids.length); assert.equal(qa('.page').length, 8); assert.equal(qa('.nav-item[href]').length, 8); assert.equal(q('#page-title').textContent, '控制台'); });
  await test('activity chart shows actual daily counts', () => { assert.equal(qa('.chart-column').length, 30); assert.ok(q('.chart-column').getAttribute('aria-label').includes('成功 21')); assert.equal(q('#s-today').textContent, '118'); });
  await test('secrets are masked and can be revealed deliberately', () => { assert.equal(q('#c-key').type, 'password'); q('#btn-reveal-key').click(); assert.equal(q('#c-key').type, 'text'); q('#btn-reveal-key').click(); assert.equal(q('#c-key').type, 'password'); });
  await test('copy uses the actual field value', () => { q('[data-copy="#c-base"]').click(); assert.equal(copied, 'http://127.0.0.1:19326/v1'); });
  await test('theme switch updates appearance and accessible name', () => { q('#btn-theme').click(); assert.equal(d.documentElement.dataset.theme, 'dark'); assert.equal(q('#btn-theme').getAttribute('aria-label'), '切换浅色模式'); q('#btn-theme').click(); assert.equal(d.documentElement.dataset.theme, 'light'); });
  await test('mobile navigation opens and closes after selecting a page', async () => { q('#btn-menu').click(); assert.equal(q('#btn-menu').getAttribute('aria-expanded'), 'true'); await nav('accounts'); assert.equal(q('#btn-menu').getAttribute('aria-expanded'), 'false'); });
  await waitFor(() => qa('#tb-accounts [data-toggle]').length === 3, 'accounts');
  await test('account type filter and search work together', () => { q('[data-account-filter="gemini"]').click(); assert.equal(qa('#tb-accounts [data-toggle]').length, 1); q('#account-search').value = 'missing'; q('#account-search').dispatchEvent(new w.Event('input')); assert.ok(q('#tb-accounts').textContent.includes('没有匹配')); q('#account-search').value = ''; q('#account-search').dispatchEvent(new w.Event('input')); q('[data-account-filter="all"]').click(); assert.equal(qa('#tb-accounts [data-toggle]').length, 3); });
  await test('business failure rolls back the account switch', async () => { const toggle = q('[data-toggle]'), previous = toggle.checked; failNext.set('/api/accounts/' + encodeURIComponent(toggle.dataset.toggle) + '/disabled', { ok: false, error: '模拟操作失败' }); toggle.click(); await waitFor(() => !toggle.disabled, 'failed switch'); assert.equal(toggle.checked, previous); assert.ok(q('#toast').textContent.includes('模拟操作失败')); });
  await test('failed usage refresh never shows success', async () => { failNext.set('/api/usage', { ok: false, error: '模拟额度失败' }); q('#btn-refresh-usage').click(); await waitFor(() => !q('#btn-refresh-usage').disabled, 'usage'); assert.ok(q('#toast').classList.contains('err')); assert.ok(q('#toast').textContent.includes('模拟额度失败')); });
  await test('providers are independent and searchable', async () => { await nav('providers'); await waitFor(() => qa('[data-pedit]').length === 2, 'providers'); q('#provider-search').value = 'claude'; q('#provider-search').dispatchEvent(new w.Event('input')); assert.equal(qa('[data-pedit]').length, 1); assert.ok(q('#tb-providers').textContent.includes('Anthropic Messages')); q('#provider-search').value = ''; q('#provider-search').dispatchEvent(new w.Event('input')); });
  await test('provider editor preserves protocol and supports removing models', () => { q('[data-pedit="claude-relay"]').click(); assert.equal(q('#p-kind').value, 'claude'); assert.equal(q('#p-key').type, 'password'); q('[data-remove-pmodel]').click(); assert.equal(qa('[data-pmrow]').length, 0); q('#btn-p-cancel').click(); });
  await test('browser back restores the previous page', async () => { w.history.back(); await waitFor(() => !q('#page-accounts').classList.contains('hidden'), 'history back'); });
  await test('unknown URL fragment falls back safely', async () => { w.location.hash = '#not-a-real-page'; await waitFor(() => !q('#page-overview').classList.contains('hidden'), 'fallback'); assert.equal(q('#page-title').textContent, '控制台'); });
  await test('empty key permissions cannot silently allow every model', async () => { await nav('keys'); await waitFor(() => qa('[data-keyedit]').length === 2, 'keys'); q('#btn-add-key').click(); q('#key-all').click(); qa('#key-models input[value]').forEach(e => { e.checked = false; }); const before = requests.length; q('#btn-key-save').click(); await new Promise(r => setImmediate(r)); assert.ok(!q('#dlg-key').classList.contains('hidden')); assert.ok(q('#toast').textContent.includes('至少选择一个模型')); assert.equal(requests.length, before); q('#btn-key-cancel').click(); });
  await test('editing a key preserves a temporarily unavailable model', () => { q('[data-keyedit="2"]').click(); const missing = q('#key-models input[value="temporarily-unavailable"]'); assert.ok(missing && missing.checked); assert.equal(q('#key-all').checked, false); q('#btn-key-cancel').click(); });
  await test('model drafts require an explicit decision before discarding', async () => { await nav('models'); await waitFor(() => !!q('[data-m="alias"]'), 'model editor'); const input = q('[data-m="alias"]'); input.value = 'edited-model'; input.dispatchEvent(new w.Event('input', { bubbles: true })); q('.nav-item[data-page="logs"]').click(); await waitFor(() => !q('#dlg-confirm').classList.contains('hidden'), 'discard confirm'); q('#btn-confirm-cancel').click(); assert.ok(!q('#page-models').classList.contains('hidden')); assert.equal(input.value, 'edited-model'); q('.nav-item[data-page="logs"]').click(); await waitFor(() => !q('#dlg-confirm').classList.contains('hidden'), 'discard again'); q('#btn-confirm-ok').click(); await waitFor(() => !q('#page-logs').classList.contains('hidden'), 'discard accepted'); });
  await test('clearing logs is cancellable and sends no deletion on cancel', async () => { const before = requests.filter(r => r.method === 'DELETE').length; q('#btn-logs-clear').click(); await waitFor(() => !q('#dlg-confirm').classList.contains('hidden'), 'clear logs confirm'); q('#btn-confirm-cancel').click(); assert.equal(requests.filter(r => r.method === 'DELETE').length, before); });
  await test('invalid ports are blocked before any settings write', async () => { await nav('settings'); await waitFor(() => q('#f-ui-port').value !== '', 'settings'); const before = requests.filter(r => r.method === 'PUT').length; q('#f-ui-port').value = '0'; q('#btn-save-settings').click(); await new Promise(r => setImmediate(r)); assert.ok(q('#toast').textContent.includes('端口')); assert.equal(requests.filter(r => r.method === 'PUT').length, before); q('#f-ui-port').value = '19326'; });
  await test('select all skips unavailable test targets', async () => { await nav('modeltest'); await waitFor(() => !!q('#mt-providers input:disabled'), 'targets'); q('[data-all="mt-providers"]').click(); assert.equal(q('#mt-providers input:disabled').checked, false); assert.ok(qa('#mt-providers input:not(:disabled)').every(x => x.checked)); });
  await test('failed model test start restores both buttons', async () => { failNext.set('/api/modeltrace/run', { ok: false, error: '模拟测试启动失败' }); q('#btn-mt-run').click(); await waitFor(() => !q('#btn-mt-run').disabled, 'test failure'); assert.equal(q('#btn-mt-cancel').disabled, true); assert.ok(q('#toast').textContent.includes('模拟测试启动失败')); });
  await test('Escape closes the top dialog and returns focus', async () => { await nav('providers'); q('#btn-add-provider').focus(); q('#btn-add-provider').click(); await waitFor(() => q('.shell').inert, 'modal open'); d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await waitFor(() => q('#dlg-provider').classList.contains('hidden'), 'escape'); assert.equal(d.activeElement.id, 'btn-add-provider'); });
  await test('no uncaught script errors', () => assert.deepEqual(errors, []));
} finally { dom.window.close(); }
fs.writeFileSync(path.join(resultsDir, 'ui-test-results.json'), JSON.stringify(results, null, 2));
console.log(`${results.filter(r => r.ok).length}/${results.length} UI scenarios passed`);
if (results.some(r => !r.ok)) process.exitCode = 1;
