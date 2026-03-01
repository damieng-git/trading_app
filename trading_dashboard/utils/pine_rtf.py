"""
Utilities to extract Pine Script source code from .rtf files.

Content migrated from legacy `trading_dashboard/pine_rtf.py`.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_HEX_ESCAPE_RE = re.compile(r"\\'([0-9a-fA-F]{2})")


def _decode_hex_escapes(rtf: str) -> str:
    def _repl(m: re.Match) -> str:
        try:
            return bytes([int(m.group(1), 16)]).decode("cp1252", errors="replace")
        except Exception:
            return "�"

    return _HEX_ESCAPE_RE.sub(_repl, rtf)


def rtf_to_text(rtf: str) -> str:
    s = rtf.replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []
    i = 0
    n = len(s)

    ignored_destinations = {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "pict",
        "object",
        "listtable",
        "listoverridetable",
        "expandedcolortbl",
        "generator",
        "datastore",
    }

    while i < n:
        ch = s[i]

        if ch == "{":
            j = i + 1
            if j < n and s[j] == "\\":
                j += 1
                if j < n and s[j] == "*":
                    j += 1
                k = j
                while k < n and s[k].isalpha():
                    k += 1
                dest = s[j:k]
                if dest in ignored_destinations:
                    depth = 1
                    i += 1
                    while i < n and depth > 0:
                        if s[i] == "{":
                            depth += 1
                        elif s[i] == "}":
                            depth -= 1
                        i += 1
                    continue
            i += 1
            continue

        if ch == "}":
            i += 1
            continue

        if ch != "\\":
            out.append(ch)
            i += 1
            continue

        if i + 1 >= n:
            break

        nxt = s[i + 1]
        if nxt in ("\\", "{", "}"):
            out.append(nxt)
            i += 2
            continue

        if nxt == "'" and i + 3 < n:
            hex_bytes = s[i + 2 : i + 4]
            try:
                out.append(bytes([int(hex_bytes, 16)]).decode("cp1252", errors="replace"))
            except Exception:
                out.append("�")
            i += 4
            continue

        j = i + 1
        while j < n and s[j].isalpha():
            j += 1
        word = s[i + 1 : j]

        k = j
        if k < n and (s[k] == "-" or s[k].isdigit()):
            k += 1
            while k < n and s[k].isdigit():
                k += 1

        if k < n and s[k] == " ":
            k += 1

        if word in ("par", "line"):
            out.append("\n")
        elif word == "tab":
            out.append("\t")
        elif word == "u":
            num_str = s[j:k].strip()
            try:
                codepoint = int(num_str)
                if codepoint < 0:
                    codepoint += 65536
                out.append(chr(codepoint))
            except Exception as exc:
                logger.debug("Failed to decode Unicode escape in RTF: %s", exc)
                pass

        i = k

    txt = "".join(out)
    txt = txt.replace("\t", " ")
    txt = re.sub(r"[ ]{2,}", " ", txt)
    lines = [ln.rstrip() for ln in txt.split("\n")]
    return "\n".join(lines).strip()


def extract_pine_source_from_rtf(rtf_text: str) -> str:
    txt = rtf_to_text(rtf_text)
    lines: list[str] = []
    for ln in txt.split("\n"):
        s = ln.strip()
        if not s:
            continue

        has_alnum = re.search(r"[A-Za-z0-9]", s) is not None
        looks_like_pine = any(
            tok in s for tok in ("//", "@version", "indicator", "study", "strategy", "input", "plot", "=", ":=", "=>", "(", ")", "[", "]")
        )
        if not has_alnum and not looks_like_pine:
            continue
        if not has_alnum and looks_like_pine is False:
            continue

        lines.append(s)

    return "\n".join(lines).strip()

