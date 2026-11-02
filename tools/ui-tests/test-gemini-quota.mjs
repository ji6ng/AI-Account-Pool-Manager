import { JSDOM,VirtualConsole } from 'jsdom';
import { fixtures } from './fixtures.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../web');
const live={"accounts":[{"account":"Gemini 2","quota":{"ok":true,"cached":false,"plan":"Google AI Pro","tier_id":"standard-tier","has_weekly":true,"groups":[{"name":"Gemini","raw_name":"Gemini models","desc":"上游没有下发 Gemini 组的周限额（该套餐下 Gemini 模型通常不受周上限约束）","buckets":[{"window":"5h","label":"5 小时","left":100.0,"used":0.0,"reset_at":1793349100,"reset_after":18003}]},{"name":"Claude / GPT","raw_name":"Claude and GPT models","desc":"Models within this group: Claude Opus, Claude Sonnet, GPT-OSS","buckets":[{"window":"5h","label":"5 小时","left":100.0,"used":0.0,"reset_at":1793349099,"reset_after":18002},{"window":"weekly","label":"周","left":97.4,"used":2.6,"reset_at":1793714668,"reset_after":383571}]}],"models":[{"model":"claude-opus-4-6-thinking","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"claude-sonnet-4-6","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-2.5-flash","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-2.5-flash-lite","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-2.5-flash-thinking","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-2.5-pro","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-3-flash","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-3.1-flash-image","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-3.1-flash-lite","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-3.5-flash-lite","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gemini-3.8-flash-tiered","left":100.0,"reset_at":1793349100,"reset_after":18003},{"model":"gpt-oss-120b-medium","left":100.0,"reset_at":1793349100,"reset_after":18003}],"model_count":12,"fetched_at":1793331097}}]};
const fixture=fixtures(),qresults=[],errors=[];
const gem=fixture.accounts.find(a=>a.provider==='gemini');
let usage=structuredClone(fixture.usage);
let failed=false;
const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(root+'/index.html','utf8'),{url:'http://127.0.0.1:19366/#accounts',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
const w=dom.window,d=w.document,q=s=>d.querySelector(s);
w.fetch=async(url,opt={})=>{
 const p=String(url).split('?')[0];let data={ok:true};
 if(p==='/api/whoami')data={allowed:true};
 else if(p==='/api/overview')data=fixture.overview;
 else if(p==='/api/settings')data=fixture.settings;
 else if(p==='/api/keys')data={keys:fixture.keys};
 else if(p==='/api/models')data={exposed:fixture.models};
 else if(p==='/api/accounts')data={accounts:fixture.accounts};
 else if(p==='/api/providers')data={providers:fixture.providers};
 else if(p==='/api/runtime/accounts')data={accounts:[]};
 else if(p==='/api/usage')data=failed?{ok:false,error:'upstream unavailable'}:{usage};
 return{status:200,ok:true,text:async()=>JSON.stringify(data)};
};
async function wait(fn,label){for(let i=0;i<160;i++){if(fn())return;await new Promise(r=>setTimeout(r,5));}throw Error('Timed out: '+label);}
function cell(){return d.querySelector('[data-ucell="'+gem.file+'"]');}
async function refresh(snapshot){usage[gem.file]=snapshot;q('#btn-refresh-usage').click();await wait(()=>!q('#btn-refresh-usage').disabled,'refresh');}
async function test(name,fn){try{await fn();qresults.push({name,ok:true});process.stdout.write('PASS '+name+'\n');}catch(error){qresults.push({name,ok:false,error:error.message});process.stdout.write('FAIL '+name+' '+error.message+'\n');}}
try{
 usage[gem.file]=live.accounts.find(a=>a.account==='Gemini 2').quota;
 w.eval(fs.readFileSync(root+'/app.js','utf8'));
 await wait(()=>cell()?.textContent.includes('97.4%'),'actual quota rendered');
 await test('live quota shows Gemini 100% and shared weekly 97.4% separately',()=>{assert.ok(cell().textContent.includes('100%'));assert.ok(cell().textContent.includes('97.4%'));assert.ok(cell().querySelector('.quota-detail-body').textContent.includes('97.4%'));assert.ok(cell().textContent.includes('Claude / GPT'));assert.ok(cell().textContent.includes('Google 未提供 Gemini 周限额'));});
 await test('depleted Gemini model renders zero rather than unlimited',async()=>{await refresh({ok:true,provider:'gemini',models:[{model:'gemini-test',left:0,reset_after:600}],groups:[],fetched_at:1793331097});assert.ok(cell().textContent.includes('0%'));assert.ok(!cell().textContent.includes('不限额'));assert.equal(cell().querySelector('.qbar i').style.width,'0%');});
 await test('partial quota refresh updates percentage and timestamp',async()=>{await refresh({ok:true,provider:'gemini',models:[{model:'gemini-test',left:42,reset_after:600}],groups:[],fetched_at:1793331157,cached:false});assert.ok(cell().textContent.includes('42%'));assert.ok(cell().textContent.includes('更新于'));assert.ok(!cell().textContent.includes('缓存'));});
 await test('missing Gemini data does not borrow the shared quota',async()=>{await refresh({ok:true,provider:'gemini',models:[],groups:[{name:'Claude / GPT',buckets:[{label:'周',window:'weekly',left:25}]}]});assert.ok(cell().textContent.includes('暂无额度数据'));assert.ok(cell().textContent.includes('25%'));assert.ok(!cell().textContent.includes('不限额'));});
 await test('Gemini model minimum ignores a depleted Claude model',async()=>{await refresh({ok:true,provider:'gemini',models:[{model:'gemini-test',left:80},{model:'claude-test',left:0}],groups:[]});assert.ok(cell().textContent.includes('80%'));assert.equal(cell().querySelectorAll('.qbar').length,1);assert.equal(cell().querySelector('.qbar i').style.width,'80%');});
 await test('explicit Gemini weekly data is displayed when available',async()=>{await refresh({ok:true,provider:'gemini',models:[{model:'gemini-test',left:80}],groups:[{name:'Gemini',buckets:[{window:'weekly',label:'周',left:20}]}]});assert.ok(cell().textContent.includes('20%'));assert.ok(!cell().textContent.includes('周限额未返回'));});
 await test('failed refresh is visibly failed rather than retaining a success claim',async()=>{failed=true;await refresh({});assert.ok(cell().textContent.includes('查询失败'));assert.ok(q('#toast').classList.contains('err'));});
 await test('quota values and model names are escaped before rendering',async()=>{failed=false;await refresh({ok:true,provider:'gemini',models:[{model:'gemini-<img src=x>',left:50}],groups:[]});assert.equal(cell().querySelector('img'),null);assert.ok(cell().querySelector('.quota-detail-body').textContent.includes('<img src=x>'));});
 await test('no uncaught frontend errors',()=>assert.deepEqual(errors,[]));
}finally{w.close();}

process.stdout.write(qresults.filter(r=>r.ok).length+'/'+qresults.length+' quota tests passed\n');
if(qresults.some(r=>!r.ok))process.exitCode=1;
