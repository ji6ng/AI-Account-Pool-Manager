/* Reload stale markup before installing the new controls. */
(() => {
if (!document.getElementById('dlg-key-connect') || !document.getElementById('plus-fallback-card')) {
  const target = new URL(location.href);
  target.searchParams.set('ui', '1.2.18'); target.searchParams.set('refresh', String(Date.now()));
  location.replace(target.toString());
  return;
}
/* AI 号池管理器 · 前端逻辑（无框架、无构建步骤） */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const S = {
  page: 'overview', settings: null, models: [], pools: {}, accounts: [], keys: [],
  clientMeta: null, clientDraft: {},
  loginSid: null, loginTimer: null, editingKey: null,
  usage: {}, usageLoading: false, runtime: {}, runtimeErr: '',
  providers: [], editingProvider: null,
  mt: { job: null, timer: null, rows: [], targets: null },
  accountFilter: 'all', modelsDirty: false, settingsDirty: false, sideTimer: null,
};

const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/><circle cx="9" cy="7" r="4"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
  box: '<path d="m12 3 9 5v9l-9 5-9-5V8l9-5Zm0 9v10M3 8l9 4 9-4M7.5 5.5l9 5"/>',
  pulse: '<path d="M2 12h5l3-9 4 18 3-9h5"/>',
  key: '<circle cx="8" cy="15" r="5"/><path d="m11.5 11.5 9-9M16 7l3 3m-1-5 3 3"/>',
  list: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  settings: '<path d="m9 3-.5 3-2 1.2L3.7 6l-2 3.5L4 11v2l-2.3 1.5 2 3.5 2.8-1.2 2 1.2.5 3h4l.5-3 2-1.2 2.8 1.2 2-3.5L18 13v-2l2.3-1.5-2-3.5-2.8 1.2-2-1.2L13 3H9Z"/><circle cx="11" cy="12" r="3"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3m-3 11h3"/>',
  link: '<path d="m10 13 4-4m-5 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5m10-3a7.5 7.5 0 0 0-13-4L4 7m16 10-2 2A7.5 7.5 0 0 1 5 15"/>',
  moon: '<path d="M21 13a9 9 0 0 1-10-10 9 9 0 1 0 10 10Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  alert: '<path d="m10.3 3.8-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.2l-8-14a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/>',
};
function paintIcons(root = document) {
  [...(root.matches?.('[data-icon]') ? [root] : []), ...$$('[data-icon]', root)].forEach(el => {
    const path = ICONS[el.dataset.icon];
    if (path) el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
  });
}

let confirmation = null;
function confirmAction(message, title = '确认操作', verb = '确认') {
  if (confirmation) return Promise.resolve(false);
  $('#confirm-message').textContent = message;
  $('#confirm-title').textContent = title;
  $('#btn-confirm-ok').textContent = verb;
  $('#dlg-confirm').classList.remove('hidden');
  return new Promise(resolve => { confirmation = resolve; });
}
function settleConfirmation(value) {
  $('#dlg-confirm').classList.add('hidden');
  const resolve = confirmation; confirmation = null;
  if (resolve) resolve(value);
}
$('#btn-confirm-ok').onclick = () => settleConfirmation(true);
$('#btn-confirm-cancel').onclick = () => settleConfirmation(false);

async function withBusy(btn, action) {
  if (btn.disabled) return;
  btn.disabled = true; btn.classList.add('busy');
  try { return await action(); }
  catch (e) { toast(e.message, 'err', 5000); }
  finally { btn.disabled = false; btn.classList.remove('busy'); }
}

/* ------------------------------------------------------------ 基础 */
async function api(path, opt = {}) {
  const res = await fetch(path, {
    signal: AbortSignal.timeout(90000),
    ...opt,
    headers: { 'Content-Type': 'application/json', ...opt.headers },
    body: opt.body ? JSON.stringify(opt.body) : undefined,
  });
  if (res.status === 401) { $('#login-mask').classList.remove('hidden'); throw new Error('未登录'); }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
  if (!res.ok || (data && data.ok === false)) {
    const detail = data && (data.detail || data.error || data.message);
    throw new Error(typeof detail === 'string' ? detail : detail?.message || `请求失败（HTTP ${res.status}）`);
  }
  return data;
}

let toastTimer = null;
function toast(msg, type = '', ms = 2600) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), ms);
}

function fmtUptime(s) {
  if (!s) return '—';
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  if (h) return `${h} 小时 ${m} 分`;
  if (m) return `${m} 分 ${s % 60} 秒`;
  return `${s} 秒`;
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function copy(text) {
  const done = ok => toast(ok ? '已复制' : '复制失败，请手动选中后按 Ctrl+C', ok ? 'ok' : 'err');
  // 备用通道：用局域网 IP 打开界面时是 http + 非 localhost，属于非安全上下文，
  // navigator.clipboard 整个不存在；老办法 execCommand 仍然可用。
  const legacy = () => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    done(ok);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done(true), legacy);
  } else {
    legacy();
  }
}

/* ------------------------------------------------------------ 导航 */
const TITLES = {
  overview: ['控制台', '查看服务状态、调用趋势与客户端接入信息。'],
  accounts: ['账号管理', '管理订阅账号，查看可用额度与调用状态。'],
  providers: ['提供商', '连接中转站与自建网关，统一管理第三方模型。'],
  models: ['模型配置', '为每个账号池设置模型别名与显示名称。'],
  modeltest: ['模型测试', '按账号验证模型是否名副其实'],
  keys: ['客户端密钥', '为不同客户端分配密钥与模型权限。'],
  clients: ['本机客户端', '把 Claude Code、Codex、Gemini CLI 直接接到本机网关，坏了能一键修复。'],
  logs: ['请求日志', '追踪请求结果、响应耗时与服务运行记录。'],
  settings: ['系统设置', '配置网络连接、访问口令与启动选项。'],
};

// 老链接 #providers 已经并进账号页了
const PAGE_ALIAS = {};
const EYEBROWS = { overview: 'OVERVIEW', accounts: 'ACCOUNTS', providers: 'PROVIDERS', models: 'MODELS', modeltest: 'MODEL VERIFICATION', keys: 'API KEYS', clients: 'LOCAL CLIENTS', logs: 'REQUEST LOGS', settings: 'PREFERENCES' };

async function switchPage(name, { fromHistory = false, replace = false } = {}) {
  name = PAGE_ALIAS[name] || name;
  if (!TITLES[name]) name = 'overview';
  if ((S.page === 'models' && S.modelsDirty) || (S.page === 'settings' && S.settingsDirty)) {
    if (!await confirmAction('当前修改尚未保存，离开或刷新会丢失这些修改。', '放弃未保存的修改？', '放弃修改')) {
      if (fromHistory) history.pushState(null, '', '#' + S.page);
      return;
    }
    S.modelsDirty = false; S.settingsDirty = false;
  }
  S.page = name;
  syncQuotaCountdownTimer();
  try { if (replace) history.replaceState(null, '', '#' + name); else if (!fromHistory && location.hash !== '#' + name) history.pushState(null, '', '#' + name); } catch (e) { }
  $$('.nav-item').forEach(a => { a.classList.toggle('active', a.dataset.page === name); if (a.dataset.page === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  $$('.page').forEach(p => p.classList.add('hidden'));
  $('#page-' + name).classList.remove('hidden');
  const [t, sub] = TITLES[name] || ['', ''];
  $('#page-title').textContent = t;
  $('#page-sub').textContent = sub;
  $('#breadcrumb-page').textContent = t;
  $('#page-eyebrow').textContent = EYEBROWS[name];
  closeSidebar();
  const loaders = { overview: loadOverview, accounts: () => Promise.all([loadAccounts(), loadRuntime()]), providers: loadProviders, models: loadModels, modeltest: loadModelTest, keys: loadKeys, clients: loadClients, logs: loadLogs, settings: loadSettings };
  try { await loaders[name](); if (name !== 'overview') await refreshSide(); }
  catch (e) { toast(e.message, 'err', 5000); }
}

// 侧边栏点击：这里必须显式绑定，<a> 没 href 是不会自己跳的
$$('.nav-item').forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    switchPage(a.dataset.page);
  });
});

// 浏览器前进/后退
window.addEventListener('hashchange', () => {
  const h = (location.hash || '').replace('#', '');
    if (h !== S.page) switchPage(h, { fromHistory: true });
});

