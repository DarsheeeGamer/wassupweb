from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..defaults import S_WHATSAPP_NET
from ..wabinary import BinaryNode, get_binary_node_child


class WMexQueryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400, data: Any = None, response: Any = None) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.data = data
        self.response = response


class MexAddress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    participant: str | None = None


class MexPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    op: str
    timestamp: int
    address: MexAddress
    content: dict = Field(default_factory=dict)


def w_mex_query(
    variables: dict[str, Any],
    query_id: str,
    query: Any,
    generate_message_tag: Any,
) -> Any:
    return query(
        BinaryNode(
            tag="iq",
            attrs={
                "id": generate_message_tag(),
                "type": "get",
                "to": S_WHATSAPP_NET,
                "xmlns": "w:mex",
            },
            content=[
                BinaryNode(
                    tag="query",
                    attrs={"query_id": query_id},
                    content=json.dumps({"variables": variables}, separators=(",", ":")).encode("utf-8"),
                )
            ],
        )
    )


async def execute_wmex_query(
    variables: dict[str, Any],
    query_id: str,
    data_path: str,
    query: Any,
    generate_message_tag: Any,
) -> Any:
    result = await w_mex_query(variables, query_id, query, generate_message_tag)
    child = get_binary_node_child(result, "result")
    if child and child.content:
        raw = child.content
        if isinstance(raw, (bytes, bytearray)):
            parsed = json.loads(bytes(raw).decode("utf-8"))
        elif isinstance(raw, str):
            parsed = json.loads(raw)
        else:
            parsed = raw

        errors = parsed.get("errors") if isinstance(parsed, dict) else None
        if isinstance(errors, list) and errors:
            first_error = errors[0] if isinstance(errors[0], dict) else {}
            error_messages = ", ".join(str(item.get("message", "Unknown error")) for item in errors if isinstance(item, dict))
            error_code = int(((first_error.get("extensions") or {}).get("error_code")) or 400)
            raise WMexQueryError(
                f"GraphQL server error: {error_messages}",
                status_code=error_code,
                data=first_error,
                response=result,
            )

        response = parsed.get("data", {}) if isinstance(parsed, dict) else {}
        if not data_path:
            if response is not None:
                return response
        elif isinstance(response, dict) and data_path in response:
            return response.get(data_path)
        elif data_path:
            for segment in data_path.split("."):
                if isinstance(response, dict):
                    response = response.get(segment)
                else:
                    response = None
                    break
        if response is not None:
            return response

    action = data_path[5:].replace("_", " ") if data_path.startswith("xwa2_") else data_path.replace("_", " ")
    raise WMexQueryError(
        f"Failed to {action}, unexpected response structure.",
        status_code=400,
        data=result,
        response=result,
    )


# camelCase aliases
executeWMexQuery = execute_wmex_query
