#!/usr/bin/env python3
"""Create and verify a data-preservation baseline for mem0-server.

Language: English — output keys are stable machine-readable contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


def _request(base_url: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scroll_points(base_url: str, collection: str) -> dict[str, Any]:
    points: dict[str, Any] = {}
    offset: str | int | None = None
    while True:
        body: dict[str, Any] = {"limit": 256, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = _request(
            base_url,
            f"/collections/{urllib.parse.quote(collection, safe='')}/points/scroll",
            method="POST",
            body=body,
        )["result"]
        for point in result["points"]:
            points[str(point["id"])] = point.get("payload")
        offset = result.get("next_page_offset")
        if offset is None:
            return points


def _write_points(path: Path, points: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for point_id in sorted(points):
            stream.write(
                json.dumps(
                    {"id": point_id, "payload": points[point_id]},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def _read_points(path: Path) -> dict[str, Any]:
    points: dict[str, Any] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            points[str(row["id"])] = row.get("payload")
    return points


def compare_points(baseline: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Allow additions but reject a missing or mutated baseline point."""
    missing = sorted(set(baseline) - set(live))
    changed = sorted(point_id for point_id in set(baseline) & set(live) if baseline[point_id] != live[point_id])
    new = sorted(set(live) - set(baseline))
    return {
        "ok": not missing and not changed,
        "baseline_count": len(baseline),
        "live_count": len(live),
        "missing_ids": missing,
        "changed_ids": changed,
        "new_ids": new,
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _restore_snapshot(snapshot_path: Path, collection: str, qdrant_bin: Path) -> tuple[dict[str, Any], dict]:
    """Restore one snapshot into an isolated Qdrant and return its exact state."""
    if not qdrant_bin.is_file() or not os.access(qdrant_bin, os.X_OK):
        raise FileNotFoundError(f"Qdrant binary is missing or not executable: {qdrant_bin}")
    http_port, grpc_port = _free_port(), _free_port()
    while grpc_port == http_port:
        grpc_port = _free_port()
    with tempfile.TemporaryDirectory(prefix="mem0-qdrant-restore-") as temporary:
        root = Path(temporary)
        config = root / "config.yaml"
        config.write_text(
            "storage:\n"
            f"  storage_path: {root / 'storage'}\n"
            f"  snapshots_path: {root / 'snapshots'}\n"
            "service:\n"
            "  host: 127.0.0.1\n"
            f"  http_port: {http_port}\n"
            f"  grpc_port: {grpc_port}\n"
            "telemetry_disabled: true\n",
            encoding="utf-8",
        )
        log_path = root / "qdrant.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    str(qdrant_bin),
                    "--config-path",
                    str(config),
                    "--snapshot",
                    f"{snapshot_path}:{collection}",
                    "--force-snapshot",
                    "--disable-telemetry",
                ],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        base_url = f"http://127.0.0.1:{http_port}"
        try:
            for _ in range(120):
                if process.poll() is not None:
                    tail = log_path.read_text(errors="replace")[-4000:]
                    raise RuntimeError(f"Isolated Qdrant exited early; log: {tail}")
                try:
                    _request(base_url, "/collections")
                    break
                except Exception:
                    time.sleep(0.25)
            else:
                raise RuntimeError("Isolated Qdrant did not become ready")
            points = _scroll_points(base_url, collection)
            info = _request(
                base_url,
                f"/collections/{urllib.parse.quote(collection, safe='')}",
            )["result"]
            return points, info
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _baseline_name(manifest: dict) -> str:
    name = manifest.get("baseline_file") or manifest.get("live_baseline_file")
    if not name:
        raise RuntimeError("Backup manifest has no payload baseline file")
    return str(name)


def restore_verify(backup: Path, qdrant_bin: Path) -> dict[str, Any]:
    """Restore a snapshot into an isolated Qdrant and compare every payload."""
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    baseline = _read_points(backup / _baseline_name(manifest))
    restored, info = _restore_snapshot(
        backup / manifest["snapshot_file"], manifest["collection"], qdrant_bin
    )
    comparison = compare_points(baseline, restored)
    comparison.update(
        {
            "restore_exact": not comparison["new_ids"],
            "collection_status": info["status"],
        }
    )
    comparison["ok"] = (
        comparison["ok"]
        and comparison["restore_exact"]
        and info["status"] == "green"
    )
    return comparison


