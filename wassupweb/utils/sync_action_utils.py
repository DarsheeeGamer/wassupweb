from __future__ import annotations

from typing import Any, Literal, TypedDict

from ..wabinary import is_lid_user, is_pn_user


class ContactsUpsertResult(TypedDict):
    event: Literal["contacts.upsert"]
    data: list[dict[str, Any]]


class LidMappingUpdateResult(TypedDict):
    event: Literal["lid-mapping.update"]
    data: dict[str, str]


SyncActionResult = ContactsUpsertResult | LidMappingUpdateResult


def process_contact_action(
    action: dict[str, Any],
    contact_id: str | None,
    logger: Any = None,
) -> list[SyncActionResult]:
    results: list[SyncActionResult] = []
    if not contact_id:
        if logger:
            payload = {
                "hasFullName": bool(action.get("fullName")),
                "hasLidJid": bool(action.get("lidJid")),
                "hasPnJid": bool(action.get("pnJid")),
            }
            if hasattr(logger, "warn") and callable(logger.warn):
                logger.warn(payload, "contactAction sync: missing id in index")
            elif hasattr(logger, "warning") and callable(logger.warning):
                logger.warning("contactAction sync: missing id in index", extra=payload)
        return results

    lid_jid = action.get("lidJid")
    id_is_pn = is_pn_user(contact_id)
    phone_number = contact_id if id_is_pn else action.get("pnJid")

    results.append(
        {
            "event": "contacts.upsert",
            "data": [
                {
                    "id": contact_id,
                    "name": action.get("fullName") or action.get("firstName") or action.get("username"),
                    "lid": lid_jid,
                    "phoneNumber": phone_number,
                }
            ],
        }
    )

    if lid_jid and is_lid_user(lid_jid) and id_is_pn:
        results.append(
            {
                "event": "lid-mapping.update",
                "data": {"lid": lid_jid, "pn": contact_id},
            }
        )

    return results


async def emit_sync_action_results(ev: Any, results: list[SyncActionResult]) -> None:
    for result in results:
        await ev.emit(result["event"], result["data"])


# camelCase aliases
processContactAction = process_contact_action
emitSyncActionResults = emit_sync_action_results


__all__ = ["process_contact_action", "emit_sync_action_results", "SyncActionResult"]
