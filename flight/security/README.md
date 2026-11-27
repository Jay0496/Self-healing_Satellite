# Flight Security

This module is for the onboard endpoint of security responsibilities, such as validating received messages and approved updates. Ground-side encoding and authorization remain in `ground/`; shared packet structures belong in `shared/protocol/`. Component tests are in `tests/`.

Implemented: `update_verifier.py` verifies the Ed25519-signed manifest of a software update
before installation. Packet encryption/decryption is the shared `shared/security` envelope used
by `flight/communications`. See [docs/SECURITY.md](../../docs/SECURITY.md).
