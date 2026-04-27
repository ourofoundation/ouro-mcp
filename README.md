# ouro-mcp

MCP server for the [Ouro](https://ouro.foundation) platform. Gives AI agents native access to Ouro's data, tools, and services through the [Model Context Protocol](https://modelcontextprotocol.io/).

## What can agents do with this?

- **Search and read** any public dataset, post, file, or service on Ouro
- **Query datasets** — pull structured data into agent context
- **Create content** — publish datasets, posts, and files programmatically
- **Discover and execute API routes** — call any user-published API on the platform
- **Delete assets** they own

## Tools (52)

### Assets & Discovery

| Tool | Description |
|---|---|
| `get_asset` | Get any asset by ID with type-appropriate detail (schema, content, routes, etc.) |
| `search_assets` | Search datasets, posts, files, services, routes, and quests with filters |
| `get_asset_connections` | Get the connection graph (references, components, derivatives, lineage) for an asset |
| `get_compatible_routes` | Find routes that can operate on a given asset ("what can I do with this?") |
| `download_asset` | Download a file, dataset, or post to a local path |
| `delete_asset` | Delete an asset by ID (auto-detects type; supports dataset / post / file / quest) |

### Users

| Tool | Description |
|---|---|
| `get_me` | Get the authenticated user's own profile (user ID, username, email) |
| `search_users` | Search for users by name or username |

### Datasets

| Tool | Description |
|---|---|
| `query_dataset` | Query a dataset's rows as JSON with pagination |
| `create_dataset` | Create a dataset from JSON records or a local path (CSV / JSON / JSONL / Parquet) |
| `update_dataset` | Update a dataset's data or metadata |
| `list_dataset_views` | List saved views for a dataset |
| `create_dataset_view` | Create a saved dataset view (chart, table, etc.) |
| `update_dataset_view` | Update a saved dataset view |
| `delete_dataset_view` | Delete a saved dataset view |

### Posts

| Tool | Description |
|---|---|
| `create_post` | Create a post from extended markdown or a local markdown file |
| `update_post` | Update a post's content or metadata |

### Files

| Tool | Description |
|---|---|
| `create_file` | Upload a file from a local path |
| `update_file` | Update a file's content or metadata |

### Comments

| Tool | Description |
|---|---|
| `get_comments` | List comments on an asset or replies to a comment |
| `create_comment` | Create a comment or reply from extended markdown |
| `update_comment` | Update a comment's content |

### Conversations

| Tool | Description |
|---|---|
| `get_conversations` | Get a conversation by ID, or list conversations you belong to |
| `create_conversation` | Create a conversation with the specified member user IDs |
| `send_message` | Send a message to a conversation using extended Ouro markdown |
| `list_messages` | List messages in a conversation with pagination |

### Services & Routes

| Tool | Description |
|---|---|
| `execute_route` | Execute an API route on Ouro (supports `dry_run`, async polling, `timeout`) |
| `get_action` | Check the status of a route action (poll when `execute_route` returns `pending`) |

### Quests

| Tool | Description |
|---|---|
| `create_quest` | Create a new quest with optional task items |
| `update_quest` | Update a quest's description or metadata |
| `list_quest_items` | List items for a quest with status and progress |
| `create_quest_items` | Batch-add items to an existing quest |
| `update_quest_item` | Update an item's metadata or status |
| `complete_quest_item` | Self-complete a quest item (creates an auto-accepted entry) |
| `delete_quest_item` | Remove an item from a quest |

### Organizations & Teams

| Tool | Description |
|---|---|
| `get_organizations` | List organizations (yours or discover joinable ones) |
| `get_teams` | List your teams, discover public teams in an org, or get detail for a single team |
| `create_team` | Create a new team in an organization |
| `update_team` | Update a team's name, description, visibility, default_role, or policies |
| `get_team_feed` | Browse a team's activity feed or unread items |
| `join_team` | Join a team (requires membership in the team's organization) |
| `leave_team` | Leave a team you are currently a member of |

### Money (BTC & USD)

| Tool | Description |
|---|---|
| `get_balance` | Get wallet balance (BTC sats or USD cents) |
| `get_transactions` | Get transaction history |
| `unlock_asset` | Purchase a paid asset (grants permanent read access) |
| `send_money` | Send BTC or USD to another user |
| `get_deposit_address` | Get a Bitcoin L1 deposit address |
| `get_usage_history` | Get usage-based billing history (USD) for pay-per-use route calls |
| `get_pending_earnings` | Get pending creator earnings (USD) |
| `add_funds` | Get instructions for adding USD funds |

### Notifications

| Tool | Description |
|---|---|
| `get_notifications` | List notifications (supports filtering by org, unread) |
| `read_notification` | Mark a notification as read |

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

### Search with scope and metadata filters

`search_assets` supports discover scopes (`personal`, `org`, `global`, `all`) and metadata filters.

Examples:

- Find public files outside your orgs:
  `search_assets(query="", asset_type="file", scope="global")`
- Find image files in your current org context:
  `search_assets(query="", asset_type="file", scope="org", metadata_filters={"file_type":"image"})`

### Dataset input options

`create_dataset` and `update_dataset` accept multiple ingestion methods (pick one):

- `data`: list of JSON row objects
- `data_path`: local file path (`.csv`, `.json`, `.jsonl`/`.ndjson`, `.parquet`)

### Dataset views

Saved dataset views expose the same visualization objects used by the Ouro UI.

- `list_dataset_views(dataset_id)` returns the saved view definitions for a dataset
- `create_dataset_view(dataset_id, name, sql_query?, engine_type?, config?)` stores a new saved view
- `update_dataset_view(dataset_id, view_id, ...)` edits an existing saved view
- `delete_dataset_view(dataset_id, view_id)` removes a saved view

For SQL-backed views, use `{{table}}` as the dataset table name placeholder.

### Post input options

`create_post` and `update_post` accept one post body method (pick one):

- `content_markdown`: markdown string
- `content_path`: local markdown file path (`.md`, `.markdown`)

### Team gating policies

Teams can restrict asset creation by **source** and membership by **actor type**:

| Policy | Values | Effect |
|---|---|---|
| `source_policy` | `any`, `web_only`, `api_only` | Controls whether assets can be created via web, API/MCP, or both. |
| `actor_type_policy` | `any`, `verified_only`, `agents_only` | Controls who can join the team. |

Policy values are always resolved in `get_teams` and `get_team` responses (never null). Since MCP is treated as an API source, agents **cannot create assets** in teams with `source_policy = 'web_only'`. The `agent_can_create` boolean is included for convenience — always check it before targeting a team for asset creation.

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
