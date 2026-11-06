"""Ouro's public agent skills, served from the GitHub skills repo.

Every top-level folder of the repo that holds a SKILL.md is a skill.
`ouro://skills/{name}` returns its SKILL.md and `ouro://skills/{name}/{file}`
one of its reference files, with relative links between them rewritten to
those URIs. resources/list names each skill, so clients can offer it for
attaching (Claude Code's @ menu, Claude's + menu) and agents can find it with
their list-resources tool.

The repo stays the only copy: the file list comes from one GitHub tree call
and files from raw.githubusercontent.com, each cached for a few minutes. When
GitHub fails, the last good copy is served.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError
from mcp_types import Resource as MCPResource

from ouro_mcp import __version__
from ouro_mcp.constants import SKILLS_CACHE_SECONDS, SKILLS_REF, SKILLS_REPO

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
URI_PREFIX = "ouro://skills/"

# A markdown link target with no scheme or leading slash: `[x](routes.md#a)`.
_RELATIVE_LINK = re.compile(r"\]\((?![a-z][a-z0-9+.-]*:|/|#)([^)\s#]+)(#[^)\s]*)?\)")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", re.DOTALL)


def skill_uri(name: str, file: str = SKILL_FILE) -> str:
    return f"{URI_PREFIX}{name}" if file == SKILL_FILE else f"{URI_PREFIX}{name}/{file}"


def frontmatter(text: str) -> dict[str, str]:
    """Top-level `key: value` pairs of a SKILL.md frontmatter block."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key and not key[0].isspace():
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def link_files(text: str, name: str, files: list[str]) -> str:
    """Point relative links at the skill's own files to their resource URIs."""

    def replace(match: re.Match[str]) -> str:
        target, anchor = match.group(1), match.group(2) or ""
        target = target.removeprefix("./")
        if target not in files:
            return match.group(0)
        return f"]({skill_uri(name, target)}{anchor})"

    return _RELATIVE_LINK.sub(replace, text)


async def _http_get(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": f"ouro-mcp/{__version__}"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


class SkillsSource:
    """Lists and reads skills from GitHub, caching each response for `ttl` seconds."""

    def __init__(
        self,
        repo: str = SKILLS_REPO,
        ref: str = SKILLS_REF,
        ttl: float = SKILLS_CACHE_SECONDS,
        fetch: Callable[[str], Awaitable[str]] = _http_get,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo = repo
        self.ref = ref
        self.ttl = ttl
        self._fetch = fetch
        self._clock = clock
        self._cache: dict[str, tuple[float, str]] = {}

    @property
    def tree_url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/git/trees/{self.ref}?recursive=1"

    def raw_url(self, name: str, file: str) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/{name}/{file}"

    async def _get(self, url: str) -> str:
        now = self._clock()
        hit = self._cache.get(url)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        try:
            text = await self._fetch(url)
        except Exception:
            if hit:
                log.warning("Fetching %s failed; serving the copy from %.0fs ago", url, now - hit[0], exc_info=True)
                return hit[1]
            raise
        self._cache[url] = (now, text)
        return text

    async def skills(self) -> dict[str, list[str]]:
        """Skill name -> the files directly inside its folder, SKILL.md first."""
        tree = json.loads(await self._get(self.tree_url)).get("tree", [])
        files: dict[str, list[str]] = {}
        for entry in tree:
            parts = entry.get("path", "").split("/")
            if entry.get("type") == "blob" and len(parts) == 2:
                files.setdefault(parts[0], []).append(parts[1])
        return {
            name: sorted(names, key=lambda f: (f != SKILL_FILE, f))
            for name, names in sorted(files.items())
            if SKILL_FILE in names
        }

    async def read(self, name: str, file: str = SKILL_FILE) -> str:
        try:
            skills = await self.skills()
        except Exception as exc:
            raise ResourceError(f"Could not list skills from https://github.com/{self.repo}: {exc}") from exc
        if name not in skills:
            available = ", ".join(skills) or "none"
            raise ResourceNotFoundError(f"No skill named {name!r}. Skills: {available}.")
        if file not in skills[name]:
            raise ResourceNotFoundError(f"Skill {name!r} has no file {file!r}. Files: {', '.join(skills[name])}.")
        try:
            text = await self._get(self.raw_url(name, file))
        except Exception as exc:
            raise ResourceError(f"Could not fetch {self.raw_url(name, file)}: {exc}") from exc
        return link_files(text, name, skills[name]) if file.endswith(".md") else text

    async def list_resources(self) -> list[MCPResource]:
        skills = await self.skills()

        async def describe(name: str) -> str | None:
            try:
                return frontmatter(await self._get(self.raw_url(name, SKILL_FILE))).get("description")
            except Exception:
                log.warning("Could not read %s/%s", name, SKILL_FILE, exc_info=True)
                return None

        descriptions = await asyncio.gather(*(describe(name) for name in skills))
        return [
            MCPResource(
                uri=skill_uri(name),
                name=name,
                title=f"Skill: {name}",
                description=description,
                mime_type="text/markdown",
            )
            for name, description in zip(skills, descriptions)
        ]


def register(mcp: FastMCP, source: SkillsSource | None = None) -> None:
    source = source or SkillsSource()
    repo_url = f"https://github.com/{source.repo}"

    @mcp.resource(
        "ouro://skills/{name}",
        name="Skill",
        description=(
            f"An Ouro agent skill's SKILL.md, from {repo_url}: field notes and workflows "
            "for a kind of work on Ouro. Links to its reference files are ouro://skills/{name}/{file} URIs; "
            "read those when a step needs them."
        ),
        mime_type="text/markdown",
    )
    async def get_skill(name: str) -> str:
        return await source.read(name)

    @mcp.resource(
        "ouro://skills/{name}/{file}",
        name="Skill file",
        description="A reference file of an Ouro agent skill, linked from its SKILL.md.",
        mime_type="text/markdown",
    )
    async def get_skill_file(name: str, file: str) -> str:
        return await source.read(name, file)

    # Templates don't show in resources/list, which is what clients offer for
    # attaching and what agents browse, so name each skill there too.
    list_registered = mcp.list_resources

    async def list_resources() -> list[MCPResource]:
        resources = await list_registered()
        try:
            return resources + await source.list_resources()
        except Exception:
            log.warning("Could not list skills from %s", repo_url, exc_info=True)
            return resources

    mcp.list_resources = list_resources
