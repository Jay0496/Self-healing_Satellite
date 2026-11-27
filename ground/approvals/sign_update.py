"""Ground: sign an operator-approved software fix so the Pi will accept it (REQ-SEC-02).

    python -m ground.approvals.sign_update --package fix.tar.gz --approved-by "operator name"

Writes ``fix.tar.gz.manifest.json`` next to the package. Only run after sandbox tests
passed and the operator approved the change (CR-15, CR-16).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.security.keystore import KeyMaterialError, load_signing_key
from shared.security.updates import create_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign an approved software update package")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--secrets", default=None)
    args = parser.parse_args(argv)
    try:
        manifest = create_manifest(args.package, load_signing_key(args.secrets),
                                   approved_by=args.approved_by, description=args.description)
    except (KeyMaterialError, OSError) as exc:
        print(f"[SIGN UPDATE] {exc}")
        return 2
    out = args.out or args.package.with_name(args.package.name + ".manifest.json")
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[SIGN UPDATE] {args.package.name} sha256={manifest['package_sha256'][:16]}... -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
