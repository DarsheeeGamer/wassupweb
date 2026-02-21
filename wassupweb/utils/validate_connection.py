from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from ..waproto import proto
from ..wabinary import S_WHATSAPP_NET, get_binary_node_child, jid_decode
from ..wabinary.types import BinaryNode
from .crypto import Curve, hmac_sign
from .generics import encode_big_endian
from .signal import create_signal_identity

KEY_BUNDLE_TYPE = bytes([5])
WA_ADV_ACCOUNT_SIG_PREFIX = bytes([6, 0])
WA_ADV_DEVICE_SIG_PREFIX = bytes([6, 1])
WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX = bytes([6, 5])

_PLATFORM_TYPE_MAP = {
    "CHROME": "CHROME",
    "FIREFOX": "FIREFOX",
    "SAFARI": "SAFARI",
    "EDGE": "EDGE",
}

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _decode_encoded_bytes(value: Any) -> bytes:
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return b""
        prefers_hex = bool(len(text) % 2 == 0 and _HEX_RE.fullmatch(text))
        decoders = (
            (lambda s: bytes.fromhex(s), lambda: prefers_hex),
            (lambda s: base64.b64decode(s, validate=True), lambda: True),
        )
        for decoder, should_try in decoders:
            if not should_try():
                continue
            try:
                return decoder(text)
            except Exception:
                continue
        if not prefers_hex:
            # If base64 decode failed, still allow explicit hex-like fallback.
            try:
                return bytes.fromhex(text)
            except Exception:
                pass
        return text.encode("utf-8")
    if value is None:
        return b""
    return bytes(value)


