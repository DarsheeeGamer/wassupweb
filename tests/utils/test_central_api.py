from __future__ import annotations

from wassupweb.api import Client, create_client, new_client
from wassupweb.central import WassupWeb, make_wa_socket, make_wa_socket_from_dict
from wassupweb.socket.communities import CommunitiesSocket
from wassupweb.types.socket import SocketConfig
from wassupweb.utils.logger import get_logger


def test_central_make_wa_socket_returns_full_stack_for_dict_and_model() -> None:
    from_dict = make_wa_socket({"country_code": "US"})
    from_model = make_wa_socket(SocketConfig())
    from_dict_fn = make_wa_socket_from_dict({"country_code": "US"})

    assert isinstance(from_dict, CommunitiesSocket)
    assert isinstance(from_model, CommunitiesSocket)
    assert isinstance(from_dict_fn, CommunitiesSocket)
    assert hasattr(from_dict.ev, "buffer")


def test_api_client_aliases_resolve_to_full_stack_socket() -> None:
    sock_a = create_client()
    sock_b = new_client(SocketConfig())

    assert isinstance(sock_a, CommunitiesSocket)
    assert isinstance(sock_b, CommunitiesSocket)
    assert isinstance(sock_a, Client)
    assert isinstance(sock_b, Client)


def test_defaults_include_logger_and_socket_config_default_sync_filter() -> None:
    facade = WassupWeb()
    sock = facade.make_socket()
    assert sock.config.logger is not None
    assert sock.config.should_sync_history_message({"syncType": "FULL"}) is False
    assert sock.config.should_sync_history_message({"syncType": 2}) is False
    assert sock.config.should_sync_history_message({"syncType": "RECENT"}) is True


def test_custom_logger_flows_into_default_event_buffer() -> None:
    cfg_logger = get_logger("wassupweb.tests").child({"suite": "central-api"})
    sock = make_wa_socket(SocketConfig(logger=cfg_logger))

    ev_logger = getattr(sock.ev, "_logger", None)
    assert ev_logger is not None
    assert ev_logger.extra.get("suite") == "central-api"
    assert ev_logger.extra.get("class") == "wassupweb.event-buffer"
