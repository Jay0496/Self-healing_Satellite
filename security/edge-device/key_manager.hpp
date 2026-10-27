#ifndef KEY_MANAGER_HPP
#define KEY_MANAGER_HPP

/**
 * @file key_manager.hpp
 * @brief Key rotation (<= 24h) and software update signature verification (REQ-SEC-02).
 */

#include <vector>
#include <string>
#include <chrono>

class KeyManager {
private:
    std::vector<unsigned char> currentKey;
    std::chrono::system_clock::time_point lastRotationTime;

public:
    explicit KeyManager(const std::vector<unsigned char>& initialKey);

    /**
     * @brief Checks if key rotation interval (<= 24h) has elapsed and performs rotation (REQ-SEC-02).
     */
    bool checkAndRotateKey(const std::vector<unsigned char>& newKey);

    /**
     * @brief Verifies a digital signature on an incoming software update before installation (REQ-SEC-02).
     */
    bool verifySoftwareUpdateSignature(const std::vector<unsigned char>& firmwareBinary, 
        const std::vector<unsigned char>& signature, 
        const std::string& publicKeyPem);
};

#endif // KEY_MANAGER_HPP