/* ------------------------------------------------------------ 概览 */
function paintSide(o) {
  const running = o.pool.running && o.pool.reachable;
  const dot = $('#side-status');
  dot.className = 'stat-dot ' + (running ? 'on' : 'off');
  dot.querySelector('span').textContent = running ? '号池运行中' : '号池未就绪';
  $('#side-port').textContent = `界面 ${o.ports.ui} · 号池 ${o.pool.port}`;
  $('#side-version').textContent = 'v' + o.version;
  $('#nav-accounts').textContent = o.accounts.total;
  $('#connection-state').className = 'connection-pill on';
  $('#connection-state span').textContent = '已连接';
  $('#last-updated').textContent = '更新于 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

async function refreshSide() {
  try {
    const o = await api('/api/overview');
    paintSide(o);
    paintBaseUrls(o);                                  // 两个页面的接入地址一起刷新
    if (S.page === 'overview') paintOverview(o);
    if (S.page === 'settings' && S.fallbackMeta && !$('#btn-fallback-refresh').disabled
        && Date.now() - (S.fallbackStatusAt || 0) >= 15000) await loadPlusFallback(false);
    return o;
  } catch (e) {
    $('#connection-state').className = 'connection-pill off';
    $('#connection-state span').textContent = '连接中断';
    $('#side-status').className = 'stat-dot off';
    $('#side-status span').textContent = '网关连接中断';
    $('#last-updated').textContent = '同步失败';
    if (!String(e.message).includes('未登录')) toast('连接失败，请确认服务正在运行', 'err');
    return null;
  }
}

function paintStats(st) {
  if (!st) return;
  $('#st-ok').textContent = st.ok;
  $('#st-fail').textContent = st.fail;

  const list = st.days || [];
  const last = list.length ? list[list.length - 1].date : '';
  const peak = Math.max(1, ...list.map(d => d.total));
  $('#activity-chart').innerHTML = list.map(d => {
    const tip = `${d.date} · 成功 ${d.ok} · 失败 ${d.fail}`;
    return `<div class="chart-column" tabindex="0" aria-label="${esc(tip)}" data-tip="${esc(tip)}"><div class="chart-stack" style="height:${Math.max(2, d.total / peak * 100)}%">${d.fail ? `<span class="chart-fail" style="flex:${d.fail}"></span>` : ''}${d.ok ? `<span class="chart-ok" style="flex:${d.ok}"></span>` : ''}${!d.total ? '<span class="chart-idle"></span>' : ''}</div></div>`;
  }).join('');
  $('#chart-start').textContent = list[0]?.label || '暂无请求';
  $('#chart-end').textContent = list.at(-1)?.label ? list.at(-1).label + ' · 今天' : '今天';
  paintAccHeat(st, last);
  $('#st-heat').innerHTML = list.map(d => {
    const cls = !d.total ? 'empty' : (d.fail === 0 ? 'ok' : (d.ok === 0 ? 'bad' : 'mix'));
    const tip = d.total ? `${d.date} · 成功 ${d.ok} · 失败 ${d.fail}` : `${d.date} · 没有请求`;
    return `<i class="${cls}${d.date === last ? ' today' : ''}" title="${esc(tip)}"></i>`;
  }).join('');

  const t = st.today || {};
  const foot = $('#s-today-foot');
  if (foot) foot.textContent = t.total ? `成功 ${t.ok} · 失败 ${t.fail}` : '今天还没有请求';

  const tot = $('#st-total');
  if (tot) tot.innerHTML = st.total ? `${st.total}<span class="muted tiny"> 次</span>` : '—';

  const rate = $('#st-rate');
  if (!st.total) {
    rate.textContent = '—';
    rate.className = '';
    rate.title = '';
    return;
  }
  rate.textContent = `${Math.round(st.rate)}%`;
  rate.className = st.rate < 80 ? 'err' : (st.rate < 95 ? 't-warn' : 'ok');
  rate.title = `成功 ${st.ok} / 共 ${st.total} 次`;

  paintAccHeat(st, last);
}

// 每个账号一条自己的方块：哪一天跑了多少、有没有失败
function paintAccHeat(st, last) {
  const box = $('#acc-heat');
  if (!box) return;
  const accs = (st.accounts || []).filter(a => a.total > 0);
  if (!accs.length) {
    box.innerHTML = '';
    return;
  }
  const dates = (st.days || []).map(d => d.date);
  box.innerHTML = `<div class="acc-head muted tiny">各账号（同一段时间）</div>` +
    accs.map(a => {
      const cells = (a.series || []).map((v, i) => {
        const [o, f] = v;
        const cls = (!o && !f) ? 'empty' : (!f ? 'ok' : (!o ? 'bad' : 'mix'));
        const tip = `${dates[i] || ''} · 成功 ${o} · 失败 ${f}`;
        return `<i class="${cls}${dates[i] === last ? ' today' : ''}" title="${esc(tip)}"></i>`;
      }).join('');
      const hint = a.tip ? ` title="${esc(a.tip)}"` : ` title="${esc(a.name)}"`;
      return `<div class="acc-row">
        <div class="acc-name"${hint}>${esc(a.name)}</div>
        <div class="heat mini">${cells}</div>
        <div class="acc-num">${a.total}<span class="muted tiny"> 次</span></div>
      </div>`;
    }).join('');
}

/* 「客户端接入」和「客户端密钥」页都要显示 Base URL。
   第一条来自浏览器地址栏——在本机用 127.0.0.1 打开时就只有回环地址，
   手机 / 别的电脑照着填连不上；所以后端额外报一份内网 IP，这里补第二行。 */
function paintBaseUrls(o) {
  if (!o || !o.ports) return;
  const host = location.hostname || '127.0.0.1';
  const local = `${location.origin}/v1`;
  const lan = (o.lan_ips || []).filter(ip => ip && ip !== host);
  const u = {
    local,
    lan,
    lanUrl: lan.length ? `http://${lan[0]}:${location.port || o.ports.ui}/v1` : '',
    on: !!o.lan_enabled,
  };

  const a = $('#c-base'); if (a) a.value = u.local;
  paintLanRow('#c-base-lan', '#f-base-lan', '#c-base-lan-tip', u);

  const b = $('#k-base-local'); if (b) b.value = u.local;
  paintLanRow('#k-base-lan', '#f-k-base-lan', '#k-base-lan-tip', u);


}

function paintLanRow(boxSel, fieldSel, tipSel, u) {
  const box = $(boxSel), field = $(fieldSel), tip = $(tipSel);
  if (!box || !field || !tip) return;
  field.classList.remove('hidden');

  if (!u.on) {
    box.value = '局域网访问已关闭';
    field.querySelector('[data-copy]').disabled = true;
    tip.textContent = '当前只监听本机（127.0.0.1），别的设备连不进来。';
    return;
  }
  if (!u.lanUrl) { field.classList.add('hidden'); return; }

  box.value = u.lanUrl;
  field.querySelector('[data-copy]').disabled = false;
  tip.textContent = u.lan.length > 1
    ? `其他网卡：${u.lan.slice(1).join('、')}（端口相同，按需选用）`
    : '同网段的手机、其他电脑用这条；本机仍用上面那条。';
}

async function loadOverview() {
  const o = await refreshSide();
  if (!o) return;
}

function paintOverview(o) {
  const running = o.pool.running && o.pool.reachable;
  $('#s-pool').textContent = running ? '运行中' : (o.pool.running ? '启动中' : '已停止');
  $('#s-pool-foot').textContent = o.pool.running ? `PID ${o.pool.pid} · 端口 ${o.pool.port}` : (o.pool.error || '点右侧「启动」');
  $('#s-accounts').textContent = `${o.accounts.active} / ${o.accounts.total}`;
  $('#s-accounts-foot').textContent = Object.entries(o.accounts.by_pool).map(([k, v]) => `${k} ${v}`).join(' · ') || '还没有账号';
  $('#s-keys').textContent = o.keys;
  $('#s-today').textContent = o.today;

  $('#k-port').textContent = o.pool.port;
  $('#k-pid').textContent = o.pool.pid || '—';
  $('#k-uptime').textContent = fmtUptime(o.pool.uptime);
  $('#k-models').textContent = o.exposed ? o.exposed + ' 个' : '—';
  $('#k-version').textContent = o.version || '—';
  paintStats(o.stats);
  paintBaseUrls(o);
  const first = (S.keys || []).find(k => k.enabled) || null;
  $('#c-key').value = first ? first.secret : '（还没有 Key，去「客户端密钥」页新建）';
  $('#c-key').type = first ? 'password' : 'text';
  $('#btn-reveal-key').setAttribute('aria-label', '显示密钥');
}

$('#btn-pool-start').onclick = async () => {
  toast('正在启动号池…');
  try { const r = await api('/api/pool/start', { method: 'POST' }); r.ok ? toast('已启动', 'ok') : toast(r.error || '启动失败', 'err'); }
  catch (e) { toast(e.message, 'err'); }
  setTimeout(loadOverview, 1200);
};
$('#btn-pool-stop').onclick = async () => {
  if (!await confirmAction('停止后客户端会立即无法使用，确定停止？', '停止号池服务', '停止服务')) return;
  try { await api('/api/pool/stop', { method: 'POST' }); toast('已停止', 'ok'); } catch (e) { toast(e.message, 'err'); }
  setTimeout(loadOverview, 800);
};
$('#btn-pool-restart').onclick = async () => {
  if (!await confirmAction('重启会中断正在生成的回答，服务约需 3–5 秒恢复。', '重启号池服务', '重启服务')) return;
  toast('正在重启…');
  try { const r = await api('/api/pool/restart', { method: 'POST' }); r.ok ? toast('已重启', 'ok') : toast(r.error || '重启失败', 'err'); }
  catch (e) { toast(e.message, 'err'); }
  setTimeout(loadOverview, 1500);
};
$('#btn-refresh').onclick = () => withBusy($('#btn-refresh'), () => switchPage(S.page));

/* ------------------------------------------------------------ 账号 */
function fmtDur(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  if (s >= 86400) return `${Math.floor(s / 86400)} 天 ${Math.floor(s % 86400 / 3600)} 小时`;
  if (s >= 3600) return `${Math.floor(s / 3600)} 小时 ${Math.floor(s % 3600 / 60)} 分`;
  return `${Math.max(1, Math.floor(s / 60))} 分钟`;
}

// 最近三小时这个号被用了多少次（来自号池管理接口的 10 分钟分桶）
function liveCell(file) {
  if (S.runtimeErr && !Object.keys(S.runtime || {}).length) {
    return `<span class="muted tiny" title="${esc(S.runtimeErr)}">—</span>`;
  }
  const r = (S.runtime || {})[file];
  if (!r) return '<span class="muted tiny">—</span>';
  if (r.status && r.status !== 'active') {
    const msg = r.status_message || r.status;
    return `<span class="badge warn" title="${esc(msg)}">${esc(r.status)}</span>`;
  }
  const n = r.recent_total || 0;
  if (!n) return '<span class="muted tiny">近 3 小时无调用</span>';
  const fail = r.recent_fail || 0;
  return `<b>${n}</b><span class="muted tiny"> 次</span>` +
    (fail ? `<div class="muted tiny t-warn">失败 ${fail}</div>`
          : '<div class="muted tiny">全部成功</div>');
}

function paintRuntime() {
  if (!S.accounts) return;
  $$('[data-lcell]').forEach(td => { td.innerHTML = liveCell(td.dataset.lcell); });
}

async function loadRuntime() {
  try {
    const d = await api('/api/runtime/accounts');
    S.runtime = {};
    (d.accounts || []).forEach(a => { S.runtime[a.file] = a; });
    S.runtimeErr = d.ok === false ? (d.error || '号池管理接口不可用') : '';
  } catch (e) {
    S.runtime = {};
    S.runtimeErr = e.message;
  }
  paintRuntime();
}

/* Compact quota rows; each window keeps its own reset time. */
function quotaPercent(value) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  return Math.round(Math.max(0, Math.min(100, Number(value))) * 10) / 10;
}
function quotaReset(window, snapshot) {
  let epoch = Number(window.reset_at || 0);
  if (epoch > 1e12) epoch /= 1000;
  if (!epoch && Number(window.reset_after) > 0) epoch = (Number(snapshot.fetched_at) || Date.now() / 1000) + Number(window.reset_after);
  if (!Number.isFinite(epoch) || epoch <= 0) return { text: '未提供时间', title: '上游未返回重置时间', iso: '', epoch: 0 };
  const date = new Date(epoch * 1000);
  const text = date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  const title = date.toLocaleString('zh-CN', { hour12: false });
  return { text: epoch <= Date.now() / 1000 ? '待刷新' : text, title, iso: date.toISOString(), epoch };
}
let quotaCountdownTimer = null;
function quotaCountdownText(epoch) {
  if (!Number.isFinite(Number(epoch)) || Number(epoch) <= 0) return '未提供时间';
  const remaining = Math.ceil(Number(epoch) - Date.now() / 1000);
  if (remaining <= 0) return '待刷新';
  const days = Math.floor(remaining / 86400), hours = Math.floor(remaining % 86400 / 3600);
  const minutes = Math.floor(remaining % 3600 / 60), seconds = remaining % 60;
  if (days) return `${days}天 ${hours}小时 ${minutes}分`;
  if (hours) return `${hours}小时 ${minutes}分 ${seconds}秒`;
  if (minutes) return `${minutes}分 ${seconds}秒`;
  return `${seconds}秒`;
}
function updateQuotaCountdowns() {
  if (S.page !== 'accounts' || document.visibilityState === 'hidden') return;
  $$('time[data-quota-reset]').forEach(el => {
    const text = (el.dataset.quotaPrefix || '') + quotaCountdownText(Number(el.dataset.quotaReset));
    if (el.textContent !== text) el.textContent = text;
  });
}
function stopQuotaCountdownTimer() {
  if (quotaCountdownTimer !== null) { clearInterval(quotaCountdownTimer); quotaCountdownTimer = null; }
}
function syncQuotaCountdownTimer() {
  if (S.page !== 'accounts' || document.visibilityState === 'hidden') { stopQuotaCountdownTimer(); return; }
  updateQuotaCountdowns();
  if (quotaCountdownTimer === null) quotaCountdownTimer = setInterval(updateQuotaCountdowns, 1000);
}
document.addEventListener('visibilitychange', syncQuotaCountdownTimer);
window.addEventListener('pagehide', stopQuotaCountdownTimer);
window.addEventListener('pageshow', syncQuotaCountdownTimer);

function quotaBar(label, value, title = '') {
  const left = quotaPercent(value);
  if (left == null) return '';
  const cls = left < 15 ? 'err' : left < 40 ? 'warn' : '';
  return `<div class="quota-meter" title="${esc(title)}"><span class="quota-label">${esc(label)}</span><span class="qbar ${cls}"><i style="width:${left}%"></i></span><b>${left}%</b></div>`;
}
function geminiQuota(u) {
  const groups = u.groups || [];
  const valid = buckets => (buckets || []).filter(b => quotaPercent(b.left) != null);
  const models = (u.models || []).filter(m => String(m.model || '').startsWith('gemini-') && quotaPercent(m.left) != null);
  const gemini = groups.find(g => g.name === 'Gemini');
  const geminiBuckets = valid(gemini?.buckets);
  const shared = groups.filter(g => g.name !== 'Gemini').flatMap(g => valid(g.buckets));
  const rows = [], details = [];
  let geminiValue = null;
  if (models.length) geminiValue = Math.min(...models.map(m => Number(m.left)));
  else if (geminiBuckets.length) geminiValue = Math.min(...geminiBuckets.map(b => Number(b.left)));
  const geminiWeekly = geminiBuckets.filter(b => b.window === 'weekly');
  if (geminiWeekly.length) geminiValue = Math.min(geminiValue ?? 100, ...geminiWeekly.map(b => Number(b.left)));
  if (geminiValue != null) rows.push(quotaBar('Gemini', geminiValue, 'Gemini 额度中的最低剩余比例'));
  else rows.push('<div class="quota-meter"><span class="quota-label">Gemini</span><span class="muted tiny">暂无额度数据</span></div>');
  for (const [kind, label] of [['5h', '共享 5h'], ['weekly', '共享 周']]) {
    const windows = shared.filter(b => b.window === kind);
    if (windows.length) rows.push(quotaBar(label, Math.min(...windows.map(b => Number(b.left))), 'Claude 与 GPT-OSS 的共享额度'));
  }
  for (const group of groups) {
    for (const bucket of valid(group.buckets)) {
      const reset = quotaReset(bucket, u);
      details.push(`<div><span>${esc(group.name)} · ${esc(bucket.label || bucket.window)}</span><b>${quotaPercent(bucket.left)}%</b><time title="${esc(reset.title)}" ${reset.iso ? `datetime="${esc(reset.iso)}" data-quota-reset="${reset.epoch}" data-quota-prefix="重置："` : ''}>重置：${esc(quotaCountdownText(reset.epoch))}</time></div>`);
    }
  }
  if (!geminiBuckets.some(b => b.window === 'weekly')) details.push('<p>Google 未提供 Gemini 周限额；不能据此判断为不限量。</p>');
  if (models.length) details.push('<div class="quota-model-details">' + models.map(m => `<div><span>${esc(m.model)}</span><b>${quotaPercent(m.left)}%</b></div>`).join('') + '</div>');
  if (u.fetched_at) details.push(`<p>更新于 ${esc(new Date(Number(u.fetched_at) * 1000).toLocaleTimeString('zh-CN', { hour12: false }))}${u.cached ? ' · 缓存' : ''}</p>`);
  details.push('<p>共享额度用于 Google 通道内的 Claude / GPT-OSS；与 Gemini 网页用量分开计量。</p>');
  return `<div class="quota quota-compact">${rows.join('')}<details class="quota-details"><summary>详情</summary><div class="quota-detail-body">${details.join('')}</div></details></div>`;
}
function quotaCell(file) {
  const u = S.usage[file];
  const account = (S.accounts || []).find(a => a.file === file) || {};
  if (!u) return S.usageLoading ? '<span class="muted tiny">查询中…</span>' : '<span class="muted tiny">—</span>';
  if (!u.ok) return `<div class="quota-err" title="${esc(u.error || '')}">查询失败<div class="qfoot">${esc(String(u.error || '').slice(0, 46))}</div></div>`;
  if (account.provider === 'gemini' || u.provider === 'gemini') return geminiQuota(u);
  const windows = [u.primary, u.secondary].filter(Boolean);
  if (!windows.length) return '<span class="muted tiny">上游未返回额度</span>';
  return `<div class="quota quota-compact">${windows.map(w => {
    const span = Number(w.window_seconds), label = span === 604800 || ['7 天', '周'].includes(w.label) ? '周额度' : w.label || '额度';
    const reset = quotaReset(w, u);
    return `<div class="quota-window">${quotaBar(label, w.left)}<div class="quota-reset"><span>${esc(label)}重置</span><time ${reset.iso ? `datetime="${esc(reset.iso)}" data-quota-reset="${reset.epoch}"` : ''} title="${esc(reset.title)}">${esc(quotaCountdownText(reset.epoch))}</time></div></div>`;
  }).join('')}${u.limit_reached ? '<span class="badge err">已用尽</span>' : ''}</div>`;
}

function resetCell(file) {
  const a = (S.accounts || []).find(x => x.file === file) || {};
  if (a.provider === 'gemini') {
    return '<span class="muted tiny" title="Google 号没有额度重置次数，5 小时窗口与周限额按时间自动滚动">自动</span>';
  }
  const u = S.usage[file];
  if (!u || !u.ok) return '<span class="muted tiny">—</span>';
  const n = (u.reset && u.reset.available) || 0;
  if (!n) return '<span class="muted tiny">无可用次数</span>';
  return `<b>${n} 次</b>
    <div class="mt8"><button class="btn tiny" data-reset="${esc(file)}">用一次</button></div>`;
}

/* 订阅到期：凭据里的 expired 只是刷新令牌的有效期（登录时给 10 天、每次续期往后滚），
   拿它当"账号到期"会吓人。真正的订阅到期在 id_token 里，后端解好放在 a.sub.until。 */
function subLine(a) {
  if (a.provider === 'gemini') {
    return `<div class="muted tiny">Google 账号 · 走 Antigravity 通道</div>`;
  }
  const s = a.sub || {};
  const day = t => (t ? String(t).slice(0, 10) : '');
  const until = day(s.until);
  const cred = day(s.credential || a.expired);
  if (!until) {
    if ((a.plan || '').toLowerCase() === 'free') {
      return `<div class="muted tiny" title="免费套餐没有订阅到期日">免费号 · 无订阅期</div>`;
    }
    if (!cred) return '';
    return `<div class="muted tiny" title="上游没返回订阅日期；这里显示的是刷新令牌的有效期，会自动续期">
      凭据至 ${esc(cred)}</div>`;
  }
  const left = Math.ceil((new Date(until + 'T00:00:00') - new Date(new Date().toDateString())) / 86400000);
  const soon = left <= 7;
  const txt = soon ? `订阅 ${until} · 剩 ${Math.max(left, 0)} 天` : `订阅至 ${until}`;
  const tip = [`订阅到期 ${until}`,
    cred ? `刷新令牌有效期至 ${cred}（会自动续期，不代表账号到期）` : ''].filter(Boolean).join(' · ');
  return `<div class="muted tiny${soon ? ' t-warn' : ''}" title="${esc(tip)}">${esc(txt)}</div>`;
}

