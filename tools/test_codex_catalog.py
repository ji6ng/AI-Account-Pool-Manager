"""Offline catalog conflict regressions; never touch the real client home."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import clients, gateway


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aipool-catalog-")
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"AIPOOL_CLIENT_HOME": str(self.home)})
        self.env.start()
        self.key = {"id": 1, "name": "test", "secret": "offline-test-key", "models": "*", "model_list": ["*"]}
        self.key_patch = patch.object(clients.keys, "list_keys", return_value=[self.key])
        self.key_patch.start()
        self.backup_patch = patch.object(clients, "_backup_dir", return_value=self.home)
        self.backup_patch.start()
        self.config = self.home / ".codex" / "config.toml"
        self.config.parent.mkdir()
        self.base = "http://127.0.0.1:19326"

    def tearDown(self):
        self.backup_patch.stop()
        self.key_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def apply(self):
        return clients.apply("codex", self.base, "plus-gpt-6.1-sol", 1)

    def detect(self):
        return next(item for item in clients.detect(self.base) if item["id"] == "codex")

    def test_root_catalog_disabled_backed_up_and_idempotent(self):
        original = (b'# keep this\r\nmodel_catalog_json = "/foreign/catalog.json" # original\r\n'
                    b'model_catalog_url = "unknown-root-key"\r\n'
                    b'[other]\r\nmodel_catalog_json = "nested"\r\n')
        self.config.write_bytes(original)
        self.assertIn("model_catalog_json", self.detect()["warning"])
        self.assertEqual(self.detect()["status"], "conflict")
        result = self.apply()
        self.assertEqual(result["disabled"], ["model_catalog_json"])
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        written = self.config.read_bytes()
        self.assertIn(('# [AI 号池已停用，原值保留] model_catalog_json = '
                       '"/foreign/catalog.json" # original\r\n').encode(), written)
        self.assertIn(b'model_catalog_json = "/foreign/catalog.json" # original\r\n', written)
        self.assertTrue(written.startswith(b'# keep this\r\n'))
        self.assertIn(b'model_catalog_url = "unknown-root-key"\r\n', written)
        self.assertIn(b'[other]\r\nmodel_catalog_json = "nested"\r\n', written)
        parsed = tomllib.loads(written.decode())
        self.assertNotIn("model_catalog_json", parsed)
        self.assertEqual(parsed["other"]["model_catalog_json"], "nested")
        self.assertEqual(parsed["model_providers"]["cliproxy"]["model_catalog_url"], self.base + "/v1/models")
        self.assertNotIn("warning", self.detect())
        self.assertEqual(self.detect()["status"], "ok")
        self.assertEqual(self.apply()["disabled"], [])
        self.assertEqual(self.config.read_bytes(), written)

    def test_nested_catalog_and_existing_comment_untouched(self):
        original = '# model_catalog_json = "old"\n[other]\nmodel_catalog_json = "nested"\n'
        self.config.write_text(original)
        self.assertNotIn("warning", self.detect())
        self.assertEqual(self.apply()["disabled"], [])
        self.assertIn(original.split("\n", 1)[0], self.config.read_text())
        self.assertIn('[other]\nmodel_catalog_json = "nested"\n', self.config.read_text())

    def test_scanner_preserves_non_root_text(self):
        for original in (
            'text = """\nmodel_catalog_json = "not a key"\n[not a table]\n"""\n',
            '[[other]]\nmodel_catalog_json = "nested"\n',
        ):
            with self.subTest(original=original):
                self.assertEqual(clients._disable_root_catalog(original), (original, []))

    def test_quoted_and_multiline_catalog_values(self):
        for assignment in (
            '"model_catalog_json" = "/foreign/catalog.json"\n',
            "'model_catalog_json' = '''\n/foreign/catalog.json\n'''\n",
        ):
            original = 'note = """\n[not a table]\nmodel_catalog_json = "not a key"\n"""\n' + assignment + '[other]\nkeep = true\n'
            with self.subTest(assignment=assignment):
                updated, disabled = clients._disable_root_catalog(original)
                self.assertEqual(disabled, ["model_catalog_json"])
                parsed = tomllib.loads(updated)
                self.assertNotIn("model_catalog_json", parsed)
                self.assertEqual(parsed["note"], tomllib.loads(original)["note"])
                self.assertTrue(parsed["other"]["keep"])

    def test_warning_coexists_with_drift_and_parse_error_wins(self):
        self.config.write_text('model_catalog_json = "foreign"\n[model_providers.cliproxy]\nbase_url = "http://old/v1"\n')
        self.assertEqual(self.detect()["status"], "drift")
        self.assertIn("warning", self.detect())
        self.config.write_text('model_catalog_json = "unterminated\n')
        self.assertEqual(self.detect()["status"], "broken")

    def test_failed_verification_restores_original_catalog(self):
        original = b'model_catalog_json = "foreign"\r\n'
        self.config.write_bytes(original)
        with patch.object(clients, "_verify", side_effect=ValueError("offline verification failure")):
            with self.assertRaisesRegex(ValueError, "已还原"):
                self.apply()
        self.assertEqual(self.config.read_bytes(), original)

    def test_gateway_codex_headers_serve_base_instructions(self):
        entries = [("plus-gpt-6.1-sol", "Test model", "Offline test")]
        for headers in ({"originator": "codex_cli_rs"}, {"originator": "codex_exec"},
                        {"user-agent": "codex_cli_rs/0.164.0"}):
            with self.subTest(headers=headers):
                scope = {"type": "http", "method": "GET", "path": "/v1/models",
                         "query_string": b"client_version=0.164.0", "headers":
                         [(name.encode(), value.encode()) for name, value in {**headers, "authorization": "Bearer offline"}.items()]}
                request = Request(scope, AsyncMock(return_value={"type": "http.request", "body": b""}))
                with patch.object(gateway.settings, "load", return_value={}), \
                     patch.object(gateway.keys, "find_by_secret", return_value=self.key), \
                     patch.object(gateway.keys, "allowed", return_value=True), \
                     patch.object(gateway.keys, "log"), \
                     patch.object(gateway, "_codex_entries", return_value=entries):
                    response = asyncio.run(gateway._forward("v1/models", request))
                model = json.loads(response.body)["models"][0]
                self.assertEqual(model["slug"], entries[0][0])
                self.assertTrue(model["base_instructions"].startswith("You are Codex"))
        request = Request({"type": "http", "headers": [], "query_string": b"client_version=0.164.0"})
        self.assertFalse(gateway._is_codex(request))


if __name__ == "__main__":
    unittest.main()
