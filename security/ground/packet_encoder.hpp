#ifndef PACKET_ENCODER_HPP
#define PACKET_ENCODER_HPP

/**
 * @file packet_encoder.hpp
 * @brief Ground-side packet encoder implementing AES-256-GCM encryption and anti-replay sequence tagging (REQ-SEC-01).
 */

#include <vector>
#include <string>
#include <cstdint>

struct EncryptedPacket {
    std::vector<unsigned char> iv;         // Unique IV per packet (REQ-SEC-01)
    uint64_t sequenceNumber;               // Anti-replay sequence counter
    std::vector<unsigned char> ciphertext; // Encrypted payload
    std::vector<unsigned char> tag;        // GCM Authentication tag (100% rejection of tampering)
};

class PacketEncoder {
private:
    std::vector<unsigned char> masterKey;
    uint64_t nextSequenceNumber;

public:
    explicit PacketEncoder(const std::vector<unsigned char>& key);

    /**
     * @brief Encrypts plaintext payload using AES-256-GCM with a unique IV and sequence number.
     */
    EncryptedPacket encryptPacket(const std::vector<unsigned char>& plaintext);
};

#endif // PACKET_ENCODER_HPP