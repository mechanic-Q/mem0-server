"""
Fallback LLM provider for mem0 — multi-provider fallback chain.

Inherits OpenAILLM to preserve _parse_response() (tool_calls parsing).
Switches provider on errors (rate-limit, timeout, connection, etc.).
Persistent blacklist: 3 consecutive failures → auto-blacklist → skipped until daily reset.

历史沿革（原 patches/0002 描述，于 2026-06-23 合并至此 docstring 并删除孤儿 patch）：
- 2026-05-08 加入 None response guard（free 模型偶尔返回 content: null）
- 2026-05-08 加入 persistent provider blacklist（provider_blacklist.json）
- 2026-05-08 配额错误（402/quota/insufficient/billing）自动加黑
- 2026-05-08 已加黑的 provider 直接跳过，不再消耗 timeout
本文件是仓库自有源文件，不存在被外部覆盖的风险；不再需要 patch 形式维护。
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

# Consecutive failures before auto-blacklisting a provider
BLACKLIST_THRESHOLD = 3


def _load_blacklist():
    """Load blacklist from JSON file. Returns dict {index_str: info_dict}."""
    try:
        if os.path.exists(BLACKLIST_PATH):
            with open(BLACKLIST_PATH, "r") as f:
                data = json.load(f)
                # Filter out pending entries (kept for backward compat)
                return {k: v for k, v in data.items() if not v.get("_pending")}
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


# In-memory consecutive failure tracker (resets on restart)
_failure_counts = {}


class FallbackLLMConfig(BaseLlmConfig):
    """Multi-provider chain config for FallbackLLM."""

    def __init__(self, chain=None, **kwargs):
        super().__init__(**kwargs)
        self.chain = chain or []


class FallbackLLM(OpenAILLM):
    """
    OpenAI-compatible fallback chain with multi-provider switching.

    Inherits OpenAILLM to inherit its _parse_response() logic (tool_calls,
    structured output). Falls back on any error, not just rate-limit.

    Blacklist policy:
    - Quota/billing errors → immediate blacklist
    - Other errors (timeout, connection, 500, etc.) → blacklist after 3 consecutive failures
    - Daily reset clears all blacklists
    """

    RATE_LIMIT_KEYWORDS = (
        "400", "402", "429", "rate", "quota", "capacity",
        "timeout", "timed out", "connection", "unreachable",
        "500", "502", "503", "504",
    )

    # Immediate blacklist (quota exhausted)
    QUOTA_KEYWORDS = ("402", "quota", "insufficient", "billing")

    def __init__(self, config):
        if not config.chain:
            raise ValueError("FallbackLLM requires a non-empty chain config")
        self._providers = config.chain
        primary = self._providers[0]
        config.model = primary["model"]
        config.openai_base_url = primary["base_url"]
        config.api_key = primary["api_key"]
        # True when the last generate_response ended in the graceful empty stub
        # (all providers exhausted) — server.py reads this after add() to queue
        # the turn for replay. Reset at the start of every call.
        self._last_degraded = False
        super().__init__(config)
        # Override client with short timeout (per-provider override via
        # "timeout" key in the chain entry — the local CPU tail needs 120s)
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.openai_base_url,
            timeout=primary.get("timeout", 20.0),
        )

    def generate_response(self, messages, response_format=None,
                          tools=None, tool_choice="auto", **kwargs):
        self._last_degraded = False
        last_error = None
        blacklist = _load_blacklist()
        attempted = 0
        all_null = True

        for i, provider in enumerate(self._providers):
            # Skip blacklisted providers
            if str(i) in blacklist:
                continue

            try:
                attempted += 1
                if i > 0:
                    self.client = OpenAI(
                        api_key=provider["api_key"],
                        base_url=provider["base_url"],
                        timeout=20.0,
                    )
                    self.config.model = provider["model"]
                call_format = response_format
                if (provider.get("json_schema_guarantee")
                        and response_format == {"type": "json_object"}):
                    # 解码层硬保证：llama-server 的 json_object 只是提示级约束
                    #（模型偶发输出 ```json 围栏），json_schema 才走 GBNF 语法
                    # 强制。用裸 {"type": "object"} 保语法不限字段——mem0 的
                    # 提取与去重决策调用字段形状各异，不能锁死 schema。
                    call_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "response",
                            "schema": {"type": "object"},
                        },
                    }
                result = super().generate_response(
                    messages, call_format, tools, tool_choice,
                    timeout=provider.get("timeout", 20.0), **kwargs
                )
                # Guard: some free models return 200 with null content
                if result is None:
                    logger.warning(
                        f"Provider {i} ({provider.get('model','?')}) "
                        "returned None, falling back"
                    )
                    last_error = ValueError("Empty response (None)")
                    self._record_failure(i, provider, str(last_error))
                    continue
                # Success → reset failure counter
                _failure_counts[i] = 0
                return result

            except Exception as e:
                all_null = False
                err = str(e).lower()
                if any(k in err for k in self.RATE_LIMIT_KEYWORDS):
                    model_name = provider.get("model", "unknown")
                    reason = str(e)[:200]

                    # Quota errors → immediate blacklist
                    if any(k in err for k in self.QUOTA_KEYWORDS):
                        blacklist[str(i)] = {
                            "model": model_name,
                            "reason": reason,
                            "blacklisted_at": datetime.now().isoformat(),
                        }
                        _save_blacklist(blacklist)
                        _failure_counts[i] = 0
                        logger.info(
                            f"Provider {i} ({model_name}) blacklisted (quota): {reason[:100]}"
                        )
                    else:
                        # Other errors → count consecutive failures
                        self._record_failure(i, provider, reason)

                    last_error = e
                    continue
                raise

        # All available providers exhausted — flag unconditionally: mem0's
        # Memory.add swallows LLM exceptions internally ("LLM extraction
        # failed"), so server.py can only detect total failure via this flag.
        self._last_degraded = True
        bl_count = len(blacklist)
        total = len(self._providers)
        logger.error(
            f"All providers exhausted (blacklisted: {bl_count}/{total})"
        )
        if attempted and all_null and response_format == {"type": "json_object"}:
            return '{"memory": []}'
        raise last_error or RuntimeError("All providers exhausted")

    def _record_failure(self, i, provider, reason):
        """Track consecutive failures; blacklist after threshold."""
        _failure_counts[i] = _failure_counts.get(i, 0) + 1
        count = _failure_counts[i]
        model_name = provider.get("model", "unknown")

        if count >= BLACKLIST_THRESHOLD:
            blacklist = _load_blacklist()
            blacklist[str(i)] = {
                "model": model_name,
                "reason": f"{count} consecutive failures: {reason[:150]}",
                "blacklisted_at": datetime.now().isoformat(),
            }
            _save_blacklist(blacklist)
            _failure_counts[i] = 0
            logger.info(
                f"Provider {i} ({model_name}) blacklisted after {count} consecutive failures: {reason[:100]}"
            )
        else:
            logger.warning(
                f"Provider {i} ({model_name}) failed ({count}/{BLACKLIST_THRESHOLD}): {reason[:100]}"
            )
