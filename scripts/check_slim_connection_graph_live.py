"""Integration check for slim_connection_graph compression.

Reads a raw `connections` payload as captured from the live backend (or
the existing MCP server's `get_asset_connections` tool) and replays it
through both the OLD and NEW slim implementations to confirm:

  1. `asset_type` is always present (never dropped).
  2. Both `null` and `""` `name` values drop out.
  3. Byte savings are measurable on real data.

Run from `ouro-mcp/`:
    python scripts/check_slim_connection_graph_live.py \\
        <path-to-payload.json> [--current-asset-id <uuid>]

The payload file may be either:
  - the raw list returned by `ouro.assets.connections(asset_id)`, or
  - the dict returned by `get_asset_connections` (we'll unwrap
    `connections` from it and rebuild the raw shape from the slim
    grouped form).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ouro_mcp.utils import dump_json, slim_connection_graph


def _old_slim_endpoint(node):
    """Pre-compression behavior — always emits `name` (even null) and `asset_type`."""
    if not isinstance(node, dict):
        return None
    aid = node.get("id")
    out = {
        "id": str(aid) if aid is not None else None,
        "name": node.get("name"),
        "asset_type": node.get("asset_type"),
    }
    if node.get("created_at") is not None:
        out["created_at"] = node["created_at"]
    return out


def _old_slim(connections, current_asset_id=None):
    if not isinstance(connections, list):
        return connections
    current_id = str(current_asset_id) if current_asset_id is not None else None
    grouped = {}
    for edge in connections:
        if not isinstance(edge, dict):
            grouped.setdefault("unknown", []).append({"value": edge})
            continue
        connection_type = str(edge.get("type") or "unknown")
        src = _old_slim_endpoint(edge.get("source"))
        tgt = _old_slim_endpoint(edge.get("target"))
        src_id = str(src["id"]) if src and src.get("id") is not None else None
        if current_id and src_id == current_id:
            row = tgt or {}
        else:
            row = src or {}
        grouped.setdefault(connection_type, []).append(row)
    return grouped


def _synthesize_raw_edges_from_grouped(grouped: dict, current_asset_id: str | None) -> list:
    """Reverse-engineer a list of edges from an already-slimmed `connections`
    block (e.g. the dict returned by the live MCP tool).

    The slimmed form only carries the *other* side of each edge, so we
    reconstruct a single-sided edge where `source = current_asset_id` and
    `target = <other side>`. That's enough for the slim function to
    re-derive the same grouped output — and to stress the new code path
    against real backend shapes (incl. `name: ""` for comments).
    """
    raw: list = []
    for connection_type, rows in grouped.items():
        for row in rows:
            edge = {
                "type": connection_type,
                "source": {"id": current_asset_id},
                "target": {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "asset_type": row.get("asset_type"),
                    "created_at": row.get("created_at"),
                },
            }
            raw.append(edge)
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", help="Path to JSON file with the connections payload")
    parser.add_argument(
        "--current-asset-id",
        default=None,
        help="UUID of the asset whose connections were fetched (used to pick the 'other' side)",
    )
    args = parser.parse_args()

    payload_path = Path(args.payload)
    payload = json.loads(payload_path.read_text())

    current_id = args.current_asset_id
    if isinstance(payload, dict):
        current_id = current_id or payload.get("asset_id")
        connections = payload.get("connections")
        if isinstance(connections, dict):
            raw_connections = _synthesize_raw_edges_from_grouped(connections, current_id)
        else:
            raw_connections = connections or []
    else:
        raw_connections = payload

    if not isinstance(raw_connections, list):
        print("Could not extract a list of edges from the payload.", file=sys.stderr)
        return 1

    old = _old_slim(raw_connections, current_asset_id=current_id)
    new = slim_connection_graph(raw_connections, current_asset_id=current_id)

    old_bytes = len(json.dumps(old))
    new_bytes = len(json.dumps(new))

    # Simulate the full tool response path: `get_asset_connections` returns
    # `dump_json({"asset_id": ..., "connections": <slim>})`, which routes
    # through `enrich_timestamps`. Measure the cumulative win (slim shape +
    # timestamp compression) by also serializing through dump_json with a
    # representative timezone configured.
    import os
    previous_tz = os.environ.get("OURO_MCP_TIMEZONE")
    os.environ["OURO_MCP_TIMEZONE"] = "America/Chicago"
    try:
        old_full = json.dumps({"asset_id": current_id, "connections": old})
        new_full = dump_json({"asset_id": current_id, "connections": new})
    finally:
        if previous_tz is None:
            os.environ.pop("OURO_MCP_TIMEZONE", None)
        else:
            os.environ["OURO_MCP_TIMEZONE"] = previous_tz

    old_full_bytes = len(old_full)
    new_full_bytes = len(new_full)

    print(f"current asset_id   : {current_id}")
    print(f"edge count         : {len(raw_connections)}")
    print(f"old slim payload   : {old_bytes:>6} bytes  (raw timestamps)")
    print(f"new slim payload   : {new_bytes:>6} bytes  (raw timestamps)")
    print(
        f"  slim-only saving : {old_bytes - new_bytes:>6} bytes "
        f"({(1 - new_bytes / old_bytes) * 100:.1f}%)"
    )
    print(
        f"old via dump_json  : {old_full_bytes:>6} bytes  (no compression — pre-change baseline)"
    )
    print(
        f"new via dump_json  : {new_full_bytes:>6} bytes  (slim + timestamp compression)"
    )
    print(
        f"  total saving     : {old_full_bytes - new_full_bytes:>6} bytes "
        f"({(1 - new_full_bytes / old_full_bytes) * 100:.1f}%)"
    )

    print("\nasset_type coverage check (must be present on every endpoint):")
    missing_type = []
    for ctype, rows in new.items():
        for row in rows:
            if "asset_type" not in row:
                missing_type.append((ctype, row.get("id")))
    if missing_type:
        print(f"  FAIL — {len(missing_type)} endpoints missing asset_type:")
        for ct, rid in missing_type[:5]:
            print(f"    {ct}: {rid}")
        return 1
    print(f"  OK — every endpoint has asset_type ({sum(len(v) for v in new.values())} total)")

    print("\nname dropping check (null/empty must drop, non-empty must stay):")
    name_kept = 0
    name_dropped = 0
    bad = []
    for ctype, rows in new.items():
        for row in rows:
            if "name" in row:
                name_kept += 1
                if not row["name"]:
                    bad.append((ctype, row))
            else:
                name_dropped += 1
    if bad:
        print(f"  FAIL — {len(bad)} rows kept a falsy name")
        for ct, row in bad[:5]:
            print(f"    {ct}: {row}")
        return 1
    print(f"  OK — name kept on {name_kept} rows, dropped from {name_dropped}")

    print("\nsample row (first edge, first type):")
    first_type = next(iter(new))
    print(f"  type={first_type!r}, row={new[first_type][0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