/* 套餐列：Codex 号有 plan，Google 号靠额度接口回填（free-tier / Google AI Pro / Ultra）。
   限额是按套餐给的，看到 Ultra 还是 Pro 才好判断这 100% 意味着多少。 */
function planCell(a) {
  const isGem = a.provider === 'gemini';
  const u = S.usage[a.file] || {};
  const txt = a.plan || (isGem ? (u.plan || 'Google') : '—');
  const cls = a.pool === 'pro' ? 'badge warn' : 'badge';
  return `<span class="${cls}">${esc(txt)}</span>${subLine(a)}`;
}

function paintQuota() {
  $$('[data-ucell]').forEach(td => { td.innerHTML = quotaCell(td.dataset.ucell); });
  $$('[data-rcell]').forEach(td => { td.innerHTML = resetCell(td.dataset.rcell); });
  /* Gemini 的套餐名（Google AI Pro / Ultra）只有额度接口才带，额度回来顺手补上 */
  $$('[data-pcell]').forEach(td => {
    const a = (S.accounts || []).find(x => x.file === td.dataset.pcell) || {};
    td.innerHTML = planCell(a);
  });
}

async function loadUsage(refresh) {
  S.usageLoading = true;
  paintQuota();
  try {
    const d = await api('/api/usage' + (refresh ? '?refresh=1' : ''));
    S.usage = d.usage || {};
    return { ok: true, failures: Object.values(S.usage).filter(u => !u.ok).length };
  } catch (e) {
    S.usage = Object.fromEntries(S.accounts.map(a => [a.file, { ok: false, error: e.message }]));
    return { ok: false, error: e.message };
  } finally { S.usageLoading = false; paintQuota(); }
}

async function loadAccounts() {
  try {
    const [d, s] = await Promise.all([api('/api/accounts'), api('/api/settings')]);
    S.settings = s; S.accounts = d.accounts || []; S.pools = s.pools || {};
    renderAccounts();
    loadUsage(false);
  } catch (e) { $('#tb-accounts').innerHTML = `<tr><td colspan="8" class="empty">${esc(e.message)}</td></tr>`; }
}

function renderAccounts() {
  const search = $('#account-search').value.trim().toLowerCase();
  const list = S.accounts.filter(a => (S.accountFilter === 'all' || (a.provider || 'codex') === S.accountFilter) && `${a.email} ${a.file} ${a.plan || ''} ${a.pool || ''}`.toLowerCase().includes(search));
  ['all', 'codex', 'gemini'].forEach(type => { $('#count-' + type).textContent = S.accounts.filter(a => type === 'all' || (a.provider || 'codex') === type).length; });
  $('#tb-accounts').innerHTML = list.length ? list.map(a => {
    const isGem = a.provider === 'gemini';
    const opts = Object.entries(S.pools).filter(([k]) => isGem ? k === 'gemini' : k !== 'gemini').map(([k, p]) => `<option value="${esc(k)}" ${a.pool === k ? 'selected' : ''}>${esc(p.label || k)}</option>`).join('');
    const label = a.email || a.file;
    return `<tr>
      <td><div class="account-identity"><span class="avatar ${isGem ? 'gem' : ''}" aria-hidden="true">${esc(label.slice(0, 2).toUpperCase())}</span><div><div class="account-email">${esc(label)}</div><div class="muted tiny mono account-file" title="${esc(a.file)}">${esc(a.file)}</div></div></div></td>
      <td data-pcell="${esc(a.file)}">${planCell(a)}</td>
      <td><select class="mini" data-pool-file="${esc(a.file)}" aria-label="${esc(label)} 所属池"><option value="" disabled ${!a.pool ? 'selected' : ''}>未归类</option>${opts}</select></td>
      <td data-lcell="${esc(a.file)}">${liveCell(a.file)}</td>
      <td data-ucell="${esc(a.file)}">${quotaCell(a.file)}</td>
      <td data-rcell="${esc(a.file)}">${resetCell(a.file)}</td>
      <td><label class="switchline"><input type="checkbox" data-toggle="${esc(a.file)}" aria-label="启用 ${esc(label)}" ${a.disabled ? '' : 'checked'}></label></td>
      <td class="right"><button class="btn tiny danger-ghost" data-del="${esc(a.file)}">删除</button></td>
    </tr>`;
  }).join('') : `<tr><td colspan="8" class="empty">${S.accounts.length ? '没有匹配的账号，试试其他关键词或筛选条件。' : '还没有账号，点击「添加账号」开始接入。'}</td></tr>`;
  $$('[data-pool-file]').forEach(sel => sel.onchange = async () => {
    const account = S.accounts.find(a => a.file === sel.dataset.poolFile);
    sel.disabled = true;
    try { await api(`/api/accounts/${encodeURIComponent(account.file)}/pool`, { method: 'POST', body: { pool: sel.value } }); S.mt.targets = null; toast('已切换池', 'ok'); await loadAccounts(); }
    catch (e) { sel.value = account.pool; toast(e.message, 'err'); }
    finally { sel.disabled = false; }
  });
  $$('[data-toggle]').forEach(chk => chk.onchange = async () => {
    chk.disabled = true;
    try { await api(`/api/accounts/${encodeURIComponent(chk.dataset.toggle)}/disabled`, { method: 'POST', body: { disabled: !chk.checked } }); const a = S.accounts.find(a => a.file === chk.dataset.toggle); if (a) a.disabled = !chk.checked; S.mt.targets = null; toast(chk.checked ? '已启用' : '已停用', 'ok'); }
    catch (e) { chk.checked = !chk.checked; toast(e.message, 'err'); }
    finally { chk.disabled = false; }
  });
  $$('[data-del]').forEach(btn => btn.onclick = async () => {
    if (!await confirmAction('删除后需要重新登录该账号。确定继续？', '删除账号', '删除账号')) return;
    await withBusy(btn, async () => { await api(`/api/accounts/${encodeURIComponent(btn.dataset.del)}`, { method: 'DELETE' }); S.mt.targets = null; toast('已删除', 'ok'); await loadAccounts(); });
  });
}
$('#account-search').addEventListener('input', renderAccounts);
$$('[data-account-filter]').forEach(btn => btn.onclick = () => {
  S.accountFilter = btn.dataset.accountFilter;
  $$('[data-account-filter]').forEach(b => { b.classList.toggle('active', b === btn); b.setAttribute('aria-pressed', String(b === btn)); });
  renderAccounts();
});
$('#btn-refresh-usage').onclick = () => withBusy($('#btn-refresh-usage'), async () => {
  const [result] = await Promise.all([loadUsage(true), loadRuntime()]);
  if (!result.ok) toast('额度刷新失败：' + result.error, 'err');
  else if (result.failures) toast(`已完成查询，${result.failures} 个账号查询失败，请查看额度栏。`, 'err', 5000);
  else toast('额度已更新', 'ok');
});

/* ------------------------------------------------------------ 额度重置 */
let resetTarget = null;

async function openReset(file) {
  resetTarget = file;
  const acc = S.accounts.find(a => a.file === file) || {};
  $('#reset-info').innerHTML = `<div><span>账号</span><b>${esc(acc.email || file)}</b></div>
    <div><span>当前额度</span><b id="reset-usage">查询中…</b></div>
    <div><span>重置机会</span><b id="reset-credits">查询中…</b></div>
    <div><span>最近到期</span><b id="reset-expire">—</b></div>`;
  $('#dlg-reset').classList.remove('hidden');
  $('#btn-reset-ok').disabled = true;

  const u = S.usage[file];
  if (u && u.ok) {
    const wins = [u.primary, u.secondary].filter(Boolean)
      .map(w => `${w.label} 已用 ${Math.round(w.used)}%`).join(' · ');
    $('#reset-usage').textContent = wins || '—';
  } else {
    $('#reset-usage').textContent = '未知';
  }

  try {
    const c = await api(`/api/accounts/${encodeURIComponent(file)}/credits`);
    const list = (c.credits || []).filter(x => x.status === 'available');
    $('#reset-credits').textContent = list.length ? `${list.length} 次可用` : '没有可用次数';
    if (list.length) {
      const dates = list.map(x => (x.expires_at || '').slice(0, 10)).filter(Boolean).sort();
      $('#reset-expire').textContent = dates[0] ? `${dates[0]}（最早的一张）` : '—';
    }
    $('#btn-reset-ok').disabled = !list.length;
    if (!list.length) {
      $('#reset-warn').textContent = '这个号现在没有可用的重置机会，不需要重置。';
    } else {
      $('#reset-warn').textContent = '重置会立刻清空当前额度窗口，并且不可撤销、消耗一次重置机会。';
    }
  } catch (e) {
    $('#reset-credits').textContent = '查询失败';
    $('#reset-expire').textContent = e.message;
  }
}

$('#btn-reset-cancel').onclick = () => { $('#dlg-reset').classList.add('hidden'); resetTarget = null; };
$('#btn-reset-ok').onclick = async () => {
  if (!resetTarget) return;
  const file = resetTarget;
  const btn = $('#btn-reset-ok');
  btn.disabled = true; btn.textContent = '重置中…';
  try {
    const r = await api(`/api/accounts/${encodeURIComponent(file)}/reset`, { method: 'POST', body: {} });
    toast(r.message || (r.ok ? '已重置' : '重置失败'), r.ok ? 'ok' : 'err');
    if (r.usage) S.usage[file] = r.usage;
    paintQuota();
    $('#dlg-reset').classList.add('hidden');
    resetTarget = null;
    loadUsage(true);
  } catch (e) { toast(e.message, 'err'); }
  btn.disabled = false; btn.textContent = '确认重置';
};

/* ------------------------------------------------------------ 添加账号 */
function selectProvider(provider) {
  $$('#provider-chips .chip').forEach(x => x.classList.toggle('active', x.dataset.provider === provider));
  $$('#pool-chips .chip').forEach(x => { x.classList.toggle('hidden', provider === 'gemini' ? x.dataset.pool !== 'gemini' : x.dataset.pool === 'gemini'); });
  // 换类型时顺手把池切到对应的那个，少点一次
  const want = provider === 'gemini' ? 'gemini' : 'plus';
  const target = $(`#pool-chips .chip[data-pool="${want}"]`) || $('#pool-chips .chip');
  if (target) {
    $$('#pool-chips .chip').forEach(x => x.classList.remove('active'));
    target.classList.add('active');
  }
}

$('#btn-add-account').onclick = async () => {
  await ensureSettings();
  $('#pool-chips').innerHTML = Object.entries(S.pools).map(([k, p]) =>
    `<button type="button" class="chip" data-pool="${esc(k)}">${esc(p.label || k)} 池</button>`).join('');
  $$('#pool-chips .chip').forEach(c => c.onclick = () => {
    $$('#pool-chips .chip').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
  });
  $$('#provider-chips .chip').forEach(c => c.onclick = () => selectProvider(c.dataset.provider));
  selectProvider('codex');

  $('#login-stage').classList.add('hidden');
  $('#callback-box').classList.add('hidden');
  $('#device-code').classList.remove('hidden');
  $('#device-hint').textContent = '在下面这个网址输入设备码';
  $('#login-log').textContent = '';
  $('#callback-url').value = '';
  $('#btn-login-start').classList.remove('hidden');
  $('#dlg-account').classList.remove('hidden');
};

$('#btn-login-close').onclick = async () => {
  if (S.loginSid) { try { await api(`/api/accounts/login/${S.loginSid}`, { method: 'DELETE' }); } catch (e) { } }
  clearInterval(S.loginTimer); S.loginSid = null;
  $('#dlg-account').classList.add('hidden');
  loadAccounts();
};

$('#btn-login-start').onclick = async () => {
  const start = $('#btn-login-start');
  if (start.disabled) return;
  start.disabled = true;
  const chip = $('#pool-chips .chip.active');
  const pool = chip ? chip.dataset.pool : 'plus';
  const provEl = $('#provider-chips .chip.active');
  const provider = provEl ? provEl.dataset.provider : 'codex';
  const isGem = provider === 'gemini';
  try {
    const r = await api('/api/accounts/login', { method: 'POST', body: { pool, provider } });
    if (!r.ok) { toast(r.error || '启动失败', 'err'); return; }
    S.loginSid = r.session;
    $('#login-stage').classList.remove('hidden');
    $('#btn-login-start').classList.add('hidden');
    $('#device-code').classList.toggle('hidden', isGem);
    $('#callback-box').classList.toggle('hidden', !isGem);
    $('#device-hint').textContent = isGem ? '在浏览器里打开这个链接完成授权' : '在下面这个网址输入设备码';
    if (!isGem) $('#device-code').textContent = '获取中…';
    $('#login-state').className = 'login-state';
    $('#login-state').textContent = isGem ? '正在向 Google 申请授权链接…' : '正在向 OpenAI 申请设备码…';
    clearInterval(S.loginTimer);
    S.loginTimer = setInterval(pollLogin, 1200);
    pollLogin();
  } catch (e) { toast(e.message, 'err'); }
  finally { start.disabled = false; }
};

$('#btn-callback-submit').onclick = async () => {
  if (!S.loginSid) return;
  const url = $('#callback-url').value.trim();
  if (!url) { toast('把浏览器地址栏那条链接贴进来', 'err'); return; }
  try {
    const r = await api(`/api/accounts/login/${S.loginSid}/callback`, { method: 'POST', body: { url } });
    if (!r.ok) { toast(r.error || '提交失败', 'err'); return; }
    $('#callback-url').value = '';
    toast('已提交，等 Google 返回结果', 'ok');
  } catch (e) { toast(e.message, 'err'); }
};

