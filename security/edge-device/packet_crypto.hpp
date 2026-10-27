#ifndef PACKET_CRYPTO_HPP
#define PACKET_CRYPTO_HPP

/**
 * @file packet_crypto.hpp
 * @brief Edge-side packet decryption and anti-replay verification (REQ-SEC-01).
 */

#include "packet_encoder.hpp"
#include <vector>
#include <cstdint>

class PacketCrypto {
private:
    std::vector<unsigned char> masterKey;
    uint64_t lastSeenSequenceNumber;

public:
    explicit PacketCrypto(const std::vector<unsigned char>& key);

    /**
     * @brief Decrypts packet, verifies GCM authentication tag, and enforces anti-replay sequence checks (REQ-SEC-01).
     */
    bool decryptPacket(const EncryptedPacket& packet, std::vector<unsigned char>& decryptedPlaintext);
};

#endif // PACKET_CRYPTO_HPP