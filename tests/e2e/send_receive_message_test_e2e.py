from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from wassupweb.utils.use_multi_file_auth_state import use_multi_file_auth_state


@pytest.mark.asyncio
async def test_send_receive_message_real_env_harness() -> None:
    if os.getenv("WASSUPWEB_RUN_REAL_E2E") != "1":
        pytest.skip("Real WhatsApp e2e is disabled. Set WASSUPWEB_RUN_REAL_E2E=1 to enable.")

    root = Path(tempfile.mkdtemp(prefix="wassupweb-e2e-auth-"))
    state, _save_creds = await use_multi_file_auth_state(str(root))
    try:
        assert state.creds is not None
        assert state.keys is not None
    finally:
        shutil.rmtree(root, ignore_errors=True)

    pytest.skip("Real send/receive e2e flow requires a manually paired account and live WhatsApp environment.")
