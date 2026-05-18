from __future__ import annotations

import re

# Allows: letters, digits, underscore, dot, hyphen, brackets for array notation.
# No $ (which would let users name a field "$where" and trip Mongo operators).
# No quote characters, no null, no whitespace.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-.\[\]]{0,127}$")


class IdentifierError(ValueError):
    pass


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise IdentifierError("identifier must be non-empty string")
    if "\x00" in value or "$" in value:
        raise IdentifierError("identifier contains forbidden character")
    if len(value) > 128:
        raise IdentifierError("identifier exceeds 128 chars")
    if not _NAME_RE.match(value):
        raise IdentifierError(f"identifier {value!r} does not match required shape")
    return value
