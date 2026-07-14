"""Unit tests for the installation verifier.

Language: English — names mirror the stock Hermes contract.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_install.py"
spec = importlib.util.spec_from_file_location("verify_install_under_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StockBackendSourceTest(unittest.TestCase):
    def test_accepts_official_routes(self):
        source = '''
class SelfHostedBackend:
    def search(self):
        return self._json("POST", "/search")
    def add(self):
        return self._json("POST", "/memories")
    def update(self, memory_id):
        return self._json("PUT", f"/memories/{memory_id}")
    def delete(self, memory_id):
        return self._json("DELETE", f"/memories/{memory_id}")
'''
        self.assertTrue(module.is_stock_backend_source(source))

    def test_rejects_mechanic_q_client_patch(self):
        source = '''
class SelfHostedBackend:
    def search(self):
        return self._json("POST", "/v3/memories/search/")
    def add(self):
        return self._json("POST", "/v3/memories/add/")
'''
        self.assertFalse(module.is_stock_backend_source(source))


class ResultRowsTest(unittest.TestCase):
    def test_unwraps_mem0_result_shapes(self):
        rows = [{"id": "one", "memory": "value"}]
        self.assertEqual(rows, module.result_rows(rows))
        self.assertEqual(rows, module.result_rows({"results": rows}))
        self.assertEqual([], module.result_rows({}))


if __name__ == "__main__":
    unittest.main()
