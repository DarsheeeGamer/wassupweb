from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_generate_statics_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "tools" / "waproto" / "generate_statics.py"
    if str(module_path.parent) not in sys.path:
        sys.path.insert(0, str(module_path.parent))
    spec = importlib.util.spec_from_file_location("generate_statics_tool", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load generate_statics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_enum_normalization_inserts_zero_first_value() -> None:
    mod = _load_generate_statics_module()
    source = """syntax = "proto3";
enum DemoKind {
  FOO = 1;
  BAR = 2;
}
"""
    normalized = mod.ensure_python_compatible_enums(source)
    assert "PY_DEMOKIND_UNSPECIFIED = 0;" in normalized
    assert normalized.index("PY_DEMOKIND_UNSPECIFIED = 0;") < normalized.index("FOO = 1;")


def test_enum_normalization_keeps_existing_zero_value() -> None:
    mod = _load_generate_statics_module()
    source = """syntax = "proto3";
enum DemoKind {
  DEMO_KIND_UNSPECIFIED = 0;
  FOO = 1;
}
"""
    normalized = mod.ensure_python_compatible_enums(source)
    assert normalized.count("= 0;") == 1
