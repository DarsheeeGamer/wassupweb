from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..types.auth import AuthenticationState
from ..utils.crypto import aes_decrypt_gcm, aes_encrypt_gcm, generate_signal_pub_key
from ..wabinary import (
    WAJIDDomains,
    is_hosted_lid_user,
    is_hosted_pn_user,
    is_lid_user,
    is_pn_user,
    jid_decode,
    transfer_device,
)
from .group import GroupCipher, GroupSessionBuilder, Sender, SenderKeyDistributionMessage, SenderKeyName, SenderKeyRecord
from .lid_mapping import LIDMappingStore


@dataclass(slots=True)
class ProtocolAddress:
    name: str
    device_id: int

    def __str__(self) -> str:
        return f"{self.name}.{self.device_id}"


class _SignalStorage:
    def __init__(self, auth: AuthenticationState, lid_mapping: LIDMappingStore) -> None:
        self._auth = auth
        self._keys = auth.keys
        self._creds = auth.creds
        self._lid_mapping = lid_mapping

    async def _resolve_lid_signal_address(self, addr: str) -> str:
        if "." not in addr:
            return addr
        user_domain, device = addr.split(".", 1)
        user, _, domain_type_raw = user_domain.partition("_")
        domain_type = int(domain_type_raw or "0")
        if domain_type in {WAJIDDomains.LID, WAJIDDomains.HOSTED_LID}:
            return addr
        pn_jid = f"{user}{'' if device == '0' else f':{device}'}@{'hosted' if domain_type == WAJIDDomains.HOSTED else 's.whatsapp.net'}"
        lid_for_pn = await self._lid_mapping.get_lid_for_pn(pn_jid)
        if not lid_for_pn:
            return addr
        lid_addr = jid_to_signal_protocol_address(lid_for_pn)
        return str(lid_addr)

    async def load_session_bytes(self, addr: str) -> bytes | None:
        wire = await self._resolve_lid_signal_address(addr)
        stored = await self._keys.get("session", [wire])
        return stored.get(wire)

    async def store_session_bytes(self, addr: str, data: bytes | None) -> None:
        wire = await self._resolve_lid_signal_address(addr)
        await self._keys.set({"session": {wire: data}})

    async def load_identity_key(self, addr: str) -> bytes | None:
        wire = await self._resolve_lid_signal_address(addr)
        stored = await self._keys.get("identity-key", [wire])
        return stored.get(wire)

    async def save_identity(self, addr: str, identity_key: bytes) -> bool:
        wire = await self._resolve_lid_signal_address(addr)
        stored = await self._keys.get("identity-key", [wire])
        existing = stored.get(wire)
        if existing and existing != identity_key:
            await self._keys.set({"session": {wire: None}, "identity-key": {wire: identity_key}})
            return True
        if not existing:
            await self._keys.set({"identity-key": {wire: identity_key}})
            return True
        return False

    async def load_pre_key(self, key_id: int | str) -> dict[str, bytes] | None:
        key_name = str(key_id)
        stored = await self._keys.get("pre-key", [key_name])
        key = stored.get(key_name)
        if key:
            return {"privKey": bytes(key["private"]), "pubKey": bytes(key["public"])}
        return None

    async def remove_pre_key(self, key_id: int) -> None:
        await self._keys.set({"pre-key": {str(key_id): None}})

    def load_signed_pre_key(self) -> dict[str, bytes]:
        key = self._creds.signed_pre_key
        return {
            "privKey": bytes(key.key_pair.private),
            "pubKey": bytes(key.key_pair.public),
        }

    async def load_sender_key(self, sender_key_name: SenderKeyName) -> SenderKeyRecord:
        key_id = str(sender_key_name)
        stored = await self._keys.get("sender-key", [key_id])
        raw = stored.get(key_id)
        if raw:
            return SenderKeyRecord.deserialize(raw)
        return SenderKeyRecord()

    async def store_sender_key(self, sender_key_name: SenderKeyName, key: SenderKeyRecord) -> None:
        key_id = str(sender_key_name)
        await self._keys.set({"sender-key": {key_id: key.serialize_bytes()}})

    def get_our_registration_id(self) -> int:
        return self._creds.registration_id

    def get_our_identity(self) -> dict[str, bytes]:
        identity = self._creds.signed_identity_key
        return {
            "privKey": bytes(identity.private),
            "pubKey": bytes(generate_signal_pub_key(identity.public)),
        }


