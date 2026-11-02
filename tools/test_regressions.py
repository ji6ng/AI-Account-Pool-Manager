"""Offline regressions: temporary data only; all upstream calls are mocked."""
from __future__ import annotations

import json
import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get("AIPOOL_TEST_ROOT") or Path(__file__).resolve().parents[1]).resolve()
sys.path.insert(0, str(ROOT))
import httpx
from fastapi.testclient import TestClient
from core import accounts, codex, gemini, gateway, keys, modeltrace, paths, providers, proxycfg, settings

REAL_HTTP_CLIENT = httpx.Client


class Isolated(unittest.TestCase):
    def setUp(self):
        self.data_root = Path(__file__).resolve().parents[2] / "test-data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="regression-", dir=self.data_root)
        paths.set_data_dir(Path(self.temp.name))
        settings.reset_cache()
        keys._db_path = None
        settings.load()
        keys.init()

    def tearDown(self):
        settings.reset_cache()
        keys._db_path = None
        gc.collect()
        assert self.data_root.resolve() in Path(self.temp.name).resolve().parents
        self.temp.cleanup()

    def provider(self, **extra):
        return {"label": "Test Relay", "prefix": "test", "base_url": "https://relay.invalid",
                "api_key": "test-placeholder", "kind": "codex",
                "models": [{"alias": "test-model", "name": "test-upstream"}], **extra}

    def mock_http(self, handler):
        transport = httpx.MockTransport(handler)
        return patch("httpx.Client", side_effect=lambda **kw: REAL_HTTP_CLIENT(transport=transport, timeout=kw.get("timeout", 10)))


class ProviderTests(Isolated):
    def test_duplicate_generated_ids_are_unique(self):
        rows = providers.all_of({"providers": [self.provider(), self.provider(), self.provider()]})
        self.assertEqual(len({p["id"] for p in rows}), 3)

    def test_reject_blank_provider_update_without_changing_disk(self):
        p = providers.add(self.provider())["provider"]
        before = settings.path().read_bytes()
        result = providers.update(p["id"], {"api_key": "   "})
        self.assertFalse(result["ok"])
        self.assertEqual(settings.path().read_bytes(), before)

    def test_reject_duplicate_model_aliases(self):
        p = self.provider()
        p["models"] *= 2
        self.assertFalse(providers.add(p)["ok"])

    def test_reject_invalid_url(self):
        for url in ("not-a-url", "file:///test", "https://user:password@relay.invalid/v1", "https://relay.invalid:abc", "https://relay.invalid?key=test"):
            with self.subTest(url=url):
                self.assertFalse(providers.add(self.provider(base_url=url))["ok"])

    def test_root_url_gets_version_for_codex(self):
        cfg = settings.load().copy()
        cfg["providers"] = [self.provider()]
        self.assertIn('base-url: "https://relay.invalid/v1"', proxycfg.render(cfg))

    def test_claude_does_not_duplicate_version(self):
        cfg = settings.load().copy()
        cfg["providers"] = [self.provider(kind="claude", base_url="https://relay.invalid/v1/")]
        self.assertIn('base-url: "https://relay.invalid"', proxycfg.render(cfg))
        self.assertNotIn('/v1/v1', proxycfg.render(cfg))

    def test_connectivity_rejects_html_200(self):
        with self.mock_http(lambda req: httpx.Response(200, text="<html>Sign in</html>")):
            self.assertFalse(providers.test(self.provider(models=[]))["ok"])

    def test_connectivity_uses_native_messages_and_checks_body(self):
        seen = []
        def handler(req):
            seen.append(req.url.path)
            if req.url.path == "/v1/models":
                return httpx.Response(404, json={"error": "no listing"})
            if req.url.path == "/v1/messages":
                self.assertEqual(req.headers["anthropic-version"], "2023-06-01")
                return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})
            return httpx.Response(404, json={"error": "wrong protocol"})
        with self.mock_http(handler):
            self.assertTrue(providers.test(self.provider(kind="claude"))["ok"])
        self.assertEqual(seen, ["/v1/models", "/v1/messages"])

    def test_responses_fallback_returns_real_text(self):
        def handler(req):
            if req.url.path == "/v1/chat/completions":
                return httpx.Response(404)
            self.assertEqual(req.url.path, "/v1/responses")
            return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": "hello"}]}]})
        with self.mock_http(handler):
            self.assertEqual(providers.chat(self.provider(), "m", "hi")["text"], "hello")

    def test_empty_generation_is_not_success(self):
        with self.mock_http(lambda req: httpx.Response(200, json={"choices": []})):
            self.assertFalse(providers.chat(self.provider(), "m", "hi")["ok"])


