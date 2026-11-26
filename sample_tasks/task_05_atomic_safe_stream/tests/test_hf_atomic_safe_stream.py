import pytest
from click._compat import atomic_write_file


def test_atomic_write_commits_on_success(tmp_path):
    dest = tmp_path / "a" / "b" / "out.txt"
    with atomic_write_file(dest) as f:
        f.write("committed-data")
    with atomic_write_file(dest) as f:
        f.write("updated-data")
    assert dest.read_text(encoding="utf-8") == "updated-data"
    assert not any(p.name.endswith(".tmp") or ".tmp." in p.name for p in dest.parent.iterdir())


def test_atomic_write_rolls_back_on_error(tmp_path):
    dest = tmp_path / "config.txt"
    dest.write_text("original", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_write_file(dest) as f:
            f.write("partial-corrupt")
            raise RuntimeError("boom")
    assert dest.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["config.txt"]
