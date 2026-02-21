from __future__ import annotations

import importlib

from .browser_utils import Browsers, get_platform_id
from .event_bus import EventBus
from .identity import IdentityResolver, link_pn_lid, resolve_user
from .logger import get_logger, logger

_LAZY_EXPORTS: dict[str, object] | None = None


def _load_index_exports() -> dict[str, object]:
    global _LAZY_EXPORTS
    if _LAZY_EXPORTS is None:
        _index = importlib.import_module("wassupweb.utils.index")

        _LAZY_EXPORTS = {name: getattr(_index, name) for name in dir(_index) if not name.startswith("_")}
    return _LAZY_EXPORTS


def __getattr__(name: str) -> object:
    exports = _load_index_exports()
    if name in exports:
        return exports[name]
    raise AttributeError(name)


def __dir__() -> list[str]:
    names = {
        "Browsers",
        "get_platform_id",
        "EventBus",
        "IdentityResolver",
        "resolve_user",
        "link_pn_lid",
        "get_logger",
        "logger",
    }
    names.update(_load_index_exports().keys())
    return sorted(names)


__all__ = [
    "Browsers",
    "get_platform_id",
    "EventBus",
    "IdentityResolver",
    "resolve_user",
    "link_pn_lid",
    "get_logger",
    "logger",
    # Common Baileys-style utils export surface from utils/index
    "generate_message_id_v2",
    "decode_message_node",
    "generate_wa_message",
    "download_content_from_message",
    "generate_login_node",
    "generate_registration_node",
    "configure_successful_pairing",
    "aes_encrypt",
    "make_noise_handler",
    "process_history_message",
    "chat_modification_to_app_patch",
    "new_lthash_state",
    "init_auth_creds",
    "use_multi_file_auth_state",
    "get_url_info",
    "make_event_buffer",
    "process_message",
    "MessageRetryManager",
    "handle_identity_change",
    # camelCase aliases
    "generateLoginNode",
    "generateRegistrationNode",
    "configureSuccessfulPairing",
    "encodeSyncdPatch",
    "decodePatches",
    "chatModificationToAppPatch",
    "processSyncAction",
]
