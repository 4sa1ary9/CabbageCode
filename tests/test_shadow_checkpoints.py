import shutil
import subprocess

import pytest

from cabbagecode.filehistory.history import FileHistory

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


def _read(path):
    return path.read_text(encoding="utf-8")


def test_rewind_restores_changes_made_outside_edit_tools(tmp_path):
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # 用户自己的仓库
    fh = FileHistory(str(tmp_path), "s1", shadow=True)
    first = fh.get_snapshots()[0]
    assert first.user_text == "(session start)" and first.shadow_commit

    # 模拟 Bash 造成的改动：不经过 EditFile / WriteFile，track_edit 看不到
    (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
    (tmp_path / "new.txt").write_text("created by bash", encoding="utf-8")
    fh.make_snapshot(3, "turn 1")
    (tmp_path / "a.txt").write_text("v3", encoding="utf-8")
    (tmp_path / "new.txt").unlink()

    assert fh.rewind(1)  # 回到第 1 轮结束
    assert _read(tmp_path / "a.txt") == "v2"
    assert _read(tmp_path / "new.txt") == "created by bash"

    fh.rewind(0)  # 回到会话开始
    assert _read(tmp_path / "a.txt") == "v1"
    assert not (tmp_path / "new.txt").exists()

    # 用户自己的 .git 一个提交也没多
    user_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=tmp_path, capture_output=True)
    assert user_head.returncode != 0


def test_shadow_repo_does_not_store_its_own_directory(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    FileHistory(str(tmp_path), "s2", shadow=True)
    git_dir = tmp_path / ".cabbagecode" / "file-history" / "s2" / "shadow.git"
    files = subprocess.run(["git", f"--git-dir={git_dir}", "ls-tree", "-r", "--name-only", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.split()
    assert files == ["a.txt"]


def test_file_history_without_shadow_is_unchanged(tmp_path):
    fh = FileHistory(str(tmp_path), "s3")
    assert not fh.has_snapshots()
