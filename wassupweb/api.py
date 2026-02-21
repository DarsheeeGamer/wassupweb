from __future__ import annotations

from typing import Any

from .abc import IdentityResolverABC
from .central import WassupWeb, make_wa_socket
from .socket import CommunitiesSocket
from .types.auth import AuthenticationCreds, AuthenticationState
from .types.socket import SocketConfig
from .utils.identity import IdentityResolver, link_pn_lid, resolve_user

Client = CommunitiesSocket
Config = SocketConfig
Creds = AuthenticationCreds
AuthState = AuthenticationState
App = WassupWeb
Ids = IdentityResolver


def create_client(
    config: dict[str, Any] | SocketConfig | None = None,
    *,
    identity_resolver: IdentityResolverABC | None = None,
) -> Client:
    return make_wa_socket(config, identity_resolver=identity_resolver)


def new_client(
    config: dict[str, Any] | SocketConfig | None = None,
    *,
    identity_resolver: IdentityResolverABC | None = None,
) -> Client:
    return create_client(config, identity_resolver=identity_resolver)
