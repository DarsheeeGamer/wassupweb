from __future__ import annotations

import re

from ..utils.browser_utils import Browsers
from ..utils.logger import logger

VERSION = (2, 3000, 1033105955)

UNAUTHORIZED_CODES = (401, 403, 419)
DEFAULT_ORIGIN = "https://web.whatsapp.com"
CALL_VIDEO_PREFIX = "https://call.whatsapp.com/video/"
CALL_AUDIO_PREFIX = "https://call.whatsapp.com/voice/"
DEF_CALLBACK_PREFIX = "CB:"
DEF_TAG_PREFIX = "TAG:"
PHONE_CONNECTION_CB = "CB:Pong"
S_WHATSAPP_NET = "s.whatsapp.net"

WA_ADV_ACCOUNT_SIG_PREFIX = bytes([6, 0])
WA_ADV_DEVICE_SIG_PREFIX = bytes([6, 1])
WA_ADV_HOSTED_ACCOUNT_SIG_PREFIX = bytes([6, 5])
WA_ADV_HOSTED_DEVICE_SIG_PREFIX = bytes([6, 6])

WA_DEFAULT_EPHEMERAL = 7 * 24 * 60 * 60
STATUS_EXPIRY_SECONDS = 24 * 60 * 60
PLACEHOLDER_MAX_AGE_SECONDS = 14 * 24 * 60 * 60

NOISE_MODE = "Noise_XX_25519_AESGCM_SHA256\0\0\0\0"
DICT_VERSION = 3
KEY_BUNDLE_TYPE = bytes([5])
NOISE_WA_HEADER = bytes([87, 65, 6, DICT_VERSION])
URL_REGEX = re.compile(
    r"https://(?![^:@/\s]+:[^:@/\s]+@)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(:\d+)?(/[^\s]*)?",
    re.IGNORECASE,
)
WA_CERT_DETAILS = {
    "SERIAL": 0,
    "ISSUER": "WhatsAppLongTerm1",
    "PUBLIC_KEY": bytes.fromhex("142375574d0a587166aae71ebe516437c4a28b73e3695c6ce1f7f9545da8ee6b"),
}

PROCESSABLE_HISTORY_TYPES = (
    "INITIAL_BOOTSTRAP",
    "PUSH_NAME",
    "RECENT",
    "FULL",
    "ON_DEMAND",
    "NON_BLOCKING_DATA",
    "INITIAL_STATUS_V3",
    0,
    1,
    2,
    3,
    4,
    5,
    6,
)

MEDIA_PATH_MAP: dict[str, str] = {
    "image": "/mms/image",
    "video": "/mms/video",
    "document": "/mms/document",
    "audio": "/mms/audio",
    "sticker": "/mms/image",
    "thumbnail-link": "/mms/image",
    "product-catalog-image": "/product/image",
    "md-app-state": "",
    "md-msg-hist": "/mms/md-app-state",
    "biz-cover-photo": "/pps/biz-cover-photo",
}

MEDIA_HKDF_KEY_MAPPING: dict[str, str] = {
    "audio": "Audio",
    "document": "Document",
    "gif": "Video",
    "image": "Image",
    "ppic": "",
    "product": "Image",
    "ptt": "Audio",
    "sticker": "Image",
    "video": "Video",
    "thumbnail-document": "Document Thumbnail",
    "thumbnail-image": "Image Thumbnail",
    "thumbnail-video": "Video Thumbnail",
    "thumbnail-link": "Link Thumbnail",
    "md-msg-hist": "History",
    "md-app-state": "App State",
    "product-catalog-image": "",
    "payment-bg-image": "Payment Background",
    "ptv": "Video",
    "biz-cover-photo": "Image",
}

MEDIA_KEYS = tuple(MEDIA_PATH_MAP.keys())

MIN_PREKEY_COUNT = 5
INITIAL_PREKEY_COUNT = 812
UPLOAD_TIMEOUT = 30_000
MIN_UPLOAD_INTERVAL = 5_000

DEFAULT_CACHE_TTLS = {
    "signal_store": 5 * 60,
    "msg_retry": 60 * 60,
    "call_offer": 5 * 60,
    "user_devices": 5 * 60,
}

TIME_MS = {
    "minute": 60 * 1000,
    "hour": 60 * 60 * 1000,
    "day": 24 * 60 * 60 * 1000,
    "week": 7 * 24 * 60 * 60 * 1000,
}


def _make_signal_repository(*args: object, **kwargs: object) -> object:
    from ..signal.libsignal import make_libsignal_repository

    return make_libsignal_repository(*args, **kwargs)

DEFAULT_CONNECTION_CONFIG: dict[str, object] = {
    "version": VERSION,
    "browser": Browsers.macos("Chrome"),
    "wa_websocket_url": "wss://web.whatsapp.com/ws/chat",
    "connect_timeout_ms": 20_000,
    "keep_alive_interval_ms": 30_000,
    "mobile": False,
    "agent": None,
    "logger": logger.child({"class": "wassupweb"}),
    "emit_own_events": True,
    "default_query_timeout_ms": 60_000,
    "fetch_agent": None,
    "print_qr_in_terminal": False,
    "custom_upload_hosts": [],
    "retry_request_delay_ms": 250,
    "max_msg_retry_count": 5,
    "fire_init_queries": True,
    "auth": None,
    "auth_folder": "session",
    "mark_online_on_connect": True,
    "sync_full_history": True,
    "link_preview_image_thumbnail_width": 192,
    "generate_high_quality_link_preview": False,
    "enable_auto_session_recreation": True,
    "enable_recent_message_cache": True,
    "strict_noise_cert_validation": False,
    "country_code": "US",
    "patch_message_before_sending": lambda msg, _=None: msg,
    "should_sync_history_message": lambda msg: msg.get("syncType") not in {"FULL", 2},
    "should_ignore_jid": lambda _jid: False,
    "transaction_opts": {"max_commit_retries": 10, "delay_between_tries_ms": 3000},
    "app_state_mac_verification": {"patch": False, "snapshot": False},
    "placeholder_resend_cache": None,
    "options": {},
    "get_message": None,
    "cached_group_metadata": None,
    "make_signal_repository": _make_signal_repository,
}
