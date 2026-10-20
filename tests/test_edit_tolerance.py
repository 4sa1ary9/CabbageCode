import pytest

from cabbagecode.tools.edit_file import EditFile, Params

NL = chr(10)
CRLF = chr(13) + chr(10)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


async def _edit(path, old, new, **kw):
    return await EditFile().execute(Params(file_path=str(path), old_string=old, new_string=new, **kw))


@pytest.mark.asyncio
async def test_exact_match_behaves_as_before(tmp_path):
    f = tmp_path / "a.py"
    _write(f, "x = 1" + NL + "y = 2" + NL)
    r = await _edit(f, "y = 2", "y = 3")
    assert not r.is_error and "matched after" not in r.output
    assert _read(f) == "x = 1" + NL + "y = 3" + NL


@pytest.mark.asyncio
async def test_trailing_whitespace_is_tolerated(tmp_path):
    f = tmp_path / "a.py"
    _write(f, "def f():" + NL + "    return 1   " + NL)
    r = await _edit(f, "    return 1" + NL, "    return 2" + NL)
    assert not r.is_error and "ignoring trailing whitespace" in r.output
    assert _read(f) == "def f():" + NL + "    return 2" + NL


@pytest.mark.asyncio
async def test_missing_indentation_level_is_restored_in_new_text(tmp_path):
    f = tmp_path / "a.py"
    _write(f, NL.join(["class A:", "    def f(self):", "        return 1", ""]))
    r = await _edit(f, NL.join(["def f(self):", "    return 1", ""]), NL.join(["def f(self):", "    return 2", ""]))
    assert not r.is_error and "leading/trailing whitespace" in r.output
    assert _read(f) == NL.join(["class A:", "    def f(self):", "        return 2", ""])


@pytest.mark.asyncio
async def test_unicode_quotes_are_tolerated(tmp_path):
    f = tmp_path / "a.py"
    _write(f, "msg = 'it\u2019s fine'" + NL)
    r = await _edit(f, "msg = 'it's fine'", "msg = 'ok'")
    assert not r.is_error and "Unicode punctuation" in r.output
    assert _read(f) == "msg = 'ok'" + NL


@pytest.mark.asyncio
async def test_ambiguous_tolerant_match_is_rejected(tmp_path):
    f = tmp_path / "a.py"
    original = "a = 1  " + NL + "b = 2" + NL + "a = 1 " + NL
    _write(f, original)
    r = await _edit(f, "a = 1" + NL, "a = 9" + NL)
    assert r.is_error and "matches 2 locations" in r.output
    assert _read(f) == original


@pytest.mark.asyncio
async def test_not_found_reports_most_similar_lines(tmp_path):
    f = tmp_path / "a.py"
    _write(f, NL.join(["def total(items):", "    t = 0", "    for it in items:", "        t += it.price",
                       "    return t", ""]))
    r = await _edit(f, NL.join(["    for item in items:", "        t += item.price", ""]), "pass")
    assert r.is_error and "Most similar lines" in r.output
    assert "3\t    for it in items:" in r.output


@pytest.mark.asyncio
async def test_line_endings_are_preserved(tmp_path):
    crlf, lf = tmp_path / "crlf.py", tmp_path / "lf.py"
    _write(crlf, "a = 1" + CRLF + "b = 2" + CRLF)
    _write(lf, "a = 1" + NL + "b = 2" + NL)
    assert not (await _edit(crlf, "b = 2", "b = 3")).is_error
    assert not (await _edit(lf, "b = 2", "b = 3")).is_error
    assert _read(crlf) == "a = 1" + CRLF + "b = 3" + CRLF
    assert _read(lf) == "a = 1" + NL + "b = 3" + NL  # 原实现在 Windows 上会写成 CRLF


@pytest.mark.asyncio
async def test_replace_all(tmp_path):
    f = tmp_path / "a.txt"
    _write(f, "x" + NL + "x" + NL)
    assert (await _edit(f, "x", "y")).is_error
    r = await _edit(f, "x", "y", replace_all=True)
    assert not r.is_error and _read(f) == "y" + NL + "y" + NL
