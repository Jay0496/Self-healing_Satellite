# Autonomous Mission Control Agent for Self-Healing IoT Satellites

A self-governing autonomous mission control agent designed to act as a "digital immune system" for satellites. The system monitors real-time telemetry, detects anomalies, and executes automated recovery actions to mitigate the operational risks of high-latency space communication and orbital blackouts.

## Core Features & Capabilities
- **Edge-Based Anomaly Detection:** Continuously monitors spacecraft telemetry to automatically recognize unusual or abnormal behavior onboard before it escalates into critical failures.
- **Deterministic Recovery & State Management:** Relies on structured, predictable rules to execute self-healing actions and track the system's operational health status post-recovery.
- **Fault Simulation & Testing:** Incorporates simulated communication blackouts and controlled hardware/software failure injection to rigorously test system resilience under harsh orbital conditions.
- **Azure IoT & Hybrid Ground Pipeline:** Integrates Azure IoT to manage cloud data flow, telemetry routing, and the packaging of complex, unhandled faults for ground-station analysis.
- **Operator Dashboard & HITL Oversight:** Features a centralized web interface that provides mission controllers with real-time telemetry visibility, comprehensive audit trails, and Human-in-the-Loop (HITL) authorization controls for high-risk actions and software patches.