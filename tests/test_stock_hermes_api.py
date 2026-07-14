"""Contract tests for Hermes' stock SelfHostedBackend routes.

Language: English — test identifiers mirror the upstream HTTP contract.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeMemory:
    def __init__(self):
        self.calls: list[tuple] = []

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))
        return {"results": [{"id": "memory-1", "memory": "test"}]}

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {"results": [{"id": "memory-1", "memory": "test", "score": 0.9}]}

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"id": kwargs["memory_id"], "memory": kwargs["data"]}

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))


class FakeMemoryFactory:
    instance = FakeMemory()

    @classmethod
    def from_config(cls, _config):
        cls.instance = FakeMemory()
        return cls.instance


def load_server_module():
    temp_dir = Path(tempfile.mkdtemp(prefix="mem0-server-contract-"))
    shutil.copy2(REPO_ROOT / "server.py", temp_dir / "server.py")
    (temp_dir / ".zhipu_key").write_text("test-key\n", encoding="utf-8")

    fake_mem0 = types.ModuleType("mem0")
    fake_mem0.Memory = FakeMemoryFactory
    fake_utils = types.ModuleType("mem0.utils")
    fake_utils.__path__ = []
    fake_factory = types.ModuleType("mem0.utils.factory")
    fake_factory.LlmFactory = type("LlmFactory", (), {"provider_to_class": {}})
    fake_factory.EmbedderFactory = type("EmbedderFactory", (), {"provider_to_class": {}})
    fake_fallback = types.ModuleType("fallback_llm")
    fake_fallback.FallbackLLM = type("FallbackLLM", (), {})
    fake_fallback.FallbackLLMConfig = type("FallbackLLMConfig", (), {})

    injected = {
        "mem0": fake_mem0,
        "mem0.utils": fake_utils,
        "mem0.utils.factory": fake_factory,
        "fallback_llm": fake_fallback,
    }
    previous = {name: sys.modules.get(name) for name in injected}
    sys.modules.update(injected)

    module_name = "mem0_server_contract_under_test"
    spec = importlib.util.spec_from_file_location(module_name, temp_dir / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        for name, old in previous.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module, temp_dir


class StockHermesApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.temp_dir = load_server_module()
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        shutil.rmtree(cls.temp_dir)

    def test_add_forwards_stock_self_hosted_payload(self):
        payload = {
            "messages": [{"role": "user", "content": "remember this"}],
            "user_id": "test-user",
            "agent_id": "hermes",
            "infer": False,
            "metadata": {"source": "contract-test"},
        }

        response = self.client.post("/memories", json=payload)

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            ("add", payload),
            self.server.mem0_client.calls[-1],
        )

    def test_search_forwards_stock_filters_and_limit(self):
        payload = {
            "query": "needle",
            "filters": {"user_id": "test-user", "agent_id": "hermes"},
            "top_k": 7,
        }

        response = self.client.post("/search", json=payload)

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            (
                "search",
                {
                    "query": "needle",
                    "filters": payload["filters"],
                    "top_k": 7,
                },
            ),
            self.server.mem0_client.calls[-1],
        )

    def test_update_translates_stock_text_field_without_changing_id(self):
        response = self.client.put(
            "/memories/memory-1",
            json={"text": "updated text"},
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            ("update", {"memory_id": "memory-1", "data": "updated text"}),
            self.server.mem0_client.calls[-1],
        )

    def test_delete_forwards_stock_memory_id(self):
        response = self.client.delete("/memories/memory-1")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            ("delete", {"memory_id": "memory-1"}),
            self.server.mem0_client.calls[-1],
        )


if __name__ == "__main__":
    unittest.main()
