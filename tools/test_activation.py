"""定时额度激活及 Plus 明确 429 切换回归，全部使用隔离数据和假上游。"""
import asyncio, copy, datetime as dt, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import activation, accounts, gateway, keys, paths, plus_fallback as pf, plus_priority as pp, settings
from fastapi.testclient import TestClient
import httpx

class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='activation-test-')
        paths.set_data_dir(Path(self.tmp.name))
        settings.reset_cache(); keys._db_path=None
        settings.load(); keys.init()
        self.rows=[{'file':'plus-a.json','email':'plus-a@example.invalid','pool':'plus','provider':'codex','disabled':False},
                   {'file':'pro-b.json','email':'pro-b@example.invalid','pool':'pro','provider':'codex','disabled':False},
                   {'file':'gem.json','email':'gem@example.invalid','pool':'gemini','provider':'gemini','disabled':False}]
        self.p=patch.object(activation.accounts,'list_accounts',side_effect=lambda:copy.deepcopy(self.rows)); self.p.start()
        async def inline_to_thread(fn,*args,**kwargs): return fn(*args,**kwargs)
        self.thread_patch=patch('asyncio.to_thread',side_effect=inline_to_thread); self.thread_patch.start()
    def tearDown(self):
        self.thread_patch.stop(); self.p.stop(); settings.reset_cache(); keys._db_path=None; self.tmp.cleanup()

class ActivationTests(Isolated):
    def test_validate_daily_and_once(self):
        a=activation.validate({'name':'morning','accounts':['plus-a.json'],'repeat_daily':True,'time':'08:30','prompt':'回复好'})
        self.assertEqual(a['time'],'08:30')
        b=activation.validate({'name':'once','accounts':['pro-b.json'],'repeat_daily':False,'run_at':(dt.datetime.now()+dt.timedelta(hours=1)).isoformat(timespec='minutes')})
        self.assertGreater(b['run_at'],time.time())
    def test_reject_gemini_unknown_duplicate_bad_time_and_past(self):
        for payload in (
          {'name':'x','accounts':['gem.json'],'repeat_daily':True,'time':'08:00'},
          {'name':'x','accounts':['missing.json'],'repeat_daily':True,'time':'08:00'},
          {'name':'x','accounts':['plus-a.json','plus-a.json'],'repeat_daily':True,'time':'08:00'},
          {'name':'x','accounts':['plus-a.json'],'repeat_daily':True,'time':'25:00'},
          {'name':'x','accounts':['plus-a.json'],'repeat_daily':False,'run_at':'2000-01-01T00:00'},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError): activation.validate(payload)
    def test_daily_due_once_per_local_day(self):
        task={'enabled':True,'repeat_daily':True,'time':dt.datetime.now().strftime('%H:%M'),'last_run_date':''}
        now=time.time()
        self.assertTrue(activation.due(task,now))
        task['last_run_date']=dt.datetime.fromtimestamp(now).date().isoformat()
        self.assertFalse(activation.due(task,now))
    def test_task_crud_and_recover_interrupted_daily_task(self):
        scheduler=activation.Scheduler()
        task=scheduler.create({'name':'daily','accounts':['plus-a.json'],'repeat_daily':True,'time':'09:00'})
        self.assertTrue(scheduler.set_enabled(task['id'],False)['ok'])
        self.assertTrue(scheduler.set_enabled(task['id'],True)['ok'])
        rows=activation._task_list(); rows[0]['status']='running'; rows[0]['last_run_date']=dt.datetime.now().date().isoformat()
        activation._save_tasks(rows)
        self.assertEqual(scheduler.recover_interrupted(),1)
        recovered=activation._task_list()[0]
        self.assertEqual(recovered['status'],'failed'); self.assertEqual(recovered['last_run_date'],'')
        self.assertTrue(scheduler.delete(task['id'])['ok'])
    def test_activation_uses_selected_account_and_its_configured_upstream_model(self):
        alias='plus-gpt-6.1-sol'
        auth={'email':'plus-a@example.invalid','model_aliases':[{'alias':alias}]}
        with patch.object(activation.codex,'read_auth',return_value=auth), \
             patch.object(activation.codex,'chat',return_value={'ok':True,'text':'已激活'}) as chat:
            result=activation._run_account('plus-a.json','请只回复：已激活。')
        self.assertTrue(result['ok'])
        chat.assert_called_once()
        self.assertEqual(chat.call_args.args[:3],(auth,'gpt-6.1-sol','请只回复：已激活。'))
    def test_scheduler_runs_daily_in_parallel_and_records_results(self):
        scheduler=activation.Scheduler()
        task=scheduler.create({'name':'morning','accounts':['plus-a.json','pro-b.json'],'repeat_daily':True,'time':dt.datetime.now().strftime('%H:%M')})
        async def scenario():
            with patch.object(activation,'_run_account',side_effect=lambda f,p:{'file':f,'email':f,'ok':True}):
                self.assertEqual(await scheduler.tick(time.time()),1)
                self.assertEqual(await scheduler.tick(time.time()),0)
                for _ in range(100):
                    saved=next(t for t in activation._task_list() if t['id']==task['id'])
                    if saved['status']!='running': break
                    await asyncio.sleep(.01)
                self.assertEqual(saved['status'],'success')
                self.assertEqual(len(saved['last_results']),2)
        asyncio.run(scenario())
    def test_once_task_disables_after_execution(self):
        scheduler=activation.Scheduler()
        payload={'name':'once','accounts':['plus-a.json'],'repeat_daily':False,'run_at':(dt.datetime.now()+dt.timedelta(minutes=1)).isoformat(timespec='minutes')}
        task=scheduler.create(payload)
        async def scenario():
            with patch.object(activation,'_run_account',return_value={'file':'plus-a.json','ok':True}):
                self.assertEqual(await scheduler.tick(task['run_at']+1),1)
                for _ in range(100):
                    saved=next(t for t in activation._task_list() if t['id']==task['id'])
                    if saved['status']!='running': break
                    await asyncio.sleep(.01)
                self.assertFalse(saved['enabled']); self.assertEqual(saved['status'],'success')
        asyncio.run(scenario())
    def test_api_crud_and_enable_disable(self):
        app=gateway.create_app(); client=TestClient(app,client=('127.0.0.1',50000))
        try:
            r=client.post('/api/activation-tasks',json={'name':'daily','accounts':['plus-a.json'],'repeat_daily':True,'time':'09:00'})
            self.assertEqual(r.status_code,200,r.text); task=r.json()['task']
            self.assertEqual(client.get('/api/activation-tasks').json()['tasks'][0]['id'],task['id'])
            self.assertEqual(client.put('/api/activation-tasks/'+task['id'],json={'enabled':False}).status_code,200)
            self.assertEqual(client.delete('/api/activation-tasks/'+task['id']).status_code,200)
        finally: client.close()

