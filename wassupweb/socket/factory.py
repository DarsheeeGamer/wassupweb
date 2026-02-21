from __future__ import annotations

from typing import Any

from ..abc import EventBusABC, IdentityResolverABC, SocketPluginABC, TransportABC
from ..types.socket import SocketConfig
from .communities import CommunitiesSocket
from .index import make_wa_socket as _make_wa_socket


def make_wa_socket(
    config: SocketConfig,
    *,
    transport: TransportABC | None = None,
    event_bus: EventBusABC | Any | None = None,
    plugins: list[SocketPluginABC] | None = None,
    identity_resolver: IdentityResolverABC | None = None,
) -> CommunitiesSocket:
    return _make_wa_socket(
        config=config,
        transport=transport,
        event_bus=event_bus,
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
        config,
        transport=transport,
        event_bus=event_bus,
        plugins=plugins,
        identity_resolver=identity_resolver,
    )
