"""
Fallback LLM provider for mem0 — multi-provider fallback chain.

Inherits OpenAILLM to preserve _parse_response() (tool_calls parsing).
Switches provider only on rate-limit errors (402/429/quota/capacity).
Supports persistent blacklist: failed providers are skipped until cleared.
"""
import json
import logging
import os
from datetime import datetime
from openai import OpenAI, DEFAULT_MAX_RETRIES
from mem0.llms.openai import OpenAILLM

logger = logging.getLogger(__name__)
from mem0.configs.llms.base import BaseLlmConfig

BLACKLIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "provider_blacklist.json")


def _load_blacklist():
    """Load blacklist from JSON file. Returns dict {index_str: info_dict}."""
    try:
        if os.path.exists(BLACKLIST_PATH):
            with open(BLACKLIST_PATH, "r") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Blacklist load failed, resetting: {e}")
    return {}


def _save_blacklist(bl):
    """Persist blacklist to JSON file."""
    try:
        with open(BLACKLIST_PATH, "w") as f:
            json.dump(bl, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.warning(f"Blacklist save failed: {e}")


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

    # Keywords that indicate permanent exhaustion (quota/402), not transient
    QUOTA_KEYWORDS = ("402", "quota", "insufficient", "billing")

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
        blacklist = _load_blacklist()

        for i, provider in enumerate(self._providers):
            # Skip blacklisted providers
            if str(i) in blacklist:
                continue

            try:
                if i > 0:
                    self.client = OpenAI(
                        api_key=provider["api_key"],
                        base_url=provider["base_url"],
                        timeout=20.0,
                    )
                    self.config.model = provider["model"]
                # Primary (i==0) already has client from __init__; add timeout
                result = super().generate_response(
                    messages, response_format, tools, tool_choice,
                    timeout=20.0, **kwargs
                )
                # Guard: some free models return 200 with null content
                if result is None:
                    logger.warning(
                        f"Provider {i} ({provider.get('model','?')}) "
                        "returned None, falling back"
                    )
                    last_error = ValueError("Empty response (None)")
                    continue
                return result
            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in self.RATE_LIMIT_KEYWORDS):
                    # Check if this is a quota error → add to blacklist
                    if any(k in err for k in self.QUOTA_KEYWORDS):
                        model_name = provider.get("model", "unknown")
                        blacklist[str(i)] = {
                            "model": model_name,
                            "reason": str(e)[:200],
                            "blacklisted_at": datetime.now().isoformat(),
                        }
                        _save_blacklist(blacklist)
                        logger.info(
                            f"Provider {i} ({model_name}) blacklisted: {str(e)[:100]}"
                        )
                    last_error = e
                    continue
                raise

        # All available providers exhausted
        bl_count = len(blacklist)
        total = len(self._providers)
        logger.error(
            f"All providers exhausted (blacklisted: {bl_count}/{total})"
        )
        raise last_error or RuntimeError("All providers exhausted")
