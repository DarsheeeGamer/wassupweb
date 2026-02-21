#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python "$ROOT/tools/waproto/generate_statics.py" \
  --proto "$ROOT/wassupweb/waproto/waproto.proto" \
  --out "$ROOT/wassupweb/waproto/generated"
