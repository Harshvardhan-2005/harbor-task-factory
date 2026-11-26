# Feature Specification: Command Alias Registry with Deprecation Warnings

Implement `AliasRegistry` in `click.decorators` (`src/click/decorators.py`).

## Requirements
- Provide `register(alias: str, target: str, *, deprecated: bool = False)` and `resolve(name: str) -> tuple[str, str | None]`.
- Raise `ValueError` if an alias is empty, target is empty, or an alias is rebound to a different target.
- Return `(target, warning)` with a deprecation message when `deprecated=True`, or `(name, None)` for unknown names.
