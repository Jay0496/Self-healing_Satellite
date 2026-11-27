"""REQ-SEC-02: signed commands/updates, key storage outside source code, secret scan."""
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared.mqtt_protocol import validate_command
from shared.security import keystore
from shared.security.channel import SecurityError
from shared.security.signing import sign_document, verify_document
from shared.security.updates import create_manifest, verify_package

REPO = Path(__file__).resolve().parents[2]
DASHES = '-' * 5
# A real PEM private-key block (marker + base64 body); built at runtime so this file never matches.
PEM_PRIVATE_BLOCK = re.compile(DASHES + r'BEGIN [A-Z ]*PRIVATE KEY' + DASHES + r'\s+[A-Za-z0-9+/=\s]{40,}')
# A bare 64-hex-character line, which is how link_master.key is stored.
HEX_MASTER_KEY = re.compile(r'^\s*[0-9a-f]{64}\s*$', re.MULTILINE)


class TestCommandSignatures(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.cmd = {'command_id': 'c1', 'type': 'GET_STATUS', 'expires_at': time.time() + 20}

    def test_signed_command_accepted_by_flight_path(self):
        signed = sign_document(self.cmd, self.key)
        self.assertEqual(validate_command(verify_document(signed, self.key.public_key()))['type'],
                         'GET_STATUS')

    def test_unsigned_forged_or_foreign_commands_rejected(self):
        signed = sign_document(self.cmd, self.key)
        cases = {
            'UNSIGNED': dict(self.cmd),
            'BAD_SIGNATURE': {**signed, 'type': 'REQUEST_LOGS'},
        }
        for reason, doc in cases.items():
            with self.subTest(reason=reason), self.assertRaises(SecurityError) as ctx:
                verify_document(doc, self.key.public_key())
            self.assertEqual(ctx.exception.reason, reason)
        foreign = sign_document(self.cmd, Ed25519PrivateKey.generate())
        with self.assertRaises(SecurityError):
            verify_document(foreign, self.key.public_key())


class TestSignedUpdates(unittest.TestCase):
    def test_only_signed_unmodified_packages_install(self):
        key = Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / 'fsm_rules_v2.json'
            pkg.write_text('{"rule": "reset_i2c_bus"}')
            manifest = create_manifest(pkg, key, approved_by='operator-1')
            self.assertEqual(verify_package(pkg, manifest, key.public_key())['approved_by'], 'operator-1')

            rejected = 0
            attempts = [
                (pkg, {k: v for k, v in manifest.items() if k != 'signature'}, key),  # unsigned
                (pkg, create_manifest(pkg, Ed25519PrivateKey.generate(), approved_by='x'), key),
                (pkg, {**manifest, 'approved_by': 'attacker'}, key),                 # edited manifest
            ]
            pkg2 = Path(td) / 'fsm_rules_v2_tampered.json'
            pkg2.write_text('{"rule": "rm -rf /"}')
            attempts.append((pkg2, manifest, key))                                   # swapped package
            for package, doc, k in attempts:
                try:
                    verify_package(package, doc, k.public_key())
                except SecurityError:
                    rejected += 1
            self.assertEqual(rejected, len(attempts))   # 100% of unsigned/invalid updates rejected


class TestKeyStorage(unittest.TestCase):
    def test_generate_load_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            keystore.generate(td)
            self.assertEqual(len(keystore.load_link_master_key(td)), 32)
            keystore.load_signing_key(td)
            keystore.load_verify_key(td)
            with self.assertRaises(keystore.KeyMaterialError):
                keystore.generate(td)
            info = keystore.describe(td)
            self.assertEqual(len(info['signing_identity']), 16)

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions only')
    def test_world_readable_key_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            keystore.generate(td)
            os.chmod(Path(td) / keystore.LINK_KEY_FILE, 0o644)
            with self.assertRaises(keystore.KeyMaterialError):
                keystore.load_link_master_key(td)

    def test_secrets_directory_inside_repo_refused(self):
        for inside in (REPO, REPO / 'shared' / 'security'):
            with self.assertRaises(keystore.KeyMaterialError):
                keystore.secrets_dir(inside)

    def test_missing_keys_give_actionable_error(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(keystore.KeyMaterialError) as ctx:
            keystore.load_link_master_key(td)
        self.assertIn('keytool init', str(ctx.exception))

    def test_rotation_policy_capped_at_24h(self):
        old = os.environ.get(keystore.ROTATION_ENV)
        try:
            os.environ[keystore.ROTATION_ENV] = str(25 * 3600)
            with self.assertRaises(keystore.KeyMaterialError):
                keystore.rotation_seconds()
            os.environ[keystore.ROTATION_ENV] = '60'
            self.assertEqual(keystore.rotation_seconds(), 60)
        finally:
            if old is None:
                os.environ.pop(keystore.ROTATION_ENV, None)
            else:
                os.environ[keystore.ROTATION_ENV] = old


class TestSecretScan(unittest.TestCase):
    """Zero plaintext keys in source control (REQ-SEC-02 'Secret Scan')."""

    def test_no_key_material_tracked(self):
        try:
            files = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard'], cwd=REPO,
                                   capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('git not available')
        names = {keystore.LINK_KEY_FILE, keystore.SIGNING_KEY_FILE, keystore.VERIFY_KEY_FILE}
        offenders = []
        for rel in files:
            path = REPO / rel
            if path.name in names or path.suffix in ('.key', '.pem'):
                offenders.append(rel)
                continue
            if path.suffix in ('.py', '.md', '.json', '.txt', '.sh', '.yml', '.yaml', '.cfg', '.toml'):
                text = path.read_text(encoding='utf-8', errors='ignore')
                if PEM_PRIVATE_BLOCK.search(text) or HEX_MASTER_KEY.search(text):
                    offenders.append(rel)
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
