# Simulator Tests

Tests here cover deterministic nominal sensor telemetry generation, UHF-template preprocessing, the pass-through pre-publish transformation boundary, shared record validation, and MQTT publication to the flight topic tree. Current tests use a fake MQTT client and do not require a broker. Active fault injection, ground connectivity, and link simulation are outside this implementation's test scope; system behavior checks belong in integration or end-to-end tests.
