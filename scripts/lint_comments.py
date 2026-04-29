#!/usr/bin/env python3
# / lint comments to enforce <=4 word rule (CLAUDE.md hard rule)
# / skips docstrings, shebangs, type ignores, noqa, encoding, license headers

from __future__ import annotations

import ast
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
    # / strip leading "/" prefix and any leading punctuation
    body = text.lstrip("# /").strip()
    body = URL_RE.sub("URL", body)
    if not body:
        return 0
    words = re.findall(r"\S+", body)
    return len(words)


def lint_file(path: Path) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    src = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return violations

    docstring_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    start = first.lineno
                    end = first.end_lineno or first.lineno
                    docstring_ranges.append((start, end))

    def in_docstring(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in docstring_ranges)

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenizeError:
        return violations

    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        line, _ = tok.start
        text = tok.string
        if in_docstring(line):
            continue
        if is_exempt(text):
            continue
        n = word_count(text)
        if n > MAX_WORDS:
            violations.append((line, text.rstrip()))

    return violations


def main() -> int:
    out = sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8", errors="replace")
    total = 0
    for py in sorted(SRC.rglob("*.py")):
        if "vendor" in py.parts:
            continue
        violations = lint_file(py)
        if violations:
            rel = py.relative_to(ROOT).as_posix()
            for line, text in violations:
                safe = text.encode("ascii", errors="replace").decode("ascii")
                out.write(f"{rel}:{line}: {safe}\n")
            total += len(violations)
    if total:
        sys.stderr.write(f"\n{total} violations\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
