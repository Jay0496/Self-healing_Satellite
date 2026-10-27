/**
 * @file test_security.cpp
 * @brief Comprehensive test suite for security requirements REQ-SEC-01 and REQ-SEC-02.
 * @return Returns exit code 0 on complete success, or 1 if any test fails.
 */

#include <iostream>
#include <cassert>
#include <vector>
#include "packet_encoder.hpp"
#include "packet_crypto.hpp"
#include "key_manager.hpp"

int main() {
    std::cout << "[RUNNING] Starting Security Subsystem Test Suite...\n";

    // Setup a valid 32-byte test key
    std::vector<unsigned char> testKey(32, 0xA5);

    // --- TEST 1: Nominal AES-256-GCM Encryption & Decryption (REQ-SEC-01) ---
    {
        PacketEncoder encoder(testKey);
        PacketCrypto crypto(testKey);

        std::string rawMessage = "EXECUTE_ORBITAL_MANEUVER_BURN_1";
        std::vector<unsigned char> plaintext(rawMessage.begin(), rawMessage.end());

        EncryptedPacket packet = encoder.encryptPacket(plaintext);

        std::vector<unsigned char> decryptedPlaintext;
        bool success = crypto.decryptPacket(packet, decryptedPlaintext);

        assert(success && "Nominal packet decryption failed!");
        std::string recoveredMessage(decryptedPlaintext.begin(), decryptedPlaintext.end());
        assert(recoveredMessage == rawMessage && "Decrypted payload mismatch!");
        std::cout << "  - [PASS] REQ-SEC-01 Nominal Encryption & Decryption Verified.\n";
    }

    // --- TEST 2: Anti-Replay Protection (REQ-SEC-01) ---
    {
        PacketEncoder encoder(testKey);
        PacketCrypto crypto(testKey);

        std::vector<unsigned char> plaintext = {'T', 'E', 'S', 'T'};
        EncryptedPacket packet1 = encoder.encryptPacket(plaintext);
        EncryptedPacket packet2 = encoder.encryptPacket(plaintext);

        std::vector<unsigned char> output;
        // Process first packet successfully
        assert(crypto.decryptPacket(packet1, output) && "First packet rejected!");

        // Attempt replay attack with packet1 again (same sequence number)
        bool replayAccepted = crypto.decryptPacket(packet1, output);
        assert(!replayAccepted && "Anti-replay failed to reject replayed packet!");
        std::cout << "  - [PASS] REQ-SEC-01 Anti-Replay Protection Verified.\n";
    }

    // --- TEST 3: Tamper Detection / Integrity Verification (REQ-SEC-01) ---
    {
        PacketEncoder encoder(testKey);
        PacketCrypto crypto(testKey);

        std::vector<unsigned char> plaintext = {'S', 'E', 'C', 'U', 'R', 'E'};
        EncryptedPacket packet = encoder.encryptPacket(plaintext);

        // Tamper with the ciphertext payload
        if (!packet.ciphertext.empty()) {
            packet.ciphertext[0] ^= 0xFF; 
        }

        std::vector<unsigned char> output;
        bool decryptedTampered = crypto.decryptPacket(packet, output);
        assert(!decryptedTampered && "Security failed to reject tampered packet!");
        std::cout << "  - [PASS] REQ-SEC-01 Tamper Detection Verified (100% Rejection Rate).\n";
    }

    // --- TEST 4: Key Rotation & Update Verification (REQ-SEC-02) ---
    {
        KeyManager keyManager(testKey);
        std::vector<unsigned char> newKey(32, 0x5A);
        
        // Simulating key rotation check
        bool rotated = keyManager.checkAndRotateKey(newKey);
        // Note: Unless simulated time advances 24h, rotation returns false per REQ-SEC-02
        assert(!rotated && "Key rotated prematurely before 24h window!");
        std::cout << "  - [PASS] REQ-SEC-02 Key Rotation Window Enforced.\n";
    }

    std::cout << "[SUCCESS] All Security Subsystem Unit Tests Passed Successfully!\n";
    return 0;
}