class KeyTests(Isolated):
    def test_database_connection_is_closed_after_context(self):
        import sqlite3
        with keys.conn() as connection:
            connection.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_blank_model_list_cannot_become_wildcard(self):
        self.assertFalse(keys.create_key("restricted", models="")["ok"])

    def test_empty_edit_cannot_erase_whitelist(self):
        key = keys.create_key("restricted", models="only-this")
        self.assertFalse(keys.update_key(key["id"], {"models": " "})["ok"])
        row = keys.find_by_secret(key["secret"])
        self.assertFalse(keys.allowed(row, "another-model"))

    def test_whitelist_and_disable(self):
        key = keys.create_key("restricted", models="only-this")
        row = keys.find_by_secret(key["secret"])
        self.assertTrue(keys.allowed(row, "only-this"))
        self.assertFalse(keys.allowed(row, "another-model"))
        keys.update_key(key["id"], {"enabled": False})
        self.assertFalse(keys.allowed(keys.find_by_secret(key["secret"]), "only-this"))

    def test_unknown_key_update_reports_failure(self):
        self.assertFalse(keys.update_key(99999, {"enabled": False})["ok"])


class GatewayTests(Isolated):
    def client(self, local=True):
        return TestClient(gateway.create_app(), client=("127.0.0.1" if local else "192.0.2.10", 50000))

    def test_invalid_ports_are_rejected_before_save(self):
        before = settings.path().read_bytes()
        with self.client() as client:
            for values in ({"ui_port": 0}, {"pool_port": 65536}, {"ui_port": "x"}, {"ui_port": True}, {"ui_port": 8322, "pool_port": 8322}):
                with self.subTest(values=values):
                    self.assertEqual(client.put("/api/settings", json=values).status_code, 400)
        self.assertEqual(before, settings.path().read_bytes())

    def test_empty_key_list_not_rewritten_by_route(self):
        with self.client() as client:
            self.assertFalse(client.post("/api/keys", json={"name": "test", "models": ""}).json()["ok"])

    def test_remote_without_password_is_consistently_denied(self):
        with self.client(local=False) as client:
            self.assertFalse(client.get("/api/whoami").json()["allowed"])
            self.assertEqual(client.get("/api/settings").status_code, 401)
            self.assertEqual(client.post("/api/login", json={"password": ""}).status_code, 403)

    def test_remote_login_with_password_grants_session(self):
        settings.save({"admin_password": "temporary-test-password"})
        with self.client(local=False) as client:
            self.assertEqual(client.post("/api/login", json={"password": "wrong"}).status_code, 401)
            self.assertEqual(client.post("/api/login", json={"password": "temporary-test-password"}).status_code, 200)
            self.assertTrue(client.get("/api/whoami").json()["allowed"])
            self.assertEqual(client.get("/api/settings").status_code, 200)

    def test_restart_failure_not_reported_as_success(self):
        with self.client() as client, patch.object(gateway.pool, "restart", return_value={"ok": False, "error": "test port occupied"}):
            data = client.post("/api/providers", json=self.provider()).json()
            self.assertFalse(data["ok"])
            self.assertTrue(data["saved"])
            self.assertIn("test port occupied", data["error"])

    def test_static_assets_and_eight_pages(self):
        with self.client() as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            for page in ("overview", "accounts", "providers", "models", "modeltest", "keys", "logs", "settings"):
                self.assertIn('id="page-' + page + '"', response.text)
            self.assertEqual(client.get("/assets/design.css").status_code, 200)


