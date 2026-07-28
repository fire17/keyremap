"""A tiny YAML reader for keyremap's config subset — stdlib only.

Why this exists: a fresh machine (a Mac, a clean Windows install) has no
`pyyaml`, and asking someone to `pip install` before they can remap a key
breaks the "easy as pie" promise. This parses the shape keyremap's config
actually uses and nothing more:

  * comments (`# …`) and blank lines
  * nested mappings by indentation
  * block lists (`- item`) and inline lists (`[a, b]`)
  * inline mappings (`{a: 1, b: 2}`) and empty `{}`
  * scalars: quoted strings, ints (incl. 0x hex), floats, true/false, null/~

It is verified against pyyaml on the real config in the test suite, so if it
ever diverges on a document we care about, CI says so. Anything fancier
(anchors, multi-line scalars, multiple documents) raises rather than guessing.
"""


class YamlError(ValueError):
    pass


def _scalar(tok: str):
    t = tok.strip()
    if not t:
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    # Anchors/aliases/merge keys change a document's meaning; refuse rather
    # than silently parsing them as plain strings.
    if t[0] in "&*" and len(t) > 1 and (t[1].isalnum() or t[1] == "_"):
        raise YamlError(f"anchors/aliases are not supported: {t[:20]!r}")
    low = t.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(t, 0)          # handles 0x045E and plain ints
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _split_top(s: str) -> list[str]:
    """Split on commas that are not inside brackets/braces/quotes."""
    out, buf, depth, quote = [], "", 0, ""
    for ch in s:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf)
    return out


def _inline(tok: str):
    t = tok.strip()
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        return [] if not inner else [_inline(x) for x in _split_top(inner)]
    if t.startswith("{") and t.endswith("}"):
        inner = t[1:-1].strip()
        if not inner:
            return {}
        out = {}
        for part in _split_top(inner):
            if ":" not in part:
                raise YamlError(f"inline mapping entry without ':': {part!r}")
            k, v = part.split(":", 1)
            out[_scalar(k)] = _inline(v)
        return out
    return _scalar(t)


def _strip_comment(line: str) -> str:
    out, quote = "", ""
    for i, ch in enumerate(line):
        if quote:
            out += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out += ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out += ch
    return out.rstrip()


def parse(text: str):
    lines = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if line.lstrip().startswith(("&", "*", "<<", "---", "...")):
            raise YamlError(f"unsupported YAML feature: {line.strip()[:20]!r}")
        lines.append((len(line) - len(line.lstrip()), line.strip()))

    value, idx = _parse_block(lines, 0, 0)
    if idx != len(lines):
        raise YamlError(f"could not parse from line {idx + 1}")
    return value


def _parse_block(lines, i: int, indent: int):
    if i >= len(lines):
        return None, i
    if lines[i][1].startswith("- "):
        return _parse_list(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_list(lines, i, indent):
    out = []
    while i < len(lines):
        ind, text = lines[i]
        if ind < indent or not text.startswith("- "):
            break
        item = text[2:].strip()
        if item and ":" in item and not item.startswith(("[", "{")):
            # "- key: value" — a mapping inside a list item
            sub, i = _parse_map([(ind + 2, item)] + lines[i + 1:], 0, ind + 2)
            out.append(sub)
            continue
        out.append(_inline(item))
        i += 1
    return out, i


def _parse_map(lines, i, indent):
    out = {}
    while i < len(lines):
        ind, text = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise YamlError(f"unexpected indent at {text[:24]!r}")
        if ":" not in text:
            raise YamlError(f"expected 'key: value' at {text[:24]!r}")
        key, rest = text.split(":", 1)
        key, rest = key.strip(), rest.strip()
        if key == "<<":
            raise YamlError("merge keys ('<<:') are not supported")
        i += 1
        if rest:
            out[_scalar(key)] = _inline(rest)
            continue
        # nested block: whatever is more-indented (or a list at same indent)
        if i < len(lines) and (lines[i][0] > ind or
                               (lines[i][0] == ind and lines[i][1].startswith("- "))):
            child_indent = lines[i][0]
            sub, i = _parse_block(lines, i, child_indent)
            out[_scalar(key)] = sub
        else:
            out[_scalar(key)] = None
    return out, i


def safe_load(text: str):
    """pyyaml-compatible entry point for the subset we support."""
    return parse(text)
