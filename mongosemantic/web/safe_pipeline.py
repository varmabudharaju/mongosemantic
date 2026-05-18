"""Allowlist-style validator for user-supplied aggregation pipelines.

Rejects any write or code-execution surface ($out, $merge, $function,
$accumulator, $where, $jsonSchema), enforces a stage cap, and recurses
into $lookup/$facet sub-pipelines so a denied stage can't hide inside
one.
"""
from __future__ import annotations

from typing import Any


class PipelineSafetyError(ValueError):
    pass


DENIED_STAGES = frozenset({"$out", "$merge"})
DENIED_OPERATORS = frozenset({"$function", "$accumulator", "$where", "$jsonSchema"})

MAX_STAGES = 100
MAX_DEPTH = 10


def validate_pipeline(pipeline: list[dict]) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise PipelineSafetyError("pipeline must be a non-empty array of stages")
    if len(pipeline) > MAX_STAGES:
        raise PipelineSafetyError(f"pipeline exceeds {MAX_STAGES} stages")
    for stage in pipeline:
        _validate_stage(stage, depth=0)


def _validate_stage(stage: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise PipelineSafetyError("pipeline nesting too deep")
    if not isinstance(stage, dict) or len(stage) != 1:
        raise PipelineSafetyError("each stage must be a single-key dict")
    name, body = next(iter(stage.items()))
    if not isinstance(name, str) or not name.startswith("$"):
        raise PipelineSafetyError(f"stage name {name!r} not allowed")
    if name in DENIED_STAGES:
        raise PipelineSafetyError(f"{name} is not allowed")
    _scan(body, depth + 1)
    if name == "$lookup" and isinstance(body, dict) and isinstance(body.get("pipeline"), list):
        for s in body["pipeline"]:
            _validate_stage(s, depth + 1)
    if name == "$facet" and isinstance(body, dict):
        for inner in body.values():
            if isinstance(inner, list):
                for s in inner:
                    _validate_stage(s, depth + 1)


def _scan(value: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise PipelineSafetyError("expression nesting too deep")
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k in DENIED_OPERATORS:
                raise PipelineSafetyError(f"operator {k} is not allowed")
            _scan(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _scan(v, depth + 1)
