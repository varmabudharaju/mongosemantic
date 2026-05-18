import pytest

from mongosemantic.web.identifiers import IdentifierError, validate_identifier


def test_accepts_simple_name():
    assert validate_identifier("articles") == "articles"


def test_accepts_dotted_path():
    assert validate_identifier("user.profile.bio") == "user.profile.bio"


def test_accepts_array_subdoc_path():
    assert validate_identifier("comments[].body") == "comments[].body"


def test_rejects_dollar_sign():
    with pytest.raises(IdentifierError):
        validate_identifier("$where")


def test_rejects_null_byte():
    with pytest.raises(IdentifierError):
        validate_identifier("articles\x00body")


def test_rejects_empty():
    with pytest.raises(IdentifierError):
        validate_identifier("")


def test_rejects_long():
    with pytest.raises(IdentifierError):
        validate_identifier("a" * 200)
