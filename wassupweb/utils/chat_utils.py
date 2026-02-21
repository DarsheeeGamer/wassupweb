from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Awaitable, Callable

from ..types.label_association import LabelAssociationType
from ..wabinary import BinaryNode, get_binary_node_child, get_binary_node_children, is_jid_group, jid_normalized_user
from .crypto import aes_decrypt, aes_encrypt, hmac_sign
from .generics import to_number
from .lt_hash import LT_HASH_ANTI_TAMPERING
from .messages_media import download_content_from_message, to_buffer
from .sync_action_utils import emit_sync_action_results, process_contact_action

OP_SET = 1
OP_REMOVE = 2

FetchAppStateSyncKey = Callable[[str], Awaitable[dict[str, Any] | None]]
ChatMutationMap = dict[str, dict[str, Any]]


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _json_object_hook(value: dict[str, Any]) -> Any:
    if value.get("__type__") == "bytes":
        return base64.b64decode(value["base64"])
    return value


def _value_to_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        try:
            return base64.b64decode(value)
        except Exception:
            return value.encode("utf-8")
    raise TypeError(f"expected bytes-like value, got {type(value)!r}")


def mutation_keys(keydata: bytes) -> dict[str, bytes]:
    # Fallback deterministic key expansion. Keeps shape compatible with Baileys.
    seed = hmac_sign(keydata, b"app-state-sync-key-seed", "sha512")
    material = b""
    counter = 1
    while len(material) < 160:
        material += hmac_sign(seed + bytes([counter]), keydata, "sha512")
        counter += 1
    return {
        "indexKey": material[0:32],
        "valueEncryptionKey": material[32:64],
        "valueMacKey": material[64:96],
        "snapshotMacKey": material[96:128],
        "patchMacKey": material[128:160],
    }


def generate_mac(operation: int, data: bytes, key_id: bytes | str, key: bytes) -> bytes:
    op_byte = 0x01 if int(operation) == OP_SET else 0x02
    key_id_buffer = base64.b64decode(key_id) if isinstance(key_id, str) else bytes(key_id)
    key_data = bytes([op_byte]) + key_id_buffer
    suffix = bytearray(8)
    suffix[7] = len(key_data)
    total = key_data + data + bytes(suffix)
    return hmac_sign(total, key, "sha512")[:32]


def to_64_bit_network_order(value: int) -> bytes:
    return int(value).to_bytes(8, "big", signed=False)


def make_lt_hash_generator(state: dict[str, Any]) -> dict[str, Any]:
    index_value_map = dict(state.get("indexValueMap") or {})
    current_hash = bytes(state.get("hash") or b"\x00" * 128)
    add_values: list[bytes] = []
    sub_values: list[bytes] = []

    def mix(mac: dict[str, Any]) -> None:
        index_mac = bytes(mac["indexMac"])
        value_mac = bytes(mac["valueMac"])
        operation = int(mac["operation"])

        index_b64 = base64.b64encode(index_mac).decode("ascii")
        prev = index_value_map.get(index_b64)
        if operation == OP_REMOVE:
            if not prev:
                raise ValueError("tried remove, but no previous op")
            index_value_map.pop(index_b64, None)
        else:
            add_values.append(value_mac)
            index_value_map[index_b64] = {"valueMac": value_mac}

        if prev:
            sub_values.append(bytes(prev["valueMac"]))

    def finish() -> dict[str, Any]:
        result = LT_HASH_ANTI_TAMPERING.subtract_then_add(current_hash, sub_values, add_values)
        return {"hash": result, "indexValueMap": index_value_map}

    return {"mix": mix, "finish": finish}


def generate_snapshot_mac(lthash: bytes, version: int, name: str, key: bytes) -> bytes:
    return hmac_sign(lthash + to_64_bit_network_order(version) + name.encode("utf-8"), key, "sha256")


def generate_patch_mac(snapshot_mac: bytes, value_macs: list[bytes], version: int, patch_type: str, key: bytes) -> bytes:
    return hmac_sign(snapshot_mac + b"".join(value_macs) + to_64_bit_network_order(version) + patch_type.encode("utf-8"), key)


def new_lthash_state() -> dict[str, Any]:
    return {"version": 0, "hash": b"\x00" * 128, "indexValueMap": {}}