async function pollLogin() {
  if (!S.loginSid) return;
  const sid = S.loginSid;
  let r;
  try { r = await api(`/api/accounts/login/${S.loginSid}`); } catch (e) { return; }
  if (sid !== S.loginSid) return;
  const isGem = r.provider === 'gemini';
  if (isGem) {
    $('#device-code').classList.add('hidden');
    $('#callback-box').classList.remove('hidden');
    $('#device-hint').textContent = '在浏览器里打开这个链接完成授权';
  }
  if (r.code) $('#device-code').textContent = r.code;
  if (r.url) {
    $('#device-link').href = r.url;
    $('#device-link').textContent = r.url.replace(/^https?:\/\//, '');
  } else if (isGem) {
    $('#device-link').textContent = '正在获取授权链接…';
  }
  if (r.log && r.log.length) $('#login-log').textContent = r.log.join('\n');

  const st = $('#login-state');
  if (r.status === 'waiting') {
    st.className = 'login-state';
    st.textContent = isGem
      ? (r.url ? '等待你在浏览器里完成 Google 授权…' : '正在向 Google 申请授权链接…')
      : (r.code ? '等待你在浏览器里完成授权…' : '正在向 OpenAI 申请设备码…');
  } else if (r.status === 'done') {
    st.className = 'login-state ok';
    st.textContent = isGem ? '授权成功，Gemini 号已加入号池' : '登录成功，账号已加入号池';
    clearInterval(S.loginTimer); S.loginSid = null;
    toast('账号已添加', 'ok');
    setTimeout(() => { $('#dlg-account').classList.add('hidden'); loadAccounts(); }, 1200);
  } else {
    st.className = 'login-state err';
    st.textContent = r.error || '登录失败，请重试';
    clearInterval(S.loginTimer); S.loginSid = null;
    $('#btn-login-start').classList.remove('hidden');
  }
}

/* ------------------------------------------------------------ 模型 */
/* 模型页是「草稿 → 保存」：加/删/改都先动 MODEL_DRAFT，点保存才落盘。
   这样一次请求就能把别名同步进所有账号的凭据文件（/api/settings 里做的）。 */
let MODEL_DRAFT = null;

function modelDraft() {
  if (!MODEL_DRAFT) MODEL_DRAFT = JSON.parse(JSON.stringify(S.pools || {}));
  return MODEL_DRAFT;
}

function modelSuggest(k, p) {
  const set = new Set((p.models || []).map(m => (m.name || '').trim()).filter(Boolean));
  if (k === 'plus' || k === 'pro') {
  }
  if (k === 'gemini') {
    ['gemini-3.8-flash-tiered', 'gemini-3-flash', 'gemini-3.8-flash-high',
      'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite'].forEach(n => set.add(n));
  }
  return [...set];
}

function renderModels() {
  const box = $('#model-pools');
  const draft = modelDraft();

  box.innerHTML = Object.entries(draft).map(([k, p]) => {
    const list = p.models || [];
    const sg = modelSuggest(k, p);
    return `
    <div class="card model-pool-card" data-poolbox="${esc(k)}">
      <div class="poolhead">
        <span class="poolname">${esc(p.label || k)} 池</span>
        <span class="badge">${list.length} 个模型</span>
        <button class="btn tiny" data-addmodel="${k}" style="margin-left:auto">+ 添加模型</button>
      </div>
      <div class="modelrow with-del" style="margin-bottom:6px">
        <div class="h">客户端看到的模型名</div><div class="h">显示名称</div><div class="h">上游真实模型</div><div></div>
      </div>
      ${list.length ? list.map((m, i) => `
        <div class="modelrow with-del" style="margin-bottom:8px">
          <input data-m="alias" data-pool="${k}" data-i="${i}" value="${esc(m.alias)}" placeholder="${esc(k)}-型号">
          <input data-m="display" data-pool="${k}" data-i="${i}" value="${esc(m.display || '')}" placeholder="客户端菜单里显示的名字">
          <input data-m="name" data-pool="${k}" data-i="${i}" value="${esc(m.name || '')}" placeholder="上游真实模型名" list="sg-${k}">
          <button class="btn tiny danger-ghost" data-delmodel="${k}" data-i="${i}" title="删除这个模型">✕</button>
        </div>`).join('')
      : `<div class="muted tiny" style="padding:6px 0 2px">还没有模型，点右上角「+ 添加模型」。</div>`}
      <datalist id="sg-${k}">${sg.map(n => `<option value="${esc(n)}">`).join('')}</datalist>
    </div>`;
  }).join('');
}

async function loadModels() {
  await ensureSettings();
  MODEL_DRAFT = null; S.modelsDirty = false;
  renderModels();
}

$('#model-pools').addEventListener('input', (e) => {
  const inp = e.target.closest('input[data-m]');
  if (!inp) return;
  const d = modelDraft();
  const row = ((d[inp.dataset.pool] || {}).models || [])[+inp.dataset.i];
  if (row) { row[inp.dataset.m] = inp.value; S.modelsDirty = true; }
});

/* 上游名填完、别名还空着时，自动补一个带池前缀的别名 */
$('#model-pools').addEventListener('change', (e) => {
  const inp = e.target.closest('input[data-m="name"]');
  if (!inp) return;
  const k = inp.dataset.pool, i = +inp.dataset.i;
  const d = modelDraft();
  const row = ((d[k] || {}).models || [])[i];
  if (!row || (row.alias || '').trim() || !inp.value.trim()) return;
  const name = inp.value.trim();
  row.alias = (k === 'plus' || k === 'pro') ? `${k}-${name}` : name;
  const aliasInput = $(`#model-pools input[data-m="alias"][data-pool="${k}"][data-i="${i}"]`);
  if (aliasInput) aliasInput.value = row.alias;
});

$('#model-pools').addEventListener('click', (e) => {
  const add = e.target.closest('[data-addmodel]');
  if (add) {
    const d = modelDraft();
    const k = add.dataset.addmodel;
    if (d[k]) (d[k].models = d[k].models || []).push({ alias: '', display: '', name: '' });
    S.modelsDirty = true;
    renderModels();
    return;
  }
  const del = e.target.closest('[data-delmodel]');
  if (del) {
    const d = modelDraft();
    const arr = (d[del.dataset.delmodel] || {}).models || [];
    arr.splice(+del.dataset.i, 1);
    S.modelsDirty = true;
    renderModels();
  }
});

/* 保存后等号池把模型表读进来，返回「界面里有、号池却没列出来」的那些名字。
   号池只在启动时读别名，重启要 3~5 秒；不认的上游名还会被静默丢掉 ——
   不提示的话，用户只会看到「加了没反应」。 */
async function missingModelAliases(aliases, timeout = 14000) {
  // Pro 档别名由网关直连上游，号池本来就不列，不参与比对
  const direct = new Set(Object.values(S.pools).flatMap(p => (p.models || []).filter(m => (m.name || '').endsWith('-tiered')).map(m => m.alias)));
  const want = aliases.filter(a => a && !direct.has(a));
  const end = Date.now() + timeout;
  let live = new Set();
  while (Date.now() < end) {
    try {
      const d = await api('/api/models');
      live = new Set(d.models || []);
    } catch (e) { /* 号池正在重启，连不上就先跳过 */ }
    if (want.every(a => live.has(a))) break;
    await new Promise(r => setTimeout(r, 700));
  }
  return want.filter(a => !live.has(a));
}

$('#btn-save-models').onclick = async () => {
  const pools = JSON.parse(JSON.stringify(modelDraft()));
  const seen = new Set();
  for (const [k, p] of Object.entries(pools)) {
    const rows = [];
    for (const m of p.models || []) {
      const alias = (m.alias || '').trim();
      const name = (m.name || '').trim() || alias;
      const display = (m.display || '').trim() || alias;
      if (!alias) continue;
      if (seen.has(alias)) { toast(`模型名「${alias}」重复了`, 'err'); return; }
      seen.add(alias);
      rows.push({ ...m, alias, name, display });
    }
    if (!rows.length) { toast(`${p.label || k} 池至少要留一个模型`, 'err'); return; }
    p.models = rows;
  }
  if (!await confirmAction('保存模型配置会重启号池，并中断正在生成的回答。', '保存模型配置', '保存并重启')) return;
  const btn = $('#btn-save-models');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '保存中…';
  try {
    const r = await api('/api/settings', { method: 'PUT', body: { pools } });
    MODEL_DRAFT = null; S.modelsDirty = false; S.mt.targets = null;
    await ensureSettings(true);
    await loadModels();
    const all = Object.values(pools).flatMap(p => (p.models || []).map(m => m.alias));
    const missing = await missingModelAliases(all);
    if (missing.length) {
      toast(`已保存，但号池没认出：${missing.join('、')} —— 上游模型名可能不对，发请求会 404`, 'err', 8000);
    } else {
      toast(`已保存${r.accounts_synced ? `，同步了 ${r.accounts_synced} 个账号` : ''}，号池已生效`, 'ok');
    }
  } catch (e) { toast(e.message, 'err'); }
  finally { btn.disabled = false; btn.textContent = label; }
};

/* ------------------------------------------------------------ AI 提供商 */
async function loadProviders() {
  const tb = $('#tb-providers');
  try {
    const d = await api('/api/providers');
    S.providers = d.providers || [];
  } catch (e) { tb.innerHTML = `<tr><td colspan="6" class="empty">${esc(e.message)}</td></tr>`; return; }

  $('#nav-providers').textContent = S.providers.length;
  renderProviders();
}

function renderProviders() {
  const tb = $('#tb-providers');
  const query = $('#provider-search').value.trim().toLowerCase();
  const list = S.providers.filter(p => `${p.label} ${p.prefix} ${p.base_url}`.toLowerCase().includes(query));
  $('#providers-summary').textContent = `${S.providers.length} 个提供商 · ${S.providers.filter(p => p.enabled).length} 个已启用`;
  if (!list.length) {
    tb.innerHTML = `<tr><td colspan="6" class="empty">没有匹配的提供商，可修改搜索条件或添加提供商。</td></tr>`;
    return;
  }
  tb.innerHTML = list.map(p => {
    const models = (p.models || []).map(m =>
      `<span class="badge">${esc(p.prefix)}/${esc(m.alias)}</span>`).join(' ') || '<span class="muted tiny">未配置模型</span>';
    return `<tr>
      <td><div>${esc(p.label)}</div><div class="muted tiny mono">${esc(p.id)}</div></td>
      <td><div class="mono muted provider-url">${esc(p.base_url)}</div><div class="provider-protocol">${p.kind === 'claude' ? 'Anthropic Messages' : p.kind === 'codex' ? 'OpenAI Responses' : '自动识别协议'}</div></td>
      <td><code>${esc(p.prefix)}/</code></td>
      <td><div class="provider-models">${models}</div></td>
      <td><label class="switchline"><input type="checkbox" data-ptoggle="${esc(p.id)}" ${p.enabled ? 'checked' : ''} aria-label="启用 ${esc(p.label)}"></label></td>
      <td class="right">
        <button class="btn tiny" data-ptest="${esc(p.id)}">测试</button>
        <button class="btn tiny" data-pedit="${esc(p.id)}">编辑</button>
        <button class="btn tiny danger-ghost" data-pdel="${esc(p.id)}">删除</button>
      </td>
    </tr>`;
  }).join('');

  $$('[data-ptoggle]').forEach(c => c.onchange = async () => {
    if (!await confirmAction('修改提供商状态会重启号池，并中断正在生成的回答。', '修改提供商状态', '应用并重启')) { c.checked = !c.checked; return; }
    c.disabled = true;
    try {
      await api(`/api/providers/${encodeURIComponent(c.dataset.ptoggle)}`, { method: 'PATCH', body: { enabled: c.checked } });
      toast(c.checked ? '已启用，号池已重启' : '已停用，号池已重启', 'ok');
      S.mt.targets = null; loadProviders();
    } catch (e) { toast(e.message, 'err'); c.checked = !c.checked; }
    finally { c.disabled = false; }
  });
  $$('[data-ptest]').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = '测试中';
    try {
      const r = await api('/api/providers/test', { method: 'POST', body: { id: b.dataset.ptest } });
      toast(r.message || (r.ok ? '连接正常' : '连接失败'), r.ok ? 'ok' : 'err');
    } catch (e) { toast(e.message, 'err'); }
    b.disabled = false; b.textContent = '测试';
  });
  $$('[data-pedit]').forEach(b => b.onclick = () =>
    openProviderDialog(S.providers.find(x => x.id === b.dataset.pedit)));
  $$('[data-pdel]').forEach(b => b.onclick = async () => {
    if (!await confirmAction('该提供商的模型将从客户端列表消失，号池会重启。', '删除提供商', '删除并重启')) return;
    try {
      await api(`/api/providers/${encodeURIComponent(b.dataset.pdel)}`, { method: 'DELETE' });
      toast('已删除，号池已重启', 'ok'); S.mt.targets = null; loadProviders();
    } catch (e) { toast(e.message, 'err'); }
  });
}

function providerModelRow(m) {
  return `<div class="modelrow with-del" data-pmrow>
    <input data-pm="alias" value="${esc(m && m.alias || '')}" placeholder="gpt-6-sol">
    <input data-pm="display" value="${esc(m && m.display || '')}" placeholder="显示名称">
    <input data-pm="name" value="${esc(m && m.name || '')}" placeholder="上游真实模型名"><button class="btn tiny danger-ghost" data-remove-pmodel aria-label="删除此模型行">×</button>
  </div>`;
}

function openProviderDialog(p) {
  S.editingProvider = p ? p.id : null;
  $('#provider-dlg-title').textContent = p ? '编辑提供商' : '添加提供商';
  $('#p-label').value = p ? p.label : '';
  $('#p-prefix').value = p ? p.prefix : '';
  $('#p-base').value = p ? p.base_url : '';
  $('#p-key').value = p ? p.api_key : '';
  $('#p-enabled').checked = p ? p.enabled : true;
  $('#p-kind').value = p ? p.kind || '' : '';
  $('#p-test-msg').textContent = '';
  const models = (p && p.models && p.models.length) ? p.models : [{ alias: '', display: '', name: '' }];
  $('#p-models').innerHTML = models.map(providerModelRow).join('');
  $('#dlg-provider').classList.remove('hidden');
}

$('#btn-add-provider').onclick = () => openProviderDialog(null);
$('#btn-p-cancel').onclick = () => $('#dlg-provider').classList.add('hidden');
$('#btn-p-addmodel').onclick = () => {
  $('#p-models').insertAdjacentHTML('beforeend', providerModelRow({}));
};
$('#btn-p-test').onclick = async () => {
  const btn = $('#btn-p-test');
  btn.disabled = true; btn.textContent = '测试中';
  $('#p-test-msg').textContent = '正在连接…';
  try {
    const r = await api('/api/providers/test', {
      method: 'POST',
      body: { base_url: $('#p-base').value.trim(), api_key: $('#p-key').value.trim(), kind: $('#p-kind').value, models: collectProviderModels() },
    });
    $('#p-test-msg').textContent = r.message || (r.ok ? '连接正常' : '连接失败');
    $('#p-test-msg').className = 'muted tiny ' + (r.ok ? '' : 'muted');
    toast(r.message, r.ok ? 'ok' : 'err');
  } catch (e) { $('#p-test-msg').textContent = e.message; toast(e.message, 'err'); }
  btn.disabled = false; btn.textContent = '测试连接';
};

