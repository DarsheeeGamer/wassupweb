from __future__ import annotations

import importlib


def test_baileys_style_compat_module_paths_import() -> None:
    modules = [
        "wassupweb.Defaults",
        "wassupweb.Defaults.index",
        "wassupweb.Socket",
        "wassupweb.Socket.Client",
        "wassupweb.Socket.Business",
        "wassupweb.Socket.Chats",
        "wassupweb.Socket.Communities",
        "wassupweb.Socket.Groups",
        "wassupweb.Socket.MessagesRecv",
        "wassupweb.Socket.MessagesSend",
        "wassupweb.Socket.Mex",
        "wassupweb.Socket.Newsletter",
        "wassupweb.Socket.Core",
        "wassupweb.Socket.Transport",
        "wassupweb.Socket.Factory",
        "wassupweb.Signal",
        "wassupweb.Signal.Group",
        "wassupweb.Signal.Group.index",
        "wassupweb.Types",
        "wassupweb.Types.Auth",
        "wassupweb.Types.Business",
        "wassupweb.Types.Bussines",
        "wassupweb.Types.Call",
        "wassupweb.Types.Chat",
        "wassupweb.Types.Community",
        "wassupweb.Types.Contact",
        "wassupweb.Types.Events",
        "wassupweb.Types.GroupMetadata",
        "wassupweb.Types.Label",
        "wassupweb.Types.LabelAssociation",
        "wassupweb.Types.Message",
        "wassupweb.Types.Newsletter",
        "wassupweb.Types.Product",
        "wassupweb.Types.Signal",
        "wassupweb.Types.Socket",
        "wassupweb.Types.State",
        "wassupweb.Types.USync",
        "wassupweb.Utils",
        "wassupweb.WABinary",
        "wassupweb.WAM",
        "wassupweb.WAProto",
        "wassupweb.WAUSync",
        "wassupweb.WAUSync.Protocols",
        "wassupweb.WAUSync.Protocols.index",
        "wassupweb.signal.group.index",
        "wassupweb.types.index",
        "wassupweb.types.Auth",
        "wassupweb.types.Business",
        "wassupweb.types.Bussines",
        "wassupweb.types.Community",
        "wassupweb.types.Call",
        "wassupweb.types.Chat",
        "wassupweb.types.Contact",
        "wassupweb.types.Events",
        "wassupweb.types.Label",
        "wassupweb.types.Message",
        "wassupweb.types.Newsletter",
        "wassupweb.types.Product",
        "wassupweb.types.Signal",
        "wassupweb.types.Socket",
        "wassupweb.types.State",
        "wassupweb.types.USync",
        "wassupweb.wabinary.index",
        "wassupweb.wam.index",
        "wassupweb.wam.BinaryInfo",
        "wassupweb.wausync.index",
        "wassupweb.wausync.protocols.index",
        "wassupweb.types.GroupMetadata",
        "wassupweb.types.LabelAssociation",
    ]
    for module_name in modules:
        assert importlib.import_module(module_name) is not None


def test_compat_shims_expose_expected_symbols() -> None:
    binary_info_mod = importlib.import_module("wassupweb.wam.BinaryInfo")
    group_metadata_mod = importlib.import_module("wassupweb.types.GroupMetadata")
    label_assoc_mod = importlib.import_module("wassupweb.types.LabelAssociation")
    bussines_mod = importlib.import_module("wassupweb.types.Bussines")
    community_types_mod = importlib.import_module("wassupweb.types.Community")
    root_community_types_mod = importlib.import_module("wassupweb.Types.Community")
    root_call_types_mod = importlib.import_module("wassupweb.Types.Call")
    root_newsletter_types_mod = importlib.import_module("wassupweb.Types.Newsletter")
    socket_messages_send_mod = importlib.import_module("wassupweb.Socket.MessagesSend")
    usync_mod = importlib.import_module("wassupweb.wausync.index")
    signal_mod = importlib.import_module("wassupweb.signal")
    signal_group_mod = importlib.import_module("wassupweb.signal.group")

    assert hasattr(binary_info_mod, "BinaryInfo")
    assert hasattr(group_metadata_mod, "GroupMetadata")
    assert hasattr(label_assoc_mod, "LabelAssociation")
    assert hasattr(bussines_mod, "UpdateBusinessProfileProps")
    assert hasattr(community_types_mod, "CommunityCreateInput")
    assert hasattr(root_community_types_mod, "CommunityCreateInput")
    assert hasattr(root_call_types_mod, "WACallEvent")
    assert hasattr(root_newsletter_types_mod, "NewsletterCreateInput")
    assert hasattr(socket_messages_send_mod, "MessagesSendSocket")
    assert hasattr(usync_mod, "USyncQuery")
    assert hasattr(signal_mod, "jidToSignalProtocolAddress")
    assert hasattr(signal_mod, "jidToSignalSenderKeyName")
    assert hasattr(signal_group_mod, "generateSenderKey")
