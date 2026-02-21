import importlib
import sys

from .index import *  # noqa: F401,F403
from .signal import *  # noqa: F401,F403
from .wam import *  # noqa: F401,F403
from .waproto import *  # noqa: F401,F403
from .wausync import *  # noqa: F401,F403

_ROOT_COMPAT_MODULE_ALIASES = {
    "wassupweb.Defaults": "wassupweb.defaults",
    "wassupweb.Socket": "wassupweb.socket",
    "wassupweb.Socket.Client": "wassupweb.socket.client",
    "wassupweb.Socket.Business": "wassupweb.socket.business",
    "wassupweb.Socket.Chats": "wassupweb.socket.chats",
    "wassupweb.Socket.Communities": "wassupweb.socket.communities",
    "wassupweb.Socket.Groups": "wassupweb.socket.groups",
    "wassupweb.Socket.MessagesRecv": "wassupweb.socket.messages_recv",
    "wassupweb.Socket.MessagesSend": "wassupweb.socket.messages_send",
    "wassupweb.Socket.Mex": "wassupweb.socket.mex",
    "wassupweb.Socket.Newsletter": "wassupweb.socket.newsletter",
    "wassupweb.Socket.Core": "wassupweb.socket.socket",
    "wassupweb.Socket.Transport": "wassupweb.socket.transport",
    "wassupweb.Socket.Factory": "wassupweb.socket.factory",
    "wassupweb.Signal": "wassupweb.signal",
    "wassupweb.Signal.Group": "wassupweb.signal.group",
    "wassupweb.Types": "wassupweb.types",
    "wassupweb.Types.Auth": "wassupweb.types.Auth",
    "wassupweb.Types.Business": "wassupweb.types.Business",
    "wassupweb.Types.Bussines": "wassupweb.types.Bussines",
    "wassupweb.Types.Call": "wassupweb.types.Call",
    "wassupweb.Types.Chat": "wassupweb.types.Chat",
    "wassupweb.Types.Community": "wassupweb.types.Community",
    "wassupweb.Types.Contact": "wassupweb.types.Contact",
    "wassupweb.Types.Events": "wassupweb.types.Events",
    "wassupweb.Types.GroupMetadata": "wassupweb.types.GroupMetadata",
    "wassupweb.Types.Label": "wassupweb.types.Label",
    "wassupweb.Types.LabelAssociation": "wassupweb.types.LabelAssociation",
    "wassupweb.Types.Message": "wassupweb.types.Message",
    "wassupweb.Types.Newsletter": "wassupweb.types.Newsletter",
    "wassupweb.Types.Product": "wassupweb.types.Product",
    "wassupweb.Types.Signal": "wassupweb.types.Signal",
    "wassupweb.Types.Socket": "wassupweb.types.Socket",
    "wassupweb.Types.State": "wassupweb.types.State",
    "wassupweb.Types.USync": "wassupweb.types.USync",
    "wassupweb.Utils": "wassupweb.utils",
    "wassupweb.WABinary": "wassupweb.wabinary",
    "wassupweb.WAM": "wassupweb.wam",
    "wassupweb.WAProto": "wassupweb.waproto",
    "wassupweb.WAUSync": "wassupweb.wausync",
    "wassupweb.WAUSync.Protocols": "wassupweb.wausync.protocols",
}
for _alias, _target in _ROOT_COMPAT_MODULE_ALIASES.items():
    try:
        _module = importlib.import_module(_target)
    except Exception:
        continue
    sys.modules.setdefault(_alias, _module)
