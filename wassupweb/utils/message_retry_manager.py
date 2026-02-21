from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

RECENT_MESSAGES_SIZE = 512
MESSAGE_KEY_SEPARATOR = "\u0000"
RECREATE_SESSION_TIMEOUT = 60 * 60 * 1000
PHONE_REQUEST_DELAY = 3000


@dataclass
class RecentMessageKey:
    to: str
    id: str


@dataclass
class RecentMessage:
    message: dict[str, Any]
    timestamp: int


class RetryReason(IntEnum):
    UnknownError = 0
    SignalErrorNoSession = 1
    SignalErrorInvalidKey = 2
    SignalErrorInvalidKeyId = 3
    SignalErrorInvalidMessage = 4
    SignalErrorInvalidSignature = 5
    SignalErrorFutureMessage = 6
    SignalErrorBadMac = 7
    SignalErrorInvalidSession = 8
    SignalErrorInvalidMsgKey = 9
    BadBroadcastEphemeralSetting = 10
    UnknownCompanionNoPrekey = 11
    AdvFailure = 12
    StatusRevokeDelay = 13


MAC_ERROR_CODES = {RetryReason.SignalErrorInvalidMessage, RetryReason.SignalErrorBadMac}


class _TTLOrderedCache:
    def __init__(self, *, max_items: int | None = None, ttl_ms: int = 0) -> None:
        self._max_items = max_items
        self._ttl_ms = ttl_ms
        self._store: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._lock = threading.RLock()

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _purge_expired(self) -> None:
        if self._ttl_ms <= 0:
            return
        now = self._now_ms()
        expired = [k for k, (_, exp) in self._store.items() if exp <= now]
        for key in expired:
            self._store.pop(key, None)

    def _evict_if_needed(self) -> None:
        if self._max_items is None:
            return
        while len(self._store) > self._max_items:
            self._store.popitem(last=False)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._purge_expired()
            expiry = self._now_ms() + self._ttl_ms if self._ttl_ms > 0 else (2**63 - 1)
            self._store.pop(key, None)
            self._store[key] = (value, expiry)
            self._evict_if_needed()

    def get(self, key: str) -> Any:
        with self._lock:
            self._purge_expired()
            if key not in self._store:
                return None
            value, exp = self._store.pop(key)
            if exp <= self._now_ms():
                return None
            self._store[key] = (value, exp)
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


