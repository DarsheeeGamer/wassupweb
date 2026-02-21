from __future__ import annotations

from typing import Any

import pytest

from wassupweb.utils.signal import parse_and_inject_e2e_sessions
from wassupweb.wabinary import BinaryNode


class _Repo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def inject_e2e_session(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)


def _create_user_node(jid: str) -> BinaryNode:
    return BinaryNode(
        tag="user",
        attrs={"jid": jid},
        content=[
            BinaryNode(
                tag="skey",
                attrs={},
                content=[
                    BinaryNode(tag="id", attrs={}, content=bytes([0, 0, 1])),
                    BinaryNode(tag="value", attrs={}, content=bytes([1] * 33)),
                    BinaryNode(tag="signature", attrs={}, content=bytes([2] * 64)),
                ],
            ),
            BinaryNode(
                tag="key",
                attrs={},
                content=[
                    BinaryNode(tag="id", attrs={}, content=bytes([0, 0, 2])),
                    BinaryNode(tag="value", attrs={}, content=bytes([3] * 33)),
                ],
            ),
            BinaryNode(tag="identity", attrs={}, content=bytes([4] * 32)),
            BinaryNode(tag="registration", attrs={}, content=bytes([0, 0, 0, 7])),
        ],
    )


@pytest.mark.asyncio
async def test_parse_and_inject_e2e_sessions_processes_all_users() -> None:
    repo = _Repo()
    node = BinaryNode(
        tag="iq",
        attrs={},
        content=[
            BinaryNode(
                tag="list",
                attrs={},
                content=[
                    _create_user_node("user1@s.whatsapp.net"),
                    _create_user_node("user2@s.whatsapp.net"),
                    _create_user_node("user3@s.whatsapp.net"),
                ],
            )
        ],
    )

    await parse_and_inject_e2e_sessions(node, repo)

    assert len(repo.calls) == 3
    assert repo.calls[0]["jid"] == "user1@s.whatsapp.net"
    assert repo.calls[1]["jid"] == "user2@s.whatsapp.net"
    assert repo.calls[2]["jid"] == "user3@s.whatsapp.net"


@pytest.mark.asyncio
async def test_parse_and_inject_e2e_sessions_skips_users_with_missing_jid_or_registration() -> None:
    repo = _Repo()
    valid_user = _create_user_node("ok@s.whatsapp.net")
    missing_jid = _create_user_node("ignored@s.whatsapp.net")
    missing_jid.attrs.pop("jid", None)
    missing_registration = _create_user_node("ignored2@s.whatsapp.net")
    missing_registration.content = [item for item in (missing_registration.content or []) if item.tag != "registration"]

    node = BinaryNode(
        tag="iq",
        attrs={},
        content=[
            BinaryNode(
                tag="list",
                attrs={},
                content=[valid_user, missing_jid, missing_registration],
            )
        ],
    )

    await parse_and_inject_e2e_sessions(node, repo)

    assert len(repo.calls) == 1
    assert repo.calls[0]["jid"] == "ok@s.whatsapp.net"
