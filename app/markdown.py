"""Renders the stored article bodies (a small markdown subset) to HTML.
Pure text module — no web or DB dependencies, so it's easy to test."""

import re

from markupsafe import escape

# An inline image ![alt](url) — only http(s) srcs become <img>. Must run before
# _MD_LINK, or the link regex matches the [alt](url) inside it and leaves a stray '!'.
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
# Any leftover markdown link (relative, mailto:, …) — kept as plain text so a
# raw '[text](url)' never shows. Run after _MD_LINK turns http(s) into anchors.
_MD_LINK_ANY = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")
# A markdown table separator row, e.g. '|---|:--:|'. Each cell is dashes with
# optional leading/trailing colon for alignment.
_TABLE_CELL = re.compile(r":?-+:?")


def _inline_md(text: str) -> str:
    """Renders inline markdown in already-HTML-escaped text: links, bold,
    italic, inline code. Only http(s) links become anchors; other links keep
    their text only."""
    text = _MD_IMAGE.sub(
        lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy" onerror="this.remove()">',
        text,
    )
    text = _MD_LINK.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )
    text = _MD_LINK_ANY.sub(r"\1", text)
    text = _MD_BOLD.sub(r"<strong>\1</strong>", text)
    text = _MD_ITALIC.sub(r"<em>\1</em>", text)
    text = _MD_CODE.sub(r"<code>\1</code>", text)
    return text


def _table_cells(line: str) -> list[str]:
    """Splits a markdown table row '| a | b |' into ['a', 'b']."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_table_sep(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(c and _TABLE_CELL.fullmatch(c) for c in cells)


def body_html(md: str) -> str:
    """Renders the stored body to HTML. Handles a small markdown subset: '```'
    fenced code blocks, '##' headings, '-'/'*' bullet lists, '|' tables, and
    inline bold/italic/code/links. Each non-blank line is its own paragraph —
    trafilatura never wraps a paragraph across lines, and this is robust to
    bodies that separate paragraphs with a single newline (older plain-text
    extraction) as well as blank lines. All text is escaped before markdown is
    applied, so source HTML can't leak."""
    if not md:
        return ""
    html: list[str] = []
    items: list[str] = []
    code: list[str] | None = None  # accumulating fenced-code lines when not None
    table: list[str] = []  # accumulating consecutive '|' rows

    def flush_list():
        if items:
            lis = "".join(f"<li>{_inline_md(escape(i))}</li>" for i in items)
            html.append(f"<ul>{lis}</ul>")
            items.clear()

    def flush_code():
        nonlocal code
        if code is not None:
            body = escape("\n".join(code))
            html.append(f"<pre><code>{body}</code></pre>")
            code = None

    def flush_table():
        if not table:
            return
        rows = table[:]
        table.clear()
        # A real table has a dashes separator as its second row; without it the
        # '|' lines are just prose, so fall back to paragraphs.
        if len(rows) >= 2 and _is_table_sep(rows[1]):
            head = "".join(f"<th>{_inline_md(escape(c))}</th>" for c in _table_cells(rows[0]))
            cells = "".join(
                "<tr>" + "".join(f"<td>{_inline_md(escape(c))}</td>" for c in _table_cells(r)) + "</tr>"
                for r in rows[2:]
            )
            html.append(f"<table><thead><tr>{head}</tr></thead><tbody>{cells}</tbody></table>")
        else:
            for r in rows:
                html.append(f"<p>{_inline_md(escape(r.strip()))}</p>")

    for raw in md.split("\n"):
        # A '```' fence opens or closes a code block. Inside one, lines are kept
        # verbatim (indentation preserved, no inline markdown) until the fence.
        if raw.strip().startswith("```"):
            if code is None:
                flush_list()
                flush_table()
                code = []
            else:
                flush_code()
            continue
        if code is not None:
            code.append(raw)
            continue
        line = raw.strip()
        if not line or line in ("**", "*"):
            # A lone bold/italic marker (photo-credit split across lines in old
            # extractions) would render literally — treat it as a blank line.
            flush_list()
            flush_table()
            continue
        if line.startswith("|"):
            flush_list()
            table.append(raw)
            continue
        flush_table()  # any non-table line closes a pending table
        if line.startswith("#"):
            flush_list()
            level = len(line) - len(line.lstrip("#"))
            tag = "h2" if level <= 2 else "h3"
            html.append(f"<{tag}>{_inline_md(escape(line.lstrip('#').strip()))}</{tag}>")
        elif line[:2] in ("- ", "* "):
            items.append(line[2:].strip())
        else:
            flush_list()
            html.append(f"<p>{_inline_md(escape(line))}</p>")
    flush_code()
    flush_list()
    flush_table()
    return "".join(html)