function collectProviderModels() {
  return $$('#p-models [data-pmrow]').map(row => ({
    alias: $('[data-pm="alias"]', row).value.trim(),
    display: $('[data-pm="display"]', row).value.trim(),
    name: $('[data-pm="name"]', row).value.trim(),
  })).filter(m => m.alias);
}

$('#provider-search').addEventListener('input', renderProviders);
$('#p-models').addEventListener('click', e => { const b = e.target.closest('[data-remove-pmodel]'); if (b) b.closest('[data-pmrow]').remove(); });
$('#btn-p-save').onclick = async () => {
  const body = {
    label: $('#p-label').value.trim() || '未命名',
    prefix: $('#p-prefix').value.trim() || $('#p-label').value.trim().toLowerCase(),
    base_url: $('#p-base').value.trim(),
    api_key: $('#p-key').value.trim(),
    enabled: $('#p-enabled').checked,
    models: collectProviderModels(), kind: $('#p-kind').value,
  };
  if (!body.base_url || !body.api_key) { toast('请填写接口地址和 API Key', 'err'); return; }
  try { const url = new URL(body.base_url); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error(); } catch (e) { toast('请输入有效的 HTTP 或 HTTPS 接口地址，不含密钥、查询参数或片段', 'err'); return; }
  if (!body.models.length) { toast('请至少添加一个模型', 'err'); return; }
  if (new Set(body.models.map(m => m.alias)).size !== body.models.length) { toast('模型别名不能重复', 'err'); return; }
  if (!await confirmAction('保存提供商配置会重启号池，并中断正在生成的回答。', '保存提供商配置', '保存并重启')) return;
  const save = $('#btn-p-save'); if (save.disabled) return; save.disabled = true;
  try {
    if (S.editingProvider) await api(`/api/providers/${encodeURIComponent(S.editingProvider)}`, { method: 'PATCH', body });
    else await api('/api/providers', { method: 'POST', body });
    $('#dlg-provider').classList.add('hidden');
    toast('已保存，号池已重启', 'ok');
    S.mt.targets = null;
    loadProviders();
  } catch (e) { toast(e.message, 'err'); }
  finally { save.disabled = false; }
};

/* ------------------------------------------------------------ 模型测试 */
function mtTargetsFromList() {
  const keys = $$('#mt-models input:checked').map(x => x.value);
  return {
    accounts: $$('#mt-accounts input:checked').map(x => x.value),
    models: keys.filter(k => k.startsWith('p:')).map(k => k.slice(2)),
    relays: $$('#mt-providers input:checked').map(x => x.value).map(k => {
      const s = k.slice(2), i = s.indexOf('|');
      return { provider: s.slice(0, i), model: s.slice(i + 1) };
    }),
  };
}

async function loadModelTest() {
  try {
    const b = await api('/api/modeltrace/bank');
    $('#mt-bank-info').textContent = b.ok
      ? `指纹库 ${b.model_count} 个模型 · ${String(b.built_at || '').slice(0, 10)} 构建`
      : `指纹库不可用：${b.error}`;
  } catch (e) { $('#mt-bank-info').textContent = '指纹库状态未知'; }

  if (!S.mt.targets) {
    try { S.mt.targets = await api('/api/modeltrace/targets'); }
    catch (e) { toast(e.message, 'err'); return; }
  }
  const t = S.mt.targets;
  $('#mt-accounts').innerHTML = (t.accounts || []).length
    ? t.accounts.map(a => `<label><input type="checkbox" value="${esc(a.file)}" ${a.disabled ? 'disabled' : ''}>
        <span>${esc(a.email || a.file)}</span>
        <span class="sub">${esc(a.pool_label || '未归类')}${a.disabled ? ' · 已停用' : ''}</span></label>`).join('')
    : '<div class="empty-line">还没有账号</div>';

  $('#mt-models').innerHTML = (t.models || []).length
    ? t.models.map(m => `<label><input type="checkbox" value="${esc(m.key || ('p:' + m.alias))}">
        <span>${esc(m.alias)}</span>
        <span class="sub">→ ${esc(m.upstream)}</span>
        <span class="badge ${m.in_bank ? 'ok' : 'warn'}">${m.in_bank ? '库内' : '库外'}</span></label>`).join('')
    : '<div class="empty-line">订阅池里还没有可测模型</div>';

  const pm = t.provider_models || [];
  $('#mt-providers').innerHTML = pm.length
    ? pm.map(m => `<label><input type="checkbox" value="${esc(m.key)}" ${m.ready ? '' : 'disabled'}>
        <span>${esc(m.exposed)}</span>
        <span class="sub">${esc(m.provider_label)} → ${esc(m.upstream)}</span>
        ${m.ready ? '' : '<span class="badge warn">未配 Key</span>'}
        ${m.ready && !m.enabled ? '<span class="badge">已停用</span>' : ''}
        <span class="badge ${m.in_bank ? 'ok' : 'warn'}">${m.in_bank ? '库内' : '库外'}</span></label>`).join('')
    : '<div class="empty-line">还没有提供商模型，去「提供商」加一个</div>';
}

$$('[data-all]').forEach(b => b.onclick = () => {
  const boxes = $$('#' + b.dataset.all + ' input:not(:disabled)');
  const allOn = boxes.length && boxes.every(x => x.checked);
  boxes.forEach(x => { x.checked = !allOn; });
});

$('#btn-mt-refresh').onclick = () => {
  S.mt.targets = null;
  loadModelTest();
};

$('#btn-mt-bank-update').onclick = async () => {
  const b = $('#btn-mt-bank-update');
  b.disabled = true; b.textContent = '更新中…';
  try {
    const r = await api('/api/modeltrace/bank/update', { method: 'POST' });
    toast(r.ok ? `指纹库已更新（${r.model_count} 个模型）` : (r.error || '更新失败'), r.ok ? 'ok' : 'err');
    loadModelTest();
  } catch (e) { toast(e.message, 'err'); }
  b.disabled = false; b.textContent = '更新指纹库';
};

function mtRowHtml(r) {
  const isRelay = r.via === 'relay';
  const who = isRelay
    ? `${esc(r.provider_label || r.provider || '提供商')}<div class="muted tiny">提供商</div>`
    : `${esc(r.email || r.file)}<div class="muted tiny">账号</div>`;
  if (!r.ok) {
    return `<tr><td>${who}</td><td class="mono">${esc(r.model || '—')}</td>
      <td colspan="4" class="muted">${esc(r.error || '失败')}</td>
      <td class="muted">${r.elapsed != null ? r.elapsed + ' s' : '—'}</td></tr>`;
  }
  const badge = ['match', 'close', 'mismatch', 'outside', 'weak'].includes(r.verdict)
    ? r.verdict : '';
  const cand = (r.top || []).slice(0, 3)
    .map(t => `${esc(t.model)} ${Math.round(t.probability * 100)}%`).join(' · ');
  return `<tr>
    <td>${who}</td>
    <td class="mono">${esc(r.model)}<div class="muted tiny">→ ${esc(r.upstream)}</div></td>
    <td><b>${esc(r.prediction)}</b><div class="muted tiny">${cand}</div></td>
    <td>${Math.round((r.probability || 0) * 100)}%</td>
    <td class="muted">${esc(r.family || '')}<div class="muted tiny">${Math.round((r.family_probability || 0) * 100)}%</div></td>
    <td><span class="badge ${badge}">${esc(r.verdict_text)}</span></td>
    <td class="muted">${r.elapsed} s</td>
  </tr>`;
}

function paintMt(job) {
  if (!job) return;
  $('#mt-result-card').classList.remove('hidden');
  const rows = job.rows || [];
  $('#tb-mt').innerHTML = rows.length
    ? rows.map(mtRowHtml).join('')
    : '<tr><td colspan="7" class="empty">正在测试…</td></tr>';
  const ok = rows.filter(r => r.ok).length;
  $('#mt-summary').textContent = `${rows.length}/${job.total} 已完成 · 成功 ${ok}`;
}

async function pollMt() {
  const jid = S.mt.job;
  if (!jid) return;
  let job;
  try { job = await api(`/api/modeltrace/job/${jid}`); } catch (e) { clearInterval(S.mt.timer); S.mt.job = null; $('#btn-mt-run').disabled = false; $('#btn-mt-cancel').disabled = true; $('#mt-progress').textContent = '测试状态查询失败，请刷新后重试'; toast(e.message, 'err'); return; }
  if (jid !== S.mt.job) return;
  S.mt.rows = job.rows || [];
  paintMt(job);
  $('#mt-progress').textContent = `进度 ${job.done}/${job.total}`;
  if (job.status !== 'running') {
    clearInterval(S.mt.timer); S.mt.timer = null;
    $('#btn-mt-run').disabled = false;
    $('#btn-mt-cancel').disabled = true;
    const statusText = { done: '测试完成', cancelled: '已取消', failed: '测试失败' }[job.status] || job.status;
    $('#mt-progress').textContent = `${statusText} · 用时 ${job.elapsed} 秒`
      + (job.rounds === 1 ? ' · 单条样本，概率数字会偏低；想更稳就勾「每项测 3 条」' : '');
    if (job.error) toast(job.error, 'err');
    else toast(statusText, job.status === 'done' ? 'ok' : '');
    S.mt.job = null;
    // 测试会消耗额度，回来后刷新一下账号页的数字
    S.usage = {};
    loadUsage(false);
  }
}

$('#btn-mt-run').onclick = async () => {
  const pick = mtTargetsFromList();
  const n = pick.models.length + pick.relays.length;
  if (!n) { toast('先选至少一个模型', 'err'); return; }
  if (pick.models.length && !pick.accounts.length) {
    toast('测订阅池模型要先选账号；只测提供商模型可以不选账号', 'err'); return;
  }
  $('#btn-mt-run').disabled = true;
  $('#btn-mt-cancel').disabled = false;
  $('#mt-progress').textContent = '正在启动…';
  S.mt.rows = [];
  try {
    const r = await api('/api/modeltrace/run', {
      method: 'POST',
      body: { ...pick, rounds: $('#mt-accurate').checked ? 3 : 1 },
    });
    if (!r.ok) { toast(r.error || '启动失败', 'err'); $('#btn-mt-run').disabled = false; $('#btn-mt-cancel').disabled = true; return; }
    S.mt.job = r.job;
    $('#mt-progress').textContent =
      `已排入 ${r.total} 个组合 · 每项 ${r.rounds} 条挑战（${r.expected_count} 个数字起）`;
    clearInterval(S.mt.timer);
    S.mt.timer = setInterval(pollMt, 1500);
    pollMt();
  } catch (e) {
    toast(e.message, 'err');
    $('#btn-mt-run').disabled = false;
    $('#btn-mt-cancel').disabled = true;
  }
};

$('#btn-mt-cancel').onclick = async () => {
  if (!S.mt.job) return;
  try { await api(`/api/modeltrace/job/${S.mt.job}/cancel`, { method: 'POST' }); } catch (e) { }
  $('#btn-mt-cancel').disabled = true;
  $('#mt-progress').textContent = '正在取消…';
};

