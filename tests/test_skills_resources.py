from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from ouro_mcp.resources import skills
from ouro_mcp.resources.skills import SkillsSource, frontmatter, link_files

SKILL_MD = """---
name: magnets
description: Field notes for finding permanent magnets on Ouro.
---

# Magnets

- [routes.md](routes.md): the instruments.
- [findings.md](./findings.md#dead-ends): what failed.
- [Ouro](https://ouro.foundation) and [top](#magnets) stay as they are.
"""

TREE = {
    "tree": [
        {"path": "README.md", "type": "blob"},
        {"path": "magnets", "type": "tree"},
        {"path": "magnets/SKILL.md", "type": "blob"},
        {"path": "magnets/routes.md", "type": "blob"},
        {"path": "magnets/findings.md", "type": "blob"},
        {"path": "notes", "type": "tree"},
        {"path": "notes/todo.md", "type": "blob"},
        {"path": "magnets/scripts/run.py", "type": "blob"},
    ]
}


class FakeGitHub:
    def __init__(self) -> None:
        self.files = {
            "https://api.github.com/repos/ourofoundation/skills/git/trees/main?recursive=1": json.dumps(TREE),
            "https://raw.githubusercontent.com/ourofoundation/skills/main/magnets/SKILL.md": SKILL_MD,
            "https://raw.githubusercontent.com/ourofoundation/skills/main/magnets/routes.md": "# Routes\n",
            "https://raw.githubusercontent.com/ourofoundation/skills/main/magnets/findings.md": "# Findings\n",
        }
        self.calls: list[str] = []
        self.down = False

    async def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.down:
            raise RuntimeError("GitHub is down")
        return self.files[url]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_source() -> tuple[SkillsSource, FakeGitHub, Clock]:
    github, clock = FakeGitHub(), Clock()
    return SkillsSource(ttl=600, fetch=github, clock=clock), github, clock


def make_server() -> tuple[FastMCP, FakeGitHub]:
    source, github, _ = make_source()
    mcp = FastMCP("test")
    skills.register(mcp, source)
    return mcp, github


def read_text(mcp: FastMCP, uri: str) -> str:
    [content] = asyncio.run(mcp.read_resource(uri))
    return content.content


def test_skills_come_from_folders_with_a_skill_md() -> None:
    source, _, _ = make_source()

    assert asyncio.run(source.skills()) == {"magnets": ["SKILL.md", "findings.md", "routes.md"]}


def test_list_resources_names_each_skill_with_its_description() -> None:
    mcp, _ = make_server()

    resources = [r for r in asyncio.run(mcp.list_resources()) if str(r.uri).startswith("ouro://skills/")]

    assert [(str(r.uri), r.name, r.mime_type) for r in resources] == [
        ("ouro://skills/magnets", "magnets", "text/markdown")
    ]
    assert resources[0].description == "Field notes for finding permanent magnets on Ouro."


def test_list_resources_keeps_working_when_github_is_down() -> None:
    mcp, github = make_server()
    github.down = True

    assert asyncio.run(mcp.list_resources()) == []


def test_skill_resource_returns_skill_md_with_links_to_its_files() -> None:
    mcp, _ = make_server()

    text = read_text(mcp, "ouro://skills/magnets")

    assert text.startswith("---\nname: magnets")
    assert "[routes.md](ouro://skills/magnets/routes.md)" in text
    assert "[findings.md](ouro://skills/magnets/findings.md#dead-ends)" in text
    assert "[Ouro](https://ouro.foundation)" in text
    assert "[top](#magnets)" in text


def test_skill_file_resource_returns_a_reference_file() -> None:
    mcp, _ = make_server()

    assert read_text(mcp, "ouro://skills/magnets/routes.md") == "# Routes\n"


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("ouro://skills/notes", "No skill named 'notes'. Skills: magnets."),
        ("ouro://skills/magnets/secrets.md", "has no file 'secrets.md'"),
    ],
)
def test_unknown_skill_or_file_names_what_exists(uri: str, message: str) -> None:
    mcp, github = make_server()

    with pytest.raises(ResourceNotFoundError, match=message):
        asyncio.run(mcp.read_resource(uri))
    assert not any("secrets" in url or "notes/" in url for url in github.calls)


def test_cache_reuses_responses_until_they_expire() -> None:
    source, github, clock = make_source()

    asyncio.run(source.read("magnets"))
    asyncio.run(source.read("magnets"))
    assert len(github.calls) == 2  # tree + SKILL.md, once each

    clock.now = 601
    asyncio.run(source.read("magnets"))
    assert len(github.calls) == 4


def test_cache_serves_the_last_good_copy_when_github_fails() -> None:
    source, github, clock = make_source()
    asyncio.run(source.read("magnets"))

    clock.now = 601
    github.down = True

    assert asyncio.run(source.read("magnets")).startswith("---\nname: magnets")


def test_cache_raises_when_github_fails_with_nothing_cached() -> None:
    source, github, _ = make_source()
    github.down = True

    with pytest.raises(ResourceError, match="Could not list skills.*GitHub is down"):
        asyncio.run(source.read("magnets"))


def test_frontmatter_reads_top_level_fields() -> None:
    assert frontmatter(SKILL_MD) == {
        "name": "magnets",
        "description": "Field notes for finding permanent magnets on Ouro.",
    }
    assert frontmatter("# no frontmatter") == {}


def test_link_files_leaves_links_outside_the_skill_alone() -> None:
    text = "[a](other.md) [b](../matsci-modal-app/SKILL.md) [c](routes.md)"

    assert link_files(text, "magnets", ["SKILL.md", "routes.md"]) == (
        "[a](other.md) [b](../matsci-modal-app/SKILL.md) [c](ouro://skills/magnets/routes.md)"
    )


def test_server_registers_skill_resources() -> None:
    from ouro_mcp.server import mcp

    templates = {t.uri_template for t in asyncio.run(mcp.list_resource_templates())}

    assert {"ouro://skills/{name}", "ouro://skills/{name}/{file}"} <= templates
