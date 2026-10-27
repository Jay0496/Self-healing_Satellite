/**
 * @file stress_helper.cpp
 * @brief Implementation of auxiliary memory allocation helper for testing bounds.
 */

#include "stress_helper.hpp"

void StressHelper::allocateMemoryMB(int megabytes, std::vector<char>& memoryHolder) {
    try {
        memoryHolder.resize(static_cast<size_t>(megabytes) * 1024 * 1024, 1);
    } catch (...) {
        // Handle allocation failure safely in test environments
    }
}