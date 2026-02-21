from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...types.usync import USyncQueryProtocol
from ...wabinary.generic_utils import (
    get_binary_node_child,
    get_binary_node_children,
    get_binary_node_child_string,
)
from ...wabinary.types import BinaryNode
from ..user import USyncUser


class BotProfileCommand(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    description: str


class BotProfileInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    name: str
    attributes: str
    description: str
    category: str
    is_default: bool = Field(alias="isDefault")
    prompts: list[str]
    persona_id: str = Field(alias="personaId")
    commands: list[BotProfileCommand]
    commands_description: str = Field(alias="commandsDescription")


class BotProfileProtocol(USyncQueryProtocol):
    name = "bot"

    def get_query_element(self) -> BinaryNode:
        return BinaryNode(
            tag="bot",
            attrs={},
            content=[BinaryNode(tag="profile", attrs={"v": "1"})],
        )

    def get_user_element(self, user: USyncUser) -> BinaryNode:
        return BinaryNode(
            tag="bot",
            attrs={},
            content=[BinaryNode(tag="profile", attrs={"persona_id": user.persona_id or ""})],
        )

    def parser(self, node: BinaryNode) -> BotProfileInfo:
        bot_node = get_binary_node_child(node, "bot")
        profile = get_binary_node_child(bot_node, "profile") if bot_node else None
        if not profile:
            raise ValueError("missing bot/profile node in USync response")

        commands_node = get_binary_node_child(profile, "commands")
        prompts_node = get_binary_node_child(profile, "prompts")
        commands: list[BotProfileCommand] = []
        prompts: list[str] = []

        if commands_node:
            for command in get_binary_node_children(commands_node, "command"):
                commands.append(
                    BotProfileCommand(
                        name=get_binary_node_child_string(command, "name") or "",
                        description=get_binary_node_child_string(command, "description") or "",
                    )
                )

        if prompts_node:
            for prompt in get_binary_node_children(prompts_node, "prompt"):
                emoji = get_binary_node_child_string(prompt, "emoji") or ""
                text = get_binary_node_child_string(prompt, "text") or ""
                prompts.append(f"{emoji} {text}".strip())

        return BotProfileInfo(
            is_default=bool(get_binary_node_child(profile, "default")),
            jid=node.attrs.get("jid", ""),
            name=get_binary_node_child_string(profile, "name") or "",
            attributes=get_binary_node_child_string(profile, "attributes") or "",
            description=get_binary_node_child_string(profile, "description") or "",
            category=get_binary_node_child_string(profile, "category") or "",
            persona_id=profile.attrs.get("persona_id", ""),
            commands_description=get_binary_node_child_string(commands_node, "description") if commands_node else "",
            commands=commands,
            prompts=prompts,
        )

    # camelCase aliases for Baileys parity
    getQueryElement = get_query_element
    getUserElement = get_user_element
