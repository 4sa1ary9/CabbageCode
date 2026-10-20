import sys

import pytest

from cabbagecode.tools import bash as bash_mod
from cabbagecode.tools.bash import Bash, find_git_bash


def test_git_bash_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "bash.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("CABBAGECODE_GIT_BASH_PATH", str(fake))
    find_git_bash.cache_clear()
    try:
        assert find_git_bash() == str(fake)
    finally:
        find_git_bash.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上走 Git Bash")
@pytest.mark.asyncio
async def test_bash_tool_runs_bash_syntax_on_windows(tmp_path):
    bash_mod.find_git_bash.cache_clear()
    if not find_git_bash():
        pytest.skip("本机没有 Git Bash")
    tool = Bash()
    tool.work_dir = str(tmp_path)
    result = await tool.execute(tool.params_model(command="echo $((1+2)) && ls -a"))
    assert not result.is_error
    assert "3" in result.output.splitlines()[0]