def create_backup(
    output: Path,
    base_url: str,
    collection: str,
    history_db: Path,
    qdrant_bin: Path,
) -> dict[str, Any]:
    if not history_db.is_file():
        raise FileNotFoundError(f"Mem0 history database does not exist: {history_db}")
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    working = Path(tempfile.mkdtemp(prefix=f".{output.name}.partial-", dir=output.parent))
    try:
        encoded_collection = urllib.parse.quote(collection, safe="")
        snapshot = _request(
            base_url,
            f"/collections/{encoded_collection}/snapshots?wait=true",
            method="POST",
            body={},
        )["result"]
        snapshot_name = snapshot["name"]
        snapshot_path = working / snapshot_name
        snapshot_url = (
            base_url.rstrip("/")
            + f"/collections/{encoded_collection}/snapshots/"
            + urllib.parse.quote(snapshot_name, safe="")
        )
        with urllib.request.urlopen(snapshot_url, timeout=300) as response, snapshot_path.open("wb") as target:
            shutil.copyfileobj(response, target, 1024 * 1024)
        snapshot_sha256 = _sha256(snapshot_path)
        if snapshot.get("checksum") and snapshot["checksum"] != snapshot_sha256:
            raise RuntimeError("Downloaded snapshot checksum does not match Qdrant")

        history_copy = working / "history.db"
        source = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True)
        target = sqlite3.connect(history_copy)
        with target:
            source.backup(target)
        target.close()
        source.close()
        os.chmod(history_copy, 0o600)

        check = sqlite3.connect(f"file:{history_copy}?mode=ro", uri=True)
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            history_rows = check.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        except sqlite3.Error:
            history_rows = None
        check.close()
        if integrity != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {integrity}")

        points, restored_info = _restore_snapshot(snapshot_path, collection, qdrant_bin)
        if restored_info["status"] != "green":
            raise RuntimeError(f"Restored collection is not green: {restored_info['status']}")
        baseline_path = working / "id-payload.jsonl"
        _write_points(baseline_path, points)

        live = _scroll_points(base_url, collection)
        live_comparison = compare_points(points, live)
        if not live_comparison["ok"]:
            raise RuntimeError(
                "Live collection lost or changed snapshot points during backup: "
                f"missing={live_comparison['missing_ids'][:10]} "
                f"changed={live_comparison['changed_ids'][:10]}"
            )

        _request(
            base_url,
            f"/collections/{encoded_collection}/snapshots/"
            + urllib.parse.quote(snapshot_name, safe="")
            + "?wait=true",
            method="DELETE",
        )

        manifest = {
            "created_at": datetime.now().astimezone().isoformat(),
            "collection": collection,
            "collection_status": restored_info["status"],
            "collection_points": len(points),
            "collection_config": restored_info["config"],
            "snapshot": snapshot,
            "snapshot_file": snapshot_name,
            "snapshot_sha256": snapshot_sha256,
            "snapshot_restore_verified": True,
            "qdrant_internal_snapshot_deleted": True,
            "history_db_file": history_copy.name,
            "history_db_sha256": _sha256(history_copy),
            "history_db_integrity": integrity,
            "history_rows": history_rows,
            "baseline_file": baseline_path.name,
            "baseline_sha256": _sha256(baseline_path),
            "live_count_at_backup_end": len(live),
            "new_points_during_backup": len(live_comparison["new_ids"]),
        }
        manifest_path = working / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        sums = [
            (snapshot_path, manifest["snapshot_sha256"]),
            (history_copy, manifest["history_db_sha256"]),
            (baseline_path, manifest["baseline_sha256"]),
            (manifest_path, _sha256(manifest_path)),
        ]
        (working / "SHA256SUMS").write_text(
            "".join(f"{digest}  {path.name}\n" for path, digest in sums),
            encoding="utf-8",
        )
        working.rename(output)
        return {"ok": True, "backup_dir": str(output), "manifest": manifest}
    except Exception:
        shutil.rmtree(working, ignore_errors=True)
        raise


def verify_backup(backup: Path, base_url: str) -> dict[str, Any]:
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    baseline_name = _baseline_name(manifest)
    baseline_sha256 = manifest.get("baseline_sha256") or manifest.get("live_baseline_sha256")
    if not baseline_sha256:
        raise RuntimeError("Backup manifest has no payload baseline checksum")
    for filename, expected in (
        (manifest["snapshot_file"], manifest["snapshot_sha256"]),
        (manifest["history_db_file"], manifest["history_db_sha256"]),
        (baseline_name, baseline_sha256),
    ):
        actual = _sha256(backup / filename)
        if actual != expected:
            raise RuntimeError(f"Checksum mismatch: {filename}")
    check = sqlite3.connect(backup / manifest["history_db_file"])
    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    check.close()
    if integrity != "ok":
        raise RuntimeError(f"SQLite backup integrity check failed: {integrity}")
    baseline = _read_points(backup / baseline_name)
    live = _scroll_points(base_url, manifest["collection"])
    comparison = compare_points(baseline, live)
    info = _request(
        base_url,
        f"/collections/{urllib.parse.quote(manifest['collection'], safe='')}",
    )["result"]
    expected_vectors = manifest["collection_config"]["params"]["vectors"]
    comparison.update(
        {
            "collection_status": info["status"],
            "vector_config_unchanged": info["config"]["params"]["vectors"] == expected_vectors,
        }
    )
    comparison["ok"] = comparison["ok"] and info["status"] == "green" and comparison["vector_config_unchanged"]
    return comparison


def prune_auto_backups(auto_root: Path, keep: int) -> list[str]:
    """Prune only complete, timestamp-named automatic backup directories."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if not auto_root.is_dir():
        return []
    candidates = sorted(
        (
            path
            for path in auto_root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and path.name.isdigit()
            and len(path.name) == 23
            and (path / "manifest.json").is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    removed = []
    for path in candidates[keep:]:
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "verify", "restore-verify", "prune-auto"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--collection", default="mem0_shared")
    parser.add_argument("--history-db", type=Path, default=Path.home() / ".mem0" / "history.db")
    parser.add_argument("--qdrant-bin", type=Path, default=Path.home() / ".local" / "bin" / "qdrant")
    parser.add_argument("--auto-root", type=Path)
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    if args.command == "backup":
        if not args.output:
            parser.error("backup requires --output")
        result = create_backup(
            args.output,
            args.qdrant_url,
            args.collection,
            args.history_db,
            args.qdrant_bin,
        )
    elif args.command == "verify":
        if not args.backup:
            parser.error("verify requires --backup")
        result = verify_backup(args.backup, args.qdrant_url)
    elif args.command == "restore-verify":
        if not args.backup:
            parser.error("restore-verify requires --backup")
        result = restore_verify(args.backup, args.qdrant_bin)
    else:
        if not args.auto_root:
            parser.error("prune-auto requires --auto-root")
        result = {"ok": True, "removed": prune_auto_backups(args.auto_root, args.keep)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
