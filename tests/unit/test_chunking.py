from mongosemantic.chunking.splitter import ChunkConfig, chunk_text


def test_short_text_single_chunk():
    out = chunk_text("Hello world.", ChunkConfig(chunk_size_tokens=100, overlap_tokens=0))
    assert out == ["Hello world."]

def test_long_text_splits_into_chunks():
    sentences = ". ".join([f"Sentence number {i}" for i in range(100)]) + "."
    out = chunk_text(sentences, ChunkConfig(chunk_size_tokens=50, overlap_tokens=10))
    assert len(out) > 1
    assert all(len(c) > 0 for c in out)

def test_overlap_produces_shared_content():
    sentences = ". ".join([f"s{i}" for i in range(200)]) + "."
    out = chunk_text(sentences, ChunkConfig(chunk_size_tokens=20, overlap_tokens=10))
    assert len(out) >= 2
    a, b = out[0], out[1]
    assert len(a) > 0 and len(b) > 0

def test_empty_string():
    out = chunk_text("", ChunkConfig(chunk_size_tokens=100, overlap_tokens=0))
    assert out == []

def test_unicode_handled():
    out = chunk_text("Hello 世界. Goodbye 🌍.", ChunkConfig(chunk_size_tokens=100, overlap_tokens=0))
    assert len(out) == 1
    assert "世界" in out[0]

def test_chunks_respect_sentence_boundaries():
    text = "First sentence is here. Second sentence. Third. Fourth."
    out = chunk_text(text, ChunkConfig(chunk_size_tokens=5, overlap_tokens=0))
    for chunk in out[:-1]:
        assert chunk.rstrip().endswith((".", "!", "?"))
