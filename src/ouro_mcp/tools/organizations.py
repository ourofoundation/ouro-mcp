"""Organization tools — list and discover organizations."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from mcp.server.fastmcp import Context, FastMCP

from ouro_mcp.errors import handle_ouro_errors
from ouro_mcp.utils import markdown_bullet, markdown_id, render_markdown_list


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    @handle_ouro_errors
    def get_organizations(
        ctx: Context,
        discover: Annotated[bool, Field(description="Browse discoverable orgs you could join")] = False,
    ) -> str:
        """List organizations.

        By default, returns the organizations you belong to with your role and membership info.
        Set discover=True to browse discoverable organizations you could join.
        """
        ouro = ctx.request_context.lifespan_context.ouro

        if discover:
            orgs = ouro.organizations.list_discoverable()
        else:
            orgs = ouro.organizations.list()

        results = []
        for org in orgs:
            entry = {
                "id": str(org.get("id", "")),
                "name": org.get("name"),
                "display_name": org.get("display_name"),
                "mission": org.get("mission"),
                "join_policy": org.get("join_policy"),
            }
            if not discover:
                membership = org.get("membership", {})
                if membership:
                    entry["role"] = membership.get("role")
                    entry["membership_type"] = membership.get("membership_type")
            results.append(entry)

        def _org_line(row: dict) -> str:
            parts = [markdown_id(row.get("id"))]
            if row.get("display_name") and row.get("display_name") != row.get("name"):
                parts.append(str(row["display_name"]))
            if row.get("join_policy"):
                parts.append(f"join: {row['join_policy']}")
            if row.get("role"):
                parts.append(f"role: {row['role']}")
            if row.get("membership_type"):
                parts.append(str(row["membership_type"]))
            return markdown_bullet(
                str(row.get("name") or "(unnamed)"),
                *parts,
                body=row.get("mission"),
            )

        return render_markdown_list(
            results,
            line_fn=_org_line,
            noun="organizations",
            empty_text="No organizations found.",
        )
