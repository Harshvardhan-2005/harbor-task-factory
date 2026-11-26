# Feature Specification: Structured JSON Help Schema Emitter

Implement `export_command_schema_json(command_name: str, options: list[dict[str, object]], *, include_hidden: bool = False, indent: int = 2) -> str` in `click.formatting` (`src/click/formatting.py`).

## Requirements
- Exclude option entries where `hidden=True` unless `include_hidden=True`.
- Sort exported option entries deterministically by `name`.
- Return valid JSON containing `command` and normalized `options` (`name`, `type`, `required`, `default`).
