import { JSDOM, VirtualConsole } from 'jsdom';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { fixtures } from './fixtures.mjs';

const root = new URL('../../web/', import.meta.url);
const state = fixtures();
const dom = new JSDOM(fs.readFileSync(new URL('index.html', root), 'utf8'), {
  url: 'http://127.0.0.1:19326/', runScripts: 'outside-only', pretendToBeVisual: true,
  virtualConsole: new VirtualConsole(),
});
const window = dom.window;
const warning = '配置里有其它工具设置的 model_catalog_json，会覆盖号池的模型目录，Codex 会缺少系统提示。点「写入并切换」会将其注释停用。';
const client = { id: 'codex', label: 'Codex CLI', path: '/temporary/.codex/config.toml',
  installed: true, key_known: true, key_id: state.keys[0].id, status: 'conflict', warning,
  model: state.models[0], base_url: 'http://127.0.0.1:19326/v1' };
let applied = false;
window.fetch = async (url) => {
  const request = String(url).split('?')[0];
  const responses = {
    '/api/whoami': { allowed: true, local: true },
    '/api/settings': state.settings,
    '/api/overview': state.overview,
    '/api/accounts': { accounts: state.accounts },
    '/api/usage': { usage: state.usage },
    '/api/models': { models: state.models, exposed: state.models, pools: state.settings.pools },
    '/api/keys': { keys: state.keys },
    '/api/clients': { base: 'http://127.0.0.1:19326', clients: [client], keys: state.keys },
  };
  let data = responses[request] || { ok: true };
  if (request === '/api/clients/codex/apply') {
    applied = true;
    data = { ok: true, disabled: ['model_catalog_json'], backup: '/temporary/backup.toml',
      clients: [{ ...client, status: 'ok', warning: undefined }] };
  }
  return { status: 200, ok: true, text: async () => JSON.stringify(data) };
};
async function waitFor(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('UI did not settle');
}
try {
  window.eval(fs.readFileSync(new URL('app.js', root), 'utf8'));
  await waitFor(() => window.document.querySelector('#s-accounts').textContent.includes('3'));
  window.document.querySelector('[data-page="clients"]').click();
  await waitFor(() => window.document.querySelector('[data-cl-apply="codex"]'));
  const card = window.document.querySelector('.client-card');
  assert.ok(card.textContent.includes('模型目录冲突'));
  assert.ok(card.textContent.includes(warning));
  window.document.querySelector('[data-cl-apply="codex"]').click();
  await waitFor(() => applied && window.document.querySelector('#toast').textContent.includes('原值已注释保留'));
  assert.ok(window.document.querySelector('#toast').textContent.includes('已停用其它工具设置的 model_catalog_json'));
  assert.ok(window.document.querySelector('.client-card').textContent.includes('已接入'));
  assert.ok(!window.document.querySelector('.client-card').textContent.includes(warning));
  console.log('PASS Codex catalog conflict card, apply toast, and resolved status');
} finally {
  window.close();
}
