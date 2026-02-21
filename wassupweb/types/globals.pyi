from typing import Any, Literal, NotRequired, TypedDict


class RequestInit(TypedDict, total=False):
    dispatcher: NotRequired[Any]
    duplex: NotRequired[Literal["half", "full"]]
