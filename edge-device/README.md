# Edge Device Subsystem

## Overview
This directory contains the core software components running on the emulated satellite onboard computer. It serves as the development environment for the Autonomous Mission Control Agent's edge capabilities prior to deployment onto physical single-board hardware Raspberry Pi 5.

## Directory Structure
- `monitoring/`: Core system monitoring modules (Resource bounds tracking, tamper-evident audit logging).
- `tests/`: Unit test suite and test utilities (Stress helpers, mock FSM engines).