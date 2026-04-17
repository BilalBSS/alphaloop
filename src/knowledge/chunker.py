# / markdown chunker for embedding — heading-aware with overlap

from __future__ import annotations

import re

CHARS_PER_TOKEN = 4
_HEADING_RE = re.compile(r"^(#{2,3})\s+.*$", re.MULTILINE)


def _token_count(text: str) -> int:
    # / rough estimate matching wiki_context convention
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_headings(text: str) -> list[str]:
    # / split text by ## and ### headings, preserving the heading with its section
    lines = text.splitlines(keepends=True)
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _HEADING_RE.match(line) and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["".join(s) for s in sections]


def _split_paragraphs(text: str) -> list[str]:
    # / split on blank-line boundaries; keep non-empty paragraphs
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if p.strip()]


def _pack_units(
    units: list[str],
    target_tokens: int,
    overlap: int,
) -> list[str]:
    # / greedy pack units into chunks under target_tokens with overlap at boundaries
    if not units:
        return []
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    for unit in units:
        unit_tokens = _token_count(unit)
        if unit_tokens >= target_tokens and not buffer:
            # / oversized unit alone: hard-split by characters
            chunks.extend(_hard_split(unit, target_tokens, overlap))
            continue
        if buffer_tokens + unit_tokens > target_tokens and buffer:
            chunks.append("\n\n".join(buffer).strip())
            # / build overlap tail from last units fitting into overlap budget
            tail = _take_tail_for_overlap(buffer, overlap)
            buffer = list(tail)
            buffer_tokens = sum(_token_count(b) for b in buffer)
        buffer.append(unit)
        buffer_tokens += unit_tokens

    if buffer:
        chunks.append("\n\n".join(buffer).strip())
    return [c for c in chunks if c]


def _take_tail_for_overlap(units: list[str], overlap: int) -> list[str]:
    # / collect trailing units whose total tokens fit under overlap
    tail: list[str] = []
    total = 0
    for unit in reversed(units):
        t = _token_count(unit)
        if total + t > overlap and tail:
            break
        tail.insert(0, unit)
        total += t
    return tail


def _hard_split(text: str, target_tokens: int, overlap: int) -> list[str]:
    # / fallback for a single unit larger than target_tokens
    max_chars = max(1, target_tokens * CHARS_PER_TOKEN)
    step = max(1, max_chars - overlap * CHARS_PER_TOKEN)
    pieces: list[str] = []
    i = 0
    while i < len(text):
        pieces.append(text[i:i + max_chars].strip())
        i += step
    return [p for p in pieces if p]


def chunk_markdown(
    text: str,
    target_tokens: int = 400,
    overlap: int = 50,
) -> list[str]:
    # / produce overlapping chunks of markdown respecting headings first, then paragraphs
    if not text or not text.strip():
        return []
    target_tokens = max(50, int(target_tokens))
    overlap = max(0, min(int(overlap), target_tokens // 2))

    sections = _split_headings(text)
    if not sections:
        sections = [text]

    # / if the whole doc fits, one chunk
    if _token_count(text) <= target_tokens:
        return [text.strip()]

    # / flatten sections into paragraph-level units for packing
    units: list[str] = []
    for section in sections:
        section_tokens = _token_count(section)
        if section_tokens <= target_tokens:
            units.append(section.strip())
        else:
            units.extend(_split_paragraphs(section))

    return _pack_units(units, target_tokens, overlap)
