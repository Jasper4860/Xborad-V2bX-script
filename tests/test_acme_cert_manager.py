import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acme_cert_manager", ROOT / "tools" / "acme_cert_manager.py")
acme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acme)


class FakeResponse:
    def __init__(self, obj):
        self.data = io.BytesIO(json.dumps(obj).encode())

    def __enter__(self):
        return self.data

    def __exit__(self, *args):
        self.data.close()


class AcmeTests(unittest.TestCase):
    def test_certbot_dns_flags(self):
        args = acme.certbot_command("dns", "jp.wuya.me", "admin@example.com", "/root/cf.ini")
        self.assertIn("--dns-cloudflare", args)
        self.assertIn("--dns-cloudflare-credentials", args)
        self.assertIn("--keep-until-expiring", args)
        self.assertNotIn("SECRET_TOKEN", " ".join(args))

    def test_no_email_falls_back_to_acme_flag(self):
        args = acme.certbot_command("http", "jp.wuya.me", "")
        self.assertIn("--register-unsafely-without-email", args)

    def test_invalid_domain_rejected_before_issuance(self):
        for domain in ["https://example.com", "../bad.example.com", "bad name.net", "nodot"]:
            with self.subTest(domain=domain), self.assertRaises(acme.AcmeError):
                acme.validate_domain(domain)

    def test_cloudflare_authorized_zone(self):
        queries = []
        def opener(req, timeout):
            queries.append(req.full_url)
            if "name=wuya.me" in req.full_url:
                return FakeResponse({"success": True, "result": [{"name": "wuya.me"}]})
            return FakeResponse({"success": True, "result": []})
        result = acme.verify_cloudflare_zone("jp.wuya.me", "token123", opener)
        self.assertEqual(result, "wuya.me")
        self.assertEqual(len(queries), 2)

    def test_cloudflare_service_provider_domain_refused(self):
        def opener(req, timeout):
            return FakeResponse({"success": True, "result": []})
        with self.assertRaisesRegex(acme.AcmeError, "无权管理"):
            acme.verify_cloudflare_zone("jp.8885566.xyz", "token123", opener)

    def test_credentials_secure_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "dns.ini"
            old = acme.update_credentials(path, "testtoken123")
            self.assertIsNone(old)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn(b"testtoken123", path.read_bytes())
            acme.rollback_credentials(path, old)
            self.assertFalse(path.exists())

    def test_credentials_restore_previous(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "dns.ini"
            previous = acme.update_credentials(path, "oldtoken")
            self.assertIsNone(previous)
            old = acme.update_credentials(path, "newtoken")
            acme.rollback_credentials(path, old)
            self.assertIn(b"oldtoken", path.read_bytes())
            self.assertNotIn(b"newtoken", path.read_bytes())

    def test_issuance_failure_restores_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(acme, "dependencies_ready", return_value=True), \
                 patch.object(acme, "verify_cloudflare_zone", return_value="wuya.me"), \
                 patch.object(acme, "credentials_file", return_value=root / "cf.ini"), \
                 patch.object(acme, "run_certbot", side_effect=acme.AcmeError("acme failed")):
                with self.assertRaises(acme.AcmeError):
                    acme.issue_certificate("dns", "jp.wuya.me", "test@example.com", "validtoken")
            self.assertFalse((root / "cf.ini").exists())


if __name__ == "__main__":
    unittest.main()
