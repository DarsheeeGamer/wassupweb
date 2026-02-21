from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class DecryptGroupSignalOpts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group: str
    author_jid: str = Field(alias="authorJid")
    msg: bytes


class ProcessSenderKeyDistributionMessageOpts(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    item: dict
    author_jid: str = Field(alias="authorJid")


class DecryptSignalProtoOpts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    type: str
    ciphertext: bytes


class EncryptMessageOpts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    data: bytes


class EncryptGroupMessageOpts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group: str
    data: bytes
    me_id: str = Field(alias="meId")


class PreKey(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    key_id: int = Field(alias="keyId")
    public_key: bytes = Field(alias="publicKey")


class SignedPreKey(PreKey):
    signature: bytes


class E2ESession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    registration_id: int = Field(alias="registrationId")
    identity_key: bytes = Field(alias="identityKey")
    signed_pre_key: SignedPreKey = Field(alias="signedPreKey")
    pre_key: PreKey = Field(alias="preKey")


class E2ESessionOpts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    session: E2ESession


class SignalRepository(Protocol):
    async def decrypt_group_message(self, opts: DecryptGroupSignalOpts) -> bytes:
        ...

    async def process_sender_key_distribution_message(self, opts: ProcessSenderKeyDistributionMessageOpts) -> None:
        ...

    async def decrypt_message(self, opts: DecryptSignalProtoOpts) -> bytes:
        ...

    async def encrypt_message(self, opts: EncryptMessageOpts) -> dict[str, object]:
        ...

    async def encrypt_group_message(self, opts: EncryptGroupMessageOpts) -> dict[str, bytes]:
        ...

    async def inject_e2e_session(self, opts: E2ESessionOpts) -> None:
        ...

    async def validate_session(self, jid: str) -> dict[str, object]:
        ...

    def jid_to_signal_protocol_address(self, jid: str) -> str:
        ...

    async def migrate_session(self, from_jid: str, to_jid: str) -> dict[str, int]:
        ...

    async def delete_session(self, jids: list[str]) -> None:
        ...


class SignalRepositoryWithLIDStore(SignalRepository, Protocol):
    lid_mapping: object
