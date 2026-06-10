"""Metadata filters for semantic search.

Filters are MongoDB query documents that apply to SOURCE documents
(not shadow chunks - those carry no metadata). Local search paths
pre-filter source _ids; Atlas paths over-fetch and post-$match after
the source $lookup using the source_doc.-prefixed rewrite.
"""

from __future__ import annotations

import json
from typing import Any

from pymongo.database import Database

_MAX_FILTER_BYTES = 10_000
# Server-side JS execution or stages that cannot run mid-pipeline.
_FORBIDDEN_KEYS = {"$where", "$function", "$accumulator", "$text", "$expr"}
_LOGICAL_KEYS = ("$and", "$or", "$nor")


class FilterError(ValueError):
    """A user-supplied search filter is invalid."""


def validate_filter(flt: dict[str, Any]) -> dict[str, Any]:
    """Reject forbidden operators in an already-parsed filter document."""
    _reject_forbidden(flt)
    return flt


def parse_filter(raw: str) -> dict[str, Any]:
    if len(raw) > _MAX_FILTER_BYTES:
        raise FilterError("filter too large (max 10 KB)")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise FilterError(f"filter is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise FilterError("filter must be a JSON object, e.g. {\"year\": {\"$gte\": 1960}}")
    return validate_filter(parsed)


def _reject_forbidden(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_KEYS:
                raise FilterError(f"{key} is not allowed in search filters")
            _reject_forbidden(value)
    elif isinstance(node, list):
        for item in node:
            _reject_forbidden(item)


def prefix_source_filter(flt: dict[str, Any], prefix: str = "source_doc") -> dict[str, Any]:
    """Rewrite field keys to `<prefix>.<field>` for post-$lookup matching."""
    out: dict[str, Any] = {}
    for key, value in flt.items():
        if key in _LOGICAL_KEYS:
            out[key] = [prefix_source_filter(v, prefix) for v in value]
        elif key.startswith("$"):
            out[key] = value
        else:
            out[f"{prefix}.{key}"] = value
    return out


def prefilter_source_ids(db: Database, collection: str, flt: dict[str, Any]) -> list[Any]:
    """The _ids of source docs matching the filter (exact pre-filter for local paths)."""
    return [d["_id"] for d in db[collection].find(flt, {"_id": 1})]
