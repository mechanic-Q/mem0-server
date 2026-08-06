#!/usr/bin/env python3
"""Regression checks for standalone embeddings server and daemon scripts.

stdlib-only: no pytest dependency.
"""

from __future__ import annotations

import json
import pathlib
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class EmbeddingServerRegressionTests(unittest.TestCase):
    def test_start_daemon_requires_embedding_in_already_running_guard(self):
        script = read("start-daemon.sh")
        self.assertIn(
            "if health_check_mem0 && health_check_qdrant && health_check_embedding; then",
            script,
        )

    def test_embeddings_endpoint_is_sync_def_not_async_blocking_event_loop(self):
        src = read("embeddings-server.py")
        self.assertIn("def create_embeddings(req: EmbeddingRequest):", src)
        self.assertNotIn("async def create_embeddings", src)

    def test_embedding_server_has_batch_function_and_no_per_item_embed_loop(self):
        src = read("embeddings-server.py")
        self.assertIn("def embed_texts(texts: list[str])", src)
        self.assertIn("session.run(", src)
        self.assertNotIn("for i, text in enumerate(texts):\n        emb = embed_text(text)", src)

    def test_embedding_server_uses_independent_model_dir_env_and_file_logging(self):
        src = read("embeddings-server.py")
        self.assertIn("KALM_MODEL_DIR", src)
        self.assertIn("EMBEDDING_LOG_FILE", src)
        self.assertIn("RotatingFileHandler", src)

    def test_portproxy_script_documents_8051_and_uses_distro_name_when_available(self):
        script = pathlib.Path.home().joinpath(".local/bin/wsl2-portproxy-sync.sh").read_text(encoding="utf-8")
        self.assertIn("8051", script)
        self.assertRegex(script.lower(), r"embedding|embeddings")
        self.assertIn("WSL_DISTRO_NAME", script)

    def test_powershell_setup_documents_8051_and_uses_distro_name(self):
        ps1 = pathlib.Path("/mnt/c/Users/lmr/wsl2-portproxy-setup.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("8051", ps1)
        self.assertRegex(ps1.lower(), r"embedding|embeddings")
        self.assertIn("$distro", ps1)

    def test_windows_admin_scheduled_task_scripts_exist(self):
        sync = pathlib.Path("/mnt/c/Users/lmr/wsl2-portproxy-sync-admin.ps1").read_text(encoding="utf-8-sig")
        install = pathlib.Path("/mnt/c/Users/lmr/wsl2-portproxy-install-task.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("8051", sync)
        self.assertIn("Register-ScheduledTask", install)
        self.assertIn("RunLevel Highest", install)

    def test_health_check_monitors_embeddings_server(self):
        script = read("health-check.sh")
        self.assertIn("8051", script)
        self.assertIn("embeddings", script.lower())

    def test_health_check_waits_for_start_daemon_foreground(self):
        script = read("health-check.sh")
        self.assertNotIn("nohup \"$SCRIPT_DIR/start-daemon.sh\" start", script)
        self.assertIn("\"$SCRIPT_DIR/start-daemon.sh\" start", script)
        self.assertIn("for _ in", script)

    def test_running_embedding_server_openai_shape_if_available(self):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8051/v1/embeddings",
                data=json.dumps({"input": ["alpha", "beta"], "model": "KaLM-Q4F16"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError):
            self.skipTest("embedding server is not running")
            return

        self.assertEqual(payload["object"], "list")
        self.assertEqual(payload["model"], "KaLM-Q4F16")
        self.assertEqual(len(payload["data"]), 2)
        self.assertEqual([item["index"] for item in payload["data"]], [0, 1])
        self.assertTrue(all(len(item["embedding"]) == 896 for item in payload["data"]))
        self.assertGreaterEqual(payload["usage"]["total_tokens"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