class ModelTestTests(Isolated):
    def plan(self):
        return [{"file": "test.json", "email": "test@example.invalid", "alias": "test-model", "upstream": "test-model"}]

    def analysis(self):
        return {"prediction": "test-model", "prediction_name": "Test Model", "probability": .95,
                "rows": [{"display_name": "Test Model", "probability": .95}],
                "diagnostics": [{"numbers": 300}], "family_name": "Test", "family_probability": .95}

    def test_overload_retries_same_target_and_logs_both_attempts(self):
        plan = self.plan(); jid = modeltrace._new_job(plan)
        with patch.object(modeltrace, "bank", return_value={"models": [{"id": "test-model"}]}), patch.object(modeltrace, "analyze", return_value=self.analysis()), patch.object(modeltrace, "_ask", side_effect=[{"ok": False, "error": "server_is_overloaded"}, {"ok": True, "text": "1 2 3"}]) as ask, patch.object(modeltrace.time, "sleep"), patch.object(keys, "log") as log:
            modeltrace._worker(jid, plan, [{"prompt": "same challenge", "expected_count": 300}], 1)
            self.assertEqual(modeltrace.get(jid)["status"], "done")
            self.assertTrue(modeltrace.get(jid)["rows"][0]["ok"])
            self.assertEqual(ask.call_count, 2)
            self.assertEqual(ask.call_args_list[0], ask.call_args_list[1])
            self.assertEqual(log.call_count, 2)

    def test_cancel_during_request_stays_cancelled(self):
        plan=self.plan(); jid=modeltrace._new_job(plan)
        def cancel(*args):
            modeltrace.cancel(jid)
            return {"ok": True, "text": "1 2 3"}
        with patch.object(modeltrace, "bank", return_value={"models": []}), patch.object(modeltrace, "_ask", side_effect=cancel), patch.object(modeltrace, "analyze", return_value=self.analysis()):
            modeltrace._worker(jid, plan, [{"prompt": "test", "expected_count": 300}], 1)
            self.assertEqual(modeltrace.get(jid)["status"], "cancelled")
            self.assertEqual(modeltrace.get(jid)["rows"], [])

    def test_missing_bank_becomes_terminal_failure(self):
        jid=modeltrace._new_job(self.plan())
        with patch.object(modeltrace, "bank", side_effect=ValueError("missing test bank")):
            modeltrace._worker(jid, self.plan(), [], 1)
        self.assertEqual(modeltrace.get(jid)["status"], "failed")

    def test_low_confidence_cannot_accuse_known_model(self):
        verdict, _ = modeltrace.judge("model-a", "model-b", .12, {"model-a", "model-b"}, .11)
        self.assertEqual(verdict, "weak")

    def test_google_account_uses_native_endpoint(self):
        with patch.object(codex, "read_auth", return_value={"type": "antigravity", "access_token": "test"}), patch.object(gemini, "chat", return_value={"ok": True, "text": "native"}) as native, patch.object(codex, "chat") as wrong:
            self.assertTrue(modeltrace._ask(self.plan()[0], "test")["ok"])
            native.assert_called_once(); wrong.assert_not_called()


class StreamTests(Isolated):
    def stream(self, events):
        payload="\n\n".join("data: "+json.dumps(event) for event in events)+"\n\n"
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=payload, headers={"content-type": "text/event-stream"}))
        return patch.object(codex, "_client", side_effect=lambda timeout: REAL_HTTP_CLIENT(transport=transport))

    def test_truncated_sse_is_not_success(self):
        with self.stream([{"type": "response.output_text.delta", "delta": "partial text"}]):
            self.assertFalse(codex.chat({"access_token": "test"}, "m", "hi")["ok"])

    def test_completed_sse_returns_text(self):
        with self.stream([{"type": "response.output_text.delta", "delta": "complete"}, {"type": "response.completed", "response": {}}]):
            self.assertEqual(codex.chat({"access_token": "test"}, "m", "hi")["text"], "complete")

    def test_top_level_error_keeps_real_reason(self):
        with self.stream([{"type": "error", "error": {"message": "Servers overloaded", "code": "server_is_overloaded"}}]):
            result=codex.chat({"access_token": "test"}, "m", "hi")
            self.assertIn("server_is_overloaded", result["error"])


class AccountTests(Isolated):
    def test_cannot_delete_outside_auth_directory(self):
        target=paths.data_dir()/"keep.json"; target.write_text("{}")
        self.assertFalse(accounts.delete("..\\..\\keep.json")["ok"])
        self.assertTrue(target.exists())

    def test_cannot_move_google_account_to_chatgpt_pool(self):
        file=paths.auth_dir()/"test.json"; file.write_text(json.dumps({"type": "antigravity", "access_token": "test"}))
        self.assertFalse(accounts.assign_pool(file.name,"plus")["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
