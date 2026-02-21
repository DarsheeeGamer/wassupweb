from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from wassupweb.socket.mex import WMexQueryError, execute_wmex_query
from wassupweb.socket.newsletter import NewsletterSocket
from wassupweb.wabinary import BinaryNode


def _mex_response(payload: dict[str, Any]) -> BinaryNode:
    return BinaryNode(
        tag="iq",
        attrs={"id": "1", "type": "result"},
        content=[BinaryNode(tag="result", attrs={}, content=json.dumps(payload).encode("utf-8"))],
    )


@pytest.mark.asyncio
async def test_execute_wmex_query_returns_direct_data_path() -> None:
    async def _query(_node: BinaryNode) -> BinaryNode:
        return _mex_response({"data": {"xwa2_newsletter_create": {"id": "abc"}}})

    result = await execute_wmex_query(
        variables={},
        query_id="qid",
        data_path="xwa2_newsletter_create",
        query=_query,
        generate_message_tag=lambda: "msg-1",
    )

    assert result == {"id": "abc"}


@pytest.mark.asyncio
async def test_execute_wmex_query_raises_structured_error() -> None:
    async def _query(_node: BinaryNode) -> BinaryNode:
        return _mex_response(
            {
                "errors": [
                    {"message": "nope", "extensions": {"error_code": 403}},
                ]
            }
        )

    with pytest.raises(WMexQueryError) as exc:
        await execute_wmex_query(
            variables={},
            query_id="qid",
            data_path="xwa2_newsletter_create",
            query=_query,
            generate_message_tag=lambda: "msg-1",
        )

    err = exc.value
    assert err.status_code == 403
    assert "GraphQL server error: nope" in str(err)
    assert isinstance(err.data, dict)


@pytest.mark.asyncio
async def test_newsletter_update_picture_uses_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _mock_generate_profile_picture(_content: Any) -> dict[str, bytes]:
        return {"img": b"\x00\xff\x10"}

    from wassupweb.socket import newsletter as newsletter_mod

    monkeypatch.setattr(newsletter_mod, "generate_profile_picture", _mock_generate_profile_picture)

    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def newsletter_update(self, jid: str, updates: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((jid, updates))
            return updates

    obj = _Harness()
    result = await NewsletterSocket.newsletter_update_picture(obj, "120363@s.whatsapp.net", content=b"x")

    expected = base64.b64encode(b"\x00\xff\x10").decode("ascii")
    assert obj.calls == [("120363@s.whatsapp.net", {"picture": expected})]
    assert result == {"picture": expected}


@pytest.mark.asyncio
async def test_newsletter_typed_create_update_follow_wrappers() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def newsletter_create(self, name: str, description: str | None = None) -> dict[str, Any]:
            self.calls.append(("create", (name, description)))
            return {"id": "n@newsletter"}

        async def newsletter_update(self, jid: str, updates: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(("update", (jid, updates)))
            return updates

        async def newsletter_follow(self, jid: str) -> str:
            self.calls.append(("follow", jid))
            return "ok"

        async def newsletter_unfollow(self, jid: str) -> str:
            self.calls.append(("unfollow", jid))
            return "ok"

    obj = _Harness()
    created = await NewsletterSocket.create_newsletter(obj, {"name": "N", "description": "D"})  # type: ignore[arg-type]
    updated = await NewsletterSocket.update_newsletter(  # type: ignore[arg-type]
        obj,
        {"jid": "n@newsletter", "updates": {"name": "Renamed"}},
    )
    followed = await NewsletterSocket.follow_newsletter(obj, {"jid": "n@newsletter"})  # type: ignore[arg-type]
    unfollowed = await NewsletterSocket.unfollow_newsletter(obj, {"jid": "n@newsletter"})  # type: ignore[arg-type]

    assert created == {"id": "n@newsletter"}
    assert updated == {"name": "Renamed"}
    assert followed == "ok"
    assert unfollowed == "ok"
    assert ("create", ("N", "D")) in obj.calls
    assert ("update", ("n@newsletter", {"name": "Renamed"})) in obj.calls


@pytest.mark.asyncio
async def test_newsletter_typed_react_fetch_and_admin_wrappers() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def newsletter_react_message(self, jid: str, server_id: str, reaction: str | None = None) -> None:
            self.calls.append(("react", (jid, server_id, reaction)))

        async def newsletter_fetch_messages(
            self, jid: str, count: int, since: int | None = None, after: int | None = None
        ) -> BinaryNode:
            self.calls.append(("fetch", (jid, count, since, after)))
            return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

        async def subscribe_newsletter_updates(self, jid: str) -> dict[str, str]:
            self.calls.append(("sub_updates", jid))
            return {"duration": "300"}

        async def newsletter_admin_count(self, jid: str) -> int:
            self.calls.append(("admin_count", jid))
            return 2

        async def newsletter_change_owner(self, jid: str, new_owner_jid: str) -> str:
            self.calls.append(("change_owner", (jid, new_owner_jid)))
            return "ok"

        async def newsletter_demote(self, jid: str, user_jid: str) -> str:
            self.calls.append(("demote", (jid, user_jid)))
            return "ok"

        async def newsletter_delete(self, jid: str) -> str:
            self.calls.append(("delete", jid))
            return "ok"

    obj = _Harness()
    await NewsletterSocket.react_to_newsletter_message(  # type: ignore[arg-type]
        obj, {"jid": "n@newsletter", "serverId": "77", "reaction": "👍"}
    )
    fetched = await NewsletterSocket.fetch_newsletter_messages(  # type: ignore[arg-type]
        obj, {"jid": "n@newsletter", "count": 10, "since": 1, "after": 2}
    )
    sub = await NewsletterSocket.subscribe_to_newsletter_updates(obj, {"jid": "n@newsletter"})  # type: ignore[arg-type]
    admins = await NewsletterSocket.get_newsletter_admin_count(obj, {"jid": "n@newsletter"})  # type: ignore[arg-type]
    changed = await NewsletterSocket.change_newsletter_owner(  # type: ignore[arg-type]
        obj, {"jid": "n@newsletter", "newOwnerJid": "owner@s.whatsapp.net"}
    )
    demoted = await NewsletterSocket.demote_newsletter_admin(  # type: ignore[arg-type]
        obj, {"jid": "n@newsletter", "userJid": "user@s.whatsapp.net"}
    )
    deleted = await NewsletterSocket.delete_newsletter(obj, {"jid": "n@newsletter"})  # type: ignore[arg-type]

    assert isinstance(fetched, BinaryNode)
    assert sub == {"duration": "300"}
    assert admins == 2
    assert changed == "ok"
    assert demoted == "ok"
    assert deleted == "ok"
    assert ("react", ("n@newsletter", "77", "👍")) in obj.calls
    assert ("change_owner", ("n@newsletter", "owner@s.whatsapp.net")) in obj.calls
    assert ("demote", ("n@newsletter", "user@s.whatsapp.net")) in obj.calls
