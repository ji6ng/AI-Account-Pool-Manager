export function fixtures() {
  const settings = { ui_port: 19326, pool_port: 19327, proxy_url: '', admin_password: '', autostart: false, excluded_models: [], pools: {
    plus: { label: 'Plus', models: [{ alias: 'plus-gpt-6.1-sol', name: 'gpt-6.1-sol', display: 'Plus · GPT 6.1 Sol' }] },
    pro: { label: 'Pro', models: [{ alias: 'pro-gpt-6.1-sol', name: 'gpt-6.1-sol', display: 'Pro · GPT 6.1 Sol' }] },
    gemini: { label: 'Gemini', models: [{ alias: 'gemini-flash', name: 'gemini-flash', display: 'Gemini · Flash' }] },
  } };
  const accounts = [
    { file: 'codex-plus-demo.json', email: 'alex@example.com', provider: 'codex', pool: 'plus', pool_label: 'Plus', plan: 'Plus', disabled: false, sub: {} },
    { file: 'codex-pro-demo.json', email: 'studio@example.com', provider: 'codex', pool: 'pro', pool_label: 'Pro', plan: 'Pro', disabled: false, sub: {} },
    { file: 'antigravity-demo.json', email: 'design@example.com', provider: 'gemini', pool: 'gemini', pool_label: 'Gemini', plan: 'Google AI Pro', disabled: false, sub: {} },
  ];
  const providers = [
    { id: 'openai-relay', label: 'OpenAI Relay', prefix: 'relay', base_url: 'https://api.example.com/v1', api_key: 'DEMO-NOT-A-REAL-KEY', enabled: true, kind: 'codex', models: [{ alias: 'gpt-6.1-sol', name: 'gpt-6.1-sol' }, { alias: 'gpt-6-astra', name: 'gpt-6-astra' }] },
    { id: 'claude-relay', label: 'Claude Relay', prefix: 'claude', base_url: 'https://relay.example.com', api_key: 'DEMO-NOT-A-REAL-KEY', enabled: true, kind: 'claude', models: [{ alias: 'sonnet', name: 'claude-sonnet-5-5' }] },
  ];
  const keys = [
    { id: 1, name: '默认客户端', secret: 'qk-DEMO-NOT-A-REAL-KEY', model_list: ['*'], enabled: true },
    { id: 2, name: '开发工作站', secret: 'qk-DEMO-RESTRICTED', model_list: ['temporarily-unavailable'], enabled: true },
  ];
  const days = Array.from({ length: 30 }, (_, i) => {
    const date = new Date(Date.UTC(2026, 8, 30 + i));
    const ok = [21, 39, 31, 13, 48, 62, 45, 38, 56, 72, 49, 31, 68, 44, 79, 92, 68, 82, 60, 75, 104, 86, 65, 91, 107, 82, 95, 109, 85, 118][i];
    const fail = i % 4 === 0 ? 2 : i % 9 === 0 ? 1 : 0;
    return { date: date.toISOString().slice(0, 10), label: date.toISOString().slice(5, 10), ok, fail, total: ok + fail };
  });
  const ok = days.reduce((n, d) => n + d.ok, 0), fail = days.reduce((n, d) => n + d.fail, 0);
  const overview = { version: '1.2.6', pool: { running: true, reachable: true, pid: 18420, port: 19327, uptime: 21845, models: 7 }, accounts: { active: 3, total: 3, by_pool: { Plus: 1, Pro: 1, Gemini: 1 } }, keys: 2, today: days.at(-1).total, exposed: 7, ports: { ui: 19326, pool: 19327 }, lan_ips: [], lan_enabled: false, stats: { days, ok, fail, total: ok + fail, rate: ok / (ok + fail) * 100, today: days.at(-1), accounts: [] } };
  const models = Object.values(settings.pools).flatMap(p => p.models.map(m => m.alias));
  const usage = Object.fromEntries(accounts.map((a, i) => [a.file, { ok: true, provider: a.provider, primary: { label: '5 小时', left: 86 - i * 16, reset_after: 6400, used: 14 + i * 16 }, secondary: { label: '每周', left: 64 - i * 11, reset_after: 188000, used: 36 + i * 11 }, reset: { available: 0 } }]));
  const targets = { accounts: [...accounts, { ...accounts[0], file: 'disabled.json', email: 'disabled@example.com', disabled: true }], models: models.map(alias => ({ alias, key: 'p:' + alias, upstream: alias, in_bank: true })), provider_models: providers.flatMap(p => p.models.map(m => ({ key: 'r:' + p.id + '|' + m.name, upstream: m.name, exposed: p.prefix + '/' + m.alias, provider_label: p.label, ready: true, enabled: true, in_bank: false }))).concat([{ key: 'r:disabled|test', upstream: 'unavailable', exposed: 'disabled/test', provider_label: '未配置', ready: false }]) };
  const activationTasks = { tasks: [], accounts: accounts.filter(a => ['plus', 'pro'].includes(a.pool) && !a.disabled) };
  return { settings, accounts, providers, keys, overview, models, usage, targets, activationTasks };
}
