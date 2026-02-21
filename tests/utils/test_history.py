from __future__ import annotations

from wassupweb.utils.history import process_history_message


def test_process_history_message_extracts_phone_to_lid_mappings() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [],
        "phoneNumberToLidMappings": [
            {"lidJid": "11111111111111@lid", "pnJid": "1234567890123@s.whatsapp.net"},
            {"lidJid": "22222222222222@lid", "pnJid": "9876543210987@s.whatsapp.net"},
        ],
    }

    result = process_history_message(history_sync)
    assert result["lidPnMappings"] == [
        {"lid": "11111111111111@lid", "pn": "1234567890123@s.whatsapp.net"},
        {"lid": "22222222222222@lid", "pn": "9876543210987@s.whatsapp.net"},
    ]


def test_process_history_message_skips_invalid_mapping_rows() -> None:
    history_sync = {
        "syncType": "RECENT",
        "conversations": [],
        "phoneNumberToLidMappings": [
            {"lidJid": None, "pnJid": "1234567890123@s.whatsapp.net"},
            {"lidJid": "11111111111111@lid", "pnJid": None},
            {"lidJid": "22222222222222@lid", "pnJid": "9876543210987@s.whatsapp.net"},
        ],
    }
    result = process_history_message(history_sync)
    assert result["lidPnMappings"] == [{"lid": "22222222222222@lid", "pn": "9876543210987@s.whatsapp.net"}]


def test_process_history_message_extracts_mappings_even_for_other_sync_types() -> None:
    for sync_type in ("PUSH_NAME", "ON_DEMAND"):
        history_sync = {
            "syncType": sync_type,
            "conversations": [],
            "pushnames": [],
            "phoneNumberToLidMappings": [{"lidJid": "11111111111111@lid", "pnJid": "1234567890123@s.whatsapp.net"}],
        }
        result = process_history_message(history_sync)
        assert result["lidPnMappings"] == [{"lid": "11111111111111@lid", "pn": "1234567890123@s.whatsapp.net"}]


def test_process_history_message_extracts_mapping_from_lid_chat_receipt() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [
            {
                "id": "211071956705386@lid",
                "messages": [
                    {
                        "message": {
                            "key": {
                                "remoteJid": "211071956705386@lid",
                                "fromMe": True,
                                "id": "3EB052FF8D9D00646C9994",
                            },
                            "userReceipt": [{"userJid": "5518999991234@s.whatsapp.net"}],
                        }
                    }
                ],
            }
        ],
    }

    result = process_history_message(history_sync)
    assert {"lid": "211071956705386@lid", "pn": "5518999991234@s.whatsapp.net"} in result["lidPnMappings"]


def test_process_history_message_uses_pn_chat_id_as_contact_phone_number() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [
            {
                "id": "1234567890123@s.whatsapp.net",
                "name": "Test User",
                "lidJid": "11111111111111@lid",
                "pnJid": None,
            }
        ],
    }

    result = process_history_message(history_sync)
    assert result["contacts"][0] == {
        "id": "1234567890123@s.whatsapp.net",
        "name": "Test User",
        "lid": "11111111111111@lid",
        "phoneNumber": "1234567890123@s.whatsapp.net",
    }


def test_process_history_message_extracts_mapping_when_chat_is_lid_and_pn_exists() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [{"id": "11111111111111@lid", "pnJid": "1234567890123@s.whatsapp.net"}],
    }
    result = process_history_message(history_sync)
    assert {"lid": "11111111111111@lid", "pn": "1234567890123@s.whatsapp.net"} in result["lidPnMappings"]


def test_process_history_message_extracts_mapping_when_chat_is_pn_and_lid_exists() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [{"id": "1234567890123@s.whatsapp.net", "lidJid": "11111111111111@lid"}],
    }
    result = process_history_message(history_sync)
    assert {"lid": "11111111111111@lid", "pn": "1234567890123@s.whatsapp.net"} in result["lidPnMappings"]


def test_process_history_message_does_not_extract_mapping_for_group_chat() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [
            {
                "id": "123456789012345678@g.us",
                "lidJid": "11111111111111@lid",
                "pnJid": "1234567890123@s.whatsapp.net",
            }
        ],
    }
    result = process_history_message(history_sync)
    assert result["lidPnMappings"] == []


def test_process_history_message_combines_explicit_and_conversation_mappings() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "phoneNumberToLidMappings": [{"lidJid": "11111111111111@lid", "pnJid": "1111111111111@s.whatsapp.net"}],
        "conversations": [{"id": "22222222222222@lid", "pnJid": "2222222222222@s.whatsapp.net"}],
    }
    result = process_history_message(history_sync)
    assert len(result["lidPnMappings"]) == 2
    assert {"lid": "11111111111111@lid", "pn": "1111111111111@s.whatsapp.net"} in result["lidPnMappings"]
    assert {"lid": "22222222222222@lid", "pn": "2222222222222@s.whatsapp.net"} in result["lidPnMappings"]


def test_process_history_message_extracts_mapping_for_hosted_ids() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [{"id": "11111111111111@hosted.lid", "pnJid": "1234567890123@hosted"}],
    }
    result = process_history_message(history_sync)
    assert {"lid": "11111111111111@hosted.lid", "pn": "1234567890123@hosted"} in result["lidPnMappings"]


def test_process_history_message_prefers_pn_jid_over_user_receipt_for_lid_chat() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [
            {
                "id": "211071956705386@lid",
                "pnJid": "5518888881234@s.whatsapp.net",
                "messages": [
                    {
                        "message": {
                            "key": {"remoteJid": "211071956705386@lid", "fromMe": True, "id": "3EB052FF8D9D00646C9994"},
                            "userReceipt": [{"userJid": "5518999991234@s.whatsapp.net"}],
                        }
                    }
                ],
            }
        ],
    }
    result = process_history_message(history_sync)
    assert {"lid": "211071956705386@lid", "pn": "5518888881234@s.whatsapp.net"} in result["lidPnMappings"]
    assert {"lid": "211071956705386@lid", "pn": "5518999991234@s.whatsapp.net"} not in result["lidPnMappings"]


def test_process_history_message_does_not_extract_user_receipt_mapping_when_from_me_false() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [
            {
                "id": "211071956705386@lid",
                "messages": [
                    {
                        "message": {
                            "key": {"remoteJid": "211071956705386@lid", "fromMe": False, "id": "3EB052FF8D9D00646C9994"},
                            "userReceipt": [{"userJid": "5518999991234@s.whatsapp.net"}],
                        }
                    }
                ],
            }
        ],
    }
    result = process_history_message(history_sync)
    assert {"lid": "211071956705386@lid", "pn": "5518999991234@s.whatsapp.net"} not in result["lidPnMappings"]


def test_process_history_message_does_not_extract_lid_to_lid_mapping_from_receipt() -> None:
    history_sync = {
        "syncType": "INITIAL_BOOTSTRAP",
        "conversations": [
            {
                "id": "211071956705386@lid",
                "messages": [
                    {
                        "message": {
                            "key": {"remoteJid": "211071956705386@lid", "fromMe": True, "id": "3EB052FF8D9D00646C9994"},
                            "userReceipt": [{"userJid": "152230971891797@lid"}],
                        }
                    }
                ],
            }
        ],
    }
    result = process_history_message(history_sync)
    assert result["lidPnMappings"] == []