/* One Key whitelist drives both authorization and the client's model catalog. */
let keyScopeGroups = [], keyCustomSelection = [], connectingKeyId = null;
const NATIVE_GEMINI_MODELS = new Set(['gemini-3-pro-preview', 'gemini-3.1-pro-preview', 'gemini-3.1-pro-preview-customtools', 'gemini-3-pro', 'gemini-3.1-pro', 'gemini-2.5-pro']);
function keyModelList(key) {
  const listed = key?.model_list || String(key?.models || '*').split(',').map(x => x.trim()).filter(Boolean);
  return !listed.length || listed.includes('*') ? [...S.models] : [...new Set(listed)];
}
function keyCatalogIds(ids) { return ids.filter(id => S.models.includes(id) && !NATIVE_GEMINI_MODELS.has(id) && !id.endsWith('-image') && !id.startsWith('gpt-image') && id !== 'codex-auto-review'); }
function keyGroups(extra = []) {
  const options = new Set([...S.models, ...extra]), seen = new Set(), groups = new Map();
  const add = (gid, label, id, display) => {
    if (!options.has(id) || seen.has(id)) return;
    if (!groups.has(gid)) groups.set(gid, { id: gid, label, models: [] });
    groups.get(gid).models.push({ id, display: display || id }); seen.add(id);
  };
  for (const [id, pool] of Object.entries(S.pools || {})) {
    for (const model of pool.models || []) {
      let gid = id, label = id === 'plus' ? 'ChatGPT Plus' : id === 'pro' ? 'ChatGPT Pro' : pool.label || id;
      if (id === 'gemini') {
        const name = String(model.name || model.alias).toLowerCase();
        [gid, label] = name.startsWith('claude') ? ['google-claude', 'Google · Claude'] : name.startsWith('gpt-oss') ? ['google-oss', 'Google · GPT-OSS'] : ['google-gemini', 'Google · Gemini'];
      }
      add(gid, label, model.alias, model.display);
    }
  }
  for (const provider of S.providers || []) {
    for (const model of provider.models || []) {
      const id = provider.prefix ? provider.prefix.replace(/^\/+|\/+$/g, '') + '/' + model.alias : model.alias;
      add('provider:' + provider.id, provider.label || provider.id, id, model.display);
    }
  }
  for (const id of options) if (!seen.has(id)) add(S.models.includes(id) ? 'other' : 'unavailable', S.models.includes(id) ? '其他模型' : '暂未在号池列出', id, id);
  return [...groups.values()];
}
function keyScopeSummary(key) {
  const ids = keyModelList(key), all = !key?.model_list?.length || key.model_list.includes('*');
  const labels = keyGroups(ids).map(g => { const n = g.models.filter(m => ids.includes(m.id)).length; return n ? `${g.label} · ${n}` : ''; }).filter(Boolean);
  return { all, ids, catalog: keyCatalogIds(ids), labels };
}
function updateKeyScopeStatus() {
  const all = $('#key-all').checked;
  const inputs = $$('#key-models input[data-key-model]');
  for (const input of inputs) input.disabled = all;
  for (let i = 0; i < keyScopeGroups.length; i++) {
    const group = keyScopeGroups[i], selected = group.models.filter(m => inputs.some(x => x.value === m.id && x.checked)).length;
    const checkbox = $(`[data-key-group="${i}"]`);
    checkbox.disabled = all; checkbox.checked = selected === group.models.length; checkbox.indeterminate = selected > 0 && selected < group.models.length;
    $(`[data-key-group-count="${i}"]`).textContent = `${selected}/${group.models.length}`;
  }
  const selected = all ? [...S.models] : inputs.filter(x => x.checked).map(x => x.value);
  const count = keyCatalogIds(selected).length;
  $('#key-scope-status').textContent = `授权 ${selected.length} 个模型 · Codex 目录 ${count} 个` + (count > 20 ? '（最多显示前 20 个，建议按组减少）' : '');
  $('#key-scope-status').className = 'key-scope-status tiny ' + (count > 20 ? 't-warn' : 'muted');
}
function renderKeyModelGroups(key) {
  const listed = key?.model_list || [], all = !listed.length || listed.includes('*');
  keyCustomSelection = all ? [] : [...listed];
  keyScopeGroups = keyGroups(listed.filter(x => x !== '*'));
  $('#key-models').innerHTML = `<div class="key-scope-tools"><label><input type="checkbox" id="key-all" ${all ? 'checked' : ''}>允许全部模型<span class="muted tiny">含以后新增的型号</span></label><button class="btn tiny ghost" type="button" id="key-select-none">清空选择</button></div>` + keyScopeGroups.map((group, i) => `<section class="key-model-group"><div class="key-group-head"><label><input type="checkbox" data-key-group="${i}"><strong>${esc(group.label)}</strong><span class="badge" data-key-group-count="${i}"></span></label><button class="btn tiny ghost" type="button" data-key-expand="${i}" aria-expanded="false">展开</button></div><div class="key-model-list hidden" id="key-group-${i}">${group.models.map(m => `<label><input type="checkbox" data-key-model data-key-model-group="${i}" value="${esc(m.id)}" ${all || listed.includes(m.id) ? 'checked' : ''}><span title="${esc(m.id)}">${esc(m.display)}${NATIVE_GEMINI_MODELS.has(m.id) || m.id.endsWith('-image') ? '<small>Gemini 原生接口</small>' : ''}</span></label>`).join('')}</div></section>`).join('');
  $('#key-all').onchange = e => {
    const inputs = $$('#key-models input[data-key-model]');
    if (e.target.checked) keyCustomSelection = inputs.filter(x => x.checked).map(x => x.value);
    inputs.forEach(x => { x.checked = e.target.checked || keyCustomSelection.includes(x.value); });
    updateKeyScopeStatus();
  };
  $('#key-select-none').onclick = () => { $('#key-all').checked = false; keyCustomSelection = []; $$('#key-models input[data-key-model]').forEach(x => { x.checked = false; }); updateKeyScopeStatus(); };
  $$('[data-key-group]').forEach(input => input.onchange = () => { $$('#key-models [data-key-model-group="' + input.dataset.keyGroup + '"]').forEach(x => { x.checked = input.checked; }); updateKeyScopeStatus(); });
  $$('#key-models input[data-key-model]').forEach(input => input.onchange = updateKeyScopeStatus);
  $$('[data-key-expand]').forEach(button => button.onclick = () => { const box = $('#key-group-' + button.dataset.keyExpand); const hidden = box.classList.toggle('hidden'); button.textContent = hidden ? '展开' : '收起'; button.setAttribute('aria-expanded', String(!hidden)); });
  updateKeyScopeStatus();
}
const KEY_CLAUDE_ROLES = [
  { id: 'SONNET', label: 'Sonnet 默认模型', match: /sonnet/i },
  { id: 'OPUS', label: 'Opus 默认模型', match: /opus/i },
  { id: 'HAIKU', label: 'Haiku 默认模型', match: /haiku/i },
  { id: 'FABLE', label: 'Fable 默认模型', match: /fable|mythos/i },
  { id: 'SUBAGENT', label: '子任务模型', match: null },
];
function keyConnectionDetails() {
  const key = S.keys.find(k => k.id === connectingKeyId);
  if (!key) throw new Error('客户端 Key 不存在，请刷新列表');
  const uri = new URL($('#key-connect-base').value.trim());
  if (!['http:', 'https:'].includes(uri.protocol) || uri.username || uri.password || uri.search || uri.hash) throw new Error('请输入有效的 Base URL，不要携带密钥或查询参数');
  const base = uri.toString().replace(/\/+$/, '');
  const client = $('#key-connect-client').value;
  const catalog = keyScopeSummary(key).catalog;
  if (client === 'codex' && catalog.length > 20) throw new Error(`这个 Key 的 Codex 目录有 ${catalog.length} 个模型，请先编辑 Key，减少到 20 个以内`);
  const model = $('#key-connect-model').value;
  if (!catalog.includes(model)) throw new Error('这个 Key 没有可接入的模型，请先编辑模型授权');
  return { key, base, model, client, catalog };
}
function keyConnectionConfig(includeSecret) {
  const { key, base, model, client, catalog } = keyConnectionDetails();
  const secret = includeSecret ? key.secret : '复制时自动填入当前 Key';
  if (client === 'claude-code') {
    const roles = Object.fromEntries(KEY_CLAUDE_ROLES.map(role => {
      const value = $(`[data-key-connect-role="${role.id}"]`)?.value;
      if (!catalog.includes(value)) throw new Error(`${role.label}未选择已授权模型`);
      return [role.id, value];
    }));
    return JSON.stringify({
      env: {
        ANTHROPIC_BASE_URL: base.replace(/\/v1$/i, ''),
        ANTHROPIC_AUTH_TOKEN: secret,
        ANTHROPIC_MODEL: model,
        ANTHROPIC_DEFAULT_SONNET_MODEL: roles.SONNET,
        ANTHROPIC_DEFAULT_OPUS_MODEL: roles.OPUS,
        ANTHROPIC_DEFAULT_HAIKU_MODEL: roles.HAIKU,
        ANTHROPIC_DEFAULT_FABLE_MODEL: roles.FABLE,
        CLAUDE_CODE_SUBAGENT_MODEL: roles.SUBAGENT,
      },
      availableModels: catalog,
      modelPicker: {
        replaceBuiltInOptions: true,
        options: catalog.map(id => ({ model: id, label: id })),
      },
    }, null, 2);
  }
  return `model = ${JSON.stringify(model)}\nmodel_provider = "cliproxy"\nmodel_reasoning_effort = "high"\n\n[model_providers.cliproxy]\nname = "AI 号池"\nbase_url = ${JSON.stringify(base)}\nwire_api = "responses"\nsupports_websockets = false\nrequires_openai_auth = false\nmodel_catalog_url = ${JSON.stringify(base + '/models')}\nexperimental_bearer_token = ${JSON.stringify(secret)}\n\n[features]\napi_key_model_discovery = true`;
}
function renderKeyClaudeRoles(reset = false) {
  const key = S.keys.find(k => k.id === connectingKeyId);
  if (!key) return;
  const catalog = keyScopeSummary(key).catalog, model = $('#key-connect-model').value;
  const previous = new Map($$('[data-key-connect-role]').map(select => [select.dataset.keyConnectRole, { value: select.value, auto: select.dataset.auto === 'true' }]));
  $('#key-connect-role-fields').innerHTML = KEY_CLAUDE_ROLES.map(role => {
    const old = previous.get(role.id), auto = reset || !old || old.auto || !catalog.includes(old.value);
    const choice = auto ? catalog.find(id => role.match?.test(id)) || model : old.value;
    return `<div class="field"><label for="key-connect-role-${role.id}">${role.label}</label><select id="key-connect-role-${role.id}" data-key-connect-role="${role.id}" data-auto="${auto}">${catalog.map(id => `<option value="${esc(id)}" ${id === choice ? 'selected' : ''}>${esc(id)}</option>`).join('')}</select></div>`;
  }).join('');
  $$('[data-key-connect-role]').forEach(select => select.onchange = () => { select.dataset.auto = 'false'; paintKeyConnection(); });
}
function renderKeyConnectionModels(preferAstra = false) {
  const key = S.keys.find(k => k.id === connectingKeyId);
  if (!key) return;
  const selected = $('#key-connect-model').value, catalog = keyScopeSummary(key).catalog;
  const ids = $('#key-connect-client').value === 'codex' ? catalog.slice(0, 20) : catalog;
  $('#key-connect-model').innerHTML = ids.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
  if (preferAstra && ids.includes('plus-gpt-6-astra')) $('#key-connect-model').value = 'plus-gpt-6-astra';
  else if (ids.includes(selected)) $('#key-connect-model').value = selected;
}
function paintKeyConnection() {
  const claude = $('#key-connect-client').value === 'claude-code';
  const key = S.keys.find(k => k.id === connectingKeyId), summary = key ? keyScopeSummary(key) : null;
  $('#key-connect-claude-options').classList.toggle('hidden', !claude);
  $('#key-connect-menu-count').classList.toggle('hidden', !claude);
  $('#key-connect-menu-count').textContent = `配置将包含全部 ${summary?.catalog.length || 0} 个可用模型，可在 Claude Code 的 /model 菜单中切换。`;
  if (summary) $('#key-connect-scope').textContent = claude
    ? `这把 Key 授权 ${summary.ids.length} 个模型 · 导出 ${summary.catalog.length} 个可用模型`
    : `授权 ${summary.ids.length} 个模型 · Codex 目录 ${summary.catalog.length} 个` + (summary.catalog.length > 20 ? '（Codex 最多显示前 20 个，请先编辑 Key 减少模型）' : '');
  $('#key-connect-path').textContent = claude ? '保存到 ~/.claude/settings.json（用户配置）；已有配置时合并这些字段。' : '保存到 ~/.codex/config.toml；已有配置时合并相应字段。';
  $('#key-connect-hint').textContent = claude
    ? '复制时自动带入当前 Key。完整菜单需要 Claude Code 2.1.242 或更新版本；旧版可用 /model 模型ID 切换。保存后重新启动 Claude Code。'
    : '复制时自动带入当前 Key，并开启在线模型发现。保存后重启 Codex；VS Code 插件执行 Developer: Reload Window。';
  try {
    $('#key-connect-config').textContent = keyConnectionConfig(false);
    $('#btn-connect-copy').disabled = false;
    try { localStorage.setItem('aipool-client-base', $('#key-connect-base').value); } catch (e) { }
  } catch (e) { $('#key-connect-config').textContent = e.message; $('#btn-connect-copy').disabled = true; }
}
function openKeyConnection(id) {
  connectingKeyId = id;
  const key = S.keys.find(k => k.id === id);
  $('#key-connect-title').textContent = key.name + ' · 接入配置';
  $('#key-connect-client').value = 'codex';
  renderKeyConnectionModels(true);
  renderKeyClaudeRoles(true);
  try { const saved = localStorage.getItem('aipool-client-base'); if (saved) $('#key-connect-base').value = saved; } catch (e) { }
  paintKeyConnection(); $('#dlg-key-connect').classList.remove('hidden');
}
$('#key-connect-client').onchange = () => { renderKeyConnectionModels(); renderKeyClaudeRoles(); paintKeyConnection(); };
$('#key-connect-base').addEventListener('input', paintKeyConnection);
$('#key-connect-model').onchange = () => { renderKeyClaudeRoles(); paintKeyConnection(); };
$('#btn-connect-cancel').onclick = () => $('#dlg-key-connect').classList.add('hidden');
$('#btn-connect-copy').onclick = () => { try { copy(keyConnectionConfig(true)); } catch (e) { toast(e.message, 'err'); } };


/* 客户端 Key */
async function loadKeys() {
  try {
    const [k, m, s, p] = await Promise.all([api('/api/keys'), api('/api/models'), api('/api/settings'), api('/api/providers')]);
    S.keys = k.keys; S.models = m.exposed || []; S.settings = s; S.pools = s.pools || {}; S.providers = p.providers || [];
  } catch (e) { $('#tb-keys').innerHTML = `<tr><td colspan="5" class="empty">${esc(e.message)}</td></tr>`; return; }

  const tb = $('#tb-keys');
  if (!S.keys.length) { tb.innerHTML = `<tr><td colspan="5" class="empty">还没有 Key</td></tr>`; return; }

  tb.innerHTML = S.keys.map(k => {
    const summary = keyScopeSummary(k);
    const models = summary.all ? '<span class="badge ok">全部模型</span>' : summary.labels.map(label => `<span class="badge">${esc(label)}</span>`).join(' ');
    return `<tr>
      <td>${esc(k.name)}</td>
      <td class="mono">${esc(k.secret.slice(0, 10))}…<button class="btn tiny ghost" data-copykey="${esc(k.secret)}">复制</button></td>
      <td><div class="key-scope-badges">${models}</div><div class="muted tiny">Codex 目录 ${summary.catalog.length} 个${summary.catalog.length > 20 ? ' · 超过 20 项' : ''}</div></td>
      <td><label class="switchline"><input type="checkbox" data-keytoggle="${k.id}" ${k.enabled ? 'checked' : ''}></label></td>
      <td class="right">
        <button class="btn tiny" data-keyconfig="${k.id}">接入配置</button><button class="btn tiny" data-keyedit="${k.id}">编辑</button>
        <button class="btn tiny danger-ghost" data-keydel="${k.id}">删除</button>
      </td>
    </tr>`;
  }).join('');

  $$('[data-copykey]').forEach(b => b.onclick = () => copy(b.dataset.copykey));
  $$('[data-keytoggle]').forEach(c => c.onchange = async () => {
    c.disabled = true;
    try { await api(`/api/keys/${c.dataset.keytoggle}`, { method: 'PATCH', body: { enabled: c.checked } }); const key = S.keys.find(k => String(k.id) === c.dataset.keytoggle); if (key) key.enabled = c.checked; toast(c.checked ? '已启用' : '已停用', 'ok'); }
    catch (e) { c.checked = !c.checked; toast(e.message, 'err'); }
    finally { c.disabled = false; }
  });
  $$('[data-keydel]').forEach(b => b.onclick = async () => {
    if (!await confirmAction('使用此密钥的客户端将立即失效。', '删除客户端密钥', '删除密钥')) return;
    await withBusy(b, async () => { await api(`/api/keys/${b.dataset.keydel}`, { method: 'DELETE' }); toast('已删除', 'ok'); await loadKeys(); });
  });
  $$('[data-keyedit]').forEach(b => b.onclick = () => openKeyDialog(+b.dataset.keyedit));
  $$('[data-keyconfig]').forEach(b => b.onclick = () => openKeyConnection(+b.dataset.keyconfig));
}