class MessageRetryManager:
    def __init__(self, logger: Any, max_msg_retry_count: int) -> None:
        self.logger = logger
        self.max_msg_retry_count = max_msg_retry_count
        self.recent_messages_map = _TTLOrderedCache(max_items=RECENT_MESSAGES_SIZE, ttl_ms=5 * 60 * 1000)
        self.message_key_index: dict[str, str] = {}
        self.session_recreate_history = _TTLOrderedCache(ttl_ms=RECREATE_SESSION_TIMEOUT * 2)
        self.retry_counters = _TTLOrderedCache(ttl_ms=15 * 60 * 1000)
        self.pending_phone_requests: dict[str, threading.Timer] = {}
        self.statistics: dict[str, int] = {
            "totalRetries": 0,
            "successfulRetries": 0,
            "failedRetries": 0,
            "mediaRetries": 0,
            "sessionRecreations": 0,
            "phoneRequests": 0,
        }
        self._lock = threading.RLock()

    def key_to_string(self, key: RecentMessageKey) -> str:
        return f"{key.to}{MESSAGE_KEY_SEPARATOR}{key.id}"

    def add_recent_message(self, to: str, message_id: str, message: dict[str, Any]) -> None:
        key = self.key_to_string(RecentMessageKey(to=to, id=message_id))
        self.recent_messages_map.set(key, RecentMessage(message=message, timestamp=int(time.time() * 1000)))
        self.message_key_index[message_id] = key
        self.logger.debug(f"Added message to retry cache: {to}/{message_id}")

    def get_recent_message(self, to: str, message_id: str) -> RecentMessage | None:
        key = self.key_to_string(RecentMessageKey(to=to, id=message_id))
        return self.recent_messages_map.get(key)

    def should_recreate_session(
        self,
        jid: str,
        has_session: bool,
        error_code: RetryReason | None = None,
    ) -> dict[str, Any]:
        if not has_session:
            self.session_recreate_history.set(jid, int(time.time() * 1000))
            self.statistics["sessionRecreations"] += 1
            return {"reason": "we don't have a Signal session with them", "recreate": True}

        if error_code is not None and error_code in MAC_ERROR_CODES:
            self.session_recreate_history.set(jid, int(time.time() * 1000))
            self.statistics["sessionRecreations"] += 1
            self.logger.warning(
                "MAC error detected, forcing immediate session recreation",
                extra={"jid": jid, "errorCode": error_code.name},
            )
            return {
                "reason": f"MAC error (code {int(error_code)}: {error_code.name}), immediate session recreation",
                "recreate": True,
            }

        now = int(time.time() * 1000)
        prev_time = self.session_recreate_history.get(jid)
        if not prev_time or now - int(prev_time) > RECREATE_SESSION_TIMEOUT:
            self.session_recreate_history.set(jid, now)
            self.statistics["sessionRecreations"] += 1
            return {"reason": "retry count > 1 and over an hour since last recreation", "recreate": True}
        return {"reason": "", "recreate": False}

    def parse_retry_error_code(self, error_attr: str | None) -> RetryReason | None:
        if error_attr is None or error_attr == "":
            return None
        try:
            code = int(error_attr)
        except ValueError:
            return None
        if int(RetryReason.UnknownError) <= code <= int(RetryReason.StatusRevokeDelay):
            return RetryReason(code)
        return RetryReason.UnknownError

    def is_mac_error(self, error_code: RetryReason | None) -> bool:
        return error_code in MAC_ERROR_CODES if error_code is not None else False

    def increment_retry_count(self, message_id: str) -> int:
        current = int(self.retry_counters.get(message_id) or 0) + 1
        self.retry_counters.set(message_id, current)
        self.statistics["totalRetries"] += 1
        return current

    def get_retry_count(self, message_id: str) -> int:
        return int(self.retry_counters.get(message_id) or 0)

    def has_exceeded_max_retries(self, message_id: str) -> bool:
        return self.get_retry_count(message_id) >= self.max_msg_retry_count

    def mark_retry_success(self, message_id: str) -> None:
        self.statistics["successfulRetries"] += 1
        self.retry_counters.delete(message_id)
        self.cancel_pending_phone_request(message_id)
        self.remove_recent_message(message_id)

    def mark_retry_failed(self, message_id: str) -> None:
        self.statistics["failedRetries"] += 1
        self.retry_counters.delete(message_id)
        self.cancel_pending_phone_request(message_id)
        self.remove_recent_message(message_id)

    def schedule_phone_request(
        self,
        message_id: str,
        callback: Callable[[], None],
        delay: int = PHONE_REQUEST_DELAY,
    ) -> None:
        self.cancel_pending_phone_request(message_id)

        def _run() -> None:
            with self._lock:
                self.pending_phone_requests.pop(message_id, None)
            self.statistics["phoneRequests"] += 1
            callback()

        timer = threading.Timer(delay / 1000.0, _run)
        with self._lock:
            self.pending_phone_requests[message_id] = timer
        timer.start()
        self.logger.debug(f"Scheduled phone request for message {message_id} with {delay}ms delay")

    def cancel_pending_phone_request(self, message_id: str) -> None:
        with self._lock:
            timer = self.pending_phone_requests.pop(message_id, None)
        if timer:
            timer.cancel()
            self.logger.debug(f"Cancelled pending phone request for message {message_id}")

    def remove_recent_message(self, message_id: str) -> None:
        key = self.message_key_index.pop(message_id, None)
        if key:
            self.recent_messages_map.delete(key)


# camelCase aliases
MessageRetryManagerClass = MessageRetryManager