async def encode_syncd_patch(
    patch_create: dict[str, Any],
    my_app_state_key_id: str,
    state: dict[str, Any],
    get_app_state_sync_key: FetchAppStateSyncKey,
) -> dict[str, Any]:
    key_obj = await get_app_state_sync_key(my_app_state_key_id) if my_app_state_key_id else None
    if not key_obj:
        raise ValueError(f'myAppStateKey ("{my_app_state_key_id}") not present')

    key_data = _value_to_bytes(key_obj.get("keyData") or key_obj.get("key_data") or b"")
    key_set = mutation_keys(key_data)
    enc_key_id = base64.b64decode(my_app_state_key_id)

    current = {
        "version": int(state.get("version") or 0),
        "hash": bytes(state.get("hash") or b""),
        "indexValueMap": dict(state.get("indexValueMap") or {}),
    }

    index = patch_create.get("index") or []
    sync_action = patch_create.get("syncAction") or {}
    operation = int(patch_create.get("operation", OP_SET))
    api_version = int(patch_create.get("apiVersion", 1))
    patch_type = str(patch_create.get("type"))

    index_buffer = json.dumps(index, separators=(",", ":")).encode("utf-8")
    encoded = json.dumps(
        {"index": index, "value": sync_action, "version": api_version},
        default=_json_default,
        separators=(",", ":"),
    ).encode("utf-8")

    enc_value = aes_encrypt(encoded, key_set["valueEncryptionKey"])
    value_mac = generate_mac(operation, enc_value, enc_key_id, key_set["valueMacKey"])
    index_mac = hmac_sign(index_buffer, key_set["indexKey"])

    generator = make_lt_hash_generator(current)
    generator["mix"]({"indexMac": index_mac, "valueMac": value_mac, "operation": operation})
    mixed = generator["finish"]()
    current["hash"] = mixed["hash"]
    current["indexValueMap"] = mixed["indexValueMap"]
    current["version"] += 1

    snapshot_mac = generate_snapshot_mac(current["hash"], current["version"], patch_type, key_set["snapshotMacKey"])
    patch_mac = generate_patch_mac(snapshot_mac, [value_mac], current["version"], patch_type, key_set["patchMacKey"])

    patch = {
        "patchMac": patch_mac,
        "snapshotMac": snapshot_mac,
        "keyId": {"id": enc_key_id},
        "version": {"version": current["version"]},
        "mutations": [
            {
                "operation": operation,
                "record": {
                    "index": {"blob": index_mac},
                    "value": {"blob": enc_value + value_mac},
                    "keyId": {"id": enc_key_id},
                },
            }
        ],
    }

    current["indexValueMap"][base64.b64encode(index_mac).decode("ascii")] = {"valueMac": value_mac}
    return {"patch": patch, "state": current}


async def decode_syncd_mutations(
    msg_mutations: list[dict[str, Any]],
    initial_state: dict[str, Any],
    get_app_state_sync_key: FetchAppStateSyncKey,
    on_mutation: Callable[[dict[str, Any]], Any],
    validate_macs: bool,
) -> dict[str, Any]:
    lt_generator = make_lt_hash_generator(initial_state)
    key_cache: dict[str, dict[str, bytes]] = {}

    async def get_key(key_id: bytes) -> dict[str, bytes]:
        base64_key = base64.b64encode(key_id).decode("ascii")
        if base64_key in key_cache:
            return key_cache[base64_key]
        key_obj = await get_app_state_sync_key(base64_key)
        if not key_obj:
            raise ValueError(f'failed to find key "{base64_key}" to decode mutation')
        keys = mutation_keys(_value_to_bytes(key_obj.get("keyData") or key_obj.get("key_data") or b""))
        key_cache[base64_key] = keys
        return keys

    for mutation in msg_mutations:
        operation = int(mutation.get("operation", OP_SET))
        record = mutation.get("record") or mutation
        key_id = _value_to_bytes(((record.get("keyId") or {}).get("id")) or b"")
        keys = await get_key(key_id)

        content = _value_to_bytes(((record.get("value") or {}).get("blob")) or b"")
        if len(content) < 32:
            raise ValueError("invalid mutation value blob")
        enc_content = content[:-32]
        og_value_mac = content[-32:]

        if validate_macs:
            content_mac = generate_mac(operation, enc_content, key_id, keys["valueMacKey"])
            if content_mac != og_value_mac:
                raise ValueError("HMAC content verification failed")

        decoded_raw = aes_decrypt(enc_content, keys["valueEncryptionKey"])
        decoded = json.loads(decoded_raw.decode("utf-8"), object_hook=_json_object_hook)
        index = decoded.get("index") or []

        if validate_macs:
            record_index = _value_to_bytes(((record.get("index") or {}).get("blob")) or b"")
            expected_index = hmac_sign(json.dumps(index, separators=(",", ":")).encode("utf-8"), keys["indexKey"])
            if expected_index != record_index:
                raise ValueError("HMAC index verification failed")

        mutation_obj = {
            "syncAction": {
                "index": index,
                "value": decoded.get("value"),
                "version": decoded.get("version"),
            },
            "index": index,
        }
        result = on_mutation(mutation_obj)
        if asyncio.iscoroutine(result):
            await result

        lt_generator["mix"](
            {
                "indexMac": _value_to_bytes(((record.get("index") or {}).get("blob")) or b""),
                "valueMac": og_value_mac,
                "operation": operation,
            }
        )

    return lt_generator["finish"]()