class Priority429Tests(Isolated):
    def setUp(self):
        super().setUp()
        self.rows=[{'file':'a.json','pool':'plus','provider':'codex','disabled':False,'email':'A'},
                   {'file':'b.json','pool':'plus','provider':'codex','disabled':False,'email':'B'}]
        self.usages={'a.json':self.quota(),'b.json':self.quota()}
        self.usage_patch=patch.object(pf.codex,'cached_usage',side_effect=lambda f,*args:copy.deepcopy(self.usages[f]))
        self.usage_patch.start(); self.addCleanup(self.usage_patch.stop)
        cfg=settings.save({'plus_priority':{'enabled':True,'groups':{}},'plus_account_order':['a.json','b.json'],
            'plus_fallback':{'enabled':True,'check_interval':30,'models':{SOURCE:TARGET}},
            'providers':[{'id':'relay','prefix':'Relay','label':'Test Relay','enabled':True,'api_key':'test-placeholder',
                'base_url':'https://relay.invalid/v1','kind':'codex','models':[{'alias':'gpt-6-astra','name':'gpt-6-astra'}]}]})
        self.key=keys.list_keys()[0]
        keys.update_key(self.key['id'],{'models':SOURCE+','+TARGET}); self.key=keys.list_keys()[0]
        self.rows=[
            {'file':'a.json','pool':'plus','provider':'codex','disabled':False,'email':'A','aliases':[SOURCE,pp.private_alias('a.json',SOURCE)]},
            {'file':'b.json','pool':'plus','provider':'codex','disabled':False,'email':'B','aliases':[SOURCE,pp.private_alias('b.json',SOURCE)]}]
        self.app=gateway.create_app()
        self.client=TestClient(self.app,client=('127.0.0.1',50001))
        self.seen=[]
    @staticmethod
    def quota(exhausted=False):
        return {'ok':True,'allowed':not exhausted,'limit_reached':exhausted,'fetched_at':int(time.time()),
          'primary':{'used':100 if exhausted else 10,'window_seconds':18000},'secondary':None}
    def test_quota_snapshot_selects_next_account_and_marks_pool_exhausted(self):
        rows=[{'file':'a.json','pool':'plus','provider':'codex','disabled':False,'email':'A'},
              {'file':'b.json','pool':'plus','provider':'codex','disabled':False,'email':'B'}]
        cfg={'plus_priority':{'enabled':True,'groups':{}},'plus_account_order':['a.json','b.json']}
        snap=pp.status(rows,{'a.json':self.quota(True),'b.json':self.quota()},cfg)
        self.assertEqual(snap['selected'],'b.json')
        snap=pp.status(rows,{'a.json':self.quota(True),'b.json':self.quota(True)},cfg)
        self.assertEqual(snap['selected'],''); self.assertTrue(snap['all_exhausted'])
    def test_explicit_quota_429_retries_next_pinned_account(self):
        first=pp.private_alias('a.json',SOURCE); second=pp.private_alias('b.json',SOURCE)
        def handler(req):
            body=__import__('json').loads(req.content); self.seen.append(body['model'])
            if body['model']==first:
                self.usages['a.json']=self.quota(True)
                return httpx.Response(429,json={'error':{'message':'quota exceeded'}})
            return httpx.Response(200,json={'model':body['model'],'output':[]})
        upstream=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with patch.object(gateway,'http',return_value=upstream):
                r=self.client.post('/v1/responses',headers={'Authorization':'Bearer '+self.key['secret']},
                    json={'model':SOURCE,'input':'test','stream':False})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(self.seen,[first,second])
            self.assertEqual(r.headers['x-aipool-route'],'account')
            self.assertEqual(r.json()['model'],SOURCE)
        finally: asyncio.run(upstream.aclose())

SOURCE='plus-gpt-6-astra'
TARGET='Relay/gpt-6-astra'
if __name__=='__main__': unittest.main(verbosity=2)

