from __future__ import annotations

from typing import Any

from .abc import EventBusABC, IdentityResolverABC, SocketPluginABC, TransportABC
from .defaults import DEFAULT_CONNECTION_CONFIG
from .socket import CommunitiesSocket, make_wa_socket as make_socket_with_components
from .types import SocketConfig


def _dict_to_socket_config(config: dict[str, Any]) -> SocketConfig:
    return SocketConfig.model_validate(config)


class WassupWeb:
    def __init__(self, base_config: dict[str, Any] | None = None) -> None:
        self._base_config = dict(DEFAULT_CONNECTION_CONFIG)
        if base_config:
            self._base_config.update(base_config)
        self._plugins: list[SocketPluginABC] = []

    def register_plugin(self, plugin: SocketPluginABC) -> None:
        self._plugins.append(plugin)

    def clear_plugins(self) -> None:
        self._plugins.clear()

    def make_socket(
        self,
        config: dict[str, Any] | SocketConfig | None = None,
        *,
        transport: TransportABC | None = None,
        event_bus: EventBusABC | Any | None = None,
        plugins: list[SocketPluginABC] | None = None,
        identity_resolver: IdentityResolverABC | None = None,
    ) -> CommunitiesSocket:
        merged = dict(self._base_config)
        if isinstance(config, SocketConfig):
            final = config
        else:
            if config:
                merged.update(config)
            final = _dict_to_socket_config(merged)

        all_plugins = [*self._plugins, *(plugins or [])]
        return make_socket_with_components(
            final,
            transport=transport,
            event_bus=event_bus,
            plugins=all_plugins,
            identity_resolver=identity_resolver,
        )


def make_wa_socket_from_dict(
    config: dict[str, Any] | None = None,
    *,
    transport: TransportABC | None = None,
    event_bus: EventBusABC | Any | None = None,
    plugins: list[SocketPluginABC] | None = None,
    identity_resolver: IdentityResolverABC | None = None,
) -> CommunitiesSocket:
    facade = WassupWeb()
    return facade.make_socket(
        config,
        transport=transport,
        event_bus=event_bus,
        plugins=plugins,
        identity_resolver=identity_resolver,
    )


def make_wa_socket(
    config: dict[str, Any] | SocketConfig | None = None,
    *,
    transport: TransportABC | None = None,
    event_bus: EventBusABC | Any | None = None,
    plugins: list[SocketPluginABC] | None = None,
    identity_resolver: IdentityResolverABC | None = None,
) -> CommunitiesSocket:
    facade = WassupWeb()
    return facade.make_socket(
        config,
        transport=transport,
        event_bus=event_bus,
        plugins=plugins,
        identity_resolver=identity_resolver,
    )


def makeWASocket(
    config: dict[str, Any] | SocketConfig | None = None,
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
