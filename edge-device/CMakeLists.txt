# File Purpose: Root CMake build configuration for the edge-device monitoring module and unit tests.
cmake_minimum_required(VERSION 3.10)
project(EdgeDeviceMonitoring CXX)

set(CMAKE_CXX_STANDARD 17)

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/monitoring)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/tests/utils)

# Monitoring library sources
add_library(monitoring_lib
	monitoring/resource_monitor.cpp
	monitoring/audit_logger.cpp
	monitoring/mock_fsm.cpp
	tests/utils/stress_helper.cpp
)

# Link OpenSSL for SHA-256 hashing (REQ-AUD-01)
find_package(OpenSSL REQUIRED)
target_link_libraries(monitoring_lib OpenSSL::SSL OpenSSL::Crypto)

# Unit test executable
add_executable(test_monitoring tests/test_monitoring.cpp)
target_link_libraries(test_monitoring monitoring_lib OpenSSL::SSL OpenSSL::Crypto)
