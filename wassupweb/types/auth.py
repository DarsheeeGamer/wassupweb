from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

class KeyPair(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    public: bytes
    private: bytes


class SignedKeyPair(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    key_pair: KeyPair = Field(alias="keyPair")
    signature: bytes
    key_id: int = Field(alias="keyId")
    timestamp_s: int | None = Field(default=None, alias="timestampS")


class ProtocolAddress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    device_id: int = Field(alias="deviceId")


class SignalIdentity(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    identifier: ProtocolAddress | None = None
    name: str | None = None
    device_id: int | None = Field(default=None, alias="deviceId")
    identifier_key: bytes = Field(alias="identifierKey")

    @model_validator(mode="before")
    @classmethod
    def _coerce_identifier(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        result = dict(data)
        identifier = result.get("identifier")
        if isinstance(identifier, dict):
            if result.get("name") is None:
                result["name"] = identifier.get("name")
            if result.get("deviceId") is None and result.get("device_id") is None:
                result["deviceId"] = identifier.get("deviceId")
        elif result.get("name") is not None and (result.get("deviceId") is not None or result.get("device_id") is not None):
            device_id = result.get("deviceId", result.get("device_id"))
            result["identifier"] = {"name": result.get("name"), "deviceId": device_id}
        return result

    @model_validator(mode="after")
    def _normalize_identifier(self) -> "SignalIdentity":
        if self.identifier is None and self.name is not None and self.device_id is not None:
            self.identifier = ProtocolAddress(name=self.name, deviceId=self.device_id)
        if self.identifier is not None:
            self.name = self.identifier.name
            self.device_id = self.identifier.device_id
        return self


class LIDMapping(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    pn: str
    lid: str


class LTHashMapValue(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    value_mac: bytes = Field(alias="valueMac")


class LTHashState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    version: int
    hash: bytes
    index_value_map: dict[str, LTHashMapValue] = Field(default_factory=dict, alias="indexValueMap")


class SignalCreds(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    signed_identity_key: KeyPair = Field(alias="signedIdentityKey")
    signed_pre_key: SignedKeyPair = Field(alias="signedPreKey")
    registration_id: int = Field(alias="registrationId")


SignalDataSet = dict[str, dict[str, Any | None]]


@runtime_checkable
class SignalKeyStore(Protocol):
    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        ...

    async def set(self, data: SignalDataSet) -> None:
        ...

    async def clear(self) -> None:
        ...


@runtime_checkable
class SignalKeyStoreWithTransaction(SignalKeyStore, Protocol):
    def is_in_transaction(self) -> bool:
        ...

    async def transaction(self, exec_fn: Any, key: str) -> Any:
        ...


class AccountSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    unarchive_chats: bool = Field(default=False, alias="unarchiveChats")
    default_disappearing_mode: dict[str, Any] | None = Field(default=None, alias="defaultDisappearingMode")


class AuthenticationCreds(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    noise_key: KeyPair = Field(alias="noiseKey")
    pairing_ephemeral_key_pair: KeyPair = Field(alias="pairingEphemeralKeyPair")
    signed_identity_key: KeyPair = Field(alias="signedIdentityKey")
    signed_pre_key: SignedKeyPair = Field(alias="signedPreKey")
    registration_id: int = Field(alias="registrationId")
    adv_secret_key: str = Field(alias="advSecretKey")
    processed_history_messages: list[dict[str, Any]] = Field(default_factory=list, alias="processedHistoryMessages")
    next_pre_key_id: int = Field(default=1, alias="nextPreKeyId")
    first_unuploaded_pre_key_id: int = Field(default=1, alias="firstUnuploadedPreKeyId")
    account_sync_counter: int = Field(default=0, alias="accountSyncCounter")
    account_settings: AccountSettings = Field(default_factory=AccountSettings, alias="accountSettings")
    registered: bool = False
    pairing_code: str | None = Field(default=None, alias="pairingCode")
    last_prop_hash: str | None = Field(default=None, alias="lastPropHash")
    routing_info: bytes | None = Field(default=None, alias="routingInfo")
    additional_data: Any = None
    me: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    signal_identities: list[SignalIdentity] | None = Field(default=None, alias="signalIdentities")
    my_app_state_key_id: str | None = Field(default=None, alias="myAppStateKeyId")
    last_account_sync_timestamp: int | None = Field(default=None, alias="lastAccountSyncTimestamp")
    platform: str | None = None


class AuthenticationState(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    creds: AuthenticationCreds
    keys: SignalKeyStore


class SignalAuthState(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    creds: SignalCreds
    keys: SignalKeyStore | SignalKeyStoreWithTransaction
