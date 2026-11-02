"""Account priority regressions: fake quotas, isolated data and mocked gateway transport."""
import asyncio, copy, json, time, unittest
from unittest.mock import patch
from test_plus_fallback import Routing, SOURCE, TARGET, usage
from core import plus_priority as pp, plus_fallback as pf, gateway, settings

class Priority(unittest.TestCase):
    setUpBase = Routing.setUp
    tearDown = Routing.tearDown
    send = Routing.send
    decide = Routing.decide
    def setUp(self):
        self.setUpBase()
        self.cfg = settings.save({'plus_priority': {'enabled': True, 'groups': {}}, 'plus_account_order': ['b.json','a.json']})
        for a in self.rows:
            a['aliases'] = [SOURCE, pp.private_alias(a['file'], SOURCE)]
        self.usages = {'a.json': self.quota(), 'b.json': self.quota()}
    def quota(self, exhausted=False, reserve=False):
        return usage(exhausted, primary={'window_seconds': 604800 if reserve else 18000, 'used': 100 if exhausted else 20})
    def test_manual_order_pins_account_and_wire_model(self):
        r=self.decide()
        self.assertEqual(r['file'],'b.json')
        self.assertEqual(r['wire_model'], pp.private_alias('b.json',SOURCE))
        self.assertEqual(r['actual'],SOURCE)
    def test_five_hour_group_precedes_manual_reserve_order(self):
        self.usages['b.json']=self.quota(reserve=True)
        self.assertEqual(self.decide()['file'],'a.json')
    def test_exhaustion_moves_to_next_account(self):
        self.usages['b.json']=self.quota(True)
        self.assertEqual(self.decide()['file'],'a.json')
    def test_only_all_five_hour_exhausted_enters_reserve(self):
        self.usages['a.json']=self.quota(True)
        self.usages['b.json']=self.quota(reserve=True)
        self.assertEqual(self.decide()['file'],'b.json')
    def test_recovery_restores_five_hour_account(self):
        async def run():
            m=pf.Monitor()
            self.usages['a.json']=self.quota(True)
            self.usages['b.json']=self.quota(reserve=True)
            self.assertEqual((await m.decide(self.cfg,self.key,SOURCE,'v1/responses'))['file'],'b.json')
            self.usages['a.json']=self.quota()
            self.assertEqual((await m.decide(self.cfg,self.key,SOURCE,'v1/responses',True))['file'],'a.json')
        asyncio.run(run())
    def test_unknown_quota_preserves_known_high_priority_group(self):
        async def run():
            m=pf.Monitor()
            await m.check(self.cfg)
            self.usages['b.json']={'ok':False}
            self.assertEqual((await m.decide(self.cfg,self.key,SOURCE,'v1/responses',True))['file'],'b.json')
        asyncio.run(run())
    def test_all_exhausted_uses_authorized_explicit_provider(self):
        self.usages={f:self.quota(True) for f in self.usages}
        self.assertEqual(self.decide()['actual'],TARGET)
    def test_missing_alias_blocks_instead_of_random_rotation(self):
        self.rows[1]['aliases']=[]
        self.assertEqual(self.decide()['mode'],'blocked')
    def test_gateway_rewrites_private_alias_back_to_public_model(self):
        r=self.send()
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.seen[0]['model'],pp.private_alias('b.json',SOURCE))
        self.assertEqual(r.json()['model'],SOURCE)
        self.assertEqual(r.headers['x-aipool-route'],'account')
    def test_server_error_keeps_same_account_without_replay(self):
        import httpx
        def handler(req):
            self.seen.append(json.loads(req.content))
            return httpx.Response(503,json={'error':'temporary network'})
        self.assertEqual(self.send(handler).status_code,503)
        self.assertEqual([x['model'] for x in self.seen],[pp.private_alias('b.json',SOURCE)])
        self.assertEqual(self.decide()['file'],'b.json')
    def test_private_alias_is_not_a_client_model(self):
        r=self.send(body={'model':pp.private_alias('b.json',SOURCE),'input':'hello'})
        self.assertIn(r.status_code,(400,403,404))
        self.assertEqual(self.seen,[])
    def test_disabled_accounts_are_skipped(self):
        self.rows[1]['disabled']=True
        self.assertEqual(self.decide()['file'],'a.json')

if __name__=='__main__': unittest.main(verbosity=2)

