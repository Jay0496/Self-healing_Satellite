#ifndef STRESS_HELPER_HPP
#define STRESS_HELPER_HPP

/**
 * @file stress_helper.hpp
 * @brief Header for auxiliary testing utilities used to simulate resource loads.
 */

#include <vector>

class StressHelper {
public:
    /**
     * @brief Safely allocates heap memory to test resource monitoring constraints.
     */
    static void allocateMemoryMB(int megabytes, std::vector<char>& memoryHolder);
};

#endif // STRESS_HELPER_HPP