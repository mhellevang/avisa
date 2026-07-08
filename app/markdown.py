"""Renders the stored article bodies (a small markdown subset) to HTML.
Pure text module — no web or DB dependencies, so it's easy to test."""

import re

from markupsafe import escape

# --- inline patterns -------------------------------------------------------
# Alt text may contain a ']' that is NOT the closing bracket of the image —
# some sources write photo captions as "Name [Screengrab/Reuters]" (Al Jazeera),
# so the alt legitimately holds a nested ']'. Only a ']' directly before '(' is
# the real boundary. URLs may contain balanced parentheses (e.g. Wikipedia
# "/wiki/Foo_(bar)"), so allow a matched "(…)" inside the src before the closing ')'.
_ALT = r"((?:[^\]]|\](?!\())*)"
_URL = r"(https?://(?:\([^\s()]*\)|[^\s()])*)"
# An inline image ![alt](url) — only http(s) srcs become <img>. Must run before
# _MD_LINK, or the link regex matches the [alt](url) inside it and leaves a stray '!'.
_MD_IMAGE = re.compile(r"!\[" + _ALT + r"\]\(" + _URL + r"\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(" + _URL + r"\)")
# Any leftover markdown link (relative, mailto:, …) — kept as plain text so a
# raw '[text](url)' never shows. Restricted to plausible link targets (a scheme,
# a '/', a '.', or a leading '#') so ordinary bracketed prose isn't mangled.
_MD_LINK_ANY = re.compile(r"\[([^\]]+)\]\((?=[^)]*[:/.#])[^)]*\)")
# Bold: '**x**' and '__x__'. Italic: '*x*' and '_x_'. The content must not start
# or end with whitespace (so a stray "** "/" *" isn't paired across a gap), and
# the underscore forms are guarded against intra-word/URL underscores.
_MD_BOLD = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
_MD_BOLD_U = re.compile(r"(?<![_\w])__(?!\s)(.+?)(?<!\s)__(?!\w)")
_MD_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
_MD_ITALIC_U = re.compile(r"(?<![_\w])_(?!\s)([^_]+?)(?<!\s)_(?!\w)")
_MD_CODE = re.compile(r"`([^`]+)`")
# Leftover emphasis markers after pairing would render literally as raw
# asterisks. Strip a '*' that is glued to a non-space on either side (a dangling
# emphasis marker, e.g. "film* your *tricks" or a stray "**"); a '*' with spaces
# on both sides (arithmetic "3 * 4") is left alone. Also strip doubled '__'.
_STRAY_EMPH = re.compile(r"(?<=\S)\*|\*(?=\S)|__+")
# A markdown table separator row, e.g. '|---|:--:|'. Each cell is dashes with
# optional leading/trailing colon for alignment.
_TABLE_CELL = re.compile(r":?-+:?")

# Block-opening line markers, used to decide paragraph/list boundaries.
_HEADING = re.compile(r"(#{1,6})(?:\s+(.*))?$")


def _is_block_start(line: str) -> bool:
    """A stripped line that opens its own block (heading, list item, table row,
    code fence) — i.e. must not be folded into a preceding paragraph or list item."""
    if not line:
        return True
    if line.startswith(("|", ">")) or line.startswith("```"):
        return True
    if _HEADING.match(line):
        return True
    if line[:2] in ("- ", "* ") or line in ("-", "*"):
        return True
    return False


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
    text = _MD_BOLD_U.sub(r"<strong>\1</strong>", text)
    text = _MD_ITALIC.sub(r"<em>\1</em>", text)
    text = _MD_ITALIC_U.sub(r"<em>\1</em>", text)
    text = _MD_CODE.sub(r"<code>\1</code>", text)
    text = _STRAY_EMPH.sub("", text)
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


