#!/usr/bin/env python3
"""Verify mem0-server through an unmodified Hermes Mem0 provider.

Language: English — JSON output is a stable machine-readable contract.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
from typing import Any


STOCK_MARKERS = (
    '"POST", "/memories"',
    '"POST", "/search"',
    '"PUT", f"/memories/{memory_id}"',
    '"DELETE", f"/memories/{memory_id}"',
)
PATCHED_MARKERS = ("/v3/memories/", "/v1/memories/{memory_id}")


def is_stock_backend_source(source: str) -> bool:
    return all(marker in source for marker in STOCK_MARKERS) and not any(
        marker in source for marker in PATCHED_MARKERS
    )


def result_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    return []


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def _extract_stock_plugin(repo: pathlib.Path, destination: pathlib.Path) -> tuple[pathlib.Path, str]:
    backend_path = "plugins/memory/mem0/_backend.py"
    backend_source = _git(repo, "show", f"HEAD:{backend_path}")
    if not is_stock_backend_source(backend_source):
        raise RuntimeError(
            "Hermes Git HEAD is not the stock self-hosted contract; "
            "expected /memories and /search without mechanic-Q v1/v3 routes"
        )
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "plugins/memory/mem0").splitlines()
    for relative in files:
        if not relative.endswith(".py"):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_git(repo, "show", f"HEAD:{relative}"), encoding="utf-8")
    for relative in ("plugins/__init__.py", "plugins/memory/__init__.py"):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(_git(repo, "show", f"HEAD:{relative}"), encoding="utf-8")
        except subprocess.CalledProcessError:
            target.write_text("", encoding="utf-8")
    return destination, hashlib.sha256(backend_source.encode()).hexdigest()


def _get_all(host: str, user_id: str, agent_id: str) -> list[dict]:
    query = urllib.parse.urlencode({"user_id": user_id, "agent_id": agent_id})
    with urllib.request.urlopen(f"{host.rstrip('/')}/v1/memories?{query}", timeout=60) as response:
        return result_rows(json.load(response))


def _find_id(payload: dict, token: str) -> str | None:
    token = token.lower()
    for item in payload.get("results", []):
        if token in str(item.get("memory", "")).lower() and item.get("id"):
            return str(item["id"])
    return None


def verify(host: str, hermes_repo: pathlib.Path, timeout: float) -> dict[str, Any]:
    if not (hermes_repo / ".git").exists():
        raise RuntimeError(f"Hermes source is not a Git repository: {hermes_repo}")
    temp_root = pathlib.Path(tempfile.mkdtemp(prefix="mem0-stock-hermes-verify-"))
    stock_root = temp_root / "stock"
    isolated_home = temp_root / "home"
    stock_root.mkdir()
    isolated_home.mkdir()
    stock_root, backend_sha = _extract_stock_plugin(hermes_repo, stock_root)

    run = uuid.uuid4().hex[:10]
    user_id = f"mem0-install-verify-{run}"
    agent_id = "hermes-install-verifier"
    (isolated_home / "mem0.json").write_text(
        json.dumps(
            {
                "mode": "http",
                "host": host.rstrip("/"),
                "user_id": user_id,
                "agent_id": agent_id,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    old_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(isolated_home)
    sys.path.insert(0, str(stock_root))
    sys.path.insert(1, str(hermes_repo))
    provider = None
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "stock_backend_sha256": backend_sha,
        "isolated_user_id": user_id,
        "checks": checks,
    }

    def check(name: str, condition: bool, evidence: Any) -> None:
        checks.append({"name": name, "pass": bool(condition), "evidence": evidence})
        if not condition:
            raise AssertionError(f"{name}: {evidence}")

    def tool(name: str, args: dict) -> dict:
        assert provider is not None
        payload = json.loads(provider.handle_tool_call(name, args))
        check(f"{name} returns no error", "error" not in payload, payload)
        return payload

    try:
        module = importlib.import_module("plugins.memory.mem0")
        provider = module.Mem0MemoryProvider()
        provider.initialize("install-verifier", platform="cli")
        prompt = provider.system_prompt_block()
        check(
            "stock provider selects self-hosted HTTP mode",
            "self-hosted (HTTP API)" in prompt,
            prompt.splitlines()[1],
        )

        direct = f"VERIFY-DIRECT-{run}"
        updated = f"VERIFY-UPDATED-{run}"
        inferred = f"VERIFY-INFER-{run}"

        add_payload = tool("mem0_add", {"content": f"Install verification marker is {direct}."})
        direct_search = tool("mem0_search", {"query": direct, "top_k": 20, "rerank": False})
        memory_id = _find_id(direct_search, direct)
        check(
            "stock provider add/search round trip",
            memory_id is not None,
            {"add": add_payload, "memory_id": memory_id},
        )

        update_payload = tool(
            "mem0_update",
            {"memory_id": memory_id, "text": f"Install verification marker is {updated}."},
        )
        updated_search = tool("mem0_search", {"query": updated, "top_k": 20, "rerank": False})
        updated_id = _find_id(updated_search, updated)
        check(
            "stock provider update preserves memory id",
            updated_id == memory_id,
            {"update": update_payload, "expected": memory_id, "found": updated_id},
        )

        delete_payload = tool("mem0_delete", {"memory_id": memory_id})
        deleted_search = tool("mem0_search", {"query": updated, "top_k": 20, "rerank": False})
        check(
            "stock provider delete removes memory",
            _find_id(deleted_search, updated) is None,
            {"delete": delete_payload, "deleted_id": memory_id},
        )

        provider.sync_turn(
            f"My permanent install verification codename is {inferred}.",
            "Understood.",
            session_id="install-verifier",
        )
        thread = provider._sync_thread
        if thread:
            thread.join(timeout=timeout)
        check(
            "sync_turn worker completed",
            thread is not None and not thread.is_alive(),
            {"thread_present": thread is not None, "thread_alive": thread.is_alive() if thread else None},
        )
        inferred_search = tool("mem0_search", {"query": inferred, "top_k": 20, "rerank": False})
        check(
            "stock provider sync_turn infer=true round trip",
            _find_id(inferred_search, inferred) is not None,
            {"result_count": len(inferred_search.get("results", []))},
        )
    finally:
        if provider is not None and provider._backend is not None:
            for item in _get_all(host, user_id, agent_id):
                memory_id = item.get("id")
                if memory_id:
                    try:
                        provider._backend.delete(memory_id)
                    except Exception:
                        pass
            remaining = _get_all(host, user_id, agent_id)
            report["cleanup_remaining_count"] = len(remaining)
            report["cleanup_remaining_ids"] = [item.get("id") for item in remaining]
            provider.shutdown()
        else:
            report["cleanup_remaining_count"] = None
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        shutil.rmtree(temp_root, ignore_errors=True)

    report["all_pass"] = (
        bool(checks)
        and all(item["pass"] for item in checks)
        and report.get("cleanup_remaining_count") == 0
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:8050")
    parser.add_argument("--hermes-repo", type=pathlib.Path, default=pathlib.Path.home() / ".hermes" / "hermes-agent")
    parser.add_argument("--sync-timeout", type=float, default=180.0)
    args = parser.parse_args()
    try:
        report = verify(args.host, args.hermes_repo, args.sync_timeout)
    except Exception as exc:
        print(json.dumps({"all_pass": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
