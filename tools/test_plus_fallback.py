"""Plus fallback regressions: isolated data, fake accounts and mocked HTTP only."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get("AIPOOL_TEST_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
import httpx
from fastapi.testclient import TestClient
from core import gateway, keys, paths, plus_fallback as pf, settings

SOURCE, TARGET = "plus-gpt-6-astra", "Relay/gpt-6-astra"


def usage(exhausted=False, **extra):
    return {"ok": True, "allowed": not exhausted, "limit_reached": exhausted,
            "fetched_at": int(time.time()), **extra}


class Routing(unittest.TestCase):
    def setUp(self):
        self.data_root = Path(os.environ.get("AIPOOL_TEST_DATA") or ROOT / "test-data").resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="plus-fallback-", dir=self.data_root)
        paths.set_data_dir(Path(self.temp.name))
        settings.reset_cache()
        keys._db_path = None
        settings.load()
        keys.init()
        self.cfg = settings.save({
            "plus_priority": {"enabled": False, "groups": {}},
            "plus_fallback": {"enabled": True, "check_interval": 30, "models": {SOURCE: TARGET}},
            "providers": [{"id": "relay", "prefix": "Relay", "label": "Test Relay",
                "enabled": True, "api_key": "test-placeholder", "base_url": "https://relay.invalid/v1",
                "kind": "codex", "models": [{"alias": "gpt-6-astra", "name": "gpt-6-astra"}]}]})
        self.rows = [{"file": "a.json", "pool": "plus", "provider": "codex", "mtime": 1},
                     {"file": "b.json", "pool": "plus", "provider": "codex", "mtime": 1}]
        self.usages = {"a.json": usage(True), "b.json": usage(True)}
        self.patches = [patch.object(pf.accounts, "list_accounts", side_effect=lambda: copy.deepcopy(self.rows)),
                        patch.object(pf.codex, "cached_usage", side_effect=lambda file, *args: copy.deepcopy(self.usages[file]))]
        for p in self.patches:
            p.start()
        self.key = keys.list_keys()[0]
        keys.update_key(self.key["id"], {"models": SOURCE + "," + TARGET})
        self.key = keys.list_keys()[0]
        self.app = gateway.create_app()
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.seen = []

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        settings.reset_cache()
        keys._db_path = None
        assert self.data_root in Path(self.temp.name).resolve().parents
        self.temp.cleanup()

    def decide(self, monitor=None, force=False, path="v1/responses"):
        return asyncio.run((monitor or pf.Monitor()).decide(self.cfg, self.key, SOURCE, path, force))

    def send(self, handler=None, body=None, path="/v1/responses"):
        def default(req):
            obj = json.loads(req.content)
            self.seen.append(obj)
            return httpx.Response(200, json={"model": obj["model"], "output": []})
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
        try:
            with patch.object(gateway, "http", return_value=upstream):
                return self.client.post(path, headers={"Authorization": "Bearer " + self.key["secret"]},
                    json=body or {"model": SOURCE, "input": "test", "stream": False})
        finally:
            asyncio.run(upstream.aclose())

    def test_all_exhausted_switches_to_explicit_provider_path(self):
        self.assertEqual(self.decide()["actual"], TARGET)

    def test_one_available_keeps_plus(self):
        self.usages["b.json"] = usage()
        self.assertEqual(self.decide()["actual"], SOURCE)

    def test_unknown_or_query_failure_does_not_trigger_fallback(self):
        for value in ({"ok": False}, usage(False, allowed=False), usage(True, fetched_at=0)):
            with self.subTest(value=value):
                self.usages["b.json"] = value
                self.assertEqual(self.decide()["actual"], SOURCE)

    def test_disabled_and_pro_accounts_are_not_counted(self):
        self.rows += [{"file": "ignored", "pool": "plus", "disabled": True},
                      {"file": "pro", "pool": "pro"}, {"file": "gemini", "pool": "gemini"}]
        self.assertEqual(self.decide()["actual"], TARGET)

    def test_no_enabled_plus_accounts_does_not_use_paid_fallback(self):
        self.rows = []
        self.assertEqual(self.decide()["actual"], SOURCE)

    def test_recovery_automatically_restores_plus_on_next_check(self):
        async def scenario():
            m = pf.Monitor()
            self.assertEqual((await m.decide(self.cfg, self.key, SOURCE, "v1/responses"))["actual"], TARGET)
            self.usages["b.json"] = usage()
            m.at -= 31
            self.assertEqual((await m.decide(self.cfg, self.key, SOURCE, "v1/responses"))["actual"], SOURCE)
        asyncio.run(scenario())

    def test_force_refresh_overrides_cached_exhaustion(self):
        async def scenario():
            m = pf.Monitor()
            await m.check(self.cfg)
            self.usages["a.json"] = usage()
            self.assertEqual((await m.check(self.cfg, True))["state"], "primary")
        asyncio.run(scenario())

    def test_unknown_account_does_not_overrule_available_primary(self):
        self.usages["a.json"] = {"ok": False}
        self.usages["b.json"] = usage()
        self.assertEqual(self.decide()["actual"], SOURCE)

    def test_target_requires_its_own_key_permission(self):
        self.key["model_list"] = [SOURCE]
        result = self.decide()
        self.assertEqual(result["mode"], "blocked")
        self.assertEqual(result["actual"], SOURCE)

    def test_removed_disabled_or_wrong_case_target_is_blocked(self):
        for change in ({"enabled": False}, {"prefix": "relay"}, {"kind": "claude"}):
            cfg = copy.deepcopy(self.cfg)
            cfg["providers"][0].update(change)
            result = asyncio.run(pf.Monitor().decide(cfg, self.key, SOURCE, "v1/responses"))
            self.assertEqual(result["mode"], "blocked")

    def test_no_mapping_disabled_or_non_generation_path_does_not_query_quota(self):
        with patch.object(pf.codex, "cached_usage", side_effect=AssertionError("must not query")):
            for change in ({"enabled": False}, {"models": {}}):
                cfg = copy.deepcopy(self.cfg)
                cfg["plus_fallback"].update(change)
                self.assertEqual(asyncio.run(pf.Monitor().decide(cfg, self.key, SOURCE, "v1/responses"))["actual"], SOURCE)
            self.assertEqual(self.decide(path="v1/embeddings")["actual"], SOURCE)

    def test_http_forwards_target_and_preserves_payload_and_logs_route(self):
        response = self.send(body={"model": SOURCE, "input": "history", "tools": [{"type": "function", "name": "x"}],
                                   "reasoning": {"effort": "high"}, "stream": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], TARGET)
        self.assertEqual(response.headers["x-aipool-route"], "fallback")
        self.assertEqual(response.headers["x-aipool-requested-model"], SOURCE)
        self.assertEqual(self.seen[0]["reasoning"], {"effort": "high"})
        self.assertEqual(self.seen[0]["input"], "history")
        log = keys.list_logs()[0]
        self.assertEqual(log["model"], TARGET)
        self.assertIn(SOURCE + " → " + TARGET, log["note"])

    def test_http_unauthorized_fallback_is_rejected_without_any_upstream_call(self):
        keys.update_key(self.key["id"], {"models": SOURCE})
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(self.seen, [])

    def test_previous_response_id_cannot_cross_provider(self):
        response = self.send(body={"model": SOURCE, "previous_response_id": "resp_from_plus", "input": "next"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.seen, [])

    def test_stream_is_forwarded_once_without_replay(self):
        def handler(req):
            self.seen.append(json.loads(req.content))
            return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"ok"}\n\ndata: [DONE]\n\n',
                                  headers={"content-type": "text/event-stream"})
        response = self.send(handler=handler, body={"model": SOURCE, "input": "hi", "stream": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.seen), 1)
        self.assertIn('"delta":"ok"', response.text)
        self.assertEqual(self.seen[0]["model"], TARGET)

    def test_429_refresh_switches_only_after_every_account_is_confirmed_exhausted(self):
        self.usages["a.json"] = usage()
        def handler(req):
            obj = json.loads(req.content)
            self.seen.append(obj)
            if obj["model"] == SOURCE:
                self.usages["a.json"] = usage(True)
                return httpx.Response(429, json={"error": "quota"})
            return httpx.Response(200, json={"model": obj["model"]})
        response = self.send(handler=handler)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["model"] for r in self.seen], [SOURCE, TARGET])

    def test_rate_limit_with_remaining_quota_does_not_switch(self):
        self.usages["a.json"] = usage()
        def handler(req):
            self.seen.append(json.loads(req.content))
            return httpx.Response(429, json={"error": "rate"})
        self.assertEqual(self.send(handler=handler).status_code, 429)
        self.assertEqual([r["model"] for r in self.seen], [SOURCE])

    def test_upstream_server_error_does_not_switch(self):
        self.usages["a.json"] = usage()
        def handler(req):
            self.seen.append(json.loads(req.content))
            return httpx.Response(503, json={"error": "overloaded"})
        self.assertEqual(self.send(handler=handler).status_code, 503)
        self.assertEqual([r["model"] for r in self.seen], [SOURCE])

    def test_settings_mapping_removal_replaces_old_mapping_without_restart(self):
        with patch.object(gateway.pool, "restart", side_effect=AssertionError("must not restart")):
            r = self.client.put("/api/settings", json={"plus_fallback": {"enabled": False, "check_interval": 30, "models": {}}})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["restarted"])
        self.assertEqual(settings.load()["plus_fallback"]["models"], {})

    def test_invalid_mapping_never_changes_settings(self):
        before = settings.path().read_bytes()
        for body in ({"enabled": True, "models": {}}, {"enabled": True, "models": {SOURCE: "missing/model"}},
                     {"enabled": True, "check_interval": 0, "models": {SOURCE: TARGET}}):
            r = self.client.put("/api/settings", json={"plus_fallback": body})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(settings.path().read_bytes(), before)

    def test_status_exposes_paths_and_counts_without_secrets(self):
        r = self.client.get("/api/plus-fallback")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["snapshot"]["exhausted"], 2)
        route = next(r for r in data["routes"] if r["source"] == SOURCE)
        self.assertEqual(route["actual"], TARGET)
        self.assertNotIn("test-placeholder", r.text)
        self.assertNotIn(self.key["secret"], r.text)

    def test_remote_status_requires_admin_session(self):
        client = TestClient(self.app, client=("192.0.2.9", 50000))
        try:
            self.assertEqual(client.get("/api/plus-fallback").status_code, 401)
        finally:
            client.close()

    def test_background_monitor_returns_to_plus_without_page_or_generation(self):
        async def scenario():
            m = pf.Monitor()
            task = asyncio.create_task(m.run())
            try:
                for _ in range(100):
                    if m.value:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(m.value["state"], "fallback")
                self.usages["a.json"] = usage()
                m.at -= 31
                m.wake.set()
                for _ in range(100):
                    if m.value["state"] == "primary":
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(m.value["state"], "primary")
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
