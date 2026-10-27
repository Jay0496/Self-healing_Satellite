# File Purpose: CMake build configuration for the security subsystem and security unit tests.
cmake_minimum_required(VERSION 3.10)
project(SecuritySubsystem CXX)

set(CMAKE_CXX_STANDARD 17)

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/ground)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/edge-device)

# Security library sources
add_library(security_lib
    ground/packet_encoder.cpp
    edge-device/packet_crypto.cpp
    edge-device/key_manager.cpp
)

# Link OpenSSL for AES-256-GCM and cryptographic operations (REQ-SEC-01)[cite: 1]
find_package(OpenSSL REQUIRED)
target_link_libraries(security_lib OpenSSL::SSL OpenSSL::Crypto)

# Security unit test executable
add_executable(test_security tests/test_security.cpp)
target_link_libraries(test_security security_lib OpenSSL::SSL OpenSSL::Crypto)