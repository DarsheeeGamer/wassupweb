from __future__ import annotations

import re
import sys
from pathlib import Path


def fix_imports(path: Path) -> bool:
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")
    updated = content

    # Normalize sibling generated-module imports to package-relative form.
    updated = re.sub(
        r"(^|\n)import (\w+_pb2) as (\w+__pb2)",
        r"\1from . import \2 as \3",
        updated,
    )

    if updated != content:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> int:
    target = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("wassupweb/waproto/generated/waproto_pb2.py")
    )
    changed = fix_imports(target)
    state = "updated" if changed else "no changes"
    print(f"[waproto] {state}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
