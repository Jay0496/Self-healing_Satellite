"""Ed25519 signatures for uplink commands and software-update manifests (REQ-SEC-02).

Ground signs with the private key; the Pi verifies with the public key only. A signed
document is any JSON object plus a ``signature`` field over its canonical JSON form.
"""
from __future__ import annotations

import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .channel import SecurityError, b64decode, b64encode

SIGNATURE_FIELD = "signature"


def canonical_bytes(document: dict) -> bytes:
    unsigned = {k: v for k, v in document.items() if k != SIGNATURE_FIELD}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sign_document(document: dict, private_key: Ed25519PrivateKey) -> dict:
    if not isinstance(document, dict):
        raise TypeError("only JSON objects can be signed")
    signed = {k: v for k, v in document.items() if k != SIGNATURE_FIELD}
    signed[SIGNATURE_FIELD] = b64encode(private_key.sign(canonical_bytes(signed)))
    return signed


def verify_document(document: object, public_key: Ed25519PublicKey) -> dict:
    """Return the document without its signature, or raise SecurityError."""
    if not isinstance(document, dict):
        raise SecurityError("MALFORMED", "signed document must be an object")
    if SIGNATURE_FIELD not in document:
        raise SecurityError("UNSIGNED", "document has no signature")
    signature = b64decode(document[SIGNATURE_FIELD], SIGNATURE_FIELD)
    try:
        public_key.verify(signature, canonical_bytes(document))
    except (InvalidSignature, ValueError) as exc:
        raise SecurityError("BAD_SIGNATURE", "Ed25519 signature verification failed") from exc
    return {k: v for k, v in document.items() if k != SIGNATURE_FIELD}
