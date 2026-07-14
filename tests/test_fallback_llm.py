"""Regression tests for the repository-owned LLM boundary.

Language: English — assertions encode stable provider behavior.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fallback_llm
from fallback_llm import FallbackLLM


class NullResponseTest(unittest.TestCase):
    def test_all_null_provider_responses_become_empty_memory_json(self):
        llm = object.__new__(FallbackLLM)
        llm._providers = [
            {
                "model": "null-model",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key": "test-only",
            }
        ]
        llm.config = SimpleNamespace(model="null-model")
        fallback_llm._failure_counts.clear()

        with patch(
            "mem0.llms.openai.OpenAILLM.generate_response",
            return_value=None,
        ):
            result = llm.generate_response(
                [{"role": "user", "content": "test"}],
                response_format={"type": "json_object"},
            )

        self.assertEqual('{"memory": []}', result)

    def test_null_non_json_responses_are_not_silenced(self):
        llm = object.__new__(FallbackLLM)
        llm._providers = [
            {
                "model": "null-model",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key": "test-only",
            }
        ]
        llm.config = SimpleNamespace(model="null-model")
        fallback_llm._failure_counts.clear()

        with patch(
            "mem0.llms.openai.OpenAILLM.generate_response",
            return_value=None,
        ):
            with self.assertRaisesRegex(ValueError, "Empty response"):
                llm.generate_response([{"role": "user", "content": "test"}])

    def test_non_null_provider_errors_are_not_silenced(self):
        llm = object.__new__(FallbackLLM)
        llm._providers = [
            {
                "model": "broken-model",
                "base_url": "http://127.0.0.1:1/v1",
                "api_key": "test-only",
            }
        ]
        llm.config = SimpleNamespace(model="broken-model")
        fallback_llm._failure_counts.clear()

        with patch(
            "mem0.llms.openai.OpenAILLM.generate_response",
            side_effect=ValueError("invalid structured response"),
        ):
            with self.assertRaisesRegex(ValueError, "invalid structured response"):
                llm.generate_response(
                    [{"role": "user", "content": "test"}],
                    response_format={"type": "json_object"},
                )


if __name__ == "__main__":
    unittest.main()
