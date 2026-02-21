from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from ..types.identity import IdentityResolveResult, JidKind, MessageIdentityView, UserRef
from ..wabinary import (
    is_hosted_lid_user,
    is_hosted_pn_user,
    is_jid_bot,
    is_jid_broadcast,
    is_jid_group,
    is_jid_newsletter,
    is_lid_user,
    is_pn_user,
    jid_decode,
    jid_encode,
    jid_normalized_user,
)

PHONE_CLEAN_RE = re.compile(r"[^\d+]")
E164_RE = re.compile(r"^\+?\d{6,20}$")


def normalize_phone(value: str) -> str:
    cleaned = PHONE_CLEAN_RE.sub("", value.strip())
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if not E164_RE.match(cleaned):
        raise ValueError(f"invalid phone-like value: {value!r}")
    return cleaned


def phone_to_pn_jid(phone: str) -> str:
    normalized = normalize_phone(phone)
    return f"{normalized}@s.whatsapp.net"


def is_probable_phone(value: str) -> bool:
    candidate = PHONE_CLEAN_RE.sub("", value.strip())
    if candidate.startswith("+"):
        candidate = candidate[1:]
    return bool(candidate) and candidate.isdigit() and 6 <= len(candidate) <= 20 and "@" not in value


def detect_jid_kind(jid: str | None) -> JidKind:
    if not jid:
        return JidKind.UNKNOWN
    if is_pn_user(jid) or is_hosted_pn_user(jid):
        return JidKind.PN
    if is_lid_user(jid) or is_hosted_lid_user(jid):
        return JidKind.LID
    if is_jid_group(jid):
        return JidKind.GROUP
    if is_jid_newsletter(jid):
        return JidKind.NEWSLETTER
    if is_jid_broadcast(jid):
        return JidKind.BROADCAST
    if is_jid_bot(jid):
        return JidKind.BOT
    return JidKind.UNKNOWN


def canonical_user_id_from_jid(jid: str) -> str:
    decoded = jid_decode(jid)
    if not decoded:
        return f"unknown:{jid}"
    user = decoded.get("user") or jid
    kind = detect_jid_kind(jid)
    # Device ID is intentionally excluded to keep a single stable user-level ID.
    return f"{kind.value}:{user}"


def ensure_jid(value: str, default_server: str = "s.whatsapp.net") -> str:
    text = value.strip()
    if "@" in text:
        return jid_normalized_user(text)
    if is_probable_phone(text):
        return phone_to_pn_jid(text)
    return jid_encode(text, default_server)


@dataclass
class _IndexState:
    by_user_id: dict[str, UserRef]
    by_jid: dict[str, str]
    pn_to_user_id: dict[str, str]
    lid_to_user_id: dict[str, str]


