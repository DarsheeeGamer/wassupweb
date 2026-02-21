from __future__ import annotations

import wassupweb.utils as utils


def test_utils_index_re_exports_core_baileys_surface() -> None:
    required = [
        "generate_message_id_v2",  # generics
        "decode_message_node",  # decode-wa-message
        "generate_wa_message",  # messages
        "download_content_from_message",  # messages-media
        "generate_login_node",  # validate-connection
        "aes_encrypt",  # crypto
        "make_noise_handler",  # noise-handler
        "process_history_message",  # history
        "chat_modification_to_app_patch",  # chat-utils
        "new_lthash_state",  # lt-hash/chat-utils combo entrypoint
        "init_auth_creds",  # auth-utils
        "use_multi_file_auth_state",  # use-multi-file-auth-state
        "get_url_info",  # link-preview
        "make_event_buffer",  # event-buffer
        "process_message",  # process-message
        "MessageRetryManager",  # message-retry-manager
        "Browsers",  # browser-utils
        "handle_identity_change",  # identity-change-handler
    ]
    for symbol in required:
        assert hasattr(utils, symbol), symbol


def test_utils_index_re_exports_camel_case_aliases() -> None:
    required = [
        "generateLoginNode",
        "generateRegistrationNode",
        "configureSuccessfulPairing",
        "encodeSyncdPatch",
        "decodePatches",
        "chatModificationToAppPatch",
        "processSyncAction",
    ]
    for symbol in required:
        assert hasattr(utils, symbol), symbol


def test_utils_star_import_exposes_baileys_surface_symbols() -> None:
    ns: dict[str, object] = {}
    exec("from wassupweb.utils import *", {}, ns)
    assert "generate_login_node" in ns
    assert "chat_modification_to_app_patch" in ns
    assert "processSyncAction" in ns
