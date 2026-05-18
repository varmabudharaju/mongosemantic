import pytest

from mongosemantic.web.safe_pipeline import PipelineSafetyError, validate_pipeline


def test_simple_match_is_allowed():
    validate_pipeline([{"$match": {"x": 1}}, {"$limit": 5}])


def test_out_is_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$match": {}}, {"$out": "x"}])


def test_merge_is_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$merge": "x"}])


def test_function_is_rejected_anywhere():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$match": {"$expr": {"$function": {"body": "...", "args": [], "lang": "js"}}}}])


def test_accumulator_is_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$group": {"_id": "$x", "y": {"$accumulator": {}}}}])


def test_lookup_pipeline_is_recursed():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([
            {"$lookup": {"from": "x", "as": "y", "pipeline": [{"$out": "z"}]}}
        ])


def test_empty_pipeline_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([])


def test_pipeline_too_long_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$match": {}}] * 101)


def test_facet_inner_pipelines_recursed():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$facet": {"a": [{"$out": "z"}]}}])
