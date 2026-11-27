"""Loads key material from a secrets directory that is never inside the repository (REQ-SEC-02).

Layout of the secrets directory (default ``~/.g29_secrets``, override with ``G29_SECRETS_DIR``):

=============================  ==========================  =========================
File                           Needed by                   Purpose
=============================  ==========================  =========================
``link_master.key``            Pi **and** ground           Master secret; per-epoch AES-256-GCM link keys are derived from it
``command_signing_ed25519.pem`` ground only                Signs uplink commands and approved software updates
``command_verify_ed25519.pem`` Pi (and ground)             Verifies those signatures onboard
=============================  ==========================  =========================

The Pi never needs (and should never hold) the private signing key.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .channel import DEFAULT_ROTATION_SECONDS, MASTER_KEY_BYTES

SECRETS_ENV = "G29_SECRETS_DIR"
ROTATION_ENV = "G29_KEY_ROTATION_SECONDS"
LINK_KEY_FILE = "link_master.key"
SIGNING_KEY_FILE = "command_signing_ed25519.pem"
VERIFY_KEY_FILE = "command_verify_ed25519.pem"
REPO_ROOT = Path(__file__).resolve().parents[2]


class KeyMaterialError(RuntimeError):
    """Key files are missing, malformed, or stored somewhere unsafe."""


def secrets_dir(override: str | Path | None = None) -> Path:
    raw = override or os.environ.get(SECRETS_ENV) or (Path.home() / ".g29_secrets")
    path = Path(raw).expanduser().resolve()
    if path == REPO_ROOT or REPO_ROOT in path.parents:
        raise KeyMaterialError(
            f"Secrets directory {path} is inside the repository; keys must never be stored "
            f"with source code. Use ~/.g29_secrets or set {SECRETS_ENV}.")
    return path


def rotation_seconds() -> int:
    raw = os.environ.get(ROTATION_ENV)
    if not raw:
        return DEFAULT_ROTATION_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise KeyMaterialError(f"{ROTATION_ENV} must be an integer number of seconds") from exc
    if not 10 <= value <= DEFAULT_ROTATION_SECONDS:
        raise KeyMaterialError(f"{ROTATION_ENV} must be between 10 and 86400 (24 h)")
    return value


def fingerprint(data: bytes) -> str:
    """Short public identifier for key material; safe to print (never print the key itself)."""
    return hashlib.sha256(data).hexdigest()[:16]


def _read(directory: Path, name: str, purpose: str) -> bytes:
    path = directory / name
    if not path.is_file():
        raise KeyMaterialError(
            f"Missing {purpose} at {path}. Run `python -m shared.security.keytool init` "
            f"(see docs/SECURITY.md) and copy the files listed there to each device.")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise KeyMaterialError(f"{path} is readable by other users; run: chmod 600 {path}")
    return path.read_bytes()


def load_link_master_key(directory: str | Path | None = None) -> bytes:
    raw = _read(secrets_dir(directory), LINK_KEY_FILE, "link master key").strip()
    try:
        key = bytes.fromhex(raw.decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise KeyMaterialError(f"{LINK_KEY_FILE} must contain {MASTER_KEY_BYTES * 2} hex characters") from exc
    if len(key) != MASTER_KEY_BYTES:
        raise KeyMaterialError(f"{LINK_KEY_FILE} must contain exactly {MASTER_KEY_BYTES} bytes")
    return key


def load_signing_key(directory: str | Path | None = None) -> Ed25519PrivateKey:
    data = _read(secrets_dir(directory), SIGNING_KEY_FILE, "ground command-signing key")
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyMaterialError(f"{SIGNING_KEY_FILE} is not an Ed25519 private key")
    return key


def load_verify_key(directory: str | Path | None = None) -> Ed25519PublicKey:
    data = _read(secrets_dir(directory), VERIFY_KEY_FILE, "command verification key")
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, Ed25519PublicKey):
        raise KeyMaterialError(f"{VERIFY_KEY_FILE} is not an Ed25519 public key")
    return key


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def generate(directory: str | Path | None = None, *, force: bool = False) -> dict[str, Path]:
    """Create a fresh master key and Ed25519 signing pair. Refuses to overwrite unless forced."""
    target = secrets_dir(directory)
    paths = {name: target / name for name in (LINK_KEY_FILE, SIGNING_KEY_FILE, VERIFY_KEY_FILE)}
    existing = [p for p in paths.values() if p.exists()]
    if existing and not force:
        raise KeyMaterialError(f"Key files already exist ({', '.join(p.name for p in existing)}); "
                               "use --force to replace them (both devices must then be re-provisioned)")
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(target, 0o700)
    _write_private(paths[LINK_KEY_FILE], os.urandom(MASTER_KEY_BYTES).hex().encode("ascii") + b"\n")
    signing = Ed25519PrivateKey.generate()
    _write_private(paths[SIGNING_KEY_FILE], signing.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    _write_private(paths[VERIFY_KEY_FILE], signing.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return paths


def describe(directory: str | Path | None = None) -> dict[str, str]:
    """Fingerprints of whichever key files are present (for comparing Pi vs ground)."""
    target = secrets_dir(directory)
    result = {"directory": str(target)}
    for name in (LINK_KEY_FILE, SIGNING_KEY_FILE, VERIFY_KEY_FILE):
        path = target / name
        result[name] = fingerprint(path.read_bytes().strip()) if path.is_file() else "missing"
    if (target / VERIFY_KEY_FILE).is_file():
        public = load_verify_key(target).public_bytes(serialization.Encoding.Raw,
                                                      serialization.PublicFormat.Raw)
        result["signing_identity"] = fingerprint(public)
    return result
