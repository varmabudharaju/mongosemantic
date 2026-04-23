from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

@dataclass(frozen=True)
class ChunkConfig:
    chunk_size_tokens: int = 512
    overlap_tokens: int = 64

    def __post_init__(self) -> None:
        if self.chunk_size_tokens <= 0:
            raise ValueError("chunk_size_tokens must be positive")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must be non-negative")
        if self.overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("overlap_tokens must be less than chunk_size_tokens")

def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text.strip())
    return [p for p in parts if p]

def chunk_text(text: str, config: ChunkConfig) -> list[str]:
    """Split `text` into overlapping chunks at sentence boundaries.

    Returns an empty list for empty/whitespace-only input.
    A single sentence that exceeds `config.chunk_size_tokens` is emitted as
    one oversize chunk rather than split mid-sentence — downstream embedders
    may choose to truncate or reject. Token counts are estimated as len/4,
    which is an English-text heuristic; real tokenization happens in the
    embedding provider.
    """
    if not text or not text.strip():
        return []
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    target = config.chunk_size_tokens
    overlap = config.overlap_tokens

    def _flush() -> None:
        if current:
            chunks.append(" ".join(current).strip())

    for sentence in sentences:
        tokens = _estimate_tokens(sentence)
        if current_tokens + tokens > target and current:
            _flush()
            if overlap > 0:
                tail: list[str] = []
                tail_tokens = 0
                for s in reversed(current):
                    st = _estimate_tokens(s)
                    if tail_tokens + st > overlap:
                        break
                    tail.insert(0, s)
                    tail_tokens += st
                current = list(tail)
                current_tokens = tail_tokens
            else:
                current = []
                current_tokens = 0
        current.append(sentence)
        current_tokens += tokens
    _flush()
    return chunks
