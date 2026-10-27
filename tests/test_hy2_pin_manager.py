import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hy2_pin_manager", HERE / "tools" / "hy2_pin_manager.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@unittest.skipUnless(shutil.which("openssl"), "openssl required")
class HY2CertificateTests(unittest.TestCase):
    def test_generate_reuse_and_pin_matches_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "hy2"
            cert, key, created = mod.ensure_certificate("jp.example.com", root)
            self.assertTrue(created)
            mod.verify_pair(cert, key)
            first = mod.fingerprint(cert)
            self.assertRegex(first, r"^[a-f0-9]{64}$")
            expected = subprocess.check_output(
                ["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
                text=True
            ).split("=", 1)[1].strip().replace(":", "").lower()
            self.assertEqual(first, expected)
            cert2, key2, created2 = mod.ensure_certificate("jp.example.com", root)
            self.assertFalse(created2)
            self.assertEqual((cert, key), (cert2, key2))
            self.assertEqual(first, mod.fingerprint(cert2))
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_partial_files_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jp.example.com"
            path.mkdir()
            (path / "key.pem").write_text("partial")
            with self.assertRaises(mod.PinError):
                mod.ensure_certificate("jp.example.com", Path(tmp))
            self.assertEqual((path / "key.pem").read_text(), "partial")

    def test_bad_domains_rejected(self):
        for value in ["https://bad.example", "x", "bad name.example", "../a.example"]:
            with self.subTest(value=value), self.assertRaises(mod.PinError):
                mod.domain(value)

    def test_invalid_port_hopping_rejected(self):
        for value in ["65535-65536", "3-2", "1-0", "hi", "1000:1001"]:
            with self.subTest(value=value), self.assertRaises(mod.PinError):
                mod.port_range(value)

    def test_uri_contains_pin_and_no_actual_password(self):
        pin = "f" * 64
        result = mod.uri_template("aws.example.com", "60000", "www.example.com", pin, "60000-60002")
        self.assertIn("pinSHA256=" + pin, result)
        self.assertIn("insecure=1", result)
        self.assertIn("mport=60000-60002", result)
        self.assertIn("YOUR_XBOARD_USER_PASSWORD", result)


if __name__ == "__main__":
    unittest.main()
