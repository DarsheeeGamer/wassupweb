from __future__ import annotations

from types import MethodType

import pytest

from wassupweb.socket.chats import ChatsSocket
from wassupweb.wausync import USyncQueryResult, USyncQueryResultItem


class _Logger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


class _Dummy:
    def __init__(self) -> None:
        self._logger = _Logger()

    def resolve_chat_jid(self, jid: str) -> str:
        return jid


@pytest.mark.asyncio
async def test_on_whatsapp_returns_contact_entries() -> None:
    obj = _Dummy()

    async def _fake_execute(_self: object, _query: object) -> USyncQueryResult:
        return USyncQueryResult(
            list=[
                USyncQueryResultItem(id="111@s.whatsapp.net", data={"contact": True}),
                USyncQueryResultItem(id="222@s.whatsapp.net", data={"contact": False}),
            ]
        )

    obj.execute_usync_query = MethodType(_fake_execute, obj)  # type: ignore[attr-defined]
    result = await ChatsSocket.on_whatsapp(obj, "111@s.whatsapp.net", "222@s.whatsapp.net")
    assert result == [{"jid": "111@s.whatsapp.net", "exists": True}]


@pytest.mark.asyncio
async def test_pn_from_lid_usync_extracts_mappings() -> None:
    obj = _Dummy()

    async def _fake_execute(_self: object, _query: object) -> USyncQueryResult:
        return USyncQueryResult(
            list=[
                USyncQueryResultItem(id="111@s.whatsapp.net", data={"lid": "111@lid"}),
                USyncQueryResultItem(id="222@s.whatsapp.net", data={"lid": None}),
            ]
        )

    obj.execute_usync_query = MethodType(_fake_execute, obj)  # type: ignore[attr-defined]
    result = await ChatsSocket.pn_from_lid_usync(obj, ["111@s.whatsapp.net", "222@s.whatsapp.net"])
    assert result == [{"pn": "111@s.whatsapp.net", "lid": "111@lid"}]
