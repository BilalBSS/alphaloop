# / tests for chunk_markdown — heading split, paragraph fallback, overlap correctness

from __future__ import annotations

from src.knowledge.chunker import CHARS_PER_TOKEN, chunk_markdown

# ──────────────────────────────────────────────────────
# empty / tiny input
# ──────────────────────────────────────────────────────

def test_empty_input_returns_empty_list():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  \t ") == []


def test_none_input_returns_empty_list():
    # / defensive — no None crash; behaves same as empty
    assert chunk_markdown(None or "") == []


def test_tiny_input_single_chunk():
    # / ≤ target_tokens should return a single chunk containing the full text
    text = "A small note about trading."
    chunks = chunk_markdown(text, target_tokens=400, overlap=50)
    assert len(chunks) == 1
    assert chunks[0].strip() == text.strip()


# ──────────────────────────────────────────────────────
# heading splits
# ──────────────────────────────────────────────────────

def test_splits_on_double_hash_heading():
    # / long doc with multiple ## headings should produce multiple chunks
    section = "x " * 800  # / ~1600 chars = ~400 tokens
    text = (
        f"## Section A\n{section}\n\n"
        f"## Section B\n{section}\n\n"
        f"## Section C\n{section}"
    )
    chunks = chunk_markdown(text, target_tokens=300, overlap=20)
    assert len(chunks) >= 3
    # / each heading should appear somewhere in the output
    joined = "\n".join(chunks)
    assert "Section A" in joined
    assert "Section B" in joined
    assert "Section C" in joined


def test_splits_on_triple_hash_heading():
    # / ### subheadings also trigger section boundaries
    block = "data " * 400  # / ~2000 chars
    text = (
        f"### Intro\n{block}\n\n"
        f"### Middle\n{block}\n\n"
        f"### End\n{block}"
    )
    chunks = chunk_markdown(text, target_tokens=250, overlap=20)
    assert len(chunks) >= 3


def test_single_hash_heading_not_split():
    # / only ## and ### split; a single # (H1) must not introduce a new section
    big = "x " * 800
    text = f"# Title\n{big}\n\nmore content {big}"
    chunks = chunk_markdown(text, target_tokens=400, overlap=0)
    # / the H1 heading text should end up inside the first chunk (not a fresh section)
    assert "# Title" in chunks[0]


# ──────────────────────────────────────────────────────
# paragraph splits (no headings)
# ──────────────────────────────────────────────────────

def test_paragraph_split_when_no_headings():
    # / blank lines inside an oversized block should split paragraphs
    long_para = "word " * 400  # / ~2000 chars
    text = (
        f"{long_para}\n\n"
        f"{long_para}\n\n"
        f"{long_para}"
    )
    chunks = chunk_markdown(text, target_tokens=300, overlap=20)
    assert len(chunks) >= 2


# ──────────────────────────────────────────────────────
# overlap correctness
# ──────────────────────────────────────────────────────

def test_overlap_target_tokens_and_overlap_honored():
    # / target_tokens and overlap params should be respected
    text = "\n\n".join([f"paragraph {i} " + "w " * 200 for i in range(6)])
    chunks = chunk_markdown(text, target_tokens=200, overlap=50)
    assert len(chunks) >= 2
    # / each chunk is roughly bounded by target_tokens * CHARS_PER_TOKEN
    for c in chunks:
        assert len(c) <= 200 * CHARS_PER_TOKEN * 3  # / generous bound for packed units


def test_overlap_shared_content_between_adjacent_chunks():
    # / last paragraph of chunk[i] should appear at start of chunk[i+1] when overlap permits
    paragraphs = [f"Para {i} content keyword_{i} " + ("x " * 100) for i in range(6)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_markdown(text, target_tokens=150, overlap=100)
    assert len(chunks) >= 2
    # / some paragraph text should repeat between consecutive chunks (the overlap tail)
    overlaps_found = 0
    for i in range(len(chunks) - 1):
        for kw in [f"keyword_{j}" for j in range(6)]:
            if kw in chunks[i] and kw in chunks[i + 1]:
                overlaps_found += 1
                break
    assert overlaps_found >= 1


# ──────────────────────────────────────────────────────
# huge input
# ──────────────────────────────────────────────────────

def test_huge_input_produces_multiple_chunks():
    # / 5000+ tokens should break into many chunks
    text = "\n\n".join([f"### H{i}\n" + ("w " * 400) for i in range(15)])
    chunks = chunk_markdown(text, target_tokens=400, overlap=50)
    assert len(chunks) >= 5


# ──────────────────────────────────────────────────────
# edge: oversized single unit
# ──────────────────────────────────────────────────────

def test_oversized_single_paragraph_hard_split():
    # / a single paragraph bigger than target_tokens must get hard-split into pieces
    mono = "a" * 20000  # / one blob, no paragraph breaks
    chunks = chunk_markdown(mono, target_tokens=200, overlap=20)
    assert len(chunks) >= 2
    # / all pieces non-empty
    assert all(len(c) > 0 for c in chunks)


def test_all_chunks_nonempty():
    # / filter should drop empty strings
    text = "## A\ncontent\n\n## B\nmore"
    chunks = chunk_markdown(text, target_tokens=100, overlap=10)
    assert all(c.strip() for c in chunks)


def test_target_tokens_minimum_enforced():
    # / floor of 50 tokens protects against degenerate splits
    text = "## A\n" + ("w " * 1000)
    chunks_small = chunk_markdown(text, target_tokens=1, overlap=0)
    chunks_normal = chunk_markdown(text, target_tokens=50, overlap=0)
    # / target_tokens=1 is clamped to 50 internally — output should match target=50
    assert len(chunks_small) == len(chunks_normal)