class IdentityResolver:
    def __init__(self) -> None:
        self._state = _IndexState(by_user_id={}, by_jid={}, pn_to_user_id={}, lid_to_user_id={})
        self._lock = threading.RLock()

    def resolve(self, value: str | UserRef | dict[str, Any]) -> IdentityResolveResult:
        with self._lock:
            if isinstance(value, UserRef):
                return self._resolve_user_ref(value)
            if isinstance(value, dict):
                return self._resolve_user_ref(UserRef.model_validate(value))
            return self._resolve_text(value)

    def resolve_many(self, values: Iterable[str | UserRef | dict[str, Any]]) -> list[IdentityResolveResult]:
        return [self.resolve(item) for item in values]

    def get(self, user_id: str) -> UserRef | None:
        with self._lock:
            ref = self._state.by_user_id.get(user_id)
            return ref.model_copy(deep=True) if ref else None

    def all(self) -> list[UserRef]:
        with self._lock:
            return [ref.model_copy(deep=True) for ref in self._state.by_user_id.values()]

    def link_pn_lid(self, pn_jid: str, lid_jid: str) -> IdentityResolveResult:
        with self._lock:
            pn_res = self._resolve_text(pn_jid)
            lid_res = self._resolve_text(lid_jid)
            merged = self._merge_user_ids(pn_res.ref.user_id, lid_res.ref.user_id)
            return IdentityResolveResult(ref=merged.model_copy(deep=True), created=False, merged=True)

    def as_chat_jid(self, value: str | UserRef | dict[str, Any], *, prefer: JidKind = JidKind.PN) -> str:
        result = self.resolve(value)
        ref = result.ref
        if prefer == JidKind.LID and ref.lid_jid:
            return ref.lid_jid
        if ref.pn_jid:
            return ref.pn_jid
        if ref.lid_jid:
            return ref.lid_jid
        if ref.jid:
            return ref.jid
        raise ValueError(f"cannot resolve chat JID for value: {value!r}")

    def _resolve_text(self, value: str) -> IdentityResolveResult:
        text = value.strip()
        if not text:
            raise ValueError("empty identity value")

        if text in self._state.by_user_id:
            return IdentityResolveResult(ref=self._state.by_user_id[text].model_copy(deep=True), created=False, merged=False)

        source = "jid"
        if is_probable_phone(text):
            jid = phone_to_pn_jid(text)
            source = "phone"
        elif "@" in text:
            jid = jid_normalized_user(text)
        else:
            # bare user ID fallback
            jid = ensure_jid(text)

        if jid in self._state.by_jid:
            existing = self._state.by_user_id[self._state.by_jid[jid]]
            return IdentityResolveResult(ref=existing.model_copy(deep=True), created=False, merged=False)

        kind = detect_jid_kind(jid)
        user_id = canonical_user_id_from_jid(jid)
        decoded = jid_decode(jid) or {}
        ref = UserRef(
            userId=user_id,
            jid=jid,
            pnJid=jid if kind == JidKind.PN else None,
            lidJid=jid if kind == JidKind.LID else None,
            kind=kind,
            user=decoded.get("user"),
            server=decoded.get("server"),
            device=decoded.get("device"),
            source=source,
        )

        self._state.by_user_id[user_id] = ref
        self._state.by_jid[jid] = user_id
        if ref.pn_jid:
            self._state.pn_to_user_id[ref.pn_jid] = user_id
        if ref.lid_jid:
            self._state.lid_to_user_id[ref.lid_jid] = user_id
        return IdentityResolveResult(ref=ref.model_copy(deep=True), created=True, merged=False)

    def _resolve_user_ref(self, value: UserRef) -> IdentityResolveResult:
        ref = value.model_copy(deep=True)
        if not ref.jid and ref.pn_jid:
            ref.jid = ref.pn_jid
        if not ref.jid and ref.lid_jid:
            ref.jid = ref.lid_jid
        if not ref.jid:
            raise ValueError("UserRef must include at least one of jid/pn_jid/lid_jid")
        kind = detect_jid_kind(ref.jid)
        if not ref.user_id:
            ref.user_id = canonical_user_id_from_jid(ref.jid)
        if kind == JidKind.PN and not ref.pn_jid:
            ref.pn_jid = ref.jid
        if kind == JidKind.LID and not ref.lid_jid:
            ref.lid_jid = ref.jid

        existing = self._state.by_user_id.get(ref.user_id)
        if not existing:
            self._state.by_user_id[ref.user_id] = ref
            self._state.by_jid[ref.jid] = ref.user_id
            if ref.pn_jid:
                self._state.pn_to_user_id[ref.pn_jid] = ref.user_id
            if ref.lid_jid:
                self._state.lid_to_user_id[ref.lid_jid] = ref.user_id
            return IdentityResolveResult(ref=ref.model_copy(deep=True), created=True, merged=False)

        merged = self._merge_refs(existing, ref)
        self._state.by_user_id[merged.user_id] = merged
        if merged.jid:
            self._state.by_jid[merged.jid] = merged.user_id
        if merged.pn_jid:
            self._state.pn_to_user_id[merged.pn_jid] = merged.user_id
            self._state.by_jid[merged.pn_jid] = merged.user_id
        if merged.lid_jid:
            self._state.lid_to_user_id[merged.lid_jid] = merged.user_id
            self._state.by_jid[merged.lid_jid] = merged.user_id
        return IdentityResolveResult(ref=merged.model_copy(deep=True), created=False, merged=True)

    def _merge_refs(self, base: UserRef, incoming: UserRef) -> UserRef:
        merged = base.model_copy(deep=True)
        for field in ("jid", "pn_jid", "lid_jid", "user", "server", "device"):
            value = getattr(incoming, field)
            if value is not None and getattr(merged, field) is None:
                setattr(merged, field, value)
        if merged.kind == JidKind.UNKNOWN and incoming.kind != JidKind.UNKNOWN:
            merged.kind = incoming.kind
        if merged.source == "unknown" and incoming.source != "unknown":
            merged.source = incoming.source
        return merged

    def _merge_user_ids(self, left: str, right: str) -> UserRef:
        if left == right:
            return self._state.by_user_id[left]
        left_ref = self._state.by_user_id[left]
        right_ref = self._state.by_user_id[right]
        merged = self._merge_refs(left_ref, right_ref)
        if right_ref.pn_jid and not merged.pn_jid:
            merged.pn_jid = right_ref.pn_jid
        if right_ref.lid_jid and not merged.lid_jid:
            merged.lid_jid = right_ref.lid_jid

        # Prefer PN-kind user_id as canonical if available.
        if merged.kind == JidKind.PN or left_ref.kind == JidKind.PN:
            canonical_id = left_ref.user_id if left_ref.kind == JidKind.PN else right_ref.user_id
        else:
            canonical_id = left_ref.user_id
        merged.user_id = canonical_id

        self._state.by_user_id.pop(left, None)
        self._state.by_user_id.pop(right, None)
        self._state.by_user_id[merged.user_id] = merged
        for jid in {merged.jid, merged.pn_jid, merged.lid_jid, left_ref.jid, right_ref.jid, left_ref.pn_jid, right_ref.pn_jid, left_ref.lid_jid, right_ref.lid_jid}:
            if jid:
                self._state.by_jid[jid] = merged.user_id
        if merged.pn_jid:
            self._state.pn_to_user_id[merged.pn_jid] = merged.user_id
        if merged.lid_jid:
            self._state.lid_to_user_id[merged.lid_jid] = merged.user_id
        return merged


