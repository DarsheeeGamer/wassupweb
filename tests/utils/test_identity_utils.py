from __future__ import annotations

from wassupweb.types.identity import JidKind, SendMessageInput, SendTextInput, UserRef
from wassupweb.utils.identity import IdentityResolver, resolve_message_identity


def test_resolve_message_identity_maps_remote_and_participant() -> None:
    resolver = IdentityResolver()
    message = {
        "key": {
            "remoteJid": "12345@g.us",
            "participant": "5511999999999@s.whatsapp.net",
            "id": "m1",
            "fromMe": False,
        }
    }

    mapped = resolve_message_identity(message, resolver)
    assert mapped.remote_user_id == "group:12345"
    assert mapped.participant_user_id == "pn:5511999999999"
    assert mapped.sender_user_id == "pn:5511999999999"
    assert mapped.remote and mapped.remote.kind == JidKind.GROUP
    assert mapped.participant and mapped.participant.kind == JidKind.PN


def test_identity_resolver_linking_keeps_single_user_id_and_prefer_lid() -> None:
    resolver = IdentityResolver()
    pn = resolver.resolve("5511888888888@s.whatsapp.net").ref
    lid = resolver.resolve("99112233@lid").ref

    merged = resolver.link_pn_lid(pn.jid or "", lid.jid or "").ref

    assert merged.user_id == pn.user_id
    assert merged.pn_jid == pn.jid
    assert merged.lid_jid == lid.jid
    assert resolver.as_chat_jid(merged.user_id, prefer=JidKind.LID) == lid.jid
    assert resolver.as_chat_jid(merged.user_id, prefer=JidKind.PN) == pn.jid


def test_send_input_models_accept_structured_user_refs() -> None:
    msg = SendMessageInput.model_validate(
        {
            "to": {"userId": "pn:5511888", "pnJid": "5511888@s.whatsapp.net"},
            "content": {"text": "hello"},
        }
    )
    assert isinstance(msg.to, UserRef)
    assert msg.to.user_id == "pn:5511888"
    assert msg.to.pn_jid == "5511888@s.whatsapp.net"

    txt = SendTextInput.model_validate(
        {
            "to": {"userId": "lid:001", "lidJid": "001@lid"},
            "text": "hi",
            "prefer": "lid",
        }
    )
    assert isinstance(txt.to, UserRef)
    assert txt.to.user_id == "lid:001"
    assert txt.to.lid_jid == "001@lid"
    assert txt.prefer == JidKind.LID