class _SimpleSessionCipher:
    def __init__(self, storage: _SignalStorage, addr: ProtocolAddress) -> None:
        self._storage = storage
        self._addr = addr

    @property
    def _session_key_name(self) -> str:
        return str(self._addr)

    async def _get_or_create_session_key(self) -> bytes:
        existing = await self._storage.load_session_bytes(self._session_key_name)
        if existing:
            return existing
        fresh = hashlib.sha256(f"{self._session_key_name}:{os.urandom(16).hex()}".encode("utf-8")).digest()
        await self._storage.store_session_bytes(self._session_key_name, fresh)
        return fresh

    async def encrypt(self, data: bytes) -> dict[str, Any]:
        existing = await self._storage.load_session_bytes(self._session_key_name)
        is_pre_key_message = existing is None
        key = await self._get_or_create_session_key()
        iv = os.urandom(12)
        ciphertext = aes_encrypt_gcm(data, key, iv, b"")
        payload = iv + ciphertext
        return {"type": 3 if is_pre_key_message else 1, "body": payload}

    async def decrypt_pre_key_whisper_message(self, ciphertext: bytes) -> bytes:
        key = await self._get_or_create_session_key()
        iv = ciphertext[:12]
        enc = ciphertext[12:]
        return aes_decrypt_gcm(enc, key, iv, b"")

    async def decrypt_whisper_message(self, ciphertext: bytes) -> bytes:
        key = await self._storage.load_session_bytes(self._session_key_name)
        if not key:
            raise ValueError("no session")
        iv = ciphertext[:12]
        enc = ciphertext[12:]
        return aes_decrypt_gcm(enc, key, iv, b"")


