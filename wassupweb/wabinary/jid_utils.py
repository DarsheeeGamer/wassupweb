from __future__ import annotations

from enum import IntEnum
from typing import TypedDict

S_WHATSAPP_NET = "s.whatsapp.net"
OFFICIAL_BIZ_JID = "16505361212@c.us"
SERVER_JID = "server@c.us"
PSA_WID = "0@c.us"
STORIES_JID = "status@broadcast"
META_AI_JID = "13135550002@c.us"


class WAJIDDomains(IntEnum):
    WHATSAPP = 0
    LID = 1
    HOSTED = 128
    HOSTED_LID = 129


class FullJid(TypedDict, total=False):
    user: str
    server: str
    device: int
    domainType: int


def get_server_from_domain_type(initial_server: str, domain_type: WAJIDDomains | None = None) -> str:
    if domain_type == WAJIDDomains.LID:
        return "lid"
    if domain_type == WAJIDDomains.HOSTED:
        return "hosted"
    if domain_type == WAJIDDomains.HOSTED_LID:
        return "hosted.lid"
    return initial_server


def jid_encode(user: str | int | None, server: str, device: int | None = None, agent: int | None = None) -> str:
    user_part = "" if user is None else str(user)
    agent_part = f"_{agent}" if agent else ""
    device_part = f":{device}" if device else ""
    return f"{user_part}{agent_part}{device_part}@{server}"


def jid_decode(jid: str | None) -> FullJid | None:
    if not isinstance(jid, str):
        return None
    sep_idx = jid.find("@")
    if sep_idx < 0:
        return None

    server = jid[sep_idx + 1 :]
    user_combined = jid[:sep_idx]
    user_agent, *device_part = user_combined.split(":")
    user, *agent_part = user_agent.split("_")
    agent = agent_part[0] if agent_part else None
    device = int(device_part[0]) if device_part and device_part[0] else None

    domain_type = WAJIDDomains.WHATSAPP
    if server == "lid":
        domain_type = WAJIDDomains.LID
    elif server == "hosted":
        domain_type = WAJIDDomains.HOSTED
    elif server == "hosted.lid":
        domain_type = WAJIDDomains.HOSTED_LID
    elif agent:
        domain_type = WAJIDDomains(int(agent))

    return FullJid(server=server, user=user, domainType=int(domain_type), device=device)


def are_jids_same_user(jid1: str | None, jid2: str | None) -> bool:
    left = jid_decode(jid1)
    right = jid_decode(jid2)
    return bool(left and right and left.get("user") == right.get("user"))


def is_jid_meta_ai(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@bot"))


def is_pn_user(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@s.whatsapp.net"))


def is_lid_user(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@lid"))


def is_jid_broadcast(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@broadcast"))


def is_jid_group(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@g.us"))


def is_jid_status_broadcast(jid: str) -> bool:
    return jid == "status@broadcast"


def is_jid_newsletter(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@newsletter"))


def is_hosted_pn_user(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@hosted"))


def is_hosted_lid_user(jid: str | None) -> bool:
    return bool(jid and jid.endswith("@hosted.lid"))


def is_jid_bot(jid: str | None) -> bool:
    if not jid:
        return False
    user = jid.split("@", 1)[0]
    if not jid.endswith("@c.us"):
        return False
    return (len(user) == 11 and user.startswith("1313555")) or (len(user) == 11 and user.startswith("131655500"))


def jid_normalized_user(jid: str | None) -> str:
    result = jid_decode(jid)
    if not result:
        return ""
    user = result["user"]
    server = result["server"]
    return jid_encode(user, "s.whatsapp.net" if server == "c.us" else server)


def transfer_device(from_jid: str, to_jid: str) -> str:
    from_decoded = jid_decode(from_jid) or {}
    to_decoded = jid_decode(to_jid)
    if not to_decoded:
        raise ValueError(f"invalid JID: {to_jid}")
    device_id = from_decoded.get("device", 0) or 0
    return jid_encode(to_decoded["user"], to_decoded["server"], device_id)
