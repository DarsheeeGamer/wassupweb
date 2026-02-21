from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from hashlib import md5 as _md5

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from nacl.bindings import crypto_scalarmult
from nacl.public import PrivateKey
from nacl.signing import SigningKey, VerifyKey

from ..types.auth import KeyPair, SignedKeyPair

KEY_BUNDLE_TYPE = bytes([5])


def generate_signal_pub_key(pub_key: bytes) -> bytes:
    return pub_key if len(pub_key) == 33 else KEY_BUNDLE_TYPE + pub_key


class Curve:
    @staticmethod
    def generate_key_pair() -> KeyPair:
        private_key = PrivateKey.generate()
        return KeyPair(public=bytes(private_key.public_key), private=bytes(private_key))

    @staticmethod
    def shared_key(private_key: bytes, public_key: bytes) -> bytes:
        return bytes(crypto_scalarmult(private_key, public_key))

    @staticmethod
    def sign(private_key: bytes, payload: bytes) -> bytes:
        signer = SigningKey(private_key[:32])
        return bytes(signer.sign(payload).signature)

    @staticmethod
    def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
        try:
            key_bytes = bytes(public_key)
            if len(key_bytes) == 33:
                # Signal-style public key can include a leading version byte.
                key_bytes = key_bytes[1:]
            elif len(key_bytes) > 32:
                key_bytes = key_bytes[:32]
            verifier = VerifyKey(key_bytes)
            verifier.verify(message, signature)
            return True
        except Exception:
            return False


def signed_key_pair(identity_key_pair: KeyPair, key_id: int) -> SignedKeyPair:
    pre_key = Curve.generate_key_pair()
    pub_key = generate_signal_pub_key(pre_key.public)
    signature = Curve.sign(identity_key_pair.private, pub_key)
    return SignedKeyPair(key_pair=pre_key, signature=signature, key_id=key_id)


def hmac_sign(buffer: bytes, key: bytes, variant: str = "sha256") -> bytes:
    return hmac.new(key, buffer, digestmod=variant).digest()


def sha256(buffer: bytes) -> bytes:
    return hashlib.sha256(buffer).digest()


def md5(buffer: bytes) -> bytes:
    return _md5(buffer).digest()


def hkdf(
    key_material: bytes,
    length: int,
    *,
    salt: bytes = b"",
    info: str = "",
    hash_name: str = "sha256",
) -> bytes:
    hash_len = hashlib.new(hash_name).digest_size
    if not salt:
        salt = bytes([0] * hash_len)
    prk = hmac.new(salt, key_material, digestmod=hash_name).digest()
    okm = bytearray()
    t = b""
    counter = 1
    info_bytes = info.encode("latin1") if isinstance(info, str) else bytes(info)
    while len(okm) < length:
        t = hmac.new(prk, t + info_bytes + bytes([counter]), digestmod=hash_name).digest()
        okm.extend(t)
        counter += 1
    return bytes(okm[:length])


def generate_registration_id() -> int:
    # Signal style 14-bit id.
    return secrets.randbelow(16_384)


GCM_TAG_LENGTH = 16


def aes_encrypt_gcm(plaintext: bytes, key: bytes, iv: bytes, additional_data: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    encryptor = cipher.encryptor()
    encryptor.authenticate_additional_data(additional_data)
    encrypted = encryptor.update(plaintext) + encryptor.finalize()
    return encrypted + encryptor.tag


def aes_decrypt_gcm(ciphertext: bytes, key: bytes, iv: bytes, additional_data: bytes) -> bytes:
    enc = ciphertext[:-GCM_TAG_LENGTH]
    tag = ciphertext[-GCM_TAG_LENGTH:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag))
    decryptor = cipher.decryptor()
    decryptor.authenticate_additional_data(additional_data)
    return decryptor.update(enc) + decryptor.finalize()


def aes_encrypt_ctr(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def aes_decrypt_ctr(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def aes_decrypt(buffer: bytes, key: bytes) -> bytes:
    return aes_decrypt_with_iv(buffer[16:], key, buffer[:16])


def aes_decrypt_with_iv(buffer: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(buffer) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def aes_encrypt(buffer: bytes, key: bytes) -> bytes:
    iv = os.urandom(16)
    return iv + aes_encrypt_with_iv(buffer, key, iv)


def aes_encrypt_with_iv(buffer: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(buffer) + padder.finalize()
    return encryptor.update(padded) + encryptor.finalize()


def derive_pairing_code_key(pairing_code: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pairing_code.encode("utf-8"), salt, 2 << 16, dklen=32)


# camelCase aliases for parity
generateSignalPubKey = generate_signal_pub_key
signedKeyPair = signed_key_pair
hmacSign = hmac_sign
aesEncryptGCM = aes_encrypt_gcm
aesDecryptGCM = aes_decrypt_gcm
aesEncryptCTR = aes_encrypt_ctr
aesDecryptCTR = aes_decrypt_ctr
aesDecrypt = aes_decrypt
aesDecryptWithIV = aes_decrypt_with_iv
aesEncrypt = aes_encrypt
aesEncrypWithIV = aes_encrypt_with_iv
derivePairingCodeKey = derive_pairing_code_key