async def decode_syncd_patch(
    msg: dict[str, Any],
    name: str,
    initial_state: dict[str, Any],
    get_app_state_sync_key: FetchAppStateSyncKey,
    on_mutation: Callable[[dict[str, Any]], Any],
    validate_macs: bool,
) -> dict[str, Any]:
    if validate_macs:
        key_id = _value_to_bytes(((msg.get("keyId") or {}).get("id")) or b"")
        base64_key = base64.b64encode(key_id).decode("ascii")
        key_obj = await get_app_state_sync_key(base64_key)
        if not key_obj:
            raise ValueError(f'failed to find key "{base64_key}" to decode patch')
        keys = mutation_keys(_value_to_bytes(key_obj.get("keyData") or key_obj.get("key_data") or b""))
        mutation_macs = []
        for mutation in msg.get("mutations") or []:
            blob = _value_to_bytes((((mutation.get("record") or {}).get("value") or {}).get("blob")) or b"")
            mutation_macs.append(blob[-32:])

        patch_mac = generate_patch_mac(
            _value_to_bytes(msg.get("snapshotMac") or b""),
            mutation_macs,
            to_number((msg.get("version") or {}).get("version")),
            name,
            keys["patchMacKey"],
        )
        if patch_mac != _value_to_bytes(msg.get("patchMac") or b""):
            raise ValueError("Invalid patch mac")

    return await decode_syncd_mutations(
        msg.get("mutations") or [],
        initial_state,
        get_app_state_sync_key,
        on_mutation,
        validate_macs,
    )


async def extract_syncd_patches(result: BinaryNode, options: dict[str, Any]) -> dict[str, Any]:
    def _decode_json_blob(content: Any) -> dict[str, Any] | None:
        def _decode_bytes_payload(data: bytes) -> dict[str, Any] | None:
            try:
                decoded = json.loads(data.decode("utf-8"), object_hook=_json_object_hook)
                return decoded if isinstance(decoded, dict) else None
            except Exception:
                return None

        def _dict_to_bytes_if_byte_map(value: dict[str, Any]) -> bytes | None:
            if not value:
                return None
            if all(isinstance(k, str) and k.isdigit() for k in value.keys()) and all(
                isinstance(v, int) and 0 <= v <= 255 for v in value.values()
            ):
                ordered = [value[key] for key in sorted(value.keys(), key=lambda item: int(item))]
                return bytes(ordered)
            return None

        if isinstance(content, dict):
            maybe_bytes = _dict_to_bytes_if_byte_map(content)
            if maybe_bytes is not None:
                return _decode_bytes_payload(maybe_bytes)
            return dict(content)
        if isinstance(content, list) and all(isinstance(v, int) and 0 <= v <= 255 for v in content):
            return _decode_bytes_payload(bytes(content))
        if isinstance(content, (bytes, bytearray)):
            return _decode_bytes_payload(bytes(content))
        return None

    sync_node = get_binary_node_child(result, "sync")
    collection_nodes = get_binary_node_children(sync_node, "collection")
    final: dict[str, Any] = {}

    for collection_node in collection_nodes:
        patches_node = get_binary_node_child(collection_node, "patches")
        patches = get_binary_node_children(patches_node or collection_node, "patch")
        snapshot_node = get_binary_node_child(collection_node, "snapshot")
        name = str(collection_node.attrs.get("name") or "")
        has_more = collection_node.attrs.get("has_more_patches") == "true"

        syncds: list[dict[str, Any]] = []
        snapshot: dict[str, Any] | None = None

        if snapshot_node:
            snapshot_payload = _decode_json_blob(snapshot_node.content)
            if snapshot_payload and (
                "directPath" in snapshot_payload or "direct_path" in snapshot_payload or "mediaKey" in snapshot_payload
            ):
                # External snapshot reference; download then decode into snapshot object.
                try:
                    blob_data = await download_external_blob(snapshot_payload, options)
                    snapshot = _decode_json_blob(blob_data)
                except Exception:
                    snapshot = None
            else:
                snapshot = snapshot_payload

        for patch_node in patches:
            syncd = _decode_json_blob(patch_node.content)
            if syncd is not None:
                if "version" not in syncd:
                    syncd["version"] = {"version": int(collection_node.attrs.get("version", 0)) + 1}
                syncds.append(syncd)

        final[name] = {
            "patches": syncds,
            "hasMorePatches": has_more,
            "snapshot": snapshot,
        }

    return final


