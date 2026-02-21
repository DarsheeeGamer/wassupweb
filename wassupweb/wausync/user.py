from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class USyncUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    lid: str | None = None
    phone: str | None = None
    type: str | None = None
    persona_id: str | None = Field(default=None, alias="personaId")

    def with_id(self, value: str) -> "USyncUser":
        self.id = value
        return self

    def with_lid(self, value: str) -> "USyncUser":
        self.lid = value
        return self

    def with_phone(self, value: str) -> "USyncUser":
        self.phone = value
        return self

    def with_type(self, value: str) -> "USyncUser":
        self.type = value
        return self

    def with_persona_id(self, value: str) -> "USyncUser":
        self.persona_id = value
        return self

    # camelCase aliases for Baileys parity
    withId = with_id
    withLid = with_lid
    withPhone = with_phone
    withType = with_type
    withPersonaId = with_persona_id
