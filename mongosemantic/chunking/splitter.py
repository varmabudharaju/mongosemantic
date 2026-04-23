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

def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text.strip())
    return [p for p in parts if p]

def chunk_text(text: str, config: ChunkConfig) -> list[str]:
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
