"""Safety-contract tests for the WSL2 installer.

Language: English — assertions are stable deployment requirements.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
RESTORE = ROOT / "restore.sh"


class InstallScriptContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.install = INSTALL.read_text(encoding="utf-8")
        cls.restore = RESTORE.read_text(encoding="utf-8")

    def test_shell_scripts_parse(self):
        for path in (INSTALL, RESTORE, ROOT / "start-daemon.sh", ROOT / "backup.sh"):
            subprocess.run(["bash", "-n", str(path)], check=True)

    def test_has_pinned_downloads_and_checksums(self):
        self.assertIn("qdrant-x86_64-unknown-linux-gnu.tar.gz", self.install)
        self.assertIn("318a3b1c548161ad476f9ff70b654787a20fc46685e3e1c2b7dd88b363ef3d58", self.install)
        self.assertIn("1ef826ab24cfcf52243ea16fefaf239b8c7fa285", self.install)
        self.assertIn("c5eb8abd440e7778cead911606521f52e1b35067bb648484f2928d83f2b314b4", self.install)
        self.assertIn("sha256sum", self.install)
        self.assertIn("requirements.lock.txt", self.install)
        lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
        self.assertIn("mem0ai==2.0.2", lock)
        self.assertIn("transformers==5.8.0", lock)
        self.assertIn('uv venv --python "$HERMES_PYTHON"', self.install)
        self.assertIn("--reinstall-package mem0ai", self.install)

    def test_has_key_wizard_without_repository_secrets(self):
        self.assertIn("read -r -s", self.install)
        self.assertIn("chmod 600", self.install)
        self.assertIn("bigmodel.cn/usercenter/proj-mgmt/apikeys", self.install)
        self.assertIn("platform.agnes-ai.com", self.install)
        self.assertIn("build.nvidia.com/settings/api-keys", self.install)
        self.assertIn("MEM0_ZHIPU_API_KEY", self.install)

    def test_has_data_and_stock_hermes_gates(self):
        self.assertIn("scripts/data_guard.py", self.install)
        self.assertIn("scripts/verify_install.py", self.install)
        self.assertIn("hermes config set memory.provider mem0", self.install)
        self.assertIn("mem0_shared", self.install)
        self.assertIn(".data-operation.lock", self.install)
        self.assertIn("rollback_hermes_config", self.install)
        self.assertIn('"mode": "http"', self.install)
        self.assertIn("diff --quiet -- plugins/memory/mem0/_backend.py", self.install)
        main = self.install[self.install.index("main() {"):]
        self.assertLess(main.index("protect_existing_data"), main.index("install_python"))
        backup = (ROOT / "backup.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/data_guard.py", backup)
        self.assertIn("backup", backup)

    def test_does_not_patch_hermes_or_include_8051(self):
        combined = self.install + self.restore
        self.assertNotIn("0005-hermes", combined)
        self.assertNotIn("patch -d \"${HERMES_DIR}/hermes-agent\"", combined)
        self.assertNotIn("8051", combined)
        self.assertNotIn("api.mem0.ai", combined)
        self.assertNotIn("git clean", combined)

    def test_has_no_machine_specific_home(self):
        self.assertNotIn("/home/lmr", self.install)
        self.assertIn("SCRIPT_DIR", self.install)

    def test_all_deployment_shell_scripts_have_no_machine_specific_home(self):
        paths = [ROOT / "start.sh", ROOT / "health-check.sh", ROOT / "start-daemon.sh"]
        paths.extend((ROOT / "scripts").glob("*.sh"))
        offenders = [
            str(path.relative_to(ROOT))
            for path in paths
            if "/home/lmr" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([], offenders)

    def test_qdrant_version_check_is_exact(self):
        self.assertIn('== "qdrant 1.17.1"', self.install)

    def test_restore_is_only_a_safe_compatibility_entrypoint(self):
        self.assertIn("install.sh", self.restore)
        self.assertNotIn("--from-snapshot", self.restore)
        self.assertNotIn("git pull", self.restore)

    def test_legacy_hermes_patches_are_removed(self):
        patches = ROOT / "patches"
        for name in (
            "0003-hermes-plugin-pass-host-to-MemoryClient.patch",
            "0004-hermes-plugin-add-http-backend.patch",
            "0005-hermes-httpbackend-mechanicq-api.patch",
        ):
            self.assertFalse((patches / name).exists(), name)

    def test_only_installer_writes_hermes_configuration(self):
        self.assertFalse((ROOT / "scripts" / "apply-hermes-config.sh").exists())
        templates = ROOT / "scripts" / "templates" / "hermes_config"
        self.assertFalse(templates.exists() and any(templates.iterdir()))

    def test_cron_migration_removes_legacy_duplicate_lines(self):
        self.assertIn('f"*/5 * * * * {root}/health-check.sh"', self.install)
        self.assertIn('f"0 */6 * * * {root}/backup.sh"', self.install)
        self.assertIn("line.strip() not in legacy", self.install)

    def test_services_default_to_loopback_and_logs_exclude_memory_content(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        daemon = (ROOT / "start-daemon.sh").read_text(encoding="utf-8")
        self.assertIn('MEM0_HOST = os.environ.get("MEM0_HOST", "127.0.0.1")', server)
        self.assertIn("uvicorn.run(app, host=MEM0_HOST", server)
        self.assertIn("QDRANT__SERVICE__HOST=127.0.0.1", daemon)
        self.assertNotIn("http://localhost:", daemon)
        self.assertNotIn("CORSMiddleware", server)
        self.assertNotIn("[ADD_V3 REQUEST] messages=%s", server)
        self.assertNotIn("[ADD_V3 RESULT] type=%s value=%s", server)
        self.assertNotIn('logger.info("Memory add result: %s", result)', server)
        self.assertIn('logging.getLogger("mem0").setLevel(logging.WARNING)', server)

    def test_tmux_children_do_not_inherit_data_lock(self):
        daemon = (ROOT / "start-daemon.sh").read_text(encoding="utf-8")
        self.assertIn('SESSION_NAME="${MEM0_SESSION_NAME:-mem0}"', daemon)
        self.assertIn('tmux new-session -d -s "$SESSION_NAME" -n mem0 9>&-', daemon)

    def test_cron_backups_rotate_and_health_check_has_lock_timeout(self):
        backup = (ROOT / "backup.sh").read_text(encoding="utf-8")
        health = (ROOT / "health-check.sh").read_text(encoding="utf-8")
        self.assertIn('AUTO_ROOT="$BACKUP_ROOT/auto"', backup)
        self.assertIn("prune-auto", backup)
        self.assertIn('MEM0_BACKUP_KEEP:-7', backup)
        self.assertIn("MEM0_DATA_LOCK_TIMEOUT=30", health)

    def test_legacy_snapshot_entrypoint_delegates_to_consistent_backup(self):
        snapshot = (ROOT / "scripts" / "snapshot.sh").read_text(encoding="utf-8")
        self.assertIn("backup.sh", snapshot)
        self.assertNotIn("cp -a", snapshot)
        self.assertNotIn("data/storage", snapshot)


if __name__ == "__main__":
    unittest.main()
