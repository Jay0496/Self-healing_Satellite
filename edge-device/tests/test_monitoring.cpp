/**
 * @file test_monitoring.cpp
 * @brief Comprehensive test runner validating Resource limits, FSM latency, and Audit log integrity.
 * @return Returns exit code 0 on complete success, or 1 if any test fails.
 */

#include <iostream>
#include <cassert>
#include <vector>
#include <fstream>
#include "resource_monitor.hpp"
#include "audit_logger.hpp"
#include "mock_fsm.hpp"
#include "stress_helper.hpp"

int main() {
    std::cout << "[RUNNING] Starting Edge-Device Monitoring Test Suite...\n";

    // --- TEST 1: Memory Constraint Check (REQ-HW-02) ---
    {
        ResourceMonitor monitor;
        double currentUsage = 0.0;
        bool withinLimit = monitor.checkMemoryConstraint(2.0, currentUsage);
        
        std::cout << "  - [TEST] Current RSS Memory: " << currentUsage << " GB (Limit: 2.0 GB)\n";
        assert(withinLimit && "Memory constraint violated under nominal load!");
        std::cout << "  - [PASS] REQ-HW-02 Memory Constraint Verified.\n";
    }

    // --- TEST 2: FSM Latency Check (REQ-FSM-01) ---
    {
        MockFsmEngine fsm;
        long latencyMs = 0;
        bool success = fsm.evaluateTransition("SAFE_STATE", latencyMs);

        std::cout << "  - [TEST] FSM Transition Latency: " << latencyMs << " ms (Limit: <= 50 ms)\n";
        assert(success && latencyMs <= 50 && "FSM transition exceeded 50 ms limit!");
        std::cout << "  - [PASS] REQ-FSM-01 FSM Latency Verified.\n";
    }

    // --- TEST 3: Hash-Linked Audit Chain & Tamper Detection (REQ-AUD-01) ---
    {
        std::string testLogFile = "test_audit_chain.log";
        // Clean up previous test artifact if present
        std::remove(testLogFile.c_str());

        AuditLogger logger(testLogFile);
        logger.logEvent("BOOT", "System initialized successfully.");
        logger.logEvent("ANOMALY_DETECTED", "Thermal drift identified on channel 12.");
        logger.logEvent("FSM_RECOVERY", "Power cycle executed.");

        // Verify untampered chain integrity
        assert(logger.verifyChainIntegrity() && "Audit log chain integrity check failed on valid log!");
        std::cout << "  - [PASS] REQ-AUD-01 Audit Chain Generation Verified.\n";

        // Simulate tampering by modifying a file line externally
        {
            std::fstream file(testLogFile, std::ios::in | std::ios::out);
            std::string firstLine;
            std::getline(file, firstLine);
            file.seekp(0);
            file << "TAMPERED_HASH_STRING|FAKE_EVENT|Injected malicious log payload\n";
        }

        // Verify that tampering is instantly detected
        assert(!logger.verifyChainIntegrity() && "Audit logger failed to detect tampering!");
        std::cout << "  - [PASS] REQ-AUD-01 Tamper Detection Verified.\n";

        // Clean up test artifact
        std::remove(testLogFile.c_str());
    }

    std::cout << "[SUCCESS] All Edge-Device Monitoring Unit Tests Passed Successfully!\n";
    return 0; // Exit code 0 indicates success
}