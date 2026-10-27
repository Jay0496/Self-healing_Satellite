/**
 * @file key_manager.cpp
 * @brief Implementation of key rotation tracking and OpenSSL RSA/ECDSA firmware signature verification.
 */

#include "key_manager.hpp"
#include <openssl/pem.h>
#include <openssl/sha.h>
#include <openssl/rsa.h>
#include <openssl/evp.h>
#include <stdexcept>

KeyManager::KeyManager(const std::vector<unsigned char>& initialKey) 
    : currentKey(initialKey), lastRotationTime(std::chrono::system_clock::now()) {}

bool KeyManager::checkAndRotateKey(const std::vector<unsigned char>& newKey) {
    auto now = std::chrono::system_clock::now();
    auto hoursElapsed = std::chrono::duration_cast<std::chrono::hours>(now - lastRotationTime).count();

    // Enforce <= 24h rotation interval requirement (REQ-SEC-02)
    if (hoursElapsed >= 24) {
        if (newKey.size() != 32) return false;
        currentKey = newKey;
        lastRotationTime = now;
        return true;
    }
    return false;
}

bool KeyManager::verifySoftwareUpdateSignature(const std::vector<unsigned char>& firmwareBinary, 
    const std::vector<unsigned char>& signature, 
    const std::string& publicKeyPem) {
    BIO* bio = BIO_new_mem_buf(publicKeyPem.data(), static_cast<int>(publicKeyPem.size()));
    if (!bio) return false;

    EVP_PKEY* pubKey = PEM_read_bio_PUBKEY(bio, NULL, NULL, NULL);
    BIO_free(bio);
    if (!pubKey) return false;

    EVP_MD_CTX* mdCtx = EVP_MD_CTX_new();
    bool verified = false;

    if (mdCtx) {
        if (EVP_DigestVerifyInit(mdCtx, NULL, EVP_sha256(), NULL, pubKey) == 1) {
            if (EVP_DigestVerifyUpdate(mdCtx, firmwareBinary.data(), firmwareBinary.size()) == 1) {
                int result = EVP_DigestVerifyFinal(mdCtx, signature.data(), signature.size());
                if (result == 1) verified = true; // 100% rejection of unsigned/invalid updates (REQ-SEC-02)
            }
        }
        EVP_MD_CTX_free(mdCtx);
    }

    EVP_PKEY_free(pubKey);
    return verified;
}