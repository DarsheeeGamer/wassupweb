from __future__ import annotations

from typing import Any

from ..defaults import KEY_BUNDLE_TYPE, S_WHATSAPP_NET
from ..types.auth import AuthenticationCreds, AuthenticationState, KeyPair, SignalIdentity, SignedKeyPair
from ..utils.crypto import Curve, generate_signal_pub_key
from ..wabinary import (
    WAJIDDomains,
    assert_node_error_free,
    get_binary_node_child,
    get_binary_node_child_buffer,
    get_binary_node_child_uint,
    get_binary_node_children,
    get_server_from_domain_type,
    jid_decode,
)
from ..wabinary.types import BinaryNode
from ..wausync.protocols.device import DeviceListData, ParsedDeviceInfo
from .generics import encode_big_endian


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def create_signal_identity(wid: str, account_signature_key: bytes) -> SignalIdentity:
    return SignalIdentity(name=wid, device_id=0, identifier_key=generate_signal_pub_key(account_signature_key))


async def get_pre_keys(store: Any, min_id: int, limit: int) -> dict[str, KeyPair]:
    ids = [str(key_id) for key_id in range(min_id, limit)]
    return await store.get("pre-key", ids)


def generate_or_get_pre_keys(creds: AuthenticationCreds, key_range: int) -> dict[str, Any]:
    available = creds.next_pre_key_id - creds.first_unuploaded_pre_key_id
    remaining = key_range - available
    last_pre_key_id = creds.next_pre_key_id + remaining - 1
    new_pre_keys: dict[int, KeyPair] = {}
    if remaining > 0:
        for key_id in range(creds.next_pre_key_id, last_pre_key_id + 1):
            new_pre_keys[key_id] = Curve.generate_key_pair()
    return {
        "newPreKeys": new_pre_keys,
        "lastPreKeyId": last_pre_key_id,
        "preKeysRange": (creds.first_unuploaded_pre_key_id, key_range),
    }


def xmpp_signed_pre_key(key: SignedKeyPair) -> BinaryNode:
    return BinaryNode(
        tag="skey",
        attrs={},
        content=[
            BinaryNode(tag="id", attrs={}, content=encode_big_endian(key.key_id, 3)),
            BinaryNode(tag="value", attrs={}, content=bytes(key.key_pair.public)),
            BinaryNode(tag="signature", attrs={}, content=bytes(key.signature)),
        ],
    )


def xmpp_pre_key(pair: KeyPair, key_id: int) -> BinaryNode:
    return BinaryNode(
        tag="key",
        attrs={},
        content=[
            BinaryNode(tag="id", attrs={}, content=encode_big_endian(key_id, 3)),
            BinaryNode(tag="value", attrs={}, content=bytes(pair.public)),
        ],
    )


async def parse_and_inject_e2e_sessions(node: BinaryNode, repository: Any) -> None:
    def _extract_key(key_node: BinaryNode | None) -> dict[str, Any] | None:
        if not key_node:
            return None
        return {
            "keyId": get_binary_node_child_uint(key_node, "id", 3),
            "publicKey": generate_signal_pub_key(get_binary_node_child_buffer(key_node, "value") or b""),
            "signature": get_binary_node_child_buffer(key_node, "signature"),
        }

    user_nodes = get_binary_node_children(get_binary_node_child(node, "list"), "user")
    for user_node in user_nodes:
        assert_node_error_free(user_node)

    for chunk in _chunk(user_nodes, 100):
        for user_node in chunk:
            signed_key = get_binary_node_child(user_node, "skey")
            pre_key = get_binary_node_child(user_node, "key")
            identity = get_binary_node_child_buffer(user_node, "identity") or b""
            jid = user_node.attrs.get("jid")
            registration_id = get_binary_node_child_uint(user_node, "registration", 4)
            if not jid or registration_id is None:
                continue
            await repository.inject_e2e_session(
                {
                    "jid": jid,
                    "session": {
                        "registrationId": registration_id,
                        "identityKey": generate_signal_pub_key(identity),
                        "signedPreKey": _extract_key(signed_key),
                        "preKey": _extract_key(pre_key),
                    },
                }
            )


