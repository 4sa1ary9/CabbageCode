import pytest

from cabbagecode.tools.edit_file import EditFile
from cabbagecode.tools.edit_file import Params as EditParams
from cabbagecode.tools.write_file import Params as WriteParams
from cabbagecode.tools.write_file import WriteFile

NL = chr(10)
GOOD = "def f():" + NL + "    return 1" + NL


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


async def _edit(path, old, new):
    return await EditFile().execute(EditParams(file_path=str(path), old_string=old, new_string=new))


@pytest.mark.asyncio
async def test_edit_that_breaks_a_valid_file_is_rejected(tmp_path):
    f = tmp_path / "a.py"
    _write(f, GOOD)
    r = await _edit(f, "return 1", "return (1")
    assert r.is_error and "would introduce a syntax error" in r.output and "line 2" in r.output
    assert _read(f) == GOOD


@pytest.mark.asyncio
async def test_edit_on_an_already_broken_file_applies_with_warning(tmp_path):
    f = tmp_path / "a.py"
    _write(f, "def f(:" + NL + "    return 1" + NL)
    r = await _edit(f, "return 1", "return 2")
    assert not r.is_error and "still has a syntax error" in r.output
    assert "return 2" in _read(f)


@pytest.mark.asyncio
async def test_guard_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CABBAGECODE_EDIT_GUARD", "0")
    f = tmp_path / "a.py"
    _write(f, GOOD)
    r = await _edit(f, "return 1", "return (1")
    assert not r.is_error and "return (1" in _read(f)


@pytest.mark.asyncio
async def test_tolerance_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CABBAGECODE_EDIT_TOLERANCE", "0")
    f = tmp_path / "a.py"
    _write(f, "x = 1   " + NL)
    r = await _edit(f, "x = 1" + NL, "x = 2" + NL)
    assert r.is_error and r.output == "Error: old_string not found in file"


@pytest.mark.asyncio
async def test_write_file_warns_on_syntax_error(tmp_path):
    f = tmp_path / "b.py"
    r = await WriteFile().execute(WriteParams(file_path=str(f), content="def g(:" + NL))
    assert not r.is_error and "has a syntax error" in r.output and f.exists()


@pytest.mark.asyncio
async def test_non_python_files_are_not_checked(tmp_path):
    f = tmp_path / "c.txt"
    _write(f, "a" + NL)
    r = await _edit(f, "a", "((")
    assert not r.is_error and _read(f) == "((" + NL
