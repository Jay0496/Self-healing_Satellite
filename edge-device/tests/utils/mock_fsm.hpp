#ifndef MOCK_FSM_HPP
#define Mock_FSM_HPP

/**
 * @file mock_fsm.hpp
 * @brief Header for simulating Finite State Machine transition latency (REQ-FSM-01).
 */

#include <string>

class MockFsmEngine {
public:
    /**
     * @brief Evaluates a state transition and measures execution timing.
     * @param targetState Name of destination state.
     * @param elapsedMs Output parameter storing measured duration in milliseconds.
     * @return true if transition latency <= 50ms, false otherwise.
     */
    bool evaluateTransition(const std::string& targetState, long& elapsedMs);
};

#endif // MOCK_FSM_HPP