def _render_table(rows: list[str]) -> str:
    """A real table has a dashes separator as its second row; without it the
    '|' lines are just prose, so render them as readable paragraphs (pipes
    stripped) rather than leaking raw '|' characters."""
    if len(rows) >= 2 and _is_table_sep(rows[1]):
        head = "".join(f"<th>{_inline_md(escape(c))}</th>" for c in _table_cells(rows[0]))
        cells = "".join(
            "<tr>" + "".join(f"<td>{_inline_md(escape(c))}</td>" for c in _table_cells(r)) + "</tr>"
            for r in rows[2:]
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{cells}</tbody></table>"
    return "".join(
        f"<p>{_inline_md(escape(' '.join(_table_cells(r))))}</p>" for r in rows
    )


# Drop cap: wrap the first visible letter (after leading whitespace / an opening
# quote or bracket) in a span. CSS ::first-letter always swallows leading
# punctuation, so a «guillemet»- or "quote"-led paragraph rendered a giant «D /
# "W; scoping the enlargement to the letter's own span avoids that. Works on a
# plain (escaped) string — the lead ingress — and on a leading '<p>' — the body's
# first paragraph. If the text opens with a tag or a non-letter, no cap is added.
_DROPCAP_RE = re.compile(
    r'^(\s*(?:<p>)?[\s"«»“”‘’\'(\[–—\-]*)([0-9A-Za-zÀ-ÿ])'
)


def dropcap_html(html: str) -> str:
    return _DROPCAP_RE.sub(r'\1<span class="dropcap">\2</span>', html, count=1)


def _rejoin_wraps(md: str) -> str:
    """When the body separates paragraphs with blank lines, a lone newline
    between two plain-prose lines is a soft wrap, not a paragraph break — join
    them so a hard-wrapped paragraph renders as one <p> and inline emphasis that
    straddles a wrap can pair up. Bodies with no blank line at all are legacy
    plain-text (one paragraph per line) and are left untouched."""
    if "\n\n" not in md:
        return md
    out: list[str] = []
    for line in md.split("\n"):
        s = line.strip()
        prev = out[-1].strip() if out else ""
        if (
            out
            and prev
            and s
            and not _is_block_start(prev)
            and not _is_block_start(s)
            and not prev.startswith("![")
            and not s.startswith("![")
        ):
            out[-1] = out[-1].rstrip() + " " + s
        else:
            out.append(line)
    return "\n".join(out)


def body_html(md: str) -> str:
    """Renders the stored body to HTML. Handles a small markdown subset: '```'
    fenced code blocks, '#' headings, '-'/'*' bullet lists, '|' tables, and
    inline bold/italic/code/links/images. All text is escaped before markdown is
    applied, so source HTML can't leak."""
    if not md:
        return ""
    md = _rejoin_wraps(md)
    lines = md.split("\n")
    n = len(lines)
    html: list[str] = []
    items: list[str] = []

    def flush_list():
        if items:
            lis = "".join(f"<li>{_inline_md(escape(x))}</li>" for x in items)
            html.append(f"<ul>{lis}</ul>")
            items.clear()

    i = 0
    while i < n:
        raw = lines[i]
        st = raw.strip()

        # Fenced code block: lines kept verbatim (no inline markdown) until the fence.
        if st.startswith("```"):
            flush_list()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # skip the closing fence (or run off the end)
            body = escape("\n".join(code))
            html.append(f"<pre><code>{body}</code></pre>")
            continue

        # Blank line or an orphaned emphasis marker. A blank line does NOT close
        # a list — items split by blank lines belong to the same list.
        if not st or st in ("**", "*", "__", "_"):
            i += 1
            continue

        # Table: a run of consecutive '|' rows.
        if st.startswith("|"):
            flush_list()
            tbl: list[str] = []
            while i < n and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            html.append(_render_table(tbl))
            continue

        # Heading: '#'..'######' followed by whitespace (so '#hashtag' is prose).
        m = _HEADING.match(st)
        if m:
            flush_list()
            text = (m.group(2) or "").strip()
            if not text:
                # Bare marker on its own line — the heading text is the next
                # non-blank line (trafilatura splits some headings this way).
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n:
                    text = lines[j].strip()
                    i = j
            level = len(m.group(1))
            tag = "h2" if level <= 2 else "h3"
            if text:
                html.append(f"<{tag}>{_inline_md(escape(text))}</{tag}>")
            i += 1
            continue

        # List item: '- '/'* ' (or a lone '-'/'*'). Following non-blank,
        # non-block lines are wrapped continuations of the same item.
        if st[:2] in ("- ", "* ") or st in ("-", "*"):
            item = st[2:].strip() if len(st) > 2 else ""
            i += 1
            while i < n:
                cs = lines[i].strip()
                if not cs or _is_block_start(cs):
                    break
                item = (item + " " + cs).strip()
                i += 1
            items.append(item)
            continue

        # Paragraph.
        flush_list()
        html.append(f"<p>{_inline_md(escape(st))}</p>")
        i += 1

    flush_list()
    # Drop cap on the body's opening paragraph (only when it genuinely starts
    # with a paragraph — mirrors the .body-text > p:first-child scope in CSS).
    if html and html[0].startswith("<p>"):
        html[0] = dropcap_html(html[0])
    return "".join(html)
