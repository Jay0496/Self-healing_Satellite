/**
 * @file packet_crypto.cpp
 * @brief Implementation of edge-side AES-256-GCM decryption and anti-replay window validation.
 */

#include "packet_crypto.hpp"
#include <openssl/evp.h>
#include <stdexcept>
#include <cstring>

PacketCrypto::PacketCrypto(const std::vector<unsigned char>& key) 
    : masterKey(key), lastSeenSequenceNumber(0) {
    if (key.size() != 32) {
        throw std::invalid_argument("AES-256 requires a 32-byte key.");
    }
}

bool PacketCrypto::decryptPacket(const EncryptedPacket& packet, std::vector<unsigned char>& decryptedPlaintext) {
    // Anti-replay check: Reject duplicate or older sequence numbers (REQ-SEC-01)
    if (packet.sequenceNumber <= lastSeenSequenceNumber) {
        return false; // Replay detected / rejected
    }

    decryptedPlaintext.resize(packet.ciphertext.size());
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return false;

    int len = 0;
    int plaintextLen = 0;
    bool success = false;

    try {
        if (EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL) != 1) throw 0;
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, packet.iv.size(), NULL) != 1) throw 0;
        if (EVP_DecryptInit_ex(ctx, NULL, NULL, masterKey.data(), packet.iv.data()) != 1) throw 0;

        // Bind sequence number AAD
        if (EVP_DecryptUpdate(ctx, NULL, &len, reinterpret_cast<const unsigned char*>(&packet.sequenceNumber), sizeof(packet.sequenceNumber)) != 1) throw 0;

        // Decrypt ciphertext payload
        if (EVP_DecryptUpdate(ctx, decryptedPlaintext.data(), &plaintextLen, packet.ciphertext.data(), packet.ciphertext.size()) != 1) throw 0;

        // Set expected GCM tag for integrity verification
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, 16, const_cast<unsigned char*>(packet.tag.data())) != 1) throw 0;

        // Finalize decryption (verifies authentication tag; 100% rejection on tampering)
        int finalLen = 0;
        if (EVP_DecryptFinal_ex(ctx, decryptedPlaintext.data() + plaintextLen, &finalLen) > 0) {
            plaintextLen += finalLen;
            decryptedPlaintext.resize(plaintextLen);
            lastSeenSequenceNumber = packet.sequenceNumber; // Update anti-replay window
            success = true;
        }
    } catch (...) {
        success = false;
    }

    EVP_CIPHER_CTX_free(ctx);
    return success;
}