def make_libsignal_repository(
    auth: AuthenticationState,
    logger: Any,
    pn_to_lid_func: Callable[[list[str]], Awaitable[list[dict[str, str]] | None]] | None = None,
) -> Any:
    lid_mapping = LIDMappingStore(auth.keys, logger, pn_to_lid_func)
    storage = _SignalStorage(auth, lid_mapping)
    migrated_session_cache: set[str] = set()

    class _Repository:
        async def decrypt_group_message(self, opts: dict[str, Any]) -> bytes:
            sender_name = jid_to_signal_sender_key_name(opts["group"], opts["authorJid"])
            cipher = GroupCipher(storage, sender_name)
            if hasattr(auth.keys, "transaction"):
                return await auth.keys.transaction(lambda: cipher.decrypt(opts["msg"]), opts["group"])
            return await cipher.decrypt(opts["msg"])

        async def process_sender_key_distribution_message(self, opts: dict[str, Any]) -> None:
            item = opts["item"]
            author_jid = opts["authorJid"]
            group_id = item.get("groupId")
            if not group_id:
                raise ValueError("groupId is required for sender key distribution message")
            sender_name = jid_to_signal_sender_key_name(group_id, author_jid)
            sender_msg = SenderKeyDistributionMessage(
                serialized=item.get("axolotlSenderKeyDistributionMessage"),
            )
            builder = GroupSessionBuilder(storage)
            sender_name_str = str(sender_name)

            async def _work() -> None:
                existing = await auth.keys.get("sender-key", [sender_name_str])
                if not existing.get(sender_name_str):
                    await storage.store_sender_key(sender_name, SenderKeyRecord())
                await builder.process(sender_name, sender_msg)

            if hasattr(auth.keys, "transaction"):
                await auth.keys.transaction(_work, group_id)
            else:
                await _work()

        async def decrypt_message(self, opts: dict[str, Any]) -> bytes:
            jid = opts["jid"]
            mode = opts["type"]
            ciphertext = opts["ciphertext"]
            addr = jid_to_signal_protocol_address(jid)
            cipher = _SimpleSessionCipher(storage, addr)

            async def _do_decrypt() -> bytes:
                if mode == "pkmsg":
                    return await cipher.decrypt_pre_key_whisper_message(ciphertext)
                return await cipher.decrypt_whisper_message(ciphertext)

            if hasattr(auth.keys, "transaction"):
                return await auth.keys.transaction(_do_decrypt, jid)
            return await _do_decrypt()

        async def encrypt_message(self, opts: dict[str, Any]) -> dict[str, Any]:
            jid = opts["jid"]
            data = opts["data"]
            addr = jid_to_signal_protocol_address(jid)
            cipher = _SimpleSessionCipher(storage, addr)

            async def _work() -> dict[str, Any]:
                result = await cipher.encrypt(data)
                msg_type = "pkmsg" if result["type"] == 3 else "msg"
                return {"type": msg_type, "ciphertext": bytes(result["body"])}

            if hasattr(auth.keys, "transaction"):
                return await auth.keys.transaction(_work, jid)
            return await _work()

        async def encrypt_group_message(self, opts: dict[str, Any]) -> dict[str, bytes]:
            group = opts["group"]
            me_id = opts["meId"]
            data = opts["data"]
            sender_name = jid_to_signal_sender_key_name(group, me_id)
            builder = GroupSessionBuilder(storage)
            sender_name_str = str(sender_name)

            async def _work() -> dict[str, bytes]:
                existing = await auth.keys.get("sender-key", [sender_name_str])
                if not existing.get(sender_name_str):
                    await storage.store_sender_key(sender_name, SenderKeyRecord())
                sender_key_distribution_message = await builder.create(sender_name)
                session = GroupCipher(storage, sender_name)
                ciphertext = await session.encrypt(data)
                return {
                    "ciphertext": ciphertext,
                    "senderKeyDistributionMessage": sender_key_distribution_message.serialize(),
                }

            if hasattr(auth.keys, "transaction"):
                return await auth.keys.transaction(_work, group)
            return await _work()

        async def inject_e2e_session(self, opts: dict[str, Any]) -> None:
            jid = opts["jid"]
            session = opts["session"]
            material = (
                bytes(session.get("identityKey") or b"")
                + bytes((session.get("signedPreKey") or {}).get("publicKey") or b"")
                + bytes((session.get("preKey") or {}).get("publicKey") or b"")
            )
            if not material:
                material = os.urandom(32)
            key = hashlib.sha256(material).digest()
            addr = str(jid_to_signal_protocol_address(jid))

            async def _work() -> None:
                await storage.store_session_bytes(addr, key)

            if hasattr(auth.keys, "transaction"):
                await auth.keys.transaction(_work, jid)
            else:
                await _work()

        def jid_to_signal_protocol_address(self, jid: str) -> str:
            return str(jid_to_signal_protocol_address(jid))

        async def validate_session(self, jid: str) -> dict[str, Any]:
            try:
                addr = str(jid_to_signal_protocol_address(jid))
                session = await storage.load_session_bytes(addr)
                if not session:
                    return {"exists": False, "reason": "no session"}
                return {"exists": True}
            except Exception:
                return {"exists": False, "reason": "validation error"}

        async def delete_session(self, jids: list[str]) -> None:
            if not jids:
                return
            updates: dict[str, None] = {}
            for jid in jids:
                addr = str(jid_to_signal_protocol_address(jid))
                updates[addr] = None

            async def _work() -> None:
                await auth.keys.set({"session": updates})

            if hasattr(auth.keys, "transaction"):
                await auth.keys.transaction(_work, f"delete-{len(jids)}-sessions")
            else:
                await _work()

        async def migrate_session(self, from_jid: str, to_jid: str) -> dict[str, int]:
            if not from_jid or not (is_lid_user(to_jid) or is_hosted_lid_user(to_jid)):
                return {"migrated": 0, "skipped": 0, "total": 0}
            if not (is_pn_user(from_jid) or is_hosted_pn_user(from_jid)):
                return {"migrated": 0, "skipped": 0, "total": 1}

            decoded_from = jid_decode(from_jid) or {}
            user = decoded_from.get("user")
            if not user:
                return {"migrated": 0, "skipped": 0, "total": 0}

            stored_devices = await auth.keys.get("device-list", [user])
            user_devices = list(stored_devices.get(user) or [])
            from_device = str(decoded_from.get("device") or 0)
            if from_device not in user_devices:
                user_devices.append(from_device)

            existing_sessions = await auth.keys.get("session", [f"{user}.{device}" for device in user_devices])
            device_jids = []
            for session_key, session_data in existing_sessions.items():
                if not session_data:
                    continue
                _, device = session_key.split(".", 1)
                device_num = int(device)
                if device_num == 0:
                    jid = f"{user}@s.whatsapp.net"
                elif device_num == 99:
                    jid = f"{user}:99@hosted"
                else:
                    jid = f"{user}:{device_num}@s.whatsapp.net"
                device_jids.append(jid)

            async def _work() -> dict[str, int]:
                total = len(device_jids)
                migrated = 0
                session_updates: dict[str, bytes | None] = {}
                migration_ops: list[tuple[str, str, str]] = []
                for jid in device_jids:
                    decoded = jid_decode(jid) or {}
                    pn_user = str(decoded.get("user") or "")
                    device_id = int(decoded.get("device") or 0)
                    device_key = f"{pn_user}.{device_id}"
                    from_addr = str(jid_to_signal_protocol_address(jid))
                    to_lid = transfer_device(jid, to_jid)
                    to_addr = str(jid_to_signal_protocol_address(to_lid))
                    migration_ops.append((from_addr, to_addr, device_key))

                pn_sessions = await auth.keys.get("session", [op[0] for op in migration_ops])

                for from_addr, to_addr, device_key in migration_ops:
                    if device_key in migrated_session_cache:
                        continue
                    existing = pn_sessions.get(from_addr)
                    if existing:
                        session_updates[to_addr] = existing
                        session_updates[from_addr] = None
                        migrated += 1
                        migrated_session_cache.add(device_key)
                if session_updates:
                    await auth.keys.set({"session": session_updates})
                return {"migrated": migrated, "skipped": total - migrated, "total": total}

            if hasattr(auth.keys, "transaction"):
                return await auth.keys.transaction(_work, f"migrate-{len(device_jids)}-sessions-{jid_decode(to_jid).get('user')}")
            return await _work()

        # camelCase aliases for Baileys API parity
        decryptGroupMessage = decrypt_group_message
        processSenderKeyDistributionMessage = process_sender_key_distribution_message
        decryptMessage = decrypt_message
        encryptMessage = encrypt_message
        encryptGroupMessage = encrypt_group_message
        injectE2ESession = inject_e2e_session
        jidToSignalProtocolAddress = jid_to_signal_protocol_address
        validateSession = validate_session
        deleteSession = delete_session
        migrateSession = migrate_session

    repo = _Repository()
    setattr(repo, "lid_mapping", lid_mapping)
    setattr(repo, "lidMapping", lid_mapping)
    return repo


def jid_to_signal_protocol_address(jid: str) -> ProtocolAddress:
    decoded = jid_decode(jid) or {}
    user = decoded.get("user")
    if not user:
        raise ValueError(f"invalid jid, missing user: {jid}")
    device = int(decoded.get("device") or 0)
    domain_type = int(decoded.get("domainType") or WAJIDDomains.WHATSAPP)
    signal_user = f"{user}_{domain_type}" if domain_type != WAJIDDomains.WHATSAPP else user
    if device == 99 and decoded.get("server") not in {"hosted", "hosted.lid"}:
        raise ValueError(f"unexpected non-hosted jid with device 99: {jid}")
    return ProtocolAddress(signal_user, device)


def jid_to_signal_sender_key_name(group: str, user: str) -> SenderKeyName:
    addr = jid_to_signal_protocol_address(user)
    return SenderKeyName(group, Sender(id=addr.name, device_id=addr.device_id))


# camelCase aliases for parity
makeLibSignalRepository = make_libsignal_repository
jidToSignalProtocolAddress = jid_to_signal_protocol_address
jidToSignalSenderKeyName = jid_to_signal_sender_key_name
