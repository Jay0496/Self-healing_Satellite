"""Flight: verify a software update's signed manifest before installation (REQ-SEC-02).

    python -m flight.security.update_verifier --package fix.tar.gz

Exit code 0 only if the package matches a manifest signed by the ground key. Any future
installer must call ``verify_package`` (or this CLI) and abort on failure.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from shared.security.channel import SecurityError
from shared.security.keystore import KeyMaterialError, load_verify_key
from shared.security.updates import load_manifest, verify_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a signed software update on the Pi")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--secrets", default=None)
    args = parser.parse_args(argv)
    manifest_path = args.manifest or args.package.with_name(args.package.name + ".manifest.json")
    try:
        body = verify_package(args.package, load_manifest(manifest_path), load_verify_key(args.secrets))
    except (SecurityError, KeyMaterialError) as exc:
        print(f"[UPDATE VERIFY] REJECTED {args.package.name}: {exc}")
        return 1
    print(f"[UPDATE VERIFY] ACCEPTED {body['package_name']} approved_by={body['approved_by']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
