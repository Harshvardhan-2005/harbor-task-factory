# Feature Specification: Mutually Exclusive Option Constraint Validation

Implement `validate_option_constraints(supplied_options: set[str], *, mutually_exclusive: list[set[str]] | None = None, required_together: list[set[str]] | None = None) -> None` in `click.core` (`src/click/core.py`).

## Requirements
- Raise `ValueError` when more than one option from any `mutually_exclusive` group is present in `supplied_options`.
- Raise `ValueError` when a non-empty strict subset of any `required_together` group is supplied.
- Allow empty selections or complete `required_together` groups without error.
