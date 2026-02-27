# Local Testing & Debugging

## Prerequisites

- pyenv environment: `ouro` (Python 3.12)
- ouro-mcp installed in editable mode: `pip install -e .`
- Local backend running on `:8003`
- Local Supabase running on `:54321`
- `.env` configured with local credentials (see `.env.example`)

## Activating the environment

```bash
eval "$(pyenv init -)"
pyenv shell ouro
```

## MCP Inspector (interactive testing)

The fastest way to test tools interactively:

```bash
cd /path/to/ouro-mcp
npx @modelcontextprotocol/inspector ouro-mcp
```

Opens a web UI (default `http://localhost:6274`) where you can connect, browse all tools, fill in parameters, and execute them. The proxy runs on `:6277`.

If ports are stuck from a previous session:

```bash
lsof -ti :6277 -ti :6274 | xargs kill -9
```

## Testing with Python directly

Useful for isolating SDK vs backend issues:

```python
import os, json, httpx
from dotenv import load_dotenv
load_dotenv(override=True)

base_url = os.environ["OURO_BASE_URL"]
api_key = os.environ["OURO_API_KEY"]

# Step 1: Exchange API key for JWT
client = httpx.Client(base_url=base_url, timeout=10)
resp = client.post("/users/get-token", json={"pat": api_key})
access_token = resp.json()["access_token"]

# Step 2: Call any backend endpoint with the JWT
resp = client.get(
    "/users/search",
    params={"query": "matt"},
    headers={"Authorization": access_token},
)
print(resp.status_code, resp.json())
```

## Auth notes

- ouro-py exchanges the API key (hex PAT) for a Supabase JWT via `POST /users/get-token`
- The JWT is sent as `Authorization: <token>` (no `Bearer` prefix)
- The backend's `createRouteClient` adds `Bearer` when creating the Supabase client, so sending `Bearer` from the SDK would cause double-wrapping (`Bearer Bearer <token>`) and a JWT error

## Common issues

**Hanging on Ouro() init**
The constructor does token exchange + Supabase session setup synchronously. If the backend or Supabase is down, it hangs. Check that `:8003` and `:54321` are reachable first:

```bash
curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 http://localhost:8003/
curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 http://127.0.0.1:54321/rest/v1/
```

**"JWT cryptographic operation failed"**
Usually means the token is malformed. If you're passing auth manually via curl, use the exchanged JWT, not the raw API key.
