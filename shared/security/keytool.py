"""Key provisioning CLI.

    python -m shared.security.keytool init      # once, on the ground computer
    python -m shared.security.keytool status    # on every device; fingerprints must match

Then copy ``link_master.key`` and ``command_verify_ed25519.pem`` (NOT the signing key)
into the Pi's secrets directory, e.g.:

    ssh pi 'mkdir -p ~/.g29_secrets && chmod 700 ~/.g29_secrets'
    scp ~/.g29_secrets/link_master.key ~/.g29_secrets/command_verify_ed25519.pem pi:~/.g29_secrets/
    ssh pi 'chmod 600 ~/.g29_secrets/*'
"""
from __future__ import annotations

import argparse
import json

from . import keystore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision G29 link keys (stored outside the repo)")
    parser.add_argument("action", choices=("init", "status"))
    parser.add_argument("--dir", default=None, help=f"Secrets directory (default ${keystore.SECRETS_ENV} "
                                                     "or ~/.g29_secrets)")
    parser.add_argument("--force", action="store_true", help="Replace existing keys")
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            paths = keystore.generate(args.dir, force=args.force)
            print("[KEYTOOL] Generated key material (contents are never printed):")
            for name, path in paths.items():
                print(f"  {name:32s} {path}")
            print("[KEYTOOL] Copy link_master.key and command_verify_ed25519.pem to the Pi. "
                  "Keep command_signing_ed25519.pem on ground only.")
        print(json.dumps(keystore.describe(args.dir), indent=2))
        return 0
    except keystore.KeyMaterialError as exc:
        print(f"[KEYTOOL] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