_GLOBAL_RESOLVER = IdentityResolver()


def resolve_message_identity(
    message: dict[str, Any],
    resolver: Any = None,
) -> MessageIdentityView:
    id_resolver = resolver or _GLOBAL_RESOLVER
    key = message.get("key") if isinstance(message, dict) else None
    if not isinstance(key, dict):
        key = {}

    remote_raw = key.get("remoteJid")
    participant_raw = key.get("participant") or message.get("participant")

    remote_ref = id_resolver.resolve(str(remote_raw)).ref if isinstance(remote_raw, str) and remote_raw else None
    participant_ref = (
        id_resolver.resolve(str(participant_raw)).ref if isinstance(participant_raw, str) and participant_raw else None
    )
    sender_ref = participant_ref or remote_ref

    return MessageIdentityView(
        remote=remote_ref,
        participant=participant_ref,
        sender=sender_ref,
        remoteUserId=remote_ref.user_id if remote_ref else None,
        participantUserId=participant_ref.user_id if participant_ref else None,
        senderUserId=sender_ref.user_id if sender_ref else None,
    )


def resolve_user(value: str | UserRef | dict[str, Any]) -> UserRef:
    return _GLOBAL_RESOLVER.resolve(value).ref


def link_pn_lid(pn_jid: str, lid_jid: str) -> UserRef:
    return _GLOBAL_RESOLVER.link_pn_lid(pn_jid, lid_jid).ref


__all__ = [
    "IdentityResolver",
    "resolve_user",
    "link_pn_lid",
    "resolve_message_identity",
    "normalize_phone",
    "phone_to_pn_jid",
    "is_probable_phone",
    "detect_jid_kind",
    "canonical_user_id_from_jid",
    "ensure_jid",
]
