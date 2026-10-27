#ifndef AUDIT_LOGGER_HPP
#define AUDIT_LOGGER_HPP

/**
 * @file audit_logger.hpp
 * @brief Header for tamper-evident, hash-linked audit logging (REQ-AUD-01).
 */

#include <string>

class AuditLogger {
private:
    std::string logFilePath;
    std::string computeSHA256(const std::string& data);
    std::string getLastHashFromDisk();

public:
    explicit AuditLogger(std::string path = "audit_chain.log");
    
    /**
     * @brief Logs an event into an append-only, SHA-256 hash-linked chain.
     */
    void logEvent(const std::string& eventType, const std::string& details);
    
    /**
     * @brief Verifies the cryptographic integrity of the entire audit log chain.
     */
    bool verifyChainIntegrity();
};

#endif // AUDIT_LOGGER_HPP