from __future__ import annotations

import pytest

from wassupweb.types.label_association import LabelAssociationType
from wassupweb.utils.chat_utils import process_sync_action


class _EventEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event: str, payload: object) -> None:
        self.events.append((event, payload))


class _Logger:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[object, object]] = []

    def debug(self, message: str, *, extra: object | None = None) -> None:
        self.debug_calls.append((message, extra))


def _sync_action(value: dict[str, object], index: list[str]) -> dict[str, object]:
    return {"syncAction": {"value": value}, "index": index}


@pytest.mark.asyncio
async def test_process_sync_action_mute_emits_chats_update() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({"muteAction": {"muted": True, "muteEndTimestamp": 1700000000}}, ["mute", "chat123@s.whatsapp.net"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    assert ev.events
    event, payload = ev.events[0]
    assert event == "chats.update"
    assert payload[0]["id"] == "chat123@s.whatsapp.net"
    assert payload[0]["muteEndTime"] == 1700000000


@pytest.mark.asyncio
async def test_process_sync_action_archive_type_fallback() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({}, ["archive", "chat@s.whatsapp.net"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    event, payload = ev.events[0]
    assert event == "chats.update"
    assert payload[0]["archived"] is True


@pytest.mark.asyncio
async def test_process_sync_action_mark_read_true_initial_sync_sets_null_unread() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({"markChatAsReadAction": {"read": True}}, ["markRead", "chat@s.whatsapp.net"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
        {"accountSettings": {"unarchiveChats": False}},
    )
    event, payload = ev.events[0]
    assert event == "chats.update"
    assert payload[0]["unreadCount"] is None


@pytest.mark.asyncio
async def test_process_sync_action_delete_message_for_me_emits_messages_delete() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({"deleteMessageForMeAction": {"deleteMedia": False}}, ["deleteMessageForMe", "chat@s.whatsapp.net", "msg456", "1"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    event, payload = ev.events[0]
    assert event == "messages.delete"
    assert payload == {"keys": [{"remoteJid": "chat@s.whatsapp.net", "id": "msg456", "fromMe": True}]}


@pytest.mark.asyncio
async def test_process_sync_action_contact_action_emits_contact_and_mapping() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({"contactAction": {"fullName": "John", "lidJid": "123@lid", "pnJid": None}}, ["contact", "5511999@s.whatsapp.net"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    assert ("contacts.upsert", [{"id": "5511999@s.whatsapp.net", "name": "John", "lid": "123@lid", "phoneNumber": "5511999@s.whatsapp.net"}]) in ev.events
    assert ("lid-mapping.update", {"lid": "123@lid", "pn": "5511999@s.whatsapp.net"}) in ev.events


@pytest.mark.asyncio
async def test_process_sync_action_push_name_setting_emits_creds_update() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({"pushNameSetting": {"name": "New"}}, ["pushName"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Old"},
    )
    assert ("creds.update", {"me": {"id": "me@s.whatsapp.net", "name": "New"}}) in ev.events


@pytest.mark.asyncio
async def test_process_sync_action_label_association_chat() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({"labelAssociationAction": {"labeled": True}}, [LabelAssociationType.Chat, "label123", "chat@s.whatsapp.net"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    assert (
        "labels.association",
        {
            "type": "add",
            "association": {"type": LabelAssociationType.Chat, "chatId": "chat@s.whatsapp.net", "labelId": "label123"},
        },
    ) in ev.events


@pytest.mark.asyncio
async def test_process_sync_action_unknown_logs_debug_without_emits() -> None:
    ev = _EventEmitter()
    logger = _Logger()
    action = _sync_action({"unknownAction": {}}, ["unknown", "id123"])
    await process_sync_action(action, ev, {"id": "me@s.whatsapp.net", "name": "Me"}, None, logger)
    assert ev.events == []
    assert logger.debug_calls


@pytest.mark.asyncio
async def test_process_sync_action_unarchive_setting_updates_creds_and_initial_settings() -> None:
    ev = _EventEmitter()
    initial = {"accountSettings": {"unarchiveChats": False}}
    await process_sync_action(
        _sync_action({"unarchiveChatsSetting": {"unarchiveChats": True}}, ["setting", "me@s.whatsapp.net"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
        initial,
    )
    assert ("creds.update", {"accountSettings": {"unarchiveChats": True}}) in ev.events
    assert initial["accountSettings"]["unarchiveChats"] is True


@pytest.mark.asyncio
async def test_process_sync_action_star_type_fallback_reads_index_flag() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action({}, ["star", "chat@s.whatsapp.net", "msg1", "1", "1"]),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    assert (
        "messages.update",
        [{"key": {"remoteJid": "chat@s.whatsapp.net", "id": "msg1", "fromMe": True}, "update": {"starred": True}}],
    ) in ev.events


@pytest.mark.asyncio
async def test_process_sync_action_lid_contact_and_notification_activity_emit_updates() -> None:
    ev = _EventEmitter()
    await process_sync_action(
        _sync_action(
            {"lidContactAction": {"fullName": "Alice", "firstName": "A", "username": "alice"}},
            ["lid-contact", "alice@lid"],
        ),
        ev,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    assert (
        "contacts.upsert",
        [{"id": "alice@lid", "name": "Alice", "lid": "alice@lid", "phoneNumber": None}],
    ) in ev.events

    ev2 = _EventEmitter()
    await process_sync_action(
        _sync_action(
            {"notificationActivitySettingAction": {"notificationActivitySetting": {"enabled": True}}},
            ["setting", "me@s.whatsapp.net"],
        ),
        ev2,
        {"id": "me@s.whatsapp.net", "name": "Me"},
    )
    assert (
        "settings.update",
        {"setting": "notificationActivitySetting", "value": {"enabled": True}},
    ) in ev2.events
