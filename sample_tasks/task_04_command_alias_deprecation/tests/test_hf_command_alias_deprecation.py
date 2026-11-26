import pytest
from click.decorators import AliasRegistry


def test_alias_resolution_and_warning():
    reg = AliasRegistry()
    reg.register("ls", "list")
    reg.register("old-ls", "list", deprecated=True)
    assert reg.resolve("ls") == ("list", None)
    target, warning = reg.resolve("old-ls")
    assert target == "list"
    assert warning is not None and "deprecated" in warning


def test_alias_rejects_conflicting_rebind():
    reg = AliasRegistry()
    reg.register("rm", "remove")
    reg.register("rm", "remove")
    with pytest.raises(ValueError, match="already bound"):
        reg.register("rm", "rename")
    with pytest.raises(ValueError, match="non-empty"):
        reg.register("", "remove")
    assert reg.resolve("unknown") == ("unknown", None)
