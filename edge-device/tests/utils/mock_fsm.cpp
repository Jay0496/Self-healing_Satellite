/**
 * @file mock_fsm.cpp
 * @brief Implementation of FSM transition timing check satisfying REQ-FSM-01 (<= 50 ms).
 */

#include "mock_fsm.hpp"
#include <chrono>
#include <thread>

bool MockFsmEngine::evaluateTransition(const std::string& targetState, long& elapsedMs) {
    auto start = std::chrono::high_resolution_clock::now();

    // Simulating deterministic FSM logic execution workload
    std::this_thread::sleep_for(std::chrono::milliseconds(5));

    auto end = std::chrono::high_resolution_clock::now();
    elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();

    // Verifies against REQ-FSM-01 target limit (<= 50 ms)
    return elapsedMs <= 50;
}