async def download_external_blob(blob: dict[str, Any], options: dict[str, Any]) -> bytes:
    stream = await download_content_from_message(blob, "md-app-state", {"options": options})
    return await to_buffer(stream)


async def download_external_patch(blob: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    buffer = await download_external_blob(blob, options)
    try:
        decoded = json.loads(buffer.decode("utf-8"), object_hook=_json_object_hook)
        return decoded if isinstance(decoded, dict) else {"mutations": []}
    except Exception:
        return {"mutations": []}


async def decode_syncd_snapshot(
    name: str,
    snapshot: dict[str, Any],
    get_app_state_sync_key: FetchAppStateSyncKey,
    minimum_version_number: int | None,
    validate_macs: bool = True,
) -> dict[str, Any]:
    new_state = new_lthash_state()
    new_state["version"] = to_number((snapshot.get("version") or {}).get("version"))
    mutation_map: ChatMutationMap = {}
    are_mutations_required = minimum_version_number is None or new_state["version"] > minimum_version_number

    decoded = await decode_syncd_mutations(
        snapshot.get("records") or [],
        new_state,
        get_app_state_sync_key,
        (lambda mutation: mutation_map.__setitem__(json.dumps(mutation.get("index") or []), mutation))
        if are_mutations_required
        else (lambda _mutation: None),
        validate_macs,
    )
    new_state["hash"] = decoded["hash"]
    new_state["indexValueMap"] = decoded["indexValueMap"]

    if validate_macs:
        key_id = _value_to_bytes(((snapshot.get("keyId") or {}).get("id")) or b"")
        base64_key = base64.b64encode(key_id).decode("ascii")
        key_obj = await get_app_state_sync_key(base64_key)
        if not key_obj:
            raise ValueError(f'failed to find key "{base64_key}" to decode mutation')
        keys = mutation_keys(_value_to_bytes(key_obj.get("keyData") or key_obj.get("key_data") or b""))
        computed = generate_snapshot_mac(
            new_state["hash"],
            new_state["version"],
            name,
            keys["snapshotMacKey"],
        )
        if computed != _value_to_bytes(snapshot.get("mac") or b""):
            raise ValueError(f'failed to verify LTHash at {new_state["version"]} of {name} from snapshot')

    return {"state": new_state, "mutationMap": mutation_map}


async def decode_patches(
    name: str,
    syncds: list[dict[str, Any]],
    initial: dict[str, Any],
    get_app_state_sync_key: FetchAppStateSyncKey,
    options: dict[str, Any],
    minimum_version_number: int | None = None,
    logger: Any = None,
    validate_macs: bool = True,
) -> dict[str, Any]:
    new_state = {
        "version": int(initial.get("version") or 0),
        "hash": bytes(initial.get("hash") or b""),
        "indexValueMap": dict(initial.get("indexValueMap") or {}),
    }
    mutation_map: ChatMutationMap = {}

    for syncd in syncds:
        if syncd.get("externalMutations"):
            if logger:
                logger.debug(
                    "downloading external patch",
                    extra={"name": name, "version": syncd.get("version")},
                )
            ref = await download_external_patch(syncd["externalMutations"], options)
            syncd.setdefault("mutations", [])
            syncd["mutations"].extend(ref.get("mutations") or [])

        patch_version = to_number((syncd.get("version") or {}).get("version"))
        new_state["version"] = patch_version
        should_mutate = minimum_version_number is None or patch_version > minimum_version_number

        decoded = await decode_syncd_patch(
            syncd,
            name,
            new_state,
            get_app_state_sync_key,
            (lambda mutation: mutation_map.__setitem__(json.dumps(mutation.get("index") or []), mutation))
            if should_mutate
            else (lambda _mutation: None),
            validate_macs,
        )
        new_state["hash"] = decoded["hash"]
        new_state["indexValueMap"] = decoded["indexValueMap"]

        if validate_macs:
            key_id = _value_to_bytes(((syncd.get("keyId") or {}).get("id")) or b"")
            base64_key = base64.b64encode(key_id).decode("ascii")
            key_obj = await get_app_state_sync_key(base64_key)
            if not key_obj:
                raise ValueError(f'failed to find key "{base64_key}" to decode mutation')
            keys = mutation_keys(_value_to_bytes(key_obj.get("keyData") or key_obj.get("key_data") or b""))
            computed = generate_snapshot_mac(
                new_state["hash"],
                new_state["version"],
                name,
                keys["snapshotMacKey"],
            )
            if computed != _value_to_bytes(syncd.get("snapshotMac") or b""):
                raise ValueError(f'failed to verify LTHash at {new_state["version"]} of {name}')

        syncd["mutations"] = []

    return {"state": new_state, "mutationMap": mutation_map}


def chat_modification_to_app_patch(mod: dict[str, Any], jid: str) -> dict[str, Any]:
    def get_message_range(last_messages: Any) -> dict[str, Any]:
        if not isinstance(last_messages, list):
            return last_messages or {}

        last_msg = last_messages[-1] if last_messages else None
        messages = []
        for item in last_messages:
            key = dict(item.get("key") or {})
            if not key.get("id") or not key.get("remoteJid"):
                raise ValueError("Incomplete key")
            if is_jid_group(key["remoteJid"]) and not key.get("fromMe") and not key.get("participant"):
                raise ValueError("Expected not-from-me group message to include participant")
            if not item.get("messageTimestamp") or not to_number(item.get("messageTimestamp")):
                raise ValueError("Missing timestamp in last message list")
            if key.get("participant"):
                key["participant"] = jid_normalized_user(key["participant"])
            next_item = dict(item)
            next_item["key"] = key
            messages.append(next_item)

        return {
            "lastMessageTimestamp": (last_msg or {}).get("messageTimestamp"),
            "messages": messages or None,
        }

    if "mute" in mod:
        patch = {
            "syncAction": {
                "muteAction": {
                    "muted": bool(mod["mute"]),
                    "muteEndTimestamp": mod["mute"] or None,
                }
            },
            "index": ["mute", jid],
            "type": "regular_high",
            "apiVersion": 2,
            "operation": OP_SET,
        }
    elif "archive" in mod:
        patch = {
            "syncAction": {
                "archiveChatAction": {
                    "archived": bool(mod["archive"]),
                    "messageRange": get_message_range(mod.get("lastMessages")),
                }
            },
            "index": ["archive", jid],
            "type": "regular_low",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "markRead" in mod:
        patch = {
            "syncAction": {
                "markChatAsReadAction": {
                    "read": bool(mod["markRead"]),
                    "messageRange": get_message_range(mod.get("lastMessages")),
                }
            },
            "index": ["markChatAsRead", jid],
            "type": "regular_low",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "deleteForMe" in mod:
        value = mod["deleteForMe"]
        key = value["key"]
        patch = {
            "syncAction": {
                "deleteMessageForMeAction": {
                    "deleteMedia": value.get("deleteMedia"),
                    "messageTimestamp": value.get("timestamp"),
                }
            },
            "index": ["deleteMessageForMe", jid, key.get("id"), "1" if key.get("fromMe") else "0", "0"],
            "type": "regular_high",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "clear" in mod:
        patch = {
            "syncAction": {"clearChatAction": {"messageRange": get_message_range(mod.get("lastMessages"))}},
            "index": ["clearChat", jid, "1", "0"],
            "type": "regular_high",
            "apiVersion": 6,
            "operation": OP_SET,
        }
    elif "pin" in mod:
        patch = {
            "syncAction": {"pinAction": {"pinned": bool(mod["pin"])}},
            "index": ["pin_v1", jid],
            "type": "regular_low",
            "apiVersion": 5,
            "operation": OP_SET,
        }
    elif "contact" in mod:
        patch = {
            "syncAction": {"contactAction": mod.get("contact") or {}},
            "index": ["contact", jid],
            "type": "critical_unblock_low",
            "apiVersion": 2,
            "operation": OP_SET if mod.get("contact") else OP_REMOVE,
        }
    elif "disableLinkPreviews" in mod:
        patch = {
            "syncAction": {"privacySettingDisableLinkPreviewsAction": mod.get("disableLinkPreviews") or {}},
            "index": ["setting_disableLinkPreviews"],
            "type": "regular",
            "apiVersion": 8,
            "operation": OP_SET,
        }
    elif "star" in mod:
        key = mod["star"]["messages"][0]
        patch = {
            "syncAction": {"starAction": {"starred": bool(mod["star"].get("star"))}},
            "index": ["star", jid, key.get("id"), "1" if key.get("fromMe") else "0", "0"],
            "type": "regular_low",
            "apiVersion": 2,
            "operation": OP_SET,
        }
    elif "delete" in mod:
        patch = {
            "syncAction": {"deleteChatAction": {"messageRange": get_message_range(mod.get("lastMessages"))}},
            "index": ["deleteChat", jid, "1"],
            "type": "regular_high",
            "apiVersion": 6,
            "operation": OP_SET,
        }
    elif "pushNameSetting" in mod:
        patch = {
            "syncAction": {"pushNameSetting": {"name": mod["pushNameSetting"]}},
            "index": ["setting_pushName"],
            "type": "critical_block",
            "apiVersion": 1,
            "operation": OP_SET,
        }
    elif "quickReply" in mod:
        quick = mod["quickReply"]
        patch = {
            "syncAction": {
                "quickReplyAction": {
                    "count": 0,
                    "deleted": quick.get("deleted", False),
                    "keywords": [],
                    "message": quick.get("message", ""),
                    "shortcut": quick.get("shortcut", ""),
                }
            },
            "index": ["quick_reply", quick.get("timestamp") or str(int(time.time()))],
            "type": "regular",
            "apiVersion": 2,
            "operation": OP_SET,
        }
    elif "addLabel" in mod:
        value = mod["addLabel"]
        patch = {
            "syncAction": {
                "labelEditAction": {
                    "name": value.get("name"),
                    "color": value.get("color"),
                    "predefinedId": value.get("predefinedId"),
                    "deleted": value.get("deleted"),
                }
            },
            "index": ["label_edit", value.get("id")],
            "type": "regular",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "addChatLabel" in mod:
        patch = {
            "syncAction": {"labelAssociationAction": {"labeled": True}},
            "index": [LabelAssociationType.Chat, mod["addChatLabel"]["labelId"], jid],
            "type": "regular",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "removeChatLabel" in mod:
        patch = {
            "syncAction": {"labelAssociationAction": {"labeled": False}},
            "index": [LabelAssociationType.Chat, mod["removeChatLabel"]["labelId"], jid],
            "type": "regular",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "addMessageLabel" in mod:
        value = mod["addMessageLabel"]
        patch = {
            "syncAction": {"labelAssociationAction": {"labeled": True}},
            "index": [LabelAssociationType.Message, value["labelId"], jid, value["messageId"], "0", "0"],
            "type": "regular",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    elif "removeMessageLabel" in mod:
        value = mod["removeMessageLabel"]
        patch = {
            "syncAction": {"labelAssociationAction": {"labeled": False}},
            "index": [LabelAssociationType.Message, value["labelId"], jid, value["messageId"], "0", "0"],
            "type": "regular",
            "apiVersion": 3,
            "operation": OP_SET,
        }
    else:
        raise ValueError("not supported")

    patch["syncAction"]["timestamp"] = int(time.time() * 1000)
    return patch


async def _emit(ev: Any, event: str, payload: Any) -> None:
    result = ev.emit(event, payload)
    if asyncio.iscoroutine(result):
        await result


async def process_sync_action(
    sync_action: dict[str, Any],
    ev: Any,
    me: dict[str, Any],
    initial_sync_opts: dict[str, Any] | None = None,
    logger: Any = None,
) -> None:
    is_initial_sync = bool(initial_sync_opts)
    account_settings = (initial_sync_opts or {}).get("accountSettings")

    action_data = sync_action.get("syncAction") or {}
    action = action_data.get("value") if isinstance(action_data, dict) and "value" in action_data else action_data
    index = list(sync_action.get("index") or [])
    type_name = index[0] if len(index) > 0 else None
    item_id = index[1] if len(index) > 1 else None
    msg_id = index[2] if len(index) > 2 else None
    from_me = index[3] if len(index) > 3 else None

    def get_chat_update_conditional(chat_id: str, msg_range: dict[str, Any] | None) -> Callable[[dict[str, Any]], bool | None] | None:
        if not is_initial_sync:
            return None

        def _condition(data: dict[str, Any]) -> bool | None:
            chat = (data.get("historySets") or {}).get("chats", {}).get(chat_id) or (data.get("chatUpserts") or {}).get(chat_id)
            if not chat:
                return None
            if not msg_range:
                return True
            last_msg_timestamp = to_number(msg_range.get("lastMessageTimestamp") or msg_range.get("lastSystemMessageTimestamp") or 0)
            chat_last_timestamp = to_number(chat.get("lastMessageRecvTimestamp") or 0)
            return last_msg_timestamp >= chat_last_timestamp

        return _condition

    if action.get("muteAction"):
        mute = action["muteAction"]
        await _emit(ev, "chats.update", [{"id": item_id, "muteEndTime": to_number(mute.get("muteEndTimestamp")) if mute.get("muted") else None, "conditional": get_chat_update_conditional(item_id, None)}])
    elif action.get("archiveChatAction") or type_name in {"archive", "unarchive"}:
        archive_action = action.get("archiveChatAction") or {}
        archived = bool(archive_action.get("archived")) if action.get("archiveChatAction") else (type_name == "archive")
        msg_range = archive_action.get("messageRange") if (account_settings or {}).get("unarchiveChats") else None
        await _emit(ev, "chats.update", [{"id": item_id, "archived": archived, "conditional": get_chat_update_conditional(item_id, msg_range)}])
    elif action.get("markChatAsReadAction"):
        mark = action["markChatAsReadAction"]
        is_null_update = is_initial_sync and bool(mark.get("read"))
        await _emit(ev, "chats.update", [{"id": item_id, "unreadCount": None if is_null_update else (0 if mark.get("read") else -1), "conditional": get_chat_update_conditional(item_id, mark.get("messageRange"))}])
    elif action.get("deleteMessageForMeAction") or type_name == "deleteMessageForMe":
        await _emit(ev, "messages.delete", {"keys": [{"remoteJid": item_id, "id": msg_id, "fromMe": from_me == "1"}]})
    elif action.get("contactAction"):
        results = process_contact_action(action["contactAction"], item_id, logger)
        await emit_sync_action_results(ev, results)
    elif action.get("pushNameSetting"):
        name = action["pushNameSetting"].get("name")
        if name and (me or {}).get("name") != name:
            await _emit(ev, "creds.update", {"me": {**(me or {}), "name": name}})
    elif action.get("pinAction"):
        await _emit(ev, "chats.update", [{"id": item_id, "pinned": to_number(action.get("timestamp")) if action["pinAction"].get("pinned") else None, "conditional": get_chat_update_conditional(item_id, None)}])
    elif action.get("unarchiveChatsSetting"):
        unarchive_chats = bool(action["unarchiveChatsSetting"].get("unarchiveChats"))
        await _emit(ev, "creds.update", {"accountSettings": {"unarchiveChats": unarchive_chats}})
        if account_settings is not None:
            account_settings["unarchiveChats"] = unarchive_chats
    elif action.get("starAction") or type_name == "star":
        starred = action.get("starAction", {}).get("starred")
        if not isinstance(starred, bool):
            starred = (index[-1] if index else "0") == "1"
        await _emit(ev, "messages.update", [{"key": {"remoteJid": item_id, "id": msg_id, "fromMe": from_me == "1"}, "update": {"starred": starred}}])
    elif action.get("deleteChatAction") or type_name == "deleteChat":
        if not is_initial_sync:
            await _emit(ev, "chats.delete", [item_id])
    elif action.get("labelEditAction"):
        value = action["labelEditAction"]
        await _emit(ev, "labels.edit", {"id": item_id, "name": value.get("name"), "color": value.get("color"), "deleted": value.get("deleted"), "predefinedId": str(value.get("predefinedId")) if value.get("predefinedId") is not None else None})
    elif action.get("labelAssociationAction"):
        is_add = bool(action["labelAssociationAction"].get("labeled"))
        if type_name == LabelAssociationType.Chat:
            association = {"type": LabelAssociationType.Chat, "chatId": index[2] if len(index) > 2 else None, "labelId": index[1] if len(index) > 1 else None}
        else:
            association = {"type": LabelAssociationType.Message, "chatId": index[2] if len(index) > 2 else None, "messageId": index[3] if len(index) > 3 else None, "labelId": index[1] if len(index) > 1 else None}
        await _emit(ev, "labels.association", {"type": "add" if is_add else "remove", "association": association})
    elif action.get("localeSetting", {}).get("locale"):
        await _emit(ev, "settings.update", {"setting": "locale", "value": action["localeSetting"]["locale"]})
    elif action.get("timeFormatAction"):
        await _emit(ev, "settings.update", {"setting": "timeFormat", "value": action["timeFormatAction"]})
    elif action.get("pnForLidChatAction", {}).get("pnJid"):
        await _emit(ev, "lid-mapping.update", {"lid": item_id, "pn": action["pnForLidChatAction"]["pnJid"]})
    elif action.get("privacySettingRelayAllCalls"):
        await _emit(ev, "settings.update", {"setting": "privacySettingRelayAllCalls", "value": action["privacySettingRelayAllCalls"]})
    elif action.get("statusPrivacy"):
        await _emit(ev, "settings.update", {"setting": "statusPrivacy", "value": action["statusPrivacy"]})
    elif action.get("lockChatAction"):
        await _emit(ev, "chats.lock", {"id": item_id, "locked": bool(action["lockChatAction"].get("locked"))})
    elif action.get("privacySettingDisableLinkPreviewsAction"):
        await _emit(ev, "settings.update", {"setting": "disableLinkPreviews", "value": action["privacySettingDisableLinkPreviewsAction"]})
    elif action.get("notificationActivitySettingAction", {}).get("notificationActivitySetting"):
        await _emit(ev, "settings.update", {"setting": "notificationActivitySetting", "value": action["notificationActivitySettingAction"]["notificationActivitySetting"]})
    elif action.get("lidContactAction"):
        contact = action["lidContactAction"]
        await _emit(ev, "contacts.upsert", [{"id": item_id, "name": contact.get("fullName") or contact.get("firstName") or contact.get("username"), "lid": item_id, "phoneNumber": None}])
    elif action.get("privacySettingChannelsPersonalisedRecommendationAction"):
        await _emit(ev, "settings.update", {"setting": "channelsPersonalisedRecommendation", "value": action["privacySettingChannelsPersonalisedRecommendationAction"]})
    else:
        if logger:
            logger.debug("unprocessable update", extra={"syncAction": sync_action, "id": item_id})


# camelCase aliases
newLTHashState = new_lthash_state
encodeSyncdPatch = encode_syncd_patch
decodeSyncdMutations = decode_syncd_mutations
decodeSyncdPatch = decode_syncd_patch
extractSyncdPatches = extract_syncd_patches
downloadExternalBlob = download_external_blob
downloadExternalPatch = download_external_patch
decodeSyncdSnapshot = decode_syncd_snapshot
decodePatches = decode_patches
chatModificationToAppPatch = chat_modification_to_app_patch
processSyncAction = process_sync_action


__all__ = [
    "ChatMutationMap",
    "mutation_keys",
    "generate_mac",
    "to_64_bit_network_order",
    "make_lt_hash_generator",
    "generate_snapshot_mac",
    "generate_patch_mac",
    "new_lthash_state",
    "encode_syncd_patch",
    "decode_syncd_mutations",
    "decode_syncd_patch",
    "extract_syncd_patches",
    "download_external_blob",
    "download_external_patch",
    "decode_syncd_snapshot",
    "decode_patches",
    "chat_modification_to_app_patch",
    "process_sync_action",
    "newLTHashState",
    "encodeSyncdPatch",
    "decodeSyncdMutations",
    "decodeSyncdPatch",
    "extractSyncdPatches",
    "downloadExternalBlob",
    "downloadExternalPatch",
    "decodeSyncdSnapshot",
    "decodePatches",
    "chatModificationToAppPatch",
    "processSyncAction",
]
