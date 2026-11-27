"""Signed software-update manifests (REQ-SEC-02, CR-17).

A manifest binds a package's SHA-256, size and name to an Ed25519 signature made by the
ground signing key after operator approval. The Pi recomputes the hash and verifies the
signature before anything is installed; unsigned, re-signed-by-another-key, or modified
packages are rejected.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .channel import SecurityError
from .signing import sign_document, verify_document

MANIFEST_TYPE = "G29_SOFTWARE_UPDATE"
MAX_PACKAGE_BYTES = 200_000_000


def _digest(package: Path) -> tuple[str, int]:
    sha, size = hashlib.sha256(), 0
    with package.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
            size += len(chunk)
            if size > MAX_PACKAGE_BYTES:
                raise SecurityError("TOO_LARGE", "package exceeds size limit")
    return sha.hexdigest(), size


def create_manifest(package: Path, private_key: Ed25519PrivateKey, *, approved_by: str,
                    description: str = "") -> dict:
    digest, size = _digest(package)
    return sign_document({
        "type": MANIFEST_TYPE, "package_name": package.name, "package_sha256": digest,
        "package_size": size, "approved_by": approved_by, "description": description,
        "signed_at": time.time(),
    }, private_key)


def verify_package(package: Path, manifest: object, public_key: Ed25519PublicKey) -> dict:
    """Return the verified manifest or raise SecurityError. Call before *every* install."""
    body = verify_document(manifest, public_key)
    if body.get("type") != MANIFEST_TYPE:
        raise SecurityError("BAD_MANIFEST", "not a software-update manifest")
    if not package.is_file():
        raise SecurityError("MISSING_PACKAGE", str(package))
    digest, size = _digest(package)
    if digest != body.get("package_sha256") or size != body.get("package_size"):
        raise SecurityError("HASH_MISMATCH", "package contents do not match the signed manifest")
    return body


def load_manifest(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SecurityError("BAD_MANIFEST", f"cannot read manifest: {exc}") from exc
