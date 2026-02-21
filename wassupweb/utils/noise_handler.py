from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from ..defaults import NOISE_MODE, WA_CERT_DETAILS
from ..types.auth import KeyPair
from ..wabinary import BinaryNode, decode_binary_node
from ..waproto import proto
from .crypto import Curve, aes_decrypt_gcm, aes_encrypt_gcm, hkdf, sha256

IV_LENGTH = 12
EMPTY_BUFFER = b""


def generate_iv(counter: int) -> bytes:
    iv = bytearray(IV_LENGTH)
    iv[8] = (counter >> 24) & 0xFF
    iv[9] = (counter >> 16) & 0xFF
    iv[10] = (counter >> 8) & 0xFF
    iv[11] = counter & 0xFF
    return bytes(iv)


@dataclass
class TransportState:
    enc_key: bytes
    dec_key: bytes
    read_counter: int = 0
    write_counter: int = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        iv = generate_iv(self.write_counter)
        self.write_counter += 1
        return aes_encrypt_gcm(plaintext, self.enc_key, iv, EMPTY_BUFFER)

    def decrypt(self, ciphertext: bytes) -> bytes:
        iv = generate_iv(self.read_counter)
        self.read_counter += 1
        return aes_decrypt_gcm(ciphertext, self.dec_key, iv, EMPTY_BUFFER)


