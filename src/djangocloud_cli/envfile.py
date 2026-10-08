"""Read a `.env` file into KEY -> value pairs, for `env push`.

Understands comments, blank lines, `export KEY=...`, single quotes (literal), double quotes (`\\n`, `\\"` and `\\\\`
escapes) and quoted values that span several lines (PEM keys). Anything it can't read is reported by line number
instead of being silently dropped. Values are never expanded (`$VAR` stays as written).
"""

import re

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw) and raw[i + 1] in _ESCAPES:
            out.append(_ESCAPES[raw[i + 1]])
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def parse(text: str) -> tuple[dict[str, str], list[str]]:
    """Returns (variables, errors). Later duplicates win."""
    variables: dict[str, str] = {}
    errors: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        number, line = i + 1, lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, rest = line.partition("=")
        key, rest = key.strip(), rest.lstrip()
        if not sep:
            errors.append(f"Line {number}: expected KEY=value.")
            continue
        if not KEY_RE.match(key):
            errors.append(f"Line {number}: {key[:40]!r} isn't a valid variable name.")
            continue
        if rest[:1] in ("'", '"'):
            quote, body, closed = rest[0], rest[1:], False
            collected: list[str] = []
            while True:
                end = _closing_quote(body, quote)
                if end is not None:
                    collected.append(body[:end])
                    closed = True
                    break
                collected.append(body)
                if i >= len(lines):
                    break
                body, i = lines[i], i + 1
            if not closed:
                errors.append(f"Line {number}: the quote opened here is never closed.")
                continue
            value = "\n".join(collected)
            variables[key] = _unescape(value) if quote == '"' else value
        else:
            variables[key] = re.split(r"\s+#", rest, maxsplit=1)[0].rstrip()
    return variables, errors


def _closing_quote(body: str, quote: str) -> int | None:
    """Index of the quote that ends `body`, skipping `\\"` inside double quotes; None if it isn't on this line."""
    i = 0
    while i < len(body):
        if quote == '"' and body[i] == "\\":
            i += 2
            continue
        if body[i] == quote:
            return i
        i += 1
    return None
