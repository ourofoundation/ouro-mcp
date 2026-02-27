# ouro-mcp

MCP server for the [Ouro](https://ouro.foundation) platform. Gives AI agents native access to Ouro's data, tools, and services through the [Model Context Protocol](https://modelcontextprotocol.io/).

## What can agents do with this?

- **Search and read** any public dataset, post, file, or service on Ouro
- **Query datasets** — pull structured data into agent context
- **Create content** — publish datasets, posts, and files programmatically
- **Discover and execute API routes** — call any user-published API on the platform
- **Delete assets** they own

## Tools (11)

| Tool | Description |
|---|---|
| `get_asset` | Get any asset by ID or name with type-appropriate detail |
| `search_assets` | Search datasets, posts, files, services, routes |
| `search_users` | Search for users by name or username |
| `delete_asset` | Delete an asset by ID |
| `query_dataset` | Query a dataset's rows as JSON with pagination |
| `create_dataset` | Create a dataset from JSON records |
| `update_dataset` | Update a dataset's data or metadata |
| `create_post` | Create a post from markdown |
| `update_post` | Update a post's content or metadata |
| `create_file` | Upload a file from a local path |
| `execute_route` | Execute any API route on Ouro |

## Setup

### 1. Get an API key

Generate a Personal Access Token at [ouro.foundation/settings/api-keys](https://ouro.foundation/settings/api-keys).

### 2. Install

```bash
pip install ouro-mcp
```

Or run directly with `uvx`:

```bash
uvx ouro-mcp
```

### 3. Configure your agent

#### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ouro": {
      "command": "uvx",
      "args": ["ouro-mcp"],
      "env": {
        "OURO_API_KEY": "your-api-key"
      }
    }
  }
}
```

#### Cursor

Add to your `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ouro": {
      "command": "uvx",
      "args": ["ouro-mcp"],
      "env": {
        "OURO_API_KEY": "your-api-key"
      }
    }
  }
}
```

#### Other MCP clients

Any MCP-compatible client works. The server defaults to `stdio` transport.

## Usage examples

Once connected, agents can interact with Ouro naturally:

> "Search for datasets about climate change"

> "Query the first 50 rows of dataset abc-123"

> "Create a post summarizing my analysis"

> "Find services that can generate embeddings, then execute one"

### Service discovery flow

The typical flow for discovering and using an API:

1. `search_assets(query="embeddings", asset_type="service")` — find services
2. `get_asset(service_id)` — see its routes
3. `get_asset(route_id)` — see parameter schema
4. `execute_route(route_id, body={...})` — call it

## Running in different modes

### Local (stdio) — default

```bash
OURO_API_KEY=your-key ouro-mcp
```

### Hosted (streamable HTTP)

```bash
OURO_API_KEY=your-key ouro-mcp --transport streamable-http --port 8000
```

### Against a local Ouro instance

Set these environment variables (or add them to `.env`) to point at your local dev setup:

```bash
OURO_API_KEY=your-local-key
OURO_BASE_URL=http://localhost:8003
OURO_DATABASE_URL=http://localhost:54321
OURO_DATABASE_ANON_KEY=your-local-anon-key
```

## Development

```bash
git clone https://github.com/ourofoundation/ouro-mcp.git
cd ouro-mcp
pip install -e .
```

Test with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

Then connect to `http://localhost:8000/mcp` if using streamable-http, or run via stdio.

## License

MIT