class NoiseHandler:
    def __init__(
        self,
        key_pair: KeyPair,
        noise_header: bytes,
        logger: Any,
        routing_info: bytes | None = None,
        *,
        strict_cert_validation: bool = True,
    ) -> None:
        self._private_key = bytes(key_pair.private)
        self._public_key = bytes(key_pair.public)
        self._noise_header = bytes(noise_header)
        self._logger = logger.child({"class": "ns"}) if hasattr(logger, "child") else logger

        data = NOISE_MODE.encode("utf-8")
        self._hash = data if len(data) == 32 else sha256(data)
        self._salt = self._hash
        self._enc_key = self._hash
        self._dec_key = self._hash
        self._counter = 0
        self._sent_intro = False
        self._in_bytes = bytearray()
        self._decode_lock = asyncio.Lock()

        self._transport: TransportState | None = None
        self._is_waiting_for_transport = False
        self._pending_on_frame: Callable[[bytes | BinaryNode], Any] | None = None
        self._strict_cert_validation = bool(strict_cert_validation)

        if routing_info:
            header = bytearray(7 + len(routing_info) + len(self._noise_header))
            header[0:2] = b"ED"
            header[2] = 0
            header[3] = 1
            header[4] = (len(routing_info) >> 16) & 0xFF
            header[5] = (len(routing_info) >> 8) & 0xFF
            header[6] = len(routing_info) & 0xFF
            header[7 : 7 + len(routing_info)] = routing_info
            header[7 + len(routing_info) :] = self._noise_header
            self._intro_header = bytes(header)
        else:
            self._intro_header = self._noise_header

        self.authenticate(self._noise_header)
        self.authenticate(self._public_key)

    def authenticate(self, data: bytes) -> None:
        if not self._transport:
            self._hash = sha256(self._hash + data)

    def _local_hkdf(self, data: bytes) -> tuple[bytes, bytes]:
        key = hkdf(data, 64, salt=self._salt, info="")
        return key[:32], key[32:]

    def mix_into_key(self, data: bytes) -> None:
        write_key, read_key = self._local_hkdf(data)
        self._salt = write_key
        self._enc_key = read_key
        self._dec_key = read_key
        self._counter = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        if self._transport:
            return self._transport.encrypt(plaintext)

        iv = generate_iv(self._counter)
        self._counter += 1
        encrypted = aes_encrypt_gcm(plaintext, self._enc_key, iv, self._hash)
        self.authenticate(encrypted)
        return encrypted

    def decrypt(self, ciphertext: bytes) -> bytes:
        if self._transport:
            return self._transport.decrypt(ciphertext)

        iv = generate_iv(self._counter)
        self._counter += 1
        decrypted = aes_decrypt_gcm(ciphertext, self._dec_key, iv, self._hash)
        self.authenticate(ciphertext)
        return decrypted

    async def finish_init(self) -> None:
        self._is_waiting_for_transport = True
        write_key, read_key = self._local_hkdf(b"")
        self._transport = TransportState(write_key, read_key)
        self._is_waiting_for_transport = False

        if self._logger:
            self._logger.debug("Noise handler transitioned to Transport state")

        if self._pending_on_frame:
            await self._process_data(self._pending_on_frame)
            self._pending_on_frame = None

    def process_handshake(self, payload: dict[str, Any], noise_key: KeyPair) -> bytes:
        server_hello = payload.get("serverHello") or payload.get("server_hello") or payload
        ephemeral = server_hello.get("ephemeral")
        static = server_hello.get("static")
        server_payload = server_hello.get("payload")
        if not isinstance(ephemeral, (bytes, bytearray)):
            raise ValueError("invalid noise handshake: missing ephemeral key")
        if not isinstance(static, (bytes, bytearray)):
            raise ValueError("invalid noise handshake: missing static payload")
        if not isinstance(server_payload, (bytes, bytearray)):
            raise ValueError("invalid noise handshake: missing payload")

        self.authenticate(bytes(ephemeral))
        self.mix_into_key(Curve.shared_key(self._private_key, bytes(ephemeral)))

        decrypted_static = self.decrypt(bytes(static))
        self.mix_into_key(Curve.shared_key(self._private_key, decrypted_static))

        cert_decoded = self.decrypt(bytes(server_payload))
        self._verify_noise_cert_chain(cert_decoded)

        key_enc = self.encrypt(bytes(noise_key.public))
        self.mix_into_key(Curve.shared_key(bytes(noise_key.private), bytes(ephemeral)))
        return key_enc

    def _proto_cls(self, name: str) -> Any:
        getter = getattr(proto, "get", None)
        if callable(getter):
            return getter(name)
        return getattr(proto, name, None)

    def _verify_noise_cert_chain(self, cert_payload: bytes) -> None:
        cert_chain_cls = self._proto_cls("CertChain")
        noise_cert_cls = getattr(cert_chain_cls, "NoiseCertificate", None) if cert_chain_cls is not None else None
        if noise_cert_cls is None:
            noise_cert_cls = self._proto_cls("NoiseCertificate")
        if cert_chain_cls is None or noise_cert_cls is None:
            if self._logger:
                self._logger.debug("WAProto statics unavailable; skipping noise cert verification")
            return

        cert_chain = cert_chain_cls()
        cert_chain.ParseFromString(bytes(cert_payload))
        cert_intermediate = getattr(cert_chain, "intermediate", None)
        leaf = getattr(cert_chain, "leaf", None)

        if not cert_intermediate or not getattr(leaf, "details", b"") or not getattr(leaf, "signature", b""):
            raise ValueError("invalid noise leaf certificate")

        if not getattr(cert_intermediate, "details", b"") or not getattr(cert_intermediate, "signature", b""):
            raise ValueError("invalid noise intermediate certificate")

        details_cls = getattr(noise_cert_cls, "Details", None) or self._proto_cls("Details")
        if details_cls is None:
            raise ValueError("noise certificate details type missing")

        details = details_cls()
        details.ParseFromString(bytes(cert_intermediate.details))

        issuer_serial = getattr(details, "issuerSerial", None)
        issuer = getattr(details, "issuer", None)
        details_key = bytes(getattr(details, "key", b""))
        leaf_details = bytes(getattr(leaf, "details", b""))
        leaf_sig = bytes(getattr(leaf, "signature", b""))
        intermediate_details = bytes(getattr(cert_intermediate, "details", b""))
        intermediate_sig = bytes(getattr(cert_intermediate, "signature", b""))

        verify = Curve.verify(details_key, leaf_details, leaf_sig)
        verify_intermediate = Curve.verify(
            bytes(WA_CERT_DETAILS["PUBLIC_KEY"]),
            intermediate_details,
            intermediate_sig,
        )

        if not verify:
            if self._strict_cert_validation:
                raise ValueError("noise certificate signature invalid")
            if self._logger:
                self._logger.warning("noise leaf cert signature verification failed; continuing in compatibility mode")
            return
        if not verify_intermediate:
            if self._strict_cert_validation:
                raise ValueError("noise intermediate certificate signature invalid")
            if self._logger:
                self._logger.warning("noise intermediate cert signature verification failed; continuing in compatibility mode")
            return
        if issuer_serial is not None:
            if int(issuer_serial) != int(WA_CERT_DETAILS["SERIAL"]):
                raise ValueError("certification match failed")
        elif issuer is not None:
            if str(issuer) != str(WA_CERT_DETAILS["ISSUER"]):
                raise ValueError("certification match failed")

    def encode_frame(self, data: bytes | bytearray) -> bytes:
        output = bytes(data)
        if self._transport:
            output = self._transport.encrypt(output)

        intro_size = 0 if self._sent_intro else len(self._intro_header)
        frame = bytearray(intro_size + 3 + len(output))
        if not self._sent_intro:
            frame[0:intro_size] = self._intro_header
            self._sent_intro = True

        frame[intro_size] = (len(output) >> 16) & 0xFF
        frame[intro_size + 1] = (len(output) >> 8) & 0xFF
        frame[intro_size + 2] = len(output) & 0xFF
        frame[intro_size + 3 :] = output
        return bytes(frame)

    async def _process_data(self, on_frame: Callable[[bytes | BinaryNode], Any]) -> None:
        while True:
            if len(self._in_bytes) < 3:
                return
            size = (self._in_bytes[0] << 16) | (self._in_bytes[1] << 8) | self._in_bytes[2]
            if len(self._in_bytes) < size + 3:
                return

            frame = bytes(self._in_bytes[3 : size + 3])
            del self._in_bytes[: size + 3]

            decoded: bytes | BinaryNode
            if self._transport:
                decrypted = self._transport.decrypt(frame)
                decoded = await decode_binary_node(decrypted)
            else:
                decoded = frame
            result = on_frame(decoded)
            if asyncio.iscoroutine(result):
                await result

    async def decode_frame(self, new_data: bytes | bytearray, on_frame: Callable[[bytes | BinaryNode], Any]) -> None:
        async with self._decode_lock:
            if self._is_waiting_for_transport:
                self._in_bytes.extend(bytes(new_data))
                self._pending_on_frame = on_frame
                return

            self._in_bytes.extend(bytes(new_data))
            await self._process_data(on_frame)


def make_noise_handler(
    *,
    key_pair: KeyPair,
    NOISE_HEADER: bytes,
    logger: Any,
    routing_info: bytes | None = None,
    strict_cert_validation: bool = True,
) -> NoiseHandler:
    return NoiseHandler(
        key_pair=key_pair,
        noise_header=NOISE_HEADER,
        logger=logger,
        routing_info=routing_info,
        strict_cert_validation=strict_cert_validation,
    )


# camelCase aliases for parity
makeNoiseHandler = make_noise_handler


__all__ = ["make_noise_handler", "NoiseHandler", "TransportState", "generate_iv"]
