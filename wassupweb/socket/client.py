from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from ..abc import EventBusABC, IdentityResolverABC, SocketABC, SocketPluginABC, TransportABC
from ..types.identity import JidKind, MessageIdentityView, UserRef
from ..types.socket import SocketConfig
from ..utils.event_bus import EventBus
from ..utils.generics import generate_md_tag_prefix
from ..utils.identity import IdentityResolver, resolve_message_identity
from ..utils.logger import WassupLogger, logger as default_logger
from ..wabinary import BinaryNode, decode_binary_node, encode_binary_node


class WASocketClient(SocketABC):
    def __init__(
        self,
        config: SocketConfig,
        transport: TransportABC,
        ev: EventBusABC | None = None,
        plugins: list[SocketPluginABC] | None = None,
        logger: WassupLogger | None = None,
        identity_resolver: IdentityResolverABC | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self._ev = ev or EventBus()
        self._plugins = plugins or []
        self._logger = logger or default_logger.child({"class": "wassupweb.socket"})
        self._ids = identity_resolver or IdentityResolver()
        self._recv_task: asyncio.Task[None] | None = None
        self._message_counter = 0
        self._pending_queries: dict[str, asyncio.Future[BinaryNode]] = {}
        self._tag_prefix = generate_md_tag_prefix()
        self._tag_counter = 0

    @property
    def ev(self) -> EventBusABC:
        return self._ev

    @property
    def ids(self) -> IdentityResolverABC:
        return self._ids

    def resolve_user(self, value: str | dict[str, Any]) -> dict[str, Any]:
        return self._ids.resolve(value).ref.model_dump(by_alias=True, exclude_none=True)

    def resolve_user_ref(self, value: str | dict[str, Any]) -> UserRef:
        resolved = self._ids.resolve(value).ref
        return resolved if isinstance(resolved, UserRef) else UserRef.model_validate(resolved)

    def resolve_chat_jid(self, value: str | dict[str, Any], *, prefer: JidKind = JidKind.PN) -> str:
        return self._ids.as_chat_jid(value, prefer=prefer)

    def link_identity(self, pn_jid: str, lid_jid: str) -> dict[str, Any]:
        return self._ids.link_pn_lid(pn_jid, lid_jid).ref.model_dump(by_alias=True, exclude_none=True)

    def resolve_message_identity(self, message: dict[str, Any]) -> MessageIdentityView:
        return resolve_message_identity(message, self._ids)

    async def connect(self) -> None:
        for plugin in self._plugins:
            await plugin.before_connect(self)

        await self.transport.connect()
        await self._ev.emit("connection.update", {"connection": "open"})

        self._recv_task = asyncio.create_task(self._recv_loop())
        for plugin in self._plugins:
            await plugin.after_connect(self)

    async def disconnect(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
        await self.transport.disconnect()
        await self._ev.emit("connection.update", {"connection": "close"})

    async def send_node(self, node: BinaryNode) -> None:
        current: Any = node
        for plugin in self._plugins:
            current = await plugin.before_send_node(self, current)
        payload = encode_binary_node(current)
        await self.transport.send(payload)
        await self._ev.emit("node.sent", current)

    async def query(self, node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        if "id" not in node.attrs:
            node.attrs["id"] = self._next_message_id()
        msg_id = node.attrs["id"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BinaryNode] = loop.create_future()
        self._pending_queries[msg_id] = future
        await self.send_node(node)

        timeout = (timeout_ms if timeout_ms is not None else self.config.default_query_timeout_ms) or 0
        try:
            if timeout > 0:
                return await asyncio.wait_for(future, timeout=timeout / 1000.0)
            return await future
        finally:
            self._pending_queries.pop(msg_id, None)

    def on(self, event: str, handler: Any) -> None:
        self._ev.on(event, handler)

    def off(self, event: str, handler: Any) -> None:
        self._ev.off(event, handler)

    async def wait_for(
        self,
        event: str,
        predicate: Any = None,
        timeout_ms: int | None = None,
    ) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        async def _handler(payload: Any) -> None:
            if predicate:
                allowed = predicate(payload)
                if asyncio.iscoroutine(allowed):
                    allowed = await allowed
                if not allowed:
                    return
            if not future.done():
                future.set_result(payload)

        self.on(event, _handler)
        timeout = (timeout_ms or self.config.default_query_timeout_ms) or 0
        try:
            if timeout > 0:
                return await asyncio.wait_for(future, timeout=timeout / 1000.0)
            return await future
        finally:
            self.off(event, _handler)

    async def _recv_loop(self) -> None:
        while True:
            try:
                payload = await self.transport.recv()
                node = await decode_binary_node(payload)
                current: Any = node
                for plugin in self._plugins:
                    current = await plugin.after_receive_node(self, current)
                await self._ev.emit("node", current)
                await self._ev.emit(f"node:{current.tag}", current)
                msg_id = current.attrs.get("id")
                if msg_id and msg_id in self._pending_queries and not self._pending_queries[msg_id].done():
                    self._pending_queries[msg_id].set_result(current)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - runtime wire errors
                self._logger.error("socket recv loop error", extra={"error": str(error)})
                for plugin in self._plugins:
                    await plugin.on_error(self, error)
                await self._ev.emit("error", error)
                if not self._transport_is_open():
                    await self._ev.emit(
                        "connection.update",
                        {"connection": "close", "lastDisconnect": {"error": error, "date": time.time()}},
                    )
                    return

    def _next_message_id(self) -> str:
        self._message_counter += 1
        return f"python.{self._message_counter}"

    def generate_message_tag(self) -> str:
        self._tag_counter += 1
        return f"{self._tag_prefix}{self._tag_counter}"

    # camelCase alias for Baileys parity
    def generateMessageTag(self) -> str:
        return self.generate_message_tag()

    # additional camelCase aliases for API ergonomics/parity
    resolveUser = resolve_user
    resolveUserRef = resolve_user_ref
    resolveChatJid = resolve_chat_jid
    linkIdentity = link_identity
    resolveMessageIdentity = resolve_message_identity

    def _transport_is_open(self) -> bool:
        transport = self.transport
        if hasattr(transport, "is_open"):
            state = getattr(transport, "is_open")
            if callable(state):
                try:
                    return bool(state())
                except Exception:
                    return False
            return bool(state)

        ws = getattr(transport, "_ws", None)
        if ws is not None:
            open_state = getattr(ws, "open", None)
            if open_state is not None:
                return bool(open_state)
            closed_state = getattr(ws, "closed", None)
            if isinstance(closed_state, bool):
                return not closed_state

        return False
