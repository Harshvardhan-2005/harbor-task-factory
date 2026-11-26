# Feature Specification: Bounded Duration Parameter Type

Implement `parse_duration_seconds(value: str, *, min_seconds: int | None = None, max_seconds: int | None = None) -> int` in module `click.types` (`src/click/types.py`).

## Requirements
- Parse composite positive duration strings combining `ms`, `s`, `m`, `h`, and `d` tokens (case-insensitive) into total integer seconds.
- Raise `ValueError` on empty strings, negative values, unknown units, or unparsed characters.
- Enforce optional inclusive `min_seconds` and `max_seconds` bounds with descriptive error messages.
- Preserve all existing module functionality without regressions.
