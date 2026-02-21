from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

EventHandler = Callable[[Any], Any | Awaitable[Any]]


class EventBusABC(ABC):
    @abstractmethod
    def on(self, event: str, handler: EventHandler) -> None:
        ...

    @abstractmethod
    def off(self, event: str, handler: EventHandler) -> None:
        ...

    @abstractmethod
    async def emit(self, event: str, payload: Any) -> None:
        ...

    def buffer(self) -> None:
        return None

    def flush(self) -> bool:
        return False

    def remove_all_listeners(self, event: str) -> None:
        return None

    def create_buffered_function(self, work: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        return work


class TransportABC(ABC):
    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def send(self, payload: bytes) -> None:
        ...

    @abstractmethod
    async def recv(self) -> bytes:
        ...


class SignalStoreABC(ABC):
    @abstractmethod
    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        ...

    @abstractmethod
    async def set(self, data: dict[str, dict[str, Any | None]]) -> None:
        ...

    @abstractmethod
    async def clear(self) -> None:
        ...


class AuthStateStoreABC(ABC):
    @abstractmethod
    async def load_creds(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def save_creds(self, creds: dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def load_keys(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        ...

    @abstractmethod
    async def save_keys(self, data: dict[str, dict[str, Any | None]]) -> None:
        ...


class IdentityResolverABC(ABC):
    @abstractmethod
    def resolve(self, value: Any) -> Any:
        ...

    @abstractmethod
    def as_chat_jid(self, value: Any, *, prefer: Any = None) -> str:
        ...

    @abstractmethod
    def link_pn_lid(self, pn_jid: str, lid_jid: str) -> Any:
        ...


class SocketPluginABC(ABC):
    async def before_connect(self, socket: "SocketABC") -> None:
        return None

    async def after_connect(self, socket: "SocketABC") -> None:
        return None

    async def before_send_node(self, socket: "SocketABC", node: Any) -> Any:
        return node

    async def after_receive_node(self, socket: "SocketABC", node: Any) -> Any:
        return node

    async def on_error(self, socket: "SocketABC", error: Exception) -> None:
        return None


class SocketABC(ABC):
    @property
    @abstractmethod
    def ev(self) -> EventBusABC:
        ...

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def send_node(self, node: Any) -> None:
        ...

    @abstractmethod
    async def query(self, node: Any, timeout_ms: int | None = None) -> Any:
        ...
