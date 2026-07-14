"""Tests for the data-preservation gate.

Language: English — identifiers match manifest fields and CLI output.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "data_guard.py"
spec = importlib.util.spec_from_file_location("data_guard_under_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CompareBaselineTest(unittest.TestCase):
    def test_allows_new_points_when_every_old_point_is_unchanged(self):
        baseline = {
            "old-1": {"memory": "first"},
            "old-2": {"memory": "second"},
        }
        live = {
            **baseline,
            "new-1": {"memory": "new"},
        }

        result = module.compare_points(baseline, live)

        self.assertTrue(result["ok"])
        self.assertEqual([], result["missing_ids"])
        self.assertEqual([], result["changed_ids"])
        self.assertEqual(["new-1"], result["new_ids"])

    def test_rejects_missing_or_changed_old_points(self):
        baseline = {
            "old-1": {"memory": "first"},
            "old-2": {"memory": "second"},
        }
        live = {
            "old-2": {"memory": "changed"},
            "new-1": {"memory": "new"},
        }

        result = module.compare_points(baseline, live)

        self.assertFalse(result["ok"])
        self.assertEqual(["old-1"], result["missing_ids"])
        self.assertEqual(["old-2"], result["changed_ids"])
        self.assertEqual(["new-1"], result["new_ids"])


class BackupPreconditionTest(unittest.TestCase):
    def test_missing_history_db_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "backup"

            with self.assertRaises(FileNotFoundError):
                module.create_backup(
                    output,
                    "http://127.0.0.1:1",
                    "mem0_shared",
                    root / "missing-history.db",
                    root / "missing-qdrant",
                )

            self.assertFalse(output.exists())

    def test_source_marks_snapshot_restore_as_verified(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"snapshot_restore_verified": True', source)
        self.assertIn('"qdrant_internal_snapshot_deleted": True', source)
        self.assertIn('method="DELETE"', source)


class AutoBackupPruningTest(unittest.TestCase):
    def test_prunes_only_old_timestamped_complete_backups(self):
        with tempfile.TemporaryDirectory() as root:
            auto_root = Path(root)
            for index in range(10):
                path = auto_root / f"202607140400{index:02d}123456789"
                path.mkdir()
                (path / "manifest.json").write_text("{}", encoding="utf-8")
            protected = auto_root / "pre-install-protected"
            protected.mkdir()
            incomplete = auto_root / "20260714999999123456789"
            incomplete.mkdir()

            removed = module.prune_auto_backups(auto_root, 7)

            self.assertEqual(3, len(removed))
            self.assertTrue(protected.exists())
            self.assertTrue(incomplete.exists())
            complete = [
                path
                for path in auto_root.iterdir()
                if path.name.isdigit() and (path / "manifest.json").is_file()
            ]
            self.assertEqual(7, len(complete))


if __name__ == "__main__":
    unittest.main()
