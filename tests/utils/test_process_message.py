from __future__ import annotations

import os

from wassupweb.utils.crypto import aes_encrypt_gcm, hmac_sign
from wassupweb.utils.process_message import clean_message, decrypt_event_response, decrypt_poll_vote
from wassupweb.waproto import proto


def _base_message(key: dict[str, object], message: dict[str, object] | None = None) -> dict[str, object]:
    merged_key = {"remoteJid": "chat@s.whatsapp.net", "fromMe": False, "id": "ABC", **key}
    return {
        "key": merged_key,
        "message": message or {"conversation": "hello"},
        "messageTimestamp": 1675888000,
    }


def test_clean_message_normalizes_device_jids() -> None:
    msg = _base_message(
        {
            "remoteJid": "1234567890:15@s.whatsapp.net",
            "participant": "9876543210:5@s.whatsapp.net",
        }
    )
    clean_message(msg, "me@s.whatsapp.net", "me@lid")

    assert msg["key"]["remoteJid"] == "1234567890@s.whatsapp.net"
    assert msg["key"]["participant"] == "9876543210@s.whatsapp.net"


def test_clean_message_does_not_modify_group_jid() -> None:
    msg = _base_message({"remoteJid": "123456-7890@g.us"})
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    assert msg["key"]["remoteJid"] == "123456-7890@g.us"


def test_clean_message_normalizes_lid_with_device_component() -> None:
    msg = _base_message({"participant": "1234567890:12@lid"})
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    assert msg["key"]["participant"] == "1234567890@lid"


def test_clean_message_normalizes_hosted_jids_to_standard_forms() -> None:
    msg = _base_message({"remoteJid": "1234567890:99@hosted", "participant": "9876543210:99@hosted.lid"})
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    assert msg["key"]["remoteJid"] == "1234567890@s.whatsapp.net"
    assert msg["key"]["participant"] == "9876543210@lid"


def test_clean_message_reaction_perspective_for_other_user() -> None:
    msg = _base_message(
        {"fromMe": False, "participant": "other@s.whatsapp.net"},
        {
            "reactionMessage": {
                "key": {
                    "remoteJid": "chat@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG_THEY_SENT",
                    "participant": "other@s.whatsapp.net",
                },
                "text": "x",
            }
        },
    )
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    reaction_key = msg["message"]["reactionMessage"]["key"]
    assert reaction_key["fromMe"] is False


def test_clean_message_does_not_touch_reaction_when_message_is_from_me() -> None:
    msg = _base_message(
        {"fromMe": True},
        {
            "reactionMessage": {
                "key": {"remoteJid": "chat@s.whatsapp.net", "fromMe": True, "id": "MSG_I_SENT"},
                "text": "x",
            }
        },
    )
    original = dict(msg["message"]["reactionMessage"]["key"])
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    assert msg["message"]["reactionMessage"]["key"] == original


def test_clean_message_handles_missing_jids_without_crashing() -> None:
    msg = _base_message({"remoteJid": None, "participant": None})
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    assert msg["key"]["id"] == "ABC"


def test_clean_message_handles_empty_message_object_without_crashing() -> None:
    msg = _base_message({}, {})
    clean_message(msg, "me@s.whatsapp.net", "me@lid")
    assert isinstance(msg, dict)


def test_decrypt_event_response_roundtrip() -> None:
    creator = "creator@s.whatsapp.net"
    responder = "responder@s.whatsapp.net"
    msg_id = "MSG_EVENT_1"
    enc_key = b"K" * 32

    evt = proto.Message.EventResponseMessage(response=proto.Message.EventResponseMessage.GOING, timestampMs=1700000000123)
    plaintext = evt.SerializeToString()

    sign = b"".join([msg_id.encode(), creator.encode(), responder.encode(), b"Event Response", bytes([1])])
    key0 = hmac_sign(enc_key, bytes(32), "sha256")
    dec_key = hmac_sign(sign, key0, "sha256")
    aad = f"{msg_id}\u0000{responder}".encode()
    iv = os.urandom(12)
    enc_payload = aes_encrypt_gcm(plaintext, dec_key, iv, aad)

    out = decrypt_event_response(
        {"encPayload": enc_payload, "encIv": iv},
        event_creator_jid=creator,
        event_msg_id=msg_id,
        event_enc_key=enc_key,
        responder_jid=responder,
    )
    assert out["response"] == "GOING"
    assert out["timestampMs"] == "1700000000123"


def test_decrypt_poll_vote_roundtrip() -> None:
    creator = "creator@s.whatsapp.net"
    voter = "voter@s.whatsapp.net"
    msg_id = "MSG_POLL_1"
    enc_key = b"P" * 32

    vote = proto.Message.PollVoteMessage(selectedOptions=[b"option-1"])
    plaintext = vote.SerializeToString()

    sign = b"".join([msg_id.encode(), creator.encode(), voter.encode(), b"Poll Vote", bytes([1])])
    key0 = hmac_sign(enc_key, bytes(32), "sha256")
    dec_key = hmac_sign(sign, key0, "sha256")
    aad = f"{msg_id}\u0000{voter}".encode()
    iv = os.urandom(12)
    enc_payload = aes_encrypt_gcm(plaintext, dec_key, iv, aad)

    out = decrypt_poll_vote(
        {"encPayload": enc_payload, "encIv": iv},
        poll_creator_jid=creator,
        poll_msg_id=msg_id,
        poll_enc_key=enc_key,
        voter_jid=voter,
    )
    assert out["selectedOptions"] == ["b3B0aW9uLTE="]
