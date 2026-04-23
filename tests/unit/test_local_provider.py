import numpy as np
import pytest

from mongosemantic.embeddings.local import LocalProvider


@pytest.mark.parametrize("key,dim", [("local-fast", 384), ("local-better", 768)])
def test_local_provider_dim(key, dim):
    p = LocalProvider(key)
    assert p.dim == dim


def test_local_provider_embed_returns_normalized():
    p = LocalProvider("local-fast")
    vecs = p.embed_batch(["hello world", "goodbye sun"])
    assert vecs.shape == (2, 384)
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_local_provider_rejects_unknown_key():
    with pytest.raises(ValueError):
        LocalProvider("bogus")
