from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from wassupweb.wausync.protocols import ContactProtocol, LIDProtocol
from wassupweb.wausync.query import USyncQuery
from wassupweb.wausync.user import USyncUser
from wassupweb.wabinary import BinaryNode


def _make_result_node() -> BinaryNode:
    return BinaryNode(
        tag="iq",
        attrs={"type": "result"},
        content=[
            BinaryNode(
                tag="usync",
                attrs={},
                content=[
                    BinaryNode(
                        tag="list",
                        attrs={},
                        content=[
                            BinaryNode(
                                tag="user",
                                attrs={"jid": "111@s.whatsapp.net"},
                                content=[BinaryNode(tag="contact", attrs={}, content=b"true"), BinaryNode(tag="unknown", attrs={})],
                            )
                        ],
                    ),
                    BinaryNode(
                        tag="side_list",
                        attrs={},
                        content=[
                            BinaryNode(
                                tag="user",
                                attrs={"jid": "222@s.whatsapp.net"},
                                content=[BinaryNode(tag="contact", attrs={}, content=b"true")],
                            )
                        ],
                    ),
                ],
            )
        ],
    )


def test_parse_usync_query_result_parses_list_and_side_list() -> None:
    query = USyncQuery()
    query.protocols = [SimpleNamespace(name="contact", parser=lambda _node: {"ok": True})]

    parsed = query.parse_usync_query_result(_make_result_node())

    assert parsed is not None
    assert [item.id for item in parsed.list] == ["111@s.whatsapp.net"]
    assert parsed.list[0].data == {"contact": {"ok": True}}
    assert [item.id for item in parsed.side_list] == ["222@s.whatsapp.net"]
    assert parsed.side_list[0].data == {"contact": {"ok": True}}


def test_usync_query_and_user_support_camel_case_aliases() -> None:
    user = USyncUser().withId("111@s.whatsapp.net").withPhone("+111").withType("in")
    assert user.id == "111@s.whatsapp.net"
    assert user.phone == "+111"
    assert user.type == "in"

    query = USyncQuery().withMode("query").withContext("background").withUser(user).withContactProtocol()
    assert query.mode == "query"
    assert query.context == "background"
    assert query.users[0].id == "111@s.whatsapp.net"
    assert query.protocols and query.protocols[0].name == "contact"

    query.protocols = [SimpleNamespace(name="contact", parser=lambda _node: True)]
    parsed = query.parseUSyncQueryResult(_make_result_node())
    assert parsed is not None
    assert parsed.list[0].data == {"contact": True}


def test_usync_protocols_expose_camel_case_element_aliases() -> None:
    user = USyncUser().withId("111@s.whatsapp.net").withLid("111@lid").withPhone("+111")

    contact = ContactProtocol()
    assert contact.getQueryElement().tag == "contact"
    assert contact.getUserElement(user).tag == "contact"

    lid = LIDProtocol()
    assert lid.getQueryElement().tag == "lid"
    lid_user_element = lid.getUserElement(user)
    assert lid_user_element is not None
    assert lid_user_element.attrs["jid"] == "111@lid"
