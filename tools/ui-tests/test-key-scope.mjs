import { JSDOM,VirtualConsole } from 'jsdom';
import { fixtures } from './fixtures.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../web'),real=JSON.parse(fs.readFileSync(new URL('./key-scope-fixtures.json',import.meta.url),'utf8')),base=fixtures();
base.settings.pools=real.models.pools;base.providers=real.providers;base.models=real.models.exposed;
base.accounts=real.accounts.map(({quota,...a})=>a);base.usage=Object.fromEntries(real.accounts.map(a=>[a.file,a.quota]));
base.keys=[{id:1,name:'All devices',secret:'qk-DEMO-ALL',enabled:true,model_list:['*']},{id:2,name:'Limited device',secret:'qk-DEMO-LIMITED',enabled:true,model_list:['plus-gpt-6-astra','gemini-claude-sonnet-4-6','temporarily-unavailable']}];
const errors=[],requests=[],results=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(root+'/index.html','utf8'),{url:'http://127.0.0.1:19366/#keys',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
const w=dom.window,d=w.document,q=s=>d.querySelector(s),qa=s=>[...d.querySelectorAll(s)];let copied='';
d.execCommand=()=>{copied=d.activeElement.value;return true;};
w.fetch=async(url,opt={})=>{
 const p=String(url).split('?')[0],method=opt.method||'GET',body=opt.body?JSON.parse(opt.body):null;requests.push({p,method,body});let data={ok:true};
 if(p==='/api/whoami')data={allowed:true};
 else if(p==='/api/overview')data=base.overview;
 else if(p==='/api/settings')data=base.settings;
 else if(p==='/api/accounts')data={accounts:base.accounts};
 else if(p==='/api/usage')data={usage:base.usage};
 else if(p==='/api/runtime/accounts')data={accounts:[]};
 else if(p==='/api/providers')data={providers:base.providers};
 else if(p==='/api/models')data={models:base.models,exposed:base.models,pools:base.settings.pools};
 else if(p==='/api/keys'&&method==='GET')data={keys:base.keys};
 else if(p.startsWith('/api/keys/')&&method==='PATCH'){const key=base.keys.find(k=>k.id===Number(p.split('/').at(-1)));if(body.models)key.model_list=body.models.split(',');Object.assign(key,body);}
 return{status:200,ok:true,text:async()=>JSON.stringify(data)};
};
async function wait(fn,label){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,5));}throw Error('Timed out: '+label);}
async function nav(page){q('[data-page="'+page+'"]').click();await wait(()=>!q('#page-'+page).classList.contains('hidden'),'navigate');await new Promise(r=>setImmediate(r));}
async function test(name,fn){try{await fn();results.push({name,ok:true});process.stdout.write('PASS '+name+'\n');}catch(e){results.push({name,ok:false,error:e.message});process.stdout.write('FAIL '+name+' '+e.message+'\n');}}
function groupIndex(label){return qa('.key-group-head').findIndex(h=>h.textContent.includes(label));}
function modelInput(id){return qa('#key-models input[data-key-model]').find(x=>x.value===id);}
try{
 w.eval(fs.readFileSync(root+'/app.js','utf8'));await wait(()=>qa('[data-keyedit]').length===2,'keys');
 await test('no separate catalog workflow and no nested buttons',()=>{assert.equal(q('#catalog-panel'),null);assert.equal(qa('button button').length,0);assert.ok(q('[data-keyconfig]'));});
 await test('oversized Codex catalog cannot be copied until the Key selection is reduced',()=>{q('[data-keyconfig="1"]').click();assert.equal(q('#btn-connect-copy').disabled,true);assert.ok(q('#key-connect-config').textContent.includes('减少到 20 个以内'));q('#btn-connect-cancel').click();});
 await test('source groups include Plus Pro Google Gemini Claude GPT-OSS and providers',()=>{q('[data-keyedit="1"]').click();const text=q('#key-models').textContent;for(const label of ['ChatGPT Plus','ChatGPT Pro','Google · Gemini','Google · Claude','Google · GPT-OSS'])assert.ok(text.includes(label),label);assert.ok(groupIndex(base.providers[0].label)>=0);assert.ok(q('#key-scope-status').textContent.includes('超过')||q('#key-scope-status').textContent.includes('最多显示'));});
 await test('clearing selection and group selection produce the same individual whitelist',()=>{q('#key-select-none').click();assert.equal(q('#key-all').checked,false);assert.ok(qa('#key-models input[data-key-model]').every(x=>!x.checked&&!x.disabled));const i=groupIndex('ChatGPT Plus');q('[data-key-group="'+i+'"]').click();const ids=qa('#key-models input[data-key-model]:checked').map(x=>x.value);assert.deepEqual(ids,base.settings.pools.plus.models.map(m=>m.alias));});
 await test('individual removal makes its group indeterminate',()=>{const id=base.settings.pools.plus.models[0].alias;modelInput(id).click();assert.equal(q('[data-key-group="'+groupIndex('ChatGPT Plus')+'"]').indeterminate,true);assert.equal(modelInput(id).checked,false);});
 await test('different groups can be combined and saved without settings or pool writes',async()=>{const i=groupIndex('Google · Claude');q('[data-key-group="'+i+'"]').click();const expected=qa('#key-models input[data-key-model]:checked').map(x=>x.value);const start=requests.length;q('#btn-key-save').click();await wait(()=>q('#dlg-key').classList.contains('hidden'),'save key');const writes=requests.slice(start).filter(r=>r.method!=='GET');assert.equal(writes.length,1);assert.equal(writes[0].p,'/api/keys/1');assert.deepEqual(writes[0].body.models.split(','),expected);assert.ok(!expected.includes('pro-gpt-6.1-sol'));});
 await test('saved whitelist and connection config use the same Key and catalog endpoint',async()=>{await wait(()=>q('[data-keyconfig="1"]'),'key row');q('[data-keyconfig="1"]').click();assert.ok(q('#key-connect-scope').textContent.includes('授权'));assert.ok(!q('#key-connect-config').textContent.includes('qk-DEMO-ALL'));q('#key-connect-base').value='http://127.0.0.1:18319/v1';q('#key-connect-base').dispatchEvent(new w.Event('input'));q('#btn-connect-copy').click();assert.ok(copied.includes('experimental_bearer_token = "qk-DEMO-ALL"'));assert.ok(copied.includes('model_catalog_url = "http://127.0.0.1:18319/v1/models"'));assert.ok(!copied.includes('?groups='));assert.ok(copied.includes('model = "plus-gpt-6-astra"'));q('#btn-connect-cancel').click();});
 await test('invalid client address disables copy rather than emitting malformed config',()=>{q('[data-keyconfig="2"]').click();q('#key-connect-base').value='javascript:bad';q('#key-connect-base').dispatchEvent(new w.Event('input'));assert.equal(q('#btn-connect-copy').disabled,true);q('#btn-connect-cancel').click();});
 await test('unavailable pre-existing permission is retained',()=>{q('[data-keyedit="2"]').click();assert.ok(modelInput('temporarily-unavailable').checked);assert.ok(q('#key-models').textContent.includes('暂未在号池列出'));q('#btn-key-cancel').click();});
 await test('changing back from allow-all restores the selected subset',()=>{q('[data-keyedit="2"]').click();const before=qa('#key-models input[data-key-model]:checked').map(x=>x.value);q('#key-all').click();assert.ok(qa('#key-models input[data-key-model]').every(x=>x.checked&&x.disabled));q('#key-all').click();assert.deepEqual(qa('#key-models input[data-key-model]:checked').map(x=>x.value),before);q('#btn-key-cancel').click();});
 await test('empty whitelist is rejected before an API write',async()=>{q('#btn-add-key').click();q('#key-select-none').click();const count=requests.length;q('#btn-key-save').click();await new Promise(r=>setImmediate(r));assert.ok(q('#toast').textContent.includes('至少选择一个模型'));assert.equal(requests.length,count);q('#btn-key-cancel').click();});
 await nav('accounts');await wait(()=>qa('[data-ucell]').length===base.accounts.length,'account quotas');
 await test('ChatGPT displays a separate weekly reset timestamp including a primary weekly window',()=>{let weekly=0;for(const a of base.accounts.filter(a=>a.provider==='codex')){const quota=base.usage[a.file];const windows=[quota.primary,quota.secondary].filter(Boolean);if(windows.some(x=>x.window_seconds===604800)){const cell=qa('[data-ucell]').find(x=>x.dataset.ucell===a.file);assert.ok(cell.textContent.includes('周额度重置'));const dates=cell.querySelectorAll('time[datetime]');assert.ok(dates.length>=windows.length);for(const win of windows){if(win.reset_at)assert.ok([...dates].some(t=>t.dateTime===new Date(win.reset_at*1000).toISOString()));}weekly++;}}assert.ok(weekly>=2);});
 await test('Gemini default view is three compact meters with details collapsed',()=>{for(const a of base.accounts.filter(a=>a.provider==='gemini')){const cell=qa('[data-ucell]').find(x=>x.dataset.ucell===a.file);assert.equal(cell.querySelectorAll('.quota-meter').length,3);const detail=cell.querySelector('details');assert.equal(detail.open,false);const visible=[...cell.querySelector('.quota-compact').children].filter(e=>e.tagName!=='DETAILS').map(e=>e.textContent).join('');assert.ok(!visible.includes('缓存'));assert.ok(!visible.includes('周限额未返回'));assert.ok(!visible.includes('更新于'));assert.ok(!visible.includes('最低'));assert.ok(cell.textContent.includes('Google 未提供'));}});
 await test('Gemini details remain available on click',()=>{const detail=q('.quota-details');detail.open=true;assert.ok(detail.querySelector('.quota-detail-body').textContent.includes('Claude'));assert.ok(detail.querySelector('time'));});
 await test('zero or unknown resets are not presented as a fake future date',async()=>{const a=base.accounts.find(a=>a.provider==='codex');base.usage[a.file]={ok:true,primary:{window_seconds:604800,label:'7 天',left:0,reset_at:0,reset_after:0}};q('#btn-refresh-usage').click();await wait(()=>!q('#btn-refresh-usage').disabled,'quota refresh');const cell=qa('[data-ucell]').find(x=>x.dataset.ucell===a.file);assert.ok(cell.textContent.includes('0%'));assert.ok(cell.textContent.includes('未提供时间'));assert.equal(cell.querySelector('time').hasAttribute('datetime'),false);});
 await test('no uncaught script errors',()=>assert.deepEqual(errors,[]));
}finally{w.close();}

process.stdout.write(results.filter(r=>r.ok).length+'/'+results.length+' merged Key and quota checks passed\n');
if(results.some(r=>!r.ok))process.exitCode=1;
