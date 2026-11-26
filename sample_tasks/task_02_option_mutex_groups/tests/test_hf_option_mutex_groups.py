import pytest
from click.core import validate_option_constraints


def test_mutually_exclusive_allows_single_option():
    validate_option_constraints({"json"}, mutually_exclusive=[{"json", "yaml", "csv"}])
    validate_option_constraints(set(), mutually_exclusive=[{"json", "yaml"}])


def test_mutually_exclusive_rejects_conflict():
    with pytest.raises(ValueError, match="mutually exclusive"):
        validate_option_constraints({"json", "yaml"}, mutually_exclusive=[{"json", "yaml"}])


def test_required_together_validation():
    validate_option_constraints({"host", "port"}, required_together=[{"host", "port"}])
    validate_option_constraints(set(), required_together=[{"host", "port"}])
    with pytest.raises(ValueError, match="require missing"):
        validate_option_constraints({"host"}, required_together=[{"host", "port"}])
