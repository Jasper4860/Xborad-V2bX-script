import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'anytls_manager.py'
spec = importlib.util.spec_from_file_location('anytls_manager', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AnyTLSManagerTests(unittest.TestCase):
    def node(self):
        return mod.build_node('https://example.com/', 'testkey', 123,
                              'node.example.com', 'http',
                              '/etc/V2bX/anytls-123.cer', '/etc/V2bX/anytls-123.key',
                              email='cert@example.com')

    def test_anytls_core_and_protocol(self):
        node = self.node()
        self.assertEqual(node['Core'], 'sing')
        self.assertEqual(node['NodeType'], 'anytls')
        self.assertEqual(node['NodeID'], 123)

    def test_dns_mode_stores_cloudflare_token(self):
        node = mod.build_node('https://example.com', 'key', 10, 'jp.example.com',
                              'dns', '/tmp/example.crt', '/tmp/example.key',
                              email='cert@example.com', provider='cloudflare',
                              dns_env={'CLOUDFLARE_DNS_API_TOKEN': 'testtoken'})
        self.assertEqual(node['CertConfig']['CertMode'], 'dns')
        self.assertEqual(node['CertConfig']['DNSEnv']['CLOUDFLARE_DNS_API_TOKEN'], 'testtoken')

    def test_dns_without_token_rejected(self):
        with self.assertRaises(mod.ConfigError):
            mod.build_node('https://example.com', 'key', 10, 'jp.example.com',
                           'dns', '/tmp/example.crt', '/tmp/example.key',
                           email='cert@example.com', provider='cloudflare')

    def test_acme_paths_are_unique(self):
        one = mod.acme_file_paths(Path('/etc/V2bX'), 50, 'jp.example.com')
        other = mod.acme_file_paths(Path('/etc/V2bX'), 51, 'jp.example.com')
        self.assertNotEqual(one, other)
        self.assertTrue(one[0].startswith('/etc/V2bX/certs/anytls/'))

    def test_existing_nodes_and_cores_preserved(self):
        config = {'Log': {'Level': 'info'}, 'Cores': [{'Type': 'xray'}],
                  'Nodes': [{'ApiHost': 'https://example.com', 'NodeID': 3, 'NodeType': 'vless'}]}
        updated, new_core = mod.build_updated(config, self.node(), Path('/tmp'))
        self.assertTrue(new_core)
        self.assertEqual(updated['Nodes'][0], config['Nodes'][0])
        self.assertEqual(updated['Cores'][0], config['Cores'][0])
        self.assertEqual(config['Nodes'].__len__(), 1)
        self.assertEqual(updated['Log'], config['Log'])

    def test_no_sing_duplication(self):
        config = {'Cores': [{'Type': 'sing'}], 'Nodes': []}
        updated, new_core = mod.build_updated(config, self.node(), Path('/tmp'))
        self.assertFalse(new_core)
        self.assertEqual(len(updated['Cores']), 1)

    def test_duplicate_node_rejected(self):
        config = {'Cores': [], 'Nodes': [self.node()]}
        with self.assertRaises(mod.ConfigError):
            mod.build_updated(config, self.node(), Path('/tmp'))

    def test_bad_panel_url_rejected(self):
        with self.assertRaises(mod.ConfigError):
            mod.validate_host('https://example.com/api/secret')

    def test_bad_tls_domain_rejected(self):
        with self.assertRaises(mod.ConfigError):
            mod.validate_domain('bad domain.example.com')

    def test_bad_tls_files_rejected(self):
        with self.assertRaises(mod.ConfigError):
            mod.build_node('https://example.com', 'key', 123, 'node.example.com',
                           'file', '/missing/cert', '/missing/key')

    def test_backup_and_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            config_path = Path(d) / 'config.json'
            original = {'Cores': [], 'Nodes': []}
            config_path.write_text(json.dumps(original))
            updated, new_core = mod.build_updated(original, self.node(), config_path.parent)
            backup = mod.save(config_path, updated, new_core)
            self.assertEqual(json.loads(backup.read_text()), original)
            self.assertEqual(json.loads(config_path.read_text())['Nodes'][0]['NodeType'], 'anytls')
            self.assertTrue((config_path.parent / 'sing_origin.json').is_file())

    def test_check_rejects_wrong_core(self):
        config = {'Cores': [{'Type': 'sing'}], 'Nodes': [dict(self.node(), Core='xray')]}
        self.assertEqual(mod.check(config), 1)


if __name__ == '__main__':
    unittest.main()
