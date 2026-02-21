from __future__ import annotations

import base64
from typing import Any

from ..types.newsletter import (
    NewsletterChangeOwnerInput,
    NewsletterCreateInput,
    NewsletterCreateResponse,
    NewsletterDemoteInput,
    NewsletterDescriptionUpdateInput,
    NewsletterFetchMessagesInput,
    NewsletterJidInput,
    NewsletterMetadata,
    NewsletterMetadataInput,
    NewsletterNameUpdateInput,
    NewsletterPictureUpdateInput,
    NewsletterReactInput,
    NewsletterUpdate,
    NewsletterUpdateInput,
    QueryIds,
    XWAPaths,
)
from ..utils.messages_media import generate_profile_picture
from ..wabinary import BinaryNode, get_binary_node_child
from .groups import GroupsSocket
from .mex import execute_wmex_query


def parse_newsletter_create_response(response: dict[str, Any] | NewsletterCreateResponse) -> dict[str, Any]:
    data = response.model_dump(by_alias=True, exclude_none=True) if isinstance(response, NewsletterCreateResponse) else response
    thread = data.get("thread_metadata") or {}
    viewer = data.get("viewer_metadata") or {}
    picture = thread.get("picture") or {}
    return {
        "id": data.get("id"),
        "owner": None,
        "name": ((thread.get("name") or {}).get("text")),
        "creation_time": int(thread.get("creation_time") or 0),
        "description": ((thread.get("description") or {}).get("text")),
        "invite": thread.get("invite"),
        "subscribers": int(thread.get("subscribers_count") or 0),
        "verification": thread.get("verification"),
        "picture": {"id": picture.get("id"), "directPath": picture.get("direct_path")},
        "mute_state": viewer.get("mute"),
    }


def parse_newsletter_metadata(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("id"), str):
        return result
    nested = result.get("result")
    if isinstance(nested, dict) and isinstance(nested.get("id"), str):
        return nested
    return None


