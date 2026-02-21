from __future__ import annotations

from typing import Any

from ..abc import EventBusABC, IdentityResolverABC, SocketPluginABC, TransportABC
from ..types.socket import SocketConfig
from ..utils.event_buffer import make_event_buffer
from ..utils.logger import logger as default_logger
from .client import WASocketClient
from .communities import CommunitiesSocket
from .socket import CoreSocket
from .transport import WebSocketTransport


def make_core_socket(
    config: SocketConfig,
    *,
    transport: TransportABC | None = None,
    event_bus: EventBusABC | Any | None = None,
    plugins: list[SocketPluginABC] | None = None,
    identity_resolver: IdentityResolverABC | None = None,
) -> CoreSocket:
    resolved_transport = transport or WebSocketTransport(
        config.wa_websocket_url,
        open_timeout=config.connect_timeout_ms / 1000.0,
    )
    cfg_logger = getattr(config, "logger", None) or default_logger
    resolved_event_bus = event_bus or make_event_buffer(cfg_logger.child({"class": "wassupweb.event-buffer"}))
    return CoreSocket(
        config=config,
        transport=resolved_transport,
        ev=resolved_event_bus,
        plugins=plugins,
        identity_resolver=identity_resolver,
    )


def make_wa_socket(
    config: SocketConfig,
    *,
    transport: TransportABC | None = None,
    event_bus: EventBusABC | Any | None = None,
    plugins: list[SocketPluginABC] | None = None,
    identity_resolver: IdentityResolverABC | None = None,
) -> CommunitiesSocket:
    resolved_transport = transport or WebSocketTransport(
        config.wa_websocket_url,
        open_timeout=config.connect_timeout_ms / 1000.0,
    )
    cfg_logger = getattr(config, "logger", None) or default_logger
    resolved_event_bus = event_bus or make_event_buffer(cfg_logger.child({"class": "wassupweb.event-buffer"}))
    return CommunitiesSocket(
        config=config,
        transport=resolved_transport,
        ev=resolved_event_bus,
        plugins=plugins,
        identity_resolver=identity_resolver,
    )


def makeWASocket(
    config: SocketConfig,
    *,
    transport: TransportABC | None = None,
    event_bus: EventBusABC | Any | None = None,
    plugins: list[SocketPluginABC] | None = None,
    identity_resolver: IdentityResolverABC | None = None,
) -> CommunitiesSocket:
    return make_wa_socket(
        config=config,
        transport=transport,
        event_bus=event_bus,
        plugins=plugins,
        identity_resolver=identity_resolver,
    )


__all__ = [
    "CoreSocket",
    "CommunitiesSocket",
    "WASocketClient",
    "WebSocketTransport",
    "make_core_socket",
    "make_wa_socket",
    "makeWASocket",
]
