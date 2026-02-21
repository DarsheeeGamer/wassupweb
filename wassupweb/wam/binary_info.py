from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BinaryInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    protocol_version: int = Field(default=5, alias="protocolVersion")
    sequence: int = 0
    events: list[dict[str, object]] = Field(default_factory=list)
    buffer: list[bytes] = Field(default_factory=list)
