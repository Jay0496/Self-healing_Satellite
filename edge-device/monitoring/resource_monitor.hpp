#ifndef RESOURCE_MONITOR_HPP
#define RESOURCE_MONITOR_HPP

/**
 * @file resource_monitor.hpp
 * @brief Header file for tracking and enforcing system resource bounds (RAM < 2GB per REQ-HW-02).
 */

#include <string>

class ResourceMonitor {
public:
	/**
	 * @brief Checks if current process memory usage is within allowed limits.
	 * @param maxAllowedGB Maximum permitted RAM in Gigabytes.
	 * @param currentUsageGB Output parameter capturing current resident RAM usage.
	 * @return true if within limits, false if constraint is violated.
	 */

	bool checkMemoryConstraint(double maxAllowedGB, double& currentUsageGB);
};

#endif // RESOURCE_MONITOR_HPP