function openKeyDialog(id) {
  S.editingKey = id || null;
  const k = id ? S.keys.find(x => x.id === id) : null;
  $('#key-dlg-title').textContent = k ? '编辑 Key' : '新建 Key';
  $('#key-name').value = k ? k.name : '';
  renderKeyModelGroups(k);
  $('#dlg-key').classList.remove('hidden');
}

$('#btn-add-key').onclick = () => openKeyDialog(null);
$('#btn-key-cancel').onclick = () => $('#dlg-key').classList.add('hidden');
$('#btn-key-save').onclick = async () => {
  const save = $('#btn-key-save');
  if (save.disabled) return;
  const name = $('#key-name').value.trim() || '未命名';
  const all = $('#key-all').checked;
  const models = all ? '*' : $$('#key-models input[value]').filter(c => c.checked).map(c => c.value).join(',');
  if (!all && !models) { toast('请至少选择一个模型，或明确勾选「允许全部模型」', 'err'); return; }
  save.disabled = true;
  try {
    if (S.editingKey) await api(`/api/keys/${S.editingKey}`, { method: 'PATCH', body: { name, models } });
    else await api('/api/keys', { method: 'POST', body: { name, models } });
    $('#dlg-key').classList.add('hidden');
    toast('已保存', 'ok');
    loadKeys();
  } catch (e) { toast(e.message, 'err'); }
  finally { save.disabled = false; }
};

/* ------------------------------------------------------------ 本机客户端 */
const CLIENT_STATUS = {
  ok: { label: '已接入', cls: 'ok' },
  drift: { label: '地址已失效', cls: 'err' },
  conflict: { label: '模型目录冲突', cls: 'err' },
  missing: { label: '未安装', cls: 'off' },
  broken: { label: '配置损坏', cls: 'err' },
};
const CLIENT_ROLES = [
  { id: 'SONNET', label: 'Sonnet' },
  { id: 'OPUS', label: 'Opus' },
  { id: 'HAIKU', label: 'Haiku' },
  { id: 'FABLE', label: 'Fable' },
  { id: 'SUBAGENT', label: '子任务' },
];

function clientKeys() {
  return ((S.clientMeta && S.clientMeta.keys) || []).filter(k => k.enabled !== false);
}
function clientCatalog(key) {
  if (!key) return [];
  const listed = key.model_list || [], all = !listed.length || listed.includes('*');
  return (S.models || []).filter(id => all || listed.includes(id));
}
function clientDraftOf(c) {
  let d = S.clientDraft[c.id];
  if (d) return d;
  const keys = clientKeys();
  const key = keys.find(k => k.id === c.key_id) || keys[0] || null;
  d = S.clientDraft[c.id] = { keyId: key ? key.id : 0, roles: Object.assign({}, c.roles || {}) };
  const catalog = clientCatalog(key);
  d.model = catalog.includes(c.model) ? c.model : (catalog[0] || '');
  for (const role of CLIENT_ROLES) {
    if (!d.roles[role.id] || !catalog.includes(d.roles[role.id])) d.roles[role.id] = d.model;
  }
  return d;
}

function clientCardHtml(c) {
  const st = CLIENT_STATUS[c.status] || CLIENT_STATUS.missing;
  const keys = clientKeys();
  const d = clientDraftOf(c);
  const key = keys.find(k => k.id === d.keyId) || keys[0] || null;
  const catalog = clientCatalog(key);
  if (!catalog.includes(d.model)) d.model = catalog[0] || '';

  const line = (label, value) => `<div class="client-row"><span class="muted">${label}</span><span>${value}</span></div>`;
  let rows = line('配置', `<span class="mono">${esc(c.path)}</span>`)
    + line('当前地址', c.base_url ? `<span class="mono">${esc(c.base_url)}</span>` : '<span class="muted">未配置</span>')
    + line('当前模型', c.model ? `<span class="mono">${esc(c.model)}</span>` : '<span class="muted">未配置</span>');
  if (c.installed && c.base_url && !c.key_known) rows += line('当前 Key', '<span class="err">不在本程序里</span>');

  let warn = '';
  if (c.status === 'drift') {
    warn = `<div class="notice mt16">还指向 <b class="mono">${esc(c.base_url)}</b>，当前网关是 <b class="mono">${esc(S.clientMeta.base)}</b>。写入后即可恢复。</div>`;
  } else if (c.status === 'broken') {
    warn = `<div class="notice mt16">${esc(c.error || '配置文件无法解析')}</div>`;
  }
  if (c.warning) warn += `<div class="notice mt16">${esc(c.warning)}</div>`;

  const keyOptions = keys.map(k => `<option value="${k.id}" ${k.id === d.keyId ? 'selected' : ''}>${esc(k.name)}（${esc(k.model_list.includes('*') ? '全部模型' : k.model_list.length + ' 个模型')}）</option>`).join('');
  const modelOptions = catalog.map(id => `<option value="${esc(id)}" ${id === d.model ? 'selected' : ''}>${esc(id)}</option>`).join('');
  const roleBox = c.id !== 'claude-code' ? '' : `<details class="client-roles"><summary>各档位模型（可选）</summary><div class="client-role-grid">${CLIENT_ROLES.map(role => `
    <div class="field"><label>${role.label}</label><select data-cl-role="${role.id}" data-cid="${c.id}">${catalog.map(id => `<option value="${esc(id)}" ${id === d.roles[role.id] ? 'selected' : ''}>${esc(id)}</option>`).join('')}</select></div>`).join('')}</div></details>`;

  return `<div class="client-card" data-client="${c.id}">
    <div class="client-head"><div><strong>${esc(c.label)}</strong><p class="muted tiny">${esc(c.note)}</p></div><span class="badge ${st.cls}">${st.label}</span></div>
    <div class="client-rows">${rows}</div>
    ${warn}
    <div class="field mt16"><label>使用的 Key</label><select data-cl-key="${c.id}" ${keys.length ? '' : 'disabled'}>${keyOptions || '<option value="">先去新建一个 Key</option>'}</select></div>
    <div class="field"><label>默认模型</label><select data-cl-model="${c.id}" ${catalog.length ? '' : 'disabled'}>${modelOptions || '<option value="">这把 Key 没有可用模型</option>'}</select></div>
    ${roleBox}
    <button class="btn primary client-apply" data-cl-apply="${c.id}" ${catalog.length ? '' : 'disabled'}>${c.installed ? '写入并切换' : '创建配置'}</button>
  </div>`;
}

function renderClients() {
  const meta = S.clientMeta;
  if (!meta) return;
  $('#client-list').innerHTML = meta.clients.map(clientCardHtml).join('');
  $$('[data-cl-key]').forEach(sel => {
    sel.onchange = () => {
      const cid = sel.dataset.clKey, d = S.clientDraft[cid], c = meta.clients.find(x => x.id === cid);
      d.keyId = Number(sel.value);
      const key = clientKeys().find(k => k.id === d.keyId);
      const catalog = clientCatalog(key);
      d.model = catalog.includes(d.model) ? d.model : (catalog[0] || '');
      for (const role of CLIENT_ROLES) if (!catalog.includes(d.roles[role.id])) d.roles[role.id] = d.model;
      renderClients();
    };
  });
  $$('[data-cl-model]').forEach(sel => {
    sel.onchange = () => {
      const cid = sel.dataset.clModel, d = S.clientDraft[cid];
      d.model = sel.value;
      for (const role of CLIENT_ROLES) d.roles[role.id] = sel.value;
      const c = meta.clients.find(x => x.id === cid);
      if (c && c.id === 'claude-code') {
        const box = sel.closest('.client-card').querySelector('.client-roles');
        $$('[data-cl-role]', sel.closest('.client-card')).forEach(r => { r.value = sel.value; });
        if (box) box.open = true;
      }
    };
  });
  $$('[data-cl-role]').forEach(sel => {
    sel.onchange = () => { S.clientDraft[sel.dataset.cid].roles[sel.dataset.clRole] = sel.value; };
  });
  $$('[data-cl-apply]').forEach(btn => {
    btn.onclick = async () => {
      const cid = btn.dataset.clApply, c = meta.clients.find(x => x.id === cid), d = S.clientDraft[cid];
      const key = clientKeys().find(k => k.id === d.keyId);
      btn.disabled = true; btn.classList.add('busy');
      try {
        const res = await api(`/api/clients/${cid}/apply`, {
          method: 'POST',
          body: { key_id: d.keyId, model: d.model, catalog: clientCatalog(key), roles: cid === 'claude-code' ? d.roles : {} },
        });
        S.clientMeta.clients = res.clients;
        renderClients();
        const name = String(res.backup || '').split(/[\\/]/).pop();
        const disabled = (res.disabled || []).length ? `已停用其它工具设置的 ${res.disabled.join('、')}（会覆盖号池模型目录），原值已注释保留。` : '';
        toast(`${c.label} 已写入${name ? '，原文件备份 ' + name : ''}。${disabled}重启客户端后生效。`, 'ok', disabled ? 12000 : 6000);
      } catch (e) {
        toast(e.message, 'err', 6000);
      } finally {
        btn.disabled = false; btn.classList.remove('busy');
      }
    };
  });
}

async function loadClients(reset = true) {
  const btn = $('#btn-clients-refresh');
  if (btn) btn.onclick = () => loadClients(true).catch(e => toast(e.message, 'err', 5000));
  if (!(S.models || []).length) {
    try { S.models = (await api('/api/models')).exposed || []; } catch (e) { /* 目录拿不到时仍显示客户端状态 */ }
  }
  if (reset) S.clientDraft = {};
  S.clientMeta = await api('/api/clients');
  renderClients();
}


/* ------------------------------------------------------------ 日志 */
async function loadLogs() {
  try {
    const d = await api('/api/logs?limit=60');
    const tb = $('#tb-logs');
    if (!d.logs.length) { tb.innerHTML = `<tr><td colspan="8" class="empty">还没有请求</td></tr>`; }
    else {
      tb.innerHTML = d.logs.map(l => {
        const cls = l.status >= 200 && l.status < 400 ? 'ok' : (l.status >= 500 ? 'err' : 'warn');
        return `<tr>
          <td class="mono">${esc(l.time)}</td><td>${esc(l.name || '—')}</td>
          <td class="muted tiny">${esc(l.account || '—')}</td>
          <td class="mono">${esc(l.model || '—')}</td><td class="mono muted">${esc(l.path)}</td>
          <td><span class="badge ${cls}">${l.status}</span></td>
          <td class="muted">${l.ms} ms</td><td class="muted tiny">${esc(l.note || '')}</td>
        </tr>`;
      }).join('');
    }
    $('#pool-log').textContent = (d.pool && d.pool.length) ? d.pool.join('\n') : '（暂无输出）';
  } catch (e) { toast(e.message, 'err'); }
}
$('#btn-logs-refresh').onclick = loadLogs;
$('#btn-logs-clear').onclick = async () => { if (!await confirmAction('清空后将删除全部请求记录和统计数据，此操作无法撤销。', '清空请求日志', '清空日志')) return; await withBusy($('#btn-logs-clear'), async () => { await api('/api/logs', { method: 'DELETE' }); toast('已清空', 'ok'); await loadLogs(); await refreshSide(); }); };

/* ------------------------------------------------------------ 设置 */
async function ensureSettings(force) {
  if (S.settings && !force) return S.settings;
  S.settings = await api('/api/settings');
  S.pools = S.settings.pools || {};
  return S.settings;
}

async function loadSettings() {
  const s = await ensureSettings(true);
  $('#f-ui-port').value = s.ui_port;
  $('#f-pool-port').value = s.pool_port;
  $('#f-proxy').value = s.proxy_url || '';
  $('#f-admin-password').value = s.admin_password || '';
  $('#f-autostart').checked = !!s.autostart;
  const fallback = s.plus_fallback || {};
  $('#f-plus-fallback-enabled').checked = !!fallback.enabled;
  $('#f-plus-fallback-interval').value = fallback.check_interval || 30;
  S.settingsDirty = false;
  await loadPlusFallback(true);
  await loadPlusPriority(true);
}

function paintPlusPriority() {
  const data = S.plusPriority;
  if (!data) return;
  const meta = data.account_priority || data;
  const rows = meta.accounts || [];
  $('#f-plus-priority-enabled').checked = meta.enabled !== false;
  const labels = {five_hour: '5 小时窗口', reserve: '无 5 小时窗口', unknown: '额度未知'};
  const states = {available: '可用', exhausted: '已限额', unknown: '未知'};
  const selected = meta.selected || '';
  $('#plus-priority-summary').textContent = !rows.length ? '没有可配置的 Plus 账号。'
    : `当前优先组：${rows.find(a => a.file === selected)?.group ? labels[rows.find(a => a.file === selected).group] : '等待额度'} · 当前账号：${rows.find(a => a.file === selected)?.email || '未选择'}。额度未知不会切换到后备组。`;
  $('#plus-priority-list').innerHTML = rows.map((a, i) => `<div class="priority-row" data-priority-file="${esc(a.file)}">
    <span class="priority-index">${i + 1}</span><div class="priority-main"><strong>${esc(a.email)}</strong><span class="muted tiny">${esc(labels[a.group] || a.group)} · ${esc(states[a.quota] || a.quota)}</span></div>
    <select class="mini" data-priority-group="${esc(a.file)}" aria-label="${esc(a.email)}额度分组"><option value="auto" ${a.override === 'auto' ? 'selected' : ''}>自动识别</option><option value="five_hour" ${a.override === 'five_hour' ? 'selected' : ''}>有 5 小时窗口</option><option value="reserve" ${a.override === 'reserve' ? 'selected' : ''}>无 5 小时窗口</option></select>
    <button class="btn tiny ghost" type="button" data-priority-up="${esc(a.file)}" ${i ? '' : 'disabled'}>上移</button><button class="btn tiny ghost" type="button" data-priority-down="${esc(a.file)}" ${i === rows.length - 1 ? 'disabled' : ''}>下移</button>
  </div>`).join('') || '<p class="muted tiny">先登录 Plus 账号。</p>';
  $$('[data-priority-group]').forEach(el => el.onchange = () => { const a = rows.find(x => x.file === el.dataset.priorityGroup); if (a) a.override = el.value; S.plusPriorityDirty = true; });
  $$('[data-priority-up],[data-priority-down]').forEach(btn => btn.onclick = () => {
    const i = rows.findIndex(a => a.file === (btn.dataset.priorityUp || btn.dataset.priorityDown));
    const j = btn.dataset.priorityUp ? i - 1 : i + 1;
    if (i < 0 || j < 0 || j >= rows.length) return;
    [rows[i], rows[j]] = [rows[j], rows[i]]; meta.order = rows.map(a => a.file); S.plusPriorityDirty = true; paintPlusPriority();
  });
}