class NewsletterSocket(GroupsSocket):
    async def _execute_wmex_query(self, variables: dict[str, Any], query_id: str, data_path: str) -> Any:
        return await execute_wmex_query(
            variables=variables,
            query_id=query_id,
            data_path=data_path,
            query=self.query_node,
            generate_message_tag=self.generate_message_tag,
        )

    async def newsletter_create(self, name: str, description: str | None = None) -> dict[str, Any]:
        variables = {"input": {"name": name, "description": description if description is not None else None}}
        raw = await self._execute_wmex_query(variables, QueryIds.CREATE.value, XWAPaths.xwa2_newsletter_create.value)
        return parse_newsletter_create_response(raw)

    async def newsletter_update(self, jid: str, updates: NewsletterUpdate | dict[str, Any]) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        patch = updates.model_dump(by_alias=True, exclude_none=False) if isinstance(updates, NewsletterUpdate) else dict(updates)
        variables = {"newsletter_id": resolved_jid, "updates": {**patch, "settings": None}}
        return await self._execute_wmex_query(variables, QueryIds.UPDATE_METADATA.value, "xwa2_newsletter_update")

    async def newsletter_subscribers(self, jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.SUBSCRIBERS.value,
            XWAPaths.xwa2_newsletter_subscribers.value,
        )

    async def newsletter_metadata(self, type: str, key: str) -> dict[str, Any] | None:
        variables = {
            "fetch_creation_time": True,
            "fetch_full_image": True,
            "fetch_viewer_metadata": True,
            "input": {"key": key, "type": type.upper()},
        }
        result = await self._execute_wmex_query(
            variables,
            QueryIds.METADATA.value,
            XWAPaths.xwa2_newsletter_metadata.value,
        )
        return parse_newsletter_metadata(result)

    async def newsletter_follow(self, jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.FOLLOW.value,
            XWAPaths.xwa2_newsletter_follow.value,
        )

    async def newsletter_unfollow(self, jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.UNFOLLOW.value,
            XWAPaths.xwa2_newsletter_unfollow.value,
        )

    async def newsletter_mute(self, jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.MUTE.value,
            XWAPaths.xwa2_newsletter_mute_v2.value,
        )

    async def newsletter_unmute(self, jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.UNMUTE.value,
            XWAPaths.xwa2_newsletter_unmute_v2.value,
        )

    async def newsletter_update_name(self, jid: str, name: str) -> Any:
        return await self.newsletter_update(jid, {"name": name})

    async def newsletter_update_description(self, jid: str, description: str) -> Any:
        return await self.newsletter_update(jid, {"description": description})

    async def newsletter_update_picture(self, jid: str, content: Any) -> Any:
        pic = await generate_profile_picture(content)
        image = pic.get("img")
        if not isinstance(image, (bytes, bytearray)):
            raise ValueError("generate_profile_picture() did not return binary image payload")
        return await self.newsletter_update(jid, {"picture": base64.b64encode(bytes(image)).decode("ascii")})

    async def newsletter_remove_picture(self, jid: str) -> Any:
        return await self.newsletter_update(jid, {"picture": ""})

    async def newsletter_react_message(self, jid: str, server_id: str, reaction: str | None = None) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        await self.query_node(
            BinaryNode(
                tag="message",
                attrs={
                    "to": resolved_jid,
                    **({} if reaction else {"edit": "7"}),
                    "type": "reaction",
                    "server_id": server_id,
                    "id": self.generate_message_tag(),
                },
                content=[BinaryNode(tag="reaction", attrs={"code": reaction} if reaction else {})],
            )
        )

    async def newsletter_fetch_messages(self, jid: str, count: int, since: int | None = None, after: int | None = None) -> BinaryNode:
        resolved_jid = self.resolve_chat_jid(jid)
        attrs: dict[str, str] = {"count": str(count)}
        if isinstance(since, int):
            attrs["since"] = str(since)
        if isinstance(after, int) and after > 0:
            attrs["after"] = str(after)

        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"id": self.generate_message_tag(), "type": "get", "xmlns": "newsletter", "to": resolved_jid},
                content=[BinaryNode(tag="message_updates", attrs=attrs)],
            )
        )

    async def subscribe_newsletter_updates(self, jid: str) -> dict[str, str] | None:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"id": self.generate_message_tag(), "type": "set", "xmlns": "newsletter", "to": resolved_jid},
                content=[BinaryNode(tag="live_updates", attrs={}, content=[])],
            )
        )
        node = get_binary_node_child(result, "live_updates")
        duration = node.attrs.get("duration") if node else None
        return {"duration": duration} if duration else None

    async def newsletter_admin_count(self, jid: str) -> int:
        resolved_jid = self.resolve_chat_jid(jid)
        response = await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.ADMIN_COUNT.value,
            XWAPaths.xwa2_newsletter_admin_count.value,
        )
        return int((response or {}).get("admin_count") or 0)

    async def newsletter_change_owner(self, jid: str, new_owner_jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        resolved_owner = self.resolve_chat_jid(new_owner_jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid, "user_id": resolved_owner},
            QueryIds.CHANGE_OWNER.value,
            XWAPaths.xwa2_newsletter_change_owner.value,
        )

    async def newsletter_demote(self, jid: str, user_jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        resolved_user = self.resolve_chat_jid(user_jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid, "user_id": resolved_user},
            QueryIds.DEMOTE.value,
            XWAPaths.xwa2_newsletter_demote.value,
        )

    async def newsletter_delete(self, jid: str) -> Any:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self._execute_wmex_query(
            {"newsletter_id": resolved_jid},
            QueryIds.DELETE.value,
            XWAPaths.xwa2_newsletter_delete_v2.value,
        )

    # typed convenience interfaces
    async def create_newsletter(self, request: NewsletterCreateInput | dict[str, Any]) -> dict[str, Any]:
        payload = request if isinstance(request, NewsletterCreateInput) else NewsletterCreateInput.model_validate(request)
        return await self.newsletter_create(payload.name, payload.description)

    async def update_newsletter(self, request: NewsletterUpdateInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterUpdateInput) else NewsletterUpdateInput.model_validate(request)
        updates: NewsletterUpdate | dict[str, Any] = payload.updates
        if isinstance(updates, NewsletterUpdate):
            updates = updates.model_dump(by_alias=True, exclude_none=True)
        return await self.newsletter_update(payload.jid, updates)

    async def fetch_newsletter_subscribers(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_subscribers(payload.jid)

    async def fetch_newsletter_metadata(self, request: NewsletterMetadataInput | dict[str, Any]) -> dict[str, Any] | None:
        payload = request if isinstance(request, NewsletterMetadataInput) else NewsletterMetadataInput.model_validate(request)
        return await self.newsletter_metadata(payload.type, payload.key)

    async def follow_newsletter(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_follow(payload.jid)

    async def unfollow_newsletter(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_unfollow(payload.jid)

    async def mute_newsletter(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_mute(payload.jid)

    async def unmute_newsletter(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_unmute(payload.jid)

    async def update_newsletter_name(self, request: NewsletterNameUpdateInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterNameUpdateInput) else NewsletterNameUpdateInput.model_validate(request)
        return await self.newsletter_update_name(payload.jid, payload.name)

    async def update_newsletter_description(
        self, request: NewsletterDescriptionUpdateInput | dict[str, Any]
    ) -> Any:
        payload = (
            request
            if isinstance(request, NewsletterDescriptionUpdateInput)
            else NewsletterDescriptionUpdateInput.model_validate(request)
        )
        return await self.newsletter_update_description(payload.jid, payload.description)

    async def update_newsletter_picture(self, request: NewsletterPictureUpdateInput | dict[str, Any]) -> Any:
        payload = (
            request if isinstance(request, NewsletterPictureUpdateInput) else NewsletterPictureUpdateInput.model_validate(request)
        )
        return await self.newsletter_update_picture(payload.jid, payload.content)

    async def remove_newsletter_picture(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_remove_picture(payload.jid)

    async def react_to_newsletter_message(self, request: NewsletterReactInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, NewsletterReactInput) else NewsletterReactInput.model_validate(request)
        await self.newsletter_react_message(payload.jid, payload.server_id, payload.reaction)

    async def fetch_newsletter_messages(self, request: NewsletterFetchMessagesInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request
            if isinstance(request, NewsletterFetchMessagesInput)
            else NewsletterFetchMessagesInput.model_validate(request)
        )
        return await self.newsletter_fetch_messages(payload.jid, payload.count, payload.since, payload.after)

    async def subscribe_to_newsletter_updates(self, request: NewsletterJidInput | dict[str, Any]) -> dict[str, str] | None:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.subscribe_newsletter_updates(payload.jid)

    async def get_newsletter_admin_count(self, request: NewsletterJidInput | dict[str, Any]) -> int:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_admin_count(payload.jid)

    async def change_newsletter_owner(self, request: NewsletterChangeOwnerInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterChangeOwnerInput) else NewsletterChangeOwnerInput.model_validate(request)
        return await self.newsletter_change_owner(payload.jid, payload.new_owner_jid)

    async def demote_newsletter_admin(self, request: NewsletterDemoteInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterDemoteInput) else NewsletterDemoteInput.model_validate(request)
        return await self.newsletter_demote(payload.jid, payload.user_jid)

    async def delete_newsletter(self, request: NewsletterJidInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, NewsletterJidInput) else NewsletterJidInput.model_validate(request)
        return await self.newsletter_delete(payload.jid)

    # camelCase aliases for Baileys parity
    newsletterCreate = newsletter_create
    newsletterUpdate = newsletter_update
    newsletterSubscribers = newsletter_subscribers
    newsletterMetadata = newsletter_metadata
    newsletterFollow = newsletter_follow
    newsletterUnfollow = newsletter_unfollow
    newsletterMute = newsletter_mute
    newsletterUnmute = newsletter_unmute
    newsletterUpdateName = newsletter_update_name
    newsletterUpdateDescription = newsletter_update_description
    newsletterUpdatePicture = newsletter_update_picture
    newsletterRemovePicture = newsletter_remove_picture
    newsletterReactMessage = newsletter_react_message
    newsletterFetchMessages = newsletter_fetch_messages
    subscribeNewsletterUpdates = subscribe_newsletter_updates
    newsletterAdminCount = newsletter_admin_count
    newsletterChangeOwner = newsletter_change_owner
    newsletterDemote = newsletter_demote
    newsletterDelete = newsletter_delete
    createNewsletter = create_newsletter
    updateNewsletter = update_newsletter
    fetchNewsletterSubscribers = fetch_newsletter_subscribers
    fetchNewsletterMetadata = fetch_newsletter_metadata
    followNewsletter = follow_newsletter
    unfollowNewsletter = unfollow_newsletter
    muteNewsletter = mute_newsletter
    unmuteNewsletter = unmute_newsletter
    updateNewsletterName = update_newsletter_name
    updateNewsletterDescription = update_newsletter_description
    updateNewsletterPicture = update_newsletter_picture
    removeNewsletterPicture = remove_newsletter_picture
    reactToNewsletterMessage = react_to_newsletter_message
    fetchNewsletterMessages = fetch_newsletter_messages
    subscribeToNewsletterUpdates = subscribe_to_newsletter_updates
    getNewsletterAdminCount = get_newsletter_admin_count
    changeNewsletterOwner = change_newsletter_owner
    demoteNewsletterAdmin = demote_newsletter_admin
    deleteNewsletter = delete_newsletter
