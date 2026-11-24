"""Offline ACME contract tests: temporary certificates and fixture-only service calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import acme_cert_manager as acme

REQUIRE_AUTOMATIC_RENEWAL = acme.require_automatic_renewal
SERVICE_STABLE = acme.service_stable


class ManagedAcmeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.acme_root = self.root / "letsencrypt"
        self.deployed = self.root / "managed"
        self.config = self.root / "config.json"
        self.domain = "node.example.com"
        self.cert, self.key = acme.certificate_paths(self.domain, self.acme_root)
        for name in ("service_lock", "service_stable", "require_automatic_renewal"):
            patcher = patch.object(acme, name)
            self.addCleanup(patcher.stop)
            setattr(self, name, patcher.start())
        self.service_lock.return_value = nullcontext()
        self.service_stable.return_value = True

    def make_pair(self, domain=None, cert=None, key=None, days=7):
        cert = cert or self.cert
        key = key or self.key
        cert.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-days", str(days), "-subj", "/CN=test", "-addext",
                        "subjectAltName=DNS:" + (domain or self.domain),
                        "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True)

    def config_pair(self, pair, domain=None):
        self.config.write_text(json.dumps({"Nodes": [{"CertConfig": {
            "CertMode": "file", "CertDomain": domain or self.domain,
            "CertFile": str(pair[0]), "KeyFile": str(pair[1]),
        }}]}))

    def test_webroot_does_not_use_standalone(self):
        cmd = acme.certbot_command("http", self.domain, "", webroot=self.root)
        self.assertIn("--webroot", cmd)
        self.assertIn(str(self.root), cmd)
        self.assertNotIn("--standalone", cmd)

    def test_fresh_lineage_requires_automatic_renewal_ready_before_issuance(self):
        self.require_automatic_renewal.side_effect = acme.AcmeError("renewal timer unavailable")
        with patch.object(acme.os, "geteuid", return_value=0), \
             patch.object(acme, "dependencies_ready", return_value=True), \
             patch.object(acme, "run_certbot") as run:
            with self.assertRaisesRegex(acme.AcmeError, "renewal timer unavailable"):
                acme.issue_certificate("http", self.domain, webroot=self.root,
                                       acme_root=self.acme_root, deployed_root=self.deployed)
            run.assert_not_called()

    def test_shipped_hook_hash_and_timer_readiness(self):
        hook = TOOLS / "certbot-v2bx-deploy-hook.sh"
        self.assertEqual(hashlib.sha256(hook.read_bytes()).hexdigest(), acme.DEPLOY_HOOK_SHA256)
        installed = self.root / hook.name
        installed.write_bytes(hook.read_bytes())
        installed.chmod(0o755)
        # Run in the unprivileged test container, but simulate ownership as in /etc.
        original_stat = Path.stat
        def root_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == installed:
                return type("Stat", (), {"st_uid": 0, "st_mode": info.st_mode})()
            return info
        with patch.object(Path, "stat", root_stat), patch.object(acme, "systemctl") as systemctl:
            systemctl.side_effect = [True, False, False]
            with self.assertRaisesRegex(acme.AcmeError, "timer"):
                REQUIRE_AUTOMATIC_RENEWAL(installed)
            systemctl.side_effect = [True, True]
            REQUIRE_AUTOMATIC_RENEWAL(installed)
            installed.write_text("# custom hook")
            systemctl.reset_mock()
            with self.assertRaisesRegex(acme.AcmeError, "钩子"):
                REQUIRE_AUTOMATIC_RENEWAL(installed)
            systemctl.assert_not_called()

    def test_restart_health_fails_if_pid_changed(self):
        with patch.object(acme, "service_identity", side_effect=[(10, 0), (11, 1)]), \
             patch.object(acme.time, "sleep"):
            self.assertFalse(SERVICE_STABLE())

    def test_new_certonly_does_not_run_unrelated_directory_hooks(self):
        cmd = acme.certbot_command("http", self.domain, "", webroot=self.root)
        self.assertIn("--no-directory-hooks", cmd)

    def test_bad_webroot_refused(self):
        for value in (Path("relative"), self.root / "no-such-dir"):
            with self.subTest(value=value), self.assertRaises(acme.AcmeError):
                acme.certbot_command("http", self.domain, "", webroot=value)

    def test_busy_port_does_not_call_certbot(self):
        with patch.object(acme.os, "geteuid", return_value=0), \
             patch.object(acme, "dependencies_ready", return_value=True), \
             patch.object(acme, "standalone_port_available", return_value=False, create=True), \
             patch.object(acme, "run_certbot") as run:
            with self.assertRaisesRegex(acme.AcmeError, "80"):
                acme.issue_certificate("http", self.domain, acme_root=self.acme_root,
                                       deployed_root=self.deployed)
            run.assert_not_called()

    def test_existing_dns_renewal_is_preserved_without_token_or_hooks(self):
        self.make_pair()
        renewal = self.acme_root / "renewal" / (acme.certificate_name(self.domain) + ".conf")
        renewal.parent.mkdir()
        renewal.write_text("[renewalparams]\nauthenticator = dns-cloudflare\n")
        with patch.object(acme.os, "geteuid", return_value=0), \
             patch.object(acme, "dependencies_ready", return_value=True), \
             patch.object(acme, "run_certbot") as run, \
             patch.object(acme, "verify_cloudflare_zone") as cloudflare, \
             patch.object(acme, "standalone_port_available", create=True) as port:
            result = acme.issue_certificate("http", self.domain, acme_root=self.acme_root,
                                            deployed_root=self.deployed)
            run.assert_not_called()
            cloudflare.assert_not_called()
            port.assert_not_called()
            self.assertEqual(result, (self.cert, self.key))

    def test_file_only_lineage_not_overwritten(self):
        self.make_pair()
        with patch.object(acme.os, "geteuid", return_value=0), \
             patch.object(acme, "run_certbot") as run:
            with self.assertRaises(acme.AcmeError):
                acme.issue_certificate("dns", self.domain, acme_root=self.acme_root,
                                       deployed_root=self.deployed)
            run.assert_not_called()

    def test_explicit_certificate_rejects_wrong_san_and_key(self):
        self.make_pair("other.example.com")
        with self.assertRaisesRegex(acme.AcmeError, "SAN"):
            acme.validate_certificate(self.cert, self.key, self.domain)
        self.make_pair()
        other_cert, other_key = self.root / "other.crt", self.root / "other.key"
        self.make_pair(cert=other_cert, key=other_key)
        with self.assertRaises(acme.AcmeError):
            acme.validate_certificate(self.cert, other_key, self.domain)

    def test_older_direct_matching_lineage_and_missing_domain(self):
        self.cert.parent.mkdir(parents=True)
        archive = self.acme_root / "archive" / self.cert.parent.name
        first, second = archive / "fullchain1.pem", archive / "fullchain2.pem"
        old_key, new_key = archive / "privkey1.pem", archive / "privkey2.pem"
        self.make_pair(cert=first, key=old_key)
        self.make_pair(cert=second, key=new_key)
        self.cert.symlink_to(second)
        self.key.symlink_to(new_key)
        self.config_pair((self.cert, self.key))
        config = json.loads(self.config.read_text())
        del config["Nodes"][0]["CertConfig"]["CertDomain"]
        self.config.write_text(json.dumps(config))
        with patch.object(acme, "systemctl", return_value=True) as systemctl:
            for _ in range(2):
                self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                    self.acme_root, self.deployed), 0)
            self.assertEqual(sum(call.args[0] == "try-restart" for call in systemctl.call_args_list), 1)
            self.assertEqual(self.cert.resolve(), second)

    def test_invalid_renewal_leaves_known_working_pair(self):
        self.make_pair()
        deployed = acme.stage_certificate(self.cert.parent.name, self.cert, self.key,
                                          [self.domain], self.deployed)
        known = deployed[0].read_bytes()
        self.config_pair(deployed)
        self.make_pair("other.example.com")
        with patch.object(acme, "systemctl", create=True) as systemctl:
            self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                self.acme_root, self.deployed), 1)
            systemctl.assert_not_called()
        self.assertEqual(deployed[0].read_bytes(), known)

    def test_unrelated_unchanged_and_inactive_do_not_restart(self):
        self.make_pair()
        deployed = acme.stage_certificate(self.cert.parent.name, self.cert, self.key,
                                          [self.domain], self.deployed)
        with patch.object(acme, "systemctl", create=True) as systemctl:
            self.config_pair((self.root / "other.pem", self.root / "other.key"))
            self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                self.acme_root, self.deployed), 0)
            self.config_pair(deployed)
            self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                self.acme_root, self.deployed), 0)
            systemctl.assert_not_called()
            self.make_pair()
            systemctl.return_value = False
            self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                self.acme_root, self.deployed), 0)
            systemctl.assert_called_once_with("is-active", "--quiet", "V2bX.service")
            self.assertEqual(deployed[0].read_bytes(), self.cert.read_bytes())

    def test_active_changed_deploys_only_once(self):
        self.make_pair()
        deployed = acme.stage_certificate(self.cert.parent.name, self.cert, self.key,
                                          [self.domain], self.deployed)
        self.config_pair(deployed)
        self.make_pair()
        with patch.object(acme, "systemctl", return_value=True, create=True) as systemctl:
            for _ in range(2):
                self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                    self.acme_root, self.deployed), 0)
            self.assertEqual(sum(call.args[0] == "try-restart" for call in systemctl.call_args_list), 1)
            self.assertEqual(deployed[0].read_bytes(), self.cert.read_bytes())

    def test_failed_restart_restores_previous_atomic_pair(self):
        self.make_pair()
        deployed = acme.stage_certificate(self.cert.parent.name, self.cert, self.key,
                                          [self.domain], self.deployed)
        known = tuple(path.read_bytes() for path in deployed)
        self.config_pair(deployed)
        self.make_pair()
        self.assertNotEqual(known[0], self.cert.read_bytes())
        with patch.object(acme, "systemctl", side_effect=[True, False, True], create=True) as systemctl:
            self.assertEqual(acme.deploy_renewal(self.cert.parent, self.config,
                                                self.acme_root, self.deployed), 1)
            self.assertIn("restart", [call.args[0] for call in systemctl.call_args_list])
        self.assertEqual(tuple(path.read_bytes() for path in deployed), known)
        self.assertEqual(deployed[1].stat().st_mode & 0o777, 0o600)


class WizardTests(unittest.TestCase):
    def wizard(self, answers):
        shell = r"""
            source "$1"
            NodeType=anytls
            certmode=none certemail='' certprovider='' certdnsenv='{}'
            python3() {
                case "$2" in
                    reuse|tracked) return 1 ;;
                    deps) return 0 ;;
                    issue) printf 'ISSUE:%s\n' "${*:3}"; return 1 ;;
                    validate) printf 'VALIDATE:%s\n' "${*:3}"; return 0 ;;
                    *) return 2 ;;
                esac
            }
            if configure_node_tls; then printf 'MODE:%s\n' "$certmode"; else exit 1; fi
        """
        path = TOOLS.parent / "initconfig.sh"
        return subprocess.run(["bash", "-c", shell, "test", str(path)], input=answers,
                              capture_output=True, text=True, check=False)

    def test_http_failure_requires_explicit_existing_certificate(self):
        outcome = self.wizard("\nnode.example.com\n/var/www/example\n\nn\n")
        self.assertEqual(outcome.returncode, 1, outcome.stdout + outcome.stderr)
        self.assertIn("ISSUE:--mode http", outcome.stdout)
        self.assertIn("--webroot /var/www/example", outcome.stdout)
        self.assertNotIn("VALIDATE:", outcome.stdout)
        self.assertNotIn("MODE:", outcome.stdout)

    def test_accepting_fallback_only_selects_validated_file_mode(self):
        outcome = self.wizard("\nnode.example.com\n\n\ny\n/etc/site.crt\n/etc/site.key\n")
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        self.assertIn("ISSUE:--mode http", outcome.stdout)
        self.assertNotIn("--webroot", outcome.stdout)
        self.assertIn("VALIDATE:--domain node.example.com --cert /etc/site.crt --key /etc/site.key", outcome.stdout)
        self.assertIn("MODE:file", outcome.stdout)


if __name__ == "__main__":
    unittest.main()
