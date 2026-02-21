from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticationState

WAVersion = tuple[int, int, int]
WABrowserDescription = tuple[str, str, str]


class TransactionCapabilityOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    max_commit_retries: int = Field(default=10, alias="maxCommitRetries")
    delay_between_tries_ms: int = Field(default=3000, alias="delayBetweenTriesMs")


class SocketConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    wa_websocket_url: str = Field(default="wss://web.whatsapp.com/ws/chat", alias="waWebSocketUrl")
    connect_timeout_ms: int = Field(default=20_000, alias="connectTimeoutMs")
    qr_timeout: int | None = Field(default=None, alias="qrTimeout")
    default_query_timeout_ms: int | None = Field(default=60_000, alias="defaultQueryTimeoutMs")
    keep_alive_interval_ms: int = Field(default=30_000, alias="keepAliveIntervalMs")
    mobile: bool | None = None
    agent: Any | None = None
    logger: Any | None = None
    version: WAVersion = (2, 3000, 1033105955)
    browser: WABrowserDescription = ("Mac OS", "Chrome", "14.4.1")
    fetch_agent: Any | None = Field(default=None, alias="fetchAgent")
    print_qr_in_terminal: bool = Field(default=False, alias="printQRInTerminal")
    emit_own_events: bool = Field(default=True, alias="emitOwnEvents")
    custom_upload_hosts: list[dict[str, Any]] = Field(default_factory=list, alias="customUploadHosts")
    media_cache: Any | None = Field(default=None, alias="mediaCache")
    msg_retry_counter_cache: Any | None = Field(default=None, alias="msgRetryCounterCache")
    user_devices_cache: Any | None = Field(default=None, alias="userDevicesCache")
    call_offer_cache: Any | None = Field(default=None, alias="callOfferCache")
    retry_request_delay_ms: int = Field(default=250, alias="retryRequestDelayMs")
    max_msg_retry_count: int = Field(default=5, alias="maxMsgRetryCount")
    auth: AuthenticationState | None = None
    auth_folder: str = Field(default="session", alias="authFolder")
    mark_online_on_connect: bool = Field(default=True, alias="markOnlineOnConnect")
    sync_full_history: bool = Field(default=True, alias="syncFullHistory")
    fire_init_queries: bool = Field(default=True, alias="fireInitQueries")
    link_preview_image_thumbnail_width: int = Field(default=192, alias="linkPreviewImageThumbnailWidth")
    generate_high_quality_link_preview: bool = Field(default=False, alias="generateHighQualityLinkPreview")
    enable_auto_session_recreation: bool = Field(default=True, alias="enableAutoSessionRecreation")
    enable_recent_message_cache: bool = Field(default=True, alias="enableRecentMessageCache")
    strict_noise_cert_validation: bool = Field(default=False, alias="strictNoiseCertValidation")
    country_code: str = Field(default="US", alias="countryCode")
    should_ignore_jid: Callable[[str], bool] = Field(default_factory=lambda: (lambda _: False), alias="shouldIgnoreJid")
    should_sync_history_message: Callable[[dict[str, Any]], bool] = Field(
        default_factory=lambda: (lambda msg: msg.get("syncType") not in {"FULL", 2}),
        alias="shouldSyncHistoryMessage",
    )
    patch_message_before_sending: Callable[[dict[str, Any], list[str] | None], Any] = Field(
        default_factory=lambda: (lambda msg, _=None: msg),
        alias="patchMessageBeforeSending",
    )
    get_message: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None
    cached_group_metadata: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None
    make_signal_repository: Callable[[AuthenticationState, Any, Any], Any] | None = Field(
        default=None,
        alias="makeSignalRepository",
    )
    options: dict[str, Any] = Field(default_factory=dict)
    app_state_mac_verification: dict[str, bool] = Field(
        default_factory=lambda: {"patch": False, "snapshot": False},
        alias="appStateMacVerification",
    )
    placeholder_resend_cache: Any | None = Field(default=None, alias="placeholderResendCache")
    transaction_opts: TransactionCapabilityOptions = Field(default_factory=TransactionCapabilityOptions, alias="transactionOpts")
