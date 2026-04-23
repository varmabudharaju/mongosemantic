from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo.collection import Collection


@dataclass
class FieldStats:
    type_name: str = "unknown"
    count: int = 0
    null_count: int = 0
    total_len: int = 0
    array_len_sum: int = 0
    array_occurrences: int = 0

    @property
    def avg_len(self) -> float:
        denom = max(1, self.count - self.null_count)
        return self.total_len / denom

    @property
    def avg_array_len(self) -> float:
        return self.array_len_sum / max(1, self.array_occurrences)


def _classify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        if not value:
            return "array<empty>"
        inner = _classify(value[0])
        return f"array<{inner}>"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _record(path: str, value: Any, stats: dict[str, FieldStats]) -> None:
    fs = stats.setdefault(path, FieldStats())
    fs.count += 1
    classified = _classify(value)
    if value is None:
        fs.null_count += 1
        if fs.type_name == "unknown":
            fs.type_name = "null"
        return
    if fs.type_name in ("unknown", "null"):
        fs.type_name = classified
    if isinstance(value, str):
        fs.total_len += len(value)
    elif isinstance(value, list):
        fs.array_occurrences += 1
        fs.array_len_sum += len(value)
        if value and isinstance(value[0], str):
            fs.total_len += sum(len(x) for x in value if isinstance(x, str))


def walk_document(doc: dict, stats: dict[str, FieldStats], prefix: str = "") -> None:
    for key, value in doc.items():
        path = f"{prefix}{key}"
        if isinstance(value, list) and value and isinstance(value[0], dict):
            # Record the outer array itself (array<object>).
            _record(path, value, stats)
            # Aggregate per-field stats across all subdocs as a virtual
            # array<inner_type> column rooted at "<path>[].<inner_key>".
            inner_paths: dict[str, list[Any]] = {}
            for element in value:
                if isinstance(element, dict):
                    for k, v in element.items():
                        inner_paths.setdefault(k, []).append(v)
            for inner_key, inner_vals in inner_paths.items():
                if not inner_vals:
                    continue
                full_path = f"{path}[].{inner_key}"
                sample = inner_vals[0]
                inner_classified = _classify(sample)
                fs = stats.setdefault(full_path, FieldStats())
                fs.count += 1
                if fs.type_name in ("unknown", "null"):
                    fs.type_name = f"array<{inner_classified}>"
                fs.array_occurrences += 1
                fs.array_len_sum += len(inner_vals)
                if inner_classified == "string":
                    fs.total_len += sum(
                        len(s) for s in inner_vals if isinstance(s, str)
                    )
        else:
            _record(path, value, stats)
            if isinstance(value, dict):
                walk_document(value, stats, prefix=f"{path}.")


def score_field(fs: FieldStats) -> int:
    if not fs.type_name.startswith(("string", "array<string>")):
        return 0
    score = 100
    avg_len = fs.avg_len
    if avg_len < 20:
        score -= 60
    elif avg_len < 100:
        score -= 30
    null_ratio = fs.null_count / max(1, fs.count)
    score -= int(null_ratio * 40)
    return max(0, min(100, score))


def inspect_collection(
    collection: Collection, sample_size: int = 500
) -> dict[str, FieldStats]:
    stats: dict[str, FieldStats] = {}
    cursor = collection.aggregate([{"$sample": {"size": sample_size}}])
    for doc in cursor:
        walk_document(doc, stats)
    return stats
