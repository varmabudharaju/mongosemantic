from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mongosemantic.embeddings.ollama import OllamaProvider
from mongosemantic.embeddings.openai import OpenAIProvider
from mongosemantic.exceptions import DimMismatchError, ProviderError


def test_openai_provider_uses_model_and_returns_normalized():
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[3.0, 4.0] + [0.0] * 1534)]  # 1536-d
    )
    with patch("mongosemantic.embeddings.openai._make_client", return_value=fake_client):
        p = OpenAIProvider("openai-small")
        v = p.embed_batch(["hi"])
        assert v.shape == (1, 1536)
        assert np.isclose(np.linalg.norm(v[0]), 1.0)

def test_openai_rejects_wrong_dim():
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[1.0, 2.0])]  # wrong: 2-d instead of 1536
    )
    with patch("mongosemantic.embeddings.openai._make_client", return_value=fake_client):
        p = OpenAIProvider("openai-small")
        with pytest.raises(DimMismatchError):
            p.embed_batch(["hi"])

def test_ollama_provider(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"embeddings": [[1.0] + [0.0] * 767]}
    fake_resp.raise_for_status = MagicMock()
    import mongosemantic.embeddings.ollama as ol
    monkeypatch.setattr(ol.httpx, "post", lambda *a, **kw: fake_resp)
    p = OllamaProvider("ollama-nomic")
    v = p.embed_batch(["hi"])
    assert v.shape == (1, 768)

def test_ollama_error_raises_provider_error(monkeypatch):
    import mongosemantic.embeddings.ollama as ol
    def boom(*a, **kw):
        raise ol.httpx.ConnectError("no ollama")
    monkeypatch.setattr(ol.httpx, "post", boom)
    p = OllamaProvider("ollama-nomic")
    with pytest.raises(ProviderError):
        p.embed_batch(["hi"])
