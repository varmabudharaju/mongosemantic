"""tests/unit/test_filtering.py"""
import mongomock
import pytest

from mongosemantic.search.filtering import (
    FilterError,
    parse_filter,
    prefilter_source_ids,
    prefix_source_filter,
)


def test_parse_filter_valid():
    assert parse_filter('{"year": {"$gte": 1960}}') == {"year": {"$gte": 1960}}


def test_parse_filter_rejects_non_object():
    with pytest.raises(FilterError):
        parse_filter('[1, 2]')
    with pytest.raises(FilterError):
        parse_filter('"year"')


def test_parse_filter_rejects_bad_json():
    with pytest.raises(FilterError):
        parse_filter('{year: 1960}')


def test_parse_filter_rejects_forbidden_operators():
    with pytest.raises(FilterError):
        parse_filter('{"$where": "this.x == 1"}')
    with pytest.raises(FilterError):
        parse_filter('{"$or": [{"$where": "1"}]}')
    with pytest.raises(FilterError):
        parse_filter('{"$text": {"$search": "x"}}')
    with pytest.raises(FilterError):
        parse_filter('{"$expr": {"$gt": ["$a", 1]}}')


def test_prefix_simple_fields():
    assert prefix_source_filter({"year": {"$gte": 1960}}) == {
        "source_doc.year": {"$gte": 1960}
    }


def test_prefix_recurses_logical_operators():
    flt = {"$or": [{"year": 1960}, {"$and": [{"genre": "Drama"}, {"rated": "PG"}]}]}
    assert prefix_source_filter(flt) == {
        "$or": [
            {"source_doc.year": 1960},
            {"$and": [{"source_doc.genre": "Drama"}, {"source_doc.rated": "PG"}]},
        ]
    }


def test_prefilter_source_ids():
    db = mongomock.MongoClient()["t"]
    db["movies"].insert_many(
        [{"_id": 1, "year": 1950}, {"_id": 2, "year": 1970}, {"_id": 3, "year": 1990}]
    )
    ids = prefilter_source_ids(db, "movies", {"year": {"$gte": 1960}})
    assert sorted(ids) == [2, 3]