def extract_device_jids(
    result: list[dict[str, Any]],
    my_jid: str,
    my_lid: str,
    exclude_zero_devices: bool,
) -> list[dict[str, Any]]:
    mine = jid_decode(my_jid) or {}
    my_user = mine.get("user")
    my_device = mine.get("device")
    extracted: list[dict[str, Any]] = []

    for user_result in result:
        devices = user_result.get("devices")
        item_id = user_result.get("id")
        decoded = jid_decode(item_id) if item_id else None
        if not decoded:
            continue
        user = decoded.get("user")
        server = decoded.get("server")
        domain_type = decoded.get("domainType")
        device_list = []
        if isinstance(devices, ParsedDeviceInfo):
            device_list = devices.device_list
        elif isinstance(devices, dict):
            device_list = [DeviceListData.model_validate(item) for item in devices.get("deviceList", [])]

        for device in device_list:
            device_id = int(device.id)
            key_index = device.key_index
            is_hosted = bool(device.is_hosted)
            if exclude_zero_devices and device_id == 0:
                continue
            if ((my_user == user or my_lid == user) and my_device == device_id):
                continue
            if device_id != 0 and key_index in (None, 0):
                continue
            resolved_domain = domain_type
            if is_hosted:
                resolved_domain = WAJIDDomains.HOSTED_LID if domain_type == WAJIDDomains.LID else WAJIDDomains.HOSTED
            extracted.append(
                {
                    "user": user,
                    "device": device_id,
                    "domainType": resolved_domain,
                    "server": get_server_from_domain_type(server, resolved_domain),
                }
            )

    return extracted


async def get_next_pre_keys(state: AuthenticationState, count: int) -> dict[str, Any]:
    creds = state.creds
    keys = state.keys
    generated = generate_or_get_pre_keys(creds, count)
    new_pre_keys = generated["newPreKeys"]
    last_pre_key_id = generated["lastPreKeyId"]
    start, span = generated["preKeysRange"]

    update = {
        "nextPreKeyId": max(last_pre_key_id + 1, creds.next_pre_key_id),
        "firstUnuploadedPreKeyId": max(creds.first_unuploaded_pre_key_id, last_pre_key_id + 1),
    }

    if new_pre_keys:
        await keys.set({"pre-key": {str(key_id): key for key_id, key in new_pre_keys.items()}})

    pre_keys = await get_pre_keys(keys, start, start + span)
    return {"update": update, "preKeys": pre_keys}


async def get_next_pre_keys_node(state: AuthenticationState, count: int) -> dict[str, Any]:
    creds = state.creds
    generated = await get_next_pre_keys(state, count)
    pre_keys = generated["preKeys"]
    node = BinaryNode(
        tag="iq",
        attrs={"xmlns": "encrypt", "type": "set", "to": S_WHATSAPP_NET},
        content=[
            BinaryNode(tag="registration", attrs={}, content=encode_big_endian(creds.registration_id)),
            BinaryNode(tag="type", attrs={}, content=KEY_BUNDLE_TYPE),
            BinaryNode(tag="identity", attrs={}, content=bytes(creds.signed_identity_key.public)),
            BinaryNode(
                tag="list",
                attrs={},
                content=[xmpp_pre_key(pre_keys[key], int(key)) for key in pre_keys.keys()],
            ),
            xmpp_signed_pre_key(creds.signed_pre_key),
        ],
    )
    return {"update": generated["update"], "node": node}


# camelCase aliases
createSignalIdentity = create_signal_identity
generateOrGetPreKeys = generate_or_get_pre_keys
xmppSignedPreKey = xmpp_signed_pre_key
xmppPreKey = xmpp_pre_key
parseAndInjectE2ESessions = parse_and_inject_e2e_sessions
extractDeviceJids = extract_device_jids
getNextPreKeys = get_next_pre_keys
getNextPreKeysNode = get_next_pre_keys_node


__all__ = [
    "create_signal_identity",
    "get_pre_keys",
    "generate_or_get_pre_keys",
    "xmpp_signed_pre_key",
    "xmpp_pre_key",
    "parse_and_inject_e2e_sessions",
    "extract_device_jids",
    "get_next_pre_keys",
    "get_next_pre_keys_node",
]
