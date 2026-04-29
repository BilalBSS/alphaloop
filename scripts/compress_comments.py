#!/usr/bin/env python3
# / one-shot tool — delete violating comment lines per CLAUDE.md
# / inline trailing comments are preserved if any non-comment code precedes
# / standalone violating comment lines are deleted entirely

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MAX_WORDS = 4

SKIP_MARKERS = (
    "type: ignore",
    "noqa",
    "pragma:",
    "fmt:",
    "isort:",
    "mypy:",
    "ruff:",
    "encoding=",
    "coding:",
    "coding=",
    "-*-",
    "!/",
    "bin/env",
    "shellcheck",
    "spdx-",
    "license:",
    "copyright",
)

URL_RE = re.compile(r"https?://\S+")


def is_exempt(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in SKIP_MARKERS)


def word_count(text: str) -> int:
    body = text.lstrip("# /").strip()
    body = URL_RE.sub("URL", body)
    if not body:
        return 0
    return len(re.findall(r"\S+", body))


def find_violations(src: str) -> set[int]:
    violating: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenizeError:
        return violating
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        text = tok.string
        if is_exempt(text):
            continue
        if word_count(text) > MAX_WORDS:
            violating.add(tok.start[0])
    return violating


def is_standalone_comment(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#")


def split_inline_comment(line: str) -> tuple[str, str]:
    # / split "code  # comment" -> ("code", "# comment"); naive but good for our style
    in_str = False
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            i += 1
            continue
        if c == "#":
            return line[:i].rstrip(), line[i:]
        i += 1
    return line, ""


def truncate_inline(comment: str) -> str:
    body = comment.lstrip("# /").strip()
    body = URL_RE.sub("URL", body)
    if not body:
        return "# /"
    words = re.findall(r"\S+", body)
    short = " ".join(words[:MAX_WORDS])
    return f"# / {short}"


def process_file(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    violating = find_violations(src)
    if not violating:
        return 0

    lines = src.splitlines(keepends=True)
    out: list[str] = []
    deleted = 0
    for idx, line in enumerate(lines, start=1):
        if idx not in violating:
            out.append(line)
            continue
        if is_standalone_comment(line):
            deleted += 1
            continue
        # / inline trailing — truncate to 4 words
        code, comment = split_inline_comment(line.rstrip("\n"))
        if not comment:
            out.append(line)
            continue
        new_line = code + "  " + truncate_inline(comment) + ("\n" if line.endswith("\n") else "")
        out.append(new_line)
        deleted += 1

    path.write_text("".join(out), encoding="utf-8")
    return deleted


def main() -> int:
    total = 0
    for py in sorted(SRC.rglob("*.py")):
        if "vendor" in py.parts:
            continue
        n = process_file(py)
        if n:
            print(f"{py.relative_to(ROOT).as_posix()}: -{n}")
            total += n
    print(f"\nremoved/truncated {total} violating comments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
