from __future__ import annotations

from wassupweb.types.business import BusinessOrderDetailsInput, BusinessProductDeleteInput
from wassupweb.types.chat import ChatLabelInput, MarkReadInput
from wassupweb.types.community import CommunityCreateGroupInput
from wassupweb.types.contact import Contact
from wassupweb.types.events import MessageMediaUpdate, MessagingHistorySet
from wassupweb.types.group_metadata import GroupMetadata, GroupToggleEphemeralInput
from wassupweb.types.label import LabelActionBody
from wassupweb.types.label_association import MessageLabelAssociation
from wassupweb.types.message import WAMessage, WAMessageKey, WAMessageStubType
from wassupweb.types.newsletter import NewsletterChangeOwnerInput, NewsletterReactInput
from wassupweb.types.product import ProductUpdate
from wassupweb.types.signal import DecryptSignalProtoOpts, EncryptGroupMessageOpts
from wassupweb.types.socket import SocketConfig
from wassupweb.types.state import ConnectionState


def test_business_and_chat_alias_fields_validate() -> None:
    order = BusinessOrderDetailsInput.model_validate({"orderId": "ord-1", "tokenBase64": "abc"})
    deletion = BusinessProductDeleteInput.model_validate({"productIds": ["p1", "p2"]})
    mark = MarkReadInput.model_validate({"jid": "1@s.whatsapp.net", "messageIds": ["m1"]})
    label = ChatLabelInput.model_validate({"jid": "1@s.whatsapp.net", "labelId": "L1"})
    assert order.order_id == "ord-1"
    assert deletion.product_ids == ["p1", "p2"]
    assert mark.message_ids == ["m1"]
    assert label.label_id == "L1"


def test_contact_group_and_community_models_validate() -> None:
    contact = Contact.model_validate({"id": "1@s.whatsapp.net", "phoneNumber": "123", "verifiedName": "A"})
    group = GroupMetadata.model_validate({"id": "g@g.us", "subject": "Group", "addressingMode": "pn"})
    eph = GroupToggleEphemeralInput.model_validate({"jid": "g@g.us", "ephemeralExpiration": 3600})
    community = CommunityCreateGroupInput.model_validate(
        {"subject": "Sub", "participants": ["1@s.whatsapp.net"], "parentCommunityJid": "c@g.us"}
    )
    assert contact.phone_number == "123"
    assert group.addressing_mode == "pn"
    assert eph.ephemeral_expiration == 3600
    assert community.parent_community_jid == "c@g.us"


def test_message_and_event_models_validate() -> None:
    message = WAMessage.model_validate(
        {
            "key": {"remoteJid": "1@s.whatsapp.net", "fromMe": False, "id": "m1"},
            "messageStubType": int(WAMessageStubType.CIPHERTEXT),
            "messageStubParameters": ["missing keys"],
        }
    )
    media = MessageMediaUpdate.model_validate(
        {
            "key": {"remoteJid": "1@s.whatsapp.net", "id": "m1"},
            "media": {"ciphertext": b"a", "iv": b"b"},
            "statusCode": 404,
        }
    )
    history = MessagingHistorySet.model_validate({"chats": [], "contacts": [], "messages": [], "isLatest": True})
    assert message.message_stub_type == int(WAMessageStubType.CIPHERTEXT)
    assert media.status_code == 404
    assert history.is_latest is True


def test_label_newsletter_product_models_validate() -> None:
    action = LabelActionBody.model_validate({"id": "l1", "predefinedId": 2})
    association = MessageLabelAssociation.model_validate(
        {"type": "label_message", "chatId": "1@s.whatsapp.net", "messageId": "m1", "labelId": "l1"}
    )
    react = NewsletterReactInput.model_validate({"jid": "n@newsletter", "serverId": "9", "reaction": "👍"})
    owner = NewsletterChangeOwnerInput.model_validate({"jid": "n@newsletter", "newOwnerJid": "2@s.whatsapp.net"})
    product = ProductUpdate.model_validate(
        {
            "name": "P",
            "retailerId": "r1",
            "description": "D",
            "price": 10,
            "currency": "USD",
            "images": [{"url": "https://x"}],
        }
    )
    assert action.predefined_id == 2
    assert association.message_id == "m1"
    assert react.server_id == "9"
    assert owner.new_owner_jid == "2@s.whatsapp.net"
    assert product.retailer_id == "r1"


def test_signal_socket_state_models_validate() -> None:
    decrypt_opts = DecryptSignalProtoOpts.model_validate({"jid": "1@s.whatsapp.net", "type": "msg", "ciphertext": b"x"})
    encrypt_group = EncryptGroupMessageOpts.model_validate({"group": "g@g.us", "data": b"a", "meId": "1@s.whatsapp.net"})
    config = SocketConfig.model_validate({"printQRInTerminal": True, "msgRetryCounterCache": {}, "mediaCache": {}})
    state = ConnectionState.model_validate({"connection": "connecting", "isOnline": True})
    key = WAMessageKey.model_validate({"remoteJid": "1@s.whatsapp.net", "id": "m1"})
    msg = WAMessage.model_validate({"key": key.model_dump(by_alias=True)})
    assert decrypt_opts.type == "msg"
    assert encrypt_group.me_id == "1@s.whatsapp.net"
    assert config.print_qr_in_terminal is True
    assert state.is_online is True
    assert msg.key.id == "m1"

