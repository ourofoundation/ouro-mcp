"""Markdown that agents write: undo shell escaping, normalize mentions, build Content."""

from __future__ import annotations

import re
from typing import Any, Callable


def normalize_markdown_input(markdown: str) -> str:
    """Normalize common shell-escaped markdown sequences.

    Agents frequently pass markdown via shell CLI args (e.g. content_markdown="..."),
    where escaped sequences like ``\\n`` are sent literally. Convert those back to
    markdown-friendly characters so the backend receives the intended content.

    Only text with no real line breaks is treated as escaped, and math spans
    are skipped even then: ``\\nabla``, ``\\neq`` and ``\\nu`` are LaTeX, not
    a newline followed by a word.
    """
    normalized = markdown.replace("\\`", "`")
    if "\n" not in normalized and "\\n" in normalized:
        normalized = _outside(_MATH_SPANS, normalized, _decode_newlines)
    normalized = _normalize_mentions(normalized)
    return normalized


# Spans whose text is literal rather than markdown: fenced and inline code, and
# math. Escape decoding and mention rewriting leave them alone.
_FENCED_CODE = r"(?:```|~~~)[\s\S]*?(?:```|~~~|\Z)"
_MATH = r"\$\$[\s\S]+?\$\$|(?<![\\$])\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]"
_MATH_SPANS = re.compile(_MATH)


def _outside(spans: re.Pattern, text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to the parts of ``text`` that ``spans`` does not match."""
    out: list[str] = []
    last = 0
    for m in spans.finditer(text):
        out.append(fn(text[last : m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def _decode_newlines(text: str) -> str:
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


# Ouro's markdown parser only recognizes one mention spelling: the
# backtick-wrapped brace-at form `{@username}`. Models reliably get this wrong,
# emitting @username, @{username}, {@username}, or `@username` instead, none of
# which notify the user. Normalize every supported spelling to the canonical
# form so a mention works regardless of how the agent wrote it.
_MENTION_USER = r"([A-Za-z0-9_]{1,64})"
# Braces come off first, so a backticked spelling (`{@u}`, `@{u}`) is left as
# `@u` for the last reducer.
_MENTION_REDUCERS = (
    re.compile(r"\{@" + _MENTION_USER + r"\}"),  # {@u}
    re.compile(r"@\{" + _MENTION_USER + r"\}"),  # @{u}
    re.compile(r"`@" + _MENTION_USER + r"`"),  # `@u`
)
# Bare @username, but not mid-word, not an email local part, and not already
# wrapped in a brace or backtick.
_MENTION_BARE = re.compile(r"(?<![\w`{])@" + _MENTION_USER + r"\b")
# Code and math to leave alone (``@property`` in a code block is a decorator).
# An inline span holding only a mention (`@u`, `{@u}`) is a mention spelling,
# so it is matched first and rewritten rather than skipped.
_MENTION_SKIP = re.compile(
    r"(?P<mention>`(?:\{@\w{1,64}\}|@\{\w{1,64}\}|@\w{1,64})`)|" + _FENCED_CODE + r"|`[^`\n]+`|" + _MATH
)


def _rewrite_mentions(text: str) -> str:
    # Phase 1: strip any wrapping spelling back down to a bare @username token.
    for pattern in _MENTION_REDUCERS:
        text = pattern.sub(r"@\1", text)
    # Phase 2: wrap bare @username uniformly into the canonical mention syntax.
    return _MENTION_BARE.sub(r"`{@\1}`", text)


def _normalize_mentions(text: str) -> str:
    out: list[str] = []
    last = 0
    for m in _MENTION_SKIP.finditer(text):
        out.append(_rewrite_mentions(text[last : m.start()]))
        span = m.group(0)
        out.append(_rewrite_mentions(span) if m.group("mention") else span)
        last = m.end()
    out.append(_rewrite_mentions(text[last:]))
    return "".join(out)


def content_from_markdown(ouro: Any, markdown: str) -> Any:
    """Create a Content object from markdown using the Ouro client."""
    content = ouro.posts.Content()
    content.from_markdown(normalize_markdown_input(markdown))
    return content
