from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

DEFAULT_GENERATED_MODULE = "wassupweb.waproto.generated.waproto_pb2"


@dataclass(slots=True)
class ProtoNamespace:
    module_name: str
    module: ModuleType | None = None
    load_error: Exception | None = None

    @property
    def loaded(self) -> bool:
        return self.module is not None

    def require(self) -> ModuleType:
        if self.module is None:
            reason = f" ({self.load_error})" if self.load_error else ""
            raise RuntimeError(
                "WAProto statics are not generated. "
                "Run tools/waproto/GenerateStatics.sh first."
                f"{reason}"
            )
        return self.module

    def has(self, name: str) -> bool:
        return self.module is not None and hasattr(self.module, name)

    def get(self, name: str, default: Any = None) -> Any:
        if self.module is None:
            return default
        return getattr(self.module, name, default)

    def __getattr__(self, name: str) -> Any:
        mod = self.require()
        return getattr(mod, name)


def load_proto(module_name: str = DEFAULT_GENERATED_MODULE) -> ProtoNamespace:
    try:
        module = importlib.import_module(module_name)
        return ProtoNamespace(module_name=module_name, module=module)
    except Exception as error:  # pragma: no cover - optional generated artifact
        return ProtoNamespace(module_name=module_name, load_error=error)


def proto_schema_path() -> Path:
    return Path(__file__).resolve().with_name("waproto.proto")


proto = load_proto()

# camelCase alias for parity
loadProto = load_proto


__all__ = [
    "DEFAULT_GENERATED_MODULE",
    "ProtoNamespace",
    "load_proto",
    "loadProto",
    "proto_schema_path",
    "proto",
]
