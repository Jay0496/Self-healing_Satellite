/**
 * @file resource_monitor.cpp
 * @brief Implementation of memory usage tracking via Linux pseudo-filesystems (/proc/self/status).
 */

#include "resource_monitor.hpp"
#include <fstream>
#include <sstream>

bool ResourceMonitor::checkMemoryConstraint(double maxAllowedGB, double& currentUsageGB) {
    // Read resident set size (VmRSS) from Linux process status
    std::ifstream statusFile("/proc/self/status");
    std::string line;
    long vmRssKB = 0;

    while (std::getline(statusFile, line)) {
        if (line.rfind("VmRSS:", 0) == 0) {
            std::istringstream iss(line);
            std::string key;
            iss >> key >> vmRssKB;
            break;
        }
    }

    // Convert Kilobytes to Gigabytes
    currentUsageGB = static_cast<double>(vmRssKB) / (1024.0 * 1024.0);
    
    // Validates against REQ-HW-02 target limit (< 2 GB)
    return currentUsageGB <= maxAllowedGB;
}