import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.install_manager import Installer, InstallError, Service, release_url, SCRIPTS

TAG = "v0.4.0"
ASSET = "V2bX-linux-64.zip"


class FakeService:
    def __init__(self, active=False, fail=False):
        self.active = active
        self.fail = fail
        self.actions = []

    def is_active(self):
        return self.active

    def reload(self):
        self.actions.append("reload")

    def start(self, restart):
        self.actions.append("restart" if restart else "start")
        self.active = True

    def verify(self):
        self.actions.append("verify")
        if self.fail:
            self.fail = False
            raise InstallError("new binary did not stay active")

    def stop(self):
        self.actions.append("stop")
        self.active = False


class SafeUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "host"
        self.live = self.root / "usr/local/V2bX"
        self.cfg = self.root / "etc/V2bX"
        self.unit = self.root / "etc/systemd/system/V2bX.service"
        self.cfg.mkdir(parents=True)
        self.live.mkdir(parents=True)
        self.unit.parent.mkdir(parents=True)
        (self.live / "V2bX").write_text("working binary")
        self.unit.write_text("custom unit")
        (self.cfg / "config.json").write_text("live configuration")
        (self.cfg / "geoip.dat").write_text("custom geo data")
        self.repo = self.base / "source"
        self.repo.mkdir()
        self.git("init", "-q")
        for key in SCRIPTS:
            file = self.repo / key
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("original pinned bytes\n" if not key.endswith((".sh", ".py"))
                            else "# original pinned bytes\n")
        self.git("add", ".")
        self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.com",
                 "commit", "-qm", "fixture")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.archive = self.base / "fixture.zip"
        self.make_archive()
        self.service = FakeService()
        self.installer = Installer(self.root, self.service, arch="64")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True)

    def make_archive(self, extra=None):
        with zipfile.ZipFile(self.archive, "w") as archive:
            files = {"V2bX": "#!/bin/sh\nprintf 'v0.4.0\\n'\n", "geoip.dat": "new geo",
                     "geosite.dat": "new geo site", "config.json": "{}",
                     "dns.json": "{}", "route.json": "{}",
                     "custom_outbound.json": "[]", "custom_inbound.json": "[]"}
            files.update(extra or {})
            for name, data in files.items():
                archive.writestr(name, data)
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.metadata = {"tag_name": TAG, "draft": False, "assets": [{
            "name": ASSET, "state": "uploaded", "digest": "sha256:" + self.digest,
            "browser_download_url": release_url(TAG, ASSET),
            "size": self.archive.stat().st_size}]}

    def fetch(self, url, out):
        if url == release_url(TAG, ASSET):
            out.write_bytes(self.archive.read_bytes())
        else:
            out.write_text(json.dumps(self.metadata))

    def run_install(self, **opts):
        with patch("tools.install_manager.download", side_effect=self.fetch):
            self.installer.run(tag=opts.pop("tag", TAG), **opts)

    def assert_old_intact(self):
        self.assertEqual((self.live / "V2bX").read_text(), "working binary")
        self.assertEqual((self.cfg / "config.json").read_text(), "live configuration")
        self.assertEqual(self.unit.read_text(), "custom unit")

    def test_missing_trusted_digest_refuses_without_touching_install(self):
        self.metadata["assets"][0]["digest"] = None
        with self.assertRaisesRegex(InstallError, "SHA-256"):
            self.run_install(core_only=True)
        self.assert_old_intact()
        self.assertEqual(self.service.actions, [])

    def test_archive_hash_mismatch_refuses_without_touching_running_install(self):
        self.service.active = True
        self.metadata["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(InstallError, "SHA-256"):
            self.run_install(core_only=True)
        self.assert_old_intact()
        self.assertEqual(self.service.actions, [])

    def test_interrupted_download_leaves_existing_binary_and_configuration(self):
        def interrupted(url, out):
            out.write_bytes(b"partial")
            raise InstallError("download interrupted")
        with patch("tools.install_manager.download", side_effect=interrupted):
            with self.assertRaisesRegex(InstallError, "interrupted"):
                self.installer.run(tag=TAG, core_only=True, sha256=self.digest)
        self.assert_old_intact()
        self.assertEqual(self.service.actions, [])

    def test_valid_zip_does_not_overwrite_user_configuration_or_start_stopped_service(self):
        self.run_install(core_only=True)
        self.assertEqual((self.cfg / "config.json").read_text(), "live configuration")
        self.assertEqual((self.cfg / "geoip.dat").read_text(), "custom geo data")
        self.assertEqual(self.unit.read_text(), "custom unit")
        self.assertTrue((self.live / "V2bX").read_bytes().startswith(b"#!/bin/sh\n"))
        self.assertEqual(self.service.actions, [])

    def test_failed_active_restart_restores_binary_and_restarts_the_previous_service(self):
        self.service.active = True
        self.service.fail = True
        with self.assertRaisesRegex(InstallError, "restored"):
            self.run_install(core_only=True)
        self.assert_old_intact()
        self.assertTrue(self.service.active)
        self.assertEqual(self.service.actions, ["restart", "verify", "restart", "verify"])

    def test_failed_explicit_start_restores_the_stopped_state(self):
        self.service.fail = True
        with self.assertRaisesRegex(InstallError, "restored"):
            self.run_install(core_only=True, start=True)
        self.assert_old_intact()
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.actions, ["start", "verify", "stop"])

    def test_active_upgrade_restarts_and_verifies_without_replacing_unit(self):
        self.service.active = True
        self.run_install(core_only=True, sha256=self.digest)
        self.assertEqual(self.unit.read_text(), "custom unit")
        self.assertEqual(self.service.actions, ["restart", "verify"])

    def test_script_update_uses_commit_bytes_and_leaves_existing_hook_alone(self):
        script = self.repo / "V2bX.sh"
        script.write_text("# uncommitted bytes\n")
        hook = self.root / "etc/letsencrypt/renewal-hooks/deploy/95-v2bx-native-restart"
        hook.parent.mkdir(parents=True)
        hook.write_text("# user-managed hook\n")
        self.installer.run(scripts_only=True, source=self.repo, revision=self.revision)
        self.assertEqual((self.root / "usr/bin/V2bX").read_text(), "# original pinned bytes\n")
        self.assertEqual(hook.read_text(), "# user-managed hook\n")
        self.assert_old_intact()
        self.assertEqual(self.service.actions, [])

    def test_script_update_replaces_only_known_legacy_stock_hook(self):
        hook = self.root / "etc/letsencrypt/renewal-hooks/deploy/95-v2bx-native-restart"
        hook.parent.mkdir(parents=True)
        hook.write_text("# legacy stock bytes\n")
        legacy = hashlib.sha256(hook.read_bytes()).hexdigest()
        with patch("tools.install_manager.LEGACY_HOOK_SHA256", legacy):
            self.installer.run(scripts_only=True, source=self.repo, revision=self.revision)
        self.assertEqual(hook.read_text(), "# original pinned bytes\n")
        self.assertTrue(hook.stat().st_mode & 0o100)
        self.assertEqual(self.service.actions, [])

    def test_stable_process_using_wrong_binary_fails_health_check(self):
        service = Service(binary=self.live / "V2bX")
        with patch.object(service, "snapshot", return_value=("active", os.getpid(), 0)):
            with self.assertRaisesRegex(InstallError, "不同的核心"):
                service.verify()

    def test_matching_process_must_stay_active_for_settle_period(self):
        service = Service(binary=Path("/proc/self/exe"))
        with patch.object(service, "snapshot", side_effect=[
                ("active", os.getpid(), 0), ("active", os.getpid(), 1)]):
            with patch("tools.install_manager.time.sleep"):
                with self.assertRaisesRegex(InstallError, "restarts"):
                    service.verify()

    def test_fresh_install_seeds_legacy_geo_paths_but_does_not_start_or_enable(self):
        self.unit.unlink()
        (self.live / "V2bX").unlink()
        self.make_archive({"geoip.db": "optional geo database"})
        self.run_install(source=self.repo, revision=self.revision)
        self.assertEqual((self.cfg / "config.json").read_text(), "live configuration")
        self.assertEqual((self.cfg / "geoip.dat").read_text(), "custom geo data")
        self.assertEqual((self.live / "geoip.dat").read_text(), "new geo")
        self.assertEqual((self.live / "geoip.db").read_text(), "optional geo database")
        self.assertTrue(self.unit.exists())
        self.assertEqual(self.service.actions, ["reload"])

    def test_unsafe_archive_is_rejected_even_with_matching_digest(self):
        self.make_archive({"../unexpected": "not a release member"})
        with self.assertRaisesRegex(InstallError, "archive"):
            self.run_install(core_only=True)
        self.assert_old_intact()

    def test_no_version_or_symbolic_revision_does_not_fetch_anything(self):
        with patch("tools.install_manager.download") as downloader:
            with self.assertRaises(InstallError):
                self.installer.run(core_only=True)
            with self.assertRaisesRegex(InstallError, "commit"):
                self.installer.run(scripts_only=True, source=self.repo, revision="HEAD")
            downloader.assert_not_called()
        self.assert_old_intact()


if __name__ == "__main__":
    unittest.main()