async function loadPlusPriority(force = false) {
  const btn = $('#btn-plus-priority-refresh'); if (btn) btn.disabled = true;
  try { S.plusPriority = await api('/api/plus-priority' + (force ? '?refresh=1' : '')); paintPlusPriority(); }
  catch (e) { $('#plus-priority-summary').textContent = '账号优先级读取失败：' + e.message; }
  finally { if (btn) btn.disabled = false; }
}
$('#btn-plus-priority-refresh').onclick = () => loadPlusPriority(true);
$('#f-plus-priority-enabled').onchange = () => { S.plusPriorityDirty = true; };
$('#btn-plus-priority-save').onclick = async () => {
  const data = S.plusPriority || {}, meta = data.account_priority || data, rows = meta.accounts || [];
  try {
    const r = await api('/api/plus-priority', {method: 'PUT', body: {order: rows.map(a => a.file), plus_priority: {enabled: $('#f-plus-priority-enabled').checked, groups: Object.fromEntries(rows.map(a => [a.file, a.override || 'auto']))}}});
    S.plusPriorityDirty = false; S.plusPriority = {...data, account_priority: {...meta, order: r.order}}; toast('Plus 账号优先级已保存', 'ok');
  } catch (e) { toast(e.message, 'err'); }
};

function fallbackDraft() {
  const models = {};
  $$('[data-fallback-source]').forEach(sel => { if (sel.value) models[sel.dataset.fallbackSource] = sel.value; });
  return { enabled: $('#f-plus-fallback-enabled').checked,
    check_interval: Number($('#f-plus-fallback-interval').value), models };
}

function paintFallbackPaths() {
  const data = S.fallbackMeta;
  if (!data) return;
  const snap = data.snapshot;
  const draft = fallbackDraft(), saved = data.config;
  const pending = JSON.stringify(draft) !== JSON.stringify(saved);
  $$('[data-fallback-source]').forEach(sel => {
    const block = sel.closest('.fallback-path');
    const mode = !draft.enabled ? 'disabled' : (pending ? 'pending' : snap.state);
    const fallback = mode === 'fallback' && sel.value;
    const invalid = fallback && !data.targets.some(t => t.id === sel.value);
    const label = invalid ? '备用不可用' : (!draft.enabled ? '未启用' : pending ? '待保存' : fallback ? '代理商备用' : mode === 'unknown' ? '额度待确认' : mode === 'empty' ? '无 Plus 账号' : 'Plus 优先');
    const badge = block.querySelector('[data-fallback-badge]');
    badge.className = 'badge ' + (fallback || invalid ? 'warn' : '');
    badge.textContent = label;
    const actual = block.querySelector('[data-fallback-actual]');
    actual.textContent = (fallback && !invalid ? sel.value : sel.dataset.fallbackSource);
    const restore = block.querySelector('[data-fallback-restore]');
    restore.textContent = sel.value ? 'Plus 额度恢复 → 自动回到 ' + sel.dataset.fallbackSource : '未设置备用，保持 Plus 路径';
  });
  const checked = snap.checked_at ? ' · 更新于 ' + new Date(snap.checked_at * 1000).toLocaleTimeString('zh-CN', {hour12: false}) : '';
  $('#fallback-summary').textContent = `${snap.reason} · 启用 ${snap.total} 个 / 可用 ${snap.available} 个 / 限额 ${snap.exhausted} 个 / 未知 ${snap.unknown} 个${checked}${pending ? ' · 修改尚未保存' : ''}`;
}

async function loadPlusFallback(render = false, force = false) {
  const btn = $('#btn-fallback-refresh');
  btn.disabled = true;
  S.fallbackStatusAt = Date.now();
  try {
    const data = await api('/api/plus-fallback' + (force ? '?refresh=1' : ''));
    S.fallbackMeta = data;
    if (render) {
      $('#fallback-paths').innerHTML = data.sources.map(src => {
        const selected = (data.config.models || {})[src.id] || '';
        const missing = selected && !data.targets.some(t => t.id === selected);
        const options = '<option value="">不使用备用</option>'
          + (missing ? `<option value="${esc(selected)}" selected>${esc(selected)}（已不可用）</option>` : '')
          + data.targets.map(t => `<option value="${esc(t.id)}" ${selected === t.id ? 'selected' : ''}>${esc(t.display)} · ${esc(t.id)}</option>`).join('');
        return `<div class="fallback-path"><div class="fallback-path-head"><strong>${esc(src.display)}</strong><span class="badge" data-fallback-badge></span></div>
          <div class="fallback-flow"><span class="mono">${esc(src.id)}</span><span class="muted">全部限额 →</span><select data-fallback-source="${esc(src.id)}" aria-label="${esc(src.display)} 的备用模型">${options}</select></div>
          <p class="tiny">当前路径：<span class="mono" data-fallback-actual></span></p><p class="muted tiny" data-fallback-restore></p></div>`;
      }).join('') || '<p class="muted tiny">先在模型配置中启用 Plus 池并添加模型。</p>';
      $$('[data-fallback-source]').forEach(sel => { sel.onchange = () => { S.settingsDirty = true; paintFallbackPaths(); }; });
    }
    paintFallbackPaths();
  } catch (e) {
    $('#fallback-summary').textContent = '路由状态读取失败：' + e.message;
    if (render) $('#fallback-paths').innerHTML = '<p class="err tiny">备用配置未加载，请重新检查后再保存。</p>';
    S.fallbackMeta = null;
  } finally { btn.disabled = false; }
}
$('#btn-fallback-refresh').onclick = () => loadPlusFallback(!S.fallbackMeta, true);
$('#f-plus-fallback-enabled').onchange = () => { S.settingsDirty = true; paintFallbackPaths(); };
$('#f-plus-fallback-interval').oninput = paintFallbackPaths;

$('#btn-save-settings').onclick = async () => {
  const body = {
    ui_port: +$('#f-ui-port').value,
    pool_port: +$('#f-pool-port').value,
    proxy_url: $('#f-proxy').value.trim(),
    admin_password: $('#f-admin-password').value.trim(),
    autostart: $('#f-autostart').checked,
  };
  if (S.fallbackMeta) {
    body.plus_fallback = fallbackDraft();
    const f = body.plus_fallback;
    if (!Number.isInteger(f.check_interval) || f.check_interval < 15 || f.check_interval > 300) { toast('额度检查间隔必须是 15–300 秒的整数', 'err'); return; }
    if (f.enabled && !Object.keys(f.models).length) { toast('请至少选择一条 Plus 备用路径', 'err'); return; }
  } else if ($('#f-plus-fallback-enabled').checked) { toast('请先成功加载备用路由，再保存设置', 'err'); return; }
  if (![body.ui_port, body.pool_port].every(p => Number.isInteger(p) && p >= 1024 && p <= 65535)) { toast('端口必须是 1024–65535 之间的整数', 'err'); return; }
  if (body.ui_port === body.pool_port) { toast('界面端口和内部号池端口不能相同', 'err'); return; }
  const previous = S.settings || {};
  const restarts = body.pool_port !== previous.pool_port || body.proxy_url !== (previous.proxy_url || '');
  if (restarts && !await confirmAction('这些设置会重启号池，并中断正在生成的回答。', '保存网络设置', '保存并重启')) return;
  const save = $('#btn-save-settings'); if (save.disabled) return; save.disabled = true;
  try {
    const r = await api('/api/settings', { method: 'PUT', body });
    S.settingsDirty = false;
    $('#save-hint').textContent = r.restart_ui ? '已保存。界面端口将在退出并重新启动程序后生效。' : (r.restarted ? '已保存，号池已重启' : '已保存');
    toast('设置已保存', 'ok');
    await ensureSettings(true);
    loadSettings();
    loadOverview();
  } catch (e) { toast(e.message, 'err'); }
  finally { save.disabled = false; }
};

$('#btn-goto-providers').onclick = () => switchPage('providers');

$('#btn-proxy-test').onclick = async () => {
  const btn = $('#btn-proxy-test');
  btn.disabled = true; btn.textContent = '测试中';
  try {
    const r = await api('/api/settings/proxy-test', { method: 'POST', body: { proxy_url: $('#f-proxy').value.trim() } });
    toast(r.msg, r.ok ? 'ok' : 'err');
  } catch (e) { toast(e.message, 'err'); }
  btn.disabled = false; btn.textContent = '测试';
};

$('#btn-gen-password').onclick = () => {
  const chars = 'abcdefghjkmnpqrstuvwxyz23456789';
  let s = '';
  for (let i = 0; i < 10; i++) s += chars[Math.floor(Math.random() * chars.length)];
  $('#f-admin-password').value = s;
  toast('已生成，记得点保存', 'ok');
};

$('#btn-open-folder').onclick = async () => {
  await withBusy($('#btn-open-folder'), async () => {
    const r = await api('/api/system/open-folder', { method: 'POST' });
    toast((r.opened === false ? '数据目录：' : '已打开：') + r.dir, 'ok');
  });
};
$('#btn-quit').onclick = async () => {
  if (!await confirmAction('退出后客户端将无法使用，需要重新双击程序启动。', '退出程序', '退出程序')) return;
  await withBusy($('#btn-quit'), async () => {
    await api('/api/system/quit', { method: 'POST' });
    document.body.innerHTML = '<div style="padding:60px;text-align:center;font:16px sans-serif;color:#6b7280">程序已退出，可以关闭这个页面。</div>';
  });
};

/* ------------------------------------------------------------ 登录 & 启动 */
$('#login-submit').onclick = async () => {
  try {
    await api('/api/login', { method: 'POST', body: { password: $('#login-password').value } });
    $('#login-mask').classList.add('hidden');
    boot();
  } catch (e) { toast('口令不正确', 'err'); }
};

document.addEventListener('click', e => {
  const c = e.target.closest('[data-copy]');
  if (c) { const el = $(c.dataset.copy); if (el) copy(el.value); }

  const r = e.target.closest('[data-reset]');
  if (r) openReset(r.dataset.reset);
});

async function boot() {
  try {
    const w = await api('/api/whoami');
    if (!w.allowed) {
      $('#login-mask').classList.remove('hidden');
      $('#login-submit').disabled = !w.password_required;
      $('#login-password').disabled = !w.password_required;
      $('#login-mask .muted').textContent = w.password_required ? '请输入安装时设置的管理口令。' : '当前仅允许本机管理。请先在安装此程序的电脑上设置管理口令，再从其他设备访问。';
      return;
    }
    $('#login-mask').classList.add('hidden');
    await loadKeys().catch(() => { });
    await api('/api/providers').then(d => { S.providers = d.providers || []; }).catch(() => { });
    const h = (location.hash || '').replace('#', '');
    await switchPage(TITLES[h] ? h : 'overview', { replace: true });
    clearInterval(S.sideTimer); S.sideTimer = setInterval(refreshSide, 6000);
  } catch (e) {
    if (String(e.message).includes('未登录')) return;
    toast(e.message, 'err');
  }
}

function closeSidebar() {
  document.body.classList.remove('sidebar-open');
  $('#btn-menu').setAttribute('aria-expanded', 'false');
}
$('#btn-menu').onclick = () => {
  const open = document.body.classList.toggle('sidebar-open');
  $('#btn-menu').setAttribute('aria-expanded', String(open));
};
$('#sidebar-scrim').onclick = closeSidebar;

function paintTheme() {
  const dark = document.documentElement.dataset.theme === 'dark';
  const button = $('#btn-theme');
  button.dataset.icon = dark ? 'sun' : 'moon';
  button.setAttribute('aria-label', dark ? '切换浅色模式' : '切换深色模式');
  button.title = button.getAttribute('aria-label');
  paintIcons(button);
}
$('#btn-theme').onclick = () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('aipool-theme', theme); } catch (e) { }
  paintTheme();
};
$('#btn-reveal-key').onclick = () => {
  const key = $('#c-key');
  key.type = key.type === 'password' ? 'text' : 'password';
  $('#btn-reveal-key').setAttribute('aria-label', key.type === 'password' ? '显示密钥' : '隐藏密钥');
};
$('#page-settings').addEventListener('input', () => { S.settingsDirty = true; });
window.addEventListener('beforeunload', e => {
  if (S.modelsDirty || S.settingsDirty) { e.preventDefault(); e.returnValue = ''; }
});
$('#login-password').addEventListener('keydown', e => { if (e.key === 'Enter') $('#login-submit').click(); });
document.addEventListener('click', e => {
  const button = e.target.closest('[data-dialog-cancel]');
  if (button) $('#' + button.dataset.dialogCancel).click();
});
// Keep focus in the topmost dialog and return it to the triggering control.
let activeDialog = null, dialogTrigger = null;
$$('.mask').forEach(mask => {
  const dialog = $('.dialog', mask), heading = $('h3', dialog);
  dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); dialog.tabIndex = -1;
  if (heading) { if (!heading.id) heading.id = mask.id + '-title'; dialog.setAttribute('aria-labelledby', heading.id); }
});
const modalObserver = new MutationObserver(() => {
  const open = $$('.mask:not(.hidden)').at(-1) || null;
  if (open === activeDialog) return;
  if (!activeDialog && open) dialogTrigger = document.activeElement;
  activeDialog = open;
  $('.shell').inert = !!open;
  if (open) {
    const focusable = $$('input:not(:disabled), select:not(:disabled), button:not(:disabled)', open).find(el => !el.closest('.hidden') && !el.classList.contains('dialog-close'));
    (focusable || $('.dialog', open)).focus();
  } else if (dialogTrigger?.isConnected) { dialogTrigger.focus(); dialogTrigger = null; }
});
$$('.mask').forEach(mask => modalObserver.observe(mask, { attributes: true, attributeFilter: ['class'] }));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (confirmation) settleConfirmation(false);
    else if (activeDialog) $('[data-dialog-cancel]', activeDialog)?.click();
    else closeSidebar();
  }
  if (e.key === 'Tab' && activeDialog) {
    const items = $$('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), [tabindex="0"]', activeDialog).filter(el => !el.closest('.hidden') && el.getClientRects().length);
    if (!items.length) { e.preventDefault(); $('.dialog', activeDialog).focus(); return; }
    if (e.shiftKey && (document.activeElement === items[0] || !activeDialog.contains(document.activeElement))) { e.preventDefault(); items.at(-1).focus(); }
    else if (!e.shiftKey && (document.activeElement === items.at(-1) || !activeDialog.contains(document.activeElement))) { e.preventDefault(); items[0].focus(); }
  }
});
paintIcons(); paintTheme(); boot();

})();
