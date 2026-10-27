/**
 * @file test_monitoring.cpp
 * @brief Comprehensive test runner validating Resource limits (REQ-HW-02), FSM latency (REQ-FSM-01), 
 *        and Audit log integrity/tamper detection (REQ-AUD-01).
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
    std::cout << "[RUNNING] Starting Expanded Edge-Device Monitoring Test Suite...\n";

    // --- TEST 1: Nominal Memory Constraint Check (REQ-HW-02) ---
    {
        ResourceMonitor monitor;
        double currentUsage = 0.0;
        bool withinLimit = monitor.checkMemoryConstraint(2.0, currentUsage);
        
        std::cout << "  - [TEST] Current RSS Memory: " << currentUsage << " GB (Limit: 2.0 GB)\n";
        assert(withinLimit && "Memory constraint violated under nominal load!");
        std::cout << "  - [PASS] REQ-HW-02 Nominal Memory Constraint Verified.\n";
    }

    // --- TEST 1.1: Negative Memory Constraint Check (Triggering a Breach) ---
    {
        ResourceMonitor monitor;
        double currentUsage = 0.0;
        // Pass an impossibly small limit (0.0001 GB / ~100 KB) to guarantee a triggered violation
        bool withinLimit = monitor.checkMemoryConstraint(0.0001, currentUsage);

        std::cout << "  - [TEST] Forced Threshold Test: Current Usage = " << currentUsage << " GB (Limit: 0.0001 GB)\n";
        assert(!withinLimit && "Resource monitor failed to catch a memory constraint violation!");
        std::cout << "  - [PASS] REQ-HW-02 Negative Constraint Violation Correctly Flagged.\n";
    }

    // --- TEST 2: FSM Latency Check (REQ-FSM-01) ---
    {
        MockFsmEngine fsm;
        long latencyMs = 0;
        bool success = fsm.evaluateTransition("SAFE_STATE", latencyMs);

        std::cout << "  - [TEST] FSM Transition Latency: " << latencyMs << " ms (Limit: <= 50 ms)[cite: 9]\n";
        assert(success && latencyMs <= 50 && "FSM transition exceeded 50 ms limit!");
        std::cout << "  - [PASS] REQ-FSM-01 FSM Latency Verified.\n";
    }

    // --- TEST 3: Hash-Linked Audit Chain & Tamper Detection (REQ-AUD-01) ---
    {
        std::string testLogFile = "test_audit_chain.log";
        std::remove(testLogFile.c_str());

        AuditLogger logger(testLogFile);
        logger.logEvent("BOOT", "System initialized successfully.");
        logger.logEvent("ANOMALY_DETECTED", "Thermal drift identified on channel 12.");
        logger.logEvent("FSM_RECOVERY", "Power cycle executed.");

        // Verify untampered chain integrity
        assert(logger.verifyChainIntegrity() && "Audit log chain integrity check failed on valid log!");
        std::cout << "  - [PASS] REQ-AUD-01 Audit Chain Generation Verified.\n";

        // Scenario A: Simulate tampering by modifying an existing line externally
        {
            std::fstream file(testLogFile, std::ios::in | std::ios::out);
            std::string firstLine;
            std::getline(file, firstLine);
            file.seekp(0);
            file << "TAMPERED_HASH_STRING|FAKE_EVENT|Injected malicious log payload\n";
        }

        assert(!logger.verifyChainIntegrity() && "Audit logger failed to detect file line tampering!");
        std::cout << "  - [PASS] REQ-AUD-01 Tamper Detection (Line Modification) Verified.\n";

        // Clean up and recreate for Scenario B
        std::remove(testLogFile.c_str());
        AuditLogger logger2(testLogFile);
        logger2.logEvent("BOOT", "System initialized.");
        logger2.logEvent("CHECKPOINT", "Nominal operations running.");

        // Scenario B: Simulate file corruption/truncation mid-write
        {
            std::ofstream file(testLogFile, std::ios::app);
            file << "INCOMPLETE_TRUNCATED_LINE_WITHOUT_HASH";
        }

        assert(!logger2.verifyChainIntegrity() && "Audit logger failed to detect truncated/corrupted log structure!");
        std::cout << "  - [PASS] REQ-AUD-01 Corruption/Truncation Detection Verified.\n";

        // Clean up final test artifact
        std::remove(testLogFile.c_str());
    }

    std::cout << "[SUCCESS] All Expanded Edge-Device Monitoring Unit Tests Passed Successfully!\n";
    return 0;
}