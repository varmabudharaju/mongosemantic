from mongosemantic.db.schema import FieldStats, score_field, walk_document


def test_walk_flat_string():
    stats: dict[str, FieldStats] = {}
    walk_document({"title": "hello"}, stats)
    assert "title" in stats
    assert stats["title"].type_name == "string"
    assert stats["title"].count == 1
    assert stats["title"].null_count == 0
    assert stats["title"].total_len == 5


def test_walk_nested_string():
    stats: dict[str, FieldStats] = {}
    walk_document({"author": {"name": "Alice"}}, stats)
    assert "author.name" in stats
    assert stats["author.name"].type_name == "string"
    assert stats["author.name"].total_len == 5


def test_walk_array_of_strings():
    stats: dict[str, FieldStats] = {}
    walk_document({"tags": ["a", "bb"]}, stats)
    assert "tags" in stats
    assert stats["tags"].type_name == "array<string>"
    assert stats["tags"].array_len_sum == 2
    assert stats["tags"].total_len == 3  # sum of element lens


def test_walk_array_of_subdocs():
    stats: dict[str, FieldStats] = {}
    walk_document({"comments": [{"body": "nice"}, {"body": "ok!"}]}, stats)
    assert "comments[].body" in stats
    assert stats["comments[].body"].type_name == "array<string>"
    assert stats["comments[].body"].array_len_sum == 2
    assert stats["comments[].body"].total_len == 7


def test_null_counts():
    stats: dict[str, FieldStats] = {}
    walk_document({"body": None}, stats)
    assert stats["body"].null_count == 1
    assert stats["body"].type_name == "null"


def test_score_great_field():
    fs = FieldStats(type_name="string", count=500, null_count=2, total_len=500 * 800)
    assert score_field(fs) >= 80


def test_score_terrible_field():
    fs = FieldStats(type_name="int", count=500, null_count=0, total_len=0)
    assert score_field(fs) == 0


def test_score_short_sparse_field():
    fs = FieldStats(type_name="string", count=500, null_count=400, total_len=500 * 5)
    assert score_field(fs) < 40
