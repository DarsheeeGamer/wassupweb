from __future__ import annotations

import pytest

from wassupweb.utils.sync_action_utils import emit_sync_action_results, process_contact_action


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, bool], str]] = []

    def warn(self, payload: dict[str, bool], message: str) -> None:
        self.calls.append((payload, message))


def test_process_contact_action_emits_contacts_upsert_and_lid_mapping_for_pn() -> None:
    action = {"fullName": "John Doe", "lidJid": "123456789@lid", "pnJid": None}
    contact_id = "5511999999999@s.whatsapp.net"
    results = process_contact_action(action, contact_id)

    assert {
        "event": "contacts.upsert",
        "data": [
            {
                "id": "5511999999999@s.whatsapp.net",
                "name": "John Doe",
                "lid": "123456789@lid",
                "phoneNumber": "5511999999999@s.whatsapp.net",
            }
        ],
    } in results
    assert {
        "event": "lid-mapping.update",
        "data": {"lid": "123456789@lid", "pn": "5511999999999@s.whatsapp.net"},
    } in results


def test_process_contact_action_uses_pnjid_fallback_for_lid_id() -> None:
    action = {"fullName": "John Doe", "lidJid": None, "pnJid": "5511888888888@s.whatsapp.net"}
    results = process_contact_action(action, "123456789@lid")

    assert results[0]["data"][0]["phoneNumber"] == "5511888888888@s.whatsapp.net"


def test_process_contact_action_handles_undefined_name() -> None:
    action = {"fullName": None, "lidJid": "123456789@lid", "pnJid": None}
    results = process_contact_action(action, "5511999999999@s.whatsapp.net")
    assert results[0]["data"][0]["name"] is None


def test_process_contact_action_lid_mapping_handles_lid_with_device_suffix() -> None:
    action = {"fullName": "Contact", "lidJid": "173233882013816:99@lid", "pnJid": None}
    results = process_contact_action(action, "5511999999999@s.whatsapp.net")
    assert {"event": "lid-mapping.update", "data": {"lid": "173233882013816:99@lid", "pn": "5511999999999@s.whatsapp.net"}} in results


def test_process_contact_action_no_lid_mapping_when_lid_missing() -> None:
    action = {"fullName": "John Doe", "lidJid": None, "pnJid": None}
    results = process_contact_action(action, "5511999999999@s.whatsapp.net")
    assert next((item for item in results if item["event"] == "lid-mapping.update"), None) is None


def test_process_contact_action_no_lid_mapping_when_id_is_lid() -> None:
    action = {"fullName": "John Doe", "lidJid": "123456789@lid", "pnJid": None}
    results = process_contact_action(action, "987654321@lid")
    assert next((item for item in results if item["event"] == "lid-mapping.update"), None) is None


def test_process_contact_action_no_lid_mapping_when_lid_is_invalid_format() -> None:
    action = {"fullName": "John Doe", "lidJid": "invalid-lid-format", "pnJid": None}
    results = process_contact_action(action, "5511999999999@s.whatsapp.net")
    assert next((item for item in results if item["event"] == "lid-mapping.update"), None) is None


def test_process_contact_action_no_lid_mapping_for_group_jids() -> None:
    action = {"fullName": "Group", "lidJid": "123456789@lid", "pnJid": None}
    results = process_contact_action(action, "123456789012345678@g.us")
    assert next((item for item in results if item["event"] == "lid-mapping.update"), None) is None


def test_process_contact_action_missing_id_logs_and_returns_empty() -> None:
    logger = _Logger()
    action = {"fullName": "John Doe", "lidJid": "123456789@lid", "pnJid": None}

    results = process_contact_action(action, None, logger)
    assert results == []
    assert logger.calls == [
        (
            {"hasFullName": True, "hasLidJid": True, "hasPnJid": False},
            "contactAction sync: missing id in index",
        )
    ]


def test_process_contact_action_empty_id_returns_empty() -> None:
    logger = _Logger()
    action = {"fullName": "John Doe", "lidJid": "123456789@lid", "pnJid": None}
    assert process_contact_action(action, "", logger) == []


def test_process_contact_action_prefers_id_over_pnjid_when_id_is_pn() -> None:
    action = {"fullName": "Test", "lidJid": "111222333@lid", "pnJid": "1111111111@s.whatsapp.net"}
    results = process_contact_action(action, "9999999999@s.whatsapp.net")
    upsert = next(item for item in results if item["event"] == "contacts.upsert")
    mapping = next(item for item in results if item["event"] == "lid-mapping.update")
    assert upsert["data"][0]["phoneNumber"] == "9999999999@s.whatsapp.net"
    assert mapping["data"]["pn"] == "9999999999@s.whatsapp.net"


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event: str, payload: object) -> None:
        self.events.append((event, payload))


@pytest.mark.asyncio
async def test_emit_sync_action_results_emits_all_events() -> None:
    ev = _Emitter()
    results = process_contact_action(
        {"fullName": "John Doe", "lidJid": "123456789@lid", "pnJid": None},
        "5511999999999@s.whatsapp.net",
    )
    await emit_sync_action_results(ev, results)
    assert ("contacts.upsert", results[0]["data"]) in ev.events
    assert ("lid-mapping.update", results[1]["data"]) in ev.events
