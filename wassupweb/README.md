# wassupweb

`wassupweb` is a Python-first reimplementation scaffold of Baileys with:

- a centralized facade (`App` / `WassupWeb`)
- simplified names (`create_client`, `Client`, `Config`)
- pluggable ABC contracts (transport, event bus, plugins)
- Pydantic models for config/auth data structures

## Quick start

```python
from wassupweb import App, Config

app = App()
cfg = Config(waWebSocketUrl="wss://web.whatsapp.com/ws/chat")
client = app.make_socket(cfg)
```

## Simple aliases

```python
from wassupweb import create_client, new_client, Client, Config, Creds, AuthState
```

## Identity helpers

```python
from wassupweb import IdentityResolver
from wassupweb.types import SendTextInput

ids = IdentityResolver()
ref = ids.resolve("+15551234567").ref
# ref.user_id -> stable user-level ID, independent of raw JID format

# with a socket instance:
# await client.send_text(SendTextInput(to=ref.user_id, text="hello"))
```

## Auth state

```python
import asyncio
from wassupweb import use_multi_file_auth_state

async def main() -> None:
    state, save_creds = await use_multi_file_auth_state("./auth")
    await save_creds()

asyncio.run(main())
```
