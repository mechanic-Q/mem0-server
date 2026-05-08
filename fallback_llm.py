"""
Fallback LLM provider for mem0 — multi-provider fallback chain.

Inherits OpenAILLM to preserve _parse_response() (tool_calls parsing).
Switches provider only on rate-limit errors (402/429/quota/capacity).
"""
from openai import OpenAI, DEFAULT_MAX_RETRIES
from mem0.llms.openai import OpenAILLM
from mem0.configs.llms.base import BaseLlmConfig


class FallbackLLMConfig(BaseLlmConfig):
    """Multi-provider chain config for FallbackLLM."""

    def __init__(self, chain=None, **kwargs):
        super().__init__(**kwargs)
        self.chain = chain or []


class FallbackLLM(OpenAILLM):
    """
    OpenAI-compatible fallback chain with multi-provider switching.

    Inherits OpenAILLM to inherit its _parse_response() logic (tool_calls,
    structured output). Only falls back on rate-limit errors.
    """

    RATE_LIMIT_KEYWORDS = (
        "400", "402", "429", "rate", "quota", "capacity",
        "timeout", "timed out", "connection", "unreachable",
        "500", "502", "503", "504",
    )

    def __init__(self, config):
        if not config.chain:
            raise ValueError("FallbackLLM requires a non-empty chain config")
        self._providers = config.chain
        primary = self._providers[0]
        config.model = primary["model"]
        config.openai_base_url = primary["base_url"]
        config.api_key = primary["api_key"]
        super().__init__(config)
        # Override client with short timeout
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.openai_base_url,
            timeout=20.0,
        )

    def generate_response(self, messages, response_format=None,
                          tools=None, tool_choice="auto", **kwargs):
        last_error = None
        for i, provider in enumerate(self._providers):
            try:
                if i > 0:
                    self.client = OpenAI(
                        api_key=provider["api_key"],
                        base_url=provider["base_url"],
                        timeout=20.0,
                    )
                    self.config.model = provider["model"]
                # Primary (i==0) already has client from __init__; add timeout
                return super().generate_response(
                    messages, response_format, tools, tool_choice,
                    timeout=20.0, **kwargs
                )
            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in self.RATE_LIMIT_KEYWORDS):
                    last_error = e
                    continue
                raise
        raise last_error or RuntimeError("All providers exhausted")