def _decode_adv_secret_key(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return b""
        try:
            return base64.b64decode(text, validate=True)
        except Exception:
            try:
                return bytes.fromhex(text)
            except Exception:
                return text.encode("utf-8")
    return _decode_encoded_bytes(value)


def _get_user_agent(config: dict[str, Any]) -> dict[str, Any]:
    version = config.get("version", (2, 3000, 0))
    return {
        "appVersion": {"primary": version[0], "secondary": version[1], "tertiary": version[2]},
        "platform": "WEB",
        "releaseChannel": "RELEASE",
        "osVersion": "0.1",
        "device": "Desktop",
        "osBuildNumber": "0.1",
        "localeLanguageIso6391": "en",
        "mnc": "000",
        "mcc": "000",
        "localeCountryIso31661Alpha2": config.get("countryCode", "US"),
    }


def _get_web_info(config: dict[str, Any]) -> dict[str, Any]:
    browser = config.get("browser", ("Mac OS", "Chrome", ""))
    web_sub_platform = "WEB_BROWSER"
    if config.get("syncFullHistory") and browser[1] == "Desktop":
        if browser[0] == "Mac OS":
            web_sub_platform = "DARWIN"
        elif browser[0] == "Windows":
            web_sub_platform = "WIN32"
    return {"webSubPlatform": web_sub_platform}


def _get_client_payload(config: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "connectType": "WIFI_UNKNOWN",
        "connectReason": "USER_ACTIVATED",
        "userAgent": _get_user_agent(config),
        "webInfo": _get_web_info(config),
    }
    return payload


def generate_login_node(user_jid: str, config: dict[str, Any]) -> dict[str, Any]:
    decoded = jid_decode(user_jid) or {}
    return {
        **_get_client_payload(config),
        "passive": True,
        "pull": True,
        "username": int(decoded.get("user") or 0),
        "device": decoded.get("device"),
        "lidDbMigrated": False,
    }


def _get_platform_type(platform: str) -> str:
    return _PLATFORM_TYPE_MAP.get((platform or "").upper(), "CHROME")


def _encode_device_props(payload: dict[str, Any]) -> bytes | dict[str, Any]:
    getter = getattr(proto, "get", None)
    cls = getter("DeviceProps") if callable(getter) else getattr(proto, "DeviceProps", None)
    if cls is None:
        return payload
    try:
        from google.protobuf.json_format import ParseDict

        message = cls()
        ParseDict(payload, message, ignore_unknown_fields=True)
        return message.SerializeToString()
    except Exception:
        return payload


def generate_registration_node(signal_creds: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    registration_id = signal_creds["registrationId"]
    signed_pre_key = signal_creds["signedPreKey"]
    signed_identity_key = signal_creds["signedIdentityKey"]

    version = config.get("version", (2, 3000, 0))
    app_version_buf = hashlib.md5(".".join(str(v) for v in version).encode("utf-8")).digest()
    browser = config.get("browser", ("Mac OS", "Chrome", ""))

    companion = {
        "os": browser[0],
        "platformType": _get_platform_type(browser[1]),
        "requireFullSync": config.get("syncFullHistory", True),
        "historySyncConfig": {
            "storageQuotaMb": 10240,
            "inlineInitialPayloadInE2EeMsg": True,
            "supportCallLogHistory": False,
            "supportBotUserAgentChatHistory": True,
            "supportCagReactionsAndPolls": True,
            "supportBizHostedMsg": True,
            "supportRecentSyncChunkMessageCountTuning": True,
            "supportHostedGroupMsg": True,
            "supportFbidBotChatHistory": True,
            "supportMessageAssociation": True,
            "supportGroupHistory": False,
        },
        "version": {"primary": 10, "secondary": 15, "tertiary": 7},
    }

    register_payload = {
        **_get_client_payload(config),
        "passive": False,
        "pull": False,
        "devicePairingData": {
            "buildHash": app_version_buf,
            "deviceProps": _encode_device_props(companion),
            "eRegid": encode_big_endian(registration_id),
            "eKeytype": KEY_BUNDLE_TYPE,
            "eIdent": signed_identity_key["public"],
            "eSkeyId": encode_big_endian(signed_pre_key["keyId"], 3),
            "eSkeyVal": signed_pre_key["keyPair"]["public"],
            "eSkeySig": signed_pre_key["signature"],
        },
    }
    return register_payload


def configure_successful_pairing(
    stanza: BinaryNode,
    creds: dict[str, Any],
) -> dict[str, Any]:
    msg_id = stanza.attrs.get("id")
    pair_success_node = get_binary_node_child(stanza, "pair-success")
    device_identity_node = get_binary_node_child(pair_success_node, "device-identity") if pair_success_node else None
    platform_node = get_binary_node_child(pair_success_node, "platform") if pair_success_node else None
    device_node = get_binary_node_child(pair_success_node, "device") if pair_success_node else None
    business_node = get_binary_node_child(pair_success_node, "biz") if pair_success_node else None
    if not device_identity_node or not device_node:
        raise ValueError("Missing device-identity or device in pair success node")

    adv_secret_key = creds["advSecretKey"]
    signed_identity_key = creds["signedIdentityKey"]
    signal_identities = creds.get("signalIdentities") or []

    biz_name = business_node.attrs.get("name") if business_node else None
    jid = device_node.attrs.get("jid")
    lid = device_node.attrs.get("lid")
    if not jid or not lid:
        raise ValueError("pair success node missing jid/lid")

    # Python port expects parsed account details on attrs/content.
    details = device_identity_node.attrs.get("details")
    hmac_value = device_identity_node.attrs.get("hmac")
    account_type = device_identity_node.attrs.get("account_type")

    details_bytes = details.encode("utf-8") if isinstance(details, str) else _decode_encoded_bytes(device_identity_node.content)
    hmac_bytes = _decode_encoded_bytes(hmac_value)
    hmac_prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if account_type == "HOSTED" else b""
    adv_sign = hmac_sign(hmac_prefix + details_bytes, _decode_adv_secret_key(adv_secret_key))
    if hmac_bytes and hmac_bytes != adv_sign:
        raise ValueError("Invalid account signature")

    account_signature_key = device_identity_node.attrs.get("account_signature_key")
    account_signature = device_identity_node.attrs.get("account_signature")
    if account_signature_key and account_signature:
        key_bytes = _decode_encoded_bytes(account_signature_key)
        sig_bytes = _decode_encoded_bytes(account_signature)
        account_signature_prefix = WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX if device_identity_node.attrs.get("device_type") == "HOSTED" else WA_ADV_ACCOUNT_SIG_PREFIX
        account_msg = account_signature_prefix + details_bytes + bytes(signed_identity_key["public"])
        if not Curve.verify(key_bytes, account_msg, sig_bytes):
            raise ValueError("Failed to verify account signature")
        device_msg = WA_ADV_DEVICE_SIG_PREFIX + details_bytes + bytes(signed_identity_key["public"]) + key_bytes
        device_signature = Curve.sign(bytes(signed_identity_key["private"]), device_msg)
    else:
        key_bytes = bytes(signed_identity_key["public"])
        device_signature = b""

    identity = create_signal_identity(lid, key_bytes)
    account = {
        "details": details_bytes,
        "accountSignatureKey": key_bytes,
        "deviceSignature": device_signature,
    }
    account_enc = encode_signed_device_identity(account, False)

    reply = BinaryNode(
        tag="iq",
        attrs={"to": S_WHATSAPP_NET, "type": "result", "id": msg_id or ""},
        content=[
            BinaryNode(
                tag="pair-device-sign",
                attrs={},
                content=[
                    BinaryNode(
                        tag="device-identity",
                        attrs={"key-index": str(device_identity_node.attrs.get("key-index") or "0")},
                        content=account_enc,
                    )
                ],
            )
        ],
    )

    auth_update = {
        "account": account,
        "me": {"id": jid, "name": biz_name, "lid": lid},
        "signalIdentities": [*signal_identities, identity.model_dump(by_alias=True)],
        "platform": platform_node.attrs.get("name") if platform_node else None,
    }
    return {"creds": auth_update, "reply": reply}


def encode_signed_device_identity(account: dict[str, Any], include_signature_key: bool) -> bytes:
    working = dict(account)
    sig_key = working.get("accountSignatureKey")
    if not include_signature_key or not sig_key:
        working["accountSignatureKey"] = None
    # deterministic encoding for transport
    material = {
        "details": working.get("details").hex() if isinstance(working.get("details"), (bytes, bytearray)) else str(working.get("details")),
        "accountSignatureKey": working.get("accountSignatureKey").hex() if isinstance(working.get("accountSignatureKey"), (bytes, bytearray)) else None,
        "deviceSignature": working.get("deviceSignature").hex() if isinstance(working.get("deviceSignature"), (bytes, bytearray)) else None,
    }
    return str(material).encode("utf-8")


# camelCase aliases
generateLoginNode = generate_login_node
generateRegistrationNode = generate_registration_node
configureSuccessfulPairing = configure_successful_pairing
encodeSignedDeviceIdentity = encode_signed_device_identity
