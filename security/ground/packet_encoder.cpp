/**
 * @file packet_encoder.cpp
 * @brief Implementation of ground-side AES-256-GCM packet encoding and sequence tagging.
 */

#include "packet_encoder.hpp"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <stdexcept>
#include <cstring>

PacketEncoder::PacketEncoder(const std::vector<unsigned char>& key) 
    : masterKey(key), nextSequenceNumber(1) {
    if (key.size() != 32) {
        throw std::invalid_argument("AES-256 requires a 32-byte key.");
    }
}

EncryptedPacket PacketEncoder::encryptPacket(const std::vector<unsigned char>& plaintext) {
    EncryptedPacket packet;
    packet.sequenceNumber = nextSequenceNumber++;
    
    // Generate a unique 12-byte IV per packet (REQ-SEC-01)
    packet.iv.resize(12);
    if (RAND_bytes(packet.iv.data(), 12) != 1) {
        throw std::runtime_error("Failed to generate secure random IV.");
    }

    packet.ciphertext.resize(plaintext.size());
    packet.tag.resize(16); // 128-bit authentication tag

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (!ctx) throw std::runtime_error("Failed to create EVP cipher context.");

    int len = 0;
    int ciphertextLen = 0;

    try {
        // Initialize AES-256-GCM encryption
        if (EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL) != 1)
            throw std::runtime_error("EVP_EncryptInit_ex failed.");

        // Set IV length
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, packet.iv.size(), NULL) != 1)
            throw std::runtime_error("Failed to set IV length.");

        // Initialize key and IV
        if (EVP_EncryptInit_ex(ctx, NULL, NULL, masterKey.data(), packet.iv.data()) != 1)
            throw std::runtime_error("Failed to set key and IV.");

        // Bind sequence number as Additional Authenticated Data (AAD) for anti-replay binding
        if (EVP_EncryptUpdate(ctx, NULL, &len, reinterpret_cast<const unsigned char*>(&packet.sequenceNumber), sizeof(packet.sequenceNumber)) != 1)
            throw std::runtime_error("Failed to set AAD.");

        // Encrypt payload
        if (EVP_EncryptUpdate(ctx, packet.ciphertext.data(), &ciphertextLen, plaintext.data(), plaintext.size()) != 1)
            throw std::runtime_error("EVP_EncryptUpdate failed.");

        int finalLen = 0;
        if (EVP_EncryptFinal_ex(ctx, packet.ciphertext.data() + ciphertextLen, &finalLen) != 1)
            throw std::runtime_error("EVP_EncryptFinal_ex failed.");
        ciphertextLen += finalLen;
        packet.ciphertext.resize(ciphertextLen);

        // Get GCM tag
        if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, packet.tag.data()) != 1)
            throw std::runtime_error("Failed to retrieve GCM tag.");

        EVP_CIPHER_CTX_free(ctx);
    } catch (...) {
        EVP_CIPHER_CTX_free(ctx);
        throw;
    }

    return packet;
}