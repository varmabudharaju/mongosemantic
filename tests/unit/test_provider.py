import numpy as np

from mongosemantic.embeddings.provider import EmbeddingProvider, l2_normalize


class FakeProvider(EmbeddingProvider):
    model_name = "fake"
    dim = 3

    def embed_batch(self, texts):
        raw = np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)
        return self._validate(raw, len(texts))


def test_provider_embed_single():
    p = FakeProvider()
    v = p.embed("hello")
    assert v.shape == (3,)
    assert np.allclose(np.linalg.norm(v), 1.0)


def test_l2_normalize_unit_vector():
    v = np.array([3.0, 4.0, 0.0])
    n = l2_normalize(v)
    assert np.isclose(np.linalg.norm(n), 1.0)


def test_l2_normalize_zero_vector_safe():
    v = np.zeros(3)
    n = l2_normalize(v)
    # Zero vector stays zero, no NaN
    assert not np.any(np.isnan(n))
