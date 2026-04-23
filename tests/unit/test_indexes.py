from mongosemantic.db.indexes import (
    shadow_collection_name,
    vector_index_definition,
    vector_index_name,
)


def test_shadow_collection_name():
    assert shadow_collection_name("articles") == "articles_embeddings"


def test_vector_index_name_is_stable_and_deterministic():
    a = vector_index_name("articles", "body")
    b = vector_index_name("articles", "body")
    assert a == b
    assert a.startswith("mongosemantic_articles_")
    # changing field changes name
    assert vector_index_name("articles", "title") != a


def test_vector_index_definition_shape():
    definition = vector_index_definition(dim=384)
    assert definition["fields"][0]["type"] == "vector"
    assert definition["fields"][0]["path"] == "embedding"
    assert definition["fields"][0]["numDimensions"] == 384
    assert definition["fields"][0]["similarity"] == "cosine"
