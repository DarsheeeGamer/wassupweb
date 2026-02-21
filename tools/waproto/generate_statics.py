from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from .fix_imports import fix_imports
except ImportError:  # pragma: no cover - script execution path
    from fix_imports import fix_imports


_ENUM_LINE = r"^\s*([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;"


def ensure_python_compatible_enums(proto_text: str) -> str:
    import re

    lines = proto_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    enum_decl = re.compile(r"^(\s*)enum\s+([A-Za-z_]\w*)\s*\{\s*$")
    enum_value = re.compile(_ENUM_LINE)

    while i < len(lines):
        line = lines[i]
        match = enum_decl.match(line)
        if not match:
            out.append(line)
            i += 1
            continue

        enum_indent = match.group(1)
        enum_name = match.group(2)

        block: list[str] = [line]
        i += 1
        depth = 1
        while i < len(lines) and depth > 0:
            current = lines[i]
            depth += current.count("{")
            depth -= current.count("}")
            block.append(current)
            i += 1

        if len(block) < 3:
            out.extend(block)
            continue

        body = block[1:-1]
        first_value_idx: int | None = None
        first_value: int | None = None
        enum_names: set[str] = set()

        for idx, body_line in enumerate(body):
            value_match = enum_value.match(body_line.strip())
            if not value_match:
                continue
            enum_names.add(value_match.group(1))
            if first_value_idx is None:
                first_value_idx = idx
                first_value = int(value_match.group(2))

        if first_value_idx is not None and first_value != 0:
            placeholder = f"PY_{enum_name.upper()}_UNSPECIFIED"
            if placeholder in enum_names:
                suffix = 1
                while f"{placeholder}_{suffix}" in enum_names:
                    suffix += 1
                placeholder = f"{placeholder}_{suffix}"
            indent_match = re.match(r"^(\s*)", body[first_value_idx])
            entry_indent = indent_match.group(1) if indent_match else f"{enum_indent}    "
            body.insert(first_value_idx, f"{entry_indent}{placeholder} = 0;\n")

        out.append(block[0])
        out.extend(body)
        out.append(block[-1])

    return "".join(out)


def generate_statics(proto_path: Path, out_dir: Path) -> Path:
    source = proto_path.read_text(encoding="utf-8")
    normalized = ensure_python_compatible_enums(source)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "__init__.py").touch(exist_ok=True)

    build_dir = out_dir / ".tmp_proto_build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    try:
        tmp_proto = build_dir / proto_path.name
        tmp_proto.write_text(normalized, encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            "--experimental_allow_proto3_optional",
            f"-I{build_dir}",
            f"--python_out={out_dir}",
            str(tmp_proto),
        ]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    generated = out_dir / f"{proto_path.stem}_pb2.py"
    fix_imports(generated)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Python WAProto statics compatible with grpc_tools.protoc.")
    parser.add_argument(
        "--proto",
        default="wassupweb/waproto/waproto.proto",
        help="Path to source WAProto file.",
    )
    parser.add_argument(
        "--out",
        default="wassupweb/waproto/generated",
        help="Output directory for generated pb2 module.",
    )
    args = parser.parse_args()

    proto_path = Path(args.proto).resolve()
    out_dir = Path(args.out).resolve()
    generated = generate_statics(proto_path, out_dir)
    print(f"[waproto] generated: {generated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
