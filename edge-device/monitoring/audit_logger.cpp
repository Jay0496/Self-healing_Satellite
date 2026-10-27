/**
 * @file audit_logger.cpp
 * @brief Implementation of SHA-256 hash-linked logging chain to satisfy REQ-AUD-01.
 */

#include "audit_logger.hpp"
#include <openssl/sha.h>
#include <iomanip>
#include <sstream>
#include <fstream>
#include <vector>

AuditLogger::AuditLogger(std::string path) : logFilePath(path) {}

std::string AuditLogger::computeSHA256(const std::string& data) {
    unsigned char hash[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(data.c_str()), data.size(), hash);
    
    std::stringstream ss;
    for(int i = 0; i < SHA256_DIGEST_LENGTH; i++) {
        ss << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(hash[i]);
    }
    return ss.str();
}

std::string AuditLogger::getLastHashFromDisk() {
    std::ifstream file(logFilePath);
    if (!file.is_open()) {
        return "0000000000000000000000000000000000000000000000000000000000000000"; // Genesis hash
    }

    std::string lastLine, currentLine;
    while (std::getline(file, currentLine)) {
        if (!currentLine.empty()) {
            lastLine = currentLine;
        }
    }

    if (lastLine.empty()) {
        return "0000000000000000000000000000000000000000000000000000000000000000";
    }

    // Extract first token (the hash) from the last recorded line
    size_t delimiterPos = lastLine.find('|');
    return (delimiterPos != std::string::npos) ? lastLine.substr(0, delimiterPos) : "";
}

void AuditLogger::logEvent(const std::string& eventType, const std::string& details) {
    std::string previousHash = getLastHashFromDisk();
    std::string rawData = previousHash + "|" + eventType + "|" + details;
    std::string currentHash = computeSHA256(rawData);

    std::ofstream logFile(logFilePath, std::ios::app);
    logFile << currentHash << "|" << eventType << "|" << details << "\n";
}

bool AuditLogger::verifyChainIntegrity() {
    std::ifstream file(logFilePath);
    if (!file.is_open()) return true; // Empty log is valid

    std::string line;
    std::string expectedPreviousHash = "0000000000000000000000000000000000000000000000000000000000000000";

    while (std::getline(file, line)) {
        size_t firstDelimiter = line.find('|');
        size_t secondDelimiter = line.find('|', firstDelimiter + 1);
        if (firstDelimiter == std::string::npos || secondDelimiter == std::string::npos) return false;

        std::string recordedHash = line.substr(0, firstDelimiter);
        std::string eventType = line.substr(firstDelimiter + 1, secondDelimiter - firstDelimiter - 1);
        std::string details = line.substr(secondDelimiter + 1);

        // Recompute hash to check for tampering
        std::string rawData = expectedPreviousHash + "|" + eventType + "|" + details;
        if (computeSHA256(rawData) != recordedHash) {
            return false; // Tampering detected!
        }
        expectedPreviousHash = recordedHash;
    }
    return true;
}