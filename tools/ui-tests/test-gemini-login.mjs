import { JSDOM, VirtualConsole } from 'jsdom';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { fixtures } from './fixtures.mjs';

const root=path.resolve(process.env.GEMINI_UI_ROOT || path.join(path.dirname(fileURLToPath(import.meta.url)), '../../web'));
const state=fixtures();
const requests=[],errors=[],results=[];
let failStart=false,pollFailed=false,finish=false;
const virtualConsole=new VirtualConsole();
virtualConsole.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://127.0.0.1:19366/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
const w=dom.window,d=w.document,q=s=>d.querySelector(s),qa=s=>[...d.querySelectorAll(s)];
w.fetch=async(url,opt={})=>{
  const p=String(url).split('?')[0],method=opt.method || 'GET';
  const body=opt.body?JSON.parse(opt.body):null;
  requests.push({p,method,body});
  let data={ok:true};
  if(p==='/api/whoami')data={allowed:true,local:true};
  else if(p==='/api/overview')data=state.overview;
  else if(p==='/api/settings')data=state.settings;
  else if(p==='/api/keys')data={keys:state.keys};
  else if(p==='/api/models')data={models:state.models,exposed:state.models,pools:state.settings.pools};
  else if(p==='/api/accounts')data={accounts:state.accounts};
  else if(p==='/api/providers')data={providers:state.providers};
  else if(p==='/api/usage')data={usage:state.usage};
  else if(p==='/api/runtime/accounts')data={accounts:[]};
  else if(p==='/api/accounts/login'&&method==='POST')data=failStart?{ok:false,error:'模拟登录进程启动失败'}:{ok:true,session:body.provider+'-test-session',provider:body.provider};
  else if(p.startsWith('/api/accounts/login/')&&method==='GET'){
    const provider=p.includes('gemini')?'gemini':'codex';
    data={ok:true,status:pollFailed?'failed':finish?'done':'waiting',provider,url:provider==='gemini'?'https://accounts.google.com/o/oauth2/v2/auth?state=test-only':'https://auth.openai.com/codex/device',code:provider==='codex'?'ABCD-1234':'',error:pollFailed?'模拟登录失败':'',log:[]};
  }
  return{status:200,ok:true,text:async()=>JSON.stringify(data)};
};
w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));
async function wait(fn,label){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,5));}throw Error('Timed out: '+label);}
async function open(){q('#btn-add-account').click();await wait(()=>!q('#dlg-account').classList.contains('hidden'),'open account dialog');}
async function close(){q('#btn-login-close').click();await wait(()=>q('#dlg-account').classList.contains('hidden'),'close account dialog');await new Promise(r=>setImmediate(r));}
async function test(name,fn){try{await fn();results.push({name,ok:true});process.stdout.write('PASS '+name+'\n');}catch(error){results.push({name,ok:false,error:error.message});process.stdout.write('FAIL '+name+': '+error.message+'\n');}}
try{
  await wait(()=>q('#s-accounts').textContent.includes('3'),'boot');
  await test('provider buttons remain inside a non-button group',()=>{assert.equal(q('#provider-chips').tagName,'DIV');assert.equal(qa('#provider-chips button.chip').length,2);assert.equal(q('#pool-chips').tagName,'DIV');assert.equal(qa('button button').length,0);});
  await open();
  await test('Gemini click selects the Google provider and Gemini pool',()=>{q('[data-provider="gemini"]').click();assert.equal(q('#provider-chips .chip.active')?.dataset.provider,'gemini');assert.equal(q('#pool-chips .chip.active')?.dataset.pool,'gemini');assert.equal(qa('#pool-chips .chip:not(.hidden)').length,1);});
  await test('Gemini starts with the correct request and Google authorization link',async()=>{q('#btn-login-start').click();await wait(()=>q('#device-link').href.startsWith('https://accounts.google.com/'),'Google link');const request=requests.filter(r=>r.p==='/api/accounts/login'&&r.method==='POST').at(-1);assert.equal(request.body.provider,'gemini');assert.equal(request.body.pool,'gemini');assert.equal(q('#device-code').classList.contains('hidden'),true);assert.equal(q('#callback-box').classList.contains('hidden'),false);assert.ok(q('#login-state').textContent.includes('Google'));});
  await test('manual callback belongs to the Gemini session',async()=>{q('#callback-url').value='http://localhost:18450/oauth-callback?code=test-only&state=test-only';q('#btn-callback-submit').click();await wait(()=>q('#callback-url').value==='','callback accepted');assert.ok(requests.some(r=>r.p==='/api/accounts/login/gemini-test-session/callback'));});
  await test('closing cancels only the active Gemini login session',async()=>{await close();assert.ok(requests.some(r=>r.p==='/api/accounts/login/gemini-test-session'&&r.method==='DELETE'));});
  await test('ChatGPT device-code login remains selectable after Gemini',async()=>{await open();q('[data-provider="gemini"]').click();q('[data-provider="codex"]').click();assert.equal(q('#pool-chips .chip.active')?.dataset.pool,'plus');assert.equal(q('#pool-chips .chip[data-pool="gemini"]').classList.contains('hidden'),true);q('#btn-login-start').click();await wait(()=>q('#device-code').textContent==='ABCD-1234','Codex device code');const request=requests.filter(r=>r.p==='/api/accounts/login'&&r.method==='POST').at(-1);assert.equal(request.body.provider,'codex');assert.equal(request.body.pool,'plus');assert.equal(q('#callback-box').classList.contains('hidden'),true);await close();});
  await test('Gemini start errors restore the retry button',async()=>{await open();q('[data-provider="gemini"]').click();failStart=true;q('#btn-login-start').click();await wait(()=>!q('#btn-login-start').disabled,'retry');assert.ok(q('#toast').textContent.includes('模拟登录进程启动失败'));assert.equal(q('#btn-login-start').classList.contains('hidden'),false);failStart=false;await close();});
  await test('expired or failed login can be retried',async()=>{await open();q('[data-provider="gemini"]').click();pollFailed=true;q('#btn-login-start').click();await wait(()=>q('#login-state').classList.contains('err'),'failed login');assert.equal(q('#btn-login-start').classList.contains('hidden'),false);pollFailed=false;await close();});
  await test('Gemini successful completion refreshes account list',async()=>{await open();q('[data-provider="gemini"]').click();finish=true;const before=requests.filter(r=>r.p==='/api/accounts').length;q('#btn-login-start').click();await wait(()=>q('#login-state').classList.contains('ok'),'done');await new Promise(r=>setTimeout(r,1300));assert.equal(q('#dlg-account').classList.contains('hidden'),true);assert.ok(requests.filter(r=>r.p==='/api/accounts').length>before);});
  await test('no uncaught frontend errors',()=>assert.deepEqual(errors,[]));
}finally{w.close();}

process.stdout.write(results.filter(r=>r.ok).length+'/'+results.length+' Gemini login UI checks passed\n');
if(results.some(r=>!r.ok))process.exitCode=1;
