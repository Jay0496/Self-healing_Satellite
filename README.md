# Autonomous Mission Control Agent for Self-Healing IoT Satellites

A self-governing autonomous mission control agent designed to act as a "digital immune system" for satellites. The system monitors real-time telemetry, detects anomalies, and executes automated recovery actions to mitigate the operational risks of high-latency space communication and orbital blackouts.

## Core Features & Capabilities
- **Edge-Based Anomaly Detection:** Continuously monitors spacecraft telemetry to automatically recognize unusual or abnormal behavior onboard before it escalates into critical failures.
- **Deterministic Recovery & State Management:** Relies on structured, predictable rules to execute self-healing actions and track the system's operational health status post-recovery.
- **Fault Simulation & Testing:** Incorporates simulated communication blackouts and controlled hardware/software failure injection to rigorously test system resilience under harsh orbital conditions.
- **Hybrid Ground Pipeline:** Routes telemetry and unresolved faults to ground services for analysis. The ground software is intended to run locally first, with cloud deployment kept as an infrastructure option.
- **Operator Dashboard & HITL Oversight:** Features a centralized web interface that provides mission controllers with real-time telemetry visibility, comprehensive audit trails, and Human-in-the-Loop (HITL) authorization controls for high-risk actions and software patches.

## Repository Layout

This monorepo is organized around the system boundary where each component runs:

- `flight/` — onboard monitoring, anomaly detection, deterministic decision and recovery logic, communication handling, and local buffering. Flight operation should not depend on an available ground link.
- `ground/` — telemetry ingestion and storage, unresolved-fault diagnosis, sandboxing, operator approvals, and the mission dashboard. Ground services should be runnable locally; cloud-specific deployment is optional.
- `simulator/` — telemetry replay, controlled fault injection, and simulated communication or link conditions for repeatable development and testing.
- `shared/protocol/` — shared message formats and interfaces only; flight and ground implementations should not depend on each other's runtime code.
- `tests/` — end-to-end tests spanning multiple components. Component-specific tests remain with their component.

## Setup and security

Install dependencies with `python -m pip install -r requirements.txt` and provision link keys
once with `python -m shared.security.keytool init` (keys live in `~/.g29_secrets`, never in the
repo). All space-ground packets are AES-256-GCM encrypted, replay-protected, and ground commands
are Ed25519-signed; see [docs/SECURITY.md](docs/SECURITY.md). Demo instructions:
[docs/ONE_COMMAND_DEMO.md](docs/ONE_COMMAND_DEMO.md). Run tests with `python -m pytest`.

The directory READMEs describe intended responsibilities. A documented directory is a planned boundary and does not necessarily contain an implemented